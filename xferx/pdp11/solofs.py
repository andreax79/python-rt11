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
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY FILE_TYPES, EXPRESS OR
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
from ..block import BlockDevice
from ..commons import ASCII, BLOCK_SIZE, IMAGE, READ_FILE_FULL, filename_match

__all__ = [
    "SOLOFile",
    "SOLODirectoryEntry",
    "SOLOFilesystem",
    "solo_canonical_filename",
]

EM = b"\x19"  # End of Medium

DISK_SIZE = 4800  # Disk size in blocks
ID_LENGTH = 12  # Filename length
ENTRY_FORMAT = "<12sHHH10sHH"  # Catalog entry format
ENTRY_LENGTH = 32  # Catalog entry length
CAT_PAGE_LENGTH = BLOCK_SIZE // ENTRY_LENGTH  # Entries in a catalog page
BLOCKS_PER_CYLINDER = 24  # Blocks per cylinder
CYLINDERS_PER_GROUP = 5  # Cylinders per group
GROUP_LENGTH = BLOCKS_PER_CYLINDER * CYLINDERS_PER_GROUP  # Blocks per group (120)
KERNEL_LENGTH = 24  # Kernel length (in blocks)
SEGMENT_LENGTH = 64  # Segment length (in blocks)
FREE_LIST_LENGTH = 2  # Free blocks bitmap length in blocks
# Bitset 0 - 119 => 120 bits => 15 chars, 7.5 words (8 words, 1 unused byte)
FREE_PAGE_GROUP_LENGTH = GROUP_LENGTH // 8  # 15 bytes
FREE_PAGE_GROUP_PAD = 1  # 1byte padding
FREE_PAGE_LENGTH = 31  # number of groups in a free page
FREE_PAGE_MISC_FORMAT = "<2H12x"  # Misc entry format
FREE_PAGE_MISC_LENGTH = 16
MAX_FILE_SIZE = 255  # Max file length in blocks

KERNEL_ADDR = 0  # Kernel block number
SOLO_OS_ADDR = KERNEL_ADDR + KERNEL_LENGTH  # 24 Solo OS segment block number
OTHER_OS_ADDR = SOLO_OS_ADDR + SEGMENT_LENGTH  # 88 Other OS segment block number
FREE_LIST_ADDR = OTHER_OS_ADDR + SEGMENT_LENGTH  # 152 Free blocks bitmap block number
CAT_ADDR = FREE_LIST_ADDR + FREE_LIST_LENGTH  # 154 - Catalog block number

# Segments are 3 contiguous, fixed-size files
SEGMENTS = {
    KERNEL_ADDR: "@KERNEL",
    SOLO_OS_ADDR: "@SOLO",
    OTHER_OS_ADDR: "@OTHEROS",
}

# File types
FILE_TYPE_SEGMENT = -1  # Segment
FILE_TYPE_EMPTY = 0  #    Empty file
FILE_TYPE_SCRATCH = 1  #  Scratch file
FILE_TYPE_ASCII = 2  #    Ascii file
FILE_TYPE_SEQCODE = 3  #  Sequential Pascal code file
FILE_TYPE_CONCODE = 4  #  Concurrent Pascal code file

FILE_TYPES = {
    FILE_TYPE_EMPTY: "EMPTY",
    FILE_TYPE_SCRATCH: "SCRATCH",
    FILE_TYPE_ASCII: "ASCII",
    FILE_TYPE_SEQCODE: "SEQCODE",
    FILE_TYPE_CONCODE: "CONCODE",
    FILE_TYPE_SEGMENT: "SEGMENT",
}


def get_file_type_id(file_type: t.Optional[str], default: int = FILE_TYPE_ASCII) -> int:
    """
    Get the file type id from a string
    """
    if not file_type:
        return default
    file_type = file_type.upper()
    for file_id, file_str in FILE_TYPES.items():
        if file_str == file_type:
            return file_id
    raise Exception("?KMON-F-Invalid file type specified with option")


def filename_hash(filename: str, catalog_length: int) -> int:
    """
    Calculate the hash for a given file name
    """
    key = 1
    for c in filename[:ID_LENGTH]:
        if c != " ":
            key = key * ord(c.upper()) % (catalog_length * CAT_PAGE_LENGTH) + 1
    return key


