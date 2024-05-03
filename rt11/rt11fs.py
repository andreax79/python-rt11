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

import copy
import errno
import fnmatch
import io
import math
import os
import sys
from datetime import date
from typing import Dict, Iterator, List, Optional

from .abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from .commons import BLOCK_SIZE, bytes_to_word, date_to_rt11, word_to_bytes
from .rad50 import asc2rad, rad2asc
from .rx import (
    RX01_SECTOR_SIZE,
    RX01_SIZE,
    RX02_SECTOR_SIZE,
    RX02_SIZE,
    RX_SECTOR_TRACK,
)

__all__ = [
    "RT11File",
    "RT11DirectoryEntry",
    "RT11Filesystem",
]

HOMEBLK = 1
DEFAULT_DIR_SEGMENT = 6
DIR_ENTRY_SIZE = 14
DIRECTORY_SEGMENT_HEADER_SIZE = 10
DIRECTORY_SEGMENT_SIZE = BLOCK_SIZE * 2

E_TENT = 1  # Tentative file
E_MPTY = 2  # Empty area
E_PERM = 4  # Permanent file
E_EOS = 8  # End-of-segment marker
E_READ = 64  # Protected from write
E_PROT = 128  # Protected permanent file


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
    filename = rad2asc(asc2rad(filename[0:3])) + rad2asc(asc2rad(filename[3:6]))
    filetype = rad2asc(asc2rad(filetype))
    return f"{filename}.{filetype}"


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
        if self.closed or block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        if block_number + number_of_blocks > self.entry.length:
            number_of_blocks = self.entry.length - block_number
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


class RT11Filesystem(AbstractFilesystem):
    """
    RT-11 Filesystem
    """

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
        self.id = t[472:484].decode("ascii", "replace").replace("�", "?")
        self.owner = t[484:496].decode("ascii", "replace").replace("�", "?")
        self.sys_id = t[496:508].decode("ascii", "replace").replace("�", "?")
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
                    if not include_all and (entry.is_empty or entry.is_tentative or entry.is_end_of_segment):
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
    ) -> None:
        length = int(math.ceil(len(content) * 1.0 / BLOCK_SIZE))
        entry = self.create_file(fullname, length, creation_date)
        if not entry:
            return
        content = content + (b"\0" * BLOCK_SIZE)
        self.write_block(content, entry.file_position, entry.length)

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

    def dir(self, pattern: Optional[str], options: Dict[str, bool]) -> None:
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
                if options.get("brief"):
                    continue
                fullname = "< UNUSED >"
                date = ""
                unused = unused + x.length
            else:
                fullname = x.is_empty and x.filename or "%-6s.%-3s" % (x.filename, x.filetype)
                if options.get("brief"):
                    # Lists only file names and file types
                    sys.stdout.write(f"{fullname}\n")
                    continue
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
        if options.get("brief"):
            return
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

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

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
