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
import sys
import typing as t
from datetime import date

from ..abstract import XFERXPath, AbstractFile, AbstractFilesystem
from ..commons import ASCII, IMAGE, READ_FILE_FULL, filename_match
from ..device.abstract import AbstractDevice
from ..device.block_18bit import BlockDevice18Bit, from_18bit_words_to_bytes, from_bytes_to_18bit_words
from .adssfs import (
    DECTAPE_BLOCKS,
    WORDS_PER_BLOCK,
    ADSSFilesystem,
    adss_canonical_filename,
    ascii_to_sixbit,
    oct_dump,
    sixbit_to_ascii,
)
from .iops import (
    decode_block_format,
    encode_block_format,
)

__all__ = [
    "DOS15Path",
    "DOS15File",
    "DOS15Filesystem",
    "date_to_dos15",
    "dos15_to_date",
]


# A filename is a string of up to six alphanumeric characters.
# Any printing character in the ASCII set can be used with the exception of:
# " ", ":", ";", "," "(", ")"
#
# Pag 115
# https://bitsavers.trailing-edge.com/pdf/dec/pdp15/DEC-15-YWZA-D_PDP-15_Utility_Programs_196910.pdf

# Pag 87
# https://bitsavers.org/pdf/dec/pdp15/DEC-15-ODFFA-B-D_DOS-15_System_Manual_197408.pdf

RF_MFD_BLOCK = 0o1777  # MFD block number on RF disk
RP_MFD_BLOCK = 0o47040  # MFD block number on RP disk
SAT_BITMAP_FIRST_WORD = 3  # First word of the bitmap in the SAT block
SAT_ENTRIES_PER_BLOCK = 250  # Number of entries in a SAT block
WORDS_PER_LINKED_BLOCK = WORDS_PER_BLOCK - 2  # Number of words in a linked block


def dos15_to_date(val: int) -> t.Optional[date]:
    """
    Translate DOS-15 date to Python date

    Month (bits 0-5), Day (bits 6-11), Year (bits 12-17 module 1970)
    """
    if val == 0:
        return None
    month = val >> 12
    day = (val >> 6) & 0o37
    year = (val & 0o37) + 1970
    try:
        return date(year, month, day)
    except ValueError:
        return None


def date_to_dos15(val: t.Optional[date]) -> int:
    """
    Translate Python date to DOS-15 date
    """
    if val is None:
        return 0
    return (val.month << 12) | (val.day << 6) | ((val.year - 1970) & 0o37)


def dos15_split_fullname(
    uic: t.Optional[str], fullname: t.Optional[str], wildcard: bool = True
) -> t.Tuple[t.Optional[str], t.Optional[str]]:
    """
    Split a fullname into UIC and filename

    Filename and extension are separated by a space or by a semicolon.
    The UIC is optional, if not specified the default UIC is used.

    Example of valid name ar:
    .LOAD;BIN
    [TMP]LPA;SRC
    [UIC;FILENAM;EXT
    [UIC]FILENAM EXT
    """
    if not fullname:
        return uic, fullname
    if fullname.startswith("["):  # UIC is specified
        end = fullname.find("]")
        if end == -1:
            raise ValueError(f"Invalid filename {fullname}")
        uic = fullname[1:end].upper()
        fullname = fullname[end + 1 :]
    fullname = fullname.replace(" ", ";")
    if fullname:
        fullname = adss_canonical_filename(fullname, wildcard=wildcard)
    return uic, fullname


