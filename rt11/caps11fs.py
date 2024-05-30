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
import fnmatch
import math
import os
import struct
import sys
from datetime import date
from typing import Dict, Iterator, Optional

from .abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from .commons import BLOCK_SIZE, hex_dump
from .rt11fs import rt11_canonical_filename

__all__ = [
    "CAPS11File",
    "CAPS11DirectoryEntry",
    "CAPS11Filesystem",
]

READ_FILE_FULL = -1
HEADER_RECORD = ">6s3sBHBB6s12s"
HEADER_RECORD_SIZE = 32

FILE_TYPE_ASCII = 0o1
FILE_TYPE_BIN = 0o2
FILE_TYPE_CORE1 = 0o3  # One 36-bit word in 5 bytes
FILE_TYPE_CORE2 = 0o4  # One 12-bit word in 2 bytes
FILE_TYPE_CORE3 = 0o5  # One 18-bit word in 3 bytes
FILE_TYPE_CORE4 = 0o6  # One 36-bit word in 6 bytes
FILE_TYPE_CORE5 = 0o7  # One 16-bit word in 2 bytes
FILE_TYPE_CORE6 = 0o10  # 2 x 12-bit words in 3 bytes
FILE_TYPE_CORE7 = 0o11  # 2 x 36-bit words in 9 bytes
FILE_TYPE_CORE8 = 0o12  # 4 x 18-bit words in 9 bytes
FILE_TYPE_BOOT = 0o13
FILE_TYPE_BAD = 0o14

STANDARD_FILE_TYPES = {
    FILE_TYPE_ASCII: "ascii",
    FILE_TYPE_BIN: "bin",
    FILE_TYPE_CORE1: "core1",
    FILE_TYPE_CORE2: "core2",
    FILE_TYPE_CORE3: "core3",
    FILE_TYPE_CORE4: "core4",
    FILE_TYPE_CORE5: "core5",
    FILE_TYPE_CORE6: "core6",
    FILE_TYPE_CORE7: "core7",
    FILE_TYPE_CORE8: "core8",
    FILE_TYPE_BOOT: "boot",
    FILE_TYPE_BAD: "bad",
}


def caps11_to_date(val: bytes) -> Optional[date]:
    """
    Translate CAPS-11 date to Python date
    """
    try:
        date_str = val.decode("ascii", errors="ignore")
        day = int(date_str[0:2])
        month = int(date_str[2:4])
        year = int(date_str[4:6]) + 1900
        return date(year, month, day)
    except:
        return None


def date_to_caps11(d: Optional[date]) -> bytes:
    """
    Translate Python date to CAPS-11 date
    """
    if d is None:
        return b"     "
    day = f"{d.day:02}"
    month = f"{d.month:02}"
    year = f"{d.year - 1900:02}"
    date_str = f"{day}{month}{year}"
    return date_str.encode("ascii")


