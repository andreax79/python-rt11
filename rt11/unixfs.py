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
import math
import os
import struct
import sys
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from functools import reduce
from typing import Dict, Iterator, List, Optional, Tuple, Type

from .abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from .commons import BLOCK_SIZE, READ_FILE_FULL, dump_struct, filename_match, swap_words

__all__ = [
    "UNIXFile",
    "UNIXDirectoryEntry",
    "UNIXFilesystem",
]

# ==================================================================
# Version 1 - 3

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

# ==================================================================
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

# ==================================================================
# Version 7

V7_MPB = 0o0070000  # multiplexed block special
V7_REG = 0o0100000  # regular
V7_BLK = 0o0060000  # block special
V7_DIR = 0o0040000  # directory
V7_MPC = 0o0030000  # multiplexed char special
V7_CHR = 0o0020000  # character special

V7_PERMS = [
    [(V7_BLK, "b"), (V7_DIR, "d"), (V7_CHR, "c"), (0, "-")],
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

V7_INODE_FORMAT = "<HHHH HH 40s III"
V7_FILENAME_LEN = 14
V7_INODE_SIZE = 64
V7_NADDR = 13
V7_DIR_FORMAT = f"H{V7_FILENAME_LEN}s"
V7_ROOT_INODE = 2

V7_NICINOD = 100  # number of superblock inodes
V7_NICFREE = 50  # number of superblock free blocks
V7_SUPER_BLOCK = 1  # Superblock
V7_SUPER_BLOCK_FORMAT = f"<Hlh {V7_NICFREE}l h {V7_NICINOD}H BBBB L"

assert struct.calcsize(V7_INODE_FORMAT) == V7_INODE_SIZE


# ==================================================================


def unix_canonical_filename(fullname: str, wildcard: bool = False) -> str:
    """
    Generate the canonical unix name
    """
    # TODO
    if fullname:
        fullname = fullname[:V4_FILENAME_LEN]
    return fullname


def unix_join(a: str, *p: str) -> str:
    """
    Join two or more pathname components
    """
    path = a
    if not p:
        path[:0] + "/"
    for b in p:
        if b.startswith("/"):
            path = b
        elif not path or path.endswith("/"):
            path += b
        else:
            path += "/" + b
    return path


def unix_split(p: str) -> Tuple[str, str]:
    """
    Split a pathname
    """
    i = p.rfind("/") + 1
    head, tail = p[:i], p[i:]
    if head and head != "/" * len(head):
        head = head.rstrip("/")
    return head, tail


def l3tol(data: bytes, n: int) -> List[int]:
    """
    Convert 3-byte integers
    """
    result: List[int] = []
    for i in range(0, n * 3, 3):
        t = (data[i + 1] << 0) + (data[i + 2] << 8) + (data[i + 0] << 16)
        result.append(t)
    return result


def iterate_words(data: bytes) -> Iterator[int]:
    """
    Iterate over words in a byte array
    """
    for i in range(0, len(data), 2):
        yield struct.unpack("H", data[i : i + 2])[0]


def iterate_long(data: bytes) -> Iterator[int]:
    """
    Iterate over longs in a byte array
    """
    for i in range(0, len(data), 4):
        yield swap_words(struct.unpack("I", data[i : i + 4])[0])


def format_mode(flags: int, version: int) -> str:
    result = []
    if version >= 7:
        perms = V7_PERMS
    elif version >= 4:  # Version 4 - 6
        perms = V4_PERMS
    else:  # Version 1 - 3
        perms = V1_PERMS

    for column in perms:
        ch = [ch for flag, ch in column if (flags & flag) == flag]
        if ch:
            result.append(ch[0])
    return "".join(result)


def format_time(t: int) -> str:
    mod_time = datetime.fromtimestamp(t)
    six_months_ago = datetime.now() - timedelta(days=6 * 30)
    if mod_time > six_months_ago:
        return mod_time.strftime("%b %d %H:%M")
    else:
        return mod_time.strftime("%b %d %Y ")


class UNIXFile(AbstractFile):
    inode: "UNIXInode"
    closed: bool

    def __init__(self, inode: "UNIXInode"):
        self.inode = inode
        self.closed = False

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
                t = self.inode.fs.read_block(next_block_number)
                data.extend(t)
                number_of_blocks -= 1
                if number_of_blocks == 0:
                    break
        return bytes(data)

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.inode.get_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return self.inode.get_block_size()

    def close(self) -> None:
        """
        Close the file
        """
        self.closed = True

    def __str__(self) -> str:
        return str(self.inode)


class UNIXInode(ABC):

    fs: "UNIXFilesystem"
    inode_num: int  #      inode number
    flags: int  #          flags
    nlinks: int  #         number of links to file
    uid: int  #            user ID of owner
    gid: Optional[int]  # group ID of owner
    size: int  #           size
    addr: List[int]  #     block numbers or device numbers
    atime: int = 0  #      time of last access
    mtime: int = 0  #      time of last modification
    ctime: int = 0  #      time of last change to the inode

    def __init__(self, fs: "UNIXFilesystem"):
        self.fs = fs

    @classmethod
    @abstractmethod
    def read(cls, fs: "UNIXFilesystem", inode_num: int, buffer: bytes, position: int = 0) -> "UNIXInode":
        pass

    @abstractmethod
    def blocks(self) -> Iterator[int]:
        pass

    @property
    @abstractmethod
    def isdir(self) -> bool:
        pass

    @property
    @abstractmethod
    def is_regular_file(self) -> bool:
        pass

    @property
    @abstractmethod
    def is_allocated(self) -> bool:
        pass

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return int(math.ceil(self.get_size() / self.get_block_size()))

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.size

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    def __repr__(self) -> str:
        return str(self.__dict__)


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
            addr,  #         8 words block numbers or device numbers
            self.atime,  #   1 long  time of last access
            self.mtime,  #   1 long  time of last modification
            _,  #            1 word  unused
        ) = struct.unpack_from(V1_INODE_FORMAT, buffer, position)
        self.addr = struct.unpack_from(f"{V1_NADDR}H", addr)  # type: ignore
        self.atime = swap_words(self.atime)
        self.mtime = swap_words(self.mtime)
        return self

    def blocks(self) -> Iterator[int]:
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

    def __str__(self) -> str:
        if not self.is_allocated:
            return f"{self.inode_num:>4}# ---"
        else:
            mode = format_mode(self.flags, version=self.fs.version)
            return f"{self.inode_num:>4}# uid: {self.uid:>3} nlinks: {self.nlinks:>3} size: {self.size:>5} {mode} flags: {self.flags:o}"


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

    def blocks(self) -> Iterator[int]:
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
            mode = format_mode(self.flags, version=self.fs.version)
            return f"{self.inode_num:>4}# {self.uid:>3},{self.gid:<3} nlinks: {self.nlinks:>3} size: {self.size:>8}  {mode} flags: {self.flags:06o}"


