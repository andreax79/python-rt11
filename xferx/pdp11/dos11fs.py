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
import io
import math
import os
import struct
import sys
import typing as t
from datetime import date, timedelta

from ..abstract import XFERXPath, AbstractFile
from ..commons import (
    BLOCK_SIZE,
    DECTAPE,
    READ_FILE_FULL,
    bytes_to_word,
    filename_match,
    pad_words,
)
from ..device.abstract import AbstractDevice
from ..uic import ANY_UIC, DEFAULT_UIC, UIC
from .abstract import AbstractRXBlockFilesystem
from .rad50 import asc_to_rad50_word, rad50_word_to_asc
from .rt11fs import rt11_canonical_filename

__all__ = [
    "DOS11DirectoryEntry",
    "DOS11File",
    "DOS11Filesystem",
    "date_to_dos11",
    "dos11_canonical_filename",
    "dos11_get_file_type_id",
    "dos11_split_fullname",
    "dos11_to_date",
]

MFD1_BLOCK = 1  # Master File Directory block #1
UFD_ENTRIES = 28  # Number of User File Directory entries in a block
LINKED_FILE_BLOCK_SIZE = BLOCK_SIZE - 2  # Linked file block size in bytes (510)
DECTAPE_MFD1_BLOCK = 0o100  # DECtape Master File Directory block #1
DECTAPE_MFD2_BLOCK = 0o101  # DECtape Master File Directory block #2
DECTAPE_UFD1_BLOCK = 0o102  # DECtape User File Directory block #1
DECTAPE_UFD2_BLOCK = 0o103  # DECtape User File Directory block #2
DECTAPE_BITMAP_BLOCK = 0o104  # DECtape bitmap block
DECTAPE_BLOCKS = 576  # Number of blocks on a DECtape
BITMAP_HEADER_SIZE = 4  # Size of the bitmap header in words
MAX_WORDS_PER_BITMAP = 60  # Maximum number of words in a bitmap block
WORDS_PER_BLOCK = 256  # Number of words per block
MFD_ENTRY_SIZE = 4  # MFD entry size in words
UFD_ENTRY_SIZE = 9  # UFD entry size in words
DEFAULT_PROTECTION_CODE = 0o233
DEFAULT_INTERLAVE_FACTOR = 1

# File types
LINKED_FILE_TYPE = 0
CONTIGUOUS_FILE_TYPE = 32768

FILE_TYPES = {
    LINKED_FILE_TYPE: "NOCONTIGUOUS",
    CONTIGUOUS_FILE_TYPE: "CONTIGUOUS",
}


def dos11_get_file_type_id(file_type: t.Optional[str], default: int = LINKED_FILE_TYPE) -> int:
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


def dos11_to_date(val: int) -> t.Optional[date]:
    """
    Translate DOS-11 date to Python date
    """
    if val == 0:
        return None
    val = val & 0o77777  # low 15 bits only
    year = val // 1000 + 1970  # encoded year
    doy = val % 1000  # encoded day of year
    try:
        return date(year, 1, 1) + timedelta(days=doy - 1)
    except:
        return None


def date_to_dos11(val: date) -> int:
    """
    Translate Python date to DOS-11 date
    """
    if val is None:
        return 0
    # Calculate the number of years since 1970
    year = val.year - 1970
    # Calculate the day of the year
    doy = (val - date(val.year, 1, 1)).days + 1
    # Combine into DOS-11 format
    return ((year * 1000) + doy) & 0o77777


def dos11_canonical_filename(fullname: str, wildcard: bool = False) -> str:
    try:
        if "[" in fullname:
            uic: t.Optional[UIC] = UIC.from_str(fullname)
            fullname = fullname.split("]", 1)[1]
        else:
            uic = None
    except Exception:
        uic = None
    if fullname:
        fullname = rt11_canonical_filename(fullname, wildcard=wildcard)
    return f"{uic or ''}{fullname}"


def dos11_split_fullname(
    uic: t.Optional[UIC], fullname: t.Optional[str], wildcard: bool = True
) -> t.Tuple[t.Optional[UIC], t.Optional[str]]:
    if fullname:
        if "[" in fullname:
            try:
                uic = UIC.from_str(fullname)
                fullname = fullname.split("]", 1)[1]
            except Exception:
                return uic, fullname
        if fullname:
            fullname = rt11_canonical_filename(fullname, wildcard=wildcard)
    return uic, fullname


