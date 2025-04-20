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
import typing as t
from dataclasses import dataclass

from ..abstract import AbstractFile
from ..block import BlockDevice
from ..commons import BLOCK_SIZE

__all__ = [
    "AppleDisk",
    "TrackSector",
]

SECTOR_SIZE = 256  # Sector size in bytes
SECTORS_PER_TRACK = 16  # Number of sectors per track
BLOCKS_PER_TRACK = SECTOR_SIZE * SECTORS_PER_TRACK // BLOCK_SIZE  # Number of blocks per track
BYTES_PER_TRACK = BLOCKS_PER_TRACK * BLOCK_SIZE  # Number of bytes per track
DOS_SECTOR_ORDER = [0, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 15]


class TrackSector(t.NamedTuple):
    track: int
    sector: int

    def __repr__(self) -> str:
        return f"{self.track}/{self.sector}"


@dataclass
class FloppySize:
    number_of_tracks: int  # Number of tracks
    sectors_per_track: int  # Number of sectors per track

    @property
    def size(self) -> int:
        """
        Return the size of the floppy disk in bytes
        """
        return self.number_of_tracks * self.sectors_per_track * SECTOR_SIZE

    @classmethod
    def from_size(cls, size: int) -> "FloppySize":
        """
        Return the floppy size object for the given size
        """
        for floppy_size in FLOPPY_SIZES:
            if floppy_size.size == size:
                return floppy_size
        raise ValueError(f"Unknown floppy size: {size}")


FLOPPY_SIZES = [
    FloppySize(35, 16),  # 5.25" floppy (140 KiB)
    FloppySize(35, 13),  # 5.25" floppy (113 KiB)
    FloppySize(36, 16),  # extra track 5.25" floppy (144 KiB)
    FloppySize(36, 13),  # extra track 5.25" floppy (117 KiB)
    FloppySize(40, 16),  # 5.25" floppy (160 KiB)
    FloppySize(80, 16),  # 5.25" floppy (320 KiB)
    FloppySize(50, 16),  # (200 KiB)
    FloppySize(50, 32),  # (400 KiB)
]


class AppleDisk(BlockDevice):
    """
    Apple II disk image DOS / ProDOS format
    """

    prodos_order: bool = False
    sectors_per_track: int = SECTORS_PER_TRACK
    number_of_tracks: int = 0

    def __init__(self, file: "AbstractFile", rx_device_support: bool = False):
        super().__init__(file, rx_device_support)
        # Determine the number of sectors per track
        try:
            floppy_size = FloppySize.from_size(file.get_size())
            self.sectors_per_track = floppy_size.sectors_per_track
            self.number_of_tracks = floppy_size.number_of_tracks
        except ValueError:
            self.sectors_per_track = SECTORS_PER_TRACK
            self.number_of_tracks = file.get_size() // SECTOR_SIZE // self.sectors_per_track

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
            offset_1 = SECTOR_SIZE * DOS_SECTOR_ORDER[chunk] + BYTES_PER_TRACK * track
            offset_2 = SECTOR_SIZE * DOS_SECTOR_ORDER[chunk + 1] + BYTES_PER_TRACK * track
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
            offset_1 = SECTOR_SIZE * DOS_SECTOR_ORDER[chunk] + BYTES_PER_TRACK * track
            offset_2 = SECTOR_SIZE * DOS_SECTOR_ORDER[chunk + 1] + BYTES_PER_TRACK * track
            # Write the data
            self.f.seek(offset_1)
            self.f.write(buffer[: BLOCK_SIZE // 2])
            self.f.seek(offset_2)
            self.f.write(buffer[BLOCK_SIZE // 2 :])

    def read_sector(self, address: TrackSector) -> bytes:
        sector = address.sector
        if self.prodos_order:
            sector = DOS_SECTOR_ORDER[sector % len(DOS_SECTOR_ORDER)]
        position = (address.track * self.sectors_per_track + sector) * SECTOR_SIZE
        self.f.seek(position)
        return self.f.read(SECTOR_SIZE)

    def write_sector(self, buffer: bytes, address: TrackSector) -> None:
        sector = address.sector
        if self.prodos_order:
            sector = DOS_SECTOR_ORDER[sector % len(DOS_SECTOR_ORDER)]
        position = (address.track * self.sectors_per_track + sector) * SECTOR_SIZE
        self.f.seek(position)
        self.f.write(buffer)
