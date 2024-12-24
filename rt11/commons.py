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
    "ASCII",
    "IMAGE",
    "READ_FILE_FULL",
    "PartialMatching",
    "bytes_to_word",
    "date_to_rt11",
    "dump_struct",
    "filename_match",
    "getch",
    "hex_dump",
    "splitdrive",
    "swap_words",
    "word_to_bytes",
]

import fnmatch
import sys
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

BLOCK_SIZE = 512
BYTES_PER_LINE = 16
READ_FILE_FULL = -1
ASCII = "ASCII"  # Copy in ASCII mode
IMAGE = "IMAGE"  # Copy in image mode


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


def swap_words(val: int) -> int:
    """
    Swap high order and low order word in a 32-bit integer
    """
    return (val >> 16) + ((val & 0xFFFF) << 16)


def hex_dump(data: bytes, bytes_per_line: int = BYTES_PER_LINE) -> None:
    """
    Display contents in hexadecimal
    """
    for i in range(0, len(data), bytes_per_line):
        line = data[i : i + bytes_per_line]
        hex_str = " ".join([f"{x:02x}" for x in line])
        ascii_str = "".join([chr(x) if 32 <= x <= 126 else "." for x in line])
        sys.stdout.write(f"{i:08x}   {hex_str.ljust(3 * bytes_per_line)}  {ascii_str}\n")


def dump_struct(d: Dict[str, Any], exclude: List[str] = [], include: List[str] = []) -> str:
    result: List[str] = []
    for k, v in d.items():
        if (type(v) in (int, str, bytes, list, bool) or k in include) and k not in exclude:
            if len(k) < 6:
                label = k.upper() + ":"
            else:
                label = k.replace("_", " ").title() + ":"
            result.append(f"{label:20s}{v}")
    return "\n".join(result)


def filename_match(basename: str, pattern: Optional[str], wildcard: bool) -> bool:
    if not pattern:
        return True
    if wildcard:
        return fnmatch.fnmatch(basename, pattern)
    else:
        return basename == pattern


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
        return msvcrt.getch()  # type: ignore


class PartialMatching:

    def __init__(self) -> None:
        self.short: Dict[str, str] = {}  # short key => full key
        self.full: Dict[str, str] = {}  # full key => short key

    def add(self, key: str) -> None:
        try:
            prefix, tail = key.split("_", 1)
        except:
            prefix = key
            tail = ""
        full = prefix + tail
        self.full[full] = prefix
        self.short[prefix] = full

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        try:
            return self.short[key]
        except KeyError:
            pass
        matching_keys = [(k, v) for k, v in self.full.items() if k.startswith(key) and len(key) >= len(v)]
        if not matching_keys:
            return default
        return matching_keys[0][0]