class DOS11Bitmap:
    """
    DOS-11 Bitmap

        +-------------------------------------+
      0 |     Link to next bitmap block       |
        +-------------------------------------+
      2 |         Map block number            |    sequential number of the bitmap block
        +-------------------------------------+
      4 |     Number of words in the map      |    constant for all the bitmap blocks
        +-------------------------------------+
      8 |   Link to the first bitmap block    |
        +-------------------------------------+
     10 |                                     |
        /          Map for blocks             /
    127 |                                     |
        +-------------------------------------+
    128 |                                     |
        /             Not used                /
    255 |                                     |
        +-------------------------------------+

    Disk Operating System Monitor - System Programmers Manual, Pag 138, 203
    http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf
    """

    fs: "DOS11Filesystem"
    blocks: t.List[int]  # Bitmap block numbers
    num_of_words: int  # Number of words in each bitmap block
    bitmaps: t.List[int]

    def __init__(self, fs: "DOS11Filesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "DOS11Filesystem", first_bitmap_block: int) -> "DOS11Bitmap":
        """
        Read the bitmap blocks
        """
        self = DOS11Bitmap(fs)
        self.blocks = []
        self.bitmaps = []
        block_number = first_bitmap_block
        while block_number:
            # Read the bitmaps from the disk
            self.blocks.append(block_number)
            words = self.fs.read_words_block(block_number)
            if not words:
                raise OSError(errno.EIO, f"Failed to read block {block_number}")
            (
                block_number,  #      1 word  Next bitmap block number
                _,  #                 1 word  Map block number
                self.num_of_words,  # 1 word  Number of words of map
                _,  #                 1 word  First bitmap block number
            ) = words[:BITMAP_HEADER_SIZE]
            self.bitmaps.extend(words[BITMAP_HEADER_SIZE : BITMAP_HEADER_SIZE + self.num_of_words])
        return self

    @classmethod
    def new(cls, fs: "DOS11Filesystem", first_bitmap_block: int) -> "DOS11Bitmap":
        """
        Create a new bitmap
        """
        self = DOS11Bitmap(fs)
        number_of_blocks = self.fs.get_size() // BLOCK_SIZE
        total_num_of_words = math.ceil(number_of_blocks / 16)
        if total_num_of_words <= MAX_WORDS_PER_BITMAP:
            self.num_of_words = total_num_of_words  # Number of words in each bitmap block
            bitmap_blocks = 1  # Only one bitmap block needed
        else:
            self.num_of_words = MAX_WORDS_PER_BITMAP  # Maximum number of words in a bitmap block
            bitmap_blocks = math.ceil(total_num_of_words / self.num_of_words)
        self.blocks = list(range(first_bitmap_block, first_bitmap_block + bitmap_blocks))
        self.bitmaps = [0] * self.num_of_words * bitmap_blocks
        # Mark the bitmap blocks as used
        self.set_used(0)  # Block 0 is always used
        self.set_used(self.fs.mfd_block1)  # MFD block 1
        self.set_used(self.fs.mfd_block2)  # MFD block 2
        for block in self.blocks:
            self.set_used(block)
        for block in range(number_of_blocks, self.total_bits):
            self.set_used(block)
        self.write()
        return self

    def write(self) -> None:
        """
        Write the bitmap blocks
        """
        for bitmap_num in range(0, len(self.blocks)):
            next_block = self.blocks[bitmap_num + 1] if bitmap_num < len(self.blocks) - 1 else 0
            words = [
                next_block,  #        1 word  Next bitmap block number
                bitmap_num + 1,  #    1 word  Map block number
                self.num_of_words,  # 1 word  Number of words of map
                self.blocks[0],  #    1 word  First bitmap block number
            ]
            words += self.bitmaps[bitmap_num * self.num_of_words : (bitmap_num + 1) * self.num_of_words]
            words = pad_words(words, WORDS_PER_BLOCK)  # Fill the rest with zeros
            self.fs.write_words_block(self.blocks[bitmap_num], words)

    @property
    def total_bits(self) -> int:
        """
        Return the bitmap length in bit
        """
        return len(self.bitmaps) * 16

    def is_free(self, bit_index: int) -> bool:
        """
        Check if a block is free
        """
        int_index = bit_index // 16
        bit_position = bit_index % 16
        bit_value = self.bitmaps[int_index]
        return (bit_value & (1 << bit_position)) == 0

    def set_used(self, bit_index: int) -> None:
        """
        Mark a block as used
        """
        int_index = bit_index // 16
        bit_position = bit_index % 16
        self.bitmaps[int_index] |= 1 << bit_position

    def set_free(self, bit_index: int) -> None:
        """
        Mark a block as free
        """
        int_index = bit_index // 16
        bit_position = bit_index % 16
        self.bitmaps[int_index] &= ~(1 << bit_position)

    def find_contiguous_blocks(self, size: int) -> int:
        """
        Find contiguous blocks, return the first block number
        """
        current_run = 0
        start_index = -1
        for i in range(self.total_bits - 1, -1, -1):
            if self.is_free(i):
                if current_run == 0:
                    start_index = i
                current_run += 1
                if current_run == size:
                    return start_index - size + 1
            else:
                current_run = 0
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def allocate(self, size: int, contiguous: bool = False) -> t.List[int]:
        """
        Allocate contiguous or sparse blocks
        """
        blocks = []
        if contiguous and size != 1:
            start_block = self.find_contiguous_blocks(size)
            for block in range(start_block, start_block + size):
                self.set_used(block)
                blocks.append(block)
        else:
            for block in range(0, self.total_bits):
                if self.is_free(block):
                    self.set_used(block)
                    blocks.append(block)
                if len(blocks) == size:
                    break
            if len(blocks) < size:
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        return blocks

    def used(self) -> int:
        """
        Count the number of used blocks
        """
        used = 0
        for block in self.bitmaps:
            used += block.bit_count()
        return used

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        return len(self.bitmaps) * 16 - self.used()

    def __str__(self) -> str:
        free = self.free()
        used = self.used()
        return f"Free blocks: {free:<6} Used blocks: {used:<6}"


class DOS11File(AbstractFile):
    entry: "DOS11DirectoryEntry"
    length: int  # Length in blocks
    contiguous: bool

    def __init__(self, entry: "DOS11DirectoryEntry"):
        self.entry = entry
        self.contiguous = entry.contiguous
        self.length = entry.length

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        Contiguous file block size is 512
        Linked file block size is 510
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
        if self.contiguous:
            # Contiguous file
            return self.entry.ufd_block.fs.read_block(
                self.entry.start_block + block_number,
                number_of_blocks,
            )
        else:
            # Linked file
            seq = 0
            data = bytearray()
            next_block_number = self.entry.start_block
            while next_block_number != 0 and number_of_blocks:
                t = self.entry.ufd_block.fs.read_block(next_block_number)
                next_block_number = bytes_to_word(t, 0)
                if seq >= block_number:
                    data.extend(t[2:])
                    number_of_blocks -= 1
                seq += 1
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
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.entry.length
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        if self.contiguous:
            # Contiguous file
            buffer += bytearray(BLOCK_SIZE * number_of_blocks - len(buffer))
            self.entry.ufd_block.fs.write_block(
                buffer,
                self.entry.start_block + block_number,
                number_of_blocks,
            )
        else:
            # Linked file
            seq = 0
            next_block_number = self.entry.start_block
            block_size = self.get_block_size()
            while next_block_number != 0 and number_of_blocks:
                t = self.entry.ufd_block.fs.read_block(next_block_number)
                if seq >= block_number:
                    t = t[:2] + buffer[:block_size]
                    t += bytearray(BLOCK_SIZE - len(t))
                    buffer = buffer[block_size:]
                    number_of_blocks -= 1
                    self.entry.ufd_block.fs.write_block(t, next_block_number)
                next_block_number = bytes_to_word(t, 0)
                seq += 1

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return self.length

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.get_length() * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE if self.contiguous else LINKED_FILE_BLOCK_SIZE

    def __str__(self) -> str:
        return self.entry.fullname


class DOS11DirectoryEntry(XFERXPath):
    """
    User File Directory Entry

        +-------------------------------------+
     0  |               File                  |
     2  |               name                  |
        +-------------------------------------+
     4  |            Extension                |
        +-------------------------------------+
     6  |Type| Reserved |    Creation Date    |
        +-------------------------------------+
     8  |     Spare     | Lock | Usage count  |
        +-------------------------------------+
    10  |           Start block #             |
        +-------------------------------------+
    12  |        Length (# of blocks)         |
        +-------------------------------------+
    14  |            End block #              |
        +-------------------------------------+
    16  |     Spare     |   Protection code   |
        +-------------------------------------+

    Disk Operating System Monitor - System Programmers Manual, Pag 136, 202
    http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf
    """

    fs: "DOS11Filesystem"
    ufd_block: "UserFileDirectoryBlock"
    uic: UIC = DEFAULT_UIC
    stem: str = ""
    extension: str = ""
    raw_creation_date: int = 0
    start_block: int = 0  # Block number of the first logical block
    length: int = 0  # Length in blocks
    end_block: int = 0  # Block number of the last logical block
    contiguous: bool = False  # Linked/contiguous file
    protection_code: int = 0  # System Programmers Manual, Pag 140
    usage_count: int = 0  # System Programmers Manual, Pag 136
    spare1: int = 0
    spare2: int = 0

    def __init__(self, ufd_block: "UserFileDirectoryBlock"):
        self.fs = ufd_block.fs
        self.ufd_block = ufd_block
        self.uic = ufd_block.uic

    @classmethod
    def read(cls, ufd_block: "UserFileDirectoryBlock", words: t.List[int], position: int) -> "DOS11DirectoryEntry":
        # DOS Course Handouts, Pag 14
        # http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DOS_CourseHandouts.pdf
        self = DOS11DirectoryEntry(ufd_block)
        self.stem = rad50_word_to_asc(words[position]) + rad50_word_to_asc(words[position + 1])
        self.extension = rad50_word_to_asc(words[position + 2])
        self.raw_creation_date = words[position + 3]  # Type, Creation date
        self.spare1 = words[position + 4] >> 8  # Spare
        self.usage_count = words[position + 4] & 0xFF  # Lock, usage count
        self.start_block = words[position + 5]  # Block number of the first logical block
        self.length = words[position + 6]  # Length in blocks
        self.end_block = words[position + 7]  # Block number of the last logical block
        self.protection_code = words[position + 8] & 0xFF  # Protection code
        self.spare2 = words[position + 8] >> 8  # Spare
        if self.raw_creation_date & CONTIGUOUS_FILE_TYPE:
            self.contiguous = True
            self.raw_creation_date &= ~CONTIGUOUS_FILE_TYPE
        else:
            self.contiguous = False
        return self

    def to_words(self) -> t.List[int]:
        """
        Dump the directory entry to words
        """
        # Adjust raw_creation_date for contiguous files
        if self.contiguous:
            raw_creation_date = self.raw_creation_date | CONTIGUOUS_FILE_TYPE
        else:
            raw_creation_date = self.raw_creation_date & ~CONTIGUOUS_FILE_TYPE
        # Pack values into words
        return [
            asc_to_rad50_word(self.stem[:3]),  # File Name (1 word)
            asc_to_rad50_word(self.stem[3:6]),  # File Name (1 word)
            asc_to_rad50_word(self.extension),  # File Type (1 word)
            raw_creation_date,  # Type, Creation date (1 word)
            (self.spare1 << 8) | self.usage_count,  # Lock, usage count (1 word)
            self.start_block,  # Block number of the first logical block (1 word)
            self.length,  # Length in blocks (1 word)
            self.end_block,  # Block number of the last logical block (1 word)
            (self.spare2 << 8) | self.protection_code,  # Protection code (1 word)
        ]

    def is_dir(self) -> bool:
        return False

    @property
    def parent(self) -> "UserFileDirectory":
        """
        Get the parent User File Directory
        """
        return self.fs.get_ufd(self.uic)

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
        return self.length

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get file size in bytes
        """
        return self.length * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE if self.contiguous else LINKED_FILE_BLOCK_SIZE

    @property
    def creation_date(self) -> t.Optional[date]:
        return dos11_to_date(self.raw_creation_date)

    def delete(self) -> bool:
        contiguous = self.contiguous
        start_block = self.start_block
        length = self.length
        # Write an empty User File Directory Entry
        self.raw_creation_date = 0
        self.usage_count = 0
        self.spare1 = 0
        self.start_block = 0
        self.length = 0
        self.end_block = 0
        self.protection_code = 0
        self.spare2 = 0
        self.stem = ""
        self.extension = ""
        self.contiguous = False
        self.ufd_block.write()
        # Free space
        bitmap = self.ufd_block.fs.read_bitmap()
        if contiguous:
            # Contiguous file
            for block_number in range(start_block, start_block + length):
                bitmap.set_free(block_number)
        else:
            # Linked file
            next_block_number = start_block
            while next_block_number != 0:
                bitmap.set_free(next_block_number)
                t = self.ufd_block.fs.read_block(next_block_number)
                next_block_number = bytes_to_word(t, 0)
        bitmap.write()
        return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        self.ufd_block.write()
        return True

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> DOS11File:
        """
        Open a file
        """
        return DOS11File(self)

    def __str__(self) -> str:
        return (
            f"{self.stem:<6}."
            f"{self.extension:<3}  "
            f"{self.uic.to_wide_str() if self.uic else '':<9}  "
            f"{self.creation_date or '          '} "
            f"{self.length:>6}{'C' if self.contiguous else ' '} "
            f"{self.start_block:6d} "
            f"{self.end_block:6d} "
            f"{self.protection_code:>6o} "
            f"{self.usage_count:>6o}"
        )

    def __repr__(self) -> str:
        return str(self)


class UserFileDirectoryBlock(object):
    """
    User File Directory Block

          +-------------------------------------+
       0  |          Link to next MFD           |
          +-------------------------------------+
       2  | UDF Entries                       1 |
          | .                                   |
          | .                                28 |
          +-------------------------------------+
     506  | Spare                               |
     512  | .                                   |
          +-------------------------------------+

    Each UFD Directory Block contains 28 entries, each entry is 9 words long.

    Disk Operating System Monitor - System Programmers Manual, Pag 136
    http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf
    """

    # Block number of this user file directory block
    block_number = 0
    # Block number of the next user file directory block
    next_block_number = 0
    # User Identification Code
    uic: UIC = DEFAULT_UIC
    # User File Directory Block entries
    entries_list: t.List["DOS11DirectoryEntry"] = []

    def __init__(self, fs: "DOS11Filesystem", uic: UIC = DEFAULT_UIC):
        self.fs = fs
        self.uic = uic

    @classmethod
    def new(cls, fs: "DOS11Filesystem", uic: UIC, block_number: int) -> "UserFileDirectoryBlock":
        """
        Create a new empty User File Directory Block
        """
        self = UserFileDirectoryBlock(fs, uic)
        self.block_number = block_number
        self.next_block_number = 0
        self.entries_list = []
        for _ in range(1, UFD_ENTRIES * UFD_ENTRY_SIZE, UFD_ENTRY_SIZE):
            dir_entry = DOS11DirectoryEntry(self)
            self.entries_list.append(dir_entry)
        return self

    @classmethod
    def read(cls, fs: "DOS11Filesystem", uic: UIC, block_number: int) -> "UserFileDirectoryBlock":
        """
        Read a User File Directory Block from disk
        """
        self = UserFileDirectoryBlock(fs, uic)
        self.block_number = block_number
        words = self.fs.read_words_block(self.block_number)
        self.next_block_number = words[0]
        self.entries_list = []
        for position in range(1, UFD_ENTRIES * UFD_ENTRY_SIZE, UFD_ENTRY_SIZE):
            dir_entry = DOS11DirectoryEntry.read(self, words, position)
            self.entries_list.append(dir_entry)
        return self

    def write(self) -> None:
        """
        Write a User File Directory Block to disk
        """
        words = [self.next_block_number]  # Link to next block
        # Write each directory entry to the buffer
        for entry in self.entries_list:
            words += entry.to_words()
        words = pad_words(words, WORDS_PER_BLOCK)  # Fill the rest with zeros
        # Write the blocks to the disk
        self.fs.write_words_block(self.block_number, words)

    def get_empty_entry(self) -> t.Optional["DOS11DirectoryEntry"]:
        """
        Get the first empty directory entry
        """
        for entry in self.entries_list:
            if entry.is_empty():
                return entry
        return None

    def examine(self, options: t.Dict[str, t.Union[bool, str]]) -> str:
        include_all = bool(options.get("full", False))
        buf = io.StringIO()
        buf.write("\n*User File Directory Block\n")
        buf.write(f"UIC:                   {self.uic or ''}\n")
        buf.write(f"Block number:          {self.block_number}\n")
        buf.write(f"Next dir block:        {self.next_block_number}\n")
        header = False
        for i, x in enumerate(self.entries_list):
            if include_all or not x.is_empty():
                if not header:
                    buf.write("\nNum  File        UIC        Date       Length   Block    End   Code  Usage")
                    buf.write("\n---  ----        ---        ----       ------   -----    ---   ----  -----\n")
                    header = True
                buf.write(f"{i:02d}#  {x}\n")
        return buf.getvalue()

    def __str__(self) -> str:
        """
        String representation of the User File Directory Block
        """
        return self.examine({"full": True})


class UserFileDirectory(XFERXPath):
    """
    Master File Directory Entry in the MFD block

          +-------------------------------------+
       0  |     Group code  |     User code     |
          +-------------------------------------+
       2  |          UFD start block #          |
          +-------------------------------------+
       4  |         # of words in UFD entry     |
          +-------------------------------------+
       6  |                 0                   |
          +-------------------------------------+

    Disk Operating System Monitor - System Programmers Manual, Pag 201
    https://bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf
    """

    fs: "DOS11Filesystem"
    mfd_block: "AbstractMasterFileDirectoryBlock"
    uic: UIC = DEFAULT_UIC  # User Identification Code
    ufd_block: int = 0  # UFD start block
    num_words: int = 0  # num of words in UFD entry, always 9
    zero: int = 0  # always 0

    def __init__(self, mfd_block: "AbstractMasterFileDirectoryBlock"):
        self.fs = mfd_block.fs
        self.mfd_block = mfd_block

    @classmethod
    def read(
        cls, mfd_block: "AbstractMasterFileDirectoryBlock", words: t.List[int], position: int
    ) -> "UserFileDirectory":
        self = cls(mfd_block)
        self.uic = UIC.from_word(words[position])  # UIC
        self.ufd_block = words[position + 1]  # UFD start block
        self.num_words = words[position + 2]  # number of words in UFD entry
        self.zero = words[position + 3]  # always 0
        return self

    def to_words(self) -> t.List[int]:
        """
        Dump the directory entry to words
        """
        return [
            self.uic.to_word(),  # UIC
            self.ufd_block,  # UFD start block
            self.num_words,  # number of words in UFD entry
            self.zero,  # always 0
        ]

    def read_ufd_blocks(self) -> t.Iterator["UserFileDirectoryBlock"]:
        """Read User File Directory blocks"""
        next_block_number = self.ufd_block
        while next_block_number != 0:
            ufd_block = UserFileDirectoryBlock.read(self.fs, self.uic, next_block_number)
            next_block_number = ufd_block.next_block_number
            yield ufd_block

    def iterdir(
        self,
        pattern: t.Optional[str] = None,
        include_all: bool = False,
        wildcard: bool = False,
    ) -> t.Iterator["DOS11DirectoryEntry"]:
        for ufd_block in self.read_ufd_blocks():
            for entry in ufd_block.entries_list:
                if filename_match(entry.name, pattern, wildcard):
                    if include_all or not entry.is_empty():
                        yield entry

    def is_dir(self) -> bool:
        return True

    @property
    def parent(self) -> XFERXPath:
        """The logical parent"""
        return self.fs.get_root()

    def is_empty(self) -> bool:
        return self.num_words == 0

    @property
    def fullname(self) -> str:
        return f"{self.uic}"

    @property
    def name(self) -> str:
        return f"{self.uic}"

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return len(list(self.read_ufd_blocks()))

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get entry size in bytes
        """
        return self.get_length(fork) * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> DOS11File:
        raise OSError(errno.EINVAL, "Invalid operation on directory")

    def delete(self) -> bool:
        # Delete all entries in the UFD
        for entry in self.iterdir():
            if not entry.delete():
                raise OSError(errno.EIO, os.strerror(errno.EIO))
        # Free space
        bitmap = self.mfd_block.fs.read_bitmap()
        bitmap.set_free(self.ufd_block)
        # Write an empty Master File Directory Entry
        self.uic = UIC(0, 0)
        self.ufd_block = 0
        self.num_words = 0
        self.zero = 0
        self.mfd_block.write()  # type: ignore
        # Write the bitmap
        bitmap.write()
        return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        raise OSError(errno.EINVAL, "Invalid operation on directory")

    def __str__(self) -> str:
        return f"{self.uic} ufd_block={self.ufd_block} num_words={self.num_words} zero={self.zero}"


class AbstractMasterFileDirectoryBlock:
    """
    DOS-11/XXDP+ Master File Directory Block
    """

    fs: "DOS11Filesystem"
    # Master File Directory Block entries
    entries_list: t.List["UserFileDirectory"] = []


class MasterFileDirectoryBlock(AbstractMasterFileDirectoryBlock):
    """
    Master File Directory Block 2 - N

    MFD Block 2 - N:
          +-------------------------------------+
       0  |          Link to next MFD           |
          +-------------------------------------+
       2  | MFD Entries                       1 |
          | .                                   |
          | .                                28 |
          +-------------------------------------+

    Disk Operating System Monitor - System Programmers Manual, Pag 135
    http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf
    """

    # Block number of this Master File Directory block
    block_number = 0
    # Block number of the next Master File Directory block
    next_block_number = 0

    def __init__(self, fs: "DOS11Filesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "DOS11Filesystem", block_number: int) -> "MasterFileDirectoryBlock":
        """
        Read a Master File Directory Block from disk
        """
        self = MasterFileDirectoryBlock(fs)
        self.block_number = block_number
        words = self.fs.read_words_block(self.block_number)
        self.next_block_number = words[0]  # link to next MFD
        self.entries_list = []
        for position in range(1, WORDS_PER_BLOCK - MFD_ENTRY_SIZE, MFD_ENTRY_SIZE):
            entry = UserFileDirectory.read(self, words, position)
            self.entries_list.append(entry)
        return self

    @classmethod
    def new(cls, fs: "DOS11Filesystem", block_number: int) -> "MasterFileDirectoryBlock":
        """
        Create a new empty Master File Directory Block
        """
        self = MasterFileDirectoryBlock(fs)
        self.block_number = block_number
        self.next_block_number = 0
        self.entries_list = []
        for _ in range(1, WORDS_PER_BLOCK - MFD_ENTRY_SIZE, MFD_ENTRY_SIZE):
            entry = UserFileDirectory(self)
            self.entries_list.append(entry)
        return self

    def write(self) -> None:
        """
        Write a Master File Directory Block to disk
        """
        words = [self.next_block_number]  # Link to next MFD block
        # Write each directory entry to the buffer
        for entry in self.entries_list:
            words += entry.to_words()
        words = pad_words(words, WORDS_PER_BLOCK)  # Fill the rest with zeros
        # Write the buffer to the disk
        self.fs.write_words_block(self.block_number, words)

    def get_empty_entry(self) -> t.Optional["UserFileDirectory"]:
        """
        Get the first empty directory entry
        """
        for entry in self.entries_list:
            if entry.is_empty():
                return entry
        return None


class XXDPMasterFileDirectoryBlock(AbstractMasterFileDirectoryBlock):
    """
    XXDP Master File Directory

    XXDP has only one UFD in the MFD
    """

    def __init__(self, fs: "DOS11Filesystem"):
        self.fs = fs
        entry = UserFileDirectory(self)
        entry.ufd_block = self.fs.xxdp_ufd_block
        entry.uic = self.fs.uic or DEFAULT_UIC
        entry.num_words = UFD_ENTRY_SIZE
        self.entries_list = [entry]


class DOS11RootDirectory(XFERXPath):
    fs: "DOS11Filesystem"

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
        Iterate over the User File Directory entries
        """
        for mfd in self.fs.read_mfd_blocks():
            for entry in mfd.entries_list:
                if not entry.is_empty():
                    yield entry


class DOS11Filesystem(AbstractRXBlockFilesystem):
    """
    DOS-11/XXDP+ Filesystem

    General disk layout:

    Block
          +-------------------------------------+
    0     |            Bootstrap block          |
          +-------------------------------------+
    1     |             MFD Block #1            |
          +-------------------------------------+
    2     |             UFD Block #1            |
          +-------------------------------------+
          |           User linked files         |
          |           other UFD blocks          |
          |        User contiguous files        |
          +-------------------------------------+
    l-n   |             MFD Block #2            |
          +-------------------------------------+
    l-n-1 | Bitmap Block                      1 |
          | .                                   |
    l     | .                                 n |
          +--------------------------------------


    DOS-11 format - Pag 204
    http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf

    DECtape format - Pag 206
    http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf

    XXDP File Structure Guide - Pag 8
    https://raw.githubusercontent.com/rust11/xxdp/main/XXDP%2B%20File%20Structure.pdf
    """

    fs_name = "dos11"
    fs_description = "PDP-11 DOS-11/XXDP+"
    fs_platforms = ["pdp11"]
    fs_entry_metadata = [
        "creation_date",
        "contiguous",
        "protection_code",
    ]

    uic: t.Optional[UIC]  # current User Identification Code
    xxdp: bool = False  # MFD Variety #2 (XXDP+)
    dectape: bool = False  # DECtape format
    mfd_block1: int = MFD1_BLOCK  # Master File Directory Block #1
    mfd_block2: int = 0  # Master File Directory Block #2
    bitmap_block: int = 0  # Bitmap first block number
    interleave_factor: int = DEFAULT_INTERLAVE_FACTOR  # Interleave factor
    xxdp_ufd_block: int = 0  # XXDP+ UFD first block number

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        strict: t.Union[bool, str] = True,
        **kwargs: t.Union[bool, str],
    ) -> "DOS11Filesystem":
        self = cls(file_or_dev)
        self.uic = None
        self.read_mfd()  # Read the Master File Directory
        if strict:
            # Check if the used blocks are in the bitmap
            blocks = [mfd.ufd_block for mfd in self.read_mfd_entries()]
            if not self.bitmap_block:
                raise OSError(errno.EIO, "Failed to read MFD block")
            bitmap = self.read_bitmap()
            for block in blocks:
                if bitmap.is_free(block):
                    raise OSError(errno.EIO, f"Block {block} is not in the bitmap")
            if bitmap.is_free(self.bitmap_block):
                raise OSError(errno.EIO, f"Block {self.bitmap_block} is not in the bitmap")
        return self

    @classmethod
    def initialize(
        cls, file_or_dev: t.Union["AbstractFile", "AbstractDevice"], **kwargs: t.Union[bool, str]
    ) -> "DOS11Filesystem":
        """
        Create an empty filesystem
        """
        self = cls(file_or_dev)
        self.dectape = kwargs.get("device_type", "") == DECTAPE
        self.xxdp = False
        self.uic = None
        self.interleave_factor = DEFAULT_INTERLAVE_FACTOR
        if self.dectape:
            self.mfd_block1 = DECTAPE_MFD1_BLOCK
            self.mfd_block2 = DECTAPE_MFD2_BLOCK
            self.bitmap_block = DECTAPE_BITMAP_BLOCK
        else:
            self.mfd_block1 = MFD1_BLOCK
            self.mfd_block2 = MFD1_BLOCK + 2
            self.bitmap_block = MFD1_BLOCK + 3

        # Create the bitmap
        bitmap = DOS11Bitmap.new(self, self.bitmap_block)

        # Create the Master File Directory Block #1
        words = [
            self.mfd_block2,  #        Master File Directory Block #2
            self.interleave_factor,  # Interleave factor
            self.bitmap_block,  #      Bitmap start block
        ] + bitmap.blocks  #           Bitmap blocks
        words = pad_words(words, WORDS_PER_BLOCK)  # Fill the rest with zeros
        self.write_words_block(self.mfd_block1, words)

        # Create the Master File Directory Block #2
        mfd_block = MasterFileDirectoryBlock.new(self, self.mfd_block2)
        mfd_block.write()

        # Create an User File Directory
        self.create_directory("[1,1]", {})
        return self

    def read_words_block(
        self,
        block_number: int,
    ) -> t.List[int]:
        """
        Read a 512 bytes block as 256 16bit words
        """
        data = self.read_block(block_number)
        if not data:
            raise OSError(errno.EIO, f"Failed to read block {block_number}")
        return list(struct.unpack_from("<256H", data))

    def write_words_block(
        self,
        block_number: int,
        words: t.List[int],
    ) -> None:
        """
        Write 256 16bit words as a 512 bytes block
        """
        data = struct.pack("<256H", *words)
        self.write_block(data, block_number)

    def read_mfd_entries(
        self,
        uic: UIC = ANY_UIC,
    ) -> t.Iterator["UserFileDirectory"]:
        """
        Read Master File Directory entries
        """
        for mfd in self.read_mfd_blocks():
            for entry in mfd.entries_list:
                if not entry.is_empty() and uic.match(entry.uic):  # Filter by UIC
                    yield entry

    def read_mfd_blocks(self) -> t.Iterator["AbstractMasterFileDirectoryBlock"]:
        """
        Read Master File Directory blocks
        """
        if self.mfd_block2 != 0:  # MFD Variety #1 (DOS-11)
            mfd_block = self.mfd_block2
            while mfd_block:
                mfd = MasterFileDirectoryBlock.read(self, mfd_block)
                mfd_block = mfd.next_block_number
                yield mfd
        else:  # MFD Variety #2 (XXDP+)
            yield XXDPMasterFileDirectoryBlock(self)

    def read_mfd(self) -> None:
        """
        Read Master File Directory Block 1

        MFD Block 1:

              +-------------------------------------+
              |        Block # of MFD Block 2       |
              +-------------------------------------+
              |           Interleave factor         |
              +-------------------------------------+
              |            Bitmap block #1          |
              +-------------------------------------+
              |            Bitmap block #2          |
              /                                     /
              |            Bitmap block #n          |
              +-------------------------------------+
              |                    0                |
              +-------------------------------------+
              |                                     |

        """
        # Check DECtape format
        try:
            words = self.read_words_block(DECTAPE_MFD1_BLOCK)
            self.mfd_block2 = words[0]  # Next MFD block
            self.interleave_factor = words[1]  # Interleave factor
            self.bitmap_block = words[2]  # Bitmap start block
            if self.mfd_block2 != DECTAPE_MFD2_BLOCK:
                raise OSError("Not a DECtape filesystem")
            words = self.read_words_block(self.mfd_block2)
            if words[0] != 0:  # 0, DECtape has only 2 MFD
                raise OSError("Not a DECtape filesystem")
            if words[2] != DECTAPE_UFD1_BLOCK:
                raise OSError("Not a DECtape filesystem")
            self.mfd_block1 = DECTAPE_MFD1_BLOCK
            self.dectape = True
        except OSError:
            words = self.read_words_block(MFD1_BLOCK)
            self.mfd_block1 = MFD1_BLOCK
            self.mfd_block2 = words[0]  # Next MFD block
            self.interleave_factor = words[1]  # Interleave factor
            self.bitmap_block = words[2]  # Bitmap start block
            self.dectape = False

        if self.mfd_block2 == 0:  # MFD Variety #2 (XXDP+)
            # Pag 10
            # https://raw.githubusercontent.com/rust11/xxdp/main/XXDP%2B%20File%20Structure.pdf
            self.xxdp = True
            # words[0] # Always 0
            self.xxdp_ufd_block = words[1]  # First UFD block
            # words[2] # Number of UFD blocks, not used in XXDP+
            self.bitmap_block = words[3]  # Bitmap start block

    def read_bitmap(self) -> DOS11Bitmap:
        bitmap = DOS11Bitmap.read(self, self.bitmap_block)
        return bitmap

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,  # expand directories
        wildcard: bool = True,
        uic: t.Optional[UIC] = None,
    ) -> t.Iterator[t.Union[DOS11DirectoryEntry, UserFileDirectory]]:
        if uic is None:
            uic = self.uic
        uic, filename_pattern = dos11_split_fullname(fullname=pattern, wildcard=wildcard, uic=uic)
        if pattern and not filename_pattern and not expand:
            # If expand is False, check if the pattern is an UIC
            try:
                uic = UIC.from_str(pattern)
                for mfd_block in self.read_mfd_blocks():
                    for entry in mfd_block.entries_list:
                        if not entry.is_empty() and uic.match(entry.uic):
                            yield entry  # type: ignore
                return
            except Exception:
                pass
        if uic is None:
            yield from self.get_root().iterdir()
        else:
            for mfd in self.read_mfd_entries(uic=uic):
                yield from mfd.iterdir(pattern=filename_pattern, include_all=include_all, wildcard=wildcard)

    @property
    def entries_list(self) -> t.Iterator[t.Union[DOS11DirectoryEntry, UserFileDirectory]]:
        if self.uic is None:
            yield from self.get_root().iterdir()
        else:
            for mfd in self.read_mfd_entries(uic=self.uic):
                yield from mfd.iterdir()

    def get_file_entry(self, fullname: str) -> t.Union[DOS11RootDirectory, DOS11DirectoryEntry, UserFileDirectory]:
        """
        Get the directory entry for a file
        """
        fullname = dos11_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        uic, name = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
        if not uic:
            raise FileNotFoundError(errno.ENOENT, "No UIC specified")
        try:
            if not name:
                return next(self.read_mfd_entries(uic=uic))
            else:
                return next(self.filter_entries_list(name, wildcard=False, uic=uic))
        except StopIteration:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def get_root(self) -> DOS11RootDirectory:
        """
        Get the root directory
        """
        return DOS11RootDirectory(self)

    def get_ufd(self, uic: UIC) -> UserFileDirectory:
        """
        Get the User File Directory for a given UIC
        """
        for mfd_block in self.read_mfd_blocks():
            for entry in mfd_block.entries_list:
                if not entry.is_empty() and uic.match(entry.uic):
                    return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(uic))

    def create_file(
        self,
        fullname: str,
        size: int,  # Size in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> DOS11DirectoryEntry:
        """
        Create a new file with a given length in number of blocks
        """
        metadata = metadata or {}
        file_type: t.Optional[str] = metadata.get("file_type")  # type: ignore
        protection_code: int = metadata.get("protection_code", DEFAULT_PROTECTION_CODE)  # type: ignore
        creation_date: t.Optional[date] = metadata.get("creation_date")  # type: ignore
        block_size = BLOCK_SIZE if dos11_get_file_type_id(file_type) == CONTIGUOUS_FILE_TYPE else LINKED_FILE_BLOCK_SIZE
        number_of_blocks = (size + block_size - 1) // block_size
        contiguous = dos11_get_file_type_id(file_type) == CONTIGUOUS_FILE_TYPE
        # Delete the existing file
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass
        # Get the MFD entry for the target UIC
        uic, name = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
        if not uic:
            uic = DEFAULT_UIC
        try:
            mfd = next(self.read_mfd_entries(uic=uic))
        except Exception:
            raise NotADirectoryError
        # Allocate the space for the file
        bitmap = self.read_bitmap()
        blocks = bitmap.allocate(number_of_blocks, contiguous)
        # Create the directory entry
        new_entry = None
        for ufd_block in mfd.read_ufd_blocks():
            new_entry = ufd_block.get_empty_entry()
            if new_entry is not None:
                break
        if new_entry is None:
            # Allocate a new UFD block
            new_block_number = bitmap.allocate(1)[0]
            # Write the link to new new block in the old block
            ufd_block.next_block_number = new_block_number
            ufd_block.write()
            # Create a new UFD block
            ufd_block = UserFileDirectoryBlock.new(self, uic, new_block_number)
            new_entry = ufd_block.get_empty_entry()
            if new_entry is None:
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        try:
            stem, extension = name.split(".", 1)  # type: ignore
        except Exception:
            stem = name
            extension = ""
        new_entry.stem = stem
        new_entry.extension = extension
        new_entry.raw_creation_date = date_to_dos11(creation_date or date.today())
        new_entry.start_block = blocks[0]
        new_entry.length = number_of_blocks
        new_entry.end_block = blocks[-1]
        new_entry.contiguous = contiguous
        new_entry.protection_code = protection_code
        new_entry.ufd_block.write()
        # Write bitmap
        bitmap.write()
        # Write linked file
        if not contiguous:
            for i, block in enumerate(blocks):
                buffer = bytearray(BLOCK_SIZE)
                next_block_number = blocks[i + 1] if i + 1 < len(blocks) else 0
                struct.pack_into("<H", buffer, 0, next_block_number)
                self.write_block(buffer, block)
        return new_entry

    def create_directory(
        self,
        fullname: str,
        options: t.Dict[str, t.Union[bool, str]],
    ) -> "UserFileDirectory":
        """
        Create a User File Directory
        """
        if self.xxdp:
            raise OSError(errno.EINVAL, "Invalid operation on XXDP+ filesystem")
        try:
            uic = UIC.from_str(fullname)
        except Exception:
            raise OSError(errno.EINVAL, "Invalid UIC")
        # Check if the UIC already exists
        if list(self.read_mfd_entries(uic=uic)):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST))
        found = False
        mfd: "MasterFileDirectoryBlock"
        for mfd in self.read_mfd_blocks():  # type: ignore
            entry: UserFileDirectory = mfd.get_empty_entry()  # type: ignore
            if entry is not None:
                found = True
                break
        if not found:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        # Create a new UFD block
        bitmap = self.read_bitmap()
        if self.dectape:
            if bitmap.is_free(DECTAPE_UFD1_BLOCK):
                blocks = [DECTAPE_UFD1_BLOCK, DECTAPE_UFD2_BLOCK]
                bitmap.set_used(DECTAPE_UFD1_BLOCK)
                bitmap.set_used(DECTAPE_UFD2_BLOCK)
            else:
                blocks = bitmap.allocate(2, contiguous=True)
        else:
            blocks = bitmap.allocate(2)
        bitmap.write()
        # Initialize the blocks with zeros
        self.write_words_block(blocks[0], pad_words([blocks[1]], WORDS_PER_BLOCK))
        self.write_words_block(blocks[1], [0] * WORDS_PER_BLOCK)
        # Write the new entry
        entry.uic = uic
        entry.ufd_block = blocks[0]
        entry.num_words = UFD_ENTRY_SIZE
        mfd.write()
        return entry

    def show_accounts(self, volume_id: str, options: t.Dict[str, bool]) -> None:
        """
        Listing of all UIC
        """
        sys.stdout.write(f"{volume_id}:\n\n")
        for mfd in self.read_mfd_entries(uic=ANY_UIC):
            sys.stdout.write(f"{mfd.uic.to_wide_str()}\n")

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List directory entries
        """
        files = 0
        blocks = 0
        i = 0
        uic, pattern = dos11_split_fullname(fullname=pattern, wildcard=True, uic=self.uic)
        if uic is None:
            self.show_accounts(volume_id, options)
            return
        if self.xxdp:
            sys.stdout.write("ENTRY# FILNAM.EXT        DATE          LENGTH  START\n")
        else:
            dt = date.today().strftime('%y-%b-%d').upper()
            sys.stdout.write(f"DIRECTORY {volume_id}: {uic}\n\n{dt}\n\n")
        for x in self.filter_entries_list(pattern, uic=uic, include_all=True, wildcard=True):
            if isinstance(x, DOS11DirectoryEntry) and not x.is_empty():
                i = i + 1
                fullname = x.is_empty() and x.stem or "%-6s.%-3s" % (x.stem, x.extension)
                creation_date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or ""
                attr = "C" if x.contiguous else ""
                if self.xxdp:
                    sys.stdout.write(
                        f"{i:6} {fullname:>10s} {creation_date:>14s} {x.length:>10d}    {x.start_block:06o}\n"
                    )
                else:
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
        if options.get("bitmap"):
            # Display the bitmap
            bitmap = self.read_bitmap()
            for i in range(0, bitmap.total_bits):
                sys.stdout.write(f"{i:>4d} {'[ ]' if bitmap.is_free(i) else '[X]'}  ")
                if i % 16 == 15:
                    sys.stdout.write("\n")
            sys.stdout.write(f"\nUsed blocks: {bitmap.used()}\n")
        elif arg:
            # Display the file entry
            entries = self.filter_entries_list(arg, wildcard=True)
            for entry in entries:
                sys.stdout.write(f"UIC:                      {entry.uic.to_wide_str()}\n")
                sys.stdout.write(f"Creation date:            {entry.creation_date}\n")
                if isinstance(entry, DOS11DirectoryEntry):
                    sys.stdout.write(f"Filename:                 {entry.stem}\n")
                    sys.stdout.write(f"Extension:                {entry.extension}\n")
                    sys.stdout.write(f"Length in blocks:         {entry.length}\n")
                    sys.stdout.write(f"Start block:              {entry.start_block}\n")
                    sys.stdout.write(f"End block:                {entry.end_block}\n")
                    sys.stdout.write(f"Contiguous:               {'Y' if entry.contiguous else 'N'}\n")
                    sys.stdout.write(f"Protection code:          {entry.protection_code:03o}\n")
                    sys.stdout.write(f"Usage count:              {entry.usage_count}\n")
        else:
            bitmap = self.read_bitmap()
            sys.stdout.write(f"Free blocks:       {bitmap.free():>8}\n")
            sys.stdout.write(f"Used blocks:       {bitmap.used():>8}\n")
            sys.stdout.write(f"Is DECtape:               {'Y' if self.dectape else 'N'}\n")
            sys.stdout.write(f"Is XXDP+:                 {'Y' if self.xxdp else 'N'}\n")
            sys.stdout.write(f"MFD block #1:      {self.mfd_block1:>8}\n")
            sys.stdout.write(f"MFD block #2:      {self.mfd_block2:>8}\n")
            sys.stdout.write(f"Bitmap block:      {self.bitmap_block:>8}\n")
            sys.stdout.write(f"Bitmap blocks:     {len(bitmap.blocks):>8}\n")
            sys.stdout.write(f"Bitmap words:      {bitmap.num_of_words:>8}\n")
            sys.stdout.write(f"Interleave factor: {self.interleave_factor:>8}\n")
            for mfd in self.get_root().iterdir():
                for ufd_block in mfd.read_ufd_blocks():
                    sys.stdout.write(ufd_block.examine(options=options))

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        if self.dectape:
            return DECTAPE_BLOCKS * BLOCK_SIZE
        else:
            return self.dev.get_size()

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        """
        Change the current User Identification Code
        """
        if entry is None:
            self.uic = None
        else:
            assert isinstance(entry, UserFileDirectory)
            self.uic = entry.uic

    def get_pwd(self) -> str:
        """
        Get the current User Identification Code
        """
        return str(self.uic) if self.uic is not None else ""

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

    def get_types(self) -> t.List[str]:
        """
        Get the list of the supported file types
        """
        return list(FILE_TYPES.values())
