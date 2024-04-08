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

from .commons import BLOCK_SIZE

__all__ = [
    "RX_SECTOR_TRACK",
    "RX_TRACK_DISK",
    "RX01_SECTOR_SIZE",
    "RX02_SECTOR_SIZE",
    "RX01_SIZE",
    "RX02_SIZE",
    "rxfactr",
]

RX_SECTOR_TRACK = 26  # sectors/track
RX_TRACK_DISK = 77  # track/disk
RX01_SECTOR_SIZE = 128  # RX01 bytes/sector
RX02_SECTOR_SIZE = 256  # RX02 bytes/sector
RX01_SIZE = RX_TRACK_DISK * RX_SECTOR_TRACK * RX01_SECTOR_SIZE  # RX01 Capacity
RX02_SIZE = RX_TRACK_DISK * RX_SECTOR_TRACK * RX02_SECTOR_SIZE  # RX02 Capacity


def rxfactr(blkno: int, sector_size: int) -> int:
    """
    Calculates the physical position on the disk for a given logical sector
    """
    if sector_size == RX01_SECTOR_SIZE or sector_size == RX02_SECTOR_SIZE:
        track = blkno // RX_SECTOR_TRACK + 1
        i = (blkno % RX_SECTOR_TRACK) << 1
        if i >= RX_SECTOR_TRACK:
            i += 1
        sector = ((i + (6 * (track - 1))) % RX_SECTOR_TRACK) + 1
        if track >= RX_TRACK_DISK:
            track = 0
        position = track * 3328 + (sector - 1) * sector_size
    else:
        position = blkno * BLOCK_SIZE
    return position
