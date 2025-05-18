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
import re
import sys
import typing as t
from datetime import date

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..block import BlockDevice12Bit
from ..commons import ASCII, BLOCK_SIZE, IMAGE, READ_FILE_FULL, filename_match
from ..rx import RX_SECTOR_TRACK

__all__ = [
    "OS8File",
    "OS8DirectoryEntry",
    "OS8Filesystem",
]

# OS/8 Software Support Manual
# http://www.bitsavers.org/pdf/dec/pdp8/os8/DEC-S8-OSSMB-A-D_OS8_v3ssup.pdf Pag 62

DIR_ENTRY_SIZE = 5  # Directory entry size (words)
EMPTY_DIR_ENTRY_SIZE = 2  # Empty directory entry size (words)
DIRECTORY_SEGMENT_HEADER_SIZE = 5  # Directory segment header (words)
DIRECTORY_SEGMENT_SIZE = 256  # Directory segment size (words)
DIRECTORY_SEGMENT_START = 1  # Directory start block number
NUM_OF_SEGMENTS = 6  # Number of directory segments
PARTITION_FULLNAME_RE = re.compile(r"^\[(\d+)\](.*)$")
ASCII_EXTENSIONS = [
    "BA",  # BASIC
    "BI",  # BATCH
    "FC",  # FOCAL
    "FT",  # FORTRAN
    "HL",  # HELP
    "LS",  # Listing
    "MA",  # MACRO
    "PA",  # PAL
    "PS",  # Pascal
    "RA",  # RALF
    "SB",  # SABR
    "TE",  # TECO
    "TX",  # Text
    "WU",  # Write Up
]
OS8_BLOCK_SIZE_BYTES = 384  # Block size (in bytes)


def os8_to_date(val: int) -> t.Optional[date]:
    """
    Translate OS-8 date to Python date
    """
    if val == 0:
        return None
    year = val & 0o7
    year += 1970
    day = (val >> 3) & 0o37
    month = (val >> 8) & 0o17
    if day == 0:
        day = 1
    if month == 0:
        month = 1
    try:
        return date(year, month, day)
    except:
        return None


def date_to_os8(d: t.Optional[date]) -> int:
    """
    Convert Python date to OS-8 date integer format.
    """
    if d is None:
        return 0
    year = (d.year - 1970) & 0o7
    val = (d.month << 8) | (d.day << 3) | year
    return val


def os8_canonical_filename(fullname: t.Optional[str], wildcard: bool = False) -> str:
    """
    Generate the canonical OS8 name
    """
    fullname = (fullname or "").upper()
    try:
        filename, extension = fullname.split(".", 1)
    except Exception:
        filename = fullname
        extension = "*" if wildcard else ""
    filename = rad50_word12_to_asc(asc_to_rad50_word12(filename[0:3])) + rad50_word12_to_asc(
        asc_to_rad50_word12(filename[3:6])
    )
    extension = rad50_word12_to_asc(asc_to_rad50_word12(extension))
    return f"{filename}.{extension}"


def os8_split_fullname(
    partition: int, fullname: t.Optional[str], wildcard: bool = True
) -> t.Tuple[int, t.Optional[str]]:
    """
    Split the partition number from the fullname

    [1]filename.ext -> 1, filename.ext
    """
    if fullname:
        try:
            match = PARTITION_FULLNAME_RE.match(fullname)
            if match:
                partition_str, fullname = match.groups()
                partition = int(partition_str)
        except Exception:
            pass
        if fullname:
            fullname = os8_canonical_filename(fullname, wildcard=wildcard)
    return partition, fullname


def from_12bit_words_to_bytes(words: list[int], file_mode: str = ASCII) -> bytes:
    """
    Convert 12bit words to bytes

    http://www.bitsavers.org/pdf/dec/pdp8/os8/DEC-S8-OSSMB-A-D_OS8_v3ssup.pdf Pag 65
    """
    mask = 127 if file_mode == ASCII else 255
    data = bytearray()
    for i in range(0, len(words) - 1, 2):
        chr1 = words[i]
        try:
            chr2 = words[i + 1]
        except IndexError:
            chr2 = 0
        chr3 = ((chr2 >> 8) & 0o17) | ((chr1 >> 4) & 0o360)
        data.append(chr1 & mask)
        data.append(chr2 & mask)
        data.append(chr3 & mask)
    return bytes(data)


