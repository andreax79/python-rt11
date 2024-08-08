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
import os
import struct
import sys
from functools import reduce
from typing import Dict, Iterator, List, Optional, Tuple

from .abstract import AbstractFile
from .commons import ASCII, IMAGE, READ_FILE_FULL
from .unixfs import UNIXDirectoryEntry, UNIXFile, UNIXFilesystem, UNIXInode, unix_join

__all__ = [
    "UNIXFile0",
    "UNIXDirectoryEntry0",
    "UNIXFilesystem0",
]

V0_BYTES_PER_WORD = 4  # Each word is encoded in 4 bytes
V0_IO_BYTES_PER_WORD = 3  # Whem files are exported, each word is encoded in 3 bytes
V0_WORDS_PER_BLOCK = 64  # Number of words per block
V0_BLOCK_SIZE = V0_BYTES_PER_WORD * V0_WORDS_PER_BLOCK  # Block size (in bytes)

V0_BLOCKS_PER_SURFACE = 8000  # Number of blocks on a surface
V0_NUMINODEBLKS = 710  # Number of i-node blocks
V0_FIRSTINODEBLK = 2  # First i-node block number
V0_INODE_SIZE = 12  # Inode size (in words)
V0_INODES_PER_BLOCK = V0_WORDS_PER_BLOCK // V0_INODE_SIZE  # Number of inodes per block
V0_DIRENT_SIZE = 8  # Size of a directory entry (in words)
V0_SURFACE_SIZE = V0_BLOCKS_PER_SURFACE * V0_WORDS_PER_BLOCK * V0_BYTES_PER_WORD

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