class UNIXInode6(UNIXInode4):

    @classmethod
    def read(cls, fs: "UNIXFilesystem", inode_num: int, buffer: bytes, position: int = 0) -> "UNIXInode":
        self = UNIXInode6(fs)
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

    @property
    def is_huge(self) -> bool:
        """
        Extra-large files are not marked by any flag, but only by having addr[7] non-zero
        """
        return self.is_large and (self.addr[V4_NADDR - 1] != 0)

    def blocks(self) -> Iterator[int]:
        if self.is_huge:
            # Huge file
            for index, block_number in enumerate(self.addr):
                if block_number == 0:
                    break
                if index < V4_NADDR - 1:
                    indirect_block = self.fs.read_block(block_number)
                    for n in iterate_words(indirect_block):
                        if n == 0:
                            break
                        yield n
                else:
                    double_indirect_block = self.fs.read_block(block_number)
                    for d in iterate_words(double_indirect_block):
                        if d == 0:
                            break
                        indirect_block = self.fs.read_block(d)
                        for n in iterate_words(indirect_block):
                            if n == 0:
                                break
                            yield n
        else:
            # Small and large files
            yield from super().blocks()


class UNIXInode7(UNIXInode):

    @classmethod
    def read(cls, fs: "UNIXFilesystem", inode_num: int, buffer: bytes, position: int = 0) -> "UNIXInode":
        self = UNIXInode7(fs)
        self.inode_num = inode_num
        (
            self.flags,  #    1 word  flags
            self.nlinks,  #   1 word  number of links to file
            self.uid,  #      1 word  user ID of owner
            self.gid,  #      1 word  group ID of owner
            sz0,  #           1 word  high word of size
            sz1,  #           1 word  low word of size
            addr,  #          40 chars disk block addresses
            self.atime,  #    1 long  time of last access
            self.mtime,  #    1 long  time of last modification
            self.ctime,  #    1 long  time created
        ) = struct.unpack_from(V7_INODE_FORMAT, buffer, position)
        self.addr = l3tol(addr, V7_NADDR)
        self.size = (sz0 << 16) + sz1
        self.atime = swap_words(self.atime)
        self.mtime = swap_words(self.mtime)
        self.ctime = swap_words(self.ctime)
        return self

    @property
    def isdir(self) -> bool:
        return (self.flags & V7_DIR) == V7_DIR

    @property
    def is_regular_file(self) -> bool:
        return not self.isdir

    @property
    def is_allocated(self) -> bool:
        return self.flags != 0

    def blocks(self) -> Iterator[int]:
        rem = self.get_size()
        for block_number in self.addr[:-3]:
            if block_number == 0:
                break
            rem -= self.get_block_size()
            yield block_number
        if rem > 0:
            # Indirect block
            block_number = self.addr[-3]
            if block_number != 0:
                indirect_block = self.fs.read_block(block_number)
                for n in iterate_long(indirect_block):
                    if n != 0:
                        rem -= self.get_block_size()
                        yield n
        if rem > 0:
            # Double indirect block
            block_number = self.addr[-2]
            if block_number != 0:
                double_indirect_block = self.fs.read_block(block_number)
                for d in iterate_long(double_indirect_block):
                    if d != 0:
                        indirect_block = self.fs.read_block(d)
                        for n in iterate_long(indirect_block):
                            if n != 0:
                                rem -= self.get_block_size()
                                yield n
        if rem > 0:
            # Triple indirect block
            block_number = self.addr[-1]
            if block_number != 0:
                triple_indirect_block = self.fs.read_block(block_number)
                for t in iterate_long(triple_indirect_block):
                    if t != 0:
                        double_indirect_block = self.fs.read_block(t)
                        for d in iterate_long(double_indirect_block):
                            if d != 0:
                                indirect_block = self.fs.read_block(d)
                                for n in iterate_long(indirect_block):
                                    if n != 0:
                                        rem -= self.get_block_size()
                                        yield n

    def __str__(self) -> str:
        if not self.is_allocated:
            return f"{self.inode_num:>4}# ---"
        else:
            mode = format_mode(self.flags, version=self.fs.version)
            return f"{self.inode_num:>4}# {self.uid:>3},{self.gid:<3} nlinks: {self.nlinks:>3} size: {self.size:>8}  {mode} flags: {self.flags:06o}"


