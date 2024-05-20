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
from .dos11fs import dos11_to_date
from .rad50 import rad50_word_to_asc
from .rt11fs import rt11_canonical_filename
from .uic import UIC

__all__ = [
    "DOS11MagTapeFile",
    "DOS11MagTapeDirectoryEntry",
    "DOS11MagTapeFilesystem",
]

READ_FILE_FULL = -1
HEADER_RECORD = "<HHHHHHH"
HEADER_RECORD_SIZE = 14
DEFAULT_UIC = UIC(0o1, 0o1)


class DOS11MagTapeFile(AbstractFile):
    entry: "DOS11MagTapeDirectoryEntry"
    closed: bool
    size: int  # size in bytes
    content: bytes  # file content

    def __init__(self, entry: "DOS11MagTapeDirectoryEntry"):
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


class DOS11MagTapeDirectoryEntry(AbstractDirectoryEntry):
    """
    DOS-11 MagTape File Header

        +-------------------------------------+
     0  |               File                  |
     2  |               name                  |
        +-------------------------------------+
     4  |            Extension                |
        +-------------------------------------+
     6  |               UIC                   |
        +-------------------------------------+
     8  |          Protection code            |
        +-------------------------------------+
    10  |           Creation Date             |
        +-------------------------------------+
    12  |             File name               |
        +-------------------------------------+
    """

    fs: "DOS11MagTapeFilesystem"
    uic: UIC = DEFAULT_UIC
    filename: str = ""
    filetype: str = ""
    raw_creation_date: int = 0
    file_number: int = 0
    size: int = 0  # size in bytes
    protection_code: int = 0  # System Programmers Manual, Pag 140

    def __init__(self, fs: "DOS11MagTapeFilesystem"):
        self.fs = fs

    def read(self, buffer: bytes, file_number: int) -> None:
        self.file_number = file_number
        (
            fnam1,
            fnam2,
            ftyp,
            fuic,
            self.protection_code,
            self.raw_creation_date,
            fnam3,
        ) = struct.unpack_from(HEADER_RECORD, buffer, 0)
        self.filename = rad50_word_to_asc(fnam1) + rad50_word_to_asc(fnam2) + rad50_word_to_asc(fnam3)  # RAD50 chars
        self.filetype = rad50_word_to_asc(ftyp)  # RAD50 chars
        self.uic = UIC.from_word(fuic)
        self.size = len(buffer) - HEADER_RECORD_SIZE

    @property
    def length(self) -> int:
        """Length in blocks"""
        return int(math.ceil(self.size / BLOCK_SIZE))

    @property
    def is_empty(self) -> bool:
        return self.filename == "" and self.filetype == ""

    @property
    def fullname(self) -> str:
        return f"{self.uic or ''}{self.filename}.{self.filetype}"

    @property
    def basename(self) -> str:
        return f"{self.filename}.{self.filetype}"

    @property
    def creation_date(self) -> Optional[date]:
        return dos11_to_date(self.raw_creation_date)

    def delete(self) -> bool:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def __str__(self) -> str:
        return (
            f"{self.file_number:<4} "
            f"{self.filename:<9}."
            f"{self.filetype:<3} "
            f"{self.uic.to_wide_str() if self.uic else '':<9}  "
            f"<{self.protection_code:o}> "
            f"{self.creation_date or '          '} "
            f"{self.size:>6} "
        )

    def __repr__(self) -> str:
        return str(self)