def get_v0_inode_block_offset(inode_num: int) -> Tuple[int, int]:
    """
    Return block number and offset for an inode number
    """
    block_num = V0_FIRSTINODEBLK + (inode_num // V0_INODES_PER_BLOCK)
    offset = V0_INODE_SIZE * (inode_num % V0_INODES_PER_BLOCK)
    return block_num, offset


def from_18bit_words_to_bytes(words: list[int], file_type: str = ASCII) -> bytes:
    """
    Convert 18bit words to bytes
    """
    data = bytearray()
    if file_type == ASCII:
        for word in words:
            data.append((word >> 9) & 0o177)
            data.append(word & 0o177)
    else:
        for word in words:
            data.append(((word >> 12) & 0o077) + 0x80)
            data.append(((word >> 6) & 0o077) + 0x80)
            data.append((word & 0o077) + 0x80)
    return bytes(data)


class UNIXFile0(UNIXFile):
    inode: "UNIXInode0"

    def __init__(self, inode: "UNIXInode0", file_type: Optional[str] = None):
        super().__init__(inode)
        self.file_type = file_type or IMAGE

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
        for i, next_block_number in enumerate(self.inode.blocks()):
            if i >= block_number:
                words = self.inode.fs.read_18bit_words_block(next_block_number)
                t = from_18bit_words_to_bytes(words, self.file_type)
                data.extend(t)
                number_of_blocks -= 1
                if number_of_blocks == 0:
                    break
        return bytes(data)

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        if self.file_type == ASCII:
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

    fs: "UNIXFilesystem0"
    uniq: int  # Unique value assigned at creation

    @classmethod
    def read(cls, fs: "UNIXFilesystem0", inode_num: int, words: List[int], position: int = 0) -> "UNIXInode0":  # type: ignore
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

    def blocks(self) -> Iterator[int]:
        if self.is_large:
            # Large file
            for block_number in self.addr:
                if block_number == 0:
                    break
                for n in self.fs.read_18bit_words_block(block_number):
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
    def is_regular_file(self) -> bool:
        return not self.isdir

    @property
    def is_large(self) -> bool:
        return bool(self.flags & V0_LARGE)

    @property
    def is_allocated(self) -> bool:
        return (self.flags & V0_USED) != 0

    def read_words(self) -> List[int]:
        """
        Read inode data as 18bit words
        """
        data = []
        for block_number in self.blocks():
            data.extend(self.fs.read_18bit_words_block(block_number))
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

    def __str__(self) -> str:
        if not self.is_allocated:
            return f"{self.inode_num:>4}# ---"
        else:
            return f"{self.inode_num:>4}# uid: {self.uid:>3}  nlinks: {self.nlinks:>3}  size: {self.size:>5} words  flags: {self.flags:o}"


class UNIXDirectoryEntry0(UNIXDirectoryEntry):
    inode: "UNIXInode0"

    def open(self, file_type: Optional[str] = None) -> UNIXFile:
        """
        Open a file
        """
        return UNIXFile0(self.inode, file_type)


class UNIXFilesystem0(UNIXFilesystem):
    """
    UNIX Filesystem
    """

    def __init__(self, file: "AbstractFile"):
        self.f = file
        self.version = 0
        self.pwd = "/"
        self.inode_size = V0_INODE_SIZE
        self.root_inode = V0_ROOT_INODE
        self.read_superblock()

    def read_superblock(self) -> None:
        """Read superblock"""
        # The first word of block 0 points to the first block of the free-storage map.
        # Each block in the free-storage map is structured as follows:
        # - the first word is the block number of the next block in the free-storage map,
        #   or zero if this is the end of the free-storage map.
        # - The next nine words hold free block numbers, or zero (no block number).

    def read_18bit_word(self) -> int:
        """
        Read 4 bytes as one 18bit word
        """
        t: Tuple[int, int, int, int] = struct.unpack("BBBB", self.f.read(V0_BYTES_PER_WORD))  # type: ignore
        return (t[0] & 0xFF) | ((t[1] & 0xFF) << 8) | ((t[2] & 0xFF) << 16) | ((t[3] & 0xFF) << 24)

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        data = bytearray()
        for i in range(block_number, block_number + number_of_blocks):
            words = self.read_18bit_words_block(block_number)
            data.extend(from_18bit_words_to_bytes(words, IMAGE))
        return bytes(data)

    def read_18bit_words_block(
        self,
        block_number: int,
    ) -> List[int]:
        """
        Read a 256 bytes block as 18bit words
        """
        self.f.seek(V0_SURFACE_SIZE + block_number * V0_WORDS_PER_BLOCK * V0_BYTES_PER_WORD)
        return [self.read_18bit_word() for _ in range(V0_WORDS_PER_BLOCK)]

    def read_inode(self, inode_num: int) -> UNIXInode:
        """
        Read inode by number
        """
        block_number, offset = get_v0_inode_block_offset(inode_num)
        words = self.read_18bit_words_block(block_number)[offset : offset + V0_INODE_SIZE]
        return UNIXInode0.read(self, inode_num, words)

    def list_dir(self, inode: UNIXInode0) -> List[Tuple[int, str]]:  # type: ignore
        if not inode.isdir:
            return []
        files = []
        data = inode.read_words()
        for i in range(0, len(data), V0_DIRENT_SIZE):
            inum = data[i]
            name = from_18bit_words_to_bytes(data[i + 1 : i + 2 + 4])
            if inum > 0:
                name_ascii = name.decode("ascii", errors="ignore").rstrip(" \x00")
                files.append((inum, name_ascii))
        return files

    def read_dir_entries(self, dirname: str) -> Iterator["UNIXDirectoryEntry"]:
        inode: UNIXInode0 = self.get_inode(dirname)  # type: ignore
        if inode:
            for inode_num, filename in self.list_dir(inode):
                fullname = unix_join(dirname, filename)
                yield UNIXDirectoryEntry0(self, fullname, inode_num)

    def get_file_entry(self, fullname: str) -> Optional[UNIXDirectoryEntry]:
        inode: UNIXInode0 = self.get_inode(fullname)  # type: ignore
        if not inode:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return UNIXDirectoryEntry0(self, fullname, inode.inode_num, inode)

    def dir(self, volume_id: str, pattern: Optional[str], options: Dict[str, bool]) -> None:
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