class UNIXDirectoryEntry(AbstractDirectoryEntry):

    fs: "UNIXFilesystem"
    _inode: Optional["UNIXInode"]
    inode_num: int  # Inode number
    filename: str  # File name
    dirname: str  # Parent directory name

    def __init__(self, fs: "UNIXFilesystem", fullname: str, inode_num: int, inode: Optional["UNIXInode"] = None):
        self.fs = fs
        self.dirname, self.filename = unix_split(fullname)
        self.inode_num = inode_num
        self._inode = inode

    @property
    def inode(self) -> "UNIXInode":
        if self._inode is None:
            self._inode = self.fs.read_inode(self.inode_num)
        return self._inode

    @property
    def is_empty(self) -> bool:
        return not self.inode.is_allocated

    @property
    def fullname(self) -> str:
        return unix_join(self.dirname, self.filename)

    @property
    def basename(self) -> str:
        return self.filename

    def get_length(self) -> int:
        """Get the length in blocks"""
        return self.inode.get_length()

    def get_size(self) -> int:
        """Get file size in bytes"""
        return self.inode.get_size()

    def get_block_size(self) -> int:
        """Get file block size in bytes"""
        return self.inode.get_block_size()

    @property
    def creation_date(self) -> Optional[date]:
        return datetime.fromtimestamp(self.inode.mtime)

    def delete(self) -> bool:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def open(self, file_type: Optional[str] = None) -> UNIXFile:
        """
        Open a file
        """
        return UNIXFile(self.inode)

    def __lt__(self, other: "UNIXDirectoryEntry") -> bool:
        return self.filename < other.filename

    def __gt__(self, other: "UNIXDirectoryEntry") -> bool:
        return self.filename > other.filename

    def __str__(self) -> str:
        return f"{self.inode_num:>5} {self.basename}"


