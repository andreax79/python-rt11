# Copyright (C) 2414 Andrea Bonomi <andrea.bonomi@gmail.com>

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import io
import struct
import typing as t

from ..abstract import AbstractFile, AbstractFilesystem
from ..commons import swap_words
from .commons import (
    Bitmap,
    InodeBitmap,
    UNIXFilesystem,
    UNIXInode,
    format_mode,
    iterate_words,
)

# Version 1 - 3

V1_SUPER_BLOCK = 0  # Superblock
V1_SUPER_BLOCK_SIZE = 2  # Superblock size in blocks

V1_USED = 0o100000  # i-node is allocated
V1_DIR = 0o040000  # directory
V1_MOD = 0o020000  # file has been modified (always on)
V1_LARGE = 0o010000  # large file

V1_SUID = 0o000040  # set user ID on execution
V1_XOWN = 0o000020  # executable
V1_ROWN = 0o000010  # read, owner
V1_WOWN = 0o000004  # write, owner
V1_ROTH = 0o000002  # read, non-owner
V1_WOTH = 0o000001  # write, non-owner

V1_PERMS = [
    [(V1_LARGE, "l"), (0, "s")],
    [(V1_DIR, "d"), (V1_SUID, "s"), (V1_XOWN, "x"), (0, "-")],
    [(V1_ROWN, "r"), (0, "-")],
    [(V1_WOWN, "w"), (0, "-")],
    [(V1_ROTH, "r"), (0, "-")],
    [(V1_WOTH, "w"), (0, "-")],
]

V1_INODE_FORMAT = "<HBBH 16s II H"
V1_FILENAME_LEN = 8
V1_INODE_SIZE = 32
V1_NADDR = 8
V1_DIR_FORMAT = f"H{V1_FILENAME_LEN}s"
V1_ROOT_INODE = 41
assert struct.calcsize(V1_INODE_FORMAT) == V1_INODE_SIZE


class UNIXInode1(UNIXInode):

    @classmethod
    def read(cls, fs: "UNIXFilesystem", inode_num: int, buffer: bytes, position: int = 0) -> "UNIXInode":
        self = UNIXInode1(fs)
        self.inode_num = inode_num
        (
            self.flags,  #   1 word  flags
            self.nlinks,  #  1 byte  number of links to file
            self.uid,  #     1 byte  user ID of owner
            self.size,  #    1 word  size
            addr,  #         8 words content or indirect block numbers
            self.ctime,  #   1 long  creation time
            self.mtime,  #   1 long  modification time
            _,  #            1 word  unused
        ) = struct.unpack_from(V1_INODE_FORMAT, buffer, position)
        self.addr = struct.unpack_from(f"{V1_NADDR}H", addr)  # type: ignore
        self.ctime = swap_words(self.ctime)  # creation time
        self.mtime = swap_words(self.mtime)  # modification tim
        return self

    def write_buffer(self, buffer: bytearray, position: int = 0) -> None:
        """
        Write the inode to the buffer
        """
        addr = struct.pack(f"{V1_NADDR}H", *self.addr)
        data = struct.pack(
            V1_INODE_FORMAT,
            self.flags,
            self.nlinks,
            self.uid,
            self.size,
            addr,
            swap_words(self.ctime),
            swap_words(self.mtime),
            0,  # unused
        )
        buffer[position : position + len(data)] = data

    def blocks(self, include_indexes: bool = False) -> t.Iterator[int]:
        if self.is_large:
            # Large file
            for block_number in self.addr:
                if block_number == 0:
                    break
                if include_indexes:
                    yield block_number
                indirect_block = self.fs.read_block(block_number)
                for n in iterate_words(indirect_block):
                    if n == 0:
                        break
                    yield n
        else:
            # Small file
            for block_number in self.addr:
                if block_number == 0:
                    break
                yield block_number

    @property
    def isdir(self) -> bool:
        return (self.flags & V1_DIR) == V1_DIR

    @property
    def is_regular_file(self) -> bool:
        return not self.isdir

    @property
    def is_large(self) -> bool:
        return bool(self.flags & V1_LARGE)

    @property
    def is_allocated(self) -> bool:
        return bool(self.flags & V1_USED)

    @property
    def is_special_file(self) -> bool:
        return self.inode_num < V1_ROOT_INODE

    def __str__(self) -> str:
        if not self.is_allocated:
            return f"{self.inode_num:>4}# ---"
        else:
            mode = format_mode(self.flags, version=self.fs.version, perms=self.fs.perms)
            return f"{self.inode_num:>4}# uid: {self.uid:>3} nlinks: {self.nlinks:>3} size: {self.size:>5} {mode} flags: {self.flags:o}"

    def examine(self) -> str:
        buf = io.StringIO()
        buf.write("\n*Inode\n")
        buf.write(f"Inode number:         {self.inode_num}\n")
        buf.write(f"Flags:                {self.flags:06o}\n")
        if self.is_special_file:
            buf.write("Type:                 special file\n")
        elif self.isdir:
            buf.write("Type:                 directory\n")
        elif self.is_large:
            buf.write("Type:                 large file\n")
        else:
            buf.write("Type:                 file\n")
        buf.write(f"Owner user id:        {self.uid}\n")
        buf.write(f"Creation time:        {self.ctime}\n")
        buf.write(f"Modification time:    {self.mtime}\n")
        buf.write(f"Link count:           {self.nlinks}\n")
        buf.write(f"Size:                 {self.size}\n")
        if self.is_large:
            buf.write(f"Indirect blocks:      {self.addr}\n")
        buf.write(f"Blocks:               {list(self.blocks())}\n")
        return buf.getvalue()


