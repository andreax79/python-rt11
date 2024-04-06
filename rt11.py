#!/usr/bin/env python3

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

import argparse
import cmd
import copy
import errno
import fnmatch
import glob
import io
import math
import os
import shlex
import stat
import sys
import traceback
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Dict, Iterator, List, Optional, Tuple, Union

try:
    import readline
except:
    readline = None  # type: ignore

BLOCK_SIZE = 512

HOMEBLK = 1
DEFAULT_DIR_SEGMENT = 6
DIR_ENTRY_SIZE = 14

HISTORY_FILENAME = "~/.rt_history"
HISTORY_LENGTH = 1000

E_TENT = 1  # Tentative file
E_MPTY = 2  # Empty area
E_PERM = 4  # Permanent file
E_EOS = 8  # End-of-segment marker
E_READ = 64  # Protected from write
E_PROT = 128  # Protected permanent file

#    READ     =    0
#    WRITE    =    0
#    CLOSE    =    1
#    DELETE   =    2
#    LOOKUP   =    3
#    ENTER    =    4
#    RENAME   =    5

DIRECTORY_SEGMENT_HEADER_SIZE = 10
DIRECTORY_SEGMENT_SIZE = BLOCK_SIZE * 2

RX_SECTOR_TRACK = 26  # sectors/track
RX_TRACK_DISK = 77  # track/disk
RX01_SECTOR_SIZE = 128  # RX01 bytes/sector
RX02_SECTOR_SIZE = 256  # RX02 bytes/sector
RX01_SIZE = RX_TRACK_DISK * RX_SECTOR_TRACK * RX01_SECTOR_SIZE  # RX01 Capacity
RX02_SIZE = RX_TRACK_DISK * RX_SECTOR_TRACK * RX02_SECTOR_SIZE  # RX02 Capacity

RAD50 = "\0ABCDEFGHIJKLMNOPQRSTUVWXYZ$.%0123456789:"


