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
import fnmatch
import io
import os
import sys
from datetime import date, timedelta
from typing import Iterator, List, Optional

from .abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from .commons import BLOCK_SIZE, bytes_to_word
from .rad50 import rad2asc
from .rt11fs import rt11_canonical_filename

__all__ = [
    "DOS11File",
    "DOS11DirectoryEntry",
    "DOS11Filesystem",
]

MFD_BLOCK = 1
UFD_ENTRIES = 28
MFD_ENTRY_SIZE = 8
UFD_ENTRY_SIZE = 18
CONTIGUOUS_FILE_TYPE = 32768
LINKED_FILE_BLOCK_SIZE = 510
READ_FILE_FULL = -1


def dos11_to_date(val: int) -> Optional[date]:
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


class DOS11File(AbstractFile):
    entry: "DOS11DirectoryEntry"
    closed: bool
    size: int
    contiguous: bool

    def __init__(self, entry: "DOS11DirectoryEntry"):
        self.entry = entry
        self.closed = False
        self.size = entry.length * BLOCK_SIZE
        self.contiguous = entry.contiguous

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
                self.entry.file_position + block_number,
                number_of_blocks,
            )
        else:
            # Linked file
            seq = 0
            data = bytearray()
            next_block_number = self.entry.file_position
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
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

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


class DOS11DirectoryEntry(AbstractDirectoryEntry):

    ufd_block: "UserFileDirectoryBlock"
    uic: Optional["UIC"] = None
    filename: str = ""
    filetype: str = ""
    raw_creation_date: int = 0
    file_position: int = 0
    length: int = 0
    contiguous: bool = False
    protection_code: int = 0

    def __init__(self, ufd_block: "UserFileDirectoryBlock"):
        self.ufd_block = ufd_block
        self.uic = ufd_block.uic

    def read(self, buffer: bytes, position: int) -> None:
        # DOS Course Handouts, Pag 14
        # http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DOS_CourseHandouts.pdf
        self.filename = rad2asc(buffer, position + 0) + rad2asc(buffer, position + 2)  # RAD50 chars
        self.filetype = rad2asc(buffer, position + 4)  # RAD50 chars
        self.raw_creation_date = bytes_to_word(buffer, position + 6)
        if self.raw_creation_date & CONTIGUOUS_FILE_TYPE:
            self.contiguous = True
            self.raw_creation_date &= ~CONTIGUOUS_FILE_TYPE
        # next free byte
        self.file_position = bytes_to_word(buffer, position + 10)  # block number of the first logical block
        self.length = bytes_to_word(buffer, position + 12)  # length in blocks
        # last block written (word)
        self.protection_code = bytes_to_word(buffer, position + 16)  # lock, usage count, protection code

    @property
    def is_empty(self) -> bool:
        return self.filename == "" and self.filetype == ""

    @property
    def fullname(self) -> str:
        return f"{self.uic or ''}{self.filename}.{self.filetype}"

    @property
    def basename(self) -> str:
        return f"{self.filename}.{self.filetype}"

    @property
    def creation_date(self) -> Optional[date]:
        return dos11_to_date(self.raw_creation_date)

    def delete(self) -> bool:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def __str__(self) -> str:
        return (
            f"{self.filename:<6}."
            f"{self.filetype:<3}  "
            f"{str(self.uic) or '':<9}  "
            f"{self.creation_date or '          '} "
            f"{self.length:>6} "
            f"{self.file_position:6d} "
            f"{self.protection_code:>6o} "
        )

    def __repr__(self) -> str:
        return str(self)


class UserFileDirectoryBlock(object):
    """
    User File Directory Block

    +--------------+
    |Next block    |
    +--------------+
    |Entries     1 |
    |.             |
    |.          28 |
    +--------------+
    """

    # Block number of this user file directory block
    block_number = 0
    # Block number of the next user file directory block
    next_block_number = 0
    # User Identification Code
    uic: Optional["UIC"] = None
    # User File Directory Block entries
    entries_list: List["DOS11DirectoryEntry"] = []

    def __init__(self, fs: "DOS11Filesystem", uic: Optional["UIC"]):
        self.fs = fs
        self.uic = uic

    def read(self, block_number: int) -> None:
        """
        Read a User File Directory Block from disk
        """
        self.block_number = block_number
        t = self.fs.read_block(self.block_number, 2)
        self.next_block_number = bytes_to_word(t, 0)
        self.entries_list = []
        for position in range(2, UFD_ENTRIES * UFD_ENTRY_SIZE, UFD_ENTRY_SIZE):
            dir_entry = DOS11DirectoryEntry(self)
            dir_entry.read(t, position)
            self.entries_list.append(dir_entry)

    def write(self) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def __str__(self) -> str:
        buf = io.StringIO()
        buf.write("\n*User File Directory Block\n")
        buf.write(f"UIC:                   {self.uic or ''}\n")
        buf.write(f"Block number:          {self.block_number}\n")
        buf.write(f"Next dir block:        {self.next_block_number}\n")
        buf.write("\nNum  File        UIC        Date       Length  Block   Code\n\n")
        for i, x in enumerate(self.entries_list):
            buf.write(f"{i:02d}#  {x}\n")
        return buf.getvalue()


