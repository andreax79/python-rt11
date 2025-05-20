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

from ..commons import swap_words
from .commons import UNIXFilesystem, UNIXInode, iterate_words
from .unix4fs import V4_INODE_FORMAT, V4_NADDR, UNIX4Filesystem, UNIXInode4


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

    def blocks(self) -> t.Iterator[int]:
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


class UNIX6Filesystem(UNIX4Filesystem):
    """
    UNIX version 6 Filesystem
    """

    fs_name = "unix6"
    fs_description = "UNIX version 6"
    version: int = 6  # UNIX version
    unix_inode_class = UNIXInode6
