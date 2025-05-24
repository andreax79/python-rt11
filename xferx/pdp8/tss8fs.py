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
import math
import os
import re
import sys
import typing as t
from abc import abstractmethod
from datetime import date, datetime

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..block import BlockDevice12Bit
from ..commons import ASCII, BLOCK_SIZE, IMAGE, READ_FILE_FULL, filename_match
from ..uic import ANY_GROUP, ANY_USER, UIC
from .os8fs import oct_dump

__all__ = [
    "TSS8File",
    "TSS8Filesystem",
]

# TSS/8 TIME-SHARING SYSTEM USER'S GUIDE, Pag 117
# https://bitsavers.org/pdf/dec/pdp8/tss8/DEC-T8-MRFB-D_UserGde_Feb70.pdf

# System Manager's Guide for PDP-8E TSS 8.24 Monitor, Pag 147
# https://bitsavers.org/pdf/dec/pdp8/tss8/TSS8_8.24_ManagersGuide.pdf

# The disk is divided into tracks. One track is defined as 4096 words.

WORDS_PER_BLOCK = 256  # One segment is defined as 256 words.
WORDS_PER_TRACK = 4096  # Track size (in words)
BLOCKS_PER_TRACK = WORDS_PER_TRACK // WORDS_PER_BLOCK  # Number of blocks per track
MONITOR_SIZE = 5 * BLOCKS_PER_TRACK  # Monitor size (in blocks)
SI_BLOCK = 0 * BLOCKS_PER_TRACK  # System Interpreter
FIP_BLOCK = 1 * BLOCKS_PER_TRACK  # FIP File Phantom
INIT_BLOCK = 2 * BLOCKS_PER_TRACK
TSS8_BLOCK = 3 * BLOCKS_PER_TRACK  # TSS/8 resident monitor (2 traks)

# MFD

MFD_UID_POS = 0
MFD_PASSWORD_POS = 1
MFD_PASSWORD_SIZE = 2  # Password size (in words)
MFD_NEXT_POS = 3
MFD_QUOTA_POS = 4
MFD_DEVICE_TIME_POS = 5
MFD_CPU_TIME_POS = 6
MFD_RETRIEVAL_POINTER_POS = 7
MFD_SIZE = 8  # in words

# UFD

UFD_FILENAME_SIZE = 3  # Filename size (in words)
UFD_FILENAME_POS = 0
UFD_NEXT_POS = 3
UFD_EXT_PROTECTION_POS = 4
UFD_FILE_SIZE_POS = 5
UFD_CREATION_DATE_POS = 6
UFD_RETRIEVAL_POINTER_POS = 7
UFD_SIZE = 8  # in words

# SAT

SAT_SIZE = 0o530  # Size of the Storage Allocation Table (in words)
SAT_END_POS = 0o7777  # Position of the end of the Storage Allocation Table in FIP
SAT_START_POS = SAT_END_POS - SAT_SIZE + 1 + 2  # Position of the Storage Allocation Table in FIP
SAT_CNT = SAT_END_POS - SAT_SIZE + 2  # Position of the available segments count

# Pag 23
# https://bitsavers.org/pdf/dec/pdp8/tss8/TSS8_8.24_ManagersGuide.pdf

QUOTA_MULTIPLIER = 25  # Quota multiplier
ENTRY_SIZE = 8  # MFD/UFD entry in words
RETRIEVAL_SIZE = 8  # Size of the retrieval block (in words)

assert ENTRY_SIZE == RETRIEVAL_SIZE == MFD_SIZE == UFD_SIZE

# Pag 79
# https://bitsavers.org/pdf/dec/pdp8/tss8/TSS8_8.24_ManagersGuide.pdf

DEFAULT_PROTECTION_CODE = 0o12  # Default protection code for new files

# File protection masks (octal):
#  1 - Other projects - read protected
#  2 - Other projects - write protected
#  4 - Same project   - read protected
# 10 - Same project   - write protected
# 20 - Owner          - write protected

# Pag 109
# https://bitsavers.org/pdf/dec/pdp8/tss8/TSS8_8.24_ManagersGuide.pdf

EXTENSIONS = [
    "",
    "ASC",  #   1 - ASCII files
    "SAV",  #   2 - Save format files
    "BIN",  #   3 - Binary files
    "BAS",  #   4 - BASIC source files
    "BAC",  #   5 - BASIC compiled files
    "FCL",  #   6 - FOCAL source files
    "TMP",  #   7 - Temporary files
    "",  #      8 - blank
    "DAT",  #   9 - Data files
    "LST",  #  10 - Listing files
    "PAL",  #  11 - PAL assembler source files
    "",  #     12 - blank
    "",  #     13 - blank
    "",  #     14 - blank
    "",  #     15 - blank
]

PARTITION_FULLNAME_RE = re.compile(r"^\[(\d+)\](.*)$")
BINARY_EXTENSIONS = {"SAV", "BIN", "BAC", "TMP", "DAT"}
TSS8_BLOCK_SIZE_BYTES = 384  # Block size (in bytes)


class PPN(UIC):
    """
    TSS/8 Project-Programmer Numbers
    """

    @classmethod
    def from_str(cls, code_str: str) -> "PPN":
        code_str = code_str.split("[")[1].split("]")[0]
        group_str, user_str = code_str.split(",")
        if group_str == "*":
            group = ANY_GROUP
        else:
            group = int(group_str, 8) & 0o77
        if user_str == "*":
            user = ANY_USER
        else:
            user = int(user_str, 8) & 0o77
        return cls(group, user)

    @classmethod
    def from_word(cls, code_int: int) -> "PPN":
        group = code_int >> 6
        user = code_int & 0o77
        return cls(group, user)

    def to_word(self) -> int:
        return (self.group << 6) + self.user


ANY_PPN = PPN.from_str("[*,*]")
DEFAULT_PPN = PPN.from_str("[0,1]")
MFD_PPN = PPN.from_str("[0,1]")


def from_12bit_words_to_ascii(words: list[int]) -> str:
    """
    Convert 12bit words to ASCII string
    """
    result = bytearray()
    for word in words:
        if word == 0:
            continue
        result.append(((word >> 6) & 0o77) + 0o40)
        result.append((word & 0o77) + 0o40)
    return result.decode("ascii", errors="replace")


def from_ascii_to_12bit_words(data: str) -> list[int]:
    """
    Convert ASCII string to 12bit words
    """
    buffer = bytearray(data.upper(), "ascii")
    if len(buffer) % 2 != 0:
        buffer.append(0x20)
    words = []
    for i in range(0, len(buffer), 2):
        chr1 = (buffer[i] - 0o40) & 0o77
        chr2 = (buffer[i + 1] - 0o40) & 0o77
        words.append(chr2 | (chr1 << 6))
    return words


