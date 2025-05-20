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

import struct
import typing as t

from ..abstract import AbstractFile, AbstractFilesystem
from ..commons import BLOCK_SIZE, swap_words
from .commons import UNIXFilesystem, UNIXInode, format_mode, iterate_words

__all__ = ["UNIX4Filesystem", "UNIXInode4"]

# Version 4 - 6

V4_USED = 0o100000  # i-node is allocated
V4_BLK = 0o060000  # block device
V4_DIR = 0o040000  # directory
V4_CHR = 0o020000  # character device
V4_LARGE = 0o010000  # large file

V4_SUID = 0o4000  # set user ID on execution
V4_SGID = 0o2000  # set group ID on execution
V4_STXT = 0o1000  # sticky bit

V4_ROWN = 0o400  # read by owner
V4_WOWN = 0o200  # write by owner
V4_XOWN = 0o100  # execute by owner
V4_RGRP = 0o040  # read by group
V4_WGRP = 0o020  # write by group
V4_XGRP = 0o010  # execute by group
V4_ROTH = 0o004  # read by other
V4_WOTH = 0o002  # write by other
V4_XOTH = 0o001  # execute by other

V4_PERMS = [
    [(V4_BLK, "b"), (V4_DIR, "d"), (V4_CHR, "c"), (0, "-")],
    [(V4_ROWN, "r"), (0, "-")],
    [(V4_WOWN, "w"), (0, "-")],
    [(V4_SUID, "s"), (V4_XOWN, "x"), (0, "-")],
    [(V4_RGRP, "r"), (0, "-")],
    [(V4_WGRP, "w"), (0, "-")],
    [(V4_SGID, "s"), (V4_XGRP, "x"), (0, "-")],
    [(V4_ROTH, "r"), (0, "-")],
    [(V4_WOTH, "w"), (0, "-")],
    [(V4_XOTH, "x"), (0, "-")],
    [(V4_STXT, "t"), (0, " ")],
]

V4_NICFREE = 100  # number of superblock free blocks
V4_NICINOD = 100  # number of superblock inodes
V4_SUPER_BLOCK = 1  # Superblock
V4_SUPER_BLOCK_FORMAT = f"<HHH {V4_NICFREE}H H {V4_NICINOD}H BBB L"

V4_INODE_FORMAT = "<HBBBBH 16s II"
V4_FILENAME_LEN = 14
V4_INODE_SIZE = 32
V4_NADDR = 8
V4_DIR_FORMAT = f"H{V4_FILENAME_LEN}s"
V4_ROOT_INODE = 1
assert struct.calcsize(V4_INODE_FORMAT) == V4_INODE_SIZE


class UNIXInode4(UNIXInode):

    @classmethod
    def read(cls, fs: "UNIXFilesystem", inode_num: int, buffer: bytes, position: int = 0) -> "UNIXInode":
        self = UNIXInode4(fs)
        self.inode_num = inode_num
        (
            self.flags,  #   1 word  flags
            self.nlinks,  #  1 byte  number of links to file
            self.uid,  #     1 byte  user ID of owner
            self.gid,  #     1 byte  group ID of owner
            sz0,  #          1 byte  high byte of 24-bit size
            sz1,  #          1 word  low word of 24-bit size
            addr,  #         8 words block numbers or device numbers
            self.atime,  #   1 long  time of last access
            self.mtime,  #   1 long  time of last modification
        ) = struct.unpack_from(V4_INODE_FORMAT, buffer, position)
        self.addr = struct.unpack_from(f"{V4_NADDR}H", addr)  # type: ignore
        self.size = (sz0 << 16) + sz1
        self.atime = swap_words(self.atime)
        self.mtime = swap_words(self.mtime)
        return self

    def blocks(self) -> t.Iterator[int]:
        if self.is_large:
            # Large file
            for block_number in self.addr:
                if block_number == 0:
                    break
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
        return (self.flags & V4_DIR) == V4_DIR

    @property
    def is_regular_file(self) -> bool:
        return not self.isdir

    @property
    def is_large(self) -> bool:
        return bool(self.flags & V4_LARGE)

    @property
    def is_allocated(self) -> bool:
        return bool(self.flags & V4_USED)

    def __str__(self) -> str:
        if not self.is_allocated:
            return f"{self.inode_num:>4}# ---"
        else:
            mode = format_mode(self.flags, version=self.fs.version, perms=self.fs.perms)
            return f"{self.inode_num:>4}# {self.uid:>3},{self.gid:<3} nlinks: {self.nlinks:>3} size: {self.size:>8}  {mode} flags: {self.flags:06o}"


class UNIX4Filesystem(UNIXFilesystem):
    """
    UNIX version 4, 5, 6 Filesystem
    """

    fs_name = "unix4"
    fs_description = "UNIX version 4"
    version: int = 4  # UNIX version
    inode_size = V4_INODE_SIZE
    dir_format = V4_DIR_FORMAT
    root_inode = V4_ROOT_INODE
    perms = V4_PERMS
    unix_inode_class = UNIXInode4

    @classmethod
    def mount(cls, file: "AbstractFile") -> "AbstractFilesystem":
        self = cls(file)
        self.pwd = "/"
        self.read_superblock()
        return self

    def read_superblock(self) -> None:
        """Read superblock"""
        superblock_data = self.read_block(V4_SUPER_BLOCK)
        superblock = struct.unpack_from(V4_SUPER_BLOCK_FORMAT, superblock_data, 0)
        self.inode_list_blocks = superblock[0]
        self.volume_size = superblock[1]
        self.free_blocks_in_list = superblock[2]
        self.free_blocks_list = list(superblock[3 : 3 + V4_NICFREE])
        self.free_inodes_in_list = superblock[3 + V4_NICFREE]
        self.free_inodes_list = list(superblock[4 + V4_NICFREE : 4 + V4_NICFREE + V4_NICINOD])
        # _ = superblock[4 + V4_NICINOD + V4_NICFREE]  # lock during free list manipulation
        # _ = superblock[5 + V4_NICINOD + V4_NICFREE]  # lock during i-list manipulation
        # _ = superblock[6 + V4_NICINOD + V4_NICFREE]  # flag to indicate that the super-block has changed and should be written
        # _ = swap_words(superblock[7 + V4_NICINOD + V4_NICFREE]) # last super block update
        self.inodes = (self.inode_list_blocks - 1) * (BLOCK_SIZE // self.inode_size)
