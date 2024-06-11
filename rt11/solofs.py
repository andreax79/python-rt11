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
import os
import struct
import sys
from datetime import date
from typing import Dict, Iterator, List, Optional

from .abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from .commons import BLOCK_SIZE, filename_match, hex_dump

__all__ = [
    "SOLOFile",
    "SOLODirectoryEntry",
    "SOLOFilesystem",
    "solo_canonical_filename",
]

READ_FILE_FULL = -1

DISK_SIZE = 4800  # Disk size in blocks
CAT_ADDR = 154  # Catalog block number
ID_LENGTH = 12  # Filename length
ENTRY_FORMAT = "<12sHHH10sHH"  # Catalog entry format
ENTRY_LENGTH = 32  # Catalog entry length
CAT_PAGE_LENGTH = BLOCK_SIZE // ENTRY_LENGTH  # Entries in a catalog page

# File types
EMPTY = 0  #   Empty file
SCRATCH = 1  # Scratch file
ASCII = 2  #   Ascii file
SEQCODE = 3  # Sequential Pascal code file
CONCODE = 4  # Concurrent Pascal code file

KIND = {
    EMPTY: "EMPTY",
    SCRATCH: "SCRATCH",
    ASCII: "ASCII",
    SEQCODE: "SEQCODE",
    CONCODE: "CONCODE",
}


def filename_hash(filename: str, catalog_length: int) -> int:
    """
    Calculate the hash for a given file name
    """
    key = 1
    for c in filename[:ID_LENGTH]:
        if c != " ":
            key = key * ord(c.upper()) % (catalog_length * CAT_PAGE_LENGTH) + 1
    return key


def solo_canonical_filename(fullname: Optional[str], wildcard: bool = False) -> str:
    """
    Generate the canonical SOLO name
    """
    return (fullname or "").upper()[:ID_LENGTH]


class SOLOFile(AbstractFile):
    entry: "SOLODirectoryEntry"
    closed: bool

    def __init__(self, entry: "SOLODirectoryEntry"):
        self.entry = entry
        self.closed = False

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
        data = bytearray()
        for i in range(block_number, block_number + number_of_blocks):
            disk_block_number = self.entry.page_map[i]
            data.extend(self.entry.fs.read_block(disk_block_number))
        return bytes(data)

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
        return self.entry.length * BLOCK_SIZE

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