def from_bytes_to_12bit_words(byte_data: bytes, file_mode: str = "ASCII") -> t.List[int]:
    """
    Convert bytes to 12-bit words.
    """
    mask = 127 if file_mode == "ASCII" else 255
    words = []
    for i in range(0, len(byte_data), 3):
        chr1 = byte_data[i] & mask
        try:
            chr2 = byte_data[i + 1] & mask
        except IndexError:
            chr2 = 0
        try:
            chr3 = byte_data[i + 2] & mask
        except IndexError:
            chr3 = 0
        words.append(chr1 | ((chr3 & 0o360) << 4))
        words.append(chr2 | ((chr3 & 0o17) << 8))
    return words


def rad50_word12_to_asc(val: int) -> str:
    """
    Convert RAD50 12 bit word to 0-3 chars of ASCII
    """
    t = "".join(chr(c) if c > 0o40 else chr(c + 0o100) for c in [(val >> 6), (val & 0o77)])
    return t.replace("@", "")


def asc_to_rad50_word12(val: str) -> int:
    """
    Convert 0-3 chars of ASCII to RAD50 12 bit word
    """
    val = val.rjust(2, "@")
    c1 = ord(val[0])
    c2 = ord(val[1])
    if c1 < 0o100:
        c1 += 0o100
    if c2 < 0o100:
        c2 += 0o100
    return ((c1 & 0o77) << 6) | (c2 & 0o77)


def oct_dump(words: t.List[int], words_per_line: int = 8) -> None:
    """
    Display contents in octal
    """
    for i in range(0, len(words), words_per_line):
        line = words[i : i + words_per_line]
        ascii_str = "".join([chr(x) if 32 <= x <= 126 else "." for x in from_12bit_words_to_bytes(line)])
        oct_str = " ".join([f"{x:04o}" for x in line])
        sys.stdout.write(f"{i:08o}   {oct_str.ljust(5 * words_per_line)}  {ascii_str}\n")


class OS8File(AbstractFile):
    entry: "OS8DirectoryEntry"
    file_mode: str
    closed: bool
    size: int

    def __init__(self, entry: "OS8DirectoryEntry", file_mode: t.Optional[str] = None):
        self.entry = entry
        if file_mode is not None:
            self.file_mode = file_mode
        else:
            self.file_mode = ASCII if entry.extension.upper() in ASCII_EXTENSIONS else IMAGE
        self.closed = False
        self.size = entry.length * OS8_BLOCK_SIZE_BYTES

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
        for i in range(number_of_blocks):
            block_position = block_number + self.entry.file_position + i
            words = self.entry.segment.partition.read_12bit_words_block(block_position)
            t = from_12bit_words_to_bytes(words, self.file_mode)
            data.extend(t)
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
        for i in range(number_of_blocks):
            block_position = block_number + self.entry.file_position + i
            data = buffer[i * OS8_BLOCK_SIZE_BYTES : (i + 1) * OS8_BLOCK_SIZE_BYTES]
            words = from_bytes_to_12bit_words(data, self.file_mode)
            self.entry.segment.partition.write_12bit_words_block(block_position, words)

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.entry.get_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return self.entry.get_block_size()

    def close(self) -> None:
        """
        Close the file
        """
        self.closed = True

    def __str__(self) -> str:
        return self.entry.fullname


