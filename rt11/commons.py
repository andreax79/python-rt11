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
    "BLOCK_SIZE",
    "bytes_to_word",
    "word_to_bytes",
    "splitdrive",
    "date_to_rt11",
    "getch",
]

import sys
from datetime import date
from typing import Optional, Tuple

BLOCK_SIZE = 512


def bytes_to_word(val: bytes, position: int = 0) -> int:
    """
    Converts two bytes to a single integer (word)
    """
    return val[1 + position] << 8 | val[0 + position]


def word_to_bytes(val: int) -> bytes:
    """
    Converts an integer (word) to two bytes
    """
    return bytes([val % 256, val // 256])


def splitdrive(path: str) -> Tuple[str, str]:
    """
    Split a pathname into drive and path.
    """
    result = path.split(":", 1)
    if len(result) < 2:
        return ("DK", path)
    else:
        return (result[0].upper(), result[1])


def date_to_rt11(val: Optional[date]) -> int:
    """
    Translate Python date to RT-11 date
    """
    if val is None:
        return 0
    age = (val.year - 1972) // 32
    if age < 0:
        age = 0
    elif age > 3:
        age = 3
    year = (val.year - 1972) % 32
    return year + (val.day << 5) + (val.month << 10) + (age << 14)


try:
    import termios
    import tty

    def getch() -> str:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch

except Exception:
    import msvcrt

    def getch() -> str:
        return msvcrt.getch()