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

__all__ = [
    "RAD50",
    "rad50_word_to_asc",
    "rad2asc",
    "asc_to_rad50_word",
    "asc2rad",
]

from ..commons import bytes_to_word, word_to_bytes

RAD50_ALT = "\0ABCDEFGHIJKLMNOPQRSTUVWXYZ$.%0123456789:"
RAD50 = "\0ABCDEFGHIJKLMNOPQRSTUVWXYZ$%*0123456789:"


def rad50_word_to_asc(val: int) -> str:
    """
    Convert RAD50 word to 0-3 chars of ASCII
    """
    # split out RAD50 digits into three ASCII characters a/b/c
    c = RAD50[val % 0x28]
    b = RAD50[(val // 0x28) % 0x28]
    a = RAD50[val // (0x28 * 0x28)]
    result = ""
    if a != "\0":
        result += a
    if b != "\0":
        result += b
    if c != "\0":
        result += c
    return result


def rad2asc(buffer: bytes, position: int = 0) -> str:
    """
    Convert RAD50 2 bytes to 0-3 chars of ASCII
    """
    return rad50_word_to_asc(bytes_to_word(buffer, position=position))


def asc_to_rad50_word(val: str) -> int:
    """
    Convert a string of 3 ASCII to a RAD50 word
    """
    val1 = [RAD50.find(c.upper()) for c in val] + [0, 0, 0]
    val2 = [x > 0 and x or 0 for x in val1]
    return (val2[0] * 0x28 + val2[1]) * 0x28 + val2[2]


def asc2rad(val: str) -> bytes:
    """
    Convert a string of 3 ASCII to a RAD50 2 bytes
    """
    return word_to_bytes(asc_to_rad50_word(val))