def solo_canonical_filename(fullname: t.Optional[str], wildcard: bool = False, segment: bool = False) -> str:
    """
    Generate the canonical SOLO name
    """

    def filter_fn(s: str) -> bool:
        if wildcard and s == "*":
            return True
        return str.isalnum(s)

    if segment and fullname and fullname.startswith("@"):
        return "@" + "".join(filter(filter_fn, fullname or "")).upper()[:ID_LENGTH]
    else:
        return "".join(filter(filter_fn, fullname or "")).upper()[:ID_LENGTH]


def solo_to_ascii(data: bytes) -> bytes:
    if not data:
        return data
    if not EM in data:
        data = data.rstrip(b"\0")
    else:
        data = data.split(EM)[0]
    return data


def ascii_to_solo(data: bytes) -> bytes:
    if not data:
        return data
    if not data.endswith(EM):
        data += EM
    return data


class SOLOFile(AbstractFile):
    entry: "SOLODirectoryEntry"
    file_mode: str
    closed: bool

    def __init__(self, entry: "SOLODirectoryEntry", file_mode: t.Optional[str] = None) -> None:
        self.entry = entry
        if file_mode:
            self.file_mode = file_mode
        else:
            self.file_mode = ASCII if self.entry.file_type_id == FILE_TYPE_ASCII else IMAGE
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
        if data and self.file_mode == ASCII:
            return solo_to_ascii(bytes(data))
        else:
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


class SOLOSegment(AbstractFile):
    """
    Segments are 3 pre-defined, contiguous, fixed-size files

             start    end      size
             block    block
    @KERNEL      0 => 23        24
    @SOLO       24 => 87        64
    @OTHEROS    88 => 152       64
    """

    entry: "SOLOSegmentDirectoryEntry"
    closed: bool

    def __init__(self, entry: "SOLOSegmentDirectoryEntry"):
        self.closed = False
        self.entry = entry

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the segment
        """
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = self.entry.length
        if self.closed or block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        if block_number + number_of_blocks > self.entry.size:
            number_of_blocks = self.entry.size - block_number
        return self.entry.fs.read_block(self.entry.segment_addr + block_number, number_of_blocks)

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the segment
        """
        if self.closed or block_number < 0 or number_of_blocks < 0 or block_number + number_of_blocks > self.entry.size:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        self.entry.fs.write_block(buffer, self.entry.segment_addr + block_number, number_of_blocks)

    def get_size(self) -> int:
        """
        Get segment size in bytes
        """
        return self.entry.size * BLOCK_SIZE

    def get_block_size(self) -> int:
        """
        Get segment block size in bytes
        """
        return BLOCK_SIZE

    def close(self) -> None:
        """
        Close the file
        """
        self.closed = True


class SOLOAbstractSortableDirectoryEntry(AbstractDirectoryEntry):
    filename: str = ""

    @property
    def fullname(self) -> str:
        return self.filename

    @property
    def basename(self) -> str:
        return self.filename

    def __lt__(self, other: "SOLOAbstractSortableDirectoryEntry") -> bool:
        return self.filename < other.filename

    def __gt__(self, other: "SOLOAbstractSortableDirectoryEntry") -> bool:
        return self.filename > other.filename


