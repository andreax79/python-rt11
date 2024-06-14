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
EM = b"\x19"  # End of Medium

DISK_SIZE = 4800  # Disk size in blocks
ID_LENGTH = 12  # Filename length
ENTRY_FORMAT = "<12sHHH10sHH"  # Catalog entry format
ENTRY_LENGTH = 32  # Catalog entry length
CAT_PAGE_LENGTH = BLOCK_SIZE // ENTRY_LENGTH  # Entries in a catalog page
BLOCKS_PER_CYLINDER = 24  # Blocks per cylinder
CYLINDERS_PER_GROUP = 5  # Cylinders per group
GROUP_LENGTH = BLOCKS_PER_CYLINDER * CYLINDERS_PER_GROUP  # Blocks per group (120)
SEGMENT_LENGTH = 64  # Segment length
FREE_LIST_LENGTH = 2  # Free blocks bitmap length in blocks
# Bitset 0 - 119 => 120 bits => 15 chars, 7.5 words (8 words, 1 unused byte)
FREE_PAGE_GROUP_LENGTH = GROUP_LENGTH // 8  # 15 bytes
FREE_PAGE_GROUP_PAD = 1  # 1byte padding
FREE_PAGE_LENGTH = 31  # number of groups in a free page
FREE_PAGE_MISC_FORMAT = "<2H12x"  # Misc entry format
FREE_PAGE_MISC_LENGTH = 16
MAX_FILE_SIZE = 255  # Max file length in blocks

SOLO_OS_ADDR = 24  # Solo OS segment block number
OTHER_OS_ADDR = SOLO_OS_ADDR + SEGMENT_LENGTH  # 88 Other OS segment block number
FREE_LIST_ADDR = OTHER_OS_ADDR + SEGMENT_LENGTH  # 152 Free blocks bitmap block number
CAT_ADDR = FREE_LIST_ADDR + FREE_LIST_LENGTH  # 154 - Catalog block number

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
    return "".join(filter(str.isalnum, fullname or "")).upper()[:ID_LENGTH]


def solo_to_ascii(data: bytes) -> bytes:
    if not data:
        return data
    data = data.split(EM)[0]
    return data


