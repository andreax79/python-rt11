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
import io
import os
import sys
import math
import typing as t
from dataclasses import dataclass, fields, field
from datetime import datetime, timedelta, date

from ..abstract import AbstractBlockFilesystem, XFERXPath, AbstractFile
from ..device.abstract import AbstractDevice
from ..device.block_36bit import BlockDevice36Bit
from ..commons import filename_match, READ_FILE_FULL, IMAGE, ASCII
from ..uic import UIC

__all__ = [
    "TOPS10Filesystem",
]

WORDS_PER_BLOCK = 128  # Number of 36-bit words per block
DISK_IMAGE_WORD_SIZE = 8
DISK_IMAGE_BLOCK_SIZE = WORDS_PER_BLOCK * DISK_IMAGE_WORD_SIZE
PDP10_HALF_MASK = (1 << 18) - 1  # Mask for 18-bit half of a PDP-10 word
HOME_SIGNATURE = 0o505755000000  # "HOM" in SIXBIT
MFD_ENTRY_SIZE = 2  # MFD entry size in words
UFD_ENTRY_SIZE = 2  # UFD entry size in words

# List of known TOPS-10 file extensions that are typically ASCII files
TEXT_EXTENSIONS = [
    ".ALP",  # printer forms alignment
    ".ATO",  # PTYCON automatic command file
    ".BAK",  # TECO backup file
    ".B10",  # BLISS-10 source
    ".B20",  # BASIC-PLUS-2/20 source
    ".B36",  # BLISS-36 specifications/manuals
    ".BTC",  # DSR output for TOC input
    ".BWR",  # beware/warning file
    ".C68",  # COBOL-68 source
    ".C74",  # COBOL-74 source
    ".CBL",  # COBOL source
    ".CCL",  # LINK command file
    ".CED",  # COPYED input
    ".CFL",  # RUNFIL command file
    ".DIR",  # DIRECTORY command output
    ".DMP",  # COBOL compiler dump
    ".DOC",  # software modification documentation
    ".ERR",  # error message file
    ".FAI",  # FAIL source
    ".FCL",  # FOCAL source
    ".FLO",  # English-language flowchart
    ".FOR",  # FORTRAN source
    ".FRM",  # blank form
    ".FTP",  # FORTRAN test programs
    ".GND",  # automatic wirewrap ground-pin list
    ".HLP",  # help text
    ".LAP",  # LISP compiler output
    ".LIB",  # COBOL source library
    ".LOG",  # batch/PTYCON/LINK log
    ".LPT",  # spooled line-printer output
    ".LSP",  # LISP source
    ".OLD",  # bject: backup source program (may be source)
    ".OPR",  # installation/assembly instructions
    ".P11",  # MACX11 source
    ".PAL",  # PAL-10/PDP-8 assembler source
    ".PAS",  # Pascal source
    ".RNO",  # RUNOFF/DSR input
    ".RSP",  # SCRIPT response-time log
    ".RUN",  # SYSJOB command file
    ".SAI",  # SAIL source
    ".SCD",  # directory differences
    ".SCM",  # FILCOM source-comparison listing
    ".SPT",  # SPRINT-created file
    ".SPU",  # SPELL uppercase-word file
    ".SPX",  # SPELL exception/error-line file
    ".SRC",  # source files
    ".STD",  # standards
    ".TEC",  # TECO
]


class PPN(UIC):
    """
    TOPS-10 Project-Programmer Numbers
    """

    GROUP_BITS = 18
    USER_BITS = 18


ANY_PPN = PPN.from_str("[*,*]")
MFD_PPN = PPN.from_str("[1,1]")


def dump_dataclass(instance: object, title: str, newline: bool = True) -> str:
    buf = io.StringIO()
    buf.write(f"{title}:\n")
    for item in fields(instance):  # type: ignore
        value = getattr(instance, item.name)
        if item.name == "words" or item.name == "fs":
            continue
        elif isinstance(value, int):
            value = f"{value:012o} (octal)"
        elif isinstance(value, tuple) and all(isinstance(part, int) for part in value):
            value = " ".join(f"{part:012o}" for part in value)
        description = item.metadata.get("description", "")
        buf.write(f"  {item.name.upper():<10} {str(value):<24} {description}\n")
    if newline:
        buf.write("\n")
    return buf.getvalue()


def sixbit_to_ascii(word: int) -> str:
    """Decode a six-character PDP-10 SIXBIT word."""
    return "".join(chr(((word >> shift) & 0o77) + 0o40) for shift in range(30, -1, -6)).rstrip()


def ascii_to_sixbit(s: str) -> int:
    """Encode a six-character PDP-10 SIXBIT word."""
    s = s.upper().ljust(6)[:6]
    return sum(((ord(c) - 0o40) & 0o77) << shift for c, shift in zip(s, range(30, -1, -6)))


def ascii_to_word(s: bytes) -> int:
    """Encode up to five 7-bit ASCII characters in a PDP-10 36-bit word."""
    s = s.ljust(5)[:5]
    return sum((c & 0x7F) << shift for c, shift in zip(s, range(29, -1, -7)))


def from_36bit_words_to_bytes(words: list[int]) -> bytes:
    """
    Convert 36bit words to bytes

    Decode five 7-bit ASCII characters from a PDP-10 36-bit word
    """
    data = bytearray()
    for word in words:
        data.extend((word >> shift) & 0x7F for shift in range(29, -1, -7))
    return bytes(data)  # .rstrip()


def left_half(word: int) -> int:
    """Return the left 18-bit half of a PDP-10 word."""
    return (word >> 18) & PDP10_HALF_MASK


def right_half(word: int) -> int:
    """Return the right 18-bit half of a PDP-10 word."""
    return word & PDP10_HALF_MASK


def tops10_datetime(ribprv: int, ribtime: int) -> t.Union[date, datetime, None]:
    """
    Decode a TOPS-10 file creation date/time.

    Arguments are PDP-10 36-bit words supplied as Python integers.
    They are normally written in octal, e.g.:

        ribprv  = int("57016607542", 8)
        ribtime = int("154623327762", 8)

    Returns a naive datetime representing the TOPS-10 timestamp.

    The legacy date is in RIBPRV bits 24-35.  For DATE75-era files,
    the three high-order date bits extend this 12-bit value.

    RIBTIM supplies the creation time in 1/60-second clock ticks.
    """
    # RIBPRV bits 24-35 are the low 12 bits of the TOPS-10 date.
    date_low = ribprv & 0o7777

    # For the DATE75 extended representation the date is 15 bits.
    # For the RIBs represented by these values, the high 3 date bits
    # are recovered from RIBTIM bits 10-12.
    # 10
    # date_high = (ribtime >> 10) & 0o7
    # print(date_high)

    # tops10_date = (date_high << 12) | date_low
    tops10_date = date_low

    # TOPS-10 date encoding:
    # (((year - 1964) * 12) + (month - 1)) * 31 + (day - 1)
    month_day, day0 = divmod(tops10_date, 31)
    year0, month0 = divmod(month_day, 12)

    year = 1964 + year0
    month = month0 + 1
    day = day0 + 1
    return date(year, month, day)

    # RIBTIM low 24 bits are 60-Hz clock ticks since midnight.
    ticks = ribtime & 0o77777777
    seconds, tick = divmod(ticks, 60)

    if seconds >= 24 * 60 * 60:
        raise ValueError(f"invalid TOPS-10 time: {ticks:o} ticks")

    midnight = datetime(year, month, day)
    return midnight + timedelta(
        seconds=seconds,
        microseconds=(tick * 1_000_000) // 60,
    )