class SOLODirectoryEntry(SOLOAbstractSortableDirectoryEntry):
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
    file_type_id: int = 0  # File type (scratch, ascii, seqcode and concode)
    page_map_block_number: int = 0  # Page map block number
    protected: bool = False  # Protected against accidental overwriting or deletion
    spare: bytes = b""
    hash_key: int = 0  # Filename hash
    searchlength: int = 0  # Number of files with the same key
    page_map: t.List[int]  # Map file blocks to disk blocks

    def __init__(self, cat_page: "SOLOCatalogPage"):
        self.cat_page = cat_page

    @classmethod
    def new(
        cls,
        cat_page: "SOLOCatalogPage",
        filename: str,
        file_type_id: int,
        page_map_block_number: int,
        page_map: t.List[int],
        protected: bool,
        searchlength: int,
    ) -> "SOLODirectoryEntry":
        self = SOLODirectoryEntry(cat_page)
        self.filename = filename
        self.file_type_id = file_type_id
        self.page_map_block_number = page_map_block_number
        self.protected = protected
        self.hash_key = filename_hash(filename, cat_page.fs.catalog_length)
        self.searchlength = searchlength
        self.page_map = page_map
        return self

    @classmethod
    def read(cls, cat_page: "SOLOCatalogPage", buffer: bytes, position: int) -> "SOLODirectoryEntry":
        self = SOLODirectoryEntry(cat_page)
        (
            file_id,
            self.file_type_id,
            self.page_map_block_number,
            raw_protected,
            self.spare,
            self.hash_key,
            self.searchlength,
        ) = struct.unpack_from(ENTRY_FORMAT, buffer, position)
        self.filename = file_id.decode("ascii", errors="ignore").rstrip(" \x00")
        self.protected = bool(raw_protected)
        if self.is_empty:
            self.page_map = []
        else:
            self.page_map = self.cat_page.fs.read_page_map(self.page_map_block_number)
        return self

    def write_buffer(self, buffer: bytearray, position: int) -> None:
        file_id = self.filename.encode("ascii", errors="ignore").ljust(ID_LENGTH)
        raw_protected = 1 if self.protected else 0
        struct.pack_into(
            ENTRY_FORMAT,
            buffer,
            position,
            file_id,
            self.file_type_id,
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
    def file_type(self) -> str:
        return FILE_TYPES.get(self.file_type_id, "")

    @property
    def length(self) -> int:
        """
        File length in blocks
        """
        return len(self.page_map)

    def get_length(self) -> int:
        """
        File length in blocks
        """
        return len(self.page_map)

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.length * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

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
        self.file_type_id = FILE_TYPE_EMPTY
        self.protected = False
        self.filename = ""
        self.page_map_block_number = 0
        self.cat_page.write()
        # Decrement the counter of files with the same key
        self.fs.update_searchlength(old_key, -1)
        return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        raise OSError(errno.EINVAL, "Invalid operation on SOLO filesystem")

    def open(self, file_mode: t.Optional[str] = None) -> SOLOFile:
        """
        Open a file
        """
        return SOLOFile(self, file_mode)

    def __str__(self) -> str:
        return f"{self.filename:<12}  \
{self.file_type:<8}  \
{'PROT' if self.protected else '    '}  \
Key: {self.hash_key:>6} {'('+str(self.searchlength)+')':<6}  \
Length: {self.length:>4}  \
Map: {self.page_map_block_number:>4}  \
Blocks: {[x for x in self.page_map]}"

    def __repr__(self) -> str:
        return str(self)


class SOLOSegmentDirectoryEntry(SOLOAbstractSortableDirectoryEntry):
    fs: "SOLOFilesystem"
    filename: str = ""
    segment_addr: int = 0
    file_type_id: int = FILE_TYPE_SEGMENT
    protected: bool = True
    is_empty: bool = False
    file_type: str = "SEGMENT"
    creation_date: t.Optional[date] = None

    def __init__(self, fs: "SOLOFilesystem", filename: str):
        self.fs = fs
        self.filename = filename.upper()
        self.segment_addr = -1
        for addr, segment_name in SEGMENTS.items():
            if segment_name == self.filename:
                self.segment_addr = addr
                break
        if self.segment_addr == -1:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), filename)
        if self.segment_addr == KERNEL_ADDR:
            self.size = KERNEL_LENGTH
        else:
            self.size = SEGMENT_LENGTH

    @property
    def length(self) -> int:
        """
        Segment length in blocks
        """
        if self.segment_addr == KERNEL_ADDR:
            return KERNEL_LENGTH
        else:
            return SEGMENT_LENGTH

    def get_length(self) -> int:
        """
        File length in blocks
        """
        if self.segment_addr == KERNEL_ADDR:
            return KERNEL_LENGTH
        else:
            return SEGMENT_LENGTH

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.length * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    def delete(self) -> bool:
        return False

    def write(self) -> bool:
        """
        Write the directory entry
        """
        raise OSError(errno.EINVAL, "Invalid operation on segment")

    def open(self, file_mode: t.Optional[str] = None) -> SOLOSegment:
        """
        Open a segment
        """
        return SOLOSegment(self)

    def __str__(self) -> str:
        return f"{self.filename:<12}  {self.file_type:<8}  Length: {self.length:>4}"

    def __repr__(self) -> str:
        return str(self)