def ascii_to_solo(data: bytes) -> bytes:
    if not data:
        return data
    if data.endswith(EM):
        data += EM
    return data


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
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.entry.length
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        for i in range(block_number, block_number + number_of_blocks):
            disk_block_number = self.entry.page_map[i]
            t = buffer[i * BLOCK_SIZE : (i + 1) * BLOCK_SIZE]
            if len(t) < BLOCK_SIZE:
                t = t + bytes([0] * (BLOCK_SIZE - len(t)))  # Pad with zeros
            self.entry.fs.write_block(t, disk_block_number)

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
    SOLO Directory Entry

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

    cat_page: "SOLOCatalogPage"
    filename: str = ""  # Filename (12 chars)
    kind_id: int = 0  # File type (scratch, ascii, seqcode and concode)
    page_map_block_number: int = 0  # Page map block number
    protected: bool = False  # Protected against accidental overwriting or deletion
    spare: bytes = b""
    hash_key: int = 0  # Filename hash
    searchlength: int = 0  # Number of files with the same key
    page_map: List[int]  # Map file blocks to disk blocks

    def __init__(self, cat_page: "SOLOCatalogPage"):
        self.cat_page = cat_page

    @classmethod
    def new(
        cls, cat_page: "SOLOCatalogPage", filename: str, kind_id: int, page_map_block_number: int, page_map: List[int]
    ) -> "SOLODirectoryEntry":
        self = SOLODirectoryEntry(cat_page)
        self.filename = filename
        self.kind_id = kind_id
        self.page_map_block_number = page_map_block_number
        self.hash_key = filename_hash(filename, cat_page.fs.catalog_length)
        self.searchlength = 0
        self.page_map = page_map
        return self

    @classmethod
    def read(cls, cat_page: "SOLOCatalogPage", buffer: bytes, position: int) -> "SOLODirectoryEntry":
        self = SOLODirectoryEntry(cat_page)
        (
            file_id,
            self.kind_id,
            self.page_map_block_number,
            raw_protected,
            self.spare,
            self.hash_key,
            self.searchlength,
        ) = struct.unpack_from(ENTRY_FORMAT, buffer, position)
        self.filename = file_id.decode("ascii", errors="ignore").rstrip()
        self.protected = bool(raw_protected)
        if self.is_empty:
            self.page_map = []
        else:
            self.page_map = self.cat_page.fs.read_page_map(self.page_map_block_number)
        return self

    def write(self, buffer: bytearray, position: int) -> None:
        file_id = self.filename.encode("ascii", errors="ignore").ljust(ID_LENGTH)
        raw_protected = 1 if self.protected else 0
        struct.pack_into(
            ENTRY_FORMAT,
            buffer,
            position,
            file_id,
            self.kind_id,
            self.page_map_block_number,
            raw_protected,
            self.spare,
            self.hash_key,
            self.searchlength,
        )

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

    @property
    def length(self) -> int:
        """
        File length in blocks
        """
        return len(self.page_map)

    @property
    def fs(self) -> "SOLOFilesystem":
        return self.cat_page.fs

    def delete(self) -> bool:
        # Update the free block bitmap
        bitmap = SOLOBitmap.read(self.fs)
        for block_number in self.page_map:
            bitmap.set_free(block_number)  # Mark block as free
        bitmap.set_free(self.page_map_block_number)  # Mark block as free
        bitmap.write()
        # Delete entry from the catalog
        old_key = self.hash_key
        self.hash_key = 0
        self.kind_id = EMPTY
        self.protected = False
        self.filename = ""
        self.page_map_block_number = 0
        # Update searchlength for the first entry with the same key (if any)
        for entry in self.cat_page.entries:
            if entry.hash_key == old_key:
                entry.searchlength = max(entry.searchlength - 1, 0)
                break
        self.cat_page.write()
        return True

    def __str__(self) -> str:
        return f"{self.filename:<12}  \
{self.kind:<8}  \
{'PROT' if self.protected else '    '}  \
Key: {self.hash_key:>6} {'('+str(self.searchlength)+')':<6}  \
Length: {self.length:>4}  \
Map: {self.page_map_block_number:>4}  \
Blocks: {[x for x in self.page_map]}"

    def __repr__(self) -> str:
        return str(self)

    def __lt__(self, other: "SOLODirectoryEntry") -> bool:
        return self.filename < other.filename

    def __gt__(self, other: "SOLODirectoryEntry") -> bool:
        return self.filename > other.filename


