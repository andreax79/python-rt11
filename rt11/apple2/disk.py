# Copyright (C) 2014 Andrea Bonomi <andrea.bonomi@gmail.com>

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

from ..block import BlockDevice
from ..commons import BLOCK_SIZE

__all__ = [
    "AppleDisk",
]

SECTOR_SIZE = 256  # Sector size in bytes
SECTORS_PER_TRACK = 16  # Number of sectors per track
BLOCKS_PER_TRACK = SECTOR_SIZE * SECTORS_PER_TRACK // BLOCK_SIZE  # Number of blocks per track
BYTES_PER_TRACK = BLOCKS_PER_TRACK * BLOCK_SIZE  # Number of bytes per track
DOS_SECTOR_ORDER = [0, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 15]


class AppleDisk(BlockDevice):
    """
    Apple II disk image DOS / ProDOS format
    """

    prodos_order: bool = False

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        if self.prodos_order:
            position = block_number * BLOCK_SIZE
            self.f.seek(position)  # not thread safe...
            return self.f.read(number_of_blocks * BLOCK_SIZE)
        elif number_of_blocks > 1:
            # DOS 3.x order - multiple blocks
            return b"".join([self.read_block(i) for i in range(block_number, block_number + number_of_blocks)])
        else:
            # DOS 3.x order
            # Each ProDOS block spans two DOS 3.x sectors
            track = block_number // BLOCKS_PER_TRACK
            chunk = (block_number % BLOCKS_PER_TRACK) * 2
            offset_1 = 256 * DOS_SECTOR_ORDER[chunk] + BYTES_PER_TRACK * track
            offset_2 = 256 * DOS_SECTOR_ORDER[chunk + 1] + BYTES_PER_TRACK * track
            # Read the data
            self.f.seek(offset_1)
            data_1 = self.f.read(BLOCK_SIZE // 2)
            self.f.seek(offset_2)
            data_2 = self.f.read(BLOCK_SIZE // 2)
            return data_1 + data_2

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        if block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        if self.prodos_order:
            position = block_number * BLOCK_SIZE
            self.f.seek(position)
            self.f.write(buffer)
        elif number_of_blocks > 1:
            # DOS 3.x order - multiple blocks
            for i in range(number_of_blocks):
                self.write_block(buffer[BLOCK_SIZE * i : BLOCK_SIZE * (i + 1)], block_number + i)
        else:
            # DOS 3.x order
            # Each ProDOS block spans two DOS 3.x sectors
            track = block_number // BLOCKS_PER_TRACK
            chunk = (block_number % BLOCKS_PER_TRACK) * 2
            offset_1 = 256 * DOS_SECTOR_ORDER[chunk] + BYTES_PER_TRACK * track
            offset_2 = 256 * DOS_SECTOR_ORDER[chunk + 1] + BYTES_PER_TRACK * track
            # Write the data
            self.f.seek(offset_1)
            self.f.write(buffer[: BLOCK_SIZE // 2])
            self.f.seek(offset_2)
            self.f.write(buffer[BLOCK_SIZE // 2 :])
