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
import typing as t

from .commons import BLOCK_SIZE, READ_FILE_FULL
from .rx import (
    RX01_SECTOR_SIZE,
    RX02_SECTOR_SIZE,
    get_sector_size,
    rx_extract_12bit_words,
    rx_pack_12bit_words,
    rxfactr,
    rxfactr_12bit,
)

if t.TYPE_CHECKING:
    from .abstract import AbstractFile

__all__ = [
    "BlockDevice",
    "BlockDevice12Bit",
]


class BlockDevice:
    """
    Block device
    """

    f: "AbstractFile"
    size: int  # Block device size, in bytes
    is_rx: bool  # True if this device is a RX01/RX02

    def __init__(self, file: "AbstractFile", rx_device_support: bool = True):
        self.f = file
        self.size = self.f.get_size()
        if rx_device_support:
            self.sector_size = get_sector_size(self.size)
            self.is_rx = self.sector_size in (RX01_SECTOR_SIZE, RX02_SECTOR_SIZE)
        else:
            self.sector_size = BLOCK_SIZE
            self.is_rx = False

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        if self.is_rx:
            if number_of_blocks == READ_FILE_FULL:
                number_of_blocks = int(math.ceil(self.size / BLOCK_SIZE))
            if block_number < 0 or number_of_blocks < 0:
                raise OSError(errno.EIO, os.strerror(errno.EIO))
            ret = []
            start_sector = block_number * BLOCK_SIZE // self.sector_size
            for i in range(0, number_of_blocks * BLOCK_SIZE // self.sector_size):
                blkno = start_sector + i
                position = rxfactr(blkno, self.sector_size)
                self.f.seek(position)  # not thread safe...
                ret.append(self.f.read(self.sector_size))
            return b"".join(ret)
        else:
            return self.f.read_block(block_number, number_of_blocks)

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        if block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        if self.is_rx:
            start_sector = block_number * BLOCK_SIZE // self.sector_size
            for i in range(0, number_of_blocks * BLOCK_SIZE // self.sector_size):
                blkno = start_sector + i
                position = rxfactr(blkno, self.sector_size)
                self.f.seek(position)  # not thread safe...
                self.f.write(buffer[i * self.sector_size : (i + 1) * self.sector_size])
        else:
            self.f.write_block(buffer, block_number, number_of_blocks)


class BlockDevice12Bit(BlockDevice):
    """
    Block device for 12-bit mode
    """

    is_rx_12bit: bool  # True if this device is a RX01/RX02 in 12-bit mode

    def __init__(self, file: "AbstractFile", rx_device_support: bool = True):
        super().__init__(file, rx_device_support=rx_device_support)
        self.is_rx_12bit = self.is_rx
        self.is_rx = False

    def read_12bit_words_block(self, block_number: int) -> t.List[int]:
        """
        Read a block as 256 12bit words
        """
        if self.is_rx_12bit and self.sector_size in (RX01_SECTOR_SIZE, RX02_SECTOR_SIZE):
            # Read the sectors
            result = []
            for position in rxfactr_12bit(block_number, self.sector_size):
                self.f.seek(position)
                data = self.f.read(self.sector_size)
                result.extend(rx_extract_12bit_words(data, 0, self.sector_size))
            return result
        else:
            data = self.read_block(block_number)
            return [x & 0o7777 for x in struct.unpack("<256H", data)]

    def write_12bit_words_block(
        self,
        block_number: int,
        words: t.List[int],
    ) -> None:
        """
        Write 256 12bit words as a block
        """
        if self.sector_size in (RX01_SECTOR_SIZE, RX02_SECTOR_SIZE):
            if self.sector_size == RX01_SECTOR_SIZE:
                words_per_sector = 64
            elif self.sector_size == RX02_SECTOR_SIZE:
                words_per_sector = 128
            for i, position in enumerate(rxfactr_12bit(block_number, self.sector_size)):
                words_position = i * words_per_sector
                sector_data = rx_pack_12bit_words(words, words_position, self.sector_size)
                self.f.seek(position)
                self.f.write(sector_data)
        else:
            data = struct.pack("<256H", *words)
            self.write_block(data, block_number)

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
        self.f.write_block(buffer, block_number, number_of_blocks)