def rad2asc(buffer: bytes, position: int = 0) -> str:
    """
    Convert RAD50 2 bytes to 0-3 chars of ASCII
    """
    val = bytes_to_word(buffer, position=position)
    # split out RAD50 digits into three ASCII characters a/b/c
    c = RAD50[val % 0x28]
    b = RAD50[(val // 0x28) % 0x28]
    a = RAD50[val // (0x28 * 0x28)]
    result = ""
    if a != "\0":
        result += a
    if b != "\0":
        result += b
    if c != "\0":
        result += c
    return result


def asc2rad(val: str) -> bytes:
    """
    Convert a string of 3 ASCII to a RAD50 2 bytes
    """
    val1 = [RAD50.find(c.upper()) for c in val] + [0, 0, 0]
    val2 = [x > 0 and x or 0 for x in val1]
    val3 = (val2[0] * 0x28 + val2[1]) * 0x28 + val2[2]
    return word_to_bytes(val3)


def bytes_to_word(val: bytes, position: int = 0) -> int:
    """
    Converts two bytes to a single integer (word)
    """
    return val[1 + position] << 8 | val[0 + position]


def word_to_bytes(val: int) -> bytes:
    """
    Converts an integer (word) to two bytes
    """
    return bytes([val % 256, val // 256])


def rt11_to_date(val: int) -> Optional[date]:
    """
    Translate RT-11 date to Python date
    """
    if val == 0:
        return None
    year = val & int("0000000000011111", 2)
    day = (val & int("0000001111100000", 2)) >> 5
    month = (val & int("0011110000000000", 2)) >> 10
    age = (val & int("1100000000000000", 2)) >> 14
    year = year + 1972 + age * 32
    if day == 0:
        day = 1
    if month == 0:
        month = 1
    try:
        return date(year, month, day)
    except:
        return None


def date_to_rt11(val: Optional[date]) -> int:
    """
    Translate Python date to RT-11 date
    """
    if val is None:
        return 0
    age = (val.year - 1972) // 32
    if age < 0:
        age = 0
    elif age > 3:
        age = 3
    year = (val.year - 1972) % 32
    return year + (val.day << 5) + (val.month << 10) + (age << 14)


def splitdrive(path: str) -> Tuple[str, str]:
    """
    Split a pathname into drive and path.
    """
    result = path.split(":", 1)
    if len(result) < 2:
        return ("DK", path)
    else:
        return (result[0].upper(), result[1])


def rxfactr(blkno: int, sector_size: int) -> int:
    """
    Calculates the physical position on the disk for a given logical sector
    """
    if sector_size == RX01_SECTOR_SIZE or sector_size == RX02_SECTOR_SIZE:
        track = blkno // RX_SECTOR_TRACK + 1
        i = (blkno % RX_SECTOR_TRACK) << 1
        if i >= RX_SECTOR_TRACK:
            i += 1
        sector = ((i + (6 * (track - 1))) % RX_SECTOR_TRACK) + 1
        if track >= RX_TRACK_DISK:
            track = 0
        position = track * 3328 + (sector - 1) * sector_size
    else:
        position = blkno * BLOCK_SIZE
    return position


def rt11_canonical_filename(fullname: Optional[str], wildcard: bool = False) -> str:
    """
    Generate the canonical RT11 name
    """
    fullname = (fullname or "").upper()
    try:
        filename, filetype = fullname.split(".", 1)
    except Exception:
        filename = fullname
        filetype = "*" if wildcard else ""
    if wildcard:
        filename = filename.replace("*", ".")
        filetype = filetype.replace("*", ".")
    filename = rad2asc(asc2rad(filename[0:3])) + rad2asc(asc2rad(filename[3:6]))
    filetype = rad2asc(asc2rad(filetype))
    if wildcard:
        filename = filename.replace(".", "*")
        filetype = filetype.replace(".", "*")
    return f"{filename}.{filetype}"


def ask(prompt: str) -> str:
    """
    Prompt the user for input with the given prompt message
    """
    result = ""
    while not result:
        result = input(prompt).strip()
    return result


class AbstractFile(ABC):
    """Abstract base class for file operations"""

    @abstractmethod
    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """Read block(s) of data from the file"""
        pass

    @abstractmethod
    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """Write block(s) of data to the file"""
        pass

    @abstractmethod
    def get_size(self) -> int:
        """Get file size in bytes."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the file"""
        pass


class NativeFile(AbstractFile):

    f: Union[io.BufferedReader, io.BufferedRandom]

    def __init__(self, filename: str):
        self.filename = os.path.abspath(filename)
        try:
            self.f = open(filename, mode="rb+")
            self.readonly = False
        except OSError:
            self.f = open(filename, mode="rb")
            self.readonly = True
        self.size = os.path.getsize(filename)
        if self.size == RX01_SIZE:
            self.sector_size = RX01_SECTOR_SIZE
        elif self.size == RX02_SIZE:
            self.sector_size = RX02_SECTOR_SIZE
        else:
            self.sector_size = BLOCK_SIZE

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        if block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        elif self.sector_size == BLOCK_SIZE:
            position = rxfactr(block_number, self.sector_size)
            self.f.seek(position)  # not thread safe...
            return self.f.read(number_of_blocks * self.sector_size)
        else:
            ret = []
            start_sector = block_number * BLOCK_SIZE // self.sector_size
            for i in range(0, number_of_blocks * BLOCK_SIZE // self.sector_size):
                blkno = start_sector + i
                position = rxfactr(blkno, self.sector_size)
                self.f.seek(position)  # not thread safe...
                ret.append(self.f.read(self.sector_size))
            return b"".join(ret)

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        """
        if block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        elif self.readonly:
            raise OSError(errno.EROFS, os.strerror(errno.EROFS))
        elif self.sector_size == BLOCK_SIZE:
            self.f.seek(block_number * BLOCK_SIZE)  # not thread safe...
            self.f.write(buffer[0 : number_of_blocks * BLOCK_SIZE])
        else:
            start_sector = block_number * BLOCK_SIZE // self.sector_size
            for i in range(0, number_of_blocks * BLOCK_SIZE // self.sector_size):
                blkno = start_sector + i
                position = rxfactr(blkno, self.sector_size)
                self.f.seek(position)  # not thread safe...
                self.f.write(buffer[i * self.sector_size : (i + 1) * self.sector_size])

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.size

    def close(self) -> None:
        """
        Close the file
        """
        self.f.close()

    def __str__(self) -> str:
        return self.filename


class RT11File(AbstractFile):
    entry: "RT11DirectoryEntry"
    closed: bool
    size: int

    def __init__(self, entry: "RT11DirectoryEntry"):
        self.entry = entry
        self.closed = False
        self.size = entry.length * BLOCK_SIZE

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.entry.length
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return self.entry.segment.fs.read_block(
            self.entry.file_position + block_number,
            number_of_blocks,
        )

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
        self.entry.segment.fs.write_block(
            buffer,
            self.entry.file_position + block_number,
            number_of_blocks,
        )

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.size

    def close(self) -> None:
        """
        Close the file
        """
        self.closed = True

    def __str__(self) -> str:
        return self.entry.fullname


class AbstractDirectoryEntry(ABC):

    @property
    @abstractmethod
    def fullname(self) -> str:
        pass

    @property
    @abstractmethod
    def basename(self) -> str:
        pass

    @property
    @abstractmethod
    def creation_date(self) -> Optional[date]:
        pass

    @abstractmethod
    def delete(self) -> bool:
        pass


class NativeDirectoryEntry(AbstractDirectoryEntry):

    def __init__(self, fullname: str):
        self.native_fullname = fullname
        self.filename = os.path.basename(fullname)
        self.filename, self.filetype = os.path.splitext(self.filename)
        if self.filetype.startswith("."):
            self.filetype = self.filename[1:]
        self.stat = os.stat(fullname)
        self.length = self.stat.st_size  # length in bytes

    @property
    def creation_date(self) -> date:
        return datetime.fromtimestamp(self.stat.st_mtime)

    @property
    def fullname(self) -> str:
        return self.native_fullname

    @property
    def basename(self) -> str:
        return os.path.basename(self.native_fullname)

    def delete(self) -> bool:
        try:
            os.unlink(self.native_fullname)
            return True
        except:
            return False

    def __str__(self) -> str:
        return f"{self.fullname:<11} {self.creation_date or '':<6} length: {self.length:>6}"


class RT11DirectoryEntry(AbstractDirectoryEntry):

    segment: "RT11Segment"
    type: int = 0
    clazz: int = 0
    filename: str = ""
    filetype: str = ""
    length: int = 0
    job: int = 0
    channel: int = 0
    raw_creation_date: int = 0
    extra_bytes: bytes = b''
    file_position: int = 0

    def __init__(self, segment: "RT11Segment"):
        self.segment = segment

    def read(self, buffer: bytes, position: int, file_position: int, extra_bytes: int) -> None:
        self.type = buffer[position]
        self.clazz = buffer[position + 1]
        self.filename = rad2asc(buffer, position + 2) + rad2asc(buffer, position + 4)  # 6 RAD50 chars
        self.filetype = rad2asc(buffer, position + 6)  # 3 RAD50 chars
        self.length = bytes_to_word(buffer, position + 8)  # length in blocks
        self.job = buffer[position + 10]
        self.channel = buffer[position + 11]
        self.raw_creation_date = bytes_to_word(buffer, position + 12)
        self.extra_bytes = buffer[position + 14 : position + 14 + extra_bytes]
        self.file_position = file_position

    def to_bytes(self) -> bytes:
        out = bytearray()
        out.append(self.type)
        out.append(self.clazz)
        out.extend(asc2rad(self.filename[0:3]))
        out.extend(asc2rad(self.filename[3:6]))
        out.extend(asc2rad(self.filetype))
        out.extend(word_to_bytes(self.length))
        out.append(self.job)
        out.append(self.channel)
        out.extend(word_to_bytes(self.raw_creation_date))
        out.extend(self.extra_bytes)
        return bytes(out)

    @property
    def is_empty(self) -> bool:
        return self.clazz & E_MPTY == E_MPTY

    @property
    def is_tentative(self) -> bool:
        return self.clazz & E_TENT == E_TENT

    @property
    def is_permanent(self) -> bool:
        return self.clazz & E_PERM == E_PERM

    @property
    def is_end_of_segment(self) -> bool:
        return self.clazz & E_EOS == E_EOS

    @property
    def is_protected_by_monitor(self) -> bool:
        return self.clazz & E_READ == E_READ

    @property
    def is_protected_permanent(self) -> bool:
        return self.clazz & E_PROT == E_PROT

    @property
    def fullname(self) -> str:
        return f"{self.filename}.{self.filetype}"

    @property
    def basename(self) -> str:
        return self.fullname

    @property
    def creation_date(self) -> Optional[date]:
        return rt11_to_date(self.raw_creation_date)

    def delete(self) -> bool:
        # unset E_PROT,E_TENT,E_READ,E_PROT flasgs, set E_MPTY flag
        self.clazz = self.clazz & ~E_PERM & ~E_TENT & ~E_READ & ~E_PROT | E_MPTY
        self.segment.compact()
        return True

    def __str__(self) -> str:
        return (
            f"{self.fullname:<11} "
            f"{self.creation_date or '          '} "
            f"{self.length:>6} {self.type:5o} {self.clazz:5o} "
            f"{self.job:3d} {self.channel:3d} {self.file_position:6d}"
        )

    def __repr__(self) -> str:
        return str(self)


class RT11Segment(object):
    """
    Volume Directory Segment

    +--------------+
    |5-Word header |
    +--------------+
    |Entries       |
    |.             |
    |.             |
    +--------------+
    |End-of-segment|
    |Marker        |
    +--------------+
    """

    # Block number of this directory segment
    block_number = 0
    # Total number of segments in this directory (1-31)
    num_of_segments = 0
    # Segment number of the next logical directory segment
    next_logical_dir_segment = 0
    # Number of the highest segment currently in use
    highest_segment = 0
    # Number of extra bytes per directory entry
    extra_bytes = 0
    # Block number where the stored data identified by this segment begins
    data_block_number = 0
    # Max directory entires
    max_entries = 0
    # Directory entries
    entries_list: List["RT11DirectoryEntry"] = []

    def __init__(self, fs: "RT11Filesystem"):
        self.fs = fs

    def read(self, block_number: int) -> None:
        """
        Read a Volume Directory Segment from disk
        """
        self.block_number = block_number
        t = self.fs.read_block(self.block_number, 2)
        self.num_of_segments = bytes_to_word(t, 0)
        self.next_logical_dir_segment = bytes_to_word(t, 2)
        self.highest_segment = bytes_to_word(t, 4)
        self.extra_bytes = bytes_to_word(t, 6)
        self.data_block_number = bytes_to_word(t, 8)
        self.entries_list = []

        file_position = self.data_block_number
        dir_entry_size = DIR_ENTRY_SIZE + self.extra_bytes
        self.max_entries = (DIRECTORY_SEGMENT_SIZE - DIRECTORY_SEGMENT_HEADER_SIZE) // dir_entry_size
        for position in range(DIRECTORY_SEGMENT_HEADER_SIZE, DIRECTORY_SEGMENT_SIZE - dir_entry_size, dir_entry_size):
            dir_entry = RT11DirectoryEntry(self)
            dir_entry.read(t, position, file_position, self.extra_bytes)
            file_position = file_position + dir_entry.length
            self.entries_list.append(dir_entry)
            if dir_entry.is_end_of_segment:
                break

    def to_bytes(self) -> bytes:
        out = bytearray()
        out.extend(word_to_bytes(self.num_of_segments))
        out.extend(word_to_bytes(self.next_logical_dir_segment))
        out.extend(word_to_bytes(self.highest_segment))
        out.extend(word_to_bytes(self.extra_bytes))
        out.extend(word_to_bytes(self.data_block_number))
        for entry in self.entries_list:
            out.extend(entry.to_bytes())
        return out + (b"\0" * (BLOCK_SIZE * 2 - len(out)))

    def write(self) -> None:
        self.fs.write_block(self.to_bytes(), self.block_number, 2)

    @property
    def next_block_number(self) -> int:
        """Block number of the next directory segment"""
        if self.next_logical_dir_segment == 0:
            return 0
        else:
            return (self.next_logical_dir_segment - 1) * 2 + self.fs.dir_segment

    def compact(self) -> None:
        """Compact multiple unused entries"""
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
                if entry.is_end_of_segment:
                    prev_empty_entry.clazz = prev_empty_entry.clazz | E_EOS
        self.entries_list = new_entries_list
        self.write()

    def insert_entry_after(self, entry: "RT11DirectoryEntry", entry_number: int, length: int) -> None:
        if entry.length == length:
            return
        new_entry = copy.copy(entry)  # new empty space entry
        if entry.is_end_of_segment:
            new_entry.clazz = E_EOS
            entry.clazz = entry.clazz - E_EOS
        new_entry.length = entry.length - length
        new_entry.file_position = entry.file_position + length
        entry.length = length
        self.entries_list.insert(entry_number + 1, new_entry)
        entry.segment.write()

    def __str__(self) -> str:
        buf = io.StringIO()
        buf.write("\n*Segment\n")
        buf.write(f"Block number:          {self.block_number}\n")
        buf.write(f"Next dir segment:      {self.next_block_number}\n")
        buf.write(f"Number of segments:    {self.num_of_segments}\n")
        buf.write(f"Highest segment:       {self.highest_segment}\n")
        buf.write(f"Max entries:           {self.max_entries}\n")
        buf.write(f"Data block:            {self.data_block_number}\n")
        buf.write("\nNum  File        Date       Length  Type Class Job Chn  Block\n\n")
        for i, x in enumerate(self.entries_list):
            buf.write(f"{i:02d}#  {x}\n")
        return buf.getvalue()


class AbstractFilesystem(object):
    """Abstract base class for filesystem implementations"""

    @abstractmethod
    def filter_entries_list(
        self, pattern: Optional[str], include_all: bool = False
    ) -> Iterator["AbstractDirectoryEntry"]:
        """Filter directory entries based on a pattern"""
        pass

    @property
    @abstractmethod
    def entries_list(self) -> Iterator["AbstractDirectoryEntry"]:
        """Property to get an iterator of directory entries"""
        pass

    @abstractmethod
    def get_file_entry(self, fullname: str) -> Optional["AbstractDirectoryEntry"]:
        """Get the directory entry for a file"""
        pass

    @abstractmethod
    def open_file(self, fullname: str) -> "AbstractFile":
        """Open a file"""
        pass

    @abstractmethod
    def read_bytes(self, fullname: str) -> bytes:
        """Get the content of a file"""
        pass

    @abstractmethod
    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: Optional[date] = None,
    ) -> bool:
        """Write content to a file"""
        pass

    @abstractmethod
    def create_file(
        self,
        fullname: str,
        length: int,
        creation_date: Optional[date] = None,
    ) -> Optional["AbstractDirectoryEntry"]:
        """Create a new file with a given length in number of blocks"""
        pass

    @abstractmethod
    def chdir(self, fullname: str) -> bool:
        """Change the current directory"""
        pass

    @abstractmethod
    def isdir(self, fullname: str) -> bool:
        """Check if the given path is a directory"""
        pass

    @abstractmethod
    def exists(self, fullname: str) -> bool:
        """Check if the given path exists"""
        pass

    @abstractmethod
    def dir(self, pattern: Optional[str]) -> None:
        """List directory contents."""
        pass

    @abstractmethod
    def examine(self, block: Optional[str]) -> None:
        """Examine the filesytem"""
        pass

    @abstractmethod
    def initialize(self) -> None:
        """Initialize the filesytem"""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close the filesytem"""
        pass

    @abstractmethod
    def get_pwd(self) -> str:
        """Get the current directory"""
        pass


class NativeFilesystem(AbstractFilesystem):

    def __init__(self, base: Optional[str] = None):
        self.base = base or "/"
        if not base:
            self.pwd = os.getcwd()
        elif os.getcwd().startswith(base):
            self.pwd = os.getcwd()[len(base) :]
        else:
            self.pwd = os.path.sep

    def filter_entries_list(
        self, pattern: Optional[str], include_all: bool = False
    ) -> Iterator["NativeDirectoryEntry"]:
        if not pattern:
            for filename in os.listdir(os.path.join(self.base, self.pwd)):
                try:
                    v = NativeDirectoryEntry(os.path.join(self.base, self.pwd, filename))
                except:
                    v = None
                if v is not None:
                    yield v
        else:
            if not pattern.startswith("/") and not pattern.startswith("\\"):
                pattern = os.path.join(self.base, self.pwd, pattern)
            if os.path.isdir(pattern):
                pattern = os.path.join(pattern, "*")
            for filename in glob.glob(pattern):
                try:
                    v = NativeDirectoryEntry(filename)
                except:
                    v = None
                if v is not None:
                    yield v

    @property
    def entries_list(self) -> Iterator["NativeDirectoryEntry"]:
        dir = self.pwd
        for filename in os.listdir(dir):
            yield NativeDirectoryEntry(os.path.join(dir, filename))

    def get_file_entry(self, fullname: str) -> Optional[NativeDirectoryEntry]:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        return NativeDirectoryEntry(fullname)

    def open_file(self, fullname: str) -> NativeFile:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        return NativeFile(fullname)

    def read_bytes(self, fullname: str) -> bytes:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        with open(fullname, "rb") as f:
            return f.read()

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: Optional[date] = None,
    ) -> bool:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        with open(fullname, "wb") as f:
            f.write(content)
        if creation_date:
            # Set the creation and modification date of the file
            ts = datetime.combine(creation_date, datetime.min.time()).timestamp()
            os.utime(fullname, (ts, ts))
        return True

    def create_file(
        self,
        fullname: str,
        length: int,
        creation_date: Optional[date] = None,
    ) -> Optional[NativeDirectoryEntry]:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        with open(fullname, "wb") as f:
            f.truncate(length * BLOCK_SIZE)
        if creation_date:
            # Set the creation and modification date of the file
            ts = datetime.combine(creation_date, datetime.min.time()).timestamp()
            os.utime(fullname, (ts, ts))
        return NativeDirectoryEntry(fullname)

    def chdir(self, fullname: str) -> bool:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        fullname = os.path.normpath(fullname)
        if os.path.isdir(os.path.join(self.base, fullname)):
            self.pwd = fullname
            # Change the current working directory
            os.chdir(os.path.join(self.base, fullname))
            return True
        else:
            return False

    def isdir(self, fullname: str) -> bool:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        return os.path.isdir(os.path.join(self.base, fullname))

    def exists(self, fullname: str) -> bool:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        return os.path.exists(os.path.join(self.base, fullname))

    def dir(self, pattern: Optional[str]) -> None:
        for x in self.filter_entries_list(pattern):
            mode = x.stat.st_mode
            if stat.S_ISREG(mode):
                type = "%s" % x.length
            elif stat.S_ISDIR(mode):
                type = "DIRECTORY      "
            elif stat.S_ISLNK(mode):
                type = "LINK           "
            elif stat.S_ISFIFO(mode):
                type = "FIFO           "
            elif stat.S_ISSOCK(mode):
                type = "SOCKET         "
            elif stat.S_ISCHR(mode):
                type = "CHAR DEV       "
            elif stat.S_ISBLK(mode):
                type = "BLOCK DEV      "
            else:
                type = "?"
            sys.stdout.write(
                "%15s %19s %s\n"
                % (
                    type,
                    x.creation_date and x.creation_date.strftime("%d-%b-%Y %H:%M ") or "",
                    x.basename,
                )
            )

    def examine(self, block: Optional[str]) -> None:
        pass

    def initialize(self) -> None:
        pass

    def close(self) -> None:
        pass

    def get_pwd(self) -> str:
        return self.pwd

    def __str__(self) -> str:
        return self.base


class RT11Filesystem(AbstractFilesystem):

    # First directory segment block
    dir_segment: int = DEFAULT_DIR_SEGMENT
    # System version
    ver: str = ""
    # Volume Identification
    id: str = ""
    # Owner name
    owner: str = ""
    # System Identification
    sys_id: str = ""

    def __init__(self, file: "AbstractFile"):
        self.f = file
        self.read_home()

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

    def read_home(self) -> None:
        """Read home block"""
        t = self.read_block(HOMEBLK)
        self.dir_segment = bytes_to_word(t[468:470]) or DEFAULT_DIR_SEGMENT
        self.ver = rad2asc(t[470:472])
        self.id = t[472:484].decode("ascii")
        self.owner = t[484:496].decode("ascii")
        self.sys_id = t[496:508].decode("ascii")
        self.checksum = bytes_to_word(t[510:512])

    def write_home(self) -> None:
        """Write home block"""
        # Convert data to bytes
        dir_segment_bytes = word_to_bytes(self.dir_segment)
        ver_bytes = asc2rad(self.ver)
        id_bytes = self.id.encode("ascii")
        owner_bytes = self.owner.encode("ascii")
        sys_id_bytes = self.sys_id.encode("ascii")
        checksum_bytes = word_to_bytes(0)
        # Create a byte array for the home block
        home_block = bytearray([0] * BLOCK_SIZE)
        # Fill the byte array with the data
        home_block[468:470] = dir_segment_bytes
        home_block[470:472] = ver_bytes
        home_block[472:484] = id_bytes.ljust(12, b'\0')  # Pad with null bytes if needed
        home_block[484:496] = owner_bytes.ljust(12, b'\0')
        home_block[496:508] = sys_id_bytes.ljust(12, b'\0')
        home_block[510:512] = checksum_bytes
        # Write the block
        self.write_block(home_block, HOMEBLK)

    def read_dir_segments(self) -> Iterator["RT11Segment"]:
        """Read directory segments"""
        next_block_number = self.dir_segment
        while next_block_number != 0:
            segment = RT11Segment(self)
            segment.read(next_block_number)
            next_block_number = segment.next_block_number
            yield segment

    def filter_entries_list(self, pattern: Optional[str], include_all: bool = False) -> Iterator["RT11DirectoryEntry"]:
        if pattern:
            pattern = rt11_canonical_filename(pattern, wildcard=True)
        for segment in self.read_dir_segments():
            for entry in segment.entries_list:
                if (not pattern) or fnmatch.fnmatch(entry.fullname, pattern):
                    if not include_all and (entry.is_empty or entry.is_tentative):
                        continue
                    yield entry

    @property
    def entries_list(self) -> Iterator["RT11DirectoryEntry"]:
        for segment in self.read_dir_segments():
            for entry in segment.entries_list:
                yield entry

    def get_file_entry(self, fullname: str) -> Optional[RT11DirectoryEntry]:  # fullname=filename+ext
        fullname = rt11_canonical_filename(fullname)
        for entry in self.entries_list:
            if entry.fullname == fullname and entry.is_permanent:
                return entry
        return None

    def open_file(self, fullname: str) -> RT11File:
        entry = self.get_file_entry(fullname)
        if not entry:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return RT11File(entry)

    def read_bytes(self, fullname: str) -> bytes:  # fullname=filename+ext
        entry = self.get_file_entry(fullname)
        if not entry:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return self.read_block(entry.file_position, entry.length)

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: Optional[date] = None,
    ) -> bool:
        length = int(math.ceil(len(content) * 1.0 / BLOCK_SIZE))
        entry = self.create_file(fullname, length, creation_date)
        if not entry:
            return False
        content = content + (b"\0" * BLOCK_SIZE)
        self.write_block(content, entry.file_position, entry.length)
        return True

    def create_file(
        self,
        fullname: str,
        length: int,  # length in blocks
        creation_date: Optional[date] = None,  # optional creation date
    ) -> Optional[RT11DirectoryEntry]:
        fullname = os.path.basename(fullname)
        entry: Optional[RT11DirectoryEntry] = self.get_file_entry(fullname)
        if entry is not None:
            entry.delete()
        return self.allocate_space(fullname, length, creation_date)

    def split_segment(self, entry: RT11DirectoryEntry) -> bool:
        # entry is the last entry of the old_segment, new new segment will contain all the entries after that
        old_segment = entry.segment
        # find the new segment number
        segments = list(self.read_dir_segments())
        first_segment = segments[0]
        sn = [x.block_number for x in segments]
        p = 0
        segment_number = None
        for i in range(self.dir_segment, self.dir_segment + (first_segment.num_of_segments * 2), 2):
            p = p + 1
            if i not in sn:
                segment_number = i
                break
        if segment_number is None:
            return False
        # create the new segment
        segment = RT11Segment(self)
        segment.block_number = segment_number
        segment.num_of_segments = first_segment.num_of_segments
        segment.next_logical_dir_segment = old_segment.next_logical_dir_segment
        segment.highest_segment = 1
        segment.extra_bytes = segments[0].extra_bytes
        segment.data_block_number = entry.file_position + entry.length
        # set the next segment of the last segment
        old_segment.next_logical_dir_segment = (segment.block_number - self.dir_segment) // 2 + 1
        entry.clazz = entry.clazz | E_EOS  # entry is the last entry of the old segment
        first_segment.num_of_segments = len(segments)  # update the total num of segments
        first_segment.write()

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
        entry.clazz = entry.clazz | E_EOS
        segment.write()
        return True

    def allocate_space(
        self,
        fullname: str,  # fullname=filename+ext, length in blocks
        length: int,  # length in blocks
        creation_date: Optional[date] = None,  # optional creation date
    ) -> RT11DirectoryEntry:
        """
        Allocate space for a new file
        """
        entry: Optional[RT11DirectoryEntry] = None
        entry_number: Optional[int] = None
        # Search for an empty entry to be splitted
        for segment in self.read_dir_segments():
            for i, e in enumerate(segment.entries_list):
                if e.is_empty and e.length >= length:
                    if entry is None or entry.length > e.length:
                        entry = e
                        entry_number = i
                        if entry.length == length:
                            break
        if entry is None:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), fullname)
        # If the entry length is equal to the requested length, don't create the new empty entity
        if entry.length != length:
            if len(entry.segment.entries_list) >= entry.segment.max_entries:
                if not self.split_segment(entry):
                    raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), fullname)
            entry.segment.insert_entry_after(entry, entry_number, length)
        # Fill the entry
        t = os.path.splitext(fullname.upper())
        entry.filename = t[0]
        entry.filetype = t[1] and t[1][1:] or ""
        entry.raw_creation_date = date_to_rt11(creation_date)
        entry.job = 0
        entry.channel = 0
        if entry.is_end_of_segment:
            entry.clazz = E_PERM | E_EOS
        else:
            entry.clazz = E_PERM
        entry.length = length
        # Write the segment
        entry.segment.write()
        return entry

    def chdir(self, fullname: str) -> bool:
        return False

    def isdir(self, fullname: str) -> bool:
        return False

    def exists(self, fullname: str) -> bool:  # fullname=filename+ext
        entry = self.get_file_entry(fullname)
        return entry is not None

    def dir(self, pattern: Optional[str]) -> None:
        i = 0
        files = 0
        blocks = 0
        unused = 0
        for x in self.filter_entries_list(pattern, include_all=True):
            if (
                not x.is_empty
                and not x.is_tentative
                and not x.is_permanent
                and not x.is_protected_permanent
                and not x.is_protected_by_monitor
            ):
                continue
            i = i + 1
            if x.is_empty or x.is_tentative:
                fullname = "< UNUSED >"
                date = ""
                unused = unused + x.length
            else:
                fullname = x.is_empty and x.filename or "%-6s.%-3s" % (x.filename, x.filetype)
                date = x.creation_date and x.creation_date.strftime("%d-%b-%y") or ""
            if x.is_permanent:
                files = files + 1
                blocks = blocks + x.length
            if x.is_protected_permanent:
                attr = "P"
            elif x.is_protected_by_monitor:
                attr = "A"
            else:
                attr = " "
            sys.stdout.write("%10s %5d%1s %9s" % (fullname, x.length, attr, date))
            # sys.stdout.write(" %8d " % (x.file_position))
            if i % 2 == 1:
                sys.stdout.write("    ")
            else:
                sys.stdout.write("\n")
        if i % 2 == 1:
            sys.stdout.write("\n")
        sys.stdout.write(" %d Files, %d Blocks\n" % (files, blocks))
        sys.stdout.write(" %d Free blocks\n" % unused)

    def dump(self, name_or_block: str) -> None:
        bytes_per_line = 16

        def hex_dump(i: int, data: bytes) -> str:
            hex_str = ' '.join([f"{x:02x}" for x in data])
            ascii_str = ''.join([chr(x) if 32 <= x <= 126 else "." for x in data])
            return f"{i:08x}   {hex_str.ljust(3 * bytes_per_line)}  {ascii_str}\n"

        if name_or_block.isnumeric():
            data = self.read_block(int(name_or_block))
        else:
            data = self.read_bytes(name_or_block)
        for i in range(0, len(data), bytes_per_line):
            sys.stdout.write(hex_dump(i, data[i : i + bytes_per_line]))

    def examine(self, name_or_block: Optional[str]) -> None:
        if name_or_block:
            self.dump(name_or_block)
        else:
            sys.stdout.write(f"Directory segment:     {self.dir_segment}\n")
            sys.stdout.write(f"System version:        {self.ver}\n")
            sys.stdout.write(f"Volume identification: {self.id}\n")
            sys.stdout.write(f"Owner name:            {self.owner}\n")
            sys.stdout.write(f"System identification: {self.sys_id}\n")
            for segment in self.read_dir_segments():
                sys.stdout.write(f"{segment}\n")

    def initialize(self) -> None:
        """Write an RT–11 empty device directory"""
        size = self.f.get_size()
        # Adjust the size for RX01/RX02 (skip track 0)
        if size == RX01_SIZE:
            size = size - RX_SECTOR_TRACK * RX01_SECTOR_SIZE
        elif size == RX02_SIZE:
            size = size - RX_SECTOR_TRACK * RX02_SECTOR_SIZE
        length = size // BLOCK_SIZE
        # Determinate the number of directory segments
        if length >= 18000:  # 9Mb
            # DW (RD51) 10Mb
            # DL (RL02) 10.4M
            # DM (RK06) 13.8M
            num_of_segments = 31
        elif length >= 4000:  # 2Mb
            # RK (RK05) 2.45M
            # DW (RD50) 5Mb
            # DL (RL01) 5.2M
            num_of_segments = 16
        elif length >= 800:  # 400Kb
            # DZ (RX50) 400K
            # DY (RX02) 512K
            num_of_segments = 4
        else:
            # DX (RX01) 256K
            num_of_segments = 1
        # Write the home block
        self.dir_segment = DEFAULT_DIR_SEGMENT
        self.ver = "V05"
        self.id = ""
        self.owner = ""
        self.sys_id = "DECRT11A"
        self.write_home()
        # Write the directory segment
        segment = RT11Segment(self)
        segment.block_number = self.dir_segment
        segment.num_of_segments = num_of_segments
        segment.next_logical_dir_segment = 0
        segment.highest_segment = 1
        segment.extra_bytes = 0
        segment.data_block_number = self.dir_segment + (num_of_segments * 2)
        # first entry
        dir_entry = RT11DirectoryEntry(segment)
        dir_entry.file_position = segment.data_block_number
        dir_entry.length = length - dir_entry.file_position
        dir_entry.clazz = 2
        dir_entry.filename = "EMPTY"
        dir_entry.filetype = "FIL"
        segment.entries_list.append(dir_entry)
        # second entry
        dir_entry = RT11DirectoryEntry(segment)
        dir_entry.file_position = size
        dir_entry.clazz = 8
        segment.entries_list.append(dir_entry)
        segment.write()

    def close(self) -> None:
        self.f.close()

    def get_pwd(self) -> str:
        return ""

    def __str__(self) -> str:
        return str(self.f)


