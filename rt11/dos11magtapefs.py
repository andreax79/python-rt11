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
from datetime import date
from typing import Dict, Iterator, Optional

from .abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from .commons import BLOCK_SIZE, filename_match, hex_dump
from .dos11fs import (
    DEFAULT_PROTECTION_CODE,
    date_to_dos11,
    dos11_canonical_filename,
    dos11_split_fullname,
    dos11_to_date,
)
from .rad50 import asc_to_rad50_word, rad50_word_to_asc
from .tape import Tape
from .uic import ANY_UIC, DEFAULT_UIC, UIC

__all__ = [
    "DOS11MagTapeFile",
    "DOS11MagTapeDirectoryEntry",
    "DOS11MagTapeFilesystem",
]

READ_FILE_FULL = -1
HEADER_RECORD = "<HHHHHHH"
HEADER_RECORD_SIZE = 14
RECORD_SIZE = 512


class DOS11MagTapeFile(AbstractFile):
    entry: "DOS11MagTapeDirectoryEntry"
    closed: bool
    size: int  # size in bytes
    content: bytes  # file content

    def __init__(self, entry: "DOS11MagTapeDirectoryEntry"):
        self.entry = entry
        self.closed = False
        self.size = entry.size
        entry.fs.tape_seek(entry.tape_pos)
        self.content = entry.fs.tape_read_file()[HEADER_RECORD_SIZE:]

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
    extension: str = ""
    raw_creation_date: int = 0
    protection_code: int = 0  # System Programmers Manual, Pag 140
    size: int = 0  # size in bytes
    tape_pos: int = 0  # tape position (before file header)

    def __init__(self, fs: "DOS11MagTapeFilesystem"):
        self.fs = fs

    @classmethod
    def new(
        cls,
        fs: "DOS11MagTapeFilesystem",
        tape_pos: int,
        uic: UIC,
        filename: str,
        extension: str,
        creation_date: Optional[date] = None,  # optional creation date
        protection_code: int = DEFAULT_PROTECTION_CODE,
        size: int = 0,
    ) -> "DOS11MagTapeDirectoryEntry":
        self = DOS11MagTapeDirectoryEntry(fs)
        self.tape_pos = tape_pos
        self.uic = uic
        self.filename = filename
        self.extension = extension
        self.raw_creation_date = date_to_dos11(creation_date) if creation_date is not None else 0
        self.protection_code = protection_code
        self.size = size
        return self

    @classmethod
    def read(
        cls,
        fs: "DOS11MagTapeFilesystem",
        buffer: bytes,
        tape_pos: int,
        size: int,
    ) -> "DOS11MagTapeDirectoryEntry":
        self = DOS11MagTapeDirectoryEntry(fs)
        self.tape_pos = tape_pos
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
        self.extension = rad50_word_to_asc(ftyp)  # RAD50 chars
        self.uic = UIC.from_word(fuic)
        self.size = size - HEADER_RECORD_SIZE
        return self

    def write(self, skip_file: bool = True) -> None:
        buffer = bytearray(HEADER_RECORD_SIZE)
        # Convert filename to RAD50 words
        fnam1 = asc_to_rad50_word(self.filename[:3])
        fnam2 = asc_to_rad50_word(self.filename[3:6])
        fnam3 = asc_to_rad50_word(self.filename[6:9])
        ftyp = asc_to_rad50_word(self.extension)
        fuic = self.uic.to_word()
        # Pack the data into the buffer
        struct.pack_into(
            HEADER_RECORD, buffer, 0, fnam1, fnam2, ftyp, fuic, self.protection_code, self.raw_creation_date, fnam3
        )
        self.fs.tape_seek(self.tape_pos)
        self.fs.tape_write_forward(buffer)
        if skip_file:
            self.fs.tape_skip_file()

    @property
    def length(self) -> int:
        """Length in blocks"""
        return int(math.ceil(self.size / BLOCK_SIZE))

    @property
    def is_empty(self) -> bool:
        return self.filename == "" and self.extension == ""

    @property
    def fullname(self) -> str:
        return f"{self.uic or ''}{self.filename}.{self.extension}"

    @property
    def basename(self) -> str:
        return f"{self.filename}.{self.extension}"

    @property
    def creation_date(self) -> Optional[date]:
        return dos11_to_date(self.raw_creation_date)

    def delete(self) -> bool:
        """
        Delete the file
        """
        self.uic = DEFAULT_UIC
        self.filename = ""
        self.extension = ""
        self.raw_creation_date = 0
        self.protection_code = 0
        self.write()
        return True

    def __str__(self) -> str:
        return (
            f"{self.filename:>9}."
            f"{self.extension:<3} "
            f"{self.uic.to_wide_str() if self.uic else '':<9}  "
            f"<{self.protection_code:o}> "
            f"{self.creation_date or '          '} "
            f"{self.size:>6} "
        )

    def __repr__(self) -> str:
        return str(self)


