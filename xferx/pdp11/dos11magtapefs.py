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
from ..uic import ANY_UIC, DEFAULT_UIC, UIC
from .dos11fs import (
    DEFAULT_PROTECTION_CODE,
    date_to_dos11,
    dos11_canonical_filename,
    dos11_split_fullname,
    dos11_to_date,
)
from .rad50 import asc_to_rad50_word, rad50_word_to_asc

__all__ = [
    "DOS11MagTapeFile",
    "DOS11MagTapeDirectoryEntry",
    "DOS11MagTapeFilesystem",
]

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
        creation_date: t.Optional[date] = None,  # optional creation date
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

    def write(self, skip_file: bool = True) -> bool:
        """
        Write the directory entry
        """
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
        return True

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
    def creation_date(self) -> t.Optional[date]:
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

    def open(self, file_mode: t.Optional[str] = None) -> DOS11MagTapeFile:
        """
        Open a file
        """
        return DOS11MagTapeFile(self)

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
    DOS/BATCH MagTape Filesystem

    All files on magnetic tape have the following format:

    Record
        +-------------------------------------+
     1  |           File header               |  14 bytes
        +-------------------------------------+
     2  |              Data                   | 512 bytes
        +-------------------------------------+
        |                                     |
     n  /               ...                   /
        |                                     |
        +-------------------------------------+
    n-1 |              Data                   | <= 512 bytes (last data block)
        +-------------------------------------+
     n  |               EOF                   |
        +-------------------------------------+

    DOS/BATCH File Utility Package (PIP), Pag 50
    http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp11/dos-batch/V9/DEC-11-UPPA-A-D_PIP_Aug73.pdf
    """

    fs_name = "magtape"
    fs_description = "PDP-11 DOS/BATCH Magtape"

    uic: UIC = DEFAULT_UIC  # current User Identification Code

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        # if strict:
        #     self.tape_rewind()
        #     tape_pos = self.tape_pos
        #     header, size = self.tape_read_header()
        #     entry = DOS11MagTapeDirectoryEntry.read(self, header, tape_pos, size)
        #     # if entry is not None or entry.size < 0:
        #     #     raise OSError(errno.EIO, os.strerror(errno.EIO))
        return self

    def read_file_headers(self, uic: UIC = ANY_UIC) -> t.Iterator["DOS11MagTapeDirectoryEntry"]:
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
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
        uic: t.Optional[UIC] = None,
    ) -> t.Iterator["DOS11MagTapeDirectoryEntry"]:
        if uic is None:
            uic = self.uic
        uic, pattern = dos11_split_fullname(fullname=pattern, wildcard=wildcard, uic=uic)
        for entry in self.read_file_headers(uic=uic):
            if filename_match(entry.basename, pattern, wildcard):
                if include_all or not entry.is_empty:
                    yield entry

    @property
    def entries_list(self) -> t.Iterator["DOS11MagTapeDirectoryEntry"]:
        for entry in self.read_file_headers(uic=self.uic):
            if not entry.is_empty:
                yield entry

    def get_file_entry(self, fullname: str) -> DOS11MagTapeDirectoryEntry:
        fullname = dos11_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        uic, basename = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
        try:
            return next(self.filter_entries_list(basename, uic=uic, wildcard=False))
        except StopIteration:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: t.Optional[date] = None,
        file_type: t.Optional[str] = None,
        file_mode: t.Optional[str] = None,
        protection_code: int = DEFAULT_PROTECTION_CODE,
    ) -> None:
        """
        Write content to a file
        """
        number_of_blocks = int(math.ceil(len(content) * 1.0 / RECORD_SIZE))
        self.create_file(
            fullname=fullname,
            number_of_blocks=number_of_blocks,
            creation_date=creation_date,
            content=content,
            protection_code=protection_code,
        )

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,
        content: t.Optional[bytes] = None,
        protection_code: int = DEFAULT_PROTECTION_CODE,
    ) -> t.Optional[DOS11MagTapeDirectoryEntry]:
        """
        Create a new file with a given length in number of blocks
        """
        # Delete the existing file
        uic, basename = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
        try:
            self.get_file_entry(basename).delete()  # type: ignore
        except FileNotFoundError:
            pass
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
        self.tape_write_mark()
        self.f.truncate(self.tape_pos)
        return entry

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
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

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if arg:
            self.dump(arg)
        else:
            sys.stdout.write("     Filename    UIC    Access Date         Size\n")
            sys.stdout.write("     --------    ---    ------ ----         -----\n")
            for entry in self.read_file_headers(uic=ANY_UIC):
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