class UNIXFilesystem(AbstractFilesystem):
    """
    UNIX Filesystem
    """

    version: int  # UNIX version
    inode_size: int
    pwd: str
    inode_list_blocks: int  # number of blocks devoted to the i-list
    volume_size: int  # size in blocks of entire volume
    free_blocks_in_list: int  # number of free blocks in the free list
    free_inodes_in_list: int  # number of free i-numbers in the inode array
    inodes: int = 0  # number of inodes
    unix_inode_class: Type["UNIXInode"]

    def __init__(self, file: "AbstractFile", version: int):
        self.f = file
        self.version = version
        self.pwd = "/"
        if self.version in (1, 2, 3):
            self.inode_size = V1_INODE_SIZE
            self.dir_format = V1_DIR_FORMAT
            self.root_inode = V1_ROOT_INODE
            self.unix_inode_class = UNIXInode1
        elif self.version in (4, 5, 6):
            self.inode_size = V4_INODE_SIZE
            self.dir_format = V4_DIR_FORMAT
            self.root_inode = V4_ROOT_INODE
            self.unix_inode_class = UNIXInode6 if self.version == 6 else UNIXInode4
        elif self.version == 7:
            self.inode_size = V7_INODE_SIZE
            self.dir_format = V7_DIR_FORMAT
            self.root_inode = V7_ROOT_INODE
            self.unix_inode_class = UNIXInode7
        else:
            raise ValueError(f"Invalid version {self.version}")
        self.read_superblock()

    def read_superblock(self) -> None:
        """Read superblock"""
        if self.version in (4, 5, 6):
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

        elif self.version == 7:
            superblock_data = self.read_block(V7_SUPER_BLOCK)
            superblock = struct.unpack_from(V7_SUPER_BLOCK_FORMAT, superblock_data, 0)
            self.inode_list_blocks = superblock[0]  # number of blocks devoted to the i-list
            self.volume_size = swap_words(superblock[1])  # size in blocks of entire volume
            self.free_blocks_in_list = superblock[2]  # number of free blocks in the free list
            self.free_blocks_list = list([swap_words(x) for x in superblock[3 : 3 + V7_NICINOD]])  # free block list
            self.free_inodes_in_list = superblock[3 + V7_NICINOD]  # number of free inodes in the inode list
            self.free_inodes_list = list(superblock[4 + V7_NICINOD : 4 + V7_NICINOD + V7_NICFREE])  # free inode list
            # _ = superblock[4 + V7_NICINOD + V7_NICFREE]  # lock during free list manipulation
            # _ = superblock[5 + V7_NICINOD + V7_NICFREE]  # lock during i-list manipulation
            # _ = superblock[6 + V7_NICINOD + V7_NICFREE]  # flag to indicate that the super-block has changed and should be written
            # _ = superblock[7 + V7_NICINOD + V7_NICFREE]  # mounted read-only flag
            # _ = swap_words(superblock[8 + V7_NICINOD + V7_NICFREE]) # last super block update
            self.inodes = (self.inode_list_blocks - 1) * (BLOCK_SIZE // self.inode_size)

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        return self.f.read_block(block_number, number_of_blocks)

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def read_inode(self, inode_num: int) -> UNIXInode:
        """
        Read inode by number
        """
        self.f.seek(BLOCK_SIZE * 2 + (inode_num - 1) * self.inode_size)
        data = self.f.read(self.inode_size)
        return self.unix_inode_class.read(self, inode_num, data)

    def get_inode(self, path: str, inode_num: int = -1) -> Optional["UNIXInode"]:
        """
        Get inode by path
        """
        if inode_num == -1:
            inode_num = self.root_inode
        if path and path[0] == "/":
            return self.get_inode(path.strip("/"))
        inode = self.read_inode(inode_num)
        if not path:
            if inode.is_allocated:
                inode.inode_num = inode_num
                return inode
            return None
        if inode.isdir:
            name, tail = path.split("/", 1) if "/" in path else (path, "")
            for no, nm in self.list_dir(inode):
                if nm != name:
                    continue
                return self.get_inode(tail, no)
        return None

    def list_dir(self, inode: UNIXInode) -> List[Tuple[int, str]]:
        if not inode.isdir:
            return []
        files = []
        f = UNIXFile(inode)
        try:
            while True:
                data = f.read(struct.calcsize(self.dir_format))
                inum, name = struct.unpack_from(self.dir_format, data)
                if inum > 0:
                    name_ascii = name.decode("ascii", errors="ignore").rstrip("\x00")
                    files.append((inum, name_ascii))
        except IOError:
            pass
        finally:
            f.close()
        return files

    def read_dir_entries(self, dirname: str) -> Iterator["UNIXDirectoryEntry"]:
        inode = self.get_inode(dirname)
        if inode:
            for inode_num, filename in self.list_dir(inode):
                fullname = unix_join(dirname, filename)
                yield UNIXDirectoryEntry(self, fullname, inode_num)

    def filter_entries_list(
        self,
        pattern: Optional[str],
        include_all: bool = False,
        wildcard: bool = True,
    ) -> Iterator["UNIXDirectoryEntry"]:
        if not pattern:
            yield from self.read_dir_entries(self.pwd)
        else:
            if not pattern.startswith("/"):
                dirname = self.pwd
            elif self.isdir(pattern):
                dirname = pattern
                pattern = "*"
            else:
                dirname, pattern = unix_split(pattern)
            for entry in self.read_dir_entries(dirname):
                if filename_match(entry.basename, pattern, wildcard):
                    yield entry

    @property
    def entries_list(self) -> Iterator["UNIXDirectoryEntry"]:
        yield from self.read_dir_entries(self.pwd)

    def get_file_entry(self, fullname: str) -> Optional[UNIXDirectoryEntry]:
        inode = self.get_inode(fullname)
        if not inode:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return UNIXDirectoryEntry(self, fullname, inode.inode_num, inode)

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: Optional[date] = None,
        file_type: Optional[str] = None,
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def create_file(
        self,
        fullname: str,
        length: int,  # length in blocks
        creation_date: Optional[date] = None,  # optional creation date
        file_type: Optional[str] = None,
    ) -> Optional[UNIXDirectoryEntry]:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def isdir(self, fullname: str) -> bool:
        inode = self.get_inode(fullname)
        return inode is not None and inode.isdir

    def read_uids(self) -> Dict[int, str]:
        """
        Read the uid -> name map
        """
        result: Dict[int, str] = {}
        filename = "/etc/uids" if self.version < 3 else "/etc/passwd"
        try:
            for line in self.read_text(filename).split("\n"):
                if self.version < 3:
                    try:
                        name, uid = line.split(":", 1)
                        result[int(uid)] = name
                    except Exception:
                        pass
                else:
                    try:
                        name, _, uid, _ = line.split(":", 3)
                        result[int(uid)] = name
                    except Exception:
                        pass
        except Exception:
            pass
        return result

    def dir(self, volume_id: str, pattern: Optional[str], options: Dict[str, bool]) -> None:
        entries = sorted(self.filter_entries_list(pattern, include_all=True, wildcard=True))
        if not entries:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), pattern)
        uids = self.read_uids()
        if not options.get("brief"):
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
            else:
                mode = format_mode(x.inode.flags, self.version)
                time = format_time(x.inode.mtime)
                uid = uids.get(x.inode.uid, str(x.inode.uid))
                if self.version < 3:
                    sys.stdout.write(
                        f"{x.inode_num:>3} {mode} {x.inode.nlinks:>2} {uid:<6} {x.inode.size:>6} {time} {x.basename}\n"
                    )
                else:
                    sys.stdout.write(
                        f"{x.inode_num:>5} {mode}{x.inode.nlinks:>2} {uid:<6}{x.inode.size:>7} {time} {x.basename}\n"
                    )

    def examine(self, arg: Optional[str]) -> None:
        if arg:
            if arg.isnumeric():
                # Dump the inode by number
                inode_num = int(arg)
                inode: Optional[UNIXInode] = self.read_inode(inode_num)
            else:
                # Dump the inode by path
                inode = self.get_inode(arg)
            if inode:
                sys.stdout.write(dump_struct(inode.__dict__) + "\n")
                if inode.isdir:
                    # Dump the directory entries
                    sys.stdout.write("Directory entries:\n")
                    for inode_num, filename in self.list_dir(inode):
                        child_inode: Optional[UNIXInode] = self.read_inode(inode_num)
                        sys.stdout.write(f"{child_inode} {filename}\n")
        else:
            # Dump the entire filesystem
            sys.stdout.write(dump_struct(self.__dict__))
            sys.stdout.write("\n")
            for i in range(1, self.inodes + 1):
                inode = self.read_inode(i)
                sys.stdout.write(f"{inode}\n")

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def close(self) -> None:
        self.f.close()

    def chdir(self, fullname: str) -> bool:
        """
        Change the current directory
        """
        if not fullname.startswith("/"):
            fullname = unix_join(self.pwd, fullname)
        fullname = os.path.normpath(fullname)
        if self.isdir(fullname):
            self.pwd = fullname
            return True
        else:
            return False

    def get_pwd(self) -> str:
        """
        Get the current directory
        """
        return self.pwd