class Volumes(object):
    """
    Logical Device Names

    SY: System device, the device from which this program was started
    DK: Default storage device (initially the same as SY:)
    """

    volumes: Dict[str, Union[AbstractFilesystem, str]]

    def __init__(self) -> None:
        self.volumes: Dict[str, Union[AbstractFilesystem, str]] = {}
        if self._drive_letters():
            # windows
            for letter in self._drive_letters():
                self.volumes[letter] = NativeFilesystem("%s:" % letter)
            current_drive = os.getcwd().split(":")[0]
            self.volumes["SY"] = self.volumes[current_drive]
        else:
            # posix
            self.volumes["SY"] = NativeFilesystem()
        self.volumes["DK"] = "SY"

    def _drive_letters(self) -> list[str]:
        try:
            import string
            from ctypes import windll  # type: ignore

            drives = []
            bitmask = windll.kernel32.GetLogicalDrives()
            for c in string.ascii_uppercase:
                if bitmask & 1:
                    drives.append(c)
                bitmask >>= 1
            return drives
        except Exception:
            return []

    def get(self, volume_id: Optional[str], cmd: str = "KMON") -> AbstractFilesystem:
        if volume_id is None:
            volume_id = "DK"
        elif volume_id.endswith(":"):
            volume_id = volume_id[:-1]
        if volume_id.upper() == "LAST":
            volume_id = self.last()
        v = self.volumes.get(volume_id.upper())
        if isinstance(v, str):
            v = self.volumes.get(v.upper())
        if v is None:
            raise Exception("?%s-F-Illegal volume %s:" % (cmd, volume_id))
        return v

    def chdir(self, path: str) -> bool:
        volume_id, fullname = splitdrive(path)
        if volume_id.upper() == "LAST":
            volume_id = self.last()
        try:
            fs = self.get(volume_id)
        except Exception:
            return False
        if fullname and not fs.chdir(fullname):
            return False
        if volume_id != "DK":
            self.set_default_volume(volume_id)
        return True

    def get_pwd(self) -> str:
        try:
            return "%s:%s" % (self.volumes.get("DK"), self.get("DK").get_pwd())
        except:
            return "%s:???" % (self.volumes.get("DK"))

    def set_default_volume(self, volume_id: str) -> None:
        """Set the default volume"""
        if not volume_id:
            return
        if volume_id.endswith(":"):
            volume_id = volume_id[:-1]
        if volume_id.upper() == "LAST":
            volume_id = self.last()
        volume_id = volume_id.upper()
        if volume_id != "DK" and volume_id in self.volumes:
            self.volumes["DK"] = volume_id
        else:
            raise Exception("?KMON-F-Invalid volume")

    def mount(self, path: str, logical: str, verbose: bool = False) -> None:
        logical = logical.split(":")[0].upper()
        if logical in ("SY", "DK") or not logical:
            raise Exception(f"?MOUNT-F-Illegal volume {logical}:")
        volume_id, fullname = splitdrive(path)
        fs = self.get(volume_id, cmd="MOUNT")
        try:
            self.volumes[logical] = RT11Filesystem(fs.open_file(fullname))
            sys.stdout.write(f"?MOUNT-I-Disk {path} mounted to {logical}:\n")
        except:
            if verbose:
                traceback.print_exc()
            sys.stdout.write(f"?MOUNT-F-Error mounting {path} to {logical}:\n")

    def dismount(self, logical: str) -> None:
        logical = logical.split(":")[0].upper()
        if logical in ("SY", "DK") or logical not in self.volumes:
            raise Exception(f"?DISMOUNT-F-Illegal volume {logical}:")
        del self.volumes[logical]

    def last(self) -> str:
        return list(self.volumes.keys())[-1]