class OS8DirectoryEntry(AbstractDirectoryEntry):
    """
    There are three types of file directory entries.
    - Permanent
    - Tentative
    - Empty

    A permanent entry appears as follows:

        +-----------------------------------+
      0 | File name char 1-2                |
      1 | File name char 3-4                |
      2 | File name char 5-6                |
      3 | Extension char 1-2                |
        +-----------------------------------+
      4 | Extra words                       |
        |.                                  |
        |.                                  |
        +-----------------------------------+
    N+4 | Minus file length in blocks       |
        +-----------------------------------+

    A tentative file entry appears as a permanent file entry with zero length.
    It is always immediately followed by an empty file entry.

    An empty entry appears as follows:

        +-----------------------------------+
      0 | Always 0                          |
      1 | Minus empty blocks                |
        +-----------------------------------+

    http://www.bitsavers.org/pdf/dec/pdp8/os8/DEC-S8-OSSMB-A-D_OS8_v3ssup.pdf Pag 63
    """

    segment: "OS8Segment"
    filename: str = ""
    extension: str = ""
    length: int = 0
    raw_creation_date: int = 0
    extra_words: t.List[int] = []
    file_position: int = 0
    empty_entry: bool = False

    def __init__(self, segment: "OS8Segment"):
        self.segment = segment

    @classmethod
    def read(cls, segment: "OS8Segment", words: t.List[int], position: int, file_position: int) -> "OS8DirectoryEntry":
        self = cls(segment)
        if words[0 + position] != 0:
            n1 = words[0 + position]  # Filename char 1-2
            n2 = words[1 + position]  # Filename char 3-4
            n3 = words[2 + position]  # Filename char 5-6
            e1 = words[3 + position]  # Extension char 1-2
            self.empty_entry = False
            self.filename = rad50_word12_to_asc(n1) + rad50_word12_to_asc(n2) + rad50_word12_to_asc(n3)
            self.extension = rad50_word12_to_asc(e1)
            self.extra_words = words[4 + position : 4 + self.segment.extra_words + position]
            if self.segment.extra_words:
                # When extra words are used, the first one is used
                # to store the creation date
                self.raw_creation_date = self.extra_words[0]
            length = words[self.segment.extra_words + 4 + position]
            self.length = 0o10000 - length if length else length
        else:  # Empty entry
            length = 0o10000 - words[1 + position]
            self.empty_entry = True
            self.filename = ""
            self.extension = ""
            self.extra_words = []
            self.length = length
        self.file_position = file_position
        return self

    def to_words(self) -> t.List[int]:
        """
        Write the directory entry
        """
        words = []
        if self.is_empty:
            words.append(0)
            words.append(0o10000 - self.length)
        else:
            words.append(asc_to_rad50_word12(self.filename[0:2]))
            words.append(asc_to_rad50_word12(self.filename[2:4]))
            words.append(asc_to_rad50_word12(self.filename[4:6]))
            words.append(asc_to_rad50_word12(self.extension))
            if self.segment.extra_words:
                self.extra_words[0] = self.raw_creation_date
            words.extend(self.extra_words)
            words.append(0o10000 - self.length)
        return words

    @property
    def is_empty(self) -> bool:
        return self.empty_entry

    @property
    def is_tentative(self) -> bool:
        return (self.length == 0) and not self.is_empty

    @property
    def is_permanent(self) -> bool:
        return not self.is_empty and not self.is_tentative

    @property
    def directory_entry_len(self) -> int:
        """
        Length of this directory entry in words
        """
        if self.is_empty:
            return EMPTY_DIR_ENTRY_SIZE
        else:
            return DIR_ENTRY_SIZE + len(self.extra_words)

    @property
    def fullname(self) -> str:
        return f"{self.filename}.{self.extension}"

    @property
    def basename(self) -> str:
        return self.fullname

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
        return OS8_BLOCK_SIZE_BYTES

    @property
    def creation_date(self) -> t.Optional[date]:
        return os8_to_date(self.raw_creation_date)

    def delete(self) -> bool:
        """
        Delete the file
        """
        self.empty_entry = True
        self.filename = ""
        self.extension = ""
        self.extra_words = []
        self.segment.compact()
        self.segment.write()
        return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        self.segment.write()
        return True

    def open(self, file_mode: t.Optional[str] = None) -> OS8File:
        """
        Open a file
        """
        return OS8File(self, file_mode)

    def __str__(self) -> str:
        attr = "TEMP" if self.is_tentative else "PERM" if self.is_permanent else "EMPTY"
        return f"{self.fullname:<11} {attr:<5} {self.creation_date or '          '} {self.length:>6} {self.file_position:6d}"

    def __repr__(self) -> str:
        return str(self)


