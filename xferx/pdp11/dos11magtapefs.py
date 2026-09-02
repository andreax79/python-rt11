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

from ..abstract import XFERXPath, AbstractFile, AbstractFilesystem
from ..commons import BLOCK_SIZE, READ_FILE_FULL, filename_match
from ..device.abstract import AbstractDevice
from ..device.tape import Tape
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
    size: int  # size in bytes
    content: bytes  # file content

    def __init__(self, entry: "DOS11MagTapeDirectoryEntry"):
        self.entry = entry
        self.size = entry.size
        entry.fs.dev.tape_seek(entry.tape_pos)
        self.content = entry.fs.dev.tape_read_file()[HEADER_RECORD_SIZE:]

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
        return self.size

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    def __str__(self) -> str:
        return self.entry.fullname


class DOS11MagTapeDirectoryEntry(XFERXPath):
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
    stem: str = ""
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
        stem: str,
        extension: str,
        creation_date: t.Optional[date] = None,  # optional creation date
        protection_code: int = DEFAULT_PROTECTION_CODE,
        size: int = 0,
    ) -> "DOS11MagTapeDirectoryEntry":
        self = DOS11MagTapeDirectoryEntry(fs)
        self.tape_pos = tape_pos
        self.uic = uic
        self.stem = stem
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
        self.stem = rad50_word_to_asc(fnam1) + rad50_word_to_asc(fnam2) + rad50_word_to_asc(fnam3)  # RAD50 chars
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
        fnam1 = asc_to_rad50_word(self.stem[:3])
        fnam2 = asc_to_rad50_word(self.stem[3:6])
        fnam3 = asc_to_rad50_word(self.stem[6:9])
        ftyp = asc_to_rad50_word(self.extension)
        fuic = self.uic.to_word()
        # Pack the data into the buffer
        struct.pack_into(
            HEADER_RECORD, buffer, 0, fnam1, fnam2, ftyp, fuic, self.protection_code, self.raw_creation_date, fnam3
        )
        self.fs.dev.tape_seek(self.tape_pos)
        self.fs.dev.tape_write_forward(buffer)
        if skip_file:
            self.fs.dev.tape_skip_file()
        return True

    @property
    def length(self) -> int:
        """Length in blocks"""
        return int(math.ceil(self.size / BLOCK_SIZE))

    def is_dir(self) -> bool:
        return False

    @property
    def parent(self) -> "UserFileDirectory":
        """The logical parent"""
        return UserFileDirectory(self.fs, self.uic)

    def is_empty(self) -> bool:
        return self.stem == "" and self.extension == ""

    @property
    def fullname(self) -> str:
        return f"{self.uic or ''}{self.stem}.{self.extension}"

    @property
    def name(self) -> str:
        return f"{self.stem}.{self.extension}"

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return int(math.ceil(self.size / BLOCK_SIZE))

    def get_size(self, fork: t.Optional[str] = None) -> int:
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
        self.stem = ""
        self.extension = ""
        self.raw_creation_date = 0
        self.protection_code = 0
        self.write()
        return True

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> DOS11MagTapeFile:
        """
        Open a file
        """
        return DOS11MagTapeFile(self)

    def __str__(self) -> str:
        return (
            f"{self.stem:>9}."
            f"{self.extension:<3} "
            f"{self.uic.to_wide_str() if self.uic else '':<9}  "
            f"<{self.protection_code:o}> "
            f"{self.creation_date or '          '} "
            f"{self.size:>6} "
        )

    def __repr__(self) -> str:
        return str(self)


class UserFileDirectory(XFERXPath):
    fs: "DOS11MagTapeFilesystem"
    uic: UIC

    def __init__(self, fs: "DOS11MagTapeFilesystem", uic: UIC):
        self.fs = fs
        self.uic = uic

    @property
    def length(self) -> int:
        """Length in blocks"""
        return 0

    def is_dir(self) -> bool:
        return True

    @property
    def parent(self) -> XFERXPath:
        """The logical parent"""
        return self.fs.get_root()

    def is_empty(self) -> bool:
        return False

    @property
    def fullname(self) -> str:
        return f"{self.name}"

    @property
    def name(self) -> str:
        return f"{self.uic}"

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> DOS11MagTapeFile:
        """
        Open a file
        """
        raise OSError(errno.EISDIR, os.strerror(errno.EISDIR))

    def iterdir(
        self,
        pattern: t.Optional[str] = None,
        include_all: bool = False,
        wildcard: bool = True,
    ) -> t.Iterator["DOS11MagTapeDirectoryEntry"]:
        for entry in self.fs.read_file_headers(uic=self.uic):
            if filename_match(entry.name, pattern, wildcard):
                if include_all or not entry.is_empty():
                    yield entry

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return str(self)


