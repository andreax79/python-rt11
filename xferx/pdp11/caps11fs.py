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
import math
import os
import struct
import sys
import typing as t
from datetime import date

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..commons import BLOCK_SIZE, READ_FILE_FULL, filename_match
from ..tape import Tape
from .rt11fs import rt11_canonical_filename

__all__ = [
    "CAPS11File",
    "CAPS11DirectoryEntry",
    "CAPS11Filesystem",
]

HEADER_RECORD = ">6s3sBHBB6sB11s"
HEADER_RECORD_SIZE = 32
RECORD_SIZE = 128
SENTINEL_FILE = b"\0" * HEADER_RECORD_SIZE

# CAPS-11 uses file type codes 0o1 (ASCII), 0o2 (BIN), and 0o14 (BAD)
# CAPS-11 Users Guide, Pag 290
# http://bitsavers.org/pdf/dec/pdp11/caps-11/DEC-11-OTUGA-A-D_CAPS-11_Users_Guide_Oct73.pdf

FILE_TYPE_ASCII = 0o1  # ASCII (seven bits per character)
FILE_TYPE_BIN = 0o2
FILE_TYPE_CORE1 = 0o3  # One 36-bit word in 5 bytes
FILE_TYPE_CORE2 = 0o4  # One 12-bit word in 2 bytes
FILE_TYPE_CORE3 = 0o5  # One 18-bit word in 3 bytes
FILE_TYPE_CORE4 = 0o6  # One 36-bit word in 6 bytes
FILE_TYPE_CORE5 = 0o7  # One 16-bit word in 2 bytes
FILE_TYPE_CORE6 = 0o10  # 2 x 12-bit words in 3 bytes
FILE_TYPE_CORE7 = 0o11  # 2 x 36-bit words in 9 bytes
FILE_TYPE_CORE8 = 0o12  # 4 x 18-bit words in 9 bytes
FILE_TYPE_BOOT = 0o13  # Bootstrap
FILE_TYPE_BAD = 0o14  # Bad file

STANDARD_FILE_TYPES = {
    FILE_TYPE_ASCII: "ASCII",
    FILE_TYPE_BIN: "BIN",
    FILE_TYPE_CORE1: "CORE1",
    FILE_TYPE_CORE2: "CORE2",
    FILE_TYPE_CORE3: "CORE3",
    FILE_TYPE_CORE4: "CORE4",
    FILE_TYPE_CORE5: "CORE5",
    FILE_TYPE_CORE6: "CORE6",
    FILE_TYPE_CORE7: "CORE7",
    FILE_TYPE_CORE8: "CORE8",
    FILE_TYPE_BOOT: "BOOT",
    FILE_TYPE_BAD: "BAD",
}


def caps11_to_date(val: bytes) -> t.Optional[date]:
    """
    Translate CAPS-11 date (stored in ASCII as ddmmyy) to Python date
    """
    try:
        date_str = val.decode("ascii", errors="ignore")
        day = int(date_str[0:2])
        month = int(date_str[2:4])
        year = int(date_str[4:6])
        return date(year + (1900 if year >= 60 else 2000), month, day)
    except:
        return None