class Shell(cmd.Cmd):
    verbose = False

    def __init__(self, verbose: bool = False):
        cmd.Cmd.__init__(self)
        self.verbose = verbose
        self.volumes = Volumes()
        # self.prompt="."
        self.postcmd(False, "")
        self.history_file = os.path.expanduser(HISTORY_FILENAME)
        # Init readline and history
        if readline is not None:
            if sys.platform == "darwin":
                readline.parse_and_bind("bind ^I rl_complete")
            else:
                readline.parse_and_bind("tab: complete")
                readline.parse_and_bind("set bell-style none")
            readline.set_completer(self.complete)
            try:
                if self.history_file:
                    readline.set_history_length(HISTORY_LENGTH)
                    readline.read_history_file(self.history_file)
            except IOError:
                pass

    def completenames(self, text, *ignored):
        dotext = "do_" + text.lower()
        return ["%s " % a[3:] for a in self.get_names() if a.startswith(dotext)] + [
            "%s:" % a for a in self.volumes.volumes.keys() if a.startswith(text.upper())
        ]

    def completedefault(self, text, state, *ignored):
        def add_slash(fs: AbstractFilesystem, filename: str) -> str:
            try:
                if fs.isdir(filename):
                    filename = filename + "/"
                return filename.replace(" ", "\\ ")
            except:
                pass
            return filename

        try:
            has_volume_id = ":" in text
            if text:
                volume_id, path = splitdrive(text)
            else:
                volume_id = None
                path = ""
            pattern = path + "*"
            fs = self.volumes.get(volume_id)
            result: List[str] = []
            for x in fs.filter_entries_list(pattern):
                if has_volume_id:
                    result.append("%s:%s" % (volume_id, add_slash(fs, x.fullname)))
                else:
                    result.append("%s" % add_slash(fs, x.fullname))
            return result
        except Exception:
            pass  # no problem :-)
        return []

    def postloop(self) -> None:
        if readline is not None:
            # Cleanup and write history file
            readline.set_completer(None)
            try:
                if self.history_file:
                    readline.set_history_length(HISTORY_LENGTH)
                    readline.write_history_file(self.history_file)
            except:
                pass

    def cmdloop(self, intro: Optional[str] = None) -> None:
        self.update_prompt()
        try:
            return cmd.Cmd.cmdloop(self, intro)
        except KeyboardInterrupt:
            sys.stdout.write("\n")

    def update_prompt(self) -> None:
        self.prompt = "[%s] " % self.volumes.get_pwd()

    def postcmd(self, stop: bool, line: str) -> bool:
        self.update_prompt()
        return stop

    def onecmd(self, line: str, catch_exceptions: bool = True, batch: bool = False) -> bool:
        try:
            return cmd.Cmd.onecmd(self, line)
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            sys.stdout.write("\n")
            return False
        except SystemExit as ex:
            if not catch_exceptions:
                raise ex
            return True
        except Exception as ex:
            if not catch_exceptions:
                raise ex
            message = str(sys.exc_info()[1])
            sys.stdout.write(f"{message}\n")
            if self.verbose:
                traceback.print_exc()
            if batch:
                raise ex
            return False

    def parseline(self, line: str) -> Tuple[Optional[str], Optional[str], str]:
        """
        Parse the line into a command name and arguments
        """
        line = line.strip()
        if not line:
            return None, None, line
        elif line[0] == '?':
            line = f"help {line[1:]}"
        elif line[0] == '!':
            line = f"shell {line[1:]}"
        elif line[0] == '@':
            line = f"batch {line[1:]}"
        i, n = 0, len(line)
        while i < n and line[i] in self.identchars:
            i = i + 1
        cmd, arg = line[:i], line[i:].strip()
        return cmd.lower(), arg, line

    def default(self, line: str) -> None:
        if line.endswith(":"):
            self.volumes.set_default_volume(line)
        else:
            raise Exception("?KMON-F-Illegal command")

    def emptyline(self) -> bool:
        sys.stdout.write("\n")
        return False

    def do_dir(self, line: str) -> None:
        # fmt: off
        """
DIR             Lists file directories

  SYNTAX
        DIR [[volume:][filespec]]

  SEMANTICS
        This command generates a listing of the directory you specify.

  EXAMPLES
        DIR A:*.SAV
        DIR SY:

        """
        # fmt: on
        args = shlex.split(line)
        if len(args) > 1:
            sys.stdout.write("?DIR-F-Too many arguments\n")
            return
        if args:
            volume_id, pattern = splitdrive(args[0])
        else:
            volume_id = None
            pattern = None
        fs = self.volumes.get(volume_id, cmd="DIR")
        fs.dir(pattern)

    def do_ls(self, line: str) -> None:
        self.do_dir(line)

    def do_type(self, line: str) -> None:
        # fmt: off
        """
TYPE            Outputs files to the terminal

  SYNTAX
        TYPE [volume:]filespec

  EXAMPLES
        TYPE A.TXT

        """
        # fmt: on
        args = shlex.split(line)
        if not args:
            line = ask("File? ")
            args = shlex.split(line)
        if len(args) > 1:
            sys.stdout.write("?TYPE-F-Too many arguments\n")
            return
        volume_id, pattern = splitdrive(args[0])
        fs = self.volumes.get(volume_id, cmd="TYPE")
        match = False
        for x in fs.filter_entries_list(pattern):
            match = True
            content = fs.read_bytes(x.fullname)
            if content is not None:
                os.write(sys.stdout.fileno(), content)
                sys.stdout.write("\n")
        if not match:
            raise Exception("?TYPE-F-No files")

    def do_copy(self, line: str) -> None:
        # fmt: off
        """
COPY            Copies files

  SYNTAX
        COPY [input-volume:]input-filespec [output-volume:][output-filespec]

  EXAMPLES
        COPY *.TXT DK:

        """
        # fmt: on
        args = shlex.split(line)
        if len(args) > 2:
            sys.stdout.write("?COPY-F-Too many arguments\n")
            return
        cfrom = len(args) > 0 and args[0]
        to = len(args) > 1 and args[1]
        if not cfrom:
            cfrom = ask("From? ")
        from_volume_id, cfrom = splitdrive(cfrom)
        from_fs = self.volumes.get(from_volume_id, cmd="COPY")
        if not to:
            to = ask("To? ")
        to_volume_id, to = splitdrive(to)
        to_fs = self.volumes.get(to_volume_id, cmd="COPY")
        from_len = len(list(from_fs.filter_entries_list(cfrom)))
        from_list = from_fs.filter_entries_list(cfrom)
        if from_len == 0:  # No files
            raise Exception("?COPY-F-No files")
        elif from_len == 1:  # One file to be copied
            source = list(from_list)[0]
            if not to:
                to = os.path.join(self.volumes.get(to_volume_id).get_pwd(), source.fullname)
            elif to and to_fs.isdir(to):
                to = os.path.join(to, source.basename)
            content = from_fs.read_bytes(source.fullname)
            entry = from_fs.get_file_entry(source.fullname)
            if not entry:
                sys.stdout.write(f"?COPY-F-Error copying {source.fullname}\n")
                return
            sys.stdout.write("%s:%s -> %s:%s\n" % (from_volume_id, source.fullname, to_volume_id, to))
            if not to_fs.write_bytes(to, content, entry.creation_date):
                sys.stdout.write(f"?COPY-F-Error copying {source.fullname}\n")
        else:
            if not to:
                to = self.volumes.get(to_volume_id).get_pwd()
            elif not to_fs.isdir(to):
                raise Exception("?COPY-F-Target must be a volume or a directory")
            for entry in from_fs.filter_entries_list(cfrom):
                if to:
                    target = os.path.join(to, entry.basename)
                else:
                    target = entry.basename
                content = from_fs.read_bytes(entry.fullname)
                sys.stdout.write("%s:%s -> %s:%s\n" % (from_volume_id, entry.fullname, to_volume_id, target))
                if not to_fs.write_bytes(target, content, entry.creation_date):
                    sys.stdout.write(f"?COPY-F-Error copying {entry.fullname}\n")

    def do_del(self, line: str) -> None:
        # fmt: off
        """
DEL             Removes files from a volume

  SYNTAX
        DEL [volume:]filespec

  SEMANTICS
        This command deletes the files you specify from the volume.

  EXAMPLES
        DEL *.OBJ

        """
        # fmt: on
        args = shlex.split(line)
        if not args:
            line = ask("Files? ")
            args = shlex.split(line)
        volume_id, pattern = splitdrive(args[0])
        fs = self.volumes.get(volume_id, cmd="DEL")
        match = False
        for x in fs.filter_entries_list(pattern):
            match = True
            if not x.delete():
                sys.stdout.write("?DEL-F-Error deleting %s\n" % x.fullname)
        if not match:
            raise Exception("?DEL-F-No files")

    def do_examine(self, line: str) -> None:
        # fmt: off
        """
EXAMINE         Examines disk/block/file structure

  SYNTAX
        EXAMINE volume:[filespec/block num]

        """
        # fmt: on
        volume_id, block = splitdrive(line or "")
        fs = self.volumes.get(volume_id)
        fs.examine(block)

    def do_create(self, line: str) -> None:
        # fmt: off
        """
CREATE          Creates a file with a specific name and size

  SYNTAX
        CREATE [volume:]filespec size

  SEMANTICS
        Filespec is the device name, file name, and file type
        of the file to create.
        The size specifies the number of blocks to allocate.

  EXAMPLES
        CREATE NEW.DSK 200

        """
        # fmt: on
        args = shlex.split(line)
        if len(args) > 2:
            sys.stdout.write("?CREATE-F-Too many arguments\n")
            return
        path = len(args) > 0 and args[0]
        size = len(args) > 1 and args[1]
        if not path:
            path = ask("File? ")
        if not size:
            size = ask("Size? ")
        try:
            length = int(size)
            if length < 0:
                raise ValueError
        except ValueError:
            raise Exception("?KMON-F-Invalid value specified with option")
        volume_id, fullname = splitdrive(path)
        fs = self.volumes.get(volume_id, cmd="CREATE")
        fs.create_file(fullname, length)

    def do_mount(self, line: str) -> None:
        # fmt: off
        """
MOUNT           Assigns a logical disk unit to a file

  SYNTAX
        MOUNT volume: [volume:]filespec

  SEMANTICS
        Associates a logical disk unit with a file.

  EXAMPLES
        MOUNT AB: SY:AB.DSK

        """
        # fmt: on
        args = shlex.split(line)
        if len(args) > 2:
            sys.stdout.write("?MOUNT-F-Too many arguments\n")
            return
        logical = len(args) > 0 and args[0]
        path = len(args) > 1 and args[1]
        if not logical:
            logical = ask("Volume? ")
        if not path:
            path = ask("File? ")
        self.volumes.mount(path, logical, verbose=self.verbose)

    def do_dismount(self, line: str) -> None:
        # fmt: off
        """
DISMOUNT        Disassociates a logical disk assignment from a file

  SYNTAX
        DISMOUNT logical_name

  SEMANTICS
        Removes the association of a logical disk unit with its currently
        assigned file, thereby freeing it to be assigned to another file.

  EXAMPLES
        DISMOUNT AB:

        """
        # fmt: on
        args = shlex.split(line)
        if len(args) > 1:
            sys.stdout.write("?DISMOUNT-F-Too many arguments\n")
            return
        if args:
            logical = args[0]
        else:
            logical = ask("Volume? ")
        self.volumes.dismount(logical)

    def do_initialize(self, line: str) -> None:
        # fmt: off
        """
INITIALIZE      Writes an RT–11 empty device directory on the specified volume

  SYNTAX
        INITIALIZE volume:

        """
        # fmt: on
        if not line:
            line = ask("Volume? ")
        fs = self.volumes.get(line)
        fs.initialize()

    def do_cd(self, line: str) -> None:
        # fmt: off
        """
CD              Changes or displays the current working drive and directory

  SYNTAX
        CD [[volume:][filespec]]

        """
        # fmt: on
        args = shlex.split(line)
        if len(args) > 1:
            sys.stdout.write("?CD-F-Too many arguments\n")
            return
        elif len(args) == 0:
            sys.stdout.write("%s\n" % self.volumes.get_pwd())
            return
        if not self.volumes.chdir(args[0]):
            sys.stdout.write("?CD-F-Directory not found\n")

    def do_batch(self, line: str) -> None:
        # fmt: off
        """
@               Executes a command file

  SYNTAX
        @filespec

  SEMANTICS
        You can group a collection of commands that you want to execute
        sequentially into a command file.
        This command executes the command file.

  EXAMPLES
        @MAKE.COM

        """
        # fmt: on
        line = line.strip()
        if not line:
            return
        try:
            with open(line, "r") as f:
                for line in f:
                    if line.startswith("!"):
                        continue
                    self.onecmd(line.strip(), catch_exceptions=False, batch=True)
        except FileNotFoundError:
            raise Exception("?KMON-F-File not found")

    def do_pwd(self, line: str) -> None:
        # fmt: off
        """
PWD             Displays the current working drive and directory

  SYNTAX
        PWD

        """
        sys.stdout.write("%s\n" % self.volumes.get_pwd())

    def do_show(self, line: str) -> None:
        # fmt: off
        """
SHOW            Displays the volume assignment

  SYNTAX
        SHOW

        """
        # fmt: on
        sys.stdout.write("Volumes\n")
        sys.stdout.write("-------\n")
        for k, v in self.volumes.volumes.items():
            if k != "DK":
                sys.stdout.write("%-10s %s\n" % ("%s:" % k, v))

    def do_exit(self, line: str) -> None:
        # fmt: off
        """
EXIT            Exit the shell

  SYNTAX
        EXIT
        """
        # fmt: on
        raise SystemExit

    def do_quit(self, line: str) -> None:
        raise SystemExit

    def do_help(self, arg) -> None:
        # fmt: off
        """
HELP            Displays commands help

  SYNTAX
        HELP [topic]

        """
        # fmt: on
        if arg and arg != "*":
            if arg == "@":
                arg = "batch"
            try:
                doc = getattr(self, "do_" + arg).__doc__
                if doc:
                    self.stdout.write("%s\n" % str(doc))
                    return
            except AttributeError:
                pass
            self.stdout.write("%s\n" % str(self.nohelp % (arg,)))
        else:
            names = self.get_names()
            help = {}
            for name in names:
                if name[:5] == "help_":
                    help[name[5:]] = 1
            for name in sorted(set(names)):
                if name[:3] == "do_":
                    cmd = name[3:]
                    if cmd in help:
                        del help[cmd]
                    elif getattr(self, name).__doc__:
                        sys.stdout.write("%s\n" % getattr(self, name).__doc__.split("\n")[1])

    def do_shell(self, arg) -> None:
        # fmt: off
        """
SHELL           Executes a system shell command

  SYNTAX
        SHELL command

        """
        # fmt: on
        os.system(arg)

    def do_EOF(self, line: str) -> bool:
        return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c",
        action="append",
        metavar="command",
        help="execute a single command",
    )
    parser.add_argument(
        "-d",
        "--dir",
        metavar="dir",
        help="set working drive and directory",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        default=False,
        help="force opening an interactive shell even if commands are provided",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="display verbose output",
    )
    parser.add_argument(
        "disk",
        nargs="*",
        help="disk to be mounted",
    )
    options = parser.parse_args()
    shell = Shell(verbose=options.verbose)
    # Mount disks
    for i, dsk in enumerate(options.disk):
        shell.volumes.mount(dsk, f"DL{i}:", verbose=shell.verbose)
    # Change dir
    if options.dir:
        shell.volumes.set_default_volume(options.dir)
    # Execute the commands
    if options.c:
        try:
            for command in options.c:
                shell.onecmd(command, batch=True)
        except Exception:
            pass
    # Start interactive shell
    if options.interactive or not options.c:
        shell.cmdloop()


if __name__ == "__main__":
    main()