class SOLOBitmap:

    fs: "SOLOFilesystem"
    bitmaps: List[int]  # 2(blocks) x 30(groups) x 120bit integer

    def __init__(self, fs: "SOLOFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "SOLOFilesystem") -> "SOLOBitmap":
        """
        Read the bitmap blocks from the disk
        """
        self = SOLOBitmap(fs)
        self.bitmaps = []
        # Read the bitmaps from the disk
        for block_number in range(FREE_LIST_ADDR, FREE_LIST_ADDR + FREE_LIST_LENGTH):
            t = self.fs.read_block(block_number)
            # print(block_number, t)
            if not t:
                raise OSError(errno.EIO, f"Failed to read block {block_number}")
            for i in range(0, FREE_PAGE_LENGTH):
                position = i * (FREE_PAGE_GROUP_LENGTH + FREE_PAGE_GROUP_PAD)
                bitmap = int.from_bytes(t[position : position + FREE_PAGE_GROUP_LENGTH], byteorder="big", signed=False)
                self.bitmaps.append(bitmap)
            # TODO misc (0=> # of free blocks, 1=> first free block?)
            # misc = struct.unpack_from(FREE_PAGE_MISC_FORMAT, t, BLOCK_SIZE - FREE_PAGE_MISC_LENGTH)
            # print(">>>", block_number, misc, self.is_free(misc[1]))
        return self

    def write(self) -> None:
        """
        Write the bitmap blocks to the disk
        """
        free = self.free()
        first_free_block = self.find_first_free()
        for block_number in range(FREE_LIST_ADDR, FREE_LIST_ADDR + FREE_LIST_LENGTH):
            t = bytearray(BLOCK_SIZE)
            for i in range(0, FREE_PAGE_LENGTH):
                position = i * (FREE_PAGE_GROUP_LENGTH + FREE_PAGE_GROUP_PAD)
                bitmap = self.bitmaps.pop(0)
                t[position : position + FREE_PAGE_GROUP_LENGTH] = bitmap.to_bytes(
                    FREE_PAGE_GROUP_LENGTH, byteorder="big"
                )
            # Update first free block and number of free blocks
            struct.pack_into(FREE_PAGE_MISC_FORMAT, t, BLOCK_SIZE - FREE_PAGE_MISC_LENGTH, free, first_free_block)
            self.fs.write_block(bytes(t), block_number)

    @property
    def total_bits(self) -> int:
        """
        Return the bitmap length in bit
        """
        return DISK_SIZE

    def is_free(self, block_number: int) -> bool:
        """
        Check if a block is free
        """
        int_index = block_number // GROUP_LENGTH
        bit_position = block_number % GROUP_LENGTH
        bit_value = self.bitmaps[int_index]
        return (bit_value & (1 << bit_position)) != 0

    def set_free(self, block_number: int) -> None:
        """
        Mark a block as free
        """
        int_index = block_number // GROUP_LENGTH
        bit_position = block_number % GROUP_LENGTH
        self.bitmaps[int_index] |= 1 << bit_position

    def set_used(self, block_number: int) -> None:
        """
        Allocate a block
        """
        int_index = block_number // GROUP_LENGTH
        bit_position = block_number % GROUP_LENGTH
        self.bitmaps[int_index] &= ~(1 << bit_position)

    def allocate(self, size: int) -> List[int]:
        """
        Allocate blocks
        """
        if size > MAX_FILE_SIZE:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        blocks = []
        for block in range(0, self.total_bits):
            if self.is_free(block):
                self.set_used(block)  # Mark as used
                blocks.append(block)
            if len(blocks) == size:
                break
        if len(blocks) < size:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        return blocks

    def find_first_free(self) -> int:
        """
        Get the first free block (does not allocate the block)
        """
        for block in range(0, self.total_bits):
            if self.is_free(block):
                return block
        return self.total_bits

    def used(self) -> int:
        """
        Count the number of used blocks
        """
        return self.total_bits - self.free()

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        free = 0
        for i, bitmap in enumerate(self.bitmaps):
            if i * GROUP_LENGTH < DISK_SIZE:  # Consider only the blocks < DISK_SIZE
                free += bitmap.bit_count()
        return free


class SOLOCatalogPage:
    """
    SOLO Catalog page

        +-------------------------------------+
     0  |          Directory Entry 1          |
        +-------------------------------------+
        |                 ...                 |
        +-------------------------------------+
    480 |          Directory Entry 16         |
        +-------------------------------------+
    """

    fs: "SOLOFilesystem"
    block_number: int  # Catalog block number
    entries: List["SOLODirectoryEntry"]  # Directory entries

    def __init__(self, fs: "SOLOFilesystem", block_number: int):
        self.fs = fs
        self.block_number = block_number

    @classmethod
    def read(cls, fs: "SOLOFilesystem", block_number: int) -> "SOLOCatalogPage":
        """
        Read a catalog page from the disk
        """
        self = SOLOCatalogPage(fs, block_number)
        buffer = self.fs.read_block(block_number)
        self.entries = [SOLODirectoryEntry.read(self, buffer, pos) for pos in range(0, BLOCK_SIZE, ENTRY_LENGTH)]
        return self

    def write(self) -> None:
        """
        Write the catalog page on the disk
        """
        buffer = bytearray(BLOCK_SIZE)
        for i, entry in enumerate(self.entries):
            pos = i * ENTRY_LENGTH
            entry.write(buffer, pos)
        self.fs.write_block(buffer, self.block_number)

    def create_entry(
        self,
        filename: str,
        kind_id: int,
        page_map_block_number: int,
        page_map: List[int],
        search_key: Optional[int] = None,
    ) -> Optional["SOLODirectoryEntry"]:
        """
        Create a new entry in this catalog page.
        If search_key is not None, put the new entry in the first position after the search_key
        """
        found = search_key is None
        for i, entry in enumerate(self.entries):
            if not found and (search_key is not None and entry.hash_key < search_key):
                found = True
            if found and entry.is_empty:
                self.entries[i] = SOLODirectoryEntry.new(self, filename, kind_id, page_map_block_number, page_map)
                return self.entries[i]
        return None

    def __str__(self) -> str:
        return f"Catalog#{self.block_number}"

    def __repr__(self) -> str:
        return str(self)


class SOLOFilesystem(AbstractFilesystem):
    """
    SOLO Filesystem

    P. Brinch Hansen,
    The Solo operating system: a Concurrent Pascal progran Software—Practice and Experience, 1976

    http://brinch-hansen.net/papers/1976b.pdf

    Block
          +-------------------------------------+
       0  |          Kernel (24 block)          |
          +-------------------------------------+
      24  |         Solo OS (64 blocks)         |
          +-------------------------------------+
      88  |        Other OS (64 blocks)         |
          +-------------------------------------+
     152  |    Free block bitmap (2 blocks)     |
          +-------------------------------------+
     154  |   Catalog pages index (1 blocks)    |
          +-------------------------------------+
          |           Catalog pages             |
          |         Directory entries           |
          |               Files                 |
          +-------------------------------------+
    """

    catalog_length: int

    def __init__(self, file: "AbstractFile"):
        self.f = file
        # Get catalog length
        buffer = self.read_block(CAT_ADDR)
        self.catalog_length = struct.unpack_from("<H", buffer, 0)[0]
        # assert self.catalog_length == 15

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

    def write_page_map(self, page_map: List[int], block_number: int) -> None:
        """
        Write a page map
        """
        if len(page_map) > MAX_FILE_SIZE:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        length = len(page_map)
        words = [length] + page_map
        if len(words) < 256:
            words += [0] * (256 - len(words))
        assert len(words) == 256
        buffer = struct.pack("<256H", *words)
        self.write_block(buffer, block_number)

    def read_catalog(self) -> Iterator["SOLODirectoryEntry"]:
        """
        Read the catalog
        """
        for block_number in self.read_page_map(CAT_ADDR):
            catalog_page = SOLOCatalogPage.read(self, block_number)
            for entry in catalog_page.entries:
                yield entry

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

    def get_first_file_entry_for_hash(self, hash_key: int) -> Optional[SOLODirectoryEntry]:
        page_num = (hash_key - 1) // CAT_PAGE_LENGTH + 1
        pages = self.read_page_map(CAT_ADDR)
        block_number = pages[page_num - 1]
        catalog_page = SOLOCatalogPage.read(self, block_number)
        for entry in catalog_page.entries:
            if entry.hash_key == hash_key:
                return entry
        return None

    def get_file_entry(self, fullname: str) -> Optional[SOLODirectoryEntry]:
        """
        Get the directory entry for a file
        """
        fullname = solo_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        # Lookup file by key hash
        hash_key = filename_hash(fullname, self.catalog_length)
        entry = self.get_first_file_entry_for_hash(hash_key)
        if entry is not None:
            # Check if the entry match by filename
            if entry.basename == fullname:
                return entry
            # Check the other entries in the same catalog page
            for entry in entry.cat_page.entries:
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

    def read_bytes(self, fullname: str, raw: bool = False) -> bytes:
        """
        Get the content of a file
        """
        f = self.open_file(fullname)
        try:
            data = f.read_block(0, READ_FILE_FULL)
        finally:
            f.close()
        if (not raw) and data and f.entry.kind_id == ASCII:
            data = solo_to_ascii(data)
        return data

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: Optional[date] = None,
        contiguous: Optional[bool] = None,
        kind: int = ASCII,
        protected: bool = False,
    ) -> None:
        """
        Write content to a file
        """
        length = int(math.ceil(len(content) * 1.0 / BLOCK_SIZE))
        if length > MAX_FILE_SIZE:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        if kind == ASCII:
            content = ascii_to_solo(content)

        entry = self.create_file(fullname=fullname, length=length, kind=kind, protected=protected)
        if entry is not None:
            f = SOLOFile(entry)
            try:
                f.write_block(content, 0, entry.length)
            finally:
                f.close()

    def create_file(
        self,
        fullname: str,
        length: int,  # length in blocks
        creation_date: Optional[date] = None,  # optional creation date
        contiguous: Optional[bool] = None,
        kind: int = ASCII,
        protected: bool = False,
    ) -> Optional[SOLODirectoryEntry]:
        """
        Create a new file with a given length in number of blocks
        """
        if length > MAX_FILE_SIZE:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        # Delete the existing file
        fullname = solo_canonical_filename(fullname)
        old_entry = self.get_file_entry(fullname)
        if old_entry is not None:
            old_entry.delete()
        # Allocate the space for the page map and the the file
        bitmap = SOLOBitmap.read(self)
        blocks = bitmap.allocate(length + 1)
        page_map_block_number = blocks[0]
        file_blocks = blocks[1:]
        self.write_page_map(file_blocks, page_map_block_number)
        # Lookup catalog entry by key hash
        hash_key = filename_hash(fullname, self.catalog_length)
        existing_entry = self.get_first_file_entry_for_hash(hash_key)
        # Create the catalog entry
        if existing_entry:
            catalog_page = existing_entry.cat_page
        else:
            page_num = (hash_key - 1) // CAT_PAGE_LENGTH + 1  # 1 >= page_num >= CAT_PAGES
            cat_pages = self.read_page_map(CAT_ADDR)
            block_number = cat_pages[page_num - 1]
            catalog_page = SOLOCatalogPage.read(self, block_number)
        # print(f"{hash_key=} {page_num+1=} {block_number=} {catalog_page=}")
        new_entry = catalog_page.create_entry(fullname, kind, page_map_block_number, file_blocks, search_key=hash_key)
        if new_entry is None:
            for block_number in self.read_page_map(CAT_ADDR):
                catalog_page = SOLOCatalogPage.read(self, block_number)
                new_entry = catalog_page.create_entry(
                    fullname,
                    kind,
                    page_map_block_number,
                    file_blocks,
                )
                if new_entry is not None:
                    break
        if new_entry is None:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        # Increment the counter of files with the same key
        if existing_entry:
            existing_entry.searchlength += 1
            existing_entry.cat_page.write()
        catalog_page.write()
        # Write bitmap
        bitmap.write()
        return new_entry

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
            data = self.read_bytes(name_or_block, raw=True)
        hex_dump(data)

    def examine(self, name_or_block: Optional[str]) -> None:
        if not name_or_block:
            for page_num, block_number in enumerate(self.read_page_map(CAT_ADDR), start=1):
                catalog_page = SOLOCatalogPage.read(self, block_number)
                for entry in catalog_page.entries:
                    sys.stdout.write(f"{page_num:>2}# {entry}\n")
        elif name_or_block.strip().lower() == "/free":
            bitmap = SOLOBitmap.read(self)
            for i in range(0, bitmap.total_bits):
                sys.stdout.write(f"{i:>4} {'[ ]' if bitmap.is_free(i) else '[X]'}  ")
                if i % 16 == 15:
                    sys.stdout.write("\n")
        else:
            self.dump(name_or_block)

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