class DOS11MagTapeRootDirectory(XFERXPath):
    fs: "DOS11MagTapeFilesystem"

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
    def parent(self) -> XFERXPath:
        """The logical parent"""
        return self

    def iterdir(self) -> t.Iterator["UserFileDirectory"]:
        """
        Get the Master File Directory entries
        """
        for uic in sorted(set([x.uic for x in self.fs.read_file_headers()])):
            yield UserFileDirectory(self.fs, uic)


class DOS11MagTapeFilesystem(AbstractFilesystem):
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
    fs_platforms = ["pdp11"]
    fs_entry_metadata = [
        "creation_date",
        "protection_code",
    ]

    dev: Tape

    uic: t.Optional[UIC] = None  # current User Identification Code

    def __init__(self, file_or_device: t.Union["AbstractFile", "AbstractDevice"]):
        if isinstance(file_or_device, AbstractFile):
            self.dev = Tape(file_or_device)
        elif isinstance(file_or_device, Tape):
            self.dev = file_or_device
        else:
            raise OSError(errno.EIO, f"Invalid device type for {self.fs_description} filesystem")

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        strict: t.Union[bool, str] = True,
        **kwargs: t.Union[bool, str],
    ) -> "DOS11MagTapeFilesystem":
        """
        Mount the filesystem from a file or device
        """
        self = cls(file_or_dev)
        # if strict:
        #     self.dev.tape_rewind()
        #     tape_pos = self.dev.tape_pos
        #     header, size = self.tape_read_header()
        #     entry = DOS11MagTapeDirectoryEntry.read(self, header, tape_pos, size)
        #     # if entry is not None or entry.size < 0:
        #     #     raise OSError(errno.EIO, os.strerror(errno.EIO))
        return self

    def read_file_headers(self, uic: UIC = ANY_UIC) -> t.Iterator["DOS11MagTapeDirectoryEntry"]:
        """Read file headers"""
        self.dev.tape_rewind()
        try:
            while True:
                tape_pos = self.dev.tape_pos
                header, size = self.dev.tape_read_header()
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
    ) -> t.Iterator[t.Union["DOS11MagTapeDirectoryEntry", "UserFileDirectory"]]:
        if uic is None:
            uic = self.uic
        uic, pattern = dos11_split_fullname(fullname=pattern, wildcard=wildcard, uic=uic)
        if uic is None:
            yield from self.get_root().iterdir()
        else:
            for entry in self.read_file_headers(uic=uic):
                if filename_match(entry.name, pattern, wildcard):
                    if include_all or not entry.is_empty():
                        yield entry

    @property
    def entries_list(self) -> t.Iterator[t.Union["DOS11MagTapeDirectoryEntry", "UserFileDirectory"]]:
        if self.uic is None:
            yield from self.get_root().iterdir()
        else:
            for entry in self.read_file_headers(uic=self.uic):
                if not entry.is_empty():
                    yield entry

    def get_file_entry(
        self, fullname: str
    ) -> t.Union["DOS11MagTapeRootDirectory", "DOS11MagTapeDirectoryEntry", "UserFileDirectory"]:
        fullname = dos11_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        uic, name = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
        if not uic:
            raise FileNotFoundError(errno.ENOENT, "No UIC specified")
        if not name:
            try:
                return UserFileDirectory(self, UIC.from_str(fullname, strict=True))
            except Exception:
                raise FileNotFoundError(errno.ENOENT, "Invalid UIC specified")
        else:
            try:
                return next(self.filter_entries_list(name, uic=uic, wildcard=False))
            except StopIteration:
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def get_root(self) -> "DOS11MagTapeRootDirectory":
        """
        Get the root directory
        """
        return DOS11MagTapeRootDirectory(self)

    def write_bytes(
        self,
        fullname: str,
        content: t.Union[bytes, bytearray],
        fork: t.Optional[str] = None,
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        """
        Write content to a file
        """
        self.create_file(fullname=fullname, size=len(content), metadata=metadata or {}, content=content)

    def create_file(
        self,
        fullname: str,
        size: int,  # Size in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
        content: t.Optional[t.Union[bytes, bytearray]] = None,
    ) -> DOS11MagTapeDirectoryEntry:
        """
        Create a new file with a given length in number of blocks
        """
        metadata = metadata or {}
        protection_code: int = metadata.get("protection_code", DEFAULT_PROTECTION_CODE)  # type: ignore
        creation_date: t.Optional[date] = metadata.get("creation_date")  # type: ignore
        # Delete the existing file
        uic, name = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
        if not uic:
            raise FileNotFoundError(errno.ENOENT, "No UIC specified")
        try:
            self.get_file_entry(name).delete()  # type: ignore
        except FileNotFoundError:
            pass
        # Find the position for the new file
        tape_pos = self.dev.tape_pos - 4  # tape mark size
        self.dev.tape_truncate()
        # Create the new directory entry
        stem, extension = name.split(".", 1)  # type: ignore
        entry = DOS11MagTapeDirectoryEntry.new(
            fs=self,
            tape_pos=tape_pos,
            uic=uic,
            stem=stem,
            extension=extension,
            creation_date=creation_date,
            protection_code=protection_code,
        )
        entry.write(skip_file=False)
        # Write the file
        empty_record = b"\0" * RECORD_SIZE
        number_of_blocks = (size + RECORD_SIZE - 1) // RECORD_SIZE
        for i in range(0, number_of_blocks):
            if content is not None:
                record = content[i * RECORD_SIZE : (i + 1) * RECORD_SIZE]
                if len(record) < RECORD_SIZE:
                    record += b"\0" * (RECORD_SIZE - len(record))
                self.dev.tape_write_forward(record)
            else:
                self.dev.tape_write_forward(empty_record)
        # Write tape mark
        self.dev.tape_write_mark()
        self.dev.tape_write_mark()
        self.dev.tape_truncate()
        return entry

    def show_accounts(self, volume_id: str, options: t.Dict[str, bool]) -> None:
        """
        Listing of all UIC
        """
        sys.stdout.write(f"{volume_id}:\n\n")
        for entry in self.get_root().iterdir():
            sys.stdout.write(f"{entry.uic.to_wide_str()}\n")

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List directory entries
        """
        i = 0
        files = 0
        blocks = 0
        uic, pattern = dos11_split_fullname(fullname=pattern, wildcard=True, uic=self.uic)
        if uic is None:
            self.show_accounts(volume_id, options)
            return
        dt = date.today().strftime('%y-%b-%d').upper()
        sys.stdout.write(f"DIRECTORY {volume_id}: {uic}\n\n{dt}\n\n")
        for x in self.filter_entries_list(pattern, uic=uic, include_all=True):
            if isinstance(x, DOS11MagTapeDirectoryEntry) and not x.is_empty():
                i = i + 1
                fullname = x.is_empty() and x.stem or "%-6s.%-3s" % (x.stem, x.extension)
                creation_date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or ""
                attr = ""
                uic_str = x.uic.to_wide_str() if uic.has_wildcard else ""
                sys.stdout.write(
                    f"{fullname:>10s} {x.length:>5d}{attr:1} {creation_date:>9s} <{x.protection_code:03o}> {uic_str}\n"
                )
                blocks += x.length
                files += 1
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

    @classmethod
    def initialize(
        cls, file_or_dev: t.Union["AbstractFile", "AbstractDevice"], **kwargs: t.Union[bool, str]
    ) -> "DOS11MagTapeFilesystem":
        """
        Initialize the filesystem
        """
        self = cls(file_or_dev)
        self.dev.tape_rewind()
        self.dev.tape_write_mark()
        self.dev.tape_truncate()
        return self

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        """
        Change the current User Identification Code
        """
        if entry is None:
            self.uic = None
        else:
            assert isinstance(entry, UserFileDirectory)
            self.uic = entry.uic

    def path_join(self, path: str, *paths: str) -> str:
        """
        Join UIC and filename
        """
        paths = [x for x in paths if x]  # type: ignore
        if not paths:
            return path
        try:
            uic = UIC.from_str(path)
        except Exception:
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
        if len(paths) > 1:
            raise OSError(errno.EINVAL, "Can only join UIC and filename")
        return f"{uic}{paths[0]}"