class SOLOBitmap:

    fs: "SOLOFilesystem"
    bitmaps: t.List[int]  # 2(blocks) x 30(groups) x 120bit integer

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
        bitmaps = list(self.bitmaps)
        for block_number in range(FREE_LIST_ADDR, FREE_LIST_ADDR + FREE_LIST_LENGTH):
            t = bytearray(BLOCK_SIZE)
            for i in range(0, FREE_PAGE_LENGTH):
                position = i * (FREE_PAGE_GROUP_LENGTH + FREE_PAGE_GROUP_PAD)
                bitmap = bitmaps.pop(0)
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

    def allocate(self, size: int) -> t.List[int]:
        """
        Allocate blocks
        """
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
    entries: t.List["SOLODirectoryEntry"]  # Directory entries

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
            entry.write_buffer(buffer, pos)
        self.fs.write_block(buffer, self.block_number)

    def create_entry(
        self,
        filename: str,
        file_type: t.Optional[str],
        page_map_block_number: int,
        page_map: t.List[int],
        search_key: t.Optional[int] = None,
        protected: bool = False,
    ) -> t.Optional["SOLODirectoryEntry"]:
        """
        Create a new entry in this catalog page.
        If search_key is not None, put the new entry in the first position after the search_key
        """
        file_type_id = get_file_type_id(file_type)
        if file_type_id == FILE_TYPE_EMPTY or file_type_id == FILE_TYPE_SEGMENT:
            raise Exception("?KMON-F-Invalid file type specified with option")
        if search_key is not None:
            pos = (search_key - 1) % CAT_PAGE_LENGTH
            for i, entry in enumerate(self.entries[pos:], start=pos):
                if entry.is_empty:
                    searchlength = self.entries[i].searchlength
                    self.entries[i] = SOLODirectoryEntry.new(
                        self, filename, file_type_id, page_map_block_number, page_map, protected, searchlength
                    )
                    return self.entries[i]
        else:
            for i, entry in enumerate(self.entries):
                if entry.is_empty:
                    searchlength = self.entries[i].searchlength
                    self.entries[i] = SOLODirectoryEntry.new(
                        self, filename, file_type_id, page_map_block_number, page_map, protected, searchlength
                    )
                    return self.entries[i]
        return None

    def __str__(self) -> str:
        return f"Catalog#{self.block_number}"

    def __repr__(self) -> str:
        return str(self)


