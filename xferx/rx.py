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

import typing as t

from .commons import BLOCK_SIZE

__all__ = [
    "RX_SECTOR_TRACK",
    "RX_TRACK_DISK",
    "RX01_SECTOR_SIZE",
    "RX02_SECTOR_SIZE",
    "RX01_SIZE",
    "RX02_SIZE",
    "get_sector_size",
    "rxfactr",
    "rxfactr_12bit",
    "rx_extract_12bit_words",
    "rx_pack_12bit_words",
]

RX_SECTOR_TRACK = 26  # sectors/track
RX_TRACK_DISK = 77  # track/disk
RX01_SECTOR_SIZE = 128  # RX01 bytes/sector
RX02_SECTOR_SIZE = 256  # RX02 bytes/sector
RX01_SIZE = RX_TRACK_DISK * RX_SECTOR_TRACK * RX01_SECTOR_SIZE  # RX01 Capacity
RX02_SIZE = RX_TRACK_DISK * RX_SECTOR_TRACK * RX02_SECTOR_SIZE  # RX02 Capacity

# Interleave tables for RX01 in 12-bit mode
RX01_INTERLEAVE_12B = [
    (t * RX_SECTOR_TRACK) + s - 1
    for t in range(2)
    for s in (list(range(1, RX_SECTOR_TRACK, 2)) + list(range(2, RX_SECTOR_TRACK + 1, 2)))
]

# Interleave tables for RX02 in 12-bit mode
RX02_INTERLEAVE_12B = [
    (0 * RX_SECTOR_TRACK) + s - 1
    for s in (
        list(range(1, RX_SECTOR_TRACK + 1, 3))
        + list(range(2, RX_SECTOR_TRACK + 1, 3))
        + list(range(3, RX_SECTOR_TRACK + 1, 3))
    )
]


def get_sector_size(device_size: int) -> int:
    if device_size == RX01_SIZE:
        return RX01_SECTOR_SIZE
    elif device_size == RX02_SIZE:
        return RX02_SECTOR_SIZE
    else:
        return BLOCK_SIZE


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


def rxfactr_12bit(block_number: int, sector_size: int) -> t.List[int]:
    """
    Calculates the physical position on the disk for a given logical sector
    for RX01 and RX02 in 12-bit mode
    """
    if sector_size == RX01_SECTOR_SIZE:
        interleave = RX01_INTERLEAVE_12B
    else:
        interleave = RX02_INTERLEAVE_12B
    sectors_per_block = BLOCK_SIZE // sector_size  # 4 for RX01, 2 for RX02
    repeat = len(interleave) // sectors_per_block
    base = (block_number // repeat) * repeat * BLOCK_SIZE // RX01_SECTOR_SIZE
    offset = block_number % repeat
    skip = RX_SECTOR_TRACK * sector_size
    result = []
    # Read the sectors
    for i in range(BLOCK_SIZE // sector_size):
        sector = base + interleave[(offset * sectors_per_block) + i]
        position = sector * sector_size + skip
        result.append(position)
    return result


def rx_extract_12bit_words(byte_data: bytes, position: int, sector_size: int) -> t.List[int]:
    """
    Extracts 64 12-bit words from the first 96-bytes of a 128 bytes array
    or the first 128 12-bit words from the first 192-bytes of a 256 bytes array.

    RX01 has 64 12 bit words per sector.
    The words are bit packed into the first 96 bytes.
    RX02 has 128 12 bit words per sector.
    The words are bit packed into the first 192 bytes.
    """
    if sector_size == RX01_SECTOR_SIZE:
        byte_array = byte_data[position : position + 96]
    elif sector_size == RX02_SECTOR_SIZE:
        byte_array = byte_data[position : position + 192]
    else:
        raise ValueError(f"Invalid sector size: {sector_size}")

    bit_buffer = int.from_bytes(byte_array, byteorder="big")
    total_bits = len(byte_array) * 8  # Total number of bits
    total_words = total_bits // 12  # Total number of 12-bit words
    assert total_words == 64 or total_words == 128

    words = []
    for i in range(total_words):
        # Extract the 12 bits corresponding to the current word
        word = (bit_buffer >> (total_bits - 12 * (i + 1))) & 0xFFF  # Mask the last 12 bits
        words.append(word)

    return words


def rx_pack_12bit_words(words: t.List[int], position: int, sector_size: int) -> bytes:
    """
    Converts a list of 12-bit words back to a byte array for RX01 or RX02.

    RX01: Converts 64 12-bit words to a 96-byte array.
    RX02: Converts 128 12-bit words to a 192-byte array.
    """
    if sector_size == RX01_SECTOR_SIZE:
        expected_words = 64
    elif sector_size == RX02_SECTOR_SIZE:
        expected_words = 128
    else:
        raise ValueError(f"Invalid sector size: {sector_size}")

    words = words[position : position + expected_words]
    if len(words) != expected_words:
        raise ValueError(f"Expected {expected_words} words, but got {len(words)}")

    bit_buffer = 0
    for word in words:
        bit_buffer = (bit_buffer << 12) | (word & 0xFFF)  # Pack each 12-bit word

    byte_length = (len(words) * 12 + 7) // 8  # Calculate number of bytes needed
    return bit_buffer.to_bytes(byte_length, byteorder="big")