class UIC:
    """
    User Identification Code
    The format of UIC if [ggg,uuu] there ggg and uuu are octal digits
    The value on the left of the comma is represents the group number,
    the value on the right represents the user's number within the group.
    """

    group: int
    user: int

    def __init__(self, group: int, user: int):
        self.group = group & 0xFF
        self.user = user & 0xFF

    @classmethod
    def from_str(cls, code_str: str) -> "UIC":
        code_str = code_str.split("[")[1].split("]")[0]
        group_str, user_str = code_str.split(",")
        group = int(group_str, 8) & 0xFF
        user = int(user_str, 8) & 0xFF
        return cls(group, user)

    @classmethod
    def from_word(cls, code_int: int) -> "UIC":
        group = code_int >> 8
        user = code_int & 0xFF
        return cls(group, user)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, UIC):
            return self.group == other.group and self.user == other.user
        elif isinstance(other, str):
            other_uic = UIC.from_str(other)
            return self.group == other_uic.group and self.user == other_uic.user
        elif isinstance(other, int):
            other_uic = UIC.from_word(other)
            return self.group == other_uic.group and self.user == other_uic.user
        else:
            raise ValueError("Invalid type for comparison")

    def __str__(self) -> str:
        return f"[{self.group:o},{self.user:o}]"


class MasterFileDirectoryEntry:
    """
    Master File Directory Block

    +--------------+
    |Next block    |
    +--------------+
    |Entries       |
    |.             |
    |.             |
    +--------------+
    """

    fs: "DOS11Filesystem"
    uic: Optional["UIC"] = None  # User Identification Code
    ufd_block: int = 0  # UFD start block
    num_words: int = 0  # num of words in UFD entry
    zero: int = 0  # always 0

    def __init__(self, fs: "DOS11Filesystem"):
        self.fs = fs

    def read(self, buffer: bytes, position: int) -> None:
        self.uic = UIC.from_word(bytes_to_word(buffer[position + 2 : position + 4]))
        self.ufd_block = bytes_to_word(buffer[position + 4 : position + 6])  # UFD start block
        self.num_words = bytes_to_word(buffer[position + 6 : position + 8])  # number of words in UFD entry
        self.zero = bytes_to_word(buffer[position + 8 : position + 10])  # always 0

    def read_ufd_blocks(self) -> Iterator["UserFileDirectoryBlock"]:
        """Read User File Directory blocks"""
        next_block_number = self.ufd_block
        while next_block_number != 0:
            ufd_block = UserFileDirectoryBlock(self.fs, self.uic)
            ufd_block.read(next_block_number)
            next_block_number = ufd_block.next_block_number
            yield ufd_block

    def __str__(self) -> str:
        return f"{self.uic} ufd_block={self.ufd_block} num_words={self.num_words} zero={self.zero}"


