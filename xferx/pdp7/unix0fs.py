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

import errno
import io
import math
import os
import sys
import typing as t
from functools import reduce

from ..abstract import AbstractFile, AbstractFilesystem
from ..commons import ASCII, IMAGE, READ_FILE_FULL
from ..unix.commons import (
    UNIXDirectory,
    UNIXDirectoryEntry,
    UNIXFile,
    UNIXFilesystem,
    UNIXInode,
    unix_join,
)
from .block import BYTES_PER_WORD_18BIT, BlockDevice18Bit, from_18bit_words_to_bytes

__all__ = [
    "UNIXFile0",
    "UNIXDirectoryEntry0",
    "UNIX0Filesystem",
]

V0_SUPER_BLOCK = 0  # Superblock

V0_IO_BYTES_PER_WORD = 3  # When files are exported, each word is encoded in 3 bytes
V0_WORDS_PER_BLOCK = 64  # Number of words per block
V0_BLOCK_SIZE = BYTES_PER_WORD_18BIT * V0_WORDS_PER_BLOCK  # Block size (in bytes)

V0_BLOCKS_PER_SURFACE = 8000  # Number of blocks on a surface
V0_FILESYSTEM_SIZE = 6400  # Number of blocks in the filesystem
V0_INODE_BLOCKS = 710  # Number of i-node blocks
V0_FIRST_INODE_BLOCK = 2  # First i-node block number
V0_INODE_SIZE = 12  # Inode size (in words)
V0_INODES_PER_BLOCK = V0_WORDS_PER_BLOCK // V0_INODE_SIZE  # Number of inodes per block
V0_DIRENT_SIZE = 8  # Size of a directory entry (in words)
V0_SURFACE_SIZE = V0_BLOCKS_PER_SURFACE * V0_WORDS_PER_BLOCK * BYTES_PER_WORD_18BIT
V0_FREE_BLOCKS_LIST_SIZE = 9  # Number of free blocks in a block (in words)

V0_MAXINT = 0o777777  # Biggest unsigned integer

V0_FLAGS = 0
V0_ADDR = 1
V0_UID = 8
V0_NLINKS = 9
V0_SIZE = 10
V0_UNIQ = 11

V0_NUMBLKS = 7  # Seven block pointers in i-node

V0_USED = 0o400000  # i-node is allocated
V0_LARGE = 0o200000  # large file
V0_SPECIAL = 0o000040  # special file
V0_DIR = 0o000020  # directory

V0_ROWN = 0o000010  # read, owner
V0_WOWN = 0o000004  # write, owner
V0_ROTH = 0o000002  # read, non-owner
V0_WOTH = 0o000001  # write, non-owner

V0_ROOT_INODE = 4  # 'dd' folder