def tss8_to_date(val: t.Optional[int]) -> t.Optional[date]:
    """
    Translate TSS/8 date to Python date
    """
    if val is None:
        return None
    day = val % 31 + 1
    month = (val // 31) % 12 + 1
    year = val // 372 + 1974
    try:
        return date(year, month, day)
    except:
        return None


def date_to_tss8(d: t.Optional[date]) -> int:
    """
    Convert Python date to TSS/8 date integer format
    """
    if d is None:
        return 0
    return ((d.year - 1974) * 372 + (d.month - 1) * 31 + d.day - 1) & 0o7777


def tss8_canonical_filename(fullname: str, wildcard: bool = False) -> str:
    # Split the PPN from the fullname if it exists
    try:
        if "[" in fullname:
            ppn: t.Optional[PPN] = PPN.from_str(fullname)
            fullname = fullname.split("]", 1)[1]
        else:
            ppn = None
    except Exception:
        ppn = None
    if fullname:
        fullname = fullname.upper()
        try:
            filename, extension = fullname.split(".", 1)
        except Exception:
            filename = fullname
            extension = "*" if wildcard else ""
        filename = from_12bit_words_to_ascii(from_ascii_to_12bit_words(filename)[:UFD_FILENAME_SIZE]).strip()
        # TODO
        # check extension
        fullname = f"{filename}.{extension}"
    return f"{ppn or ''}{fullname}"


def tss8_split_fullname(
    ppn: PPN,
    fullname: t.Optional[str],
    wildcard: bool = True,
) -> t.Tuple[PPN, t.Optional[str]]:
    if fullname:
        if "[" in fullname:
            try:
                ppn = PPN.from_str(fullname)
                fullname = fullname.split("]", 1)[1]
            except Exception:
                return ppn, fullname
        if fullname:
            fullname = tss8_canonical_filename(fullname, wildcard=wildcard)
    return ppn, fullname


def tss8_prepare_filename_extension(filename: str) -> t.Tuple[str, str, int]:
    """
    Prepare the filename and extension for TSS/8
    """
    filename = tss8_canonical_filename(filename, wildcard=False)
    if not filename:
        raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))
    try:
        filename, extension = filename.split(".", 1)
    except Exception:
        extension = ""
    try:
        extension_idx = EXTENSIONS.index(extension)
    except Exception:
        extension_idx = 0
        extension = ""
    return filename, extension, extension_idx


def from_12bit_words_to_bytes(words: list[int], file_mode: str = ASCII) -> bytes:
    """
    Convert 12bit words to bytes
    """
    mask = 127 if file_mode == ASCII else 255
    data = bytearray()
    while words:
        if len(words) < 2:
            break
        dw = (words.pop(0) << 12) + words.pop(0)
        data.append((dw >> 16) & mask)
        data.append((dw >> 8) & mask)
        data.append(dw & mask)
    return bytes(data)


def from_bytes_to_12bit_words(data: bytes, file_mode: str = ASCII) -> list[int]:
    """
    Convert bytes back to 12-bit words
    """
    mask = 127 if file_mode == ASCII else 255
    words = []
    i = 0
    while i + 2 < len(data):
        b1, b2, b3 = data[i] & mask, data[i + 1] & mask, data[i + 2] & mask
        dw = (b1 << 16) | (b2 << 8) | b3
        words.append((dw >> 12) & 0xFFF)
        words.append(dw & 0xFFF)
        i += 3
    return words


def format_time(seconds: int) -> str:
    """
    Format seconds into HH:MM:SS
    """
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{hours:02}:{minutes:02}:{seconds:02}"


class TSS8File(AbstractFile):
    entry: "UserFileDirectoryEntry"
    file_mode: str
    closed: bool
    size: int

    def __init__(self, entry: "UserFileDirectoryEntry", file_mode: t.Optional[str] = None):
        self.entry = entry
        if file_mode is not None:
            self.file_mode = file_mode
        else:
            self.file_mode = IMAGE if entry.extension.upper() in BINARY_EXTENSIONS else ASCII
        self.closed = False
        self.size = entry.length * TSS8_BLOCK_SIZE_BYTES

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
        # Get the blocks to be read
        blocks = list(self.entry.blocks())[block_number : block_number + number_of_blocks]
        # Read the blocks
        for disk_block_number in blocks:
            words = self.entry.ufd.fs.read_12bit_words_block(disk_block_number)
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
        words = from_bytes_to_12bit_words(buffer, self.file_mode)
        # Get the blocks to be written
        blocks = list(self.entry.blocks())[block_number : block_number + number_of_blocks]
        # Write the blocks
        for i, disk_block_number in enumerate(blocks):
            block_words = words[i * WORDS_PER_BLOCK : (i + 1) * WORDS_PER_BLOCK]
            block_words.extend([0] * (WORDS_PER_BLOCK - len(block_words)))
            self.entry.ufd.fs.write_12bit_words_block(disk_block_number, block_words)

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


class TSS8AbstractDirectoryEntry(AbstractDirectoryEntry):
    """
    Master or User File Directory Entry
    """

    position: int = 0  # Position (offset in MFD/UFD)
    next: int = 0  # Link to next entry
    retrieval_pointer: int = 0  # Retrieval pointer (offset in MFD/UFD)

    @abstractmethod
    def update_dir(self) -> None:
        """
        Write the entry in the Master/User File Directory
        """
        pass

    @abstractmethod
    def to_words(self) -> t.List[int]:
        """
        Convert the entry to a list of 12-bit words
        """
        pass