class DOS11Filesystem(AbstractFilesystem):
    """
    DOS-11/XXDP+ Filesystem
    """

    uic: UIC  # current User Identification Code

    def __init__(self, file: "AbstractFile"):
        self.f = file
        self.uic = UIC(0o1, 0o1)

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
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def read_mfd_entries(
        self,
        mfd_block: int = MFD_BLOCK,
        uic: Optional[UIC] = None,
    ) -> Iterator["MasterFileDirectoryEntry"]:
        """Read master file directory"""
        t = self.read_block(mfd_block)
        mfd2 = bytes_to_word(t[0:2])
        if mfd2 != 0:  # MFD Variety #1 (DOS-11)
            # DOS Course Handouts, Pag 13
            # http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DOS_CourseHandouts.pdf
            next_mfd = mfd2
            if next_mfd:
                t = self.read_block(next_mfd)
                next_mfd = bytes_to_word(t[0:2])  # link to next MFD
                for i in range(0, BLOCK_SIZE - MFD_ENTRY_SIZE, MFD_ENTRY_SIZE):
                    entry = MasterFileDirectoryEntry(self)
                    entry.read(t, i)
                    if entry.ufd_block:
                        # Filter by UIC
                        if uic is None or uic == entry.uic:
                            yield entry

        else:  # MFD Variery #2 (XXDP+)
            entry = MasterFileDirectoryEntry(self)
            entry.ufd_block = bytes_to_word(t[2:4])
            entry.uic = self.uic
            yield entry

    def dos11_canonical_filename(self, fullname: str, wildcard: bool = False) -> str:
        try:
            if "[" in fullname:
                uic: Optional[UIC] = UIC.from_str(fullname)
                fullname = fullname.split("]", 1)[1]
            else:
                uic = None
        except Exception:
            uic = None
        if fullname:
            fullname = rt11_canonical_filename(fullname, wildcard=wildcard)
        return f"{uic or ''}{fullname}"

    def filter_entries_list(
        self, pattern: Optional[str], include_all: bool = False, wildcard: bool = True
    ) -> Iterator["DOS11DirectoryEntry"]:
        uic = self.uic
        if pattern:
            if "[" in pattern:
                try:
                    uic = UIC.from_str(pattern)
                    pattern = pattern.split("]", 1)[1]
                except Exception:
                    return
            if pattern:
                pattern = rt11_canonical_filename(pattern, wildcard=wildcard)
        for mfd in self.read_mfd_entries(uic=uic):
            for ufd_block in mfd.read_ufd_blocks():
                for entry in ufd_block.entries_list:
                    if (
                        (not pattern)
                        or (wildcard and fnmatch.fnmatch(entry.basename, pattern))
                        or (not wildcard and entry.basename == pattern)
                    ):
                        if not include_all and entry.is_empty:
                            continue
                        yield entry

    @property
    def entries_list(self) -> Iterator["DOS11DirectoryEntry"]:
        for mfd in self.read_mfd_entries(uic=self.uic):
            for ufd_block in mfd.read_ufd_blocks():
                for entry in ufd_block.entries_list:
                    yield entry

    def get_file_entry(self, fullname: str) -> Optional[DOS11DirectoryEntry]:
        fullname = self.dos11_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return next(self.filter_entries_list(fullname, wildcard=False), None)

    def open_file(self, fullname: str) -> DOS11File:
        entry = self.get_file_entry(fullname)
        if not entry:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return DOS11File(entry)

    def read_bytes(self, fullname: str) -> bytes:
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
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def create_file(
        self,
        fullname: str,
        length: int,  # length in blocks
        creation_date: Optional[date] = None,  # optional creation date
    ) -> Optional[DOS11DirectoryEntry]:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def isdir(self, fullname: str) -> bool:
        return False

    def exists(self, fullname: str) -> bool:
        entry = self.get_file_entry(fullname)
        return entry is not None

    def dir(self, pattern: Optional[str]) -> None:
        i = 0
        files = 0
        blocks = 0
        for x in self.filter_entries_list(pattern, include_all=True):
            if x.is_empty:
                continue
            i = i + 1
            if x.is_empty:
                continue
            fullname = x.is_empty and x.filename or "%-6s.%-3s" % (x.filename, x.filetype)
            date = x.creation_date and x.creation_date.strftime("%d-%b-%y") or ""
            attr = "C" if x.contiguous else ""
            sys.stdout.write("%10s %5d%1s %9s" % (fullname, x.length, attr, date))
            blocks += x.length
            files += 1
            if i % 2 == 1:
                sys.stdout.write("    ")
            else:
                sys.stdout.write("\n")
        if i % 2 == 1:
            sys.stdout.write("\n")
        sys.stdout.write("\n")
        sys.stdout.write(f"TOTL BLKS: {blocks:5}\n")
        sys.stdout.write(f"TOTL FILES: {files:4}\n")

    def dump(self, name_or_block: str) -> None:
        bytes_per_line = 16

        def hex_dump(i: int, data: bytes) -> str:
            hex_str = " ".join([f"{x:02x}" for x in data])
            ascii_str = "".join([chr(x) if 32 <= x <= 126 else "." for x in data])
            return f"{i:08x}   {hex_str.ljust(3 * bytes_per_line)}  {ascii_str}\n"

        if name_or_block.isnumeric():
            data = self.read_block(int(name_or_block))
        else:
            print(name_or_block)
            data = self.read_bytes(name_or_block)
        for i in range(0, len(data), bytes_per_line):
            sys.stdout.write(hex_dump(i, data[i : i + bytes_per_line]))

    def examine(self, name_or_block: Optional[str]) -> None:
        if name_or_block:
            self.dump(name_or_block)
        else:
            for mfd in self.read_mfd_entries():
                for ufd_block in mfd.read_ufd_blocks():
                    sys.stdout.write(f"{ufd_block}\n")

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size() // BLOCK_SIZE

    def initialize(self) -> None:
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
        return str(self.uic)

    def __str__(self) -> str:
        return str(self.f)
