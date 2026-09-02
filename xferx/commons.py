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
    "ASCII",
    "BLOCK_SIZE",
    "BlockDirection",
    "DATA_FORK",
    "DECTAPE",
    "DECTAPE_EXT",
    "Direction",
    "IMAGE",
    "LoggingLevel",
    "READ_FILE_FULL",
    "RESOURCE_FORK",
    "TrackSector",
    "bytes_to_word",
    "cache",
    "dump_struct",
    "filename_match",
    "format_sizegetch",
    "hex_dump",
    "pad_words",
    "pairwise",
    "splitdrive",
    "swap_words",
    "word_to_bytes",
]

import fnmatch
import sys
import typing as t
from dataclasses import dataclass
from enum import Enum, IntEnum

if sys.version_info >= (3, 9):
    from functools import cache
else:
    from functools import lru_cache

    _F = t.TypeVar("_F", bound=t.Callable[..., t.Any])

    def cache(user_function: _F, /) -> _F:
        return lru_cache(maxsize=None)(user_function)


BLOCK_SIZE = 512
BYTES_PER_LINE = 16
READ_FILE_FULL = -1
ASCII = "ASCII"  # Copy in ASCII mode
IMAGE = "IMAGE"  # Copy in image mode

DECTAPE_EXT = ["tu56", "tap", "dta"]  # DECtape file extensions
DECTAPE = "dectape"  # DECtape device type

DATA_FORK = "DATA"
RESOURCE_FORK = "RESOURCE"


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


def splitdrive(path: str) -> t.Tuple[str, str]:
    """
    Split a pathname into drive and path.
    """
    result = path.split(":", 1)
    if len(result) < 2:
        return ("DK", path)
    else:
        return (result[0].upper(), result[1])


def swap_words(val: int) -> int:
    """
    Swap high order and low order word in a 32-bit integer
    """
    return (val >> 16) + ((val & 0xFFFF) << 16)


def pad_words(words: t.List[int], size: int) -> t.List[int]:
    """
    Pad a list of words to a given size with zeros
    """
    return words + [0] * (size - len(words))


def format_size(size: float, si: bool = True, precision: str = "3.1f") -> str:
    """
    Format size in human readable format
    """
    power = 1000 if si else 1024
    for unit in ("", "K", "M", "G", "T"):
        if abs(size) < power or unit == "T":
            break
        size = size / power
    return f"{size:{precision}}{unit}" if unit else str(int(size))


def hex_dump(data: bytes, bytes_per_line: int = BYTES_PER_LINE) -> None:
    """
    Display contents in hexadecimal
    """
    for i in range(0, len(data), bytes_per_line):
        line = data[i : i + bytes_per_line]
        hex_str = " ".join([f"{x:02x}" for x in line])
        ascii_str = "".join([chr(x) if 32 <= x <= 126 else "." for x in line])
        sys.stdout.write(f"{i:08x}   {hex_str.ljust(3 * bytes_per_line)}  {ascii_str}\n")


def dump_struct(
    d: t.Dict[str, t.Any],
    exclude: t.List[str] = [],
    include: t.List[str] = [],
    width: int = 20,
    newline: bool = False,
    format_label: bool = True,
) -> str:
    result: t.List[str] = []
    for k, v in d.items():
        if (type(v) in (int, str, bytes, list, bool) or k in include) and k not in exclude:
            if not format_label:
                label = k + ":"
            elif len(k) < 6:
                label = k.upper() + ":"
            else:
                label = k.replace("_", " ").title() + ":"
            label = label.ljust(width)
            result.append(f"{label}{v}")
    if newline:
        result.append("")
    return "\n".join(result)


def filename_match(name: str, pattern: t.Optional[str], wildcard: bool) -> bool:
    if not pattern:
        return True
    if wildcard:
        return fnmatch.fnmatch(name, pattern)
    else:
        return name == pattern


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


class TrackSector(t.NamedTuple):
    """
    A track/sector pair
    """

    track: int
    sector: int

    def __repr__(self) -> str:
        return f"{self.track}/{self.sector}"


T = t.TypeVar('T')


def pairwise(iterator: t.Iterable[T]) -> t.Iterator[t.Tuple[T, T]]:
    """
    Iterate over an iterable in pairs.
    """
    it = iter(iterator)
    while True:
        try:
            yield next(it), next(it)
        except StopIteration:
            break


class Direction(Enum):
    """
    The direction of a block transfer
    """

    FORWARD = 0
    BACKWARD = 1

    def reverse(self) -> "Direction":
        """
        Reverse the direction
        """
        return Direction.BACKWARD if self == Direction.FORWARD else Direction.FORWARD


@dataclass
class BlockDirection:
    """
    A block number with a direction
    """

    block_number: int
    direction: Direction

    def __str__(self) -> str:
        prefix = "F" if self.direction == Direction.FORWARD else "D"
        return f"{prefix}{self.block_number}"

    def __repr__(self) -> str:
        return str(self)


class LoggingLevel(IntEnum):
    DEBUG = 10
    INFO = 20
    ERROR = 40