class MasterFileDirectoryEntry(TSS8AbstractDirectoryEntry):
    """
    Master File Directory Entry in the MFD

    Word
       +-----------------------+
    0  | User Identification   |  Project-Programmer Number (PPN)
       +-----------------------+
    1  | Password (2 words)    |
    2  |                       |
       +-----------------------+
    3  | Next                  |  Offset of next entry in MFD
       +-----------------------+
    4  | Quota                 |
       +-----------------------+
    5  | Device Time           |
       +-----------------------+
    6  | CPU Time              |
       +-----------------------+
    7  | Retrieval Pointer     |  Offset of the retrieval block in MFD
       +-----------------------+

    """

    mfd: "MasterFileDirectory"  # Reference to the Master File Directory
    position: int  # Position (offset in MFD)
    ppn: PPN  # Project-Programmer Number (PPN)
    password: str  # Password
    next: int  # Link to next entry
    quota: int  # Quota
    cpu_time: int  # CPU time
    device_time: int  # Device time
    retrieval_pointer: int  # Retrieval pointer (offset in MFD)

    def __init__(self, mfd: "MasterFileDirectory"):
        self.mfd = mfd

    @classmethod
    def read(cls, mfd: "MasterFileDirectory", position: int) -> "MasterFileDirectoryEntry":
        """
        Read a User File Directory from disk
        """
        self = cls(mfd)
        self.position = position
        self.ppn = PPN.from_word(mfd.words[position + MFD_UID_POS])  # Project-Programmer Number (PPN)
        password = mfd.words[position + MFD_PASSWORD_POS : position + MFD_PASSWORD_POS + MFD_PASSWORD_SIZE]
        self.password = from_12bit_words_to_ascii(password)  # Password
        self.next = mfd.words[position + MFD_NEXT_POS]  # Link to next entry
        self.quota = mfd.words[position + MFD_QUOTA_POS] * QUOTA_MULTIPLIER  # Quota
        self.device_time = mfd.words[position + MFD_DEVICE_TIME_POS]  # Device time
        self.cpu_time = mfd.words[position + MFD_CPU_TIME_POS]  # CPU time
        self.retrieval_pointer = mfd.words[position + MFD_RETRIEVAL_POINTER_POS]  # Pointer to retrieval
        return self

    @classmethod
    def create(
        cls,
        mfd: "MasterFileDirectory",
        ppn: PPN,
        position: int,
        retrieval_pointer: int,
        password: str = "",
        quota: int = 0,
    ) -> "MasterFileDirectoryEntry":
        """
        Create a new Master File Directory Entry
        """
        self = cls(mfd)
        self.position = position
        self.ppn = ppn
        self.password = password
        self.next = 0
        self.quota = quota
        self.cpu_time = 0
        self.device_time = 0
        self.retrieval_pointer = retrieval_pointer
        self.update_dir()  # Write the entry to the MFD
        return self

    def iterdir(
        self,
        pattern: t.Optional[str] = None,
        include_all: bool = False,
        wildcard: bool = False,
    ) -> t.Iterator["UserFileDirectoryEntry"]:
        ufd = UserFileDirectory.read(self)
        entry: "UserFileDirectoryEntry"
        for entry in list(ufd.entries):  # type: ignore
            if (not entry.is_dummy) and (not pattern or filename_match(entry.basename, pattern, wildcard=wildcard)):
                yield entry

    def disk_usage(self) -> int:
        """
        Get the disk usage in blocks
        """
        return sum(entry.get_length() for entry in self.iterdir())

    def update_dir(self) -> None:
        """
        Write the entry in the User File Directory
        """
        self.mfd.words[self.position : self.position + ENTRY_SIZE] = self.to_words()

    def to_words(self) -> t.List[int]:
        """
        Convert the entry to a list of 12-bit words
        """
        password = from_ascii_to_12bit_words(self.password)[:2]
        password += [0] * (MFD_PASSWORD_SIZE - len(password))
        words = [self.ppn.to_word()]  # Project-Programmer Number (PPN)
        words += password  # Password (2 words)
        words.append(self.next)  # Link to next entry
        words.append(self.quota // QUOTA_MULTIPLIER)  # Quota
        words.append(self.device_time)  # Device Time
        words.append(self.cpu_time)  # CPU Time
        words.append(self.retrieval_pointer)  # Retrieval Pointer
        return words

    def open(self, file_mode: t.Optional[str] = None) -> TSS8File:
        raise OSError(errno.EINVAL, "Invalid operation on directory")

    @property
    def is_dummy(self) -> bool:
        """
        The MFD entry (at position 0) is a dummy entry
        """
        return self.position == 0

    @property
    def fullname(self) -> str:
        return f"{self.ppn}"

    @property
    def basename(self) -> str:
        return f"{self.ppn}"

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return len(list(self.mfd.retrieval_blocks(self.retrieval_pointer)))

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

    def delete(self) -> bool:
        # Delete all entries in the UFD
        for entry in self.iterdir():
            if not entry.delete():
                raise OSError(errno.EIO, os.strerror(errno.EIO))
        # Delete the UFD
        return self.mfd.delete_ufd(self)

    def write(self) -> bool:
        """
        Write the directory entry
        """
        raise OSError(errno.EINVAL, "Invalid operation on directory")

    @property
    def fs(self) -> "TSS8Filesystem":
        """
        Get the filesystem associated with this MFD entry
        """
        return self.mfd.fs

    def __str__(self) -> str:
        return (
            f"{str(self.ppn):<11} {self.password:8} {self.next:>5}  {self.quota:>4}  "
            f"{self.device_time:>4}  {self.cpu_time:>4}  {self.retrieval_pointer:>4}"
        )


class UserFileDirectoryEntry(TSS8AbstractDirectoryEntry):
    """
    User File Directory Entry

    Word
       +-----------------------+
    0  | File Name (3 words)   |
    3  |                       |
       +-----------------------+
    4  | Next                  |  Offset of next entry in UFD
       +-----------------------+
    5  | Ext | Protection      |
       +-----------------------+
    6  | File Size             |
       +-----------------------+
    7  | Creation Date         |
       +-----------------------+
    8  | Retrieval Pointer     |  Offset of the retrieval block in UFD
       +-----------------------+
    """

    ufd: "UserFileDirectory"
    position: int = 0  # Position (offset in MFD)
    filename: str = ""
    extension: str = ""
    extension_idx: int = 0  # Extension index
    protection: int = 0  # Protection bits
    next: int = 0  # Link to next entry
    length: int = 0  # Length in blocks
    raw_creation_date: int = 0  # Creation date
    retrieval_pointer: int = 0  # Retrieval pointer (offset in MFD)

    def __init__(self, ufd: "UserFileDirectory"):
        self.ufd = ufd

    @classmethod
    def read(cls, ufd: "UserFileDirectory", position: int) -> "UserFileDirectoryEntry":
        self = cls(ufd)
        filename = ufd.words[UFD_FILENAME_POS + position : UFD_FILENAME_POS + position + UFD_FILENAME_SIZE]
        self.position = position
        self.filename = from_12bit_words_to_ascii(filename).strip()
        self.next = ufd.words[position + UFD_NEXT_POS]
        ext_protection = ufd.words[position + UFD_EXT_PROTECTION_POS]
        self.protection = ext_protection & 0o77
        self.extension_idx = (ext_protection >> 7) & 0xF
        self.extension = EXTENSIONS[self.extension_idx]
        self.length = ufd.words[position + UFD_FILE_SIZE_POS]
        self.raw_creation_date = ufd.words[position + UFD_CREATION_DATE_POS]
        self.retrieval_pointer = ufd.words[position + UFD_RETRIEVAL_POINTER_POS]
        return self

    @classmethod
    def create(
        cls,
        ufd: "UserFileDirectory",
        basename: str,
        number_of_blocks: int,
        position: int,
        retrieval_pointer: int,
        protection_code: int = DEFAULT_PROTECTION_CODE,
        creation_date: t.Optional[date] = None,
    ) -> "UserFileDirectoryEntry":
        """
        Create a new User File Directory Entry
        """
        self = cls(ufd)
        (self.filename, self.extension, self.extension_idx) = tss8_prepare_filename_extension(basename)
        self.protection = protection_code
        self.position = position
        self.next = 0
        self.length = number_of_blocks
        self.raw_creation_date = date_to_tss8(creation_date or date.today())
        self.retrieval_pointer = retrieval_pointer
        self.update_dir()  # Write the entry to the UFD
        return self

    def update_dir(self) -> None:
        """
        Write the entry in the User File Directory
        """
        self.ufd.words[self.position : self.position + ENTRY_SIZE] = self.to_words()

    def to_words(self) -> t.List[int]:
        """
        Convert the entry to a list of 12-bit words
        """
        words = from_ascii_to_12bit_words(self.filename)[:UFD_FILENAME_SIZE]
        words = words + [0] * (UFD_FILENAME_SIZE - len(words))  # Ensure 3 words for filename
        words.append(self.next)
        words.append(self.protection + ((self.extension_idx & 0xF) << 7))
        words.append(self.length)
        words.append(self.raw_creation_date)
        words.append(self.retrieval_pointer)
        return words

    def blocks(self) -> t.Iterator[int]:
        """
        Iterate over the blocks of the file
        """
        yield from self.ufd.retrieval_blocks(self.retrieval_pointer)

    @property
    def fullname(self) -> str:
        return f"{self.ufd.ppn or ''}{self.filename}.{self.extension}"

    @property
    def basename(self) -> str:
        return f"{self.filename}.{self.extension}"

    @property
    def creation_date(self) -> t.Optional[date]:
        return tss8_to_date(self.raw_creation_date)

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
        return TSS8_BLOCK_SIZE_BYTES

    def delete(self) -> bool:
        """
        Delete the file
        """
        return self.ufd.delete(self)

    def resize(self, number_of_blocks: int) -> None:
        """
        Resize the file to the specified size
        """
        self.ufd.resize(self, number_of_blocks)

    def write(self) -> bool:
        """
        Write the directory entry
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def open(self, file_mode: t.Optional[str] = None) -> TSS8File:
        """
        Open a file
        """
        return TSS8File(self, file_mode)

    @property
    def is_dummy(self) -> bool:
        """
        The UFD entry (at position 0) is a dummy entry
        """
        return self.position == 0

    def __str__(self) -> str:
        return (
            f"{str(self.ufd.ppn):<11} {self.basename:12}  {self.protection:02o}  {str(self.creation_date or ''):12}  "
            f"{self.position:>5}  {self.next:>5}  {self.retrieval_pointer:>5}  {self.length:>5}"
        )

    def __repr__(self) -> str:
        return str(self)


class AbstractFileDirectory:
    """
    Abstract (Master or User) File Directory
    """

    fs: "TSS8Filesystem"
    words: t.List[int]
    entries: t.List["TSS8AbstractDirectoryEntry"]

    def __init__(self, fs: "TSS8Filesystem"):
        self.fs = fs

    def retrieval_blocks(self, retrieval_pointer: int) -> t.Iterator[int]:
        """
        Retrieval block

        A retrieval block is a linked list of blocks that contain the retrieval segments.

        Word
             +-----------------------+
          0  | Next Retrieval Block  | (offset in MFD/UFD)
             +-----------------------+
          1  | Retrieval Segment 1   |
             /                       /
          7  | Retrieval Segment 7   |
             +-----------------------+
        """
        while retrieval_pointer != 0:
            next_retrieval = self.words[retrieval_pointer]  # Link to next retrieval block
            retrieval_segments = self.words[retrieval_pointer + 1 : retrieval_pointer + RETRIEVAL_SIZE]
            for ret_block in retrieval_segments:
                if ret_block != 0:
                    # Calculate the disk block number
                    yield ret_block - 1 + self.fs.mfd_block
            retrieval_pointer = next_retrieval

    def resize_retrieval_blocks(
        self,
        retrieval_pointer: int,
        number_of_blocks: int,
        free_dir_blocks: t.Optional[t.List[int]] = None,
    ) -> None:
        """
        Resize the retrieval blocks to the specified size.
        This will allocate or free blocks as necessary.
        """
        current_blocks = list(self.retrieval_blocks(retrieval_pointer))
        current_size = len(current_blocks)
        if number_of_blocks <= 0:
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))
        if number_of_blocks < current_size:
            self.reduce_retrieval_blocks(
                retrieval_pointer=retrieval_pointer,
                number_of_blocks=number_of_blocks,
                free_dir_blocks=free_dir_blocks,
            )
        elif number_of_blocks > current_size:
            self.extend_retrieval_blocks(
                retrieval_pointer=retrieval_pointer,
                extend_number_of_blocks=number_of_blocks - current_size,
                free_dir_blocks=free_dir_blocks,
            )

    def reduce_retrieval_blocks(
        self,
        retrieval_pointer: int,
        number_of_blocks: int,
        free_dir_blocks: t.Optional[t.List[int]] = None,
    ) -> None:
        """
        Reduce the retrieval blocks by freeing blocks.
        This will free the last `reduce_number_of_blocks` blocks.
        """
        bitmap = self.fs.read_bitmap()
        while retrieval_pointer != 0:
            next_retrieval = self.words[retrieval_pointer]  # Link to next retrieval block
            for i in range(retrieval_pointer + 1, retrieval_pointer + RETRIEVAL_SIZE):
                if self.words[i] != 0:
                    if number_of_blocks == 0:
                        bitmap.set_free(self.words[i])
                        self.words[i] = 0  # Clear the block
                    else:
                        number_of_blocks -= 1
            retrieval_pointer = next_retrieval
        # Write the bitmap back to disk
        bitmap.write()
        self.write()  # Write the updated retrieval blocks to disk

    def extend_retrieval_blocks(
        self,
        retrieval_pointer: int,
        extend_number_of_blocks: int,
        free_dir_blocks: t.Optional[t.List[int]] = None,
    ) -> None:
        """
        Extend the retrieval blocks by allocating new blocks.
        This will allocate new blocks and link them to the retrieval pointer.
        """
        # Allocate new blocks if needed
        bitmap = self.fs.read_bitmap()
        allocated_blocks = bitmap.allocate(extend_number_of_blocks)
        if free_dir_blocks is None:
            free_dir_blocks = self.get_free_file_directory_blocks()

        while allocated_blocks:
            next_retrieval = self.words[retrieval_pointer]  # Link to next retrieval block
            for i in range(retrieval_pointer + 1, retrieval_pointer + RETRIEVAL_SIZE):
                if self.words[i] == 0:
                    self.words[i] = allocated_blocks.pop(0)
                    if not allocated_blocks:
                        break
            if allocated_blocks and next_retrieval == 0:
                # If we still have blocks to allocate
                # we need to create a new retrieval block
                next_retrieval = free_dir_blocks.pop(0)
                # TODO - extend the directory if needed
                self.words[retrieval_pointer] = next_retrieval
                self.words[next_retrieval : next_retrieval + RETRIEVAL_SIZE] = [0] * RETRIEVAL_SIZE
            retrieval_pointer = next_retrieval
        bitmap.write()  # Write the bitmap back to disk
        self.write()  # Write the updated retrieval blocks to disk

    def free_retrieval_blocks(self, retrieval_pointer: int) -> None:
        """
        Free the retrieval blocks, updating the bitmap accordingly.
        """
        bitmap = self.fs.read_bitmap()
        while retrieval_pointer != 0:
            next_retrieval = self.words[retrieval_pointer]  # Link to next retrieval block
            retrieval_segments = self.words[retrieval_pointer + 1 : retrieval_pointer + RETRIEVAL_SIZE]
            for ret_block in retrieval_segments:
                if ret_block != 0:
                    # assert not bitmap.is_free(ret_block)
                    bitmap.set_free(ret_block)
            self.words[retrieval_pointer : retrieval_pointer + RETRIEVAL_SIZE] = [
                0
            ] * RETRIEVAL_SIZE  # Clear the retrieval block
            retrieval_pointer = next_retrieval
        bitmap.write()  # Write the bitmap back to disk
        self.write()  # Write the updated retrieval blocks to disk

    def get_used_file_directory_blocks(self) -> t.Set[int]:
        """
        Get the used blocks in the File Directory
        A File Directory block is group of 8 words longs.
        """
        result = set()
        for entry in self.entries:
            result.add(entry.position)
            if entry.retrieval_pointer != 0:
                retrieval_pointer = entry.retrieval_pointer
                result.add(retrieval_pointer)
                while self.words[retrieval_pointer] != 0:
                    retrieval_pointer = self.words[retrieval_pointer]
                    result.add(retrieval_pointer)
        return result

    def get_free_file_directory_blocks(self) -> t.List[int]:
        """
        Get the free blocks in the File Directory
        A File Directory block is group of 8 words longs.
        """
        total_space = set(range(0, len(self.words), ENTRY_SIZE))
        used_blocks = self.get_used_file_directory_blocks()
        return sorted(total_space - used_blocks)

    def read_file(self, retrieval_pointer: int) -> t.List[int]:
        result = []
        for block in self.retrieval_blocks(retrieval_pointer):
            result += self.fs.read_12bit_words_block(block)
        return result

    def write_file(self, retrieval_pointer: int, words: t.List[int]) -> None:
        for i, block in enumerate(self.retrieval_blocks(retrieval_pointer)):
            tmp = words[i * WORDS_PER_BLOCK : (i + 1) * WORDS_PER_BLOCK]
            self.fs.write_12bit_words_block(block, tmp)

    @abstractmethod
    def write(self) -> None:
        """
        Write the Master File Directory back to the disk
        """
        pass


class MasterFileDirectory(AbstractFileDirectory):
    """
    Master File Directory
    """

    @classmethod
    def read(cls, fs: "TSS8Filesystem") -> "MasterFileDirectory":
        """
        Read the Master File Directory from disk
        """
        self = cls(fs)
        self.words = fs.read_12bit_words_track(self.fs.mfd_block)
        self.entries = []
        position = 0
        while True:
            entry = MasterFileDirectoryEntry.read(self, position)
            self.entries.append(entry)
            position = entry.next
            if position == 0:
                break
        return self

    def write(self) -> None:
        """
        Write the Master File Directory back to the disk
        """
        self.fs.write_12bit_words_track(self.fs.mfd_block, self.words)

    def create_ufd(
        self,
        ppn: PPN,
        password: str = "",
    ) -> t.Optional["MasterFileDirectoryEntry"]:
        """
        Create a User File Directory
        """
        bitmap = self.fs.read_bitmap()
        free_dir_blocks = self.get_free_file_directory_blocks()

        # Create a new entry
        entry = MasterFileDirectoryEntry.create(
            mfd=self,
            ppn=ppn,
            position=free_dir_blocks.pop(0),
            retrieval_pointer=free_dir_blocks.pop(0),
            password=password,
        )

        # Update the previous entry
        prev_entry = self.entries[-1]
        prev_entry.next = entry.position
        prev_entry.update_dir()  # Write the previous entry to the UFD

        # Update the retrieval pointer
        block = bitmap.allocate_one()
        self.words[entry.retrieval_pointer : entry.retrieval_pointer + ENTRY_SIZE] = [0, block, 0, 0, 0, 0, 0, 0]

        # Write an empty block for the UFD
        self.fs.write_12bit_words_block(block - 1 + self.fs.mfd_block, [0] * WORDS_PER_BLOCK)

        # Write the entry to the MFD
        self.write()
        # Write the bitmap back to disk
        bitmap.write()

        # Add the entry to the list of entries
        self.entries.append(entry)
        return entry

    def delete_ufd(self, entry: "MasterFileDirectoryEntry") -> bool:
        """
        Delete an User File Directory
        """
        try:
            # Re-read the directory
            self.words = self.fs.read_12bit_words_track(self.fs.mfd_block)
            # Get the index of the entry to be deleted
            index = self.entries.index(entry)
            # Get the previous entry
            prev_entry = self.entries[index - 1]
            # Remove the entry from the list
            del self.entries[index]
            self.words[entry.position : entry.position + ENTRY_SIZE] = [0] * ENTRY_SIZE
            # Update the next pointer of the previous entry
            prev_entry.next = entry.next
            # Write the previous entry
            self.words[prev_entry.position : prev_entry.position + ENTRY_SIZE] = prev_entry.to_words()
        except ValueError:
            # If the entry is not found, it means it has already been deleted or does not exist
            return False
        # Free the retrieval blocks, updating the bitmap accordingly
        self.free_retrieval_blocks(entry.retrieval_pointer)
        self.write()
        return True


class UserFileDirectory(AbstractFileDirectory):
    """
    User File Directory
    """

    mfd_entry: "MasterFileDirectoryEntry"

    @classmethod
    def read(cls, mfd_entry: "MasterFileDirectoryEntry") -> "UserFileDirectory":
        """
        Read the User File Directory from disk
        """
        self = cls(mfd_entry.fs)
        self.mfd_entry = mfd_entry
        self.words = mfd_entry.mfd.read_file(mfd_entry.retrieval_pointer)
        self.entries = []
        # Read the User File Directory entries
        position = 0
        while True:
            entry = UserFileDirectoryEntry.read(self, position)
            self.entries.append(entry)
            position = entry.next
            if position == 0:
                break
        return self

    def write(self) -> None:
        """
        Write the User File Directory back to disk
        """
        self.mfd_entry.mfd.write_file(self.mfd_entry.retrieval_pointer, self.words)

    def delete(self, entry: "UserFileDirectoryEntry") -> bool:
        """
        Delete a file from the User File Directory
        """
        try:
            # Re-read the words from the MFD entry
            self.words = self.mfd_entry.mfd.read_file(self.mfd_entry.retrieval_pointer)
            # Get the index of the entry to be deleted
            index = self.entries.index(entry)
            # Get the previous entry
            prev_entry = self.entries[index - 1]
            # Remove the entry from the list
            del self.entries[index]
            self.words[entry.position : entry.position + ENTRY_SIZE] = [0] * ENTRY_SIZE
            # Update the next pointer of the previous entry
            prev_entry.next = entry.next
            # Write the previous entry
            self.words[prev_entry.position : prev_entry.position + ENTRY_SIZE] = prev_entry.to_words()
        except ValueError:
            # If the entry is not found, it means it has already been deleted or does not exist
            return False
        # Free the retrieval blocks, updating the bitmap accordingly
        self.free_retrieval_blocks(entry.retrieval_pointer)
        self.write()
        return True

    def resize(self, entry: "UserFileDirectoryEntry", number_of_blocks: int) -> None:
        """
        Resize the file to the specified size
        """
        if number_of_blocks < 0:
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))
        if number_of_blocks == 0:
            self.delete(entry)
            return
        entry.length = number_of_blocks
        entry.update_dir()  # Write the entry to the UFD
        self.resize_retrieval_blocks(entry.retrieval_pointer, number_of_blocks)

    def create_file(
        self,
        basename: str,
        number_of_blocks: int,
        protection_code: int = DEFAULT_PROTECTION_CODE,
        creation_date: t.Optional[date] = None,
    ) -> "UserFileDirectoryEntry":
        """
        Create a new file in the User File Directory
        """
        free_dir_blocks = self.get_free_file_directory_blocks()
        # TODO check UFD space

        # Create a new entry
        entry = UserFileDirectoryEntry.create(
            ufd=self,
            basename=basename,
            number_of_blocks=number_of_blocks,
            position=free_dir_blocks.pop(0),
            retrieval_pointer=free_dir_blocks.pop(0),
            protection_code=protection_code,
            creation_date=creation_date,
        )

        # Update the previous entry
        prev_entry = self.entries[-1]
        prev_entry.next = entry.position
        prev_entry.update_dir()  # Write the previous entry to the UFD

        # Update the retrieval pointer
        self.words[entry.retrieval_pointer : entry.retrieval_pointer + RETRIEVAL_SIZE] = [0] * RETRIEVAL_SIZE

        # Extend the retrieval blocks to the specified size, and write the entry/bitmap
        self.extend_retrieval_blocks(entry.retrieval_pointer, number_of_blocks, free_dir_blocks=free_dir_blocks)
        return entry

    @property
    def ppn(self) -> PPN:
        """
        Get the Project-Programmer Numbers User of the User File Directory
        """
        return self.mfd_entry.ppn


class StorageAllocationTable:
    """
    Storage Allocation Table

    SAT resides in FIP at 0o7777 and extends down through 0o7777-(SAT_SIZE-1).
    Each bit position represents 1 block of file storage.
    A block is available if its sat bit has the value 0.

    Word                               Size
           +------------------------+
    0o7250 | SATBOT                 |     1
           +------------------------+
    0o7251 | SATCNT                 |     1
           +------------------------+
    0o7252 | SAT                    | 0o530
           /                        /
    0o7777 |                        |
           +------------------------+
    """

    fs: "TSS8Filesystem"
    bitmaps: t.List[int]

    def __init__(self, fs: "TSS8Filesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "TSS8Filesystem") -> "StorageAllocationTable":
        """
        Read the bitmap blocks
        """
        self = StorageAllocationTable(fs)
        # Read the SAT from the FIP track
        words = self.fs.read_12bit_words_track(FIP_BLOCK)
        self.bitmaps = words[SAT_START_POS : SAT_END_POS + 1]
        return self

    def write(self) -> None:
        """
        Write the bitmap blocks
        """
        # Read the SAT from the FIP track
        words = self.fs.read_12bit_words_track(FIP_BLOCK)
        words[SAT_CNT] = self.free()  # Update the available segments count
        words[SAT_START_POS : SAT_END_POS + 1] = self.bitmaps
        # Write the SAT back to the FIP track
        self.fs.write_12bit_words_track(FIP_BLOCK, words)

    @property
    def total_bits(self) -> int:
        """
        Return the bitmap length in bit
        """
        return len(self.bitmaps) * 12

    def is_free(self, bit_index: int) -> bool:
        """
        Check if a block is free
        """
        int_index = bit_index // 12
        bit_position = bit_index % 12
        bit_value = self.bitmaps[int_index]
        return (bit_value & (1 << bit_position)) == 0

    def set_used(self, bit_index: int) -> None:
        """
        Mark a block as used
        """
        int_index = bit_index // 12
        bit_position = bit_index % 12
        self.bitmaps[int_index] |= 1 << bit_position

    def set_free(self, bit_index: int) -> None:
        """
        Mark a block as free
        """
        int_index = bit_index // 12
        bit_position = bit_index % 12
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

    def allocate_one(self) -> int:
        """ "
        Allocate one block
        """
        for block in range(0, self.total_bits):
            if self.is_free(block):
                self.set_used(block)
                return block
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
        return self.total_bits - self.used()

    def __str__(self) -> str:
        free = self.free()
        used = self.used()
        return f"LEFT: {free:<6} USED: {used:<6}"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, StorageAllocationTable) and self.bitmaps == other.bitmaps  # type: ignore


class TSS8Filesystem(AbstractFilesystem, BlockDevice12Bit):
    """
    TSS/8 Filesystem

    Disk storage
                               Words
    +-----------------------+
    | Monitor               |  20k
    |                       |
    +-----------------------+
    | Swapping area         |  4k * users
    |                       |
    +-----------------------+
    | File area             |
    |                       |
    +-----------------------+

    TSS/8 TIME-SHARING SYSTEM USER'S GUIDE, Pag 117
    https://bitsavers.org/pdf/dec/pdp8/tss8/DEC-T8-MRFB-D_UserGde_Feb70.pdf

    System Manager's Guide for PDP-8E TSS 8.24 Monitor, Pag 147
    https://bitsavers.org/pdf/dec/pdp8/tss8/TSS8_8.24_ManagersGuide.pdf
    """

    fs_name = "tss8"
    fs_description = "PDP-8 TSS/8"

    users: int  # Number of users
    mfd_block: int  # Block number of the Master File Directory
    ppn: PPN = DEFAULT_PPN

    @classmethod
    def mount(cls, file: "AbstractFile") -> "AbstractFilesystem":
        self = cls(file)
        self.users, self.mfd_block = self.guess_users()
        # self.users = 20  # Default number of users
        # self.mfd_block = MONITOR_SIZE + BLOCKS_PER_TRACK * self.users
        return self

    def guess_users(self) -> t.Tuple[int, int]:
        for users in range(8, 32):
            block_number = MONITOR_SIZE + BLOCKS_PER_TRACK * users
            # block_number = (MONITOR_SIZE + 4 * users) * 4
            words = self.read_12bit_words_block(block_number)
            # The first 8-word block of the UFD is a dummy block.
            # It contains all zeros except for a pointer to the next block
            if words[UFD_NEXT_POS] != 0o10:  # Link to next block
                continue
            if words[ENTRY_SIZE + UFD_EXT_PROTECTION_POS] & 0o7700:  # Protection bits
                continue
            if words[ENTRY_SIZE + UFD_RETRIEVAL_POINTER_POS] != 0o20:  # Pointer to retrieval
                continue
            if words[ENTRY_SIZE + ENTRY_SIZE] != 0:  # PPN
                continue
            return users, block_number
        raise OSError(errno.EIO, os.strerror(errno.EIO), "No valid MFD found")

    def read_12bit_words_track(self, first_block_number: int) -> t.List[int]:
        """
        Read a track of 12-bit words
        """
        words = []
        for i in range(0, BLOCKS_PER_TRACK):
            words += self.read_12bit_words_block(first_block_number + i)
        return words

    def write_12bit_words_track(self, first_block_number: int, words: t.List[int]) -> None:
        """
        Write a track of 12-bit words
        """
        for i in range(0, BLOCKS_PER_TRACK):
            tmp = words[i * WORDS_PER_BLOCK : (i + 1) * WORDS_PER_BLOCK]
            self.write_12bit_words_block(first_block_number + i, tmp)

    def read_mfd_entries(
        self,
        ppn: PPN = ANY_PPN,
    ) -> t.Iterator["MasterFileDirectoryEntry"]:
        """Read Master File Directory entries"""
        mfd = MasterFileDirectory.read(self)
        entry: "MasterFileDirectoryEntry"
        for entry in mfd.entries:  # type: ignore
            if ppn.match(entry.ppn) and not entry.is_dummy:  # Filter by PPN
                yield entry

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
        ppn: t.Optional[PPN] = None,
    ) -> t.Iterator["UserFileDirectoryEntry"]:
        if ppn is None:
            ppn = self.ppn
        ppn, filename_pattern = tss8_split_fullname(fullname=pattern, wildcard=wildcard, ppn=ppn)

        if pattern and not filename_pattern and not expand:
            # If expand is False, check if the pattern is a PPN
            try:
                ppn = PPN.from_str(pattern)
                mfd = MasterFileDirectory.read(self)
                mfd_entry: "MasterFileDirectoryEntry"
                for mfd_entry in mfd.entries:  # type: ignore
                    if ppn.match(mfd_entry.ppn) and not mfd_entry.is_dummy:  # Filter by PPN
                        yield mfd_entry  # type: ignore
                return
            except Exception:
                pass

        for mfd_entry in self.read_mfd_entries(ppn=ppn):
            if ppn != MFD_PPN or include_all:
                yield from mfd_entry.iterdir(pattern=filename_pattern, include_all=include_all, wildcard=wildcard)

    @property
    def entries_list(self) -> t.Iterator["UserFileDirectoryEntry"]:
        for mfd_entry in self.read_mfd_entries(ppn=self.ppn):
            yield from mfd_entry.iterdir()

    def get_file_entry(self, fullname: str) -> "UserFileDirectoryEntry":
        """
        Get the directory entry for a file
        """
        fullname = tss8_canonical_filename(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        ppn, basename = tss8_split_fullname(fullname=fullname, wildcard=False, ppn=self.ppn)
        try:
            return next(self.filter_entries_list(basename, wildcard=False, ppn=ppn, expand=False))
        except StopIteration:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def read_bitmap(self) -> StorageAllocationTable:
        """
        Read the bitmap
        """
        return StorageAllocationTable.read(self)

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: t.Optional[date] = None,
        file_type: t.Optional[str] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        number_of_blocks = int(math.ceil(len(content) / TSS8_BLOCK_SIZE_BYTES))
        entry = self.create_file(fullname, number_of_blocks, creation_date, file_type)
        if entry is not None:
            content = content + (b"\0" * TSS8_BLOCK_SIZE_BYTES)
            f = entry.open(file_mode)
            try:
                f.write_block(content, block_number=0, number_of_blocks=entry.length)
            finally:
                f.close()

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,
    ) -> t.Optional[UserFileDirectoryEntry]:
        try:
            entry = self.get_file_entry(fullname)
            entry.resize(number_of_blocks)
            return entry
        except FileNotFoundError:
            pass
        ppn, basename = tss8_split_fullname(fullname=fullname, wildcard=False, ppn=self.ppn)
        # Get the User File Directory entry for the PPN
        try:
            mfd_entry = next(self.read_mfd_entries(ppn=ppn))
        except Exception:
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(ppn))
        ufd = UserFileDirectory.read(mfd_entry)
        # Create a new file entry in the User File Directory
        return ufd.create_file(
            basename=basename,
            number_of_blocks=number_of_blocks,
            creation_date=creation_date,
        )

    def create_directory(
        self,
        fullname: str,
        options: t.Dict[str, t.Union[bool, str]],
    ) -> t.Optional["MasterFileDirectoryEntry"]:
        """
        Create a User File Directory
        """
        # Check if the PPN is valid
        try:
            ppn = PPN.from_str(fullname)
        except Exception:
            raise OSError(errno.EINVAL, "Invalid PPN")

        # Check if the PPN already exists
        if list(self.read_mfd_entries(ppn=ppn)):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST))

        # Create the User File Directory
        mfd = MasterFileDirectory.read(self)
        return mfd.create_ufd(ppn=ppn)

    def chdir(self, fullname: str) -> bool:
        """
        Change the current Project-Programmer Number (PPN)
        """
        try:
            self.ppn = PPN.from_str(fullname)
            return True
        except Exception:
            return False

    def get_pwd(self) -> str:
        """
        Get the current Project-Programmer Number (PPN)
        """
        return str(self.ppn)

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        ppn, _ = tss8_split_fullname(fullname=pattern, wildcard=True, ppn=self.ppn)
        if options.get("uic") or ppn == MFD_PPN:
            if not options.get("brief"):
                dt = datetime.now().strftime('%d-%b-%y  %H:%M:%S').upper()
                sys.stdout.write(f"SYSTEM ACCOUNT    {dt}\n\n")
                sys.stdout.write(" PASSWORD    CPU        DEV     DISK  QUOTA\n\n")
            # Listing of all PPN
            for mfd in self.read_mfd_entries():
                if options.get("brief"):
                    sys.stdout.write(f"{mfd.ppn}\n")
                else:
                    cpu_time = format_time(int(mfd.cpu_time * 6.4))
                    device_time = format_time(int(mfd.device_time * 51.2))
                    disk_usage = mfd.disk_usage()
                    sys.stdout.write(
                        f"{mfd.ppn.to_word():>4o} {mfd.password:4}  {cpu_time}  {device_time} {disk_usage:>5}  {mfd.quota:>5}\n"
                    )
        else:
            blocks = 0
            if not options.get("brief"):
                dt = date.today().strftime('%d-%b-%y').upper()
                sys.stdout.write(f"DISK FILES FOR USER {ppn.group:2o},{ppn.user:2o} ON  {dt:>9}\n\n")
                sys.stdout.write("NAME      SIZE  PROT    DATE\n")
            for entry in self.filter_entries_list(pattern):
                if options.get("brief"):
                    sys.stdout.write(f"{entry.basename}\n")
                else:
                    try:
                        dt = entry.creation_date.strftime('%d-%b-%y').upper()  # type: ignore
                    except Exception:
                        dt = ""
                    blocks += entry.length
                    sys.stdout.write(
                        f"{entry.filename:<6}.{entry.extension:<3} {entry.length:>3}   {entry.protection:2o}  {dt:>9}\n"
                    )
            if not options.get("brief"):
                sys.stdout.write(f"\nTOTAL DISK SEGMENTS:  {blocks:<6}\n")
                # sys.stdout.write(f"\nTOTAL DISK SEGMENTS:  {blocks:<6} QUOTA: 1575\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if options.get("bitmap"):
            # Display the bitmap
            bitmap = self.read_bitmap()
            for i in range(0, bitmap.total_bits):
                sys.stdout.write(f"{i:>4} {'[ ]' if bitmap.is_free(i) else '[X]'}  ")
                if i % 16 == 15:
                    sys.stdout.write("\n")
            sys.stdout.write(f"\n{bitmap}\n")
        elif arg:
            sys.stdout.write("PPN         Basename     Prt  Creation        Pos   Next    Ret Length\n")
            sys.stdout.write("---         --------     ---  --------        ---   ----    --- ------\n")
            ppn, filename_pattern = tss8_split_fullname(fullname=arg, wildcard=True, ppn=self.ppn)
            if filename_pattern and "*" not in filename_pattern:
                entry = self.get_file_entry(arg)
                # sys.stdout.write(f"{entry}\n")
                sys.stdout.write(f"{entry} {list(entry.blocks())}\n")
            else:
                for entry in self.filter_entries_list(arg, include_all=False):
                    sys.stdout.write(f"{entry}\n")
        else:
            sys.stdout.write(f"Number of users:          {self.users}\n\n")
            sys.stdout.write("PPN         Password  Next Quota  Dev   CPU  Retrieval\n")
            sys.stdout.write("                                  Time  Time Pointer\n")
            sys.stdout.write("---------   --------  ---- -----  ----  ---- ---------\n")
            for mfd in self.read_mfd_entries():
                sys.stdout.write(f"{mfd}\n")

    def dump(self, fullname: t.Optional[str], start: t.Optional[int] = None, end: t.Optional[int] = None) -> None:
        """Dump the content of a file or a range of blocks"""
        if fullname:
            entry = self.get_file_entry(fullname)
            if start is None:
                start = 0
            if end is None or end > entry.get_length() - 1:
                end = entry.get_length() - 1
            blocks = list(entry.blocks())[start : end + 1]
            for i, block_number in enumerate(blocks):
                words = self.read_12bit_words_block(block_number)
                sys.stdout.write(f"\nBLOCK NUMBER   {i:08}\n")
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
                sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                oct_dump(words)

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        """
        Create an empty TSS/8 filesystem
        """
        self.users = 20  # Default number of users
        self.mfd_block = MONITOR_SIZE + BLOCKS_PER_TRACK * self.users

        bitmap = self.read_bitmap()
        # Mark the MFD blocks as used
        for i in range(0, BLOCKS_PER_TRACK):
            bitmap.set_used(i)
        # Mark the last block of the bitmap as used
        for i in range((self.get_size() // BLOCK_SIZE) - self.mfd_block, bitmap.total_bits):
            bitmap.set_used(i)
        bitmap.write()

        # Create the Master File Directory
        mfd = MasterFileDirectory.read(self)
        mfd.words[UFD_NEXT_POS] = 0o10  # Link to next block
        mfd.words[UFD_NEXT_POS + 8] = 0o10 + 8  # Link to next block
        mfd.create_ufd(ppn=PPN(0, 1), password="SYSTEM")
        mfd.create_ufd(ppn=PPN(0, 2), password="LIBRARY")
        mfd.create_ufd(ppn=PPN(0, 3), password="OPERATOR")

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def close(self) -> None:
        self.f.close()

    def __str__(self) -> str:
        return str(self.f)