# A byte pointer is a 36-bit value used to describe where a byte is located inside a word
# It contains:
# - bits 0–5: P - position, i.e. number of bits to the right of the byte
# - bits 6–11: S - byte size
#
#   35       30 29       24 23                                            0
#   +----------+-----------+----------------------------------------------+
#   | position |   size    |                                              |
#   +----------+-----------+----------------------------------------------+
#      6 bits      6 bits                    18 bits
#
# The position tells the processor where the byte is within the 36-bit word,
# while size tells it how many bits to access.


def signed18(value: int) -> int:
    """Interpret an 18-bit field as a signed two's complement number."""
    value &= PDP10_HALF_MASK
    if value & (1 << 17):
        value -= 1 << 18
    return value


def extract_byte(word: int, position: int, size: int) -> int:
    """Extract a ``size``-bit field ending at bit ``position`` (0 = leftmost bit)."""
    mask = (1 << size) - 1
    result = (word >> position) & mask
    return result


def decode_byte_pointer(word: int) -> t.Tuple[int, int]:
    """Decode a HOMCNP/HOMCKP/HOMCLP byte pointer word into (position, size)."""
    # bits 0–5: P - position, i.e. number of bits to the right of the byte
    # bits 6–11: S - byte size
    p = word >> 30
    s = (word >> 24) & 0o77
    return p, s


def tops10_canonical_filename(fullname: str, wildcard: bool = False) -> str:
    try:
        if "[" in fullname:
            ppn: t.Optional[PPN] = PPN.from_str(fullname)
            fullname = fullname.split("]", 1)[1]
        else:
            ppn = None
    except Exception:
        ppn = None
    if fullname:
        fullname = (fullname or "").upper()
        try:
            stem, extension = fullname.split(".", 1)
        except Exception:
            stem = fullname
            extension = "*" if wildcard else ""
        stem = sixbit_to_ascii(ascii_to_sixbit(stem))
        extension = sixbit_to_ascii(ascii_to_sixbit(extension[:3]))
        fullanme = f"{stem}.{extension}"
    return f"{ppn or ''}{fullname}"


def tops10_split_fullname(ppn: PPN, fullname: t.Optional[str], wildcard: bool = True) -> t.Tuple[PPN, t.Optional[str]]:
    if fullname:
        if "[" in fullname:
            try:
                ppn = PPN.from_str(fullname)
                fullname = fullname.split("]", 1)[1]
            except Exception:
                return ppn, fullname
        if fullname:
            fullname = tops10_canonical_filename(fullname, wildcard=wildcard)
    return ppn, fullname