def get_v0_inode_block_offset(inode_num: int) -> t.Tuple[int, int]:
    """
    Return block number and offset for an inode number
    """
    block_num = V0_FIRST_INODE_BLOCK + (inode_num // V0_INODES_PER_BLOCK)
    offset = V0_INODE_SIZE * (inode_num % V0_INODES_PER_BLOCK)
    return block_num, offset


class UNIXFile0(UNIXFile):
    inode: "UNIXInode0"

    def __init__(self, inode: "UNIXInode0", file_mode: t.Optional[str] = None):
        super().__init__(inode)
        self.file_mode = file_mode or IMAGE

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = self.inode.get_length()
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.inode.get_length()
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        data = bytearray()
        # Get the blocks to be read
        blocks = list(self.inode.blocks())[block_number : block_number + number_of_blocks]
        # Read the blocks
        for disk_block_number in blocks:
            words = self.inode.fs.read_18bit_words_block(V0_BLOCKS_PER_SURFACE + disk_block_number)
            t = from_18bit_words_to_bytes(words, self.file_mode)
            data.extend(t)
        return bytes(data)

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        if self.file_mode == ASCII:
            return self.inode.size * 2  # 2 ASCII bytes per 18 bit word
        else:
            return self.inode.size * V0_IO_BYTES_PER_WORD


class UNIXInode0(UNIXInode):
    """
    Inode numbers begin at 1, and the storage for i-nodes begins at block 2.
    Blocks 2 to 711 contain the i-nodes, with five 12-word i-nodes per block.

    Reserved i-node numbers:

     1  The core file
     2  The "dd" directory
     3  The "system" directory
     6  The "ttyin" special file
     7  The "keyboard" GRAPHIC-2 keyboard special file
     8  The "pptin" paper tape reader special file
    10  The "ttyout" special file
    11  The "display" GRAPHIC-2 display special file
    12  The "pptout" paper tape punch special file

    https://github.com/DoctorWkt/pdp7-unix/blob/master/man/fs.5
    """

    fs: "UNIX0Filesystem"
    uniq: int  # Unique value assigned at creation
    inode_num: int  # Inode number
    flags: int  # Flags
    uid: int  # Owner user id
    nlinks: int  # Link count
    size: int  # Size (in words)
    addr: t.List[int]  # Indirect blocks or data blocks

    @classmethod
    def read(cls, fs: "UNIX0Filesystem", inode_num: int, words: t.List[int], position: int = 0) -> "UNIXInode0":  # type: ignore
        self = UNIXInode0(fs)
        self.inode_num = inode_num
        self.flags = words[position + V0_FLAGS]
        self.uid = words[position + V0_UID]  # Owner user id
        if self.uid == V0_MAXINT:
            self.uid = -1  # 'system' (root) uid
        self.nlinks = V0_MAXINT - words[position + V0_NLINKS] + 1  # Link count
        self.size = words[position + V0_SIZE]  # Size (in words)
        self.uniq = words[position + V0_UNIQ]  # Unique value assigned at creation
        self.addr = words[position + V0_ADDR : position + V0_ADDR + V0_NUMBLKS]  # Indirect blocks or data blocks
        return self

    def write(self) -> None:
        """
        Write inode
        """
        block_number, offset = get_v0_inode_block_offset(self.inode_num)
        words = self.fs.read_18bit_words_block(V0_BLOCKS_PER_SURFACE + block_number)
        words[offset : offset + V0_INODE_SIZE] = self.to_words()
        self.fs.write_18bit_words_block(V0_BLOCKS_PER_SURFACE + block_number, words)

    def to_words(self) -> t.List[int]:
        """
        Convert inode data to 18bit words
        """
        words = [0] * V0_INODE_SIZE
        words[V0_FLAGS] = self.flags
        if self.uid == -1:
            words[V0_UID] = V0_MAXINT
        else:
            words[V0_UID] = self.uid
        words[V0_NLINKS] = V0_MAXINT - self.nlinks + 1
        words[V0_SIZE] = self.size
        words[V0_UNIQ] = self.uniq
        words[V0_ADDR : V0_ADDR + V0_NUMBLKS] = self.addr
        return words

    def blocks(self, include_indexes: bool = False) -> t.Iterator[int]:
        if self.is_large:
            # Large file
            for block_number in self.addr:
                if block_number == 0:
                    break
                if include_indexes:
                    yield block_number
                for n in self.fs.read_18bit_words_block(V0_BLOCKS_PER_SURFACE + block_number):
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
        return (self.flags & V0_DIR) == V0_DIR

    @property
    def is_special_file(self) -> bool:
        return (self.flags & V0_SPECIAL) == V0_SPECIAL

    @property
    def is_regular_file(self) -> bool:
        return not self.isdir

    @property
    def is_large(self) -> bool:
        return bool(self.flags & V0_LARGE)

    @property
    def is_allocated(self) -> bool:
        return (self.flags & V0_USED) != 0

    def read_words(self) -> t.List[int]:
        """
        Read inode data as 18bit words
        """
        data = []
        for block_number in self.blocks():
            data.extend(self.fs.read_18bit_words_block(V0_BLOCKS_PER_SURFACE + block_number))
        return data

    def get_block_size(self) -> int:
        """
        Get block size in bytes
        """
        return V0_BLOCK_SIZE

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.size * V0_IO_BYTES_PER_WORD

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return len(list(self.blocks()))

    def examine(self) -> str:
        buf = io.StringIO()
        buf.write("\n*Inode\n")
        buf.write(f"Inode number:          {self.inode_num:>6}\n")
        buf.write(f"Uniq:                  {self.uniq:>6}\n")
        buf.write(f"Flags:                 {self.flags:>06o}\n")
        if self.isdir:
            buf.write("Type:               directory\n")
        elif self.is_special_file:
            buf.write("Type:            special file\n")
        elif self.is_large:
            buf.write("Type:              large file\n")
        else:
            buf.write("Type:                    file\n")
        buf.write(f"Owner user id:         {self.uid:>6}\n")
        buf.write(f"Link count:            {self.nlinks:>6}\n")
        buf.write(f"Size (words):          {self.size:>6}\n")
        if self.is_large:
            buf.write(f"Indirect blocks:       {self.addr}\n")
        buf.write(f"Blocks:                {list(self.blocks())}\n")
        return buf.getvalue()

    def __str__(self) -> str:
        if not self.is_allocated:
            return f"{self.inode_num:>4}# ---"
        else:
            return f"{self.inode_num:>4}# uid: {self.uid:>3}  nlinks: {self.nlinks:>3}  size: {self.size:>5} words  flags: {self.flags:o}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UNIXInode0):
            return False
        return (
            self.inode_num == other.inode_num
            and self.uniq == other.uniq
            and self.flags == other.flags
            and self.uid == other.uid
            and self.nlinks == other.nlinks
            and self.size == other.size
            and self.addr == other.addr
        )


class UNIXDirectoryEntry0(UNIXDirectoryEntry):
    inode: "UNIXInode0"

    def open(self, file_mode: t.Optional[str] = None) -> UNIXFile:
        """
        Open a file
        """
        return UNIXFile0(self.inode, file_mode)


class UNIX0Directory(UNIXDirectory):

    @classmethod
    def read(cls, fs: "UNIXFilesystem", inode: "UNIXInode") -> "UNIXDirectory":
        """
        Read the directory
        """
        self = UNIX0Directory(fs, inode)
        if self.inode.isdir:
            self.entries = []
            data = inode.read_words()  # type: ignore
            for i in range(0, len(data), V0_DIRENT_SIZE):
                inum = data[i]
                name = from_18bit_words_to_bytes(data[i + 1 : i + 2 + 4])
                if inum > 0:
                    name_ascii = name.decode("ascii", errors="ignore").rstrip(" \x00")
                    self.entries.append((inum, name_ascii))
        return self

    def write(self) -> None:
        """
        Write the directory
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))


class UNIX0FreeStorageMap:
    """
    Free-Storage Map

    Each block in the free-storage map is structured as follows:

    - the first word is the block number of the next block
      in the free-storage map, or zero if this is the end of
      the free-storage map;
    - the next 9 words hold free block numbers,
      or 0 (no block number).
    """

    fs: "UNIX0Filesystem"
    map_blocks: t.List[int]  # List of map blocks
    free_blocks: t.List[int]  # List of free blocks

    def __init__(self, fs: "UNIX0Filesystem"):
        self.fs = fs
        self.map_blocks = []
        self.free_blocks = []

    @classmethod
    def read(cls, fs: "UNIX0Filesystem") -> "UNIX0FreeStorageMap":
        self = UNIX0FreeStorageMap(fs)
        block_number = fs.storage_map_block
        while block_number != 0:
            self.map_blocks.append(block_number)
            words = fs.read_18bit_words_block(V0_BLOCKS_PER_SURFACE + block_number)
            block_number = words[0]
            self.free_blocks.extend(words[1 : V0_FREE_BLOCKS_LIST_SIZE + 1])
        return self

    def write(self) -> None:
        # Check if the map blocks are enough
        required_map_blocks = math.ceil(len(self.free_blocks) / V0_FREE_BLOCKS_LIST_SIZE)
        for _ in range(len(self.map_blocks), required_map_blocks):
            # Allocate a new block for the map
            self.map_blocks.append(self.allocate(1)[0])
        tmp = list(self.free_blocks)
        for i, map_block_number in enumerate(self.map_blocks):
            # Write the map block
            words = [0] * V0_WORDS_PER_BLOCK
            words[0] = self.map_blocks[i + 1] if i + 1 < len(self.map_blocks) else 0
            for j in range(1, V0_FREE_BLOCKS_LIST_SIZE + 1):
                if tmp:
                    words[j] = tmp.pop(0)
                else:
                    words[j] = 0
            self.fs.write_18bit_words_block(V0_BLOCKS_PER_SURFACE + map_block_number, words)

    def is_free(self, block_number: int) -> bool:
        """
        Check if the block is free
        """
        return block_number in self.free_blocks

    def set_free(self, block_number: int) -> None:
        """
        Mark the block as free
        """
        # Check if the block is already free
        if block_number not in self.free_blocks:
            # Update the free block list
            for i in range(0, len(self.free_blocks)):
                if self.free_blocks[i] == 0:
                    self.free_blocks[i] = block_number
                    return
            # If there is no free block, add it to the end of the list
            self.free_blocks.append(block_number)

    def set_used(self, block_number: int) -> None:
        """
        Mark the block as used
        """
        # Check if the block is already used
        if block_number in self.free_blocks:
            # Update the free block list
            for i in range(0, len(self.free_blocks)):
                if self.free_blocks[i] == block_number:
                    self.free_blocks[i] = 0
                    break

    def allocate(self, size: int) -> t.List[int]:
        """
        Allocate blocks
        """
        blocks = []
        for block_number in self.free_blocks:
            if block_number != 0:
                blocks.append(block_number)
                self.set_used(block_number)
            if len(blocks) == size:
                break
        if len(blocks) < size:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        return blocks

    def used(self) -> int:
        """
        Count the number of used blocks
        """
        return V0_FILESYSTEM_SIZE - self.free()

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        return len([x for x in self.free_blocks if x != 0])

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UNIX0FreeStorageMap):
            return False
        return self.map_blocks == other.map_blocks and self.free_blocks == other.free_blocks


class UNIX0Filesystem(UNIXFilesystem, BlockDevice18Bit):
    """
    UNIX version 0 Filesystem
    """

    fs_name = "unix0"
    fs_description = "UNIX version 0"
    version: int = 0
    words_per_block = V0_WORDS_PER_BLOCK
    directory_class = UNIX0Directory
    storage_map_block: int = 0  # First block of the free-storage map

    @classmethod
    def mount(cls, file: "AbstractFile") -> "AbstractFilesystem":
        self = cls(file)
        self.version = 0
        self.pwd = "/"
        self.inode_size = V0_INODE_SIZE
        self.root_inode = V0_ROOT_INODE
        self.read_superblock()
        return self

    def read_superblock(self) -> None:
        """Read superblock"""
        # The first word of block 0 points to the first block of the free-storage map.
        words = self.read_18bit_words_block(V0_BLOCKS_PER_SURFACE + V0_SUPER_BLOCK)
        self.storage_map_block = words[0]

    def read_inode(self, inode_num: int) -> UNIXInode:
        """
        Read inode by number
        """
        block_number, offset = get_v0_inode_block_offset(inode_num)
        words = self.read_18bit_words_block(V0_BLOCKS_PER_SURFACE + block_number)[offset : offset + V0_INODE_SIZE]
        return UNIXInode0.read(self, inode_num, words)

    def read_dir_entries(self, dirname: str) -> t.Iterator["UNIXDirectoryEntry"]:
        inode: UNIXInode0 = self.get_inode(dirname)  # type: ignore
        if inode:
            for inode_num, filename in self.list_dir(inode):
                fullname = unix_join(dirname, filename)
                yield UNIXDirectoryEntry0(self, fullname, inode_num)

    def get_file_entry(self, fullname: str) -> UNIXDirectoryEntry:
        inode: UNIXInode0 = self.get_inode(fullname)  # type: ignore
        if not inode:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return UNIXDirectoryEntry0(self, fullname, inode.inode_num, inode)

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        entries = sorted(self.filter_entries_list(pattern, include_all=True, wildcard=True))
        if not entries:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), pattern)
        if not options.get("brief") and not self.version == 0:
            blocks = reduce(lambda x, y: x + y, [x.inode.get_length() for x in entries])
            if self.version < 3:
                sys.stdout.write(f"total {blocks:>4}\n")
            else:
                sys.stdout.write(f"blocks = {blocks}\n")
        for x in entries:
            if not options.get("full") and x.basename.startswith("."):
                pass
            elif options.get("brief"):
                # Lists only file names
                sys.stdout.write(f"{x.basename}\n")
            uid = x.inode.uid if x.inode.uid != -1 else 0o77
            sys.stdout.write(
                f"{x.inode_num:>03o} {x.inode.flags & 0o77:02o} {uid:02o} {x.inode.nlinks:>02o} {x.inode.size:>05o} {x.basename}\n"
            )