class DOS15File(AbstractFile):
    entry: "DOS15Path"
    file_mode: str  # ASCII of IMAGE

    def __init__(self, entry: "DOS15Path", file_mode: t.Optional[str] = None):
        self.entry = entry
        self.file_mode = file_mode or IMAGE

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        return from_18bit_words_to_bytes(self.read_words_block(block_number, number_of_blocks), self.file_mode)

    def read_words_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> t.List[int]:
        """
        Read block(s) of words from the file

        Exclude the last two words (previous/next block number)
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
        words: t.List[int] = []
        for i, next_block_number in enumerate(self.entry.get_blocks()):
            if i >= block_number:
                # Exclude the last two words (previous/next block number)
                data = self.entry.fs.read_words_block(next_block_number)[:-2]
                words.extend(data)
                number_of_blocks -= 1
                if number_of_blocks == 0:
                    break
        return words

    def write_block(
        self,
        buffer: t.Union[bytes, bytearray],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        """
        words = from_bytes_to_18bit_words(buffer, self.file_mode)
        self.write_words_block(words, block_number, number_of_blocks)

    def write_words_block(
        self,
        words: t.List[int],
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
            or block_number + number_of_blocks > self.entry.get_length()
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        blocks = self.entry.get_blocks()
        for i in range(block_number, block_number + number_of_blocks):
            previous_block_number = blocks[i - 1] if i > 0 else 0o777777
            next_block_number = blocks[i + 1] if i < len(blocks) - 1 else 0o777777
            data = words[:WORDS_PER_LINKED_BLOCK] + [previous_block_number, next_block_number]
            if len(data) < WORDS_PER_LINKED_BLOCK:
                data += [0] * (WORDS_PER_LINKED_BLOCK - len(data))
            self.entry.fs.write_words_block(blocks[i], data)
            words = words[WORDS_PER_LINKED_BLOCK:]  # Remove the written words

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return self.entry.get_length()

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.get_length() * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return WORDS_PER_LINKED_BLOCK * 3

    def __str__(self) -> str:
        return self.entry.fullname


class DOS15Path(XFERXPath):
    """
    Directory Entry
    ---------------

    Word

        +-----------------------------------+
      0 | File name                         |
      1 |                                   |
        +-----------------------------------+
      2 | Extension                         |
        +---------------+-------------------+
      3 | Truncated bit | First block       |
        +---+-----------+-------------------+
      4 | ? | Number of blocks              |
        +---+-------------------------------+
      5 | First Retrieval Information Block |
        +-------+---------------------------+
      6 | Prot. | RIB first word position   |
        +-------+---------------------------+
      7 | Creation date                     |
        +-----------------------------------+

    Pag 87
    https://bitsavers.org/pdf/dec/pdp15/DEC-15-ODFFA-B-D_DOS-15_System_Manual_197408.pdf
    """

    fs: "DOS15Filesystem"
    ufd: "UserFileDirectory"
    file_number: int = 0  # File number in the directory
    stem: str  # Filename
    extension: str = ""  # File extension
    is_truncated: bool = False
    block_number: int = 0  # First file block number
    _u4: int = 0  # Unknown
    length: int  # Number of blocks in the file
    protection_code: int = 0  # Protection code
    rib: int  # RIB first block number
    rib_position: int = 0  # Position in RIB block
    raw_creation_date: int

    def __init__(self, ufd: "UserFileDirectory"):
        self.fs = ufd.fs
        self.ufd = ufd

    @classmethod
    def read(cls, ufd: "UserFileDirectory", words: t.List[int], file_number: int, position: int) -> "DOS15Path":
        self = cls(ufd)
        self.file_number = file_number
        self.stem = sixbit_to_ascii(words[position]) + sixbit_to_ascii(words[position + 1])
        self.extension = sixbit_to_ascii(words[position + 2])
        self.block_number = words[position + 3] & 0o377777  # First block number
        self.is_truncated = bool(words[position + 3] >> 17)  # Truncated bit
        self._u4 = words[position + 4] >> 16  # Unknown
        self.length = words[position + 4] & 0o377777  # Number of blocks in the file (first 2 bit are undocumented)
        self.rib = words[position + 5]
        self.protection_code = words[position + 6] >> 16  # Protection code
        self.rib_position = words[position + 6] & 0o177777  # RIB first word position
        self.raw_creation_date = words[position + 7]
        return self

    def to_words(self) -> t.List[int]:
        """
        Dump the directory entry to words
        """
        return [
            ascii_to_sixbit(self.stem[:3]),
            ascii_to_sixbit(self.stem[3:6]),
            ascii_to_sixbit(self.extension),
            (int(self.is_truncated) << 17) | self.block_number,
            (self._u4 << 16) | self.length,
            self.rib,
            (self.protection_code << 16) | self.rib_position,
            self.raw_creation_date,
        ]

    def is_dir(self) -> bool:
        return False

    @property
    def parent(self) -> "UserFileDirectory":
        """Parent directory"""
        return self.ufd

    @property
    def is_empty(self) -> bool:
        """
        Is the file active?
        """
        return self.block_number == 0 or self.is_truncated

    @property
    def fullname(self) -> str:
        return f"[{self.uic}]{self.name}"

    @property
    def uic(self) -> str:
        return self.ufd.uic

    @property
    def name(self) -> str:
        return f"{self.stem};{self.extension}"

    def get_blocks(self) -> t.List[int]:
        """
        Get the blocks used by the file
        """
        next_block_number = self.rib
        length = self.length  # Length in blocks
        position = self.rib_position
        blocks = []
        while next_block_number != 0o777777 and length > 0:
            rib = RetrievalInformationBlock.read(self, next_block_number)
            blocks = rib.data_blocks[position : position + length]
            length -= len(blocks)
            next_block_number = rib.next_block_number
            position = 0
        return blocks

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return self.length

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get file size in bytes
        """
        return self.get_length() * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return WORDS_PER_LINKED_BLOCK * 3

    @property
    def creation_date(self) -> t.Optional[date]:
        return dos15_to_date(self.raw_creation_date)

    def delete(self) -> bool:
        """
        Delete the directory entry
        """
        # Clear the data block references in the RIB
        data_blocks = self.get_blocks()
        rib_blocks = []
        rib_block_number = self.rib
        remaining = self.length
        position = self.rib_position
        while rib_block_number != 0o777777:
            rib = RetrievalInformationBlock.read(self, rib_block_number)
            rib_blocks.append(rib.block_number)
            number_of_blocks = min(remaining, len(rib.data_blocks))
            rib.zero_references(position, number_of_blocks)
            rib.write()
            remaining -= number_of_blocks
            rib_block_number = rib.next_block_number
            position = 0
            if remaining == 0:
                break

        # Free the data blocks in the bitmap
        bitmap = self.fs.read_bitmap()
        for block_number in data_blocks:
            if block_number not in rib_blocks:
                bitmap.set_free(block_number)
        bitmap.write()

        # Remove the entry from the UFD
        entries = [entry for entry in self.ufd.iterdir(include_all=True) if entry.file_number != self.file_number]
        self.ufd.write(entries)
        return True

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> DOS15File:
        """
        Open a file
        """
        return DOS15File(self, file_mode)

    def read_bytes(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> bytes:
        """Get the content of the file"""
        with self.open() as f:
            length = self.get_length()  # Number of blocks in the file
            result = bytearray()
            for i in range(length):
                words = f.read_words_block(i)
                result += decode_block_format(words)
        return bytes(result)

    def __str__(self) -> str:
        return (
            f"{self.fullname:<17} "
            f"{self.block_number:>6} "
            f"{self.length:>6} "
            f"{self.protection_code:>5} "
            f"{self.rib:>6}  "
            f"{self.rib_position:>6}    "
            f"{self.creation_date} "
            f"{'N' if self.is_empty else 'Y'}"
        )


class RetrievalInformationBlock:
    """
    Retrieval Information Block (RIB)
    ---------------------------------

    The RIB associates the logical sequence of blocks in the file with
    the physical locations of the blocks on the disk.
    The RIB is a linked list of blocks, each containing a number of entries.
    It can be fit the last data block of the file, in which case the RIB is not a separate block and
    does not start at the beginning of the block.
    In this case, the first word of the RIB is not the first word of the physical block
    and the RIB is not a complete block, but only a portion of the block.

    Word

        +-----------------------------------+
      0 | Number of blocks                  |
        +-----------------------------------+
      1 | Data block 1                      |
        //                                  /
      n | Data block n                      |
        +-----------------------------------+
    254 | Previous RIB block number         |
        +-----------------------------------+
    255 | Next RIB block number             |
        +-----------------------------------+

    Pag 83
    https://bitsavers.org/pdf/dec/pdp15/DEC-15-ODFFA-A_DOS15_SysMan.pdf
    """

    entry: "DOS15Path"
    # Total number of blocks described by this RIB
    number_of_blocks: int = 0
    # List of entries in the UFD block
    data_blocks: t.List[int]
    # Block number of this Master File Directory block
    block_number: int = 0
    # Block number of the next Master File Directory block
    next_block_number: int = 0
    # Block number of the previous Master File Directory block
    previous_block_number: int = 0

    def __init__(self, entry: "DOS15Path"):
        self.entry = entry

    @classmethod
    def read(cls, entry: "DOS15Path", block_number: int) -> "RetrievalInformationBlock":
        self = cls(entry)
        self.block_number = block_number
        # Read the block
        words = self.fs.read_words_block(block_number)
        self.number_of_blocks = words[0]  # Number of blocks in the RIB
        self.data_blocks = words[1 : self.number_of_blocks + 1]  # Data blocks
        self.previous_block_number = words[-2]  # Pointer to previous MFD block
        self.next_block_number = words[-1]  # Pointer to next MFD block
        return self

    def write(self) -> None:
        """
        Write the RIB block
        """
        words: t.List[int] = [self.number_of_blocks] + self.data_blocks
        words = (words + [0] * WORDS_PER_BLOCK)[:WORDS_PER_LINKED_BLOCK]
        words.extend([self.previous_block_number, self.next_block_number])
        self.fs.write_words_block(self.block_number, words)

    def zero_references(self, position: int, number_of_blocks: int) -> None:
        """
        Clear data-block references belonging to a file
        """
        self.data_blocks[position : position + number_of_blocks] = [0] * number_of_blocks

    @property
    def fs(self) -> "DOS15Filesystem":
        return self.entry.fs


class UserFileDirectoryBlock(object):
    """
    User File Directory Block
    -------------------------

    Pag 81
    https://bitsavers.org/pdf/dec/pdp15/DEC-15-ODFFA-A_DOS15_SysMan.pdf
    """

    ufd: "UserFileDirectory"
    # List of entries in the UFD block
    entries_list: t.List[DOS15Path]
    # Block number of this Master File Directory block
    block_number: int = 0
    # Block number of the next Master File Directory block
    next_block_number: int = 0
    # Block number of the previous Master File Directory block
    previous_block_number: int = 0

    def __init__(self, ufd: "UserFileDirectory"):
        self.ufd = ufd

    @classmethod
    def read(cls, ufd: "UserFileDirectory", block_number: int) -> "UserFileDirectoryBlock":
        self = cls(ufd)
        self.block_number = block_number
        # Read the UFD block
        self.entries_list = []
        words = self.fs.read_words_block(block_number)
        for i, position in enumerate(range(0, len(words) - 8, 8)):
            entry = DOS15Path.read(ufd, words, i, position)
            if not entry.is_empty:
                self.entries_list.append(entry)
        self.previous_block_number = words[-2]  # Pointer to previous MFD block
        self.next_block_number = words[-1]  # Pointer to next MFD block
        return self

    def write(self) -> None:
        """
        Write all directory entries and the block links.
        """
        words: t.List[int] = []
        for entry in self.entries_list:
            words.extend(entry.to_words())
        words = (words + [0] * WORDS_PER_BLOCK)[:WORDS_PER_LINKED_BLOCK]
        words.extend([self.previous_block_number, self.next_block_number])
        self.fs.write_words_block(self.block_number, words)

    @property
    def uic(self) -> str:
        return self.ufd.uic

    @property
    def fs(self) -> "DOS15Filesystem":
        return self.ufd.fs


class UserFileDirectory(XFERXPath):
    """
    User File Directory
    -------------------

    Each UFD is named by a unique three character User Identification Code (UIC)

    Word

        +-----------------------------------+
      0 | UIC                               |
        +-----------------------------------+
      1 | UFD First Block                   |
        +-----------+-----------------------+
      2 | Prot. bit | UFD Entry Size        |
        +-----------+-----------------------+
      3 | Unused                            |
        +-----------------------------------+

    Pag 81
    https://bitsavers.org/pdf/dec/pdp15/DEC-15-ODFFA-A_DOS15_SysMan.pdf
    """

    fs: "DOS15Filesystem"
    mfd_block: "MasterFileDirectoryBlock"
    uic: str  # User Identification Code
    ufd_block_number: int  # Block number of the file
    protected: bool  # Protected bit
    ufd_entry_size: int  # UFD entry size in bits

    def __init__(self, mfd_block: "MasterFileDirectoryBlock"):
        self.fs = mfd_block.fs
        self.mfd_block = mfd_block

    @classmethod
    def read(cls, mfd_block: "MasterFileDirectoryBlock", words: t.List[int], position: int) -> "UserFileDirectory":
        """
        Read a Master File Directory entry from the MFD block
        """
        self = cls(mfd_block)
        self.uic = sixbit_to_ascii(words[position]).strip("\0")
        self.ufd_block_number = words[position + 1]
        self.protected = bool(words[position + 2] >> 17)
        self.ufd_entry_size = words[position + 2] & ~0o400000
        return self

    @property
    def length(self) -> int:
        """Length in blocks"""
        return 0

    def is_dir(self) -> bool:
        return True

    @property
    def parent(self) -> "MasterFileDirectory":
        """Parent directory"""
        return self.fs.get_root()

    @property
    def is_empty(self) -> bool:
        """
        Check if the entry is empty
        """
        return self.uic == ""

    @property
    def fullname(self) -> str:
        return f"{self.name}"

    @property
    def name(self) -> str:
        return f"[{self.uic}]"

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return WORDS_PER_LINKED_BLOCK * 3

    def delete(self) -> bool:
        """
        Delete the directory entry
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> DOS15File:
        """
        Open a file
        """
        raise OSError(errno.EISDIR, os.strerror(errno.EISDIR))

    def read_ufd_blocks(self) -> t.Iterator["UserFileDirectoryBlock"]:
        """Read User File Directory blocks"""
        next_block_number = self.ufd_block_number
        while next_block_number != 0o777777:
            ufd_block = UserFileDirectoryBlock.read(self, next_block_number)
            next_block_number = ufd_block.next_block_number
            yield ufd_block

    def write(self, entries: t.Optional[t.List[DOS15Path]] = None) -> None:
        """
        Rewrite all directory entries, growing the UFD when necessary.
        """
        blocks = list(self.read_ufd_blocks())
        if entries is None:
            entries = [entry for block in blocks for entry in block.entries_list]
        entries_per_block = (WORDS_PER_LINKED_BLOCK) // self.ufd_entry_size
        required_blocks = max(1, math.ceil(len(entries) / entries_per_block))

        # If the UFD is empty and there are entries to write, allocate a new block for the UFD
        if self.ufd_block_number == 0o777777 and len(entries) > 0:
            bitmap = self.fs.read_bitmap()
            new_block_number = bitmap.allocate(1)[0]
            new_block = UserFileDirectoryBlock(self)
            new_block.block_number = new_block_number
            new_block.previous_block_number = 0o777777
            new_block.next_block_number = 0o777777
            blocks.append(new_block)
            bitmap.write()
            # Update the MFD block with the new UFD block number
            self.ufd_block_number = new_block_number
            self.mfd_block.write()

        # If there are not enough blocks to hold all entries, allocate new blocks and link them
        if len(blocks) < required_blocks:
            bitmap = self.fs.read_bitmap()
            while len(blocks) < required_blocks:
                new_block_number = bitmap.allocate(1)[0]
                previous_block = blocks[-1]
                previous_block.next_block_number = new_block_number
                new_block = UserFileDirectoryBlock(self)
                new_block.block_number = new_block_number
                new_block.previous_block_number = previous_block.block_number
                new_block.next_block_number = 0o777777
                blocks.append(new_block)
            bitmap.write()

        # Write the entries to the blocks
        for block_number, block in enumerate(blocks):
            block.previous_block_number = blocks[block_number - 1].block_number if block_number else 0o777777
            block.next_block_number = (
                blocks[block_number + 1].block_number if block_number + 1 < len(blocks) else 0o777777
            )
            start = block_number * entries_per_block
            block.entries_list = entries[start : start + entries_per_block]
            block.write()

    def iterdir(
        self,
        pattern: t.Optional[str] = None,
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator[DOS15Path]:
        """
        Iterate over all entries in the User File Directory
        """
        for ufd_block in self.read_ufd_blocks():
            for entry in ufd_block.entries_list:
                if (include_all or not entry.is_empty) and filename_match(entry.name, pattern, wildcard=True):
                    yield entry

    def to_words(self) -> t.List[int]:
        """
        Dump the directory entry to words
        """
        return [
            ascii_to_sixbit(self.uic),
            self.ufd_block_number,
            (int(self.protected) << 17) | self.ufd_entry_size,
            0,
        ]

    def __str__(self) -> str:
        """
        String representation of the Master File Directory entry
        """
        uic = f"[{self.uic}]"
        ufd_block_number = f"{self.ufd_block_number:>6}" if self.ufd_block_number != 0o777777 else "NON"
        return f"{uic:<3}             {ufd_block_number:>6}        {self.protected:>5}"


class MasterFileDirectoryBlock:
    """
    Master File Directory Block
    ---------------------------

    Word

        +-----------------------------------+
      0 | -1                                |
        +-----------------------------------+
      1 | Bad Allocation Table Block        |
        +-----------------------------------+
      2 | System Block                      |
        +------------+----------------------+
      3 | Entry Size | SAT Table Block      |
        +------------+----------------------+
      4 | MFD Entries (4 words each)        |
        /                                   /
        |                                   |
        +-----+-----------------------------+
    252 | Chk | Spooler disk area           |   XVM-DOS only
        +-----+-----------------------------+
    253 | Chk | Spooler starting block      |   XVM-DOS only
        +-----+-----------------------------+
    254 | Previous MFD block                |
        +-----------------------------------+
    255 | Next MFD block                    |
        +-----------------------------------+

    Pag 85
    https://bitsavers.org/pdf/dec/pdp15/DEC-15-ODFFA-A_DOS15_SysMan.pdf

    Pag 110
    https://bitsavers.trailing-edge.com/pdf/dec/pdp15/XVM/DEC-XV-ODSAA-A-D_XVMdosSys.pdf
    """

    fs: "DOS15Filesystem"
    # List of entries in the MFD block
    entries_list: t.List[UserFileDirectory]
    # Bad allocation table first block number
    bad_allocation_table: int
    # System first block number
    system_block_number: int
    # Storage Allocation Table first block number
    sat_block_number: int
    # Block number of this Master File Directory block
    block_number: int = 0
    # MFD entry size
    mfd_entry_size: int = 0
    # Block number of the next Master File Directory block
    next_block_number: int = 0
    # Block number of the previous Master File Directory block
    previous_block_number: int = 0

    def __init__(self, fs: "DOS15Filesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "DOS15Filesystem", block_number: int) -> "MasterFileDirectoryBlock":
        self = cls(fs)
        self.block_number = block_number
        # Read the MFD block
        words = self.fs.read_words_block(RF_MFD_BLOCK)
        if words[0] != 0o777777:
            words = self.fs.read_words_block(RP_MFD_BLOCK)
            assert words[0] == 0o777777
        self.bad_allocation_table = words[1]
        self.system_block_number = words[2]
        # Word 3 contains the MFD entry size (3 bit) and the block number of the first submap
        self.mfd_entry_size = 4  # words[3] >> 15
        self.sat_block_number = words[3] & 0o377777
        self.entries_list = []
        for i in range(4, len(words) - self.mfd_entry_size, self.mfd_entry_size):
            entry = UserFileDirectory.read(self, words, i)
            if not entry.is_empty:
                self.entries_list.append(entry)
        self.previous_block_number = words[-2]  # Pointer to previous MFD block
        self.next_block_number = words[-1]  # Pointer to next MFD block
        return self

    def write(self) -> None:
        """
        Write all directory entries and the block links.
        """
        words: t.List[int] = [0o777777, self.bad_allocation_table, self.system_block_number]
        words.append((self.mfd_entry_size << 15) | self.sat_block_number)
        for entry in self.entries_list:
            words.extend(entry.to_words())
        words = (words + [0] * WORDS_PER_BLOCK)[:WORDS_PER_LINKED_BLOCK]
        words.extend([self.previous_block_number, self.next_block_number])
        self.fs.write_words_block(self.block_number, words)


class MasterFileDirectory(XFERXPath):
    """
    Master File Directory
    ---------------------

    The Master File Directory (MFD) points to each User File Directory (UFD)
    The MFD is a linked list of blocks, each containing a number of entries.

    Pag 81
    https://bitsavers.org/pdf/dec/pdp15/DEC-15-ODFFA-A_DOS15_SysMan.pdf
    """

    fs: "DOS15Filesystem"
    mfd_blocks: t.List[MasterFileDirectoryBlock]  # List of MFD blocks

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
    def parent(self) -> "MasterFileDirectory":
        """Parent directory"""
        return self

    @classmethod
    def read(cls, fs: "DOS15Filesystem") -> "MasterFileDirectory":
        """
        Read the Master File Directory (MFD)
        """
        self = cls(fs)
        self.mfd_blocks = []
        mfd_block_number = RF_MFD_BLOCK
        while mfd_block_number != 0o777777:  # 0o777777 is the end of the MFD
            mfd_block = MasterFileDirectoryBlock.read(self.fs, mfd_block_number)
            mfd_block_number = mfd_block.next_block_number
            self.mfd_blocks.append(mfd_block)
        return self

    @property
    def sat_block_number(self) -> int:
        """
        Return the Storage Allocation Table block number
        """
        return self.mfd_blocks[0].sat_block_number

    def iterdir(self) -> t.Iterator[UserFileDirectory]:
        """
        Iterate over all entries in the Master File Directory
        """
        for mfd_block in self.mfd_blocks:
            yield from mfd_block.entries_list

    def get_entry(self, uic: str) -> t.Optional[UserFileDirectory]:
        for entry in self.iterdir():
            if entry.uic == uic:
                return entry
        return None


class StorageAllocationTables:
    """
    Storage Allocation Tables (SAT)
    -------------------------------

    The disk handlers use a Storage Allocation Table, in order to distinguish
    between allocated and free blocks.
    If more than one physical block is required, the individual blocks are called Submaps.

    Word

        +-----------------------------------+
      0 | Total blocks on disk              |
        +-----------------------------------+
      1 | Blocks in this submap             |
        +-----------------------------------+
      2 | Occupied blocks in this submap    |
        +-----------------------------------+
      3 | Bitmap                            |
        / 250 words                         /
        |                                   |
        +-----------------------------------+
    254 | Previous submap                   |
        +-----------------------------------+
    255 | Next submap                       |
        +-----------------------------------+

    Pag 85
    https://bitsavers.org/pdf/dec/pdp15/DEC-15-ODFFA-A_DOS15_SysMan.pdf
    """

    fs: "DOS15Filesystem"
    total_blocks: int  # Total number of blocks on disk

    blocks: t.List[int]  # SAT Submap block numbers
    bitmaps: t.List[int]

    def __init__(self, fs: "DOS15Filesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "DOS15Filesystem", first_block: int) -> "StorageAllocationTables":
        """
        Read the bitmap blocks
        """
        self = cls(fs)
        self.bitmaps = []
        self.blocks = []
        next_block_number = first_block
        while next_block_number != 0o777777:
            # Read the submap
            self.blocks.append(next_block_number)
            words = self.fs.read_words_block(next_block_number)
            if next_block_number == first_block:
                self.total_blocks = words[0]
            self.bitmaps.extend(words[SAT_BITMAP_FIRST_WORD:253])  # Bitmap words
            next_block_number = words[-1]  # Pointer to next bitmap block
        self.bitmaps = self.bitmaps[: math.ceil(self.total_blocks / 18)]
        return self

    def write(self) -> None:
        """
        Write the bitmap blocks
        """
        bitmap_position = 0
        for block_number in self.blocks:
            words = self.fs.read_words_block(block_number)
            bitmap_words = min(len(self.bitmaps) - bitmap_position, SAT_ENTRIES_PER_BLOCK)
            words[SAT_BITMAP_FIRST_WORD : SAT_BITMAP_FIRST_WORD + bitmap_words] = self.bitmaps[
                bitmap_position : bitmap_position + bitmap_words
            ]
            self.fs.write_words_block(block_number, words)
            bitmap_position += bitmap_words
            if bitmap_position == len(self.bitmaps):
                break

    def is_free(self, bit_index: int) -> bool:
        """
        Check if a block is free
        """
        int_index = bit_index // 18
        bit_position = bit_index % 18
        bit_value = self.bitmaps[int_index]
        return (bit_value & (1 << (17 - bit_position))) == 0

    def set_used(self, bit_index: int) -> None:
        """
        Mark a block as used
        """
        int_index = bit_index // 18
        bit_position = bit_index % 18
        self.bitmaps[int_index] |= 1 << (17 - bit_position)

    def set_free(self, bit_index: int) -> None:
        """
        Mark a block as free
        """
        int_index = bit_index // 18
        bit_position = bit_index % 18
        self.bitmaps[int_index] &= ~(1 << (17 - bit_position))

    def allocate(self, size: int) -> t.List[int]:
        """
        Allocate blocks
        """
        blocks = []
        for block in range(0, self.total_blocks):
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
        return self.total_blocks - self.used()

    def __str__(self) -> str:
        free = self.free()
        used = self.used()
        return f"Free blocks: {free:<6} Used blocks: {used:<6}"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, StorageAllocationTables) and self.bitmaps == other.bitmaps  # type: ignore


class DOS15Filesystem(AbstractFilesystem):
    """
    DOS-15 Filesystem

    +------------+          +--------+
    |    MFD     +--------> |  SAT   |
    |   Master   |          | Master |
    |    File    |          | Bitmap |
    |  Directory |          +--------+
    +-----+------+         +-----------+    +-----------+
          |                |   UFD 1   |    |   RIB 1   |    +---------+
          +--------------> +    User   +--> | Retrieval +--> | Block 1 |
                           |    File   |    |   Block   |    |         |
                           | Directory |    +-^-------+-+    +--^----+-+
                           +-^-------+-+      |       |         |    |
                             |       |        |       |         |    |
                           +-+-------v-+    +-+-------v-+    +--+----v-+
                           |   UFD 2   |    |   RIB 2   |    | Block 2 |
                           +-----------+    +-----------+    +-^-----+-+

    Pag 129
    https://bitsavers.org/pdf/dec/pdp15/PDP-15_System_Software_Handouts_1975.pdf
    """

    fs_name = "dos15"
    fs_description = "PDP-15 DOS-15"
    fs_platforms = ["pdp-9", "pdp-15"]
    fs_entry_metadata = [
        "creation_date",
        "protection_code",
    ]

    uic: t.Optional[str] = None  # current User Identification Code
    dev: BlockDevice18Bit

    def __init__(self, file_or_device: t.Union["AbstractFile", "AbstractDevice"]):
        if isinstance(file_or_device, AbstractFile):
            self.dev = BlockDevice18Bit(file_or_device, words_per_block=WORDS_PER_BLOCK)
        elif isinstance(file_or_device, BlockDevice18Bit):
            self.dev = file_or_device
        else:
            raise OSError(errno.EIO, f"Invalid device type for {self.fs_description} filesystem")

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        strict: t.Union[bool, str] = True,
        **kwargs: t.Union[bool, str],
    ) -> t.Union["DOS15Filesystem", "ADSSFilesystem"]:
        """
        Mount the filesystem from a file or device
        """
        self = cls(file_or_dev)
        blocks = self.get_size() // 3 // 512
        is_dectape = abs(blocks - DECTAPE_BLOCKS) < 4
        if is_dectape:
            return ADSSFilesystem.mount(self.dev, strict=strict, **kwargs)

        self.uic = None
        if strict:
            for ufd in self.get_root().iterdir():
                pass
        return self

    def read_words_block(
        self,
        block_number: int,
    ) -> t.List[int]:
        """
        Read a 256 bytes block as 18bit words
        """
        return self.dev.read_words_block(block_number)

    def write_words_block(
        self,
        block_number: int,
        words: t.List[int],
    ) -> None:
        """
        Write 256 18bit words as a block
        """
        self.dev.write_words_block(block_number, words)

    def read_bitmap(self) -> StorageAllocationTables:
        """
        Read the Storage Allocation Table (SAT)
        """
        mfd = self.get_root()
        return StorageAllocationTables.read(self, mfd.sat_block_number)

    def read_dir_entries(self, uic: t.Optional[str] = None) -> t.Iterator[t.Union[DOS15Path, UserFileDirectory]]:
        """
        Read directory entries
        """
        uic = uic or self.uic
        mfd = self.get_root()
        if uic:
            ufd = mfd.get_entry(uic)
            if ufd:
                yield from ufd.iterdir(include_all=True)
        else:
            yield from mfd.iterdir()

    def get_ufd(self, uic: str) -> UserFileDirectory:
        """
        Get the User File Directory for a given UIC
        """
        mfd = self.get_root()
        for ufd in mfd.iterdir():
            if ufd.uic == uic:
                return ufd
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), uic)

    @property
    def entries_list(self) -> t.Iterator[t.Union[DOS15Path, UserFileDirectory]]:
        yield from self.read_dir_entries()

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
        uic: t.Optional[str] = None,
    ) -> t.Iterator[t.Union[DOS15Path, UserFileDirectory]]:
        uic, pattern = dos15_split_fullname(fullname=pattern, wildcard=wildcard, uic=uic or self.uic)
        if not uic:
            yield from self.get_root().iterdir()
        else:
            try:
                ufd = self.get_ufd(uic)
                if pattern:
                    yield from ufd.iterdir(include_all=include_all, pattern=pattern, wildcard=wildcard)
                elif expand:
                    yield from ufd.iterdir(include_all=include_all)
                else:
                    yield ufd
            except FileNotFoundError:
                pass

    def get_file_entry(self, fullname: str) -> t.Union[DOS15Path, UserFileDirectory]:
        """
        Get the directory entry for a file
        """
        for entry in self.filter_entries_list(fullname, wildcard=False, expand=False):
            return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def get_root(self) -> MasterFileDirectory:
        """
        Get the Master File Directory
        """
        return MasterFileDirectory.read(self)

    def create_file(
        self,
        fullname: str,
        size: int,  # Size in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> DOS15Path:
        """
        Create a new file with a given length in number of blocks
        """
        # If the file already exists, delete it
        try:
            entry: DOS15Path = self.get_file_entry(fullname)  # type: ignore
            entry.delete()
        except FileNotFoundError:
            pass
        uic, fullname = dos15_split_fullname(self.uic, fullname, wildcard=False)  # type: ignore
        if not fullname:
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))
        stem, extension = fullname.split(";", 1)
        if uic is None:
            raise FileNotFoundError(errno.ENOENT, "No UIC specified")
        ufd = self.get_root().get_entry(uic)
        if ufd is None:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), uic)

        # Allocate the data blocks and the RIB blocks
        number_of_data_blocks: t.Optional[int] = metadata.get("number_of_blocks", None)  # type: ignore
        if number_of_data_blocks is None:
            block_size = (WORDS_PER_LINKED_BLOCK) * 3
            number_of_data_blocks = max(1, math.ceil(size / block_size))
        number_of_rib_blocks = math.ceil(number_of_data_blocks / SAT_ENTRIES_PER_BLOCK)
        bitmap = self.read_bitmap()
        allocated = bitmap.allocate(number_of_data_blocks + number_of_rib_blocks)
        data_blocks = allocated[:number_of_data_blocks]
        rib_blocks = allocated[number_of_data_blocks:]
        bitmap.write()

        # Initialize the data blocks
        for index, block_number in enumerate(data_blocks):
            content_words = [0] * (WORDS_PER_LINKED_BLOCK)
            previous_block = data_blocks[index - 1] if index else 0o777777
            next_block = data_blocks[index + 1] if index + 1 < len(data_blocks) else 0o777777
            self.write_words_block(block_number, content_words + [previous_block, next_block])

        # Initialize the RIB blocks
        for index, block_number in enumerate(rib_blocks):
            references = data_blocks[index * (WORDS_PER_BLOCK - 3) : (index + 1) * (WORDS_PER_BLOCK - 3)]
            previous_block = rib_blocks[index - 1] if index else 0o777777
            next_block = rib_blocks[index + 1] if index + 1 < len(rib_blocks) else 0o777777
            words = [len(references)] + references
            words += [0] * (WORDS_PER_LINKED_BLOCK - len(words))
            words += [previous_block, next_block]
            self.write_words_block(block_number, words)

        # Update the UFD with the new entry
        entries_list = list(ufd.iterdir(include_all=True))
        entry = DOS15Path(ufd)
        entry.file_number = max((item.file_number for item in entries_list), default=-1) + 1
        entry.stem = stem
        entry.extension = extension
        entry.block_number = data_blocks[0]
        entry.length = number_of_data_blocks
        entry.rib = rib_blocks[0]
        entry.rib_position = 0
        entry.raw_creation_date = date_to_dos15((metadata or {}).get("creation_date", date.today()))
        entry.protection_code = (metadata or {}).get("protection_code", 0)
        ufd.write(entries_list + [entry])
        return entry

    def write_bytes(
        self,
        fullname: str,
        content: t.Union[bytes, bytearray],
        fork: t.Optional[str] = None,
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        """
        Write bytes to a file, creating it if necessary
        """
        metadata = metadata or {}
        file_mode = file_mode or IMAGE
        blocks_content = list(encode_block_format(content, file_mode, words_per_block=WORDS_PER_LINKED_BLOCK))
        metadata["number_of_blocks"] = len(blocks_content)
        # Create the file entry
        entry = self.create_file(fullname, len(content), metadata)
        assert entry.get_length() == len(blocks_content)
        # Write the file content
        with entry.open() as f:
            for i, block_content in enumerate(blocks_content):
                f.write_words_block(block_content, i)

    def show_accounts(self, volume_id: str, options: t.Dict[str, bool]) -> None:
        """
        Listing of all UIC

        For each UFD entry, display the following information:
        - UFD identifier
        - Number of first device block occupied by ufd (octal)
        - Protected (0=protected, 1=unprotected)
        - Number of files in the UFD (octal)
        - Number of blocks occupied by the files in the UFD (octal)

        Pag 33
        https://bitsavers.org/pdf/dec/pdp15/DEC-15-UPIPA-A-D_PIP_DOS_Monitor_Utility_Program_197408.pdf
        """
        buf = io.StringIO()
        mfd = self.get_root()
        total_files = 0
        total_blocks = 0
        for ufd in mfd.iterdir():
            block_number = f"{ufd.ufd_block_number:o}" if ufd.ufd_block_number != 0o777777 else "NON"
            protected = "1" if ufd.protected else "0"
            files = 0
            blocks = 0
            for entry in ufd.iterdir(include_all=False):
                files += 1
                blocks += entry.get_length()
            buf.write(f" {ufd.uic} {block_number:>6}({protected}) {files:>6o}{blocks:>6o}\n")
            total_files += files
            total_blocks += blocks

        bitmap = self.read_bitmap()
        entries = list(self.filter_entries_list("*", uic="BNK", wildcard=True))
        used = sum(entry.get_length() for entry in entries if not entry.is_empty)

        dt = date.today().strftime('%y-%b-%d').upper()
        sys.stdout.write(f"     {dt}\n")
        sys.stdout.write(" MFD DIRECTORY LISTING\n")
        sys.stdout.write(f" {bitmap.free():>6o} FREE BLKS\n")
        sys.stdout.write(f" {total_files:>6o} USER FILES\n")
        sys.stdout.write(f" {total_blocks:>6o} USER BLKS\n")
        sys.stdout.write(buf.getvalue())

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        For each file, display the following information:
        - Filename and extension
        - Number of blocks in the file (octal)
        - Creation date (dd-mmm-yy)

        Pag 32
        https://bitsavers.org/pdf/dec/pdp15/DEC-15-UPIPA-A-D_PIP_DOS_Monitor_Utility_Program_197408.pdf
        """
        uic, pattern = dos15_split_fullname(fullname=pattern, wildcard=True, uic=self.uic)
        if not uic:
            # If no UIC is specified, show the accounts
            self.show_accounts(volume_id, options)
            return

        ufd = self.get_ufd(uic)
        if not pattern:
            pattern = "*;*"
        entries = list(entry for entry in ufd.iterdir(pattern=pattern, include_all=False, wildcard=True))

        bitmap = self.read_bitmap()
        dt = date.today().strftime('%y-%b-%d').upper()
        used = sum(entry.get_length() for entry in entries if not entry.is_empty)
        sys.stdout.write(f"     {dt}\n")
        sys.stdout.write(f" DIRECTORY LISTING  ({uic})\n")
        sys.stdout.write(f" {bitmap.free():>6o} FREE BLKS\n")
        sys.stdout.write(f" {len(entries):>6o} USER FILES\n")
        sys.stdout.write(f" {used:>6o} USER BLKS\n")

        for x in entries:
            if not x.is_empty:
                creation_date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or ""
                sys.stdout.write(f" {x.stem:<6} {x.extension:<3}  {x.length:>6o}  {creation_date}\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if options.get("bitmap"):
            # Display the bitmap
            bitmap = self.read_bitmap()
            for i in range(0, bitmap.total_blocks):
                sys.stdout.write(f"{i:>4d} {'[ ]' if bitmap.is_free(i) else '[X]'}  ")
                if i % 16 == 15:
                    sys.stdout.write("\n")
            sys.stdout.write(f"\n\nUsed blocks: {bitmap.used()}\n")
            sys.stdout.write(f"Free blocks: {bitmap.free()}\n")
        elif arg:
            uic, fullname = dos15_split_fullname(self.uic, arg, wildcard=False)
            if uic is None:
                raise FileNotFoundError(errno.ENOENT, "No UIC specified")
            if not fullname:
                mfd = self.get_root()
                ufd = mfd.get_entry(uic)
                if ufd:
                    sys.stdout.write(f"UIC:                      {ufd.uic}\n")
                    sys.stdout.write(f"First block number:       {ufd.ufd_block_number}\n")
                    sys.stdout.write(f"Protection code:          {ufd.protected}\n")
                    self.examine(f"[{ufd.uic}]*;*", options)
            else:
                # Display the file entry
                entries = self.filter_entries_list(arg, wildcard=True)
                for entry in entries:
                    sys.stdout.write(f"UIC:                      {entry.uic}\n")
                    sys.stdout.write(f"Active:                   {'N' if entry.is_empty else 'Y'}\n")
                    sys.stdout.write(f"Size:                     {entry.get_length()} blocks\n")
                    sys.stdout.write(f"Creation date:            {entry.creation_date}\n")
                    if isinstance(entry, DOS15Path):
                        sys.stdout.write(f"First block number:       {entry.block_number}\n")
                        sys.stdout.write(f"File number:              {entry.file_number}\n")
                        sys.stdout.write(f"Filename:                 {entry.stem}\n")
                        sys.stdout.write(f"Extension:                {entry.extension}\n")
                        sys.stdout.write(f"RIB:                      {entry.rib}\n")
                        sys.stdout.write(f"RIB position:             {entry.rib_position}\n")
                        sys.stdout.write(f"Protection code:          {entry.protection_code}\n")
                        blocks = str(list(entry.get_blocks()))
                        sys.stdout.write(f"Blocks:                   {blocks}\n")
                    sys.stdout.write("\n")
        else:
            # Display the directory entries
            sys.stdout.write("Filename           Block   Size   Prt    RIB RIB pos Creation Date Active\n")
            sys.stdout.write("--------           -----   ----   ---    --- ------- ------------- ------\n")
            full = bool(options.get("full", False))
            mfd = self.get_root()
            for ufd in mfd.iterdir():
                sys.stdout.write(f"{ufd}\n")
                for entry in ufd.iterdir(include_all=True):
                    if full or not entry.is_empty:
                        sys.stdout.write(f"{entry}\n")

    def dump(
        self,
        fullname: t.Optional[str],
        start: t.Optional[int] = None,
        end: t.Optional[int] = None,
        fork: t.Optional[str] = None,
    ) -> None:
        """Dump the content of a file or a range of blocks"""
        if fullname:
            entry = self.get_file_entry(fullname)
            if not isinstance(entry, DOS15Path):
                raise OSError(errno.EISDIR, os.strerror(errno.EISDIR), fullname)
            if start is None:
                start = 0
            blocks = entry.get_blocks()
            if end is None or end > len(blocks) - 1:
                end = entry.get_length() - 1
            for block_number in range(start, end + 1):
                words = self.read_words_block(blocks[block_number])
                sys.stdout.write(f"\nBLOCK NUMBER   {blocks[block_number]:08} ({block_number:08})\n")
                oct_dump(words)
        else:
            if start is None:
                start = 0
                if end is None:  # full disk
                    end = (self.get_size() // WORDS_PER_BLOCK // 4) - 1
            elif end is None:  # one single block
                end = start
            for block_number in range(start, end + 1):
                words = self.read_words_block(block_number)
                sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                oct_dump(words)

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
        return self.uic or ""

    def path_join(self, path: str, *paths: str) -> str:
        """Join directory path and filename"""
        p = [x for x in [path] + list(paths) if x and x != "/"]
        return "".join(p)