class TOPS10File(AbstractFile):
    entry: "TOPS10Path"
    file_mode: str

    def __init__(self, entry: "TOPS10Path", file_mode: t.Optional[str] = None):
        if file_mode is None:
            # Determine file mode based on the file extension
            if entry.extension in TEXT_EXTENSIONS:
                self.file_mode = ASCII
            else:
                self.file_mode = IMAGE
        else:
            self.file_mode = file_mode
        self.entry = entry

    @property
    def name(self) -> str:
        """Final path component"""
        return self.entry.name

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = self.entry.get_length()
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.entry.get_length()
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        data = bytearray()
        # Get the blocks to be read
        blocks = self.entry.rib.get_blocks()[block_number : block_number + number_of_blocks]
        data = bytearray()
        for block in blocks:
            words = self.entry.fs.read_words(block)
            data.extend(from_36bit_words_to_bytes(words))  # TODO
        return bytes(data)

    def write_block(
        self,
        buffer: t.Union[bytes, bytearray],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.entry.get_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return self.entry.get_block_size()

    def __str__(self) -> str:
        return str(self.entry)


@dataclass
class MasterFileDirectoryEntry:
    """
    Master File Directory (MFD) entry

    Word
          +---------------+---------------+
      0   |   Project     |   Programmer  |
          +---------------+---------------+
      1   | "UFD"(SIXBIT) |      CFP      |
          +---------------+---------------+
    """

    ppn: PPN = field(metadata={"description": "Project-programmer number of the UFD PPN"})
    extension: str = field(metadata={"description": "SIXBIT UFD marker"})
    cfp: int = field(metadata={"description": "Compressed file pointer to the UFD RIB"})

    @classmethod
    def parse(cls, words: t.Sequence[int], position: int = 0) -> t.Optional["MasterFileDirectoryEntry"]:
        if position < 0 or position + 1 >= len(words):
            raise OSError(errno.EIO, "Incomplete TOPS-10 MFD entry")
        ppn_word = words[position]
        pointer_word = words[position + 1]
        extension = sixbit_to_ascii(left_half(pointer_word) << 18)
        if extension != "UFD":
            return None
        return cls(
            ppn=PPN.from_word(ppn_word),
            extension=extension,
            cfp=right_half(pointer_word),
        )

    @property
    def stem(self) -> str:
        return str(self.ppn)

    @property
    def name(self) -> str:
        return str(self.ppn)

    def __str__(self) -> str:
        return f"{self.ppn.to_wide_str()} {self.cfp:06o}"

    def __repr__(self) -> str:
        return f"<MFD entry: {self.ppn} {self.cfp:06o}>"


@dataclass
class UserFileDirectoryEntry:
    """
    User File Directory (UFD) entry

    Word
          +---------------+---------------+
      0   |      File name (SIXBIT)       |
          +---------------+---------------+
      1   |   Extension   |      CFP      |
          +---------------+---------------+
    """

    stem: str = field(metadata={"description": "File name"})
    extension: str = field(metadata={"description": "File extension"})
    cfp: int = field(metadata={"description": "Compressed file pointer to the file RIB"})

    @classmethod
    def parse(cls, words: t.Sequence[int], position: int = 0) -> "UserFileDirectoryEntry":
        if position < 0 or position + 1 >= len(words):
            raise OSError(errno.EIO, "Incomplete TOPS-10 MFD entry")
        ufdname = words[position]
        ufdext = words[position + 1]
        return cls(
            stem=sixbit_to_ascii(ufdname),
            extension=sixbit_to_ascii(left_half(ufdext) << 18),
            cfp=right_half(ufdext),
        )

    @property
    def name(self) -> str:
        return f"{self.stem}.{self.extension}"

    def __str__(self) -> str:
        return f"{self.name:<12} {self.cfp:06o}"

    def __repr__(self) -> str:
        return f"<UFD entry: {self.name} {self.cfp:06o}>"


class TOPS10Path(XFERXPath):
    fs: "TOPS10Filesystem"
    dir_entry: t.Union["UserFileDirectoryEntry", "MasterFileDirectoryEntry"]
    rib: "RetrievalInformationBlock"

    def __init__(
        self,
        fs: "TOPS10Filesystem",
        dir_entry: t.Union["UserFileDirectoryEntry", "MasterFileDirectoryEntry"],
        rib: "RetrievalInformationBlock",
    ):
        self.fs = fs
        self.dir_entry = dir_entry
        self.rib = rib

    @classmethod
    def read(
        cls,
        fs: "TOPS10Filesystem",
        dir_entry: t.Union["UserFileDirectoryEntry", "MasterFileDirectoryEntry"],
    ) -> "TOPS10Path":
        rib = fs.read_rib(cfp=dir_entry.cfp)
        return cls(fs=fs, dir_entry=dir_entry, rib=rib)

    @property
    def ppn(self) -> PPN:
        """
        Get the project-programmer number (PPN) of the file owner
        """
        if isinstance(self.dir_entry, MasterFileDirectoryEntry):
            return self.dir_entry.ppn
        else:
            return self.rib.ribppn

    @property
    def access_code(self) -> int:
        # RIBPRV bits 0-8 - Access code
        return self.rib.ribprv >> 27

    @property
    def creation_date(self) -> t.Union[date, datetime, None]:
        return tops10_datetime(self.rib.ribprv, self.rib.ribtim)

    @property
    def version(self) -> int:
        return right_half(self.rib.ribver)

    @property
    def last_programmer(self) -> int:
        return left_half(self.rib.ribver) >> 0o6

    @property
    def stem(self) -> str:
        return self.dir_entry.stem

    @property
    def extension(self) -> str:
        return self.dir_entry.extension

    @property
    def name(self) -> str:
        return self.dir_entry.name

    @property
    def fullname(self) -> str:
        if isinstance(self.dir_entry, MasterFileDirectoryEntry):
            return f"{self.ppn or ''}"
        else:
            return f"{self.ppn or ''}{self.name}"

    def is_dir(self) -> bool:
        """
        Is a directory entry
        """
        return isinstance(self.dir_entry, MasterFileDirectoryEntry)

    @property
    def parent(self) -> XFERXPath:
        """The logical parent"""
        if self.is_dir():
            return self.fs.get_root()
        else:
            return self.fs.get_root().get_entry(self.ppn)

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return math.ceil(self.rib.ribsiz / WORDS_PER_BLOCK)

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get file size in bytes
        """
        return self.get_length() * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return WORDS_PER_BLOCK * 7  # TODO

    def delete(self) -> bool:
        """
        Delete the directory entry
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> TOPS10File:
        """
        Open a file
        """
        return TOPS10File(self, file_mode)

    def iterdir(
        self,
        pattern: t.Optional[str] = None,
        include_all: bool = False,
        wildcard: bool = True,
    ) -> t.Iterator["TOPS10Path"]:
        """
        Iterate over all entries in the User File Directory
        """
        if not self.is_dir():
            raise NotADirectoryError(self.fullname)
        if self.ppn == MFD_PPN:
            mfd = self.fs.get_root()
            yield from mfd.iterdir(pattern=pattern, include_all=include_all, wildcard=wildcard)
        else:
            for ufd_entry in self.fs.parse_ufd_entries(self.rib.read_words()):
                if filename_match(ufd_entry.name, pattern, wildcard):
                    yield TOPS10Path.read(self.fs, ufd_entry)

    def __str__(self) -> str:
        return self.fullname


@dataclass
class TOPS10HomeBlock:
    """
    TOPS-10 HOME block
    ------------------

    +-----+---------+---------------------------------------------------------------+
    | Pos | Symbol  | Contents                                                      |
    +-----+---------+---------------------------------------------------------------+
    |   0 | HOMNAM  | SIXBIT /HOM/                                                  |
    +-----+---------+---------------------------------------------------------------+
    |   1 | HOMHID  | SIXBIT unit ID                                                |
    +-----+---------+---------------------------------------------------------------+
    |   2 | HOMPHY  | LH = physical address of this Home block on this unit         |
    |     |         | RH = physical address of other Home block on this unit        |
    |     |         | Byte (8) cylinder address, (5) surface, sector address        |
    |     |         | Written and used by MAP program                               |
    +-----+---------+---------------------------------------------------------------+
    |   3 | HOMSRC  | Logical position of this STR in "SYS" search list (0...N)     |
    |     |         | -1 means not in SYS search list                               |
    +-----+---------+---------------------------------------------------------------+
    |   4 | HOMSNM  | SIXBIT file structure name this unit belongs to               |
    |     |         | 0 indicates not in a file structure                           |
    |     |         | Ignored by monitor when a pack is mounted; FILE STRLST.SYS    |
    |     |         | is final authority for the STR name                           |
    +-----+---------+---------------------------------------------------------------+
    |   5 | HOMNXT  | SIXBIT unit ID of next unit in this file structure            |
    |     |         | 0 if this unit is last or only unit in file structure         |
    |     |         | Checked by monitor and OMOUNT CUSP when pack is mounted       |
    +-----+---------+---------------------------------------------------------------+
    |   6 | HOMPRV  | SIXBIT unit ID of previous unit in this file structure        |
    |     |         | 0 if this unit is only unit in file structure                 |
    |     |         | Checked by monitor and OMOUNT CUSP when pack is mounted       |
    +-----+---------+---------------------------------------------------------------+
    |   7 | HOMLOG  | SIXBIT logical unit number within file structure              |
    |     |         | e.g. DSKA0, DSKC12                                            |
    +-----+---------+---------------------------------------------------------------+
    |   8 | HOMLUN  | Logical unit number within file structure                     |
    |     |         | Checked by monitor and OMOUNT CUSP when pack is mounted       |
    +-----+---------+---------------------------------------------------------------+
    |   9 | HOMPPN  | Project-programmer number of user who refreshed disk          |
    |     |         | under timesharing, or 0                                       |
    +-----+---------+---------------------------------------------------------------+
    |  10 | HOMHOM  | LH = logical block number within unit of 1st Home block       |
    |     |         | RH = logical block number within unit of redundant Home block |
    |     |         | Used if first Home block is bad                               |
    |     |         | Home blocks restricted to first 262,000 blocks of a unit      |
    +-----+---------+---------------------------------------------------------------+
    |  11 | HOMGRP  | Number of blocks (not clusters) per group to try for on       |
    |     |         | sequential output allocation                                  |
    +-----+---------+---------------------------------------------------------------+
    |  12 | HOMBSC  | Number of blocks per supercluster                             |
    +-----+---------+---------------------------------------------------------------+
    |  13 | HOMSCU  | Number of superclusters per unit                              |
    +-----+---------+---------------------------------------------------------------+
    |  14 | HOMCNP  | Byte pointer for cluster count in a retrieval pointer         |
    +-----+---------+---------------------------------------------------------------+
    |  15 | HOMCKP  | Byte pointer for checksum in a retrieval pointer              |
    +-----+---------+---------------------------------------------------------------+
    |  16 | HOMCLP  | Byte pointer for cluster address in a retrieval pointer       |
    +-----+---------+---------------------------------------------------------------+
    |  17 | HOMBPC  | Number of blocks per cluster                                  |
    |     |         | Same for all units in an STR                                  |
    +-----+---------+---------------------------------------------------------------+
    |  18 | HOMK4S  | Number of K words of this unit used for swapping              |
    |     |         | 0 means no swapping space allocated                           |
    +-----+---------+---------------------------------------------------------------+
    |  19 | HOMREF  | Non-zero if file structure must be refreshed because some     |
    |     |         | parameter for this unit has changed                           |
    |     |         | Set by disk once-only code; checked at system startup and     |
    |     |         | by OMOUNT when pack is mounted                                |
    +-----+---------+---------------------------------------------------------------+
    |  20 | HOMSIC  | Number of SAT blocks in core                                  |
    +-----+---------+---------------------------------------------------------------+
    |  21 | HOMSID  | SIXBIT unit ID of next unit in active swapping list           |
    |     |         | 0 if last unit, or unit not in active swapping list           |
    +-----+---------+---------------------------------------------------------------+
    |  22 | HOMSUN  | Logical unit number in active swapping list (0...7)           |
    |     |         | -1 indicates unit is not in active swapping list              |
    |     |         | May be changed at once-time without refreshing if swapping    |
    |     |         | space was allocated at refresh time                           |
    +-----+---------+---------------------------------------------------------------+
    |  23 | HOMSLB  | First logical block on unit for swapping                      |
    +-----+---------+---------------------------------------------------------------+
    |  24 | HOMCFS  | Swapping class for unit                                       |
    +-----+---------+---------------------------------------------------------------+
    |  25 | HOMSPU  | Number of SAT blocks per unit                                 |
    +-----+---------+---------------------------------------------------------------+
    |  26 | HOMOVR  | Negative number of blocks of overdraw allowed a user on this  |
    |     |         | STR before no more outputs are allowed                        |
    +-----+---------+---------------------------------------------------------------+
    |  27 | HOMGAR  | Upper bound on number of blocks guaranteed to users by        |
    |     |         | reserved quotas                                               |
    +-----+---------+---------------------------------------------------------------+
    |  28 | HOMSAT  | Logical block number of SAT.SYS                               |
    +-----+---------+---------------------------------------------------------------+
    |  29 | HOMHMS  | Logical block number of HOME.SYS                              |
    +-----+---------+---------------------------------------------------------------+
    |  30 | HOMSWP  | Logical block number of SWAP.SYS                              |
    +-----+---------+---------------------------------------------------------------+
    |  31 | HOMMNT  | Logical block number of MAINT.SYS                             |
    +-----+---------+---------------------------------------------------------------+
    |  32 | HOMBAD  | Logical block number of BADBLK.SYS                            |
    +-----+---------+---------------------------------------------------------------+
    |  33 | HOMCRS  | Logical block number of CRASH.EXE                             |
    +-----+---------+---------------------------------------------------------------+
    |  34 | HOMSNP  | Logical block number of SNAP.SYS                              |
    +-----+---------+---------------------------------------------------------------+
    |  35 | HOMRCV  | Logical block number of RECOV.SYS                             |
    +-----+---------+---------------------------------------------------------------+
    |  36 | HOMSUF  | Logical block number of SYS UFD                               |
    +-----+---------+---------------------------------------------------------------+
    |  37 | HOMPUF  | Logical block number of PRINTR UFD                            |
    +-----+---------+---------------------------------------------------------------+
    |  38 | HOMMFD  | Logical block number of MFD [1,1].UFD                         |
    +-----+---------+---------------------------------------------------------------+
    |  39 | HOMPT1  | Copy of 1st retrieval pointer for MFD for STR this unit is in |
    +-----+---------+---------------------------------------------------------------+
    |  40 | HOMUN1  | Logical unit number of unit on which MFD begins               |
    +-----+---------+---------------------------------------------------------------+
    |  41 | HOMLEN  | First address of table of lengths of files created by refresh |
    |     |         | Lengths needed are CRS, SNP, RCV, and UFDs (in that order)    |
    +-----+---------+---------------------------------------------------------------+
    |  47 |         | Last file length                                              |
    +-----+---------+---------------------------------------------------------------+
    |  47 | HOMUTP  | Unit type on which Home block was written                     |
    |     |         | See UNYUTP                                                    |
    +-----+---------+---------------------------------------------------------------+
    |  48 | HOMRIP  | Used by RIPOFF                                                |
    +-----+---------+---------------------------------------------------------------+
    |  49 | HOMKLB  | 20 words used by PDP-11 in KL10 systems                       |
    |     | HOMFEB  | HOMFEB overlays this location: block number of FE.SYS         |
    |     |         | (1st data block)                                              |
    +-----+---------+---------------------------------------------------------------+
    |  50 | HOMFEL  | Length of FE.SYS                                              |
    +-----+---------+---------------------------------------------------------------+
    |  65 | HOMFEA  | FE-file address for KS10                                      |
    +-----+---------+---------------------------------------------------------------+
    |  66 | HOMFES  | FE-file length for KS10                                       |
    +-----+---------+---------------------------------------------------------------+
    |  67 | HOMTCS  | Track / cylinder / sector for KS10                            |
    +-----+---------+---------------------------------------------------------------+
    |  68 | HOMKLE  | Information used to find files for bootstrap/dump             |
    +-----+---------+---------------------------------------------------------------+
    |  69 | HOMK4C  | KL10/KS10-specific field                                      |
    +-----+---------+---------------------------------------------------------------+
    |  70 | HOMBTS  | Bits in the Home block                                        |
    |     | HOMPVS  | If FTSTR: word containing bit indicating private STR          |
    |     | HOMSET  | If FTSETS: word containing byte specifying structure set      |
    +-----+---------+---------------------------------------------------------------+
    |  71 | HOSSET  | Byte size = 6, for HOMSET                                     |
    |     |         | If FTSETS                                                     |
    +-----+---------+---------------------------------------------------------------+
    |  72 | HONSET  | Byte pointer position = 32, for HOMSET                        |
    |     |         | If FTSETS                                                     |
    +-----+---------+---------------------------------------------------------------+
    |  73 | HOMSDL  | Position of this STR in system dump list (1...N)              |
    |     |         | -1 or 0 means not in SDL (0 for compatibility)                |
    +-----+---------+---------------------------------------------------------------+
    |  72 | HOSSET  | See note above: these conditional symbols are source-level    |
    |     |         | byte-size/position constants, not additional Home-block words |
    +-----+---------+---------------------------------------------------------------+
    |  74 | HOMOPP  | Owner PPN of this structure                                   |
    +-----+---------+---------------------------------------------------------------+
    |  75 | HOMMSU  | Reserved for future use                                       |
    +-----+---------+---------------------------------------------------------------+
    |  76 | HOMCUS  | 4 words reserved to customers                                 |
    |  77 | HOMCUS+3|                                                               |
    +-----+---------+---------------------------------------------------------------+
    |  77 | HOMCUL  | Last word in Home block reserved to customers                 |
    |  77 | HOMEND  | Last word containing valid data in Home block                 |
    +-----+---------+---------------------------------------------------------------+
    | 117 | HOMVID  | Volume ID — 3 words, 12 PDP-11 bytes                          |
    +-----+---------+---------------------------------------------------------------+
    | 120 | HOMOKC  | K for CRASH.SAV file                                          |
    |     | HOMOWN  | Owner name — overlays same word                               |
    +-----+---------+---------------------------------------------------------------+
    | 123 | HOMVSY  | System type (TOPS-10)                                         |
    +-----+---------+---------------------------------------------------------------+
    | 126 | HOMCOD  | Contains unlikely code; LH = 0                                |
    |     |         | CODHOM = 707070                                               |
    +-----+---------+---------------------------------------------------------------+
    | 127 | HOMSLF  | LH = 0                                                        |
    |     |         | RH = this block (not cluster) address within unit (SELF)      |
    +-----+---------+---------------------------------------------------------------+

    Pag 127
    https://bitsavers.org/pdf/dec/pdp10/TOPS10_softwareNotebooks/vol18/AA-BJ92B-RB_TOPS-10_Monitor_Tables_Apr86.pdf

    https://pdp-10.trailing-edge.com/BB-X140B-BB_1986/01/10,7/703mon/commod.mac

    """

    words: t.List[int]
    homnam: str = field(metadata={"description": "SIXBIT home signature"})
    homhid: str = field(metadata={"description": "SIXBIT unit ID"})
    homphy: int = field(metadata={"description": "Physical disk address of this block on this unit"})
    homsrc: int = field(metadata={"description": "Position of this structure in System Search List"})
    homsnm: str = field(metadata={"description": "File-structure name in SIXBIT"})
    homnxt: int = field(metadata={"description": "Unit number of the next unit in the structure"})
    homprv: int = field(metadata={"description": "Unit number of the previous unit in the structure"})
    homlog: str = field(metadata={"description": "Logical unit name in SIXBIT"})
    homlun: int = field(metadata={"description": "Logical unit number within the structure"})
    homppn: int = field(metadata={"description": "PPN that refreshed structure under timesharing"})
    homhom: int = field(metadata={"description": "Logical block # for Home block on this unit"})
    homgrp: int = field(metadata={"description": "Number of blocks per group to try for on output"})
    hombsc: int = field(metadata={"description": "Number of blocks per supercluster on this unit"})
    homscu: int = field(metadata={"description": "Number of superclusters per unit"})
    homcnp: int = field(metadata={"description": "Byte pointer for cluster count in retrieval pointers"})
    homckp: int = field(metadata={"description": "Byte pointer for checksum in retrieval pointers"})
    homclp: int = field(metadata={"description": "Byte pointer for cluster address in retrieval pointer"})
    hombpc: int = field(metadata={"description": "Number of blocks per cluster for this structure"})
    homk4s: int = field(metadata={"description": "Number of K words for swapping on this unit"})
    homref: int = field(metadata={"description": "Non-zero if file structure must be refreshed"})
    homsic: int = field(metadata={"description": "Number of SAT blocks in core"})
    homsid: int = field(metadata={"description": "Unit ID of next unit in active swapping list"})
    homsun: int = field(metadata={"description": "Logical unit number in active swapping list"})
    homslb: int = field(metadata={"description": "First logical block on unit for swapping"})
    homcfs: int = field(metadata={"description": "Swapping class for unit"})
    homspu: int = field(metadata={"description": "Number of SAT blocks per unit"})
    homovr: int = field(metadata={"description": "Negative number of blocks of overdraw allowed"})
    homgar: int = field(metadata={"description": "Upper bound on number of blocks guaranteed to users"})
    homsat: int = field(metadata={"description": "Logical block number or SAT.SYS"})
    homhms: int = field(metadata={"description": "Logical block number of HOME.SYS"})
    # ...
    hommfd: int = field(metadata={"description": "Logical block number of MFD [1,1].UFD"})

    @classmethod
    def parse(cls, words: t.List[int]) -> "TOPS10HomeBlock":
        if len(words) != WORDS_PER_BLOCK or words[0] != HOME_SIGNATURE:
            raise OSError(errno.EIO, "Invalid TOPS-10 HOME block")
        return cls(
            words=words,
            homnam=sixbit_to_ascii(words[0]),
            homhid=sixbit_to_ascii(words[1]),
            homphy=words[2],
            homsrc=words[3],
            homsnm=sixbit_to_ascii(words[4]),
            homnxt=words[5],
            homprv=words[6],
            homlog=sixbit_to_ascii(words[7]),
            homlun=words[8],
            homppn=words[9],
            homhom=words[10],
            homgrp=words[11],
            hombsc=words[12],
            homscu=words[13],
            homcnp=words[14],
            homckp=words[15],
            homclp=words[16],
            hombpc=words[17],
            homk4s=words[18],
            homref=words[19],
            homsic=words[20],
            homsid=words[21],
            homsun=words[22],
            homslb=words[23],
            homcfs=words[24],
            homspu=words[25],
            homovr=words[26],
            homgar=words[27],
            homsat=words[28],
            homhms=words[29],
            hommfd=words[38],
        )


@dataclass
class RetrievalPointer:
    """
     Retrieval Pointer
     -----------------

     Each retrieval pointer in the RIB describes a contiguous goup of clusters.

     A retrieval pointer contains the following information:
     - The number of clusters in the group
     - The cluster number where the group starts
     - The checksum for the group

    If the left half of the retrieval pointer is zero:
     - If the right half is zero, there is no more data in the file.
     - If bit 18 is set
         Bits 19 through 35 contain the logical unit number of the next unit.
         This allows one RIB on one unit to hold pointers to data on another unit.
    """

    fs: "TOPS10Filesystem"
    word: int = field(metadata={"description": "Raw retrieval pointer word"})
    cluster_count: int = field(metadata={"description": "Number of contiguous clusters described"})
    cluster_address: int = field(metadata={"description": "Address of the first cluster, within the unit"})
    checksum: int = field(metadata={"description": "Checksum field of the retrieval pointer"})
    is_unit_change: bool = field(metadata={"description": "True for unit switch"}, default=False)
    unit_number: t.Optional[int] = field(metadata={"description": "New unit number"}, default=None)

    @classmethod
    def parse(cls, fs: "TOPS10Filesystem", word: int) -> t.Optional["RetrievalPointer"]:
        if word == 0:
            # If the left and right half words are zero, there is no more data in the file
            return None

        if left_half(word) == 0:
            if right_half(word) & (1 << 17):
                # If Bit 18 is set, bits 19 through 35 contain the logical unit number of the next unit
                unit_number = right_half(word) & ~(1 << 17)
                return cls(
                    fs=fs,
                    word=word,
                    cluster_count=0,
                    cluster_address=0,
                    checksum=0,
                    is_unit_change=True,
                    unit_number=unit_number,
                )

        return cls(
            fs=fs,
            word=word,
            cluster_count=extract_byte(word, fs.cluster_count_pos, fs.cluster_count_size),
            cluster_address=extract_byte(word, fs.cluster_address_pos, fs.cluster_address_size),
            checksum=extract_byte(word, fs.checksum_pos, fs.checksum_size),
        )

    def __repr__(self) -> str:
        if self.is_unit_change:
            return f"<RIB pointer: switch to unit {self.unit_number}>"
        else:
            return (
                f"<RIB pointer: address: {self.cluster_address}, count: {self.cluster_count} checksum: {self.checksum}>"
            )


@dataclass
class RetrievalInformationBlock:
    """
    TOPS-10 Retrieval Information Block
    ------------------------------------

    Every file (including the MFD and UFDs) begins with a 128-word RIB
    (relative block 0 of the file), describing the file's attributes and
    the retrieval pointers to its data blocks.

    +------+---------+-------------------------------------------------------+
    | Word | Symbol  | Contents                                              |
    +------+---------+-------------------------------------------------------+
    |    0 | RIBFIR  | LH = negative count of retrieval pointer slots        |
    |      |         | RH = word offset of the first retrieval pointer       |
    +------+---------+-------------------------------------------------------+
    |    1 | RIBPPN  | Project-programmer number, or project name in LH and  |
    |      |         | programmer initials in RH, each as 3-char SIXBIT      |
    +------+---------+-------------------------------------------------------+
    |    2 | RIBNAM  | 6-character file name in SIXBIT                       |
    +------+---------+-------------------------------------------------------+
    |    3 | RIBEXT  | LH = 3-character file extension in SIXBIT             |
    |      |         | Bits 24-35 = access date                              |
    +------+---------+-------------------------------------------------------+
    |    4 | RIBPRV  | Bits 0-8 = access code; 9-12 = mode;                  |
    |      |         | 13-23 = creation time in minutes since midnight;      |
    |      |         | 24-35 = creation date                                 |
    +------+---------+-------------------------------------------------------+
    |    5 | RIBSIZ  | Written length in words; LH-only or full 36-bit       |
    |      |         | positive length                                       |
    +------+---------+-------------------------------------------------------+
    |    6 | RIBVER  | LH = programmer number last making change;            |
    |      |         | RH = octal version number                             |
    +------+---------+-------------------------------------------------------+
    |    7 | RIBSPL  | Possible user file name when file is being spooled    |
    +------+---------+-------------------------------------------------------+
    |    8 | RIBEST  | Estimated length in core and number of blocks         |
    +------+---------+-------------------------------------------------------+
    |    9 | RIBALC  | Number of blocks allocated to file, including both    |
    |      |         | RIBs                                                  |
    +------+---------+-------------------------------------------------------+
    |   10 | RIBPOS  | Logical block number of the last allocated group      |
    |      |         | within the STR; 0 if allocation did not specify       |
    |      |         | logical block position                                |
    +------+---------+-------------------------------------------------------+
    |   11 | RIBFT1  | Argument saved for future definition by Digital       |
    +------+---------+-------------------------------------------------------+
    |   12 | RIBNCA  | Unprivileged argument available for customer use      |
    +------+---------+-------------------------------------------------------+
    |   13 | RIBMTA  | 36-bit tape label if file has been put on magnetic    |
    |      |         | tape                                                  |
    +------+---------+-------------------------------------------------------+
    |   14 | RIBDEV  | Value only: file structure name on which file starts  |
    +------+---------+-------------------------------------------------------+
    |   15 | RIBSTS  | Status bits for all files in the UFD (LH)             |
    +------+---------+-------------------------------------------------------+
    |   16 | RIBELB  | Logical block within which a bad region begins;       |
    |      |         | 0 = file has had no bad regions; LH = CONI bits       |
    |      |         | 12-29 on error                                        |
    +------+---------+-------------------------------------------------------+
    |   17 | RIBEUN  | LH = logical unit number within STR on which the      |
    |      |         | error region occurred                                 |
    +------+---------+-------------------------------------------------------+
    |   18 | RIBQTF  | UFD only: first-come-first-served logged-in quota,    |
    |      |         | in total data + RIB blocks allowed for this user      |
    +------+---------+-------------------------------------------------------+
    |   19 | RIBQTO  | UFD only: logged-out quota, in total data + RIB       |
    |      |         | blocks allowed for this user                          |
    +------+---------+-------------------------------------------------------+
    |   20 | RIBQTR  | UFD only: administrator-reserved logged-in quota,     |
    |      |         | in total data + RIB blocks                            |
    +------+---------+-------------------------------------------------------+
    |   21 | RIBUSD  | UFD only: count of blocks used, including all         |
    |      |         | overhead blocks, when job was logged out              |
    +------+---------+-------------------------------------------------------+
    |   22 | RIBAUT  | Project-programmer number of author of file           |
    |      |         | (user performing CREATE or SUPERSEDE)                 |
    +------+---------+-------------------------------------------------------+
    |   23 | RIBNXT  | SIXBIT name of next file structure if file continues  |
    |      |         | on another structure; 0 if none                       |
    +------+---------+-------------------------------------------------------+
    |   24 | RIBPRD  | SIXBIT name of predecessor file structure if this     |
    |      |         | is not the first subfile; 0 if first or only          |
    +------+---------+-------------------------------------------------------+
    |   25 | RIBPCA  | Privileged argument available for customer use        |
    +------+---------+-------------------------------------------------------+
    |   26 | RIBUFD  | Logical block number within STR of the UFD data       |
    |      |         | block containing this file's entry                    |
    +------+---------+-------------------------------------------------------+
    |   27 | RIBFLR  | Relative block number in file of the first block in   |
    |      |         | the RIB; implemented for extended RIBs                |
    +------+---------+-------------------------------------------------------+
    |   28 | RIBXRA  | Extended RIB address; points to the next RIB in the   |
    |      |         | chain; 0 if this is the last RIB                      |
    +------+---------+-------------------------------------------------------+
    |   29 | RIBTIM  | Creation date and time in new date format             |
    +------+---------+-------------------------------------------------------+
    |   30 | RIBLAD  | Last accounting date (UFD)                            |
    +------+---------+-------------------------------------------------------+
    |   31 | RIBDED  | Directory expiration date (UFD)                       |
    +------+---------+-------------------------------------------------------+
    |   32 | RIBACT  | AOBJN pointer to account string                       |
    +------+---------+-------------------------------------------------------+
    |   33 |         | First retrieval pointer is stored here; no symbol     |
    |      |         | assigned. DSKSER obtains its address from RIBFIR.     |
    +------+---------+-------------------------------------------------------+
    |126-8?| RICACS  | Account string (pointer in RIBACT)                    |
    +------+---------+-------------------------------------------------------+
    |  126 | RIBCOD  | Contains an unlikely data word (i.e. not ASCII or     |
    |      |         | floating point)                                       |
    +------+---------+-------------------------------------------------------+
    |  127 | RIBSLF  | This block number (self)                              |
    +------+---------+-------------------------------------------------------+

    """

    fs: "TOPS10Filesystem"
    words: t.List[int]
    ribfir: int = field(metadata={"description": "First retrieval pointer offset and negative count"})
    ribppn: PPN = field(metadata={"description": "Project-programmer number"})
    ribnam: str = field(metadata={"description": "File name in SIXBIT"})
    ribext: str = field(metadata={"description": "File extension in SIXBIT and access date"})
    ribprv: int = field(metadata={"description": "Access code, mode, creation time and date"})
    ribsiz: int = field(metadata={"description": "Written length in words"})
    ribver: int = field(metadata={"description": "Programmer number and version number"})
    ribspl: int = field(metadata={"description": "Possible user file name when spooled"})
    ribest: int = field(metadata={"description": "Estimated length in core and number of blocks"})
    ribalc: int = field(metadata={"description": "Number of blocks allocated to file"})
    ribpos: int = field(metadata={"description": "Logical block number of last allocated group"})
    ribft1: int = field(metadata={"description": "Argument saved for future definition"})
    ribnca: int = field(metadata={"description": "Unprivileged argument available for customer use"})
    ribmta: int = field(metadata={"description": "36-bit tape label if file has been put on magnetic tape"})
    ribdev: int = field(metadata={"description": "File structure name on which file starts"})
    ribsts: int = field(metadata={"description": "Status bits for all files in the UFD"})
    ribelb: int = field(metadata={"description": "Logical block within which a bad region begins"})
    ribeun: int = field(metadata={"description": "Logical unit number within STR on which error region occurred"})
    ribqtf: int = field(metadata={"description": "UFD only: first-come-first-served logged-in quota"})
    ribqto: int = field(metadata={"description": "UFD only: logged-out quota"})
    ribqtr: int = field(metadata={"description": "UFD only: administrator-reserved logged-in quota"})
    ribusd: int = field(metadata={"description": "UFD only: count of blocks used when job was logged out"})
    ribaut: int = field(metadata={"description": "Project-programmer number of author of file"})
    ribnxt: str = field(metadata={"description": "SIXBIT name of next file structure if file continues"})
    ribprd: str = field(metadata={"description": "SIXBIT name of predecessor file structure if not first subfile"})
    ribpca: int = field(metadata={"description": "Privileged argument available for customer use"})
    ribufd: int = field(metadata={"description": "Logical block number within STR of the UFD data block"})
    ribflr: int = field(metadata={"description": "Relative block number in file of the first block in the RIB"})
    ribxra: int = field(metadata={"description": "Extended RIB address; points to the next RIB in the chain"})
    ribtim: int = field(metadata={"description": "Creation date and time in new date format"})
    riblad: int = field(metadata={"description": "Last accounting date (UFD)"})
    ribded: int = field(metadata={"description": "Directory expiration date (UFD)"})
    ribact: int = field(metadata={"description": "AOBJN pointer to account string"})
    # Retrieval pointers start at RIBFIR
    # ribacs: int = field(metadata={"description": "Account string (pointer in RIBACT)"})
    ribcod: int = field(metadata={"description": "Contains an unlikely data word (i.e. not ASCII or floating point)"})
    ribslf: int = field(metadata={"description": "This block number (self)"})

    @classmethod
    def parse(cls, fs: "TOPS10Filesystem", words: t.Sequence[int]) -> "RetrievalInformationBlock":
        return cls(
            fs=fs,
            words=list(words),
            ribfir=words[0],
            ribppn=PPN.from_word(words[1]),
            ribnam=sixbit_to_ascii(words[2]),
            ribext=sixbit_to_ascii(left_half(words[3]) << 18),
            ribprv=words[4],
            ribsiz=words[5],
            ribver=words[6],
            ribspl=words[7],
            ribest=words[8],
            ribalc=words[9],
            ribpos=words[10],
            ribft1=words[11],
            ribnca=words[12],
            ribmta=words[13],
            ribdev=words[14],
            ribsts=words[15],
            ribelb=words[16],
            ribeun=words[17],
            ribqtf=words[18],
            ribqto=words[19],
            ribqtr=words[20],
            ribusd=words[21],
            ribaut=words[22],
            ribnxt=sixbit_to_ascii(words[23]),
            ribprd=sixbit_to_ascii(words[24]),
            ribpca=words[25],
            ribufd=words[26],
            ribflr=words[27],
            ribxra=words[28],
            ribtim=words[29],
            riblad=words[30],
            ribded=words[31],
            ribact=words[32],
            ribcod=words[126],
            ribslf=words[127],
        )

    @property
    def max_pointers(self) -> int:
        """Max possible number of retrieval pointers in this RIB"""
        return -signed18(left_half(self.ribfir))

    @property
    def first_pointer_offset(self) -> int:
        """Offset of the first retrieval pointer in this RIB"""
        return right_half(self.ribfir)

    def retrieval_pointers(self) -> t.Iterator[RetrievalPointer]:
        """Iterate over the retrieval pointers"""
        for offset in range(self.first_pointer_offset, len(self.words)):
            if offset - self.first_pointer_offset >= self.max_pointers:
                break
            pointer = RetrievalPointer.parse(self.fs, self.words[offset])
            if pointer is None:
                break
            yield pointer

    def get_blocks(self) -> t.List[int]:
        """Get the list of block numbers for this RIB"""
        blocks: t.List[int] = []
        for pointer in self.retrieval_pointers():
            if pointer.is_unit_change:
                continue
            for cluster in range(pointer.cluster_count):
                for i in range(self.fs.blocks_per_cluster):
                    block = (pointer.cluster_address + cluster) * self.fs.blocks_per_cluster + i
                    if block != self.ribslf:  # skip the RIB block itself
                        blocks.append(block)
        return blocks

    def read_words(self) -> t.List[int]:
        """Read all the words from the blocks described by this RIB"""
        words = []
        for block in self.get_blocks():
            words.extend(self.fs.read_words(block))
            if len(words) > self.ribsiz:
                words = words[: self.ribsiz]
                break
        return words

    def examine(self) -> str:
        buf = io.StringIO()
        buf.write(dump_dataclass(self, "RIB fields"))
        buf.write(f"Retrieval pointers:\n")
        for rp in self.retrieval_pointers():
            if rp.is_unit_change:
                buf.write(f"  Switch to unit {rp.unit_number}\n")
            else:
                buf.write(f"  Address: {rp.cluster_address}, Count: {rp.cluster_count}\n")
        return buf.getvalue()


class MasterFileDirectory(XFERXPath):
    fs: "TOPS10Filesystem"

    @property
    def fullname(self) -> str:
        """Name with path"""
        return ""

    @property
    def name(self) -> str:
        """Final path component"""
        return ""

    def is_dir(self) -> bool:
        """Is a directory"""
        return True

    def is_mount(self) -> bool:
        """Is root directory"""
        return True

    @property
    def parent(self) -> "MasterFileDirectory":
        """The logical parent"""
        return self

    @classmethod
    def parse_mfd_entries(cls, words: t.Sequence[int]) -> t.Iterator[MasterFileDirectoryEntry]:
        for position in range(0, len(words) - 1, MFD_ENTRY_SIZE):
            if words[position] == 0 and words[position + 1] == 0:
                break
            entry = MasterFileDirectoryEntry.parse(words, position)
            if entry is not None:
                yield entry

    def read_mfd_entries(
        self,
        ppn: PPN = ANY_PPN,
    ) -> t.Iterator[MasterFileDirectoryEntry]:
        """
        Read Master File Directory entries
        """
        rib = self.fs.read_rib(block_number=self.fs.mfd_block)
        for mfd_entry in self.parse_mfd_entries(rib.read_words()):
            if ppn.match(mfd_entry.ppn):
                yield mfd_entry

    def get_entry(self, ppn: PPN) -> TOPS10Path:
        """
        Get a Master File Directory entry
        """
        for mfd_entry in self.read_mfd_entries(ppn=ppn):
            return TOPS10Path.read(self.fs, mfd_entry)
        raise FileNotFoundError(errno.ENOENT, f"File not found: {ppn}")

    def iterdir(
        self,
        pattern: t.Optional[str] = None,
        include_all: bool = False,
        wildcard: bool = True,
        ppn: PPN = ANY_PPN,
    ) -> t.Iterator[TOPS10Path]:
        """
        Iterate over all entries in the Master File Directory
        """
        for mfd_entry in self.read_mfd_entries(ppn=ppn):
            if filename_match(mfd_entry.name, pattern, wildcard):
                yield TOPS10Path.read(self.fs, mfd_entry)


class TOPS10Filesystem(AbstractBlockFilesystem):
    """
    TOPS-10 Filesystem
    """

    fs_name = "tops10"
    fs_description = "TOPS-10 filesystem"
    fs_platforms = ["pdp10"]
    fs_entry_metadata = [
        "creation_date",
    ]  # TODO

    dev: BlockDevice36Bit

    ppn: PPN  # current project-programmer number
    home_block: int  # HOME block number
    mfd_block: int  # MDF block number
    blocks_per_cluster: int  # number of blocks per cluster
    blocks_per_supercluster: int  # number of blocks per supercluster
    cluster_count_pos: int  # position of cluster count in retrieval pointers
    cluster_count_size: int  # size of cluster count in retrieval pointers
    checksum_pos: int  # position of checksum in retrieval pointers
    checksum_size: int  # size of checksum in retrieval pointers
    cluster_address_pos: int  # position of cluster address in retrieval pointers
    cluster_address_size: int  # size of cluster address in retrieval pointers

    def __init__(self, file_or_device: t.Union[AbstractFile, AbstractDevice]):
        if isinstance(file_or_device, AbstractFile):
            self.dev = BlockDevice36Bit(file_or_device, words_per_block=WORDS_PER_BLOCK)
        elif isinstance(file_or_device, BlockDevice36Bit):
            self.dev = file_or_device
        else:
            raise OSError(errno.EIO, f"Invalid device type for {self.fs_description} filesystem")

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union[AbstractFile, AbstractDevice],
        **kwargs: t.Union[bool, str],
    ) -> "TOPS10Filesystem":
        self = cls(file_or_dev)
        self.ppn = MFD_PPN
        size = self.dev.get_size()
        if size % DISK_IMAGE_BLOCK_SIZE:
            raise OSError(errno.EIO, "Not a SIMH TOPS-10 word image")
        self.home_block = -1
        for block_number in (1, 10, 12):
            words = self.read_words(block_number)
            if words and words[0] == HOME_SIGNATURE:
                self.home_block = block_number
                break
        if self.home_block == -1:
            raise OSError(errno.EIO, "TOPS-10 HOME block not found")
        home = TOPS10HomeBlock.parse(words)
        self.mfd_block = home.hommfd
        self.blocks_per_cluster = home.hombpc
        self.blocks_per_supercluster = home.hombsc
        self.cluster_count_pos, self.cluster_count_size = decode_byte_pointer(home.homcnp)
        self.checksum_pos, self.checksum_size = decode_byte_pointer(home.homckp)
        self.cluster_address_pos, self.cluster_address_size = decode_byte_pointer(home.homclp)
        return self

    def read_home(self) -> TOPS10HomeBlock:
        """Read home block"""
        words = self.read_words(self.home_block)
        return TOPS10HomeBlock.parse(words)

    @classmethod
    def parse_ufd_entries(cls, words: t.Sequence[int]) -> t.Iterator[UserFileDirectoryEntry]:
        for position in range(0, len(words) - 1, UFD_ENTRY_SIZE):
            if words[position] == 0 and words[position + 1] == 0:
                break
            yield UserFileDirectoryEntry.parse(words, position)

    def read_rib(self, block_number: int = 0, cfp: int = -1) -> RetrievalInformationBlock:
        """Read and parse the RIB at the given block number or cluster file pointer (CFP)"""
        if cfp != -1:
            block_number = cfp * self.blocks_per_supercluster
        return RetrievalInformationBlock.parse(self, self.read_words(block_number))

    def read_words(self, block_number: int, number_of_blocks: int = 1) -> t.List[int]:
        words: t.List[int] = []
        for block in range(block_number, block_number + number_of_blocks):
            words.extend(self.dev.read_words_block(block))
        return words

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,  # expand directories
        wildcard: bool = True,
        ppn: t.Optional[PPN] = None,
    ) -> t.Iterator[TOPS10Path]:
        if ppn is None:
            ppn = self.ppn
        ppn, filename_pattern = tops10_split_fullname(fullname=pattern, wildcard=wildcard, ppn=ppn)
        if pattern and not filename_pattern and not expand:
            # If expand is False, check if the pattern is an PPN
            try:
                ppn = PPN.from_str(pattern)
                yield from self.get_root().iterdir(ppn=ppn)
                return
            except Exception:
                pass
        if ppn == MFD_PPN:
            mfd = self.get_root()
            yield from mfd.iterdir(pattern=filename_pattern, wildcard=wildcard)
        else:
            for mfd_entry in self.get_root().read_mfd_entries(ppn=ppn):
                rib = self.read_rib(cfp=mfd_entry.cfp)
                for ufd_entry in self.parse_ufd_entries(rib.read_words()):
                    if filename_match(ufd_entry.name, filename_pattern, wildcard):
                        yield TOPS10Path.read(self, ufd_entry)

    @property
    def entries_list(self) -> t.Iterator[TOPS10Path]:
        yield from self.filter_entries_list(pattern=None, wildcard=True, ppn=self.ppn)

    def get_file_entry(self, fullname: str) -> TOPS10Path:
        """
        Get the directory entry for a file
        """
        fullname = tops10_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        ppn, name = tops10_split_fullname(fullname=fullname, wildcard=False, ppn=self.ppn)
        dir_entry = self.get_root().get_entry(ppn=ppn)
        if not name:
            # If the name is empty, it means the fullname is just a PPN,
            # so we return the MFD entry for that PPN
            return dir_entry
        try:
            return next(dir_entry.iterdir(pattern=name, wildcard=False))
        except StopIteration:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def get_root(self) -> MasterFileDirectory:
        """
        Get the root directory
        """
        return MasterFileDirectory(self)

    def show_accounts(self, volume_id: str, options: t.Dict[str, bool]) -> None:
        """
        Listing of all accounts
        """
        if "uic" in options:
            del options["uic"]
        self.dir(volume_id, pattern=str(MFD_PPN), options=options)

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List directory entries
        """
        files = 0
        blocks = 0
        ppn, name = tops10_split_fullname(fullname=pattern, wildcard=True, ppn=self.ppn)
        for entry in self.filter_entries_list(name, wildcard=True, ppn=ppn):
            if entry.is_dir():
                stem = f"{entry.ppn.group:>6o},{entry.ppn.user:<7o}"
            else:
                stem = f"{entry.stem:<6}  {entry.extension:<3}"
            creation_date = entry.creation_date if entry.creation_date else "(undated)"
            blocks += entry.get_length()
            files += 1
            version = f"{entry.last_programmer:o}({entry.version:o})" if entry.version else ""
            post = f"    {volume_id}:   {ppn}" if files == 1 else ""
            sys.stdout.write(
                f"{stem} "
                f"{entry.get_length():>5}  "
                f"<{entry.access_code:>03o}>  "
                f"{creation_date} "
                f"{version}"
                # f"{entry.rib.ribprv:12o}  "
                # f"{entry.rib.ribtim:>12o}  "
                f"{post}"
                "\n"
            )
        sys.stdout.write(f"  Total of {blocks} blocks in {files} files on {volume_id}: {ppn}\n\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if options.get("diskid"):
            # Display the HOME block
            sys.stdout.write(f"HOME block: {self.home_block}\n")
            words = self.read_words(self.home_block)
            home = TOPS10HomeBlock.parse(words)
            sys.stdout.write(dump_dataclass(home, "HOME fields"))
        elif not arg:
            # Display the Master File Directory (MFD)
            sys.stdout.write(f"MFD block: {self.mfd_block}\n")
            rib = self.read_rib(block_number=self.mfd_block)
            sys.stdout.write(rib.examine())
            sys.stdout.write("\nMFD entries:\n")
            sys.stdout.write("  PPN             CFP\n")
            for mfd_entry in self.get_root().read_mfd_entries():
                sys.stdout.write(f"  {mfd_entry}\n")
        else:
            ppn, name = tops10_split_fullname(fullname=arg, wildcard=False, ppn=self.ppn)
            if name:
                entry = self.get_file_entry(arg)
                sys.stdout.write(entry.rib.examine())
                sys.stdout.write(f"Length: {entry.get_length()}.\n")
                sys.stdout.write(f"Words written: {entry.rib.ribsiz}.\n")
                sys.stdout.write(f"Estimated length: {entry.rib.ribest}.\n")
                sys.stdout.write(f"Blocks allocated: {entry.rib.ribalc}.\n")
                sys.stdout.write(f"Data block in directory: {entry.rib.ribufd}.\n")
                # sys.stdout.write(f"Internal creation date, time: {entry.rib.ribtim}.\n")
                sys.stdout.write(f"RIB block number: {entry.rib.ribslf}.\n")
                sys.stdout.write(f"Blocks: {entry.rib.get_blocks()}\n")
            else:
                entry = self.get_file_entry(arg)
                for mfd_entry in self.get_root().read_mfd_entries(ppn=ppn):
                    rib = self.read_rib(cfp=mfd_entry.cfp)
                    sys.stdout.write(rib.examine())
                    sys.stdout.write("\nFile entries:\n")
                    for ufd_entry in self.parse_ufd_entries(rib.read_words()):
                        sys.stdout.write(f"  {ufd_entry}\n")

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        """
        Change the current Project-Programmer Number (PPN)
        """
        if entry is None:
            self.ppn = MFD_PPN
        else:
            assert isinstance(entry, TOPS10Path) and entry.is_dir()
            self.ppn = entry.ppn

    def get_pwd(self) -> str:
        """
        Get the current Project-Programmer Number
        """
        return str(self.ppn)

    def path_join(self, path: str, *paths: str) -> str:
        """
        Join PPN and filename
        """
        paths = [x for x in paths if x]  # type: ignore
        if not paths:
            return path
        try:
            ppn = PPN.from_str(path)
        except Exception:
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
        if len(paths) > 1:
            raise OSError(errno.EINVAL, "Can only join PPN and filename")
        return f"{ppn}{paths[0]}"
