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

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..block import BlockDevice
from ..commons import BLOCK_SIZE, READ_FILE_FULL, bytes_to_word, filename_match
from ..uic import ANY_UIC, DEFAULT_UIC, UIC
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

MFD_BLOCK = 1
UFD_ENTRIES = 28
LINKED_FILE_BLOCK_SIZE = 510
DECTAPE_MFD1_BLOCK = 0o100
DECTAPE_MFD2_BLOCK = 0o101
DECTAPE_UFD1_BLOCK = 0o102
DECTAPE_UFD2_BLOCK = 0o103
DEFAULT_PROTECTION_CODE = 0o233

MFD_BLOCK_FORMAT = "<HHH"
MFD_BLOCK_FORMAT_V2 = "<HHHH"
MFD_ENTRY_SIZE = 8
MFD_ENTRY_FORMAT = "<HHHH"
UFD_ENTRY_SIZE = 18
UFD_ENTRY_FORMAT = "<HHHHBBHHHBB"

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


def dos11_split_fullname(uic: UIC, fullname: t.Optional[str], wildcard: bool = True) -> t.Tuple[UIC, t.Optional[str]]:
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

    fs: "DOS11Filesystem"
    blocks: t.List[int]
    bitmaps: t.List[int]
    num_of_words: int
    first_bitmap_block: int

    def __init__(self, fs: "DOS11Filesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "DOS11Filesystem", first_bitmap_block: int) -> "DOS11Bitmap":
        """
        Read the bitmap blocks
        """
        self = DOS11Bitmap(fs)
        self.first_bitmap_block = first_bitmap_block
        self.blocks = []
        self.bitmaps = []
        block_number = first_bitmap_block
        while block_number:
            # Read the bitmaps from the disk
            self.blocks.append(block_number)
            t = self.fs.read_block(block_number)
            if not t:
                raise OSError(errno.EIO, f"Failed to read block {block_number}")
            words = struct.unpack_from("<256H", t)
            (
                block_number,  #      1 word  Next bitmap block number
                _,  #                 1 word  Map number
                self.num_of_words,  # 1 word  Number of words of map
                _,  #                 1 word  First bitmap block number
            ) = words[:4]
            self.bitmaps.extend(words[4 : 4 + self.num_of_words])
            block_number = bytes_to_word(t, 0)
        return self

    def write(self) -> None:
        """
        Write the bitmap blocks
        """
        for bitmap_num in range(0, len(self.blocks)):
            next_block = self.blocks[bitmap_num + 1] if bitmap_num < len(self.blocks) - 1 else 0
            words = (
                [
                    next_block,  #        1 word  Next bitmap block number
                    bitmap_num + 1,  #    1 word  Map number
                    self.num_of_words,  # 1 word  Number of words of map
                    self.blocks[0],  #    1 word  First bitmap block number
                ]
                + self.bitmaps[bitmap_num * self.num_of_words : (bitmap_num + 1) * self.num_of_words]
                + ([0] * (256 - 4 - self.num_of_words))
            )
            t = struct.pack("<256H", *words)
            self.fs.write_block(t, self.blocks[bitmap_num])

    @property
    def total_bits(self) -> int:
        """
        Return the bitmap length in bit
        """
        return len(self.bitmaps) * 16

    def get_bit(self, bit_index: int) -> bool:
        """
        Get the bit at the specified position
        """
        int_index = bit_index // 16
        bit_position = bit_index % 16
        bit_value = self.bitmaps[int_index]
        return (bit_value & (1 << bit_position)) != 0

    def set_bit(self, bit_index: int) -> None:
        """
        Set the bit at the specified position
        """
        int_index = bit_index // 16
        bit_position = bit_index % 16
        self.bitmaps[int_index] |= 1 << bit_position

    def clear_bit(self, bit_index: int) -> None:
        """
        Clear the bit at the specified position
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
            if not self.get_bit(i):
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
                self.set_bit(block)
                blocks.append(block)
        else:
            for block in range(0, self.total_bits):
                if not self.get_bit(block):
                    self.set_bit(block)
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


class DOS11File(AbstractFile):
    entry: "DOS11DirectoryEntry"
    closed: bool
    length: int  # Length in blocks
    contiguous: bool

    def __init__(self, entry: "DOS11DirectoryEntry"):
        self.entry = entry
        self.closed = False
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

    def close(self) -> None:
        """
        Close the file
        """
        self.closed = True

    def __str__(self) -> str:
        return self.entry.fullname


class DOS11DirectoryEntry(AbstractDirectoryEntry):
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
     8  |     Spare     |   Protection code   |
        +-------------------------------------+

    Disk Operating System Monitor - System Programmers Manual, Pag 136, 202
    http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf
    """

    ufd_block: "UserFileDirectoryBlock"
    uic: UIC = DEFAULT_UIC
    filename: str = ""
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
        self.ufd_block = ufd_block
        self.uic = ufd_block.uic

    @classmethod
    def read(cls, ufd_block: "UserFileDirectoryBlock", buffer: bytes, position: int) -> "DOS11DirectoryEntry":
        # DOS Course Handouts, Pag 14
        # http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DOS_CourseHandouts.pdf
        self = DOS11DirectoryEntry(ufd_block)
        (
            fnam0,  #                  1 word  File Name
            fnam1,  #                  1 word
            ftyp,  #                   1 word  File Type
            self.raw_creation_date,  # 1 word  Type, Creation date
            self.usage_count,  #       1 byte  Lock, usage count
            self.spare1,  #            1 byte  spare
            self.start_block,  #       1 word  Block number of the first logical block
            self.length,  #            1 word  Length in blocks
            self.end_block,  #         1 word  Block number of the last logical block
            self.protection_code,  #   1 byte  Protection code
            self.spare2,  #            1 byte  spare
        ) = struct.unpack_from(UFD_ENTRY_FORMAT, buffer, position)
        self.filename = rad50_word_to_asc(fnam0) + rad50_word_to_asc(fnam1)
        self.extension = rad50_word_to_asc(ftyp)
        if self.raw_creation_date & CONTIGUOUS_FILE_TYPE:
            self.contiguous = True
            self.raw_creation_date &= ~CONTIGUOUS_FILE_TYPE
        else:
            self.contiguous = False
        return self

    def write_buffer(self, buffer: bytearray, position: int) -> None:
        # Create filename and extension in RAD50 format
        fnam0 = asc_to_rad50_word(self.filename[:3])
        fnam1 = asc_to_rad50_word(self.filename[3:6])
        ftyp = asc_to_rad50_word(self.extension)
        # Adjust raw_creation_date for contiguous files
        if self.contiguous:
            raw_creation_date = self.raw_creation_date | CONTIGUOUS_FILE_TYPE
        else:
            raw_creation_date = self.raw_creation_date & ~CONTIGUOUS_FILE_TYPE
        # Pack values into buffer
        struct.pack_into(
            UFD_ENTRY_FORMAT,
            buffer,
            position,
            fnam0,  #                  1 word  File Name
            fnam1,  #                  1 word
            ftyp,  #                   1 word  File Type
            raw_creation_date,  #      1 word  Type, Creation date
            self.usage_count,  #       1 byte  Lock, usage count
            self.spare1,  #            1 byte  spare
            self.start_block,  #       1 word  Block number of the first logical block
            self.length,  #            1 word  Length in blocks
            self.end_block,  #         1 word  Block number of the last logical block
            self.protection_code,  #   1 byte  Protection code
            self.spare2,  #            1 byte  spare
        )

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
        return self.length

    def get_size(self) -> int:
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
        self.filename = ""
        self.extension = ""
        self.contiguous = False
        self.ufd_block.write()
        # Free space
        bitmap = self.ufd_block.fs.read_bitmap()
        if contiguous:
            # Contiguous file
            for block_number in range(start_block, start_block + length):
                bitmap.clear_bit(block_number)
        else:
            # Linked file
            next_block_number = start_block
            while next_block_number != 0:
                bitmap.clear_bit(next_block_number)
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

    def open(self, file_mode: t.Optional[str] = None) -> DOS11File:
        """
        Open a file
        """
        return DOS11File(self)

    def __str__(self) -> str:
        return (
            f"{self.filename:<6}."
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

    UDF Entry

          +-------------------------------------+
       0  |     Group code  |     User code     |
          +-------------------------------------+
       2  |          UFD start block #          |
          +-------------------------------------+
       4  |         # of words in UFD entry     |
          +-------------------------------------+
       6  |                 0                   |
          +-------------------------------------+

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
        for _ in range(2, UFD_ENTRIES * UFD_ENTRY_SIZE, UFD_ENTRY_SIZE):
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
        t = self.fs.read_block(self.block_number, 2)
        if not t:
            raise OSError(errno.EIO, f"Failed to read block {self.block_number}")
        self.next_block_number = bytes_to_word(t, 0)
        self.entries_list = []
        for position in range(2, UFD_ENTRIES * UFD_ENTRY_SIZE, UFD_ENTRY_SIZE):
            dir_entry = DOS11DirectoryEntry.read(self, t, position)
            self.entries_list.append(dir_entry)
        return self

    def write(self) -> None:
        """
        Write a User File Directory Block to disk
        """
        buffer = bytearray(BLOCK_SIZE * 2)
        # Write the next block number to the buffer
        struct.pack_into("<H", buffer, 0, self.next_block_number)
        # Write each directory entry to the buffer
        for i, dir_entry in enumerate(self.entries_list):
            position = 2 + i * UFD_ENTRY_SIZE
            dir_entry.write_buffer(buffer, position)
        # Write the buffer to the disk
        self.fs.write_block(buffer, self.block_number, 2)

    def get_empty_entry(self) -> t.Optional["DOS11DirectoryEntry"]:
        """
        Get the first empty directory entry
        """
        for entry in self.entries_list:
            if entry.is_empty:
                return entry
        return None

    def __str__(self) -> str:
        buf = io.StringIO()
        buf.write("\n*User File Directory Block\n")
        buf.write(f"UIC:                   {self.uic or ''}\n")
        buf.write(f"Block number:          {self.block_number}\n")
        buf.write(f"Next dir block:        {self.next_block_number}\n")
        buf.write("\nNum  File        UIC        Date       Length   Block    End   Code  Usage")
        buf.write("\n---  ----        ---        ----       ------   -----    ---   ----  -----\n")
        for i, x in enumerate(self.entries_list):
            buf.write(f"{i:02d}#  {x}\n")
        return buf.getvalue()


class MasterFileDirectoryEntry(AbstractDirectoryEntry):
    """
    Master File Directory Entry in the MFD block

          +-------------------------------------+
          |      User Identification Code       |
          +-------------------------------------+
          |         UFD start block #           |
          +-------------------------------------+
          |      # of words in UFD entry        |
          +-------------------------------------+
          |                    0                |
          +-------------------------------------+

    Disk Operating System Monitor - System Programmers Manual, Pag 201
    https://bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf
    """

    mfd_block: "AbstractMasterFileDirectoryBlock"
    uic: UIC = DEFAULT_UIC  # User Identification Code
    ufd_block: int = 0  # UFD start block
    num_words: int = 0  # num of words in UFD entry, always 9
    zero: int = 0  # always 0

    def __init__(self, mfd_block: "AbstractMasterFileDirectoryBlock"):
        self.mfd_block = mfd_block

    @classmethod
    def read(
        cls, mfd_block: "AbstractMasterFileDirectoryBlock", buffer: bytes, position: int
    ) -> "MasterFileDirectoryEntry":
        self = cls(mfd_block)
        (
            mfd_uic,  # UIC
            self.ufd_block,  # UFD start block
            self.num_words,  # number of words in UFD entry
            self.zero,  # always 0
        ) = struct.unpack_from(MFD_ENTRY_FORMAT, buffer, position)
        self.uic = UIC.from_word(mfd_uic)
        return self

    def write_buffer(self, buffer: bytearray, position: int) -> None:
        struct.pack_into(
            MFD_ENTRY_FORMAT,
            buffer,
            position,
            self.uic.to_word(),  # UIC
            self.ufd_block,  # UFD start block
            self.num_words,  # number of words in UFD entry
            self.zero,  # always 0
        )

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
                if filename_match(entry.basename, pattern, wildcard):
                    if include_all or not entry.is_empty:
                        yield entry

    @property
    def is_empty(self) -> bool:
        return self.num_words == 0

    @property
    def fullname(self) -> str:
        return f"{self.uic}"

    @property
    def basename(self) -> str:
        return f"{self.uic}"

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return len(list(self.read_ufd_blocks()))

    def get_size(self) -> int:
        """
        Get entry size in bytes
        """
        return self.get_length() * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    def open(self, file_mode: t.Optional[str] = None) -> DOS11File:
        raise OSError(errno.EINVAL, "Invalid operation on directory")

    def delete(self) -> bool:
        # Delete all entries in the UFD
        for entry in self.iterdir():
            if not entry.delete():
                raise OSError(errno.EIO, os.strerror(errno.EIO))
        # Free space
        bitmap = self.mfd_block.fs.read_bitmap()
        bitmap.clear_bit(self.ufd_block)
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

    @property
    def fs(self) -> "DOS11Filesystem":
        return self.mfd_block.fs

    def __str__(self) -> str:
        return f"{self.uic} ufd_block={self.ufd_block} num_words={self.num_words} zero={self.zero}"


class AbstractMasterFileDirectoryBlock:
    """
    DOS-11/XXDP+ Master File Directory Block
    """

    fs: "DOS11Filesystem"
    # Master File Directory Block entries
    entries_list: t.List["MasterFileDirectoryEntry"] = []


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
        buffer = self.fs.read_block(self.block_number)
        if not buffer:
            raise OSError(errno.EIO, f"Failed to read block {self.block_number}")
        self.next_block_number = bytes_to_word(buffer, 0)  # link to next MFD
        self.entries_list = []
        for position in range(2, BLOCK_SIZE - MFD_ENTRY_SIZE, MFD_ENTRY_SIZE):
            entry = MasterFileDirectoryEntry.read(self, buffer, position)
            self.entries_list.append(entry)
        return self

    def write(self) -> None:
        """
        Write a Master File Directory Block to disk
        """
        buffer = bytearray(BLOCK_SIZE)
        # Write the next block number to the buffer
        struct.pack_into("<H", buffer, 0, self.next_block_number)
        # Write each directory entry to the buffer
        for i, entry in enumerate(self.entries_list):
            position = 2 + i * MFD_ENTRY_SIZE
            entry.write_buffer(buffer, position)
        # Write the buffer to the disk
        self.fs.write_block(buffer, self.block_number)

    def get_empty_entry(self) -> t.Optional["MasterFileDirectoryEntry"]:
        """
        Get the first empty directory entry
        """
        for entry in self.entries_list:
            if entry.is_empty:
                return entry
        return None


class XXDPMasterFileDirectoryBlock(AbstractMasterFileDirectoryBlock):
    """
    XXDP Master File Directory

    XXDP has only one UFD in the MFD
    """

    def __init__(self, fs: "DOS11Filesystem", ufd_block: int):
        self.fs = fs
        entry = MasterFileDirectoryEntry(self)
        entry.ufd_block = ufd_block
        entry.uic = self.fs.uic
        entry.num_words = UFD_ENTRY_SIZE // 2
        self.entries_list = [entry]


class DOS11Filesystem(AbstractFilesystem, BlockDevice):
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

    uic: UIC  # current User Identification Code
    xxdp: bool = False  # MFD Variety #2 (XXDP+)
    dectape: bool = False  # DECtape format
    bitmap_start_block: int = 0

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        self.uic = DEFAULT_UIC
        if strict:
            # Check if the used blocks are in the bitmap
            blocks = [mfd.ufd_block for mfd in self.read_mfd_entries()]
            if not self.bitmap_start_block:
                raise OSError(errno.EIO, "Failed to read MFD block")
            bitmap = self.read_bitmap()
            for block in blocks:
                if not bitmap.get_bit(block):
                    raise OSError(errno.EIO, f"Block {block} is not in the bitmap")
            if not bitmap.get_bit(self.bitmap_start_block):
                raise OSError(errno.EIO, f"Block {self.bitmap_start_block} is not in the bitmap")
        return self

    def read_mfd_entries(
        self,
        mfd_block: int = MFD_BLOCK,
        uic: UIC = ANY_UIC,
    ) -> t.Iterator["MasterFileDirectoryEntry"]:
        """Read Master File Directory entries"""
        for mfd in self.read_mfd(mfd_block=mfd_block):
            for entry in mfd.entries_list:
                if not entry.is_empty and uic.match(entry.uic):  # Filter by UIC
                    yield entry

    def read_mfd(
        self,
        mfd_block: int = MFD_BLOCK,
    ) -> t.Iterator["AbstractMasterFileDirectoryBlock"]:
        """
        Read Master File Directory Block 1

        MFD Block 1:

              +-------------------------------------+
              |        Block # of MFD Block 2       |
              +-------------------------------------+
              |           Interleave factor         |
              +-------------------------------------+
              |         Bitmap start block #        |
              +-------------------------------------+
              | Bitmap block                      1 |
              | .                                   |
              | .                                 n |
              +-------------------------------------+
              |                    0                |
              +-------------------------------------+
              |                                     |


        """
        # Check DECtape format
        self.dectape = False
        t = self.read_block(DECTAPE_MFD1_BLOCK)
        if t:
            (
                mfd2,  # Next MFD block
                _,  # Interleave factor
                self.bitmap_start_block,  # Bitmap start block
            ) = struct.unpack_from(MFD_BLOCK_FORMAT, t)
            if mfd2 == DECTAPE_MFD2_BLOCK:
                tmp = self.read_block(mfd2)
                mfd3 = bytes_to_word(tmp[0:2])  # 0, DECtape has only 2 MFD
                ufd1 = bytes_to_word(tmp[4:6])  # 0o102, First UFD
                self.dectape = (mfd3 == 0) and (ufd1 == DECTAPE_UFD1_BLOCK)

        if not self.dectape:
            t = self.read_block(mfd_block)
            if not t:
                raise OSError(errno.EIO, f"Failed to read block {mfd_block}")
            (
                mfd2,  # Next MFD block
                _,  # Interleave factor
                self.bitmap_start_block,  # Bitmap start block
            ) = struct.unpack_from(MFD_BLOCK_FORMAT, t)

        if mfd2 != 0:  # MFD Variety #1 (DOS-11)
            mfd_block = mfd2
            while mfd_block:
                mfd = MasterFileDirectoryBlock.read(self, mfd_block)
                mfd_block = mfd.next_block_number
                yield mfd
        else:  # MFD Variety #2 (XXDP+)
            self.xxdp = True
            (
                _,  # Zero
                ufd_block,  # First UFD
                _,  # Number of UFD
                self.bitmap_start_block,  # Bitmap start block
            ) = struct.unpack_from(MFD_BLOCK_FORMAT_V2, t)
            yield XXDPMasterFileDirectoryBlock(self, ufd_block)

    def read_bitmap(self) -> DOS11Bitmap:
        bitmap = DOS11Bitmap.read(self, self.bitmap_start_block)
        return bitmap

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,  # expand directories
        wildcard: bool = True,
        uic: t.Optional[UIC] = None,
    ) -> t.Iterator["DOS11DirectoryEntry"]:
        if uic is None:
            uic = self.uic
        uic, filename_pattern = dos11_split_fullname(fullname=pattern, wildcard=wildcard, uic=uic)
        if pattern and not filename_pattern and not expand:
            # If expand is False, check if the pattern is an UIC
            try:
                uic = UIC.from_str(pattern)
                for mfd_block in self.read_mfd():
                    for entry in mfd_block.entries_list:
                        if not entry.is_empty and uic.match(entry.uic):
                            yield entry  # type: ignore
                return
            except Exception:
                pass
        for mfd in self.read_mfd_entries(uic=uic):
            yield from mfd.iterdir(pattern=filename_pattern, include_all=include_all, wildcard=wildcard)

    @property
    def entries_list(self) -> t.Iterator["DOS11DirectoryEntry"]:
        for mfd in self.read_mfd_entries(uic=self.uic):
            yield from mfd.iterdir()

    def get_file_entry(self, fullname: str) -> DOS11DirectoryEntry:
        """
        Get the directory entry for a file
        """
        fullname = dos11_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        uic, basename = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
        try:
            return next(self.filter_entries_list(basename, wildcard=False, uic=uic))
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
        block_size = BLOCK_SIZE if dos11_get_file_type_id(file_type) == CONTIGUOUS_FILE_TYPE else LINKED_FILE_BLOCK_SIZE
        number_of_blocks = int(math.ceil(len(content) * 1.0 / block_size))
        entry = self.create_file(
            fullname=fullname,
            number_of_blocks=number_of_blocks,
            creation_date=creation_date,
            file_type=file_type,
            protection_code=protection_code,
        )
        if entry is not None:
            f = DOS11File(entry)
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
        protection_code: int = DEFAULT_PROTECTION_CODE,
    ) -> t.Optional[DOS11DirectoryEntry]:
        """
        Create a new file with a given length in number of blocks
        """
        contiguous = dos11_get_file_type_id(file_type) == CONTIGUOUS_FILE_TYPE
        # Delete the existing file
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass
        # Get the MFD entry for the target UIC
        uic, basename = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
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
            filename, extension = basename.split(".", 1)  # type: ignore
        except Exception:
            filename = basename
            extension = ""
        new_entry.filename = filename
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
    ) -> t.Optional["MasterFileDirectoryEntry"]:
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
        for mfd in self.read_mfd():  # type: ignore
            entry: MasterFileDirectoryEntry = mfd.get_empty_entry()  # type: ignore
            if entry is not None:
                found = True
                break
        if not found:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        # Create a new UFD block
        bitmap = self.read_bitmap()
        blocks = bitmap.allocate(1)
        bitmap.write()
        # Write the new entry
        entry.uic = uic
        entry.ufd_block = blocks[0]
        entry.num_words = UFD_ENTRY_SIZE // 2
        mfd.write()
        return entry

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        if options.get("uic"):
            # Listing of all UIC
            sys.stdout.write(f"{volume_id}:\n\n")
            for mfd in self.read_mfd_entries(uic=ANY_UIC):
                sys.stdout.write(f"{mfd.uic.to_wide_str()}\n")
            return
        files = 0
        blocks = 0
        i = 0
        uic, pattern = dos11_split_fullname(fullname=pattern, wildcard=True, uic=self.uic)
        if not options.get("brief"):
            if self.xxdp:
                sys.stdout.write("ENTRY# FILNAM.EXT        DATE          LENGTH  START\n")
            else:
                dt = date.today().strftime('%y-%b-%d').upper()
                sys.stdout.write(f"DIRECTORY {volume_id}: {uic}\n\n{dt}\n\n")
        for x in self.filter_entries_list(pattern, uic=uic, include_all=True, wildcard=True):
            if x.is_empty:
                continue
            i = i + 1
            fullname = x.is_empty and x.filename or "%-6s.%-3s" % (x.filename, x.extension)
            if options.get("brief"):
                # Lists only file names and file types
                sys.stdout.write(f"{fullname}\n")
                continue
            creation_date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or ""
            attr = "C" if x.contiguous else ""
            if self.xxdp:
                sys.stdout.write(f"{i:6} {fullname:>10s} {creation_date:>14s} {x.length:>10d}    {x.start_block:06o}\n")
            else:
                uic_str = x.uic.to_wide_str() if uic.has_wildcard else ""
                sys.stdout.write(
                    f"{fullname:>10s} {x.length:>5d}{attr:1} {creation_date:>9s} <{x.protection_code:03o}> {uic_str}\n"
                )
            blocks += x.length
            files += 1
        if options.get("brief") or self.xxdp:
            return
        sys.stdout.write("\n")
        sys.stdout.write(f"TOTL BLKS: {blocks:5}\n")
        sys.stdout.write(f"TOTL FILES: {files:4}\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if arg:
            self.dump(arg)
        else:
            for mfd in self.read_mfd_entries():
                for ufd_block in mfd.read_ufd_blocks():
                    sys.stdout.write(f"{ufd_block}\n")

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
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
        """
        Get the current User Identification Code
        """
        return str(self.uic)

    def get_types(self) -> t.List[str]:
        """
        Get the list of the supported file types
        """
        return list(FILE_TYPES.values())

    def __str__(self) -> str:
        return str(self.f)