class OS8Segment:
    """
    Volume Directory Segment

    http://www.bitsavers.org/pdf/dec/pdp8/os8/DEC-S8-OSSMB-A-D_OS8_v3ssup.pdf Pag 62

        +-----------------------------------+
      0 | Minus number of entries           |   5 words header
      1 | First file starting block         |
      2 | Link to next segment              |
      3 | Point to last tentative file word |
      4 | Minus number of extra words       |
        +-----------------------------------+
      5 |Entries                            |
        |.                                  |
    255 |.                                  |
        +-----------------------------------+
    """

    partition: "OS8Partition"
    # Block number of this directory segment
    block_number = 0
    # Block number where the stored data identified by this segment begins
    data_block_number = 0
    # Next directory segment block number
    next_block_number = 0
    # Points to last word of tentative file entry in this segment
    tentative_last_word = 0
    # Number of extra words per directory entry
    extra_words = 0
    # Directory entries
    entries_list: t.List["OS8DirectoryEntry"] = []

    def __init__(self, partition: "OS8Partition"):
        self.partition = partition
        self.entries_list = []

    @classmethod
    def read(cls, partition: "OS8Partition", block_number: int) -> "OS8Segment":
        """
        Read a Volume Directory Segment from disk
        """
        self = cls(partition)
        data = self.partition.read_12bit_words_block(block_number)
        self.block_number = block_number
        number_of_entries = 0o10000 - data[0]  # Minus the number of entries
        self.data_block_number = data[1]  # Block number where the stored data begins
        self.next_block_number = data[2]  # Link to next segment
        self.tentative_last_word = data[3]  # Link to next segment
        self.extra_words = 0o10000 - data[4]  # Minus the number of extra words
        self.entries_list = []

        file_position = self.data_block_number
        position = DIRECTORY_SEGMENT_HEADER_SIZE
        for _ in range(0, number_of_entries):
            dir_entry = OS8DirectoryEntry.read(self, data, position, file_position)
            file_position = file_position + dir_entry.length
            position = position + dir_entry.directory_entry_len
            self.entries_list.append(dir_entry)
        return self

    def compact(self) -> None:
        """
        Compact multiple unused entries
        """
        prev_empty_entry = None
        new_entries_list = []
        for entry in self.entries_list:
            if not entry.is_empty:
                prev_empty_entry = None
                new_entries_list.append(entry)
            elif prev_empty_entry is None:
                prev_empty_entry = entry
                new_entries_list.append(entry)
            else:
                prev_empty_entry.length = prev_empty_entry.length + entry.length
        self.entries_list = new_entries_list

    def write(self) -> None:
        """
        Write the Volume Directory Segment
        """
        words = []
        words.append(0o10000 - self.number_of_entries)
        words.append(self.data_block_number)
        words.append(self.next_block_number)
        words.append(self.tentative_last_word)
        words.append(0o10000 - self.extra_words)
        for entry in self.entries_list:
            words.extend(entry.to_words())
        words += [0] * (DIRECTORY_SEGMENT_SIZE - len(words))
        self.partition.write_12bit_words_block(self.block_number, words)

    @property
    def number_of_entries(self) -> int:
        """
        Number of directory entries in this segment
        """
        return len(self.entries_list)

    @property
    def max_entries(self) -> int:
        """
        Max directory entries
        """
        return (DIRECTORY_SEGMENT_SIZE - DIRECTORY_SEGMENT_HEADER_SIZE) // (DIR_ENTRY_SIZE + self.extra_words) - 1

    def insert_empty_entry_after(self, entry: "OS8DirectoryEntry", entry_number: int, length: int) -> None:
        """
        Insert an empty entry after the given entry
        If the entry length is equal to the requested length, don't create the new empty entity
        """
        if entry.length == length:
            return
        # Create new empty space entry
        new_entry = OS8DirectoryEntry(self)
        new_entry.empty_entry = True
        new_entry.length = entry.length - length
        new_entry.file_position = entry.file_position + length
        entry.length = length
        self.entries_list.insert(entry_number + 1, new_entry)
        entry.segment.write()

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        free_blocks = 0
        for entry in self.entries_list:
            if entry.is_empty:
                free_blocks += entry.length
        return free_blocks

    def __str__(self) -> str:
        buf = io.StringIO()
        buf.write("\n*Segment\n")
        buf.write(f"Block number:          {self.block_number:>5}\n")
        buf.write(f"Number of entries:     {self.number_of_entries:>5}\n")
        buf.write(f"Data block:            {self.data_block_number:>5}\n")
        buf.write(f"Next dir segment:      {self.next_block_number:>5}\n")
        buf.write(f"Tentative last word:   {self.tentative_last_word:>5}\n")
        buf.write(f"Extra words:           {self.extra_words:>5}\n")
        buf.write(f"Max entries:           {self.max_entries:>5}\n\n")
        buf.write("Num  Filename    Type  Date       Length  Block\n")
        buf.write("---  --------    ----  ----       ------  -----\n")
        for i, x in enumerate(self.entries_list):
            buf.write(f"{i:02d}#  {x}\n")
        return buf.getvalue()