class DOS11MagTapeFilesystem(AbstractFilesystem):
    """
    DOS-11 MagTape Filesystem
    """

    uic: UIC  # current User Identification Code

    def __init__(self, file: "AbstractFile"):
        self.f = file
        self.uic = DEFAULT_UIC

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

    def read_file_headers(self, uic: Optional[UIC] = None) -> Iterator["DOS11MagTapeDirectoryEntry"]:
        """Read file headers"""
        for i, record in enumerate(self.read_magtape(), start=1):
            entry = DOS11MagTapeDirectoryEntry(self)
            entry.read(record, i)
            if uic is None or uic == entry.uic:
                yield entry

    def dos11_canonical_filename(self, fullname: str, wildcard: bool = False) -> str:
        try:
            if "[" in fullname:
                uic: Optional[UIC] = UIC.from_str(fullname)
                fullname = fullname.split("]", 1)[1]
            else:
                uic = None
        except Exception:
            uic = None
        if fullname:
            fullname = rt11_canonical_filename(fullname, wildcard=wildcard)
        return f"{uic or ''}{fullname}"

    def filter_entries_list(
        self, pattern: Optional[str], include_all: bool = False, wildcard: bool = True
    ) -> Iterator["DOS11MagTapeDirectoryEntry"]:
        uic = self.uic
        if pattern:
            if "[" in pattern:
                try:
                    uic = UIC.from_str(pattern)
                    pattern = pattern.split("]", 1)[1]
                except Exception:
                    return
            if pattern:
                pattern = rt11_canonical_filename(pattern, wildcard=wildcard)
        for entry in self.read_file_headers(uic=uic):
            if (
                (not pattern)
                or (wildcard and fnmatch.fnmatch(entry.basename, pattern))
                or (not wildcard and entry.basename == pattern)
            ):
                if not include_all and entry.is_empty:
                    continue
                yield entry

    @property
    def entries_list(self) -> Iterator["DOS11MagTapeDirectoryEntry"]:
        for entry in self.read_file_headers(uic=self.uic):
            if not entry.is_empty:
                yield entry

    def get_file_entry(self, fullname: str) -> Optional[DOS11MagTapeDirectoryEntry]:
        fullname = self.dos11_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return next(self.filter_entries_list(fullname, wildcard=False), None)

    def open_file(self, fullname: str) -> DOS11MagTapeFile:
        entry = self.get_file_entry(fullname)
        if not entry:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return DOS11MagTapeFile(entry)

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
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def create_file(
        self,
        fullname: str,
        length: int,  # length in blocks
        creation_date: Optional[date] = None,  # optional creation date
    ) -> Optional[DOS11MagTapeDirectoryEntry]:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def isdir(self, fullname: str) -> bool:
        return False

    def exists(self, fullname: str) -> bool:
        entry = self.get_file_entry(fullname)
        return entry is not None

    def dir(self, volume_id: str, pattern: Optional[str], options: Dict[str, bool]) -> None:
        if options.get("uic"):
            # Listing of all UIC
            for uic in sorted(set([x.uic for x in self.read_file_headers(uic=None)])):
                sys.stdout.write(f"{uic.to_wide_str()}\n")
            return
        i = 0
        files = 0
        blocks = 0
        for x in self.filter_entries_list(pattern, include_all=True):
            if i == 0 and not options.get("brief"):
                dt = date.today().strftime('%y-%b-%d').upper()
                sys.stdout.write(f"DIRECTORY {volume_id}: {x.uic}\n\n{dt}\n\n")
            if x.is_empty:
                continue
            i = i + 1
            if x.is_empty:
                continue
            fullname = x.is_empty and x.filename or "%-6s.%-3s" % (x.filename, x.filetype)
            if options.get("brief"):
                # Lists only file names and file types
                sys.stdout.write(f"{fullname}\n")
                continue
            creation_date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or ""
            attr = ""
            sys.stdout.write(f"{fullname:>10s} {x.length:>5d}{attr:1} {creation_date:>9s} <{x.protection_code:03o}>\n")
            blocks += x.length
            files += 1
        if options.get("brief"):
            return
        sys.stdout.write("\n")
        sys.stdout.write(f"TOTL BLKS: {blocks:5}\n")
        sys.stdout.write(f"TOTL FILES: {files:4}\n")

    def dump(self, name: str) -> None:
        data = self.read_bytes(name)
        hex_dump(data)

    def examine(self, name: Optional[str]) -> None:
        if name:
            self.dump(name)
        else:
            for entry in self.read_file_headers(uic=None):
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
        """
        Change the current User Identification Code
        """
        try:
            self.uic = UIC.from_str(fullname)
            return True
        except Exception:
            return False

    def get_pwd(self) -> str:
        return str(self.uic)

    def __str__(self) -> str:
        return str(self.f)
