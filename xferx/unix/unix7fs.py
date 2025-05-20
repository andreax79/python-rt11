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
from ..commons import BLOCK_SIZE, swap_words
from .commons import UNIXFilesystem, UNIXInode, format_mode, iterate_long, l3tol
from .unix4fs import (
    V4_RGRP,
    V4_ROTH,
    V4_ROWN,
    V4_SGID,
    V4_STXT,
    V4_SUID,
    V4_WGRP,
    V4_WOTH,
    V4_WOWN,
    V4_XGRP,
    V4_XOTH,
    V4_XOWN,
)

__all__ = ["UNIX7Filesystem", "UNIXInode7"]

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

    def blocks(self) -> t.Iterator[int]:
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
                for tmp in iterate_long(triple_indirect_block):
                    if tmp != 0:
                        double_indirect_block = self.fs.read_block(tmp)
                        for d in iterate_long(double_indirect_block):
                            if d != 0:
                                indirect_block = self.fs.read_block(d)
                                for n in iterate_long(indirect_block):
                                    if n != 0:
                                        rem -= self.get_block_size()
                                        yield n

    def examine(self) -> str:
        buf = io.StringIO()
        buf.write("\n*Inode\n")
        buf.write(f"Inode number:          {self.inode_num:>6}\n")
        buf.write(f"Flags:                 {self.flags:>06o}\n")
        if self.isdir:
            buf.write("Type:               directory\n")
        # elif self.is_special_file:
        #     buf.write("Type:            special file\n")
        # elif self.is_large:
        #     buf.write("Type:              large file\n")
        else:
            buf.write("Type:                    file\n")
        buf.write(f"Owner user id:         {self.uid:>6}\n")
        buf.write(f"Group user id:         {self.gid:>6}\n")
        buf.write(f"Link count:            {self.nlinks:>6}\n")
        buf.write(f"Size:                  {self.size:>6}\n")
        # if self.is_large:
        #     buf.write(f"Indirect blocks:       {self.addr}\n")
        buf.write(f"Blocks:                {list(self.blocks())}\n")
        return buf.getvalue()

    def __str__(self) -> str:
        if not self.is_allocated:
            return f"{self.inode_num:>4}# ---"
        else:
            mode = format_mode(self.flags, version=self.fs.version, perms=self.fs.perms)
            return f"{self.inode_num:>4}# {self.uid:>3},{self.gid:<3} nlinks: {self.nlinks:>3} size: {self.size:>8}  {mode} flags: {self.flags:06o}"


class UNIX7Filesystem(UNIXFilesystem):
    """
    UNIX version 7 Filesystem
    """

    fs_name = "unix7"
    fs_description = "UNIX version 7"
    version: int = 7  # UNIX version
    inode_size = V7_INODE_SIZE
    dir_format = V7_DIR_FORMAT
    root_inode = V7_ROOT_INODE
    perms = V7_PERMS
    unix_inode_class = UNIXInode7

    @classmethod
    def mount(cls, file: "AbstractFile") -> "AbstractFilesystem":
        self = cls(file)
        self.pwd = "/"
        self.read_superblock()
        return self

    def read_superblock(self) -> None:
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