class OS8Partition:
    """
    OS/8 partition

        +-----------------------------------+
      0 | System bootstrap                  |
        +-----------------------------------+
      1 | Directory segments                |
        |.                                  |
      7 |.                                  |
        +-----------------------------------+
        | OS/8 System (Optional)            |
        |.                                  |
        +-----------------------------------+
        | File storage                      |
        |.                                  |
        |.                                  |
        +-----------------------------------+
    """

    fs: "OS8Filesystem"
    # partition number
    partition_number: int
    # partition size
    partition_size: int
    # Block number of the first block of this partition
    base_block_number: int

    def __init__(self, fs: "OS8Filesystem", partition_number: int):
        self.fs = fs
        self.partition_number = partition_number
        self.partition_size = fs.partition_size
        self.base_block_number = partition_number * fs.partition_size

    def read_12bit_words_block(self, block_number: int) -> t.List[int]:
        """
        Read a 512 bytes block as 256 12bit words
        """
        return self.fs.read_12bit_words_block(block_number + self.base_block_number)

    def write_12bit_words_block(
        self,
        block_number: int,
        words: t.List[int],
    ) -> None:
        """
        Write 256 12bit words as 512 bytes block
        """
        return self.fs.write_12bit_words_block(block_number + self.base_block_number, words)

    def read_dir_segments(self) -> t.Iterator["OS8Segment"]:
        """
        Read all directory segments of this partition
        """
        next_block_number = DIRECTORY_SEGMENT_START
        while next_block_number != 0:
            segment = OS8Segment.read(self, next_block_number)
            next_block_number = segment.next_block_number
            yield segment

    def get_file_entry(self, fullname: str) -> OS8DirectoryEntry:  # fullname=filename+ext
        """
        Get the directory entry for a file in this partition
        """
        for segment in self.read_dir_segments():
            for entry in segment.entries_list:
                if entry.fullname == fullname and entry.is_permanent:
                    return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def split_segment(self, entry: OS8DirectoryEntry) -> bool:
        # entry is the last entry of the old_segment, new new segment will contain all the entries after that
        old_segment = entry.segment
        # find the new segment number
        segments = list(self.read_dir_segments())
        sn = [x.block_number for x in segments]
        block_number = None
        for i in range(0, NUM_OF_SEGMENTS):
            block_number_t = self.base_block_number + DIRECTORY_SEGMENT_START + i
            if block_number_t not in sn:
                block_number = block_number_t
                break
        if block_number is None:
            return False
        # create the new segment
        segment = OS8Segment(self)
        segment.block_number = block_number
        segment.data_block_number = entry.file_position + entry.length
        segment.next_block_number = old_segment.next_block_number
        segment.tentative_last_word = 0
        segment.extra_words = segments[0].extra_words
        # set the next segment of the last segment
        old_segment.next_block_number = segment.block_number

        entry_position = -1
        for i, e in enumerate(old_segment.entries_list):
            if entry == e:
                entry_position = i
        if entry_position == 1:
            return False
        segment.entries_list = old_segment.entries_list[entry_position + 1 :]
        old_segment.entries_list = old_segment.entries_list[: entry_position + 1]
        old_segment.write()
        segment.data_block_number = entry.file_position + entry.length
        segment.write()
        return True

    def search_empty_entry(self, length: int) -> t.Tuple[t.Optional[OS8DirectoryEntry], int]:
        # Search for an empty entry to be split
        entry: t.Optional[OS8DirectoryEntry] = None
        entry_number: int = -1
        for segment in self.read_dir_segments():
            for i, e in enumerate(segment.entries_list):
                if e.is_empty and e.length >= length:
                    if entry is None or entry.length > e.length:
                        entry = e
                        entry_number = i
                        if entry.length == length:
                            break
        return entry, entry_number

    def allocate_space(
        self,
        fullname: str,  # fullname=filename+ext, length in blocks
        length: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
    ) -> OS8DirectoryEntry:
        """
        Allocate space for a new file
        """
        # Search for an empty entry to be split
        entry, entry_number = self.search_empty_entry(length)
        if entry is None:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), fullname)
        # If the entry length is equal to the requested length, don't create the new empty entity
        if entry.length != length:
            if len(entry.segment.entries_list) >= entry.segment.max_entries:
                if not self.split_segment(entry):
                    raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), fullname)
            entry.segment.insert_empty_entry_after(entry, entry_number, length)
        # Fill the entry
        split = os.path.splitext(fullname.upper())
        entry.empty_entry = False
        entry.extra_words = [0] * entry.segment.extra_words
        entry.filename = split[0]
        entry.extension = split[1] and split[1][1:] or ""
        entry.raw_creation_date = date_to_os8(creation_date)
        entry.length = length
        # Write the segment
        entry.segment.write()
        return entry

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # t.optional creation date
        file_type: t.Optional[str] = None,
    ) -> t.Optional[OS8DirectoryEntry]:
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass
        return self.allocate_space(fullname, number_of_blocks, creation_date)

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        """
        Create an empty OS/8 partition
        """
        # Directory segment
        segment = OS8Segment(self)
        segment.block_number = DIRECTORY_SEGMENT_START
        segment.data_block_number = segment.block_number + NUM_OF_SEGMENTS
        segment.next_block_number = 0
        segment.tentative_last_word = 0
        segment.extra_words = 1
        # Empty directory entry
        length = self.partition_size - segment.data_block_number
        if self.fs.is_rx_12bit:
            length = length - RX_SECTOR_TRACK * self.fs.sector_size // BLOCK_SIZE
        dir_entry = OS8DirectoryEntry(segment)
        dir_entry.empty_entry = True
        dir_entry.filename = ""
        dir_entry.extension = ""
        dir_entry.length = length
        segment.entries_list.append(dir_entry)
        segment.write()

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        free_blocks = 0
        for segment in self.read_dir_segments():
            free_blocks += segment.free()
        return free_blocks

    def __str__(self) -> str:
        buf = io.StringIO()
        buf.write("\n*Partition\n")
        buf.write(f"Partition number:         {self.partition_number:>5}\n")
        buf.write(f"Partition size:           {self.partition_size:>5}\n")
        buf.write(f"Partition starting block: {self.base_block_number:>5}\n")
        return buf.getvalue()