class SOLODirectoryEntry(AbstractDirectoryEntry):
    """
    Directory Entry

        +-------------------------------------+
     0  |               File                  |
    11  |               name                  |
        +-------------------------------------+
    12  |             File type               |
        +-------------------------------------+
    14  |       Page map block number         |
        +-------------------------------------+
    16  |           Protected flag            |
        +-------------------------------------+
    18  |              Spare                  |
        +-------------------------------------+
    28  |        Key (Filename hash)          |
        +-------------------------------------+
    30  |          Search length              |
        +-------------------------------------+

    """

    fs: "SOLOFilesystem"
    filename: str = ""  # Filename (12 chars)
    kind_id: int = 0  # File type (scratch, ascii, seqcode and concode)
    page_map_block_number: int = 0  # Page map block number
    protected: bool = False  # Protected against accidental overwriting or deletion
    spare: bytes = b""
    key: int = 0  # Filename hash
    searchlength: int = 0  # Number of files with the same key
    length: int = 0  # File length in blocks
    page_num: int = 0  # Catalog page number
    page_map: List[int]  # Map file blocks to disk blocks

    def __init__(self, fs: "SOLOFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "SOLOFilesystem", buffer: bytes, position: int, page_num: int) -> "SOLODirectoryEntry":
        self = SOLODirectoryEntry(fs)
        self.page_num = page_num
        (
            file_id,
            self.kind_id,
            self.page_map_block_number,
            raw_protected,
            self.spare,
            self.key,
            self.searchlength,
        ) = struct.unpack_from(ENTRY_FORMAT, buffer, position)
        self.filename = file_id.decode("ascii", errors="ignore").rstrip()
        self.protected = bool(raw_protected)
        if self.is_empty:
            self.page_map = []
            self.length = 0
        else:
            self.page_map = self.fs.read_page_map(self.page_map_block_number)
            self.length = len(self.page_map)
        return self

    def write(self, buffer: bytearray, position: int) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    @property
    def is_empty(self) -> bool:
        return self.filename == ""

    @property
    def fullname(self) -> str:
        return self.filename

    @property
    def basename(self) -> str:
        return self.filename

    @property
    def kind(self) -> str:
        return KIND.get(self.kind_id, "")

    @property
    def creation_date(self) -> Optional[date]:
        return None

    def delete(self) -> bool:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def __str__(self) -> str:
        return f"{self.filename:<12}  \
Kind: {self.kind:<8}  \
Protected: {'Y' if self.protected else 'N'}  \
Block: {self.page_map_block_number:>6}  \
Key: {self.key:>6} {'('+str(self.searchlength)+')':<6}  \
Length: {self.length:>6}  \
Page: {self.page_num:>2}"

    def __repr__(self) -> str:
        return str(self)

    def __lt__(self, other: "SOLODirectoryEntry") -> bool:
        return self.filename < other.filename

    def __gt__(self, other: "SOLODirectoryEntry") -> bool:
        return self.filename > other.filename


class SOLOFilesystem(AbstractFilesystem):
    """
    SOLO Filesystem

    P. Brinch Hansen,
    The Solo operating system: a Concurrent Pascal progran Software—Practice and Experience, 1976

    http://brinch-hansen.net/papers/1976b.pdf
    """

    catalog_length: int

    def __init__(self, file: "AbstractFile"):
        self.f = file
        # Get catalog length
        buffer = self.read_block(CAT_ADDR)
        self.catalog_length = struct.unpack_from("<H", buffer, 0)[0]
        assert self.catalog_length == 15

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        return self.f.read_block(block_number, number_of_blocks)

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        self.f.write_block(buffer, block_number, number_of_blocks)

    def read_page_map(self, block_number: int) -> List[int]:
        """
        Read a page map
        """
        buffer = self.read_block(block_number)
        words = struct.unpack_from("<256H", buffer, 0)
        length = words[0]
        return list(words[1 : length + 1])

    def read_catalog(self) -> Iterator["SOLODirectoryEntry"]:
        """
        Read the catalog
        """
        for page_num, block_number in enumerate(self.read_page_map(CAT_ADDR), start=1):
            yield from self.read_cat_page(page_num, block_number)

    def read_cat_page(self, page_num: int, block_number: int) -> Iterator["SOLODirectoryEntry"]:
        """
        Read a catalog page
        """
        buffer = self.read_block(block_number)
        for pos in range(0, BLOCK_SIZE, ENTRY_LENGTH):
            yield SOLODirectoryEntry.read(self, buffer, pos, page_num)

    def filter_entries_list(
        self,
        pattern: Optional[str],
        include_all: bool = False,
        wildcard: bool = True,
    ) -> Iterator["SOLODirectoryEntry"]:
        if pattern:
            pattern = solo_canonical_filename(pattern)
        for entry in self.read_catalog():
            if filename_match(entry.basename, pattern, wildcard) and (include_all or not entry.is_empty):
                yield entry

    @property
    def entries_list(self) -> Iterator["SOLODirectoryEntry"]:
        yield from self.read_catalog()

    def get_file_entry(self, fullname: str) -> Optional[SOLODirectoryEntry]:
        """
        Get the directory entry for a file
        """
        fullname = solo_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        # Lookup file by key hash
        hash_key = filename_hash(fullname, self.catalog_length)
        page_num = (hash_key - 1) // CAT_PAGE_LENGTH + 1
        pages = self.read_page_map(CAT_ADDR)
        block_number = pages[page_num - 1]
        for entry in self.read_cat_page(page_num, block_number):
            if entry.basename == fullname:
                return entry
        # Fallback
        return next(self.filter_entries_list(fullname, wildcard=False), None)

    def open_file(self, fullname: str) -> SOLOFile:
        """
        Open a file
        """
        entry = self.get_file_entry(fullname)
        if not entry:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return SOLOFile(entry)

    def read_bytes(self, fullname: str) -> bytes:
        """
        Get the content of a file
        """
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
        """
        Write content to a file
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def create_file(
        self,
        fullname: str,
        length: int,  # length in blocks
        creation_date: Optional[date] = None,  # optional creation date
        contiguous: Optional[bool] = None,
    ) -> Optional[SOLODirectoryEntry]:
        """
        Create a new file with a given length in number of blocks
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def isdir(self, fullname: str) -> bool:
        return False

    def exists(self, fullname: str) -> bool:
        """
        Check if the given path exists
        """
        entry = self.get_file_entry(fullname)
        return entry is not None

    def dir(self, volume_id: str, pattern: Optional[str], options: Dict[str, bool]) -> None:
        files = 0
        blocks = 0
        if not options.get("brief"):
            sys.stdout.write("SOLO SYSTEM FILES\n\n")
        for x in sorted(self.filter_entries_list(pattern, include_all=False, wildcard=True)):
            if options.get("brief"):
                sys.stdout.write(f"{x.filename}\n")
            else:
                sys.stdout.write(
                    f"{x.filename:<12} {x.kind:<12} {'PROTECTED' if x.protected else 'UNPROTECTED':<12} {x.length:>6} PAGES\n"
                )
            blocks += x.length
            files += 1
        if options.get("brief"):
            return
        sys.stdout.write(f"{files:>5} ENTRIES\n{blocks:>5} PAGES\n")

    def dump(self, name_or_block: str) -> None:
        if name_or_block.isnumeric():
            data = self.read_block(int(name_or_block))
        else:
            data = self.read_bytes(name_or_block)
        hex_dump(data)

    def examine(self, name_or_block: Optional[str]) -> None:
        if name_or_block:
            self.dump(name_or_block)
        else:
            for entry in self.read_catalog():
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