class SOLOFilesystem(AbstractFilesystem, BlockDevice):
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

    fs_name = "solo"
    fs_description = "PDP-11 SOLO"

    catalog_length: int

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        # Get catalog length
        buffer = self.read_block(CAT_ADDR)
        self.catalog_length = struct.unpack_from("<H", buffer, 0)[0]
        if self.catalog_length != 15:
            raise OSError(errno.EIO, "Invalid catalog length")
        return self

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

    def read_page_map(self, block_number: int) -> t.List[int]:
        """
        Read a page map
        """
        buffer = self.read_block(block_number)
        words = struct.unpack_from("<256H", buffer, 0)
        length = words[0]
        return list(words[1 : length + 1])

    def write_page_map(self, page_map: t.List[int], block_number: int) -> None:
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

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = True,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator[t.Union["SOLODirectoryEntry", "SOLOSegmentDirectoryEntry"]]:
        file_type_id: t.Optional[int] = None
        if pattern:
            if ";" in pattern:
                pattern, file_type = pattern.split(";", 1)
                file_type_id = get_file_type_id(file_type)
            pattern = solo_canonical_filename(pattern, segment=True, wildcard=True)
        if include_all or file_type_id == FILE_TYPE_SEGMENT:
            for segment in SEGMENTS.values():
                if filename_match(segment, pattern, wildcard):
                    if file_type_id is None or file_type_id == FILE_TYPE_SEGMENT:
                        yield SOLOSegmentDirectoryEntry(self, segment)

        for entry in self.entries_list:
            if filename_match(entry.basename, pattern, wildcard) and not entry.is_empty:
                if file_type_id is None or file_type_id == entry.file_type_id:
                    yield entry

    @property
    def entries_list(self) -> t.Iterator["SOLODirectoryEntry"]:
        """
        Read the catalog
        """
        for block_number in self.read_page_map(CAT_ADDR):
            catalog_page = SOLOCatalogPage.read(self, block_number)
            for entry in catalog_page.entries:
                yield entry

    def get_searchlength(self, hash_key: int) -> int:
        page_num = (hash_key - 1) // CAT_PAGE_LENGTH + 1
        pages = self.read_page_map(CAT_ADDR)
        block_number = pages[page_num - 1]
        catalog_page = SOLOCatalogPage.read(self, block_number)
        pos = (hash_key - 1) % CAT_PAGE_LENGTH
        entry = catalog_page.entries[pos]
        return entry.searchlength

    def update_searchlength(self, hash_key: int, delta: int) -> None:
        page_num = (hash_key - 1) // CAT_PAGE_LENGTH + 1
        pages = self.read_page_map(CAT_ADDR)
        block_number = pages[page_num - 1]
        catalog_page = SOLOCatalogPage.read(self, block_number)
        pos = (hash_key - 1) % CAT_PAGE_LENGTH
        entry = catalog_page.entries[pos]
        entry.searchlength += delta
        if entry.searchlength < 0:
            entry.searchlength = 0
        catalog_page.write()

    def get_first_file_entry_for_hash(self, hash_key: int) -> t.Optional[SOLODirectoryEntry]:
        page_num = (hash_key - 1) // CAT_PAGE_LENGTH + 1
        pages = self.read_page_map(CAT_ADDR)
        block_number = pages[page_num - 1]
        catalog_page = SOLOCatalogPage.read(self, block_number)
        for entry in catalog_page.entries:
            if entry.hash_key == hash_key:
                return entry
        return None

    def get_file_entry(self, fullname: str) -> t.Union[SOLODirectoryEntry, SOLOSegmentDirectoryEntry]:
        """
        Get the directory entry for a file
        """
        fullname = solo_canonical_filename(fullname, segment=True)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        if fullname.startswith("@"):
            return SOLOSegmentDirectoryEntry(self, fullname)
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
        protected: bool = False,
    ) -> None:
        """
        Write content to a file/segment
        """
        number_of_blocks = int(math.ceil(len(content) * 1.0 / BLOCK_SIZE))
        if number_of_blocks > MAX_FILE_SIZE:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        if get_file_type_id(file_type) == FILE_TYPE_ASCII:
            content = ascii_to_solo(content)

        entry = self.create_file(
            fullname=fullname, number_of_blocks=number_of_blocks, file_type=file_type, protected=protected
        )
        if isinstance(entry, SOLOSegmentDirectoryEntry):
            f: t.Union[SOLOSegment, SOLOFile] = SOLOSegment(entry)
        else:
            f = SOLOFile(entry)
        try:
            f.write_block(content, 0, entry.length)
        finally:
            f.close()

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,
        protected: bool = False,
    ) -> t.Union[SOLODirectoryEntry, SOLOSegmentDirectoryEntry]:
        """
        Create a new file with a given length in number of blocks
        """
        if number_of_blocks > MAX_FILE_SIZE:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        # Delete the existing file
        fullname = solo_canonical_filename(fullname, segment=True)
        try:
            old_entry = self.get_file_entry(fullname)
            if isinstance(old_entry, SOLOSegmentDirectoryEntry):
                return old_entry
            old_entry.delete()
        except FileNotFoundError:
            pass
        # Allocate the space for the page map and the the file
        bitmap = SOLOBitmap.read(self)
        blocks = bitmap.allocate(number_of_blocks + 1)
        page_map_block_number = blocks[0]
        file_blocks = blocks[1:]
        self.write_page_map(file_blocks, page_map_block_number)
        # Lookup catalog entry by key hash
        fullname = solo_canonical_filename(fullname, segment=False)
        hash_key = filename_hash(fullname, self.catalog_length)
        # Create the catalog entry
        page_num = (hash_key - 1) // CAT_PAGE_LENGTH + 1  # 1 >= page_num >= CAT_PAGE_LENGTH
        cat_pages = self.read_page_map(CAT_ADDR)
        block_number = cat_pages[page_num - 1]
        catalog_page = SOLOCatalogPage.read(self, block_number)
        # print(f"{hash_key=} {page_num+1=} {block_number=} {catalog_page=}")
        new_entry = catalog_page.create_entry(
            fullname,
            file_type,
            page_map_block_number,
            file_blocks,
            search_key=hash_key,
            protected=protected,
        )
        if new_entry is None:
            for block_number in self.read_page_map(CAT_ADDR):
                catalog_page = SOLOCatalogPage.read(self, block_number)
                new_entry = catalog_page.create_entry(
                    fullname,
                    file_type,
                    page_map_block_number,
                    file_blocks,
                    protected=protected,
                )
                if new_entry is not None:
                    break
        if new_entry is None:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        catalog_page.write()
        # Write bitmap
        bitmap.write()
        # Increment the counter of files with the same key
        self.update_searchlength(new_entry.hash_key, delta=+1)
        return new_entry

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        files = 0
        blocks = 0
        if not options.get("brief"):
            sys.stdout.write("SOLO SYSTEM FILES\n\n")
        for x in sorted(self.filter_entries_list(pattern, include_all=bool(options.get("full")), wildcard=True)):
            if options.get("brief"):
                sys.stdout.write(f"{x.filename}\n")
            else:
                sys.stdout.write(
                    f"{x.filename:<12} {x.file_type:<12} {'PROTECTED' if x.protected else 'UNPROTECTED':<12} {x.length:>6} PAGES\n"
                )
            blocks += x.length
            files += 1
        if options.get("brief"):
            return
        sys.stdout.write(f"{files:>5} ENTRIES\n{blocks:>5} PAGES\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if options.get("bitmap"):
            # Print the bitmap
            bitmap = SOLOBitmap.read(self)
            for i in range(0, bitmap.total_bits):
                sys.stdout.write(f"{i:>4} {'[ ]' if bitmap.is_free(i) else '[X]'}  ")
                if i % 16 == 15:
                    sys.stdout.write("\n")
        elif not arg:
            # Print the catalog
            for segment in SEGMENTS.values():
                segment_entry = SOLOSegmentDirectoryEntry(self, segment)
                sys.stdout.write(f" -  {segment_entry}\n")
            t = 1
            for page_num, block_number in enumerate(self.read_page_map(CAT_ADDR), start=1):
                catalog_page = SOLOCatalogPage.read(self, block_number)
                for entry in catalog_page.entries:
                    sys.stdout.write(f"{t:>3} {page_num:>2}# {entry}\n")
                    t += 1
        else:
            self.dump(arg)

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        # Zero disk
        empty_block = b"\0" * BLOCK_SIZE
        for block_number in range(0, DISK_SIZE):
            self.write_block(empty_block, block_number)
        # Mark the blocks allocated in the bitmap
        bitmap = SOLOBitmap.read(self)
        for i in range(0, CAT_ADDR + 1):
            bitmap.set_used(i)
        for i in range(CAT_ADDR + 1, DISK_SIZE):
            bitmap.set_free(i)
        bitmap.write()
        # Create the catalog
        self.catalog_length = 15
        bitmap = SOLOBitmap.read(self)
        catalog_pages = bitmap.allocate(self.catalog_length)
        bitmap.write()
        self.write_page_map(catalog_pages, CAT_ADDR)
        for block_number in catalog_pages:
            catalog_page = SOLOCatalogPage.read(self, block_number)
            catalog_page.write()
        # Create NEXT
        self.create_file(fullname="NEXT", number_of_blocks=255, file_type="SCRATCH", protected=True)

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
        return list(FILE_TYPES.values())

    def __str__(self) -> str:
        return str(self.f)
