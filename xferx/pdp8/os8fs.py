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
import os
import re
import sys
import typing as t
from datetime import date

from ..abstract import XFERXPath, AbstractFile, AbstractFilesystem
from ..commons import ASCII, BLOCK_SIZE, DECTAPE, IMAGE, READ_FILE_FULL, filename_match
from ..device.abstract import AbstractDevice
from ..device.block_12bit import BlockDevice12Bit, DECtape12Bit, RXBlockDevice12Bit
from ..device.rx import RX_SECTOR_TRACK

__all__ = [
    "OS8File",
    "OS8Path",
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


def os8_canonical_filename(name: t.Optional[str], wildcard: bool = False) -> str:
    """
    Generate the canonical OS8 name
    """
    name = (name or "").upper()
    try:
        stem, extension = name.split(".", 1)
    except Exception:
        stem = name
        extension = "*" if wildcard else ""
    stem = (
        rad50_word12_to_asc(asc_to_rad50_word12(stem[0:2]))
        + rad50_word12_to_asc(asc_to_rad50_word12(stem[2:4]))
        + rad50_word12_to_asc(asc_to_rad50_word12(stem[4:6]))
    )
    extension = rad50_word12_to_asc(asc_to_rad50_word12(extension))
    return f"{stem}.{extension}"


def os8_split_fullname(
    partition: t.Optional[int], fullname: t.Optional[str], wildcard: bool = True
) -> t.Tuple[t.Optional[int], t.Optional[str]]:
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


def from_bytes_to_12bit_words(byte_data: t.Union[bytes, bytearray], file_mode: str = "ASCII") -> t.List[int]:
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
    Convert RAD50 12 bit word to 0-2 chars of ASCII
    """
    t = "".join(chr(c) if c > 0o40 else chr(c + 0o100) for c in [(val >> 6), (val & 0o77)])
    return t.replace("@", "")


def asc_to_rad50_word12(val: str) -> int:
    """
    Convert 0-2 chars of ASCII to RAD50 12 bit word
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
    entry: "OS8Path"
    file_mode: str
    size: int

    def __init__(self, entry: "OS8Path", file_mode: t.Optional[str] = None):
        self.entry = entry
        if file_mode is not None:
            self.file_mode = file_mode
        else:
            self.file_mode = ASCII if entry.extension.upper() in ASCII_EXTENSIONS else IMAGE
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
            block_position = block_number + self.entry.first_block_number + i
            words = self.entry.segment.partition.read_words_block(block_position)
            t = from_12bit_words_to_bytes(words, self.file_mode)
            data.extend(t)
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
        for i in range(number_of_blocks):
            block_position = block_number + self.entry.first_block_number + i
            data = buffer[i * OS8_BLOCK_SIZE_BYTES : (i + 1) * OS8_BLOCK_SIZE_BYTES]
            words = from_bytes_to_12bit_words(data, self.file_mode)
            self.entry.segment.partition.write_words_block(block_position, words)

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

    def __str__(self) -> str:
        return self.entry.fullname


class OS8Path(XFERXPath):
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

    fs: "OS8Filesystem"
    segment: "OS8Segment"
    stem: str = ""
    extension: str = ""
    length: int = 0
    raw_creation_date: int = 0
    words: t.List[int] = []
    extra_words: t.List[int] = []
    first_block_number: int = 0
    empty_entry: bool = False

    def __init__(self, segment: "OS8Segment"):
        self.fs = segment.partition.fs
        self.segment = segment

    @classmethod
    def read(cls, segment: "OS8Segment", words: t.List[int], position: int, first_block_number: int) -> "OS8Path":
        self = cls(segment)
        if words[0 + position] != 0:
            self.words = words[position : position + DIR_ENTRY_SIZE + segment.extra_words]
            n1 = words[0 + position]  # Filename char 1-2
            n2 = words[1 + position]  # Filename char 3-4
            n3 = words[2 + position]  # Filename char 5-6
            e1 = words[3 + position]  # Extension char 1-2
            self.empty_entry = False
            self.stem = rad50_word12_to_asc(n1) + rad50_word12_to_asc(n2) + rad50_word12_to_asc(n3)
            self.extension = rad50_word12_to_asc(e1)
            self.extra_words = words[4 + position : 4 + self.segment.extra_words + position]
            if self.segment.extra_words:
                # When extra words are used, the first one is used
                # to store the creation date
                self.raw_creation_date = self.extra_words[0]
            self.length = 999
            length = words[self.segment.extra_words + 4 + position]
            self.length = 0o10000 - length if length else length
        else:  # Empty entry
            length = 0o10000 - words[1 + position]
            self.words = words[position : position + EMPTY_DIR_ENTRY_SIZE]
            self.empty_entry = True
            self.stem = ""
            self.extension = ""
            self.extra_words = []
            self.length = length
        self.first_block_number = first_block_number
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
            words.append(asc_to_rad50_word12(self.stem[0:2]))
            words.append(asc_to_rad50_word12(self.stem[2:4]))
            words.append(asc_to_rad50_word12(self.stem[4:6]))
            words.append(asc_to_rad50_word12(self.extension))
            if self.segment.extra_words:
                self.extra_words[0] = self.raw_creation_date
            words.extend(self.extra_words)
            words.append(0o10000 - self.length)
        self.words = words
        return words

    @property
    def partition_number(self) -> int:
        return self.segment.partition.partition_number

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
        return f"[{self.partition_number}]{self.name}"

    @property
    def name(self) -> str:
        return f"{self.stem}.{self.extension}"

    @property
    def parent(self) -> "OS8Partition":
        """The logical parent"""
        return self.segment.partition

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
        return OS8_BLOCK_SIZE_BYTES

    @property
    def creation_date(self) -> t.Optional[date]:
        return os8_to_date(self.raw_creation_date)

    def delete(self) -> bool:
        """
        Delete the file
        """
        self.empty_entry = True
        self.stem = ""
        self.extension = ""
        self.extra_words = []
        self.segment.compact()
        self.segment.write()
        self.words = self.to_words()
        return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        self.segment.write()
        return True

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> OS8File:
        """
        Open a file
        """
        return OS8File(self, file_mode)

    def __str__(self) -> str:
        attr = "TEMP" if self.is_tentative else "PERM" if self.is_permanent else "EMPTY"
        return f"{self.name:<11} {attr:<5} {self.creation_date or '          '} {self.length:>6} {self.first_block_number:6d}"

    def __repr__(self) -> str:
        return str(self)


class OS8Segment:
    """
    Volume Directory Segment

    OS/8 - Pag 62
    http://www.bitsavers.org/pdf/dec/pdp8/os8/DEC-S8-OSSMB-A-D_OS8_v3ssup.pdf

    COS-310 - Pag 5
    https://svn.so-much-stuff.com/svn/trunk/pdp8/Websites/pdp-8.org/scans/highgate/cos310-docs/aa-d647a-ta-app.pdf

        +-----------------------------------+
      0 | Minus number of entries           |   5 words header
      1 | First file starting block         |
      2 | Link to next segment              |
      3 | Point to last tentative file word |  (unused in COS)
      4 | Minus number of extra words       |  (always -1 for COS)
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
    entries_list: t.List[OS8Path] = []

    def __init__(self, partition: "OS8Partition"):
        self.partition = partition
        self.entries_list = []

    @classmethod
    def read(cls, partition: "OS8Partition", block_number: int) -> "OS8Segment":
        """
        Read a Volume Directory Segment from disk
        """
        self = cls(partition)
        data = self.partition.read_words_block(block_number)
        self.block_number = block_number
        number_of_entries = 0o10000 - data[0]  # Minus the number of entries
        self.data_block_number = data[1]  # Block number where the stored data begins
        self.next_block_number = data[2]  # Link to next segment
        self.tentative_last_word = data[3]  # Link to next segment
        self.extra_words = 0o10000 - data[4]  # Minus the number of extra words
        self.entries_list = []

        first_block_number = self.data_block_number
        position = DIRECTORY_SEGMENT_HEADER_SIZE
        for _ in range(0, number_of_entries):
            dir_entry = self.partition.fs.directory_entry_class.read(self, data, position, first_block_number)
            first_block_number = first_block_number + dir_entry.length
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
        self.partition.write_words_block(self.block_number, words)

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

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator[OS8Path]:
        for entry in self.entries_list:
            if filename_match(entry.name, pattern, wildcard):
                if not include_all and (entry.is_empty or entry.is_tentative):
                    continue
                yield entry

    def insert_empty_entry_after(self, entry: OS8Path, entry_number: int, length: int) -> None:
        """
        Insert an empty entry after the given entry
        If the entry length is equal to the requested length, don't create the new empty entity
        """
        if entry.length == length:
            return
        # Create new empty space entry
        new_entry = self.partition.fs.directory_entry_class(self)
        new_entry.empty_entry = True
        new_entry.length = entry.length - length
        new_entry.first_block_number = entry.first_block_number + length
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


class OS8Partition(XFERXPath):
    """
    OS/8 partition

        +-----------------------------------+
      0 | System bootstrap                  |
        +-----------------------------------+
      1 | Directory segments                |
        |.                                  |
      6 |.                                  |
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
    first_block_number: int

    def __init__(self, fs: "OS8Filesystem", partition_number: int):
        self.fs = fs
        self.partition_number = partition_number
        self.partition_size = min(fs.partition_size, fs.number_of_blocks - partition_number * fs.partition_size)
        self.first_block_number = partition_number * fs.partition_size

    def read_words_block(self, block_number: int) -> t.List[int]:
        """
        Read a 512 bytes block as 256 12bit words
        """
        return self.fs.read_words_block(block_number + self.first_block_number)

    def write_words_block(
        self,
        block_number: int,
        words: t.List[int],
    ) -> None:
        """
        Write 256 12bit words as 512 bytes block
        """
        return self.fs.write_words_block(block_number + self.first_block_number, words)

    def read_dir_segments(self) -> t.Iterator["OS8Segment"]:
        """
        Read all directory segments of this partition
        """
        next_block_number = DIRECTORY_SEGMENT_START
        while next_block_number != 0:
            segment = OS8Segment.read(self, next_block_number)
            next_block_number = segment.next_block_number
            yield segment

    def get_file_entry(self, name: str) -> OS8Path:
        """
        Get the directory entry for a file in this partition
        """
        for segment in self.read_dir_segments():
            for entry in segment.entries_list:
                if entry.name == name and entry.is_permanent:
                    return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), name)

    def split_segment(self, entry: OS8Path) -> bool:
        # entry is the last entry of the old_segment, new new segment will contain all the entries after that
        old_segment = entry.segment
        # find the new segment number
        segments = list(self.read_dir_segments())
        sn = [x.block_number for x in segments]
        block_number = None
        for i in range(0, NUM_OF_SEGMENTS):
            block_number_t = self.first_block_number + DIRECTORY_SEGMENT_START + i
            if block_number_t not in sn:
                block_number = block_number_t
                break
        if block_number is None:
            return False
        # create the new segment
        segment = OS8Segment(self)
        segment.block_number = block_number
        segment.data_block_number = entry.first_block_number + entry.length
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
        segment.data_block_number = entry.first_block_number + entry.length
        segment.write()
        return True

    def search_empty_entry(self, length: int) -> t.Tuple[t.Optional[OS8Path], int]:
        # Search for an empty entry to be split
        entry: t.Optional[OS8Path] = None
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
    ) -> OS8Path:
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
        entry.stem = split[0]
        entry.extension = split[1] and split[1][1:] or ""
        entry.raw_creation_date = date_to_os8(creation_date)
        entry.length = length
        # Write the segment
        entry.segment.write()
        return entry

    def create_file(
        self,
        fullname: str,
        size: int,  # Size in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> OS8Path:
        metadata = metadata or {}
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass
        number_of_blocks = (size + OS8_BLOCK_SIZE_BYTES - 1) // OS8_BLOCK_SIZE_BYTES
        creation_date: t.Optional[date] = metadata.get("creation_date", None)  # type: ignore
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
        if getattr(self.fs.dev, "is_rx", False):
            length = length - RX_SECTOR_TRACK * self.fs.dev.sector_size // BLOCK_SIZE
        dir_entry = self.fs.directory_entry_class(segment)
        dir_entry.empty_entry = True
        dir_entry.stem = ""
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

    @property
    def fullname(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        return f"[{self.partition_number}]"

    def is_dir(self) -> bool:
        """Is a directory"""
        return True

    @property
    def parent(self) -> "OS8RootDirectory":
        """The logical parent"""
        return self.fs.get_root()

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return self.partition_size

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get file size in bytes
        """
        return self.partition_size * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return OS8_BLOCK_SIZE_BYTES

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> OS8File:
        raise OSError(errno.EINVAL, "Invalid operation on partition")

    def delete(self) -> bool:
        raise OSError(errno.EINVAL, "Invalid operation on partition")

    def iterdir(
        self,
        pattern: t.Optional[str] = None,
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = False,
    ) -> t.Iterator[OS8Path]:
        for segment in self.read_dir_segments():
            yield from segment.filter_entries_list(
                pattern,
                include_all=include_all,
                expand=expand,
                wildcard=wildcard,
            )

    def __str__(self) -> str:
        buf = io.StringIO()
        buf.write("\n*Partition\n")
        buf.write(f"Partition number:         {self.partition_number:>5}\n")
        buf.write(f"Partition size:           {self.partition_size:>5}\n")
        buf.write(f"Partition starting block: {self.first_block_number:>5}\n")
        return buf.getvalue()


class OS8RootDirectory(XFERXPath):
    fs: "OS8Filesystem"

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
    def parent(self) -> "XFERXPath":
        """The logical parent"""
        return self

    def iterdir(self) -> t.Iterator[OS8Partition]:
        for partition_number in range(0, self.fs.num_of_partitions):
            yield self.fs.get_partition(partition_number)


class OS8Filesystem(AbstractFilesystem):
    """
    OS/8 Filesystem

    OS/8 FILE STRUCTURES
    http://www.bitsavers.org/pdf/dec/pdp8/os8/DEC-S8-OSSMB-A-D_OS8_v3ssup.pdf Pag 62
    """

    fs_name = "os8"
    fs_description = "PDP-8 OS/8"
    fs_platforms = ["pdp-8"]
    fs_entry_metadata = [
        "creation_date",
    ]

    dev: BlockDevice12Bit

    current_partition: t.Optional[int]  # Current partition
    number_of_blocks: int  # Number of blocks
    directory_entry_class: t.Type[OS8Path] = OS8Path

    def __init__(self, file_or_device: t.Union["AbstractFile", "AbstractDevice"], device_type: t.Union[bool, str] = ""):
        if isinstance(file_or_device, AbstractFile):
            if device_type == DECTAPE:
                self.dev = DECtape12Bit(file_or_device)
            else:
                self.dev = RXBlockDevice12Bit(file_or_device)
        elif isinstance(file_or_device, BlockDevice12Bit):
            self.dev = file_or_device
        else:
            raise OSError(errno.EIO, f"Invalid device type for {self.fs_description} filesystem")

    @property
    def num_of_partitions(self) -> int:
        """Get the number of partitions"""
        return 1 + (self.number_of_blocks - 1) // 0o10000

    @property
    def partition_size(self) -> int:  # Size of each partition
        """Get the size of each partition"""
        return self.number_of_blocks // self.num_of_partitions

    def get_partition(self, partition_number: t.Optional[int]) -> OS8Partition:
        """
        Get a partition by number
        """
        if partition_number is None:
            raise FileNotFoundError(errno.ENOENT, "No partition specified")
        try:
            if partition_number < 0 or partition_number >= self.num_of_partitions:
                raise ValueError
            return OS8Partition(self, partition_number)
        except:
            raise FileNotFoundError(errno.ENOENT, "Partition not found", partition_number)

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        strict: t.Union[bool, str] = True,
        device_type: t.Union[bool, str] = "",
        **kwargs: t.Union[bool, str],
    ) -> "OS8Filesystem":
        """
        Mount the OS/8 filesystem from a file
        """
        self = cls(file_or_dev, device_type)
        self.number_of_blocks = self.dev.get_size() // BLOCK_SIZE
        # If the filesystem has only one partition, set it as the current partition
        self.current_partition = 0 if self.num_of_partitions == 1 else None
        if strict:
            try:
                # Check if the filesystem is valid by reading the directory entries
                self.check_os8()
            except Exception:
                if isinstance(file_or_dev, AbstractFile) and device_type == DECTAPE:
                    # Try to read the device not as DECtape
                    self.dev = RXBlockDevice12Bit(file_or_dev)
                    self.check_os8()
                else:
                    raise
        return self

    def check_os8(self) -> None:
        """
        Check if the filesystem is a valid OS/8 filesystem
        """
        # Check if the filesystem is valid by reading the directory entries
        for _ in self.entries_list:
            pass

    def read_words_block(self, block_number: int) -> t.List[int]:
        """
        Read a block as 256 12bit words
        """
        return self.dev.read_words_block(block_number)

    def write_words_block(
        self,
        block_number: int,
        words: t.List[int],
    ) -> None:
        """
        Write 256 12bit words as a block
        """
        self.dev.write_words_block(block_number, words)

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator[t.Union[OS8Path, OS8Partition]]:
        partition, pattern = os8_split_fullname(self.current_partition, pattern, wildcard)
        if partition is None:
            yield from self.get_root().iterdir()
        else:
            yield from self.get_partition(partition).iterdir(
                pattern,
                include_all=include_all,
                expand=expand,
                wildcard=wildcard,
            )

    @property
    def entries_list(self) -> t.Iterator[t.Union[OS8Path, OS8Partition]]:
        yield from self.filter_entries_list("*.*")

    def get_file_entry(self, fullname: str) -> t.Union[OS8Path, OS8Partition]:
        partition, name = os8_split_fullname(self.current_partition, fullname)
        if name:
            # Get the file from the partition
            return self.get_partition(partition).get_file_entry(name)
        else:
            # Get the partition if no filename is specified
            return self.get_partition(partition)

    def get_root(self) -> OS8RootDirectory:
        """
        Get the root directory
        """
        return OS8RootDirectory(self)

    def create_file(
        self,
        fullname: str,
        size: int,  # Size in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> OS8Path:
        partition, name = os8_split_fullname(self.current_partition, fullname, wildcard=False)  # type: ignore
        if partition is None:
            raise FileNotFoundError(errno.ENOENT, "No partition specified")
        if name is None:
            raise FileNotFoundError(errno.ENOENT, "No filename specified")
        return self.get_partition(partition).create_file(name, size, metadata)

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        """
        Change the current partition
        """
        if entry is None:
            self.current_partition = None
        else:
            assert isinstance(entry, OS8Partition)
            partition = entry.partition_number
            if partition < 0 or partition >= self.num_of_partitions:
                raise NotADirectoryError
            self.current_partition = partition

    def show_partitions(self) -> None:
        """
        Show the list of partitions
        """
        for partition_number in range(0, self.num_of_partitions):
            p = self.get_partition(partition_number)
            sys.stdout.write(f"{p.name:<4} {p.get_length():>5}\n")
        return

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        i = 0
        files = 0
        blocks = 0
        partition, pattern = os8_split_fullname(self.current_partition, pattern, wildcard=True)
        if partition is None:
            self.show_partitions()
            return
        part = self.get_partition(partition)
        for x in part.iterdir(pattern, wildcard=True, include_all=True):
            if x.is_empty or x.is_tentative:
                if not options.get("full"):
                    continue
                i = i + 1
                fullname = "<EMPTY>  "
                file_date = ""
            else:
                i = i + 1
                fullname = f"{x.stem:<6}.{x.extension:<2}"
                files = files + 1
                blocks = blocks + x.length
                file_date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or ""
            sys.stdout.write(f"{fullname} {x.first_block_number:04o} {x.length:>3} {file_date:<9}")
            if i % 3 == 0:
                sys.stdout.write("\n")
            else:
                sys.stdout.write("  ")
        if i % 3 != 0:
            sys.stdout.write("\n")
        unused = part.free()
        sys.stdout.write(f"\n{files:>4} FILES IN {blocks:>4} BLOCKS - {unused:>4} FREE BLOCKS\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if arg:
            sys.stdout.write("Filename    Type  Date       Length  Block\n")
            sys.stdout.write("--------    ----  ----       ------  -----\n")
            for entry in self.filter_entries_list(arg, include_all=True):
                if isinstance(entry, OS8Path) and options.get("full"):
                    words = [f"{x:04o}" for x in entry.words]
                    sys.stdout.write(f"{entry}  {words}\n")
                else:
                    sys.stdout.write(f"{entry}\n")
        else:
            is_rx = getattr(self.dev, "is_rx", False)
            sys.stdout.write(f"Number of partitions:     {self.num_of_partitions}\n")
            sys.stdout.write(f"Size of each partition:   {self.partition_size}\n")
            sys.stdout.write(f"Is RX01/RX02:             {'YES' if is_rx else 'NO'}\n")
            for partition_number in range(0, self.num_of_partitions):
                partition = self.get_partition(partition_number)
                sys.stdout.write(f"{partition}\n")
                for segment in partition.read_dir_segments():
                    sys.stdout.write(f"{segment}\n")

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
            if start is None:
                start = 0
            if end is None or end > entry.get_length() - 1:
                end = entry.get_length() - 1
            for block_number in range(start, end + 1):
                words = self.read_words_block(entry.first_block_number + block_number)
                sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                oct_dump(words)
        else:
            if start is None:
                start = 0
                if end is None:  # full disk
                    end = self.get_size() // BLOCK_SIZE - 1
            elif end is None:  # one single block
                end = start
            for block_number in range(start, end + 1):
                words = self.read_words_block(block_number)
                sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                oct_dump(words)

    @classmethod
    def initialize(
        cls, file_or_dev: t.Union["AbstractFile", "AbstractDevice"], **kwargs: t.Union[bool, str]
    ) -> "OS8Filesystem":
        """
        Create an empty OS/8 filesystem
        """
        self = cls(file_or_dev)
        assert isinstance(self.dev, BlockDevice12Bit), "OS/8 filesystem requires a 12-bit block device"
        self.number_of_blocks = self.dev.get_size() // BLOCK_SIZE
        # If the filesystem has only one partition, set it as the current partition
        self.current_partition = 0 if self.num_of_partitions == 1 else None
        # Initialize the partitions
        for partition_number in range(0, self.num_of_partitions):
            partition = OS8Partition(self, partition_number)
            partition.initialize(**kwargs)
        return self

    def get_pwd(self) -> str:
        if self.num_of_partitions == 1 or self.current_partition is None:
            return ""
        else:
            return f"[{self.current_partition}]"

    def path_join(self, path: str, *paths: str) -> str:
        """Join directory path and filename"""
        p = [x for x in [path] + list(paths) if x and x != "/"]
        return "".join(p)