class CAPS11File(AbstractFile):
    entry: "CAPS11DirectoryEntry"
    closed: bool
    size: int  # size in bytes
    content: bytes  # file content

    def __init__(self, entry: "CAPS11DirectoryEntry"):
        self.entry = entry
        self.closed = False
        self.size = entry.size
        records = list(entry.fs.read_magtape())
        self.content = records[entry.file_number - 1][HEADER_RECORD_SIZE:]

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = self.entry.length
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.entry.length
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return self.content[block_number * BLOCK_SIZE : (block_number + number_of_blocks) * BLOCK_SIZE]

    def write_block(
        self,
        buffer: bytes,
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
        return self.size

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    def close(self) -> None:
        """
        Close the file
        """
        self.closed = True

    def __str__(self) -> str:
        return self.entry.fullname


class CAPS11DirectoryEntry(AbstractDirectoryEntry):
    """
    CAPS-11 File Header

      9 bytes   1 byte  2 bytes        1 bye  1 byte  6 bytes  12 bytes
    +----------+------+---------------+------+-------+--------+--------+
    | Filename | Type | Record length | Seq. | Cont. |  Data  |  Spare |
    +----------+------+---------------+------+-------+--------+--------+

    CAPS-11 Users Guide, Pag 289
    http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp11/caps-11/DEC-11-OTUGA-A-D_CAPS-11_Users_Guide_Oct73.pdf
    """

    fs: "CAPS11Filesystem"
    filename: str = ""  #             6 chars - name
    filetype: str = ""  #             3 chars - extension
    record_type: int = 0  #           1 byte  - record type
    record_length: int = 0  #         2 bytes - file record length (fixed at 128 bytes)
    sequence: int = 0  #              1 byte  - file sequence number for multi volume files (0)
    continued: int = 0  #             1 byte  - header auxiliary header record (0)
    raw_creation_date: bytes = b""  # 6 char  - file creation date as DDMMYY
    file_number: int = 0
    unused: bytes = b""
    size: int = 0  # size in bytes

    def __init__(self, fs: "CAPS11Filesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "CAPS11Filesystem", buffer: bytes, file_number: int) -> "CAPS11DirectoryEntry":
        self = CAPS11DirectoryEntry(fs)
        self.file_number = file_number
        (
            filename,
            filetype,
            self.record_type,
            self.record_length,
            self.sequence,
            self.continued,
            self.raw_creation_date,
            self.unused,
        ) = struct.unpack_from(HEADER_RECORD, buffer, 0)
        self.filename = filename.decode("ascii", errors="ignore").rstrip(" ")
        self.filetype = filetype.decode("ascii", errors="ignore").rstrip(" ")
        self.size = len(buffer) - HEADER_RECORD_SIZE - self.continued
        return self

    def write(self) -> bytes:
        buffer = bytearray(HEADER_RECORD_SIZE + self.size + self.continued)
        filename = self.filename.ljust(6).encode("ascii", errors="ignore")
        filetype = self.filetype.ljust(3).encode("ascii", errors="ignore")
        struct.pack_into(
            HEADER_RECORD,
            buffer,
            0,
            filename,
            filetype,
            self.record_type,
            self.record_length,
            self.sequence,
            self.continued,
            self.raw_creation_date,
            self.unused,
        )
        return bytes(buffer)

    @property
    def length(self) -> int:
        """Length in blocks"""
        return int(math.ceil(self.size / BLOCK_SIZE))

    @property
    def is_empty(self) -> bool:
        return self.record_type == FILE_TYPE_BAD

    @property
    def fullname(self) -> str:
        return f"{self.filename}.{self.filetype}"

    @property
    def basename(self) -> str:
        return f"{self.filename}.{self.filetype}"

    @property
    def creation_date(self) -> Optional[date]:
        return caps11_to_date(self.raw_creation_date)

    def delete(self) -> bool:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def __str__(self) -> str:
        record_type = STANDARD_FILE_TYPES.get(self.record_type, f"{self.record_type:>4o}")
        return (
            f"{self.file_number:<4} "
            f"{self.filename:>6}."
            f"{self.filetype:<3}  "
            f"{record_type:>6}  "
            f"{self.record_length:>6} "
            f"{self.sequence:>4} "
            f"{self.continued:>4}  "
            f"{self.creation_date or '          '} "
            f"{self.size:>8}"
        )

    def __repr__(self) -> str:
        return str(self)


class CAPS11Filesystem(AbstractFilesystem):
    """
    CAPS-11 Filesystem

        +-------------------------------------+
     1  |           File header               |  32 bytes
        +-------------------------------------+
     2  |              Data                   | 128 bytes
        +-------------------------------------+
     n  |               ...                   |
        +-------------------------------------+
    n-1 |              Data                   |
        +-------------------------------------+
     n  |               EOF                   |
        +-------------------------------------+

    CAPS-11 Users Guide, Pag 287
    http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp11/caps-11/DEC-11-OTUGA-A-D_CAPS-11_Users_Guide_Oct73.pdf
    """

    def __init__(self, file: "AbstractFile"):
        self.f = file

    def read_magtape(self) -> Iterator[bytes]:
        rc = 0
        data = bytearray()
        self.f.seek(0, 0)
        while True:
            bc = self.f.read(4)
            if len(bc) == 0:
                break
            if bc[2] != 0 or bc[3] != 0:
                raise OSError(
                    errno.EIO,
                    f"Invalid record size, record {rc}, size = 0x{bc[3]:02X}{bc[2]:02X}{bc[1]:02X}{bc[0]:02X}",
                )
            wc = (bc[1] << 8) | bc[0]
            wc = (wc + 1) & ~1
            if wc:
                buffer = self.f.read(wc)
                data.extend(buffer)
                data.extend(bytes([0] * (wc - len(buffer))))  # Pad with zeros
                bc = self.f.read(4)
                rc += 1
            else:
                if rc:
                    yield bytes(data)
                    data.clear()
                rc = 0

    def read_file_headers(self) -> Iterator["CAPS11DirectoryEntry"]:
        """Read file headers"""
        for i, record in enumerate(self.read_magtape(), start=1):
            yield CAPS11DirectoryEntry.read(fs=self, buffer=record, file_number=i)

    def filter_entries_list(
        self,
        pattern: Optional[str],
        include_all: bool = False,
        wildcard: bool = True,
    ) -> Iterator["CAPS11DirectoryEntry"]:
        if pattern:
            pattern = rt11_canonical_filename(pattern, wildcard=True)
        for entry in self.read_file_headers():
            if (
                (not pattern)
                or (wildcard and fnmatch.fnmatch(entry.basename, pattern))
                or (not wildcard and entry.basename == pattern)
            ):
                if not include_all and entry.is_empty:
                    continue
                yield entry

    @property
    def entries_list(self) -> Iterator["CAPS11DirectoryEntry"]:
        for entry in self.read_file_headers():
            if not entry.is_empty:
                yield entry

    def get_file_entry(self, fullname: str) -> Optional[CAPS11DirectoryEntry]:
        fullname = rt11_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return next(self.filter_entries_list(fullname, wildcard=False), None)

    def open_file(self, fullname: str) -> CAPS11File:
        entry = self.get_file_entry(fullname)
        if not entry:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return CAPS11File(entry)

    def read_bytes(self, fullname: str) -> bytes:
        f = self.open_file(fullname)
        try:
            return f.read_block(0, READ_FILE_FULL)
        finally:
            f.close()

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: Optional[date] = None,
        contiguous: Optional[bool] = None,
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def create_file(
        self,
        fullname: str,
        length: int,  # length in blocks
        creation_date: Optional[date] = None,  # optional creation date
        contiguous: Optional[bool] = None,
    ) -> Optional[CAPS11DirectoryEntry]:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def isdir(self, fullname: str) -> bool:
        return False

    def exists(self, fullname: str) -> bool:
        entry = self.get_file_entry(fullname)
        return entry is not None

    def dir(self, volume_id: str, pattern: Optional[str], options: Dict[str, bool]) -> None:
        if not options.get("brief"):
            dt = date.today().strftime('%d-%B-%y').upper()
            sys.stdout.write(f" {dt}\n\n")
        for x in self.filter_entries_list(pattern, include_all=True):
            if options.get("brief"):
                # Lists only file names and file types
                if x.is_empty:
                    continue
                sys.stdout.write(f"{x.filename:<6s} {x.filetype:<3s}\n")
            else:
                creation_date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or "--"
                sys.stdout.write(f"{x.filename:<6s} {x.filetype:<3s} {creation_date:<9s}\n")

    def dump(self, name: str) -> None:
        data = self.read_bytes(name)
        hex_dump(data)

    def examine(self, name: Optional[str]) -> None:
        if name:
            self.dump(name)
        else:
            sys.stdout.write("Num    Filename    Type     Rec  Seq Cont        Date     Size\n")
            for entry in self.read_file_headers():
                sys.stdout.write(f"{entry}\n")

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def close(self) -> None:
        self.f.close()

    def chdir(self, fullname: str) -> bool:
        return False

    def get_pwd(self) -> str:
        return ""

    def __str__(self) -> str:
        return str(self.f)