def date_to_caps11(d: t.Optional[date]) -> bytes:
    """
    Translate Python date to CAPS-11 date
    """
    if d is None:
        return b"     "
    day = f"{d.day:02}"
    month = f"{d.month:02}"
    year = f"{d.year:04}"[2:4]
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
        entry.fs.tape_seek(entry.tape_pos)
        self.content = entry.fs.tape_read_file()[HEADER_RECORD_SIZE + entry.continued :]

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

      9 bytes   1 byte  2 bytes        1 byte 1 byte  6 bytes  12 bytes
    +----------+------+---------------+------+-------+--------+--------+
    | Filename | Type | Record length | Seq. | Cont. |  Data  |  Spare |
    +----------+------+---------------+------+-------+--------+--------+

    CAPS-11 Users Guide, Pag 289
    http://bitsavers.org/pdf/dec/pdp11/caps-11/DEC-11-OTUGA-A-D_CAPS-11_Users_Guide_Oct73.pdf

    CAPS-8 File Header

      9 bytes   1 byte  2 bytes        1 byte  1 byte  6 bytes   1 byte  11 bytes
    +----------+------+---------------+------+-------+--------+---------+---------+
    | Filename | Type | Record length | Seq. | Cont. |  Data  | Version |  Spare  |
    +----------+------+---------------+------+-------+--------+---------+---------+

    CAPS-8 Users Guide, Pag 205
    https://bitsavers.org/pdf/dec/pdp8/caps8/DEC-8E-OCASA-B-D_CAPS8_UG.pdf

    """

    fs: "CAPS11Filesystem"
    filename: str = ""  #             6 chars - name
    extension: str = ""  #            3 chars - extension
    record_type: int = 0  #           1 byte  - record type
    record_length: int = 0  #         2 bytes - file record length (fixed at 128 bytes)
    sequence: int = 0  #              1 byte  - file sequence number for multi volume files (0)
    continued: int = 0  #             1 byte  - header auxiliary header record (0)
    raw_creation_date: bytes = b""  # 6 char  - file creation date as DDMMYY
    version: int = 0  #               1 byte  - version number (incremented by the editor) - CAPS-8 only
    unused: bytes = b""  #           11 char  - spare

    file_number: int = 0
    size: int = 0  # size in bytes
    tape_pos: int = 0  # tape position (before file header)

    def __init__(self, fs: "CAPS11Filesystem"):
        self.fs = fs

    @classmethod
    def new(
        cls,
        fs: "CAPS11Filesystem",
        file_number: int,
        tape_pos: int,
        filename: str,
        extension: str,
        creation_date: t.Optional[date] = None,  # optional creation date
        record_type: int = FILE_TYPE_BIN,
    ) -> "CAPS11DirectoryEntry":
        self = CAPS11DirectoryEntry(fs)
        self.file_number = file_number
        self.tape_pos = tape_pos
        self.filename = filename
        self.extension = extension
        self.record_type = record_type
        self.record_length = RECORD_SIZE
        self.raw_creation_date = date_to_caps11(creation_date)
        self.sequence = 0
        self.continued = 0
        self.version = 0
        self.unused = b"\0" * 11
        return self

    @classmethod
    def read(
        cls,
        fs: "CAPS11Filesystem",
        buffer: bytes,
        file_number: int,
        tape_pos: int,
        size: int,
    ) -> "CAPS11DirectoryEntry":
        self = CAPS11DirectoryEntry(fs)
        self.file_number = file_number
        self.tape_pos = tape_pos
        (
            filename,
            extension,
            self.record_type,
            self.record_length,
            self.sequence,
            self.continued,
            self.raw_creation_date,
            self.version,
            self.unused,
        ) = struct.unpack_from(HEADER_RECORD, buffer, 0)
        # print(" ".join([f"{b:02x}" for b in buffer]))
        self.filename = filename.decode("ascii", errors="ignore").rstrip(" ")
        self.extension = extension.decode("ascii", errors="ignore").rstrip(" ")
        if not self.filename or self.filename.startswith("\0"):  # Sentinel file
            self.filename = ""
            self.extension = ""
        self.size = size - self.continued
        return self

    def write(self, skip_file: bool = True) -> bool:
        """
        Write the directory entry
        """
        buffer = bytearray(HEADER_RECORD_SIZE)
        if not self.filename:  # Sentinel file
            filename = b"\0"
            extension = b"\0"
        else:
            filename = self.filename.ljust(6).encode("ascii", errors="ignore")
            extension = self.extension.ljust(3).encode("ascii", errors="ignore")
        # Pack the data into the buffer
        struct.pack_into(
            HEADER_RECORD,
            buffer,
            0,
            filename,
            extension,
            self.record_type,
            self.record_length,
            self.sequence,
            self.continued,
            self.raw_creation_date,
            self.version,
            self.unused,
        )
        self.fs.tape_seek(self.tape_pos)
        self.fs.tape_write_forward(buffer)
        if skip_file:
            self.fs.tape_skip_file()
        return True

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return int(math.ceil(self.size / BLOCK_SIZE))

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

    @property
    def is_empty(self) -> bool:
        return self.record_type == FILE_TYPE_BAD or self.is_sentinel_file

    @property
    def is_sentinel_file(self) -> bool:
        return not self.filename

    @property
    def fullname(self) -> str:
        return f"{self.filename}.{self.extension}"

    @property
    def basename(self) -> str:
        return f"{self.filename}.{self.extension}"

    @property
    def creation_date(self) -> t.Optional[date]:
        return caps11_to_date(self.raw_creation_date)

    @property
    def file_type(self) -> t.Optional[str]:
        return STANDARD_FILE_TYPES.get(self.record_type)

    def delete(self) -> bool:
        """
        Delete the file
        """
        self.filename = "*EMPTY"
        self.extension = ""
        self.record_type = FILE_TYPE_BAD
        self.record_length = 0
        self.sequence = 0
        self.continued = 0
        self.raw_creation_date = b""
        self.write()
        return True

    def open(self, file_mode: t.Optional[str] = None) -> CAPS11File:
        """
        Open a file
        """
        return CAPS11File(self)

    def __str__(self) -> str:
        file_type = self.file_type or f"{self.record_type:>4o}"
        return (
            f"{self.file_number:<4} "
            f"{self.filename:>6}."
            f"{self.extension:<3}  "
            f"{file_type:>6}  "
            f"{self.record_length:>6} "
            f"{self.sequence:>4} "
            f"{self.continued:>4}  "
            f"{self.creation_date or '          '} "
            f"{self.size:>8} "
            f"{(self.version if self.fs.caps8 else ''):>3}"
        )

    def __repr__(self) -> str:
        return str(self)


class CAPS11Filesystem(AbstractFilesystem, Tape):
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
    http://bitsavers.org/pdf/dec/pdp11/caps-11/DEC-11-OTUGA-A-D_CAPS-11_Users_Guide_Oct73.pdf
    """

    fs_name = "caps11"
    fs_description = "PDP-11 CAPS-11"
    caps8 = False  # Is CAPS-8 ?

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        if strict:
            for entry in self.read_file_headers(include_eot=False):
                if not entry.is_sentinel_file and entry.version != 0:
                    self.caps8 = True
                if entry.record_length not in [0, RECORD_SIZE] or entry.size < 0:
                    raise OSError(errno.EIO, f"Invalid record length ({entry.record_length}) {entry}")
        return self

    def read_file_headers(self, include_eot: bool = False) -> t.Iterator["CAPS11DirectoryEntry"]:
        """Read file headers"""
        self.tape_rewind()
        try:
            file_number = 0
            while True:
                tape_pos = self.tape_pos
                header, size = self.tape_read_header()
                if header:
                    file_number += 1
                    entry = CAPS11DirectoryEntry.read(self, header, file_number, tape_pos, size)
                    if include_eot or not entry.is_sentinel_file:
                        yield entry
        except EOFError:
            pass

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["CAPS11DirectoryEntry"]:
        if pattern:
            pattern = rt11_canonical_filename(pattern, wildcard=wildcard)
        for entry in self.read_file_headers():
            if filename_match(entry.basename, pattern, wildcard) and (include_all or not entry.is_empty):
                yield entry

    @property
    def entries_list(self) -> t.Iterator["CAPS11DirectoryEntry"]:
        for entry in self.read_file_headers():
            if not entry.is_empty:
                yield entry

    def get_file_entry(self, fullname: str) -> CAPS11DirectoryEntry:
        fullname = rt11_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        try:
            return next(self.filter_entries_list(fullname, wildcard=False))
        except StopIteration:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: t.Optional[date] = None,
        file_type: t.Optional[str] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        """
        Write content to a file
        """
        number_of_blocks = int(math.ceil(len(content) * 1.0 / RECORD_SIZE))
        self.create_file(fullname, number_of_blocks, creation_date, content=content)

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,
        content: t.Optional[bytes] = None,
    ) -> t.Optional[CAPS11DirectoryEntry]:
        """
        Create a new file with a given length in number of blocks
        """
        # Delete the existing file
        fullname = rt11_canonical_filename(fullname, wildcard=False)
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass
        # Find the position for the new file
        tape_pos = 0
        for entry in reversed(list(self.read_file_headers(include_eot=True))):
            if (not entry.is_sentinel_file) and entry.record_type != FILE_TYPE_BAD:
                break
            tape_pos = entry.tape_pos
        self.tape_seek(tape_pos)
        self.f.truncate(tape_pos)
        # Create the new directory entry
        filename, extension = fullname.split(".", 1)
        entry = CAPS11DirectoryEntry.new(self, 0, tape_pos, filename, extension, creation_date)
        entry.write(skip_file=False)
        # Write the file
        empty_record = b"\0" * RECORD_SIZE
        for i in range(0, number_of_blocks):
            if content is not None:
                record = content[i * RECORD_SIZE : (i + 1) * RECORD_SIZE]
                if len(record) < RECORD_SIZE:
                    record += b"\0" * (RECORD_SIZE - len(record))
                self.tape_write_forward(record)
            else:
                self.tape_write_forward(empty_record)
        # Write tape mark
        self.tape_write_mark()
        # Write the sentinel file
        self.write_sentinel_file()
        return entry

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        if not options.get("brief"):
            if self.caps8:
                dt = date.today().strftime('%m/%d/%y').upper()
                sys.stdout.write(f"{dt}\n")
            else:
                dt = date.today().strftime('%d-%B-%y').upper()
                sys.stdout.write(f" {dt}\n\n")

        for x in self.filter_entries_list(pattern, include_all=True):
            if options.get("brief"):
                # Omit creation date and version number
                if x.is_empty:
                    continue
                elif self.caps8:
                    sys.stdout.write(f"{x.filename:<6s}.{(x.extension or 'BIN'):<3s}\n")
                else:
                    sys.stdout.write(f"{x.filename:<6s} {x.extension:<3s}\n")
            elif self.caps8:
                version = f"V{x.version}" if x.version else ""
                creation_date = x.creation_date and x.creation_date.strftime("%m/%d/%y").upper() or ""
                sys.stdout.write(f"{x.filename:<6s}.{(x.extension or 'BIN'):<3s} {creation_date:<8s} {version}\n")
            else:
                creation_date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or "--"
                sys.stdout.write(f"{x.filename:<6s} {x.extension:<3s} {creation_date:<9s}\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if arg:
            self.dump(arg)
        else:
            if self.caps8:
                sys.stdout.write("Num    Filename    Type     Rec  Seq Cont        Date     Size Ver\n")
                sys.stdout.write("---    --------    ----     ---  --- ----        ----     ---- ---\n")
            else:
                sys.stdout.write("Num    Filename    Type     Rec  Seq Cont        Date     Size\n")
                sys.stdout.write("---    --------    ----     ---  --- ----        ----     ----\n")
            for entry in self.read_file_headers(include_eot=True):
                sys.stdout.write(f"{entry}\n")

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        """
        Initialize the filesystem
        """
        self.tape_rewind()
        self.tape_write_mark()
        self.write_sentinel_file()

    def write_sentinel_file(self) -> None:
        """
        Write the sentinel file at the current tape position
        The sentinel file is the last file on a tape
        """
        self.tape_write_forward(SENTINEL_FILE)
        self.f.truncate(self.tape_pos)

    def close(self) -> None:
        self.f.close()

    def chdir(self, fullname: str) -> bool:
        return False

    def get_pwd(self) -> str:
        return ""

    def get_types(self) -> t.List[str]:
        """
        Get the list of the supported file types
        """
        return list(STANDARD_FILE_TYPES.values())

    def __str__(self) -> str:
        return str(self.f)