class OS8Filesystem(AbstractFilesystem, BlockDevice12Bit):
    """
    OS/8 Filesystem

    OS/8 FILE STRUCTURES
    http://www.bitsavers.org/pdf/dec/pdp8/os8/DEC-S8-OSSMB-A-D_OS8_v3ssup.pdf Pag 62
    """

    fs_name = "os8"
    fs_description = "PDP-8 OS/8"

    current_partition: int  # Current partition
    number_of_blocks: int  # Number of blocks

    @property
    def num_of_partitions(self) -> int:
        """Get the number of partitions"""
        return 1 + (self.number_of_blocks - 1) // 0o10000

    @property
    def partition_size(self) -> int:  # Size of each partition
        """Get the size of each partition"""
        return self.number_of_blocks // self.num_of_partitions

    def get_partition(self, partition_number: int) -> OS8Partition:
        """
        Get a partition by number
        """
        try:
            if partition_number is None:
                partition_number = self.current_partition
            if partition_number < 0 or partition_number >= self.num_of_partitions:
                raise ValueError
            return OS8Partition(self, partition_number)
        except:
            raise FileNotFoundError(errno.ENOENT, "Partition not found", partition_number)

    @classmethod
    def mount(cls, file: "AbstractFile") -> "AbstractFilesystem":
        self = cls(file)
        self.current_partition = 0
        self.number_of_blocks = self.f.get_size() // BLOCK_SIZE
        return self

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["OS8DirectoryEntry"]:
        partition = self.current_partition
        if pattern:
            partition, pattern = os8_split_fullname(partition, pattern, wildcard)
        for segment in self.get_partition(partition).read_dir_segments():
            for entry in segment.entries_list:
                if filename_match(entry.basename, pattern, wildcard):
                    if not include_all and (entry.is_empty or entry.is_tentative):
                        continue
                    yield entry

    @property
    def entries_list(self) -> t.Iterator["OS8DirectoryEntry"]:
        for segment in self.get_partition(self.current_partition).read_dir_segments():
            for entry in segment.entries_list:
                yield entry

    def get_file_entry(self, fullname: str) -> OS8DirectoryEntry:  # fullname=filename+ext
        partition = self.current_partition
        if fullname:
            partition, fullname = os8_split_fullname(partition, fullname)  # type: ignore
        for segment in self.get_partition(partition).read_dir_segments():
            for entry in segment.entries_list:
                if entry.fullname == fullname and entry.is_permanent:
                    return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: t.Optional[date] = None,
        file_type: t.Optional[str] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        number_of_blocks = int(math.ceil(len(content) / OS8_BLOCK_SIZE_BYTES))
        entry = self.create_file(fullname, number_of_blocks, creation_date, file_type)
        if entry is not None:
            content = content + (b"\0" * OS8_BLOCK_SIZE_BYTES)
            f = entry.open(file_mode)
            try:
                f.write_block(content, block_number=0, number_of_blocks=entry.length)
            finally:
                f.close()

    def create_file(
        self,
        fullname: str,
        length: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,
    ) -> t.Optional[OS8DirectoryEntry]:
        partition, fullname = os8_split_fullname(self.current_partition, fullname, wildcard=False)  # type: ignore
        return self.get_partition(partition).create_file(fullname, length, creation_date, file_type)

    def chdir(self, fullname: str) -> bool:
        try:
            partition = int(fullname)
        except:
            return False
        if partition < 0 or partition >= self.num_of_partitions:
            return False
        self.current_partition = int(fullname)
        return True

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        i = 0
        files = 0
        blocks = 0
        unused = None
        for x in self.filter_entries_list(pattern, include_all=True):
            if unused is None:
                unused = x.segment.partition.free()
            if x.is_empty or x.is_tentative:
                if not options.get("full"):
                    continue
                i = i + 1
                fullname = "<EMPTY>  "
                date = ""
            else:
                i = i + 1
                fullname = f"{x.filename:<6}.{x.extension:<2}"
                if options.get("brief"):
                    # Lists only file names and file types
                    sys.stdout.write(f"{fullname}\n")
                    continue
                date = x.creation_date and x.creation_date.strftime("%d-%b-%y") or ""
                files = files + 1
                blocks = blocks + x.length
            date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or ""
            sys.stdout.write(f"{fullname} {x.file_position:04o} {x.length:>3} {date:<9}")
            if i % 3 == 0:
                sys.stdout.write("\n")
            else:
                sys.stdout.write("  ")
        if options.get("brief"):
            return
        if i % 3 != 0:
            sys.stdout.write("\n")
        sys.stdout.write(f"\n{files:>4} FILES IN {blocks:>4} BLOCKS - {unused:>4} FREE BLOCKS\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if arg:
            sys.stdout.write("Filename    Type  Date       Length  Block\n")
            sys.stdout.write("--------    ----  ----       ------  -----\n")
            for entry in self.filter_entries_list(arg, include_all=True):
                sys.stdout.write(f"{entry}\n")
        else:
            sys.stdout.write(f"Number of partitions:     {self.num_of_partitions}\n")
            sys.stdout.write(f"Size of each partition:   {self.partition_size}\n")
            for partition_number in range(0, self.num_of_partitions):
                partition = self.get_partition(partition_number)
                sys.stdout.write(f"{partition}\n")
                for segment in partition.read_dir_segments():
                    sys.stdout.write(f"{segment}\n")

    def dump(self, fullname: t.Optional[str], start: t.Optional[int] = None, end: t.Optional[int] = None) -> None:
        """Dump the content of a file or a range of blocks"""
        if fullname:
            entry = self.get_file_entry(fullname)
            if start is None:
                start = 0
            if end is None or end > entry.get_length() - 1:
                end = entry.get_length() - 1
            for block_number in range(start, end + 1):
                words = self.read_12bit_words_block(entry.file_position + block_number)
                print(f"\nBLOCK NUMBER   {block_number:08}")
                oct_dump(words)
        else:
            if start is None:
                start = 0
                if end is None:  # full disk
                    end = self.get_size() // BLOCK_SIZE - 1
            elif end is None:  # one single block
                end = start
            for block_number in range(start, end + 1):
                words = self.read_12bit_words_block(block_number)
                print(f"\nBLOCK NUMBER   {block_number:08}")
                oct_dump(words)

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        """
        Create an empty OS/8 filesystem
        """
        self.current_partition = 0
        self.number_of_blocks = self.f.get_size() // BLOCK_SIZE
        # Initialize the partitions
        for partition_number in range(0, self.num_of_partitions):
            partition = OS8Partition(self, partition_number)
            partition.initialize(**kwargs)

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def close(self) -> None:
        self.f.close()

    def get_pwd(self) -> str:
        if self.current_partition == 0:
            return ""
        else:
            return f"[{self.current_partition}]"

    def __str__(self) -> str:
        return str(self.f)
