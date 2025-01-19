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
import typing as t

from ..block import BlockDevice
from ..commons import ASCII, IMAGE

__all__ = [
    "BlockDevice18Bit",
    "from_18bit_words_to_bytes",
    "from_bytes_to_18bit_words",
    "BYTES_PER_WORD_18BIT",
]


BYTES_PER_WORD_18BIT = 4  # Each word is encoded in 4 bytes
WORDS_PER_BLOCK = 256  # Number of words per block


def from_18bit_words_to_bytes(words: list[int], file_type: str = ASCII) -> bytes:
    """
    Convert 18bit words to 3 bytes (IMAGE) or 2 bytes (ASCII)
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


def from_bytes_to_18bit_words(data: bytes, file_type: str = ASCII) -> t.List[int]:
    """
    Convert 3 bytes to 18bit words, keeping only the lower 6 bits of each byte (IMAGE)
    or 2 bytes to 18bit words (ASCII)
    """
    words = []
    if file_type == ASCII:
        for i in range(0, len(data), 2):
            words.append((data[i] << 9) | data[i + 1])
    else:
        for i in range(0, len(data), 3):
            words.append(((data[i] - 0x80) << 12) | ((data[i + 1] - 0x80) << 6) | (data[i + 2] - 0x80))
    return words


class BlockDevice18Bit(BlockDevice):
    """
    Block device for 18-bit mode
    """

    words_per_block: int = WORDS_PER_BLOCK

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
    ) -> t.List[int]:
        """
        Read a 256 bytes block as 18bit words
        """
        self.f.seek(block_number * self.words_per_block * BYTES_PER_WORD_18BIT)
        buffer = self.f.read(self.words_per_block * BYTES_PER_WORD_18BIT)
        if len(buffer) < self.words_per_block * BYTES_PER_WORD_18BIT:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return list(struct.unpack(f"{self.words_per_block}I", buffer))

    def write_18bit_words_block(
        self,
        block_number: int,
        words: t.List[int],
    ) -> None:
        """
        Write 256 18bit words as a block
        """
        self.f.seek(block_number * self.words_per_block * BYTES_PER_WORD_18BIT)
        buffer = struct.pack(f"{self.words_per_block}I", *words)
        self.f.write(buffer)