class UNIX1Filesystem(UNIXFilesystem):
    """
    UNIX version 1, 2, 3 Filesystem

    Disk Layout

    Block
        +------------------------------+
    0   | Superblock                   |
    1   |                              |
        +------------------------------+
    2   | Inode 1 - 16                 |
        +------------------------------+
    3   | Inode 17 - 32                |
        +------------------------------+
        | ...                          |
        +------------------------------+

    Superblock layout

    Byte
         +------------------------------+
    0    | Size of free storage bitmap  |
         +------------------------------+
    2    | Bitmap                       |
         | ...                          |
         +------------------------------+
    b    | Size of i-node bitmap        |
         +------------------------------+
    b+2  | I-node bitmap                |
         | ...                          |
         +------------------------------+

    """

    fs_name = "unix1"
    fs_description = "UNIX version 1"
    version: int = 1  # UNIX version
    inode_size = V1_INODE_SIZE
    dir_format = V1_DIR_FORMAT
    root_inode = V1_ROOT_INODE
    first_inode = V1_ROOT_INODE
    perms = V1_PERMS
    unix_inode_class = UNIXInode1

    bitmap_size: int  # Size of free storage bitmap
    inode_bitmap_size: int  # Size of i-node bitmap

    @classmethod
    def mount(cls, file: "AbstractFile") -> "AbstractFilesystem":
        self = cls(file)
        self.pwd = "/"
        self.read_superblock()
        return self

    def read_superblock(self) -> None:
        """Read superblock"""
        superblock_data = self.read_block(V1_SUPER_BLOCK, V1_SUPER_BLOCK_SIZE)
        (self.bitmap_size,) = struct.unpack_from("<H", superblock_data, 0)
        (self.inode_bitmap_size,) = struct.unpack_from("<H", superblock_data, self.bitmap_size + 2)
        self.inodes = self.inode_bitmap_size * 8
        # bitmap = Bitmap()
        # bitmap.bitmaps = list(superblock_data[2 : self.bitmap_size + 2])
        # print(bitmap.total_bits)
        # for i in range(0, 10):
        #     print(bitmap.is_free(i+3000))
        # inode_bitmap = InodeBitmap()
        # pos = 2 + self.bitmap_size + 2
        # inode_bitmap.bitmaps = list(superblock_data[pos:pos+self.inode_bitmap_size])
        # for i in range(30, 50):
        #     print(inode_bitmap.is_free(1000+i))
        #
