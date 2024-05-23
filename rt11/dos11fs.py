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
from typing import Dict, Iterator, List, Optional, Tuple

from .abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from .commons import BLOCK_SIZE, bytes_to_word, hex_dump
from .rad50 import rad2asc
from .rt11fs import rt11_canonical_filename
from .uic import ANY_UIC, DEFAULT_UIC, UIC

__all__ = [
    "DOS11File",
    "DOS11DirectoryEntry",
    "DOS11Filesystem",
    "dos11_to_date",
    "dos11_canonical_filename",
    "dos11_split_fullname",
]

MFD_BLOCK = 1
UFD_ENTRIES = 28
MFD_ENTRY_SIZE = 8
UFD_ENTRY_SIZE = 18
CONTIGUOUS_FILE_TYPE = 32768
LINKED_FILE_BLOCK_SIZE = 510
DECTAPE_MFD1_BLOCK = 0o100
DECTAPE_MFD2_BLOCK = 0o101
DECTAPE_UFD1_BLOCK = 0o102
DECTAPE_UFD2_BLOCK = 0o103
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


def dos11_canonical_filename(fullname: str, wildcard: bool = False) -> str:
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


def dos11_split_fullname(uic: UIC, fullname: Optional[str], wildcard: bool = True) -> Tuple[UIC, Optional[str]]:
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


class DOS11File(AbstractFile):
    entry: "DOS11DirectoryEntry"
    closed: bool
    size: int
    block_size: int
    contiguous: bool

    def __init__(self, entry: "DOS11DirectoryEntry"):
        self.entry = entry
        self.closed = False
        self.contiguous = entry.contiguous
        self.block_size = BLOCK_SIZE if self.contiguous else LINKED_FILE_BLOCK_SIZE
        self.size = entry.length * self.block_size

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

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return self.block_size

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
     8  |          Next free byte             |
        +-------------------------------------+
    10  |           Start block #             |
        +-------------------------------------+
    12  |        Length (# of blocks)         |
        +-------------------------------------+
    14  |         Last block written          |
        +-------------------------------------+
    16  |Lock | Usage count | Protection code |
        +-------------------------------------+

    Disk Operating System Monitor - System Programmers Manual, Pag 136
    http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf
    """

    ufd_block: "UserFileDirectoryBlock"
    uic: UIC = DEFAULT_UIC
    filename: str = ""
    filetype: str = ""
    raw_creation_date: int = 0
    file_position: int = 0
    length: int = 0
    contiguous: bool = False  # linked/contiguous file
    protection_code: int = 0  # System Programmers Manual, Pag 140

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
            f"{self.uic.to_wide_str() if self.uic else '':<9}  "
            f"{self.creation_date or '          '} "
            f"{self.length:>6}{'C' if self.contiguous else ' '} "
            f"{self.file_position:6d} "
            f"{self.protection_code:>6o} "
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
    entries_list: List["DOS11DirectoryEntry"] = []

    def __init__(self, fs: "DOS11Filesystem", uic: UIC = DEFAULT_UIC):
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
        buf.write("\nNum  File        UIC        Date       Length   Block   Code\n\n")
        for i, x in enumerate(self.entries_list):
            buf.write(f"{i:02d}#  {x}\n")
        return buf.getvalue()


class MasterFileDirectoryEntry:
    """
    Master File Directory Block

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

    MFD Block 2 - N:
          +-------------------------------------+
       0  |          Link to next MFD           |
          +-------------------------------------+
       2  | UDF Entries                       1 |
          | .                                   |
          | .                                28 |
          +-------------------------------------+

    Disk Operating System Monitor - System Programmers Manual, Pag 135
    http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf
    """

    fs: "DOS11Filesystem"
    uic: UIC = DEFAULT_UIC  # User Identification Code
    ufd_block: int = 0  # UFD start block
    num_words: int = 0  # num of words in UFD entry, always 9
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
    """

    uic: UIC  # current User Identification Code
    xxdp: bool = False  # MFD Variety #2 (XXDP+)
    dectape: bool = False  # DECtape format

    def __init__(self, file: "AbstractFile"):
        self.f = file
        self.uic = DEFAULT_UIC

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
        uic: UIC = ANY_UIC,
    ) -> Iterator["MasterFileDirectoryEntry"]:
        """Read master file directory"""

        # Check DECtape format
        t = self.read_block(DECTAPE_MFD1_BLOCK)
        mfd2 = bytes_to_word(t[0:2])
        if mfd2 == DECTAPE_MFD2_BLOCK:
            tmp = self.read_block(mfd2)
            mfd3 = bytes_to_word(tmp[0:2])  # 0, DECtape has only 2 MFD
            ufd1 = bytes_to_word(tmp[4:6])  # 0o102, First UFD
            self.dectape = (mfd3 == 0) and (ufd1 == DECTAPE_UFD1_BLOCK)
        else:
            self.dectape = False

        if not self.dectape:
            t = self.read_block(mfd_block)
            mfd2 = bytes_to_word(t[0:2])

        if mfd2 != 0:  # MFD Variety #1 (DOS-11)
            # DOS Course Handouts, Pag 13
            # http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DOS_CourseHandouts.pdf
            self.xxdp = False
            next_mfd = mfd2
            while next_mfd:
                t = self.read_block(next_mfd)
                next_mfd = bytes_to_word(t[0:2])  # link to next MFD
                for i in range(0, BLOCK_SIZE - MFD_ENTRY_SIZE, MFD_ENTRY_SIZE):
                    entry = MasterFileDirectoryEntry(self)
                    entry.read(t, i)
                    if entry.num_words:
                        # Filter by UIC
                        if uic.match(entry.uic):
                            yield entry

        else:  # MFD Variery #2 (XXDP+)
            self.xxdp = True
            entry = MasterFileDirectoryEntry(self)
            entry.ufd_block = bytes_to_word(t[2:4])
            entry.uic = self.uic
            yield entry

    def filter_entries_list(
        self,
        pattern: Optional[str],
        include_all: bool = False,
        wildcard: bool = True,
        uic: Optional[UIC] = None,
    ) -> Iterator["DOS11DirectoryEntry"]:
        if uic is None:
            uic = self.uic
        uic, pattern = dos11_split_fullname(fullname=pattern, wildcard=wildcard, uic=uic)
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
                    if not entry.is_empty:
                        yield entry

    def get_file_entry(self, fullname: str) -> Optional[DOS11DirectoryEntry]:
        fullname = dos11_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        uic, basename = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
        return next(self.filter_entries_list(basename, wildcard=False, uic=uic), None)

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

    def dir(self, volume_id: str, pattern: Optional[str], options: Dict[str, bool]) -> None:
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
            if x.is_empty:
                continue
            fullname = x.is_empty and x.filename or "%-6s.%-3s" % (x.filename, x.filetype)
            if options.get("brief"):
                # Lists only file names and file types
                sys.stdout.write(f"{fullname}\n")
                continue
            creation_date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or ""
            attr = "C" if x.contiguous else ""
            if self.xxdp:
                sys.stdout.write(
                    f"{i:6} {fullname:>10s} {creation_date:>14s} {x.length:>10d}    {x.file_position:06o}\n"
                )
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

    def dump(self, name_or_block: str) -> None:
        if name_or_block.isnumeric():
            data = self.read_block(int(name_or_block))
        else:
            data = self.read_bytes(name_or_block)
        hex_dump(data)

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
        return self.f.get_size()

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