class DOS11MagTapeFilesystem(AbstractFilesystem, Tape):
    """
    DOS-11 MagTape Filesystem

    Record
        +-------------------------------------+
     1  |           File header               |  14 bytes
        +-------------------------------------+
     2  |              Data                   | 512 bytes
        +-------------------------------------+
     n  |               ...                   |
        +-------------------------------------+
    n-1 |              Data                   | <= 512 bytes (last data block)
        +-------------------------------------+
     n  |               EOF                   |
        +-------------------------------------+

    DOS/BATCH File Utility Package (PIP), Pag 50
    http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp11/dos-batch/V9/DEC-11-UPPA-A-D_PIP_Aug73.pdf
    """

    uic: UIC = DEFAULT_UIC  # current User Identification Code

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

    def read_file_headers(self, uic: UIC = ANY_UIC) -> Iterator["DOS11MagTapeDirectoryEntry"]:
        """Read file headers"""
        self.tape_rewind()
        try:
            while True:
                tape_pos = self.tape_pos
                header, size = self.tape_read_header()
                if header:
                    entry = DOS11MagTapeDirectoryEntry.read(self, header, tape_pos, size)
                    if uic.match(entry.uic):
                        yield entry
        except EOFError:
            pass

    def filter_entries_list(
        self,
        pattern: Optional[str],
        include_all: bool = False,
        wildcard: bool = True,
        uic: Optional[UIC] = None,
    ) -> Iterator["DOS11MagTapeDirectoryEntry"]:
        if uic is None:
            uic = self.uic
        uic, pattern = dos11_split_fullname(fullname=pattern, wildcard=wildcard, uic=uic)
        for entry in self.read_file_headers(uic=uic):
            if filename_match(entry.basename, pattern, wildcard):
                if include_all or not entry.is_empty:
                    yield entry

    @property
    def entries_list(self) -> Iterator["DOS11MagTapeDirectoryEntry"]:
        for entry in self.read_file_headers(uic=self.uic):
            if not entry.is_empty:
                yield entry

    def get_file_entry(self, fullname: str) -> Optional[DOS11MagTapeDirectoryEntry]:
        fullname = dos11_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        uic, basename = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
        return next(self.filter_entries_list(basename, uic=uic, wildcard=False), None)

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
        file_type: Optional[str] = None,
        protection_code: int = DEFAULT_PROTECTION_CODE,
    ) -> None:
        """
        Write content to a file
        """
        length = int(math.ceil(len(content) * 1.0 / RECORD_SIZE))
        self.create_file(
            fullname=fullname,
            length=length,
            creation_date=creation_date,
            content=content,
            protection_code=protection_code,
        )

    def create_file(
        self,
        fullname: str,
        length: int,  # length in blocks
        creation_date: Optional[date] = None,  # optional creation date
        file_type: Optional[str] = None,
        content: Optional[bytes] = None,
        protection_code: int = DEFAULT_PROTECTION_CODE,
    ) -> Optional[DOS11MagTapeDirectoryEntry]:
        """
        Create a new file with a given length in number of blocks
        """
        # Delete the existing file
        uic, basename = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
        old_entry = self.get_file_entry(basename)  # type: ignore
        if old_entry is not None:
            old_entry.delete()
        # Find the position for the new file
        tape_pos = self.tape_pos - 4  # tape mark size
        self.f.truncate(tape_pos)
        # Create the new directory entry
        filename, extension = basename.split(".", 1)  # type: ignore
        entry = DOS11MagTapeDirectoryEntry.new(
            fs=self,
            tape_pos=tape_pos,
            uic=uic,
            filename=filename,
            extension=extension,
            creation_date=creation_date,
            protection_code=protection_code,
        )
        entry.write(skip_file=False)
        # Write the file
        empty_record = b"\0" * RECORD_SIZE
        for i in range(0, length):
            if content is not None:
                record = content[i * RECORD_SIZE : (i + 1) * RECORD_SIZE]
                if len(record) < RECORD_SIZE:
                    record += b"\0" * (RECORD_SIZE - len(record))
                self.tape_write_forward(record)
            else:
                self.tape_write_forward(empty_record)
        # Write tape mark
        self.tape_write_mark()
        self.tape_write_mark()
        self.f.truncate(self.tape_pos)
        return entry

    def isdir(self, fullname: str) -> bool:
        return False

    def exists(self, fullname: str) -> bool:
        entry = self.get_file_entry(fullname)
        return entry is not None

    def dir(self, volume_id: str, pattern: Optional[str], options: Dict[str, bool]) -> None:
        if options.get("uic"):
            # Listing of all UIC
            sys.stdout.write(f"{volume_id}:\n\n")
            for uic in sorted(set([x.uic for x in self.read_file_headers(uic=ANY_UIC)])):
                sys.stdout.write(f"{uic.to_wide_str()}\n")
            return
        i = 0
        files = 0
        blocks = 0
        uic, pattern = dos11_split_fullname(fullname=pattern, wildcard=True, uic=self.uic)
        if not options.get("brief"):
            dt = date.today().strftime('%y-%b-%d').upper()
            sys.stdout.write(f"DIRECTORY {volume_id}: {uic}\n\n{dt}\n\n")
        for x in self.filter_entries_list(pattern, uic=uic, include_all=True):
            if x.is_empty:
                continue
            i = i + 1
            if x.is_empty:
                continue
            fullname = x.is_empty and x.filename or "%-6s.%-3s" % (x.filename, x.extension)
            if options.get("brief"):
                # Lists only file names and file types
                sys.stdout.write(f"{fullname}\n")
                continue
            creation_date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or ""
            attr = ""
            uic_str = x.uic.to_wide_str() if uic.has_wildcard else ""
            sys.stdout.write(
                f"{fullname:>10s} {x.length:>5d}{attr:1} {creation_date:>9s} <{x.protection_code:03o}> {uic_str}\n"
            )
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
            for entry in self.read_file_headers(uic=ANY_UIC):
                sys.stdout.write(f"{entry}\n")

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self) -> None:
        """
        Initialize the filesytem
        """
        self.tape_rewind()
        self.tape_write_mark()
        self.f.truncate(self.tape_pos)

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
