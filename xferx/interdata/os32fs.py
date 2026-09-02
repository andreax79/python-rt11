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

import io
import math
import errno
import functools
import os
import struct
import sys
import typing as t
from datetime import date, datetime, timedelta

from ..device.block import BlockDevice
from ..abstract import AbstractBlockFilesystem, XFERXPath, AbstractFile
from ..commons import (
    ASCII,
    IMAGE,
    READ_FILE_FULL,
    filename_match,
)
from ..device.abstract import AbstractDevice

__all__ = [
    "OS32File",
    "OS32Filesystem",
    "os32_canonical_filename",
]

# Account number is a decimal number ranging from 0 to 65535
# Account number 0 is for system files and is the default for all operator commands.
# Account number 255 is reserved for the MTM system administrator.
# Account numbers 1 through 65,535 (excluding 255) are used by MTM for terminal users.

# The file classes are:
# - P for a private file
# - G for a group file
# - S for a system file

OS32_BLOCK_SIZE = 256
WORDS_PER_BLOCK = OS32_BLOCK_SIZE // 4  # Number of 32-bit words per block
assert WORDS_PER_BLOCK == 64

BLOCKS_PER_INDEX_BLOCK = WORDS_PER_BLOCK - 2  # Number of data blocks per index block

FILE_NAME_LENGTH = 8  # Maximum file name length
FILE_EXTENSION_LENGTH = 3  # Maximum file extension length
DIR_ENTRIES = 5  # Directory entries per block

VOLUME_DESCRIPTOR_BLOCK = 0
VOLUME_DESCRIPTOR_FORMAT = ">4sIIIIIIII"
VOLUME_DESCRIPTOR_LENGTH = struct.calcsize(VOLUME_DESCRIPTOR_FORMAT)
VOLUME_NAME_LENGTH = 4

DIR_ENTRY_FORMAT = ">8s3sBIIBBHIIHHBBBBII"
DIR_ENTRY_LENGTH = struct.calcsize(DIR_ENTRY_FORMAT)
assert DIR_ENTRY_LENGTH == 48

AUF_ENTRY_FORMAT = ">HH12s20sII8sIIII"
AUF_ENTRY_LENGTH = 128
AUF_FILE = "USERS.AUF/255"

# Type of files
CO_FILE_TYPE = 0  # CO - Contiguous file
EC_FILE_TYPE = 1  # EC - Extendable contiguous file
IN_FILE_TYPE = 2  # IN - Indexed file
NB_FILE_TYPE = 3  # NB - Non-buffered indexed file
LR_FILE_TYPE = 6  # LR - Long record file
IT_FILE_TYPE = 7  # IT - Data comm line control block

FILE_TYPES = {
    CO_FILE_TYPE: ("CO", "Contiguous"),
    EC_FILE_TYPE: ("EC", "Extendable contiguous"),
    IN_FILE_TYPE: ("IN", "Indexed"),
    NB_FILE_TYPE: ("NB", "Non-buffered indexed"),
    LR_FILE_TYPE: ("LR", "Long record"),
    IT_FILE_TYPE: ("IT", "Data comm line control block"),
}

ANY_ACCOUNT = -1
DEFAULT_VOLUME_NAME = "OS32"
DEFAULT_BLOCK_SIZE = 8
DEFAULT_INDEX_BLOCK_SIZE = 1
DEFAULT_RECORD_LENGTH = 80


def os32_get_file_type_id(file_type: t.Optional[str], default: int = IN_FILE_TYPE) -> int:
    """
    Get the file type id from a string
    """
    if not file_type:
        return default
    file_type = file_type.upper()
    for file_id, file_desc in FILE_TYPES.items():
        if file_desc[0] == file_type:
            return file_id
    raise Exception("?KMON-F-Invalid file type specified with option")


def os32_file_type_description(file_type: int) -> str:
    """
    Get the file type description from a file type id
    """
    try:
        return FILE_TYPES[file_type][1]
    except KeyError:
        return f"Unknown ({file_type})"


def bytes_to_int(buffer: bytes, position: int) -> int:
    return struct.unpack_from(">I", buffer, position)[0]  # type: ignore


def int_to_bytes(n: int) -> bytes:
    return struct.pack(">I", n)  # type: ignore


def os32_to_date(n: int) -> t.Optional[datetime]:
    """
    Convert OS/32 date and time to datetime
    """
    try:
        day_number = n & 0xFFFF
        year = 1900 + (day_number & 0x7F)
        if year < 1970:
            year += 100
        t = day_number >> 7
        month = t // 32
        day = t % 32
        minutes = n >> 16
        h = minutes // 60
        m = minutes % 60
        return datetime(year, month, day, h, m)
    except Exception:
        return None


def date_to_os32(dt: t.Union[datetime, date]) -> int:
    """
    Convert datetime to OS/32 date and time
    """
    year_field = (dt.year - 1900) & 0x7F
    t = dt.month * 32 + dt.day
    day_number = (t << 7) | year_field
    if isinstance(dt, datetime):
        minutes = dt.hour * 60 + dt.minute
    else:
        minutes = 0
    return (minutes << 16) | day_number


def format_time(dt: t.Optional[datetime]) -> str:
    """
    Format date and time
    """
    if not dt:
        return " " * 14
    tmp = dt.strftime("%m/%d/%y %H:%M").lstrip("0")
    return f"{tmp:>14}"


def records_to_ascii(data: bytes, record_length: int, number_of_recors: int) -> bytes:
    """
    Convert OS/32 records to ASCII
    """
    if record_length <= 0:
        raise ValueError(f"Invalid record length: {record_length}")
    result = bytearray()
    for i in range(number_of_recors):
        p = i * record_length
        record = data[p : p + record_length]
        # cut the record at the first 0x0d
        record = record.split(b"\x0d", 1)[0]
        record = record.split(b"\x00", 1)[0]
        result.extend(record)
        result.extend(b"\x0a")
    return bytes(result)


def ascii_to_records(data: t.Union[bytes, bytearray], record_length: int) -> t.Tuple[bytes, int]:
    """
    Convert ASCII to OS/32 records
    """
    if record_length <= 0:
        raise ValueError(f"Invalid record length: {record_length}")
    result = bytearray()
    num_of_records = 0
    if data.endswith(b"\x0a"):
        data = data[:-1]
    for line in data.split(b"\x0a"):
        line = line.rstrip(b"\x0d")
        if len(line) > record_length:
            raise ValueError(f"Line too long: {str(line)}")
        result.extend(line.ljust(record_length, b"\x00"))
        num_of_records += 1
    return bytes(result), num_of_records


def os32_canonical_filename(fullname: t.Optional[str], wildcard: bool = False) -> str:
    """
    Generate the canonical OS/32 name

    filename[.ext]

    filename - up to 8 characters alphanumeric, the first character must be alphabetic
    ext      - up to 3 characters alphanumeric
    """
    fullname = (fullname or "").upper()
    try:
        stem, extension = fullname.split(".", 1)
    except Exception:
        stem = fullname
        extension = "*" if wildcard else ""

    stem = stem[:FILE_NAME_LENGTH]
    # The first character must be alphabetic, the remaining alphanumeric
    if not stem or not (stem[0].isalpha() or (wildcard and stem[0] == '*')):
        raise ValueError(f"Invalid filename: {stem} (First character must be alphabetic)")
    if not all(c.isalnum() or (wildcard and c == '*') for c in stem):
        raise ValueError(f"Invalid filename: {stem} (Only alphanumeric characters are allowed)")
    extension = extension[:FILE_EXTENSION_LENGTH]
    if not all(c.isalnum() or (wildcard and c == '*') for c in extension):
        raise ValueError(f"Invalid extension: {extension} (Only alphanumeric characters are allowed)")
    if not extension:
        return stem
    else:
        return f"{stem}.{extension}"


def os32_split_fullname(
    account: t.Optional[int], fullname: t.Optional[str], wildcard: bool = True
) -> t.Tuple[t.Optional[int], t.Optional[str]]:
    """
    Split the fullname into account and canonical filename
    """
    if fullname:
        if "/" in fullname:
            try:
                tmp = fullname.split("/", 2)
                if wildcard and tmp[1] == "*":
                    account = ANY_ACCOUNT
                else:
                    account = int(tmp[1])
                fullname = tmp[0]
            except Exception:
                return account, fullname
        if fullname:
            fullname = os32_canonical_filename(fullname, wildcard=wildcard)
    return account, fullname


def account_match(entry_account: int, account: t.Optional[int]) -> bool:
    """
    Check if the account matches the entry account
    """
    return account is None or account == ANY_ACCOUNT or entry_account == account


class OS32Bitmap:
    fs: "OS32Filesystem"
    bitmaps: t.List[int]
    num_of_sectors: int  # number of 256 bytes sectors on the disk
    num_of_bitmap_sectors: int  # length of the bitmap in 256 bytes sectors

    def __init__(self, fs: "OS32Filesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "OS32Filesystem") -> "OS32Bitmap":
        """
        Read the bitmap blocks
        """
        self = OS32Bitmap(fs)
        self.num_of_sectors = self.fs.dev.get_size() // OS32_BLOCK_SIZE  # Number of 256 bytes sectors on the disk
        self.num_of_bitmap_sectors = math.ceil(self.num_of_sectors / WORDS_PER_BLOCK / 32)
        self.bitmaps = []
        for block in self.blocks:
            words = fs.read_32bit_words_block(block)
            if not words:
                raise OSError(errno.EIO, f"Failed to read block {block}")
            self.bitmaps.extend(words)
        self.bitmaps = self.bitmaps[: self.num_of_sectors]
        return self

    @classmethod
    def create(cls, fs: "OS32Filesystem") -> "OS32Bitmap":
        """
        Create the bitmap blocks
        """
        self = OS32Bitmap(fs)
        self.num_of_sectors = self.fs.dev.get_size() // OS32_BLOCK_SIZE  # Number of 256 bytes sectors on the disk
        self.num_of_bitmap_sectors = math.ceil(self.num_of_sectors / WORDS_PER_BLOCK / 32)
        self.bitmaps = [0] * self.num_of_sectors
        return self

    def write(self) -> None:
        """
        Write the bitmap blocks to the disk
        """
        for i, block in enumerate(self.blocks):
            words = self.bitmaps[i * WORDS_PER_BLOCK : (i + 1) * WORDS_PER_BLOCK]
            if len(words) < WORDS_PER_BLOCK:
                words.extend([0] * (WORDS_PER_BLOCK - len(words)))
            self.fs.write_32bit_words_block(words, block)

    @property
    def blocks(self) -> t.Iterator[int]:
        """
        Return the blocks used by the bitmap
        """
        for i in range(self.num_of_bitmap_sectors):
            yield self.fs.bitmap_block + i

    @property
    def total_bits(self) -> int:
        """
        Return the bitmap length in bit
        """
        return len(self.bitmaps) * 32

    def is_free(self, block_number: int) -> bool:
        """
        Check if a block is free
        """
        bit_index = block_number
        int_index = bit_index // 32
        bit_position = 31 - (bit_index % 32)
        bit_value = self.bitmaps[int_index]
        return (bit_value & (1 << bit_position)) == 0

    def set_free(self, block_number: int) -> None:
        """
        Mark a block as free
        """
        bit_index = block_number
        int_index = bit_index // 32
        bit_position = 31 - (bit_index % 32)
        self.bitmaps[int_index] &= ~(1 << bit_position)

    def set_used(self, block_number: int) -> None:
        """
        Allocate a block
        """
        bit_index = block_number
        int_index = bit_index // 32
        bit_position = 31 - (bit_index % 32)
        self.bitmaps[int_index] |= 1 << bit_position

    def find_contiguous_blocks(self, size: int, start_block: int = 0) -> int:
        """
        Find contiguous blocks, return the first block number
        """
        current_size = 0
        start_index = -1
        for block in range(start_block, self.total_bits):
            if self.is_free(block):
                if current_size == 0:
                    start_index = block
                current_size += 1
                if current_size == size:
                    return start_index
            else:
                current_size = 0
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def allocate(self, size: int, contiguous: bool = False, start_block: int = 0) -> t.List[int]:
        """
        Allocate contiguous or sparse blocks
        """
        blocks = []
        if contiguous and size != 1:
            start_block = self.find_contiguous_blocks(size, start_block)
            for block in range(start_block, start_block + size):
                self.set_used(block)
                blocks.append(block)

        else:
            for block in range(start_block, self.total_bits):
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
        return len(self.bitmaps) * 32 - self.used()

    def __str__(self) -> str:
        free = self.free()
        used = self.used()
        return f"LEFT: {free:<6} USED: {used:<6}"


class OS32File(AbstractFile):
    entry: "OS32Path"

    def __init__(self, entry: "OS32Path", file_mode: t.Optional[str] = None):
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
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = self.entry.get_length()
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.entry.get_length()
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        data = bytearray()
        # Get the blocks to be read
        blocks = list(self.entry.blocks())[block_number : block_number + number_of_blocks]
        # Read the blocks
        for disk_block_number in blocks:
            buffer = self.entry.fs.read_block(disk_block_number)
            data.extend(buffer)
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
            or block_number + number_of_blocks > self.entry.get_length()
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        block_size = self.get_block_size()
        # Get the blocks to be written
        blocks = list(self.entry.blocks())[block_number : block_number + number_of_blocks]
        # Write the blocks
        for i, disk_block_number in enumerate(blocks):
            data = buffer[i * block_size : (i + 1) * block_size]
            self.entry.fs.write_block(data, disk_block_number)

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return self.entry.get_length()

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


class OS32Path(XFERXPath):
    """
    File directory entry

        +-------------------------------------+-------------------------------------+
     0  |                                Filename                                   |
        |                                                                           |
        +---------------------------------------------------------------------------+
     8  |              Extension                                 |  Account (low)   |
        +--------------------------------------------------------+------------------+
    12  |                   First data sector / First index sector                  |
        +---------------------------------------------------------------------------+
    18  |                    Last data sector / Last index sector                   |
        +------------------+------------------+------------------+------------------+
    20  |    Write key     |    Read key      |            Record length            |
        +------------------+------------------+-------------------------------------+
    24  |                             Date file allocated                           |
        +---------------------------------------------------------------------------+
    28  |                            Date file last written                         |
        +-------------------------------------+-------------------------------------+
    32  |             Write count             |            Read count               |
        +------------------+------------------+------------------+------------------+
    36  |    Attributes    |    Block size    | Index block size | Shared disk sup. |
        +------------------+------------------+------------------+------------------+
    40  |                    Current sector / Number logical records                |
        +---------------------------------------------------------------------------+
    44  |                          Date file last assigned                          |
        +---------------------------------------------------------------------------+

    OS/32 R8.02 Application Level Programmer - Pag 83
    https://bitsavers.org/pdf/interdata/32bit/os32/1988_8.2/48-039F00R03_OS32_R08.2_Application_Level_Programmer_Reference_Manual_1988.pdf
    """

    fs: "OS32Filesystem"
    stem: str = ""  # File name, up to 8 characters
    extension: str = ""  # File extension, up to 3 characters
    account: int = 0  # Account number, 0-65535
    first_block: int = 0  # First data sector for contiguous file, first index sector for indexed file
    last_block: int = 0  # Last data sector for contiguous file, last index sector for indexed file
    write_key: int = 0
    read_key: int = 0
    record_length: int = 0  # Record length, in bytes
    raw_creation_date: int = 0  # Creation date and time
    raw_last_mod_date: int = 0  # Last modification date and time
    write_count: int = 0
    read_count: int = 0
    attributes: int = 0
    block_size: int = 0  # Block size, in number of 256 bytes sectors
    index_block_size: int = 0  # Size of the index block, in number of 256 bytes sectors
    shared_disk_support: int = 0
    num_of_records: int = 0  # Number of disk records or sectors currently used by the file
    raw_last_assigned_date: int = 0

    def __init__(self, fs: "OS32Filesystem"):
        self.fs = fs

    @classmethod
    def read(
        cls,
        fs: "OS32Filesystem",
        buffer: bytes,
        position: int,
    ) -> "OS32Path":
        self = cls(fs)
        (
            stem,
            extension,
            self.account,
            self.first_block,
            self.last_block,
            self.write_key,
            self.read_key,
            self.record_length,
            self.raw_creation_date,
            self.raw_last_mod_date,
            self.write_count,
            self.read_count,
            self.attributes,
            self.block_size,
            self.index_block_size,
            self.shared_disk_support,
            self.num_of_records,
            self.raw_last_assigned_date,
        ) = struct.unpack_from(DIR_ENTRY_FORMAT, buffer, position)
        self.stem = stem.decode("utf-8", errors='ignore').strip("\0 ")
        self.extension = extension.decode("utf-8", errors='ignore').strip("\0 ")
        return self

    @classmethod
    def create(
        cls,
        fs: "OS32Filesystem",
        fullname: str,
        size: int,  # Size in bytes
        record_length: int = 0,  # Record length in bytes
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # creation date
        file_type: int = IN_FILE_TYPE,  # file type
        first_block: int = 0,
        last_block: int = 0,
        block_size: int = 1,
        index_block_size: int = 1,
        num_of_records: int = 0,
        keys: int = 0,
    ) -> "OS32Path":
        """
        Create a new directory entry
        """
        self = cls(fs)
        account, fullname = os32_split_fullname(fullname=fullname, account=self.fs.account)  # type: ignore
        stem, extension = fullname.split(".", 1) if "." in fullname else (fullname, "")
        self.stem = stem
        self.extension = extension
        if account is None:
            raise FileNotFoundError(errno.ENOENT, "No account number specified")
        self.account = account
        self.write_key = (keys >> 8) & 0xFF
        self.read_key = keys & 0xFF
        self.record_length = record_length
        self.raw_creation_date = date_to_os32(creation_date or datetime.now())
        self.raw_last_mod_date = self.raw_creation_date
        self.write_count = 0
        self.read_count = 0
        self.attributes = (1 << 4) | (file_type << 5)
        self.first_block = first_block
        self.last_block = last_block
        self.block_size = block_size
        self.index_block_size = index_block_size
        self.num_of_records = num_of_records
        return self

    def to_bytes(self) -> bytes:
        """
        Convert the directory entry to bytes
        """
        buffer = bytearray(DIR_ENTRY_LENGTH)
        struct.pack_into(
            DIR_ENTRY_FORMAT,
            buffer,
            0,
            self.stem.encode("utf-8", errors='ignore').ljust(FILE_NAME_LENGTH, b" "),
            self.extension.encode("utf-8", errors='ignore').ljust(FILE_EXTENSION_LENGTH, b" "),
            self.account,
            self.first_block,
            self.last_block,
            self.write_key,
            self.read_key,
            self.record_length,
            self.raw_creation_date,
            self.raw_last_mod_date,
            self.write_count,
            self.read_count,
            self.attributes,
            self.block_size,
            self.index_block_size,
            self.shared_disk_support,
            self.num_of_records,
            self.raw_last_assigned_date,
        )
        return bytes(buffer)

    def blocks(self, include_indexes: bool = False) -> t.Iterator[int]:
        """
        Return the blocks used by the file,
        including index blocks if include_indexes is True
        """
        if self.is_contiguous:
            # Contiguous files are organized sequentially on disk
            for block_address in range(self.first_block, self.last_block + 1):
                yield block_address

        elif self.is_indexed or self.is_nonbuffered_indexed:
            next_block = self.first_block
            while next_block:
                if include_indexes:
                    for x in range(next_block, next_block + self.index_block_size):
                        yield x
                # Read the index block(s)
                words = self.fs.read_32bit_words_block(next_block, self.index_block_size)
                # The first two words of each blocks are used as links.
                # The first word is the link to the previous index block,
                # and the second word is the link to the next index block.
                next_block = words[1]
                for x in words[2:]:
                    if x != 0:
                        for block in range(x, x + self.block_size):
                            yield block
        else:
            raise NotImplementedError(f"File type {os32_file_type_description(self.raw_file_type)} not supported")

    def is_dir(self) -> bool:
        return False

    @property
    def parent(self) -> "OS32AccountDirectory":
        """The logical parent"""
        return OS32AccountDirectory(self.fs, self.account)

    @property
    def is_permanent(self) -> bool:
        """
        Permanent file
        """
        return ((self.attributes >> 4) & 1) == 1

    @property
    def is_contiguous(self) -> bool:
        """
        Contiguous file
        """
        return self.raw_file_type == CO_FILE_TYPE

    @property
    def is_extended_contiguous(self) -> bool:
        """
        Extended contiguous file
        """
        return self.raw_file_type == EC_FILE_TYPE

    @property
    def is_indexed(self) -> bool:
        """
        Indexed file
        """
        return self.raw_file_type == IN_FILE_TYPE

    @property
    def is_nonbuffered_indexed(self) -> bool:
        """
        Nonbuffered indexed file
        """
        return self.raw_file_type == NB_FILE_TYPE

    @property
    def is_long_record(self) -> bool:
        """
        Long record file
        """
        return self.raw_file_type == LR_FILE_TYPE

    @property
    def raw_file_type(self) -> int:
        """
        Raw file type
        """
        return self.attributes >> 5

    @property
    def file_type(self) -> t.Optional[str]:
        """
        File type
        """
        try:
            return FILE_TYPES[self.raw_file_type][0]
        except KeyError:
            return "??"

    @property
    def keys(self) -> int:
        """
        Read/write keys
        """
        return (self.write_key & 0xFF) << 8 | (self.read_key & 0xFF)

    @property
    def fullname(self) -> str:
        """
        Full name of the file, including account number
        """
        return f"{self.name}/{self.account}"

    @property
    def name(self) -> str:
        """
        File name with extension
        """
        if not self.extension:
            return self.stem
        else:
            return f"{self.stem}.{self.extension}"

    @property
    def last_mod_date(self) -> t.Optional[datetime]:
        """
        Last modification date
        """
        return os32_to_date(self.raw_last_mod_date)

    @property
    def creation_date(self) -> t.Optional[datetime]:
        """
        Creation date
        """
        return os32_to_date(self.raw_creation_date)

    @property
    def last_assigned_date(self) -> t.Optional[datetime]:
        """
        Last assigned date
        """
        return os32_to_date(self.raw_last_assigned_date)

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in 256 bytes blocks
        """
        if self.is_contiguous:
            return self.last_block - self.first_block + 1
        elif self.is_indexed or self.is_nonbuffered_indexed:
            return len(list(self.blocks()))
        else:
            return 0  # TODO

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get file size in bytes
        """
        return self.get_length() * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes (always 256 bytes)
        """
        return OS32_BLOCK_SIZE

    def _get_segment_and_index(self) -> t.Tuple["OS32DirectorySegment", int]:
        """
        Get the segment and position of the directory entry
        """
        for directory_segment in self.fs.get_root().read_dir_segments():
            for i, entry in enumerate(directory_segment.entries_list):
                if (
                    entry.stem == self.stem
                    and entry.extension == self.extension
                    and entry.account == self.account
                    and entry.is_permanent
                ):
                    return directory_segment, i
        raise OSError(errno.ENOENT, os.strerror(errno.ENOENT))

    def delete(self) -> bool:
        """
        Delete the file
        """
        # Delete the directory entry
        directory_segment, index = self._get_segment_and_index()
        self.attributes &= ~(1 << 4)  # Clear the permanent bit
        directory_segment.entries_list[index] = self
        directory_segment.write()
        # Free the blocks used by the file
        bitmap = self.fs.read_bitmap()
        for block in self.blocks(include_indexes=True):
            bitmap.set_free(block)
        bitmap.write()
        return True

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> "OS32File":
        """
        Open a file
        """
        return OS32File(self, file_mode=file_mode)

    def read_bytes(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> bytes:
        """
        Get the content of the file
        """
        data = super().read_bytes(file_mode, fork)
        if file_mode == ASCII and self.record_length > 0:
            return records_to_ascii(data, self.record_length, self.num_of_records)
        else:
            return data

    def examine(self) -> str:
        t = self.raw_creation_date >> 16
        buf = io.StringIO()
        buf.write(f"Name:                    {self.fullname}\n")
        buf.write(f"Type:                    {os32_file_type_description(self.raw_file_type)}\n")
        buf.write(f"Account:                 {self.account}\n")
        buf.write(f"Write/read keys:         {self.write_key}/{self.read_key}\n")
        buf.write(f"Record length:           {self.record_length}\n")
        buf.write(f"Creation date:           {self.creation_date}\n")
        buf.write(f"Last mod date:           {self.last_mod_date}\n")
        buf.write(f"Last assigned date:      {self.last_assigned_date or '-'}\n")
        buf.write(f"Write/read count:        {self.write_count}/{self.read_count}\n")
        buf.write(f"Attributes:              ${self.attributes:x}\n")
        if not self.is_contiguous:
            buf.write(f"Block size:              {self.block_size} sectors\n")
            buf.write(f"Index block size:        {self.index_block_size} sectors\n")
        buf.write(f"Shared disk support:     {self.shared_disk_support}\n")
        if self.is_indexed or self.is_nonbuffered_indexed:
            buf.write(f"Number of records:       {self.num_of_records}\n")
        else:
            buf.write(f"Number of sectors:       {self.num_of_records}\n")
        buf.write(f"Size:                    {self.get_size()} bytes\n")
        if self.is_contiguous:
            buf.write(f"First/last sector:       {self.first_block}-{self.last_block}\n")
        else:
            buf.write(f"First/last index sector: {self.first_block}-{self.last_block}\n")
        blocks = list(self.blocks())
        if self.is_indexed or self.is_nonbuffered_indexed:
            buf.write(f"Number of data sectors:  {len(blocks)}\n")
        buf.write(f"Data sectors:            {blocks}\n")
        return buf.getvalue()

    def __str__(self) -> str:
        last_mod_date = format_time(self.last_mod_date)
        return f"{self.name:>12}/{self.account:<5}  {self.file_type}  {self.get_size():>8}  {last_mod_date}"

    def __repr__(self) -> str:
        return str(self)


class OS32AccountDirectory(XFERXPath):
    """
    OS/32 Account
    """

    fs: "OS32Filesystem"
    account: int

    def __init__(self, fs: "OS32Filesystem", account: int):
        self.fs = fs
        self.account = account

    @property
    def length(self) -> int:
        """Length in blocks"""
        return 0

    def is_dir(self) -> bool:
        return True

    @property
    def parent(self) -> XFERXPath:
        """The logical parent"""
        return self.fs.get_root()

    @property
    def fullname(self) -> str:
        return f"{self.name}"

    @property
    def name(self) -> str:
        return f"{self.account}"

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return OS32_BLOCK_SIZE

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> OS32File:
        """
        Open a file
        """
        raise OSError(errno.EISDIR, os.strerror(errno.EISDIR))

    def iterdir(
        self,
        pattern: t.Optional[str] = None,
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["OS32Path"]:
        for entry in self.fs.get_root().read_entries_list():
            if (
                entry.is_permanent
                and filename_match(entry.name, pattern, wildcard)
                and account_match(entry.account, self.account)
            ):
                yield entry

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return str(self)


class VolumeDescriptor:
    """
    Volume Descriptor

    The volume descriptor is located in the first block of the volume.
    It contains information about the volume, including its name,
    attributes, and pointers to the first and second directory blocks.

    Byte
          +-------------------------------------+
    0     |             Volume name             |
          +-------------------------------------+
    4     |         Volume attributes           |
          +-------------------------------------+
    8     |       Directory block number        |
          +-------------------------------------+
    12    |       OS image block number         |
          +-------------------------------------+
    16    |          Size of OS image           |
          +-------------------------------------+
    20    |        Bitmap block number          |
          +-------------------------------------+
    24    |        Reserved for OS/16           |
          +-------------------------------------+
    28    |   Second directory block number     |
          +-------------------------------------+
    32    |     Synchronization Timestamp       |
          +-------------------------------------+

    OS/32 R8.02 Application Level Programmer - Pag 81
    https://bitsavers.org/pdf/interdata/32bit/os32/1988_8.2/48-039F00R03_OS32_R08.2_Application_Level_Programmer_Reference_Manual_1988.pdf
    """

    fs: "OS32Filesystem"
    name: str = ""  # Volume name
    attributes: int = 0  # Volume attributes
    directory_block: int = 0  # Directory block number
    os_image_block: int = 0  # OS image block number
    os_image_size: int = 0  # Size of OS image
    bitmap_block: int = 0  # Bitmap block number
    reserved: int = 0
    secondary_directory_block: int = 0  # Second directory block number
    timestamp: int = 0  # Syncronization timestamp

    def __init__(self, fs: "OS32Filesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "OS32Filesystem") -> "VolumeDescriptor":
        """
        Read the volume descriptor from disk
        """
        self = cls(fs)
        buffer = self.fs.dev.read_block(VOLUME_DESCRIPTOR_BLOCK)
        assert len(buffer) == OS32_BLOCK_SIZE
        (
            name,
            self.attributes,
            self.directory_block,
            self.os_image_block,
            self.os_image_size,
            self.bitmap_block,
            self.reserved,
            self.secondary_directory_block,
            self.timestamp,
        ) = struct.unpack_from(VOLUME_DESCRIPTOR_FORMAT, buffer, 0)
        self.name = name.decode("utf-8", errors="ignore").strip()
        return self

    @classmethod
    def create(cls, fs: "OS32Filesystem", name: str) -> "VolumeDescriptor":
        """
        Create a new volume descriptor
        """
        self = cls(fs)
        self.name = name
        self.attributes = 0
        self.directory_block = 0
        self.os_image_block = 0
        self.os_image_size = 0
        self.bitmap_block = 0
        self.reserved = 0
        self.secondary_directory_block = 0
        self.timestamp = 0
        return self

    def write(self) -> None:
        """
        Write the volume descriptor to disk
        """
        buffer = bytearray(OS32_BLOCK_SIZE)
        struct.pack_into(
            VOLUME_DESCRIPTOR_FORMAT,
            buffer,
            0,
            self.name.upper().encode("utf-8", errors="ignore").ljust(VOLUME_NAME_LENGTH, b" "),
            self.attributes,
            self.directory_block,
            self.os_image_block,
            self.os_image_size,
            self.bitmap_block,
            self.reserved,
            self.secondary_directory_block,
            self.timestamp,
        )
        self.fs.dev.write_block(bytes(buffer), VOLUME_DESCRIPTOR_BLOCK)

    def __str__(self) -> str:
        return (
            "\n*Volume Descriptor*\n"
            f"Name:                {self.name}\n"
            f"Attributes:          ${self.attributes:04x}\n"
            f"Directory block:     {self.directory_block}\n"
            f"Secondary directory: {self.secondary_directory_block}\n"
            f"Bitmap block:        {self.bitmap_block}\n"
            f"OS image block:      {self.os_image_block}\n"
            f"OS image size:       {self.os_image_size}\n"
            f"Sync timestamp:      {self.timestamp}\n"
            f"Reserved:            {self.reserved}\n"
        )


class OS32DirectorySegment(object):
    """
    Directory Segment

    Each directory segment is 256 bytes long and contains a pointer
    to the next directory segment and up to 5 directory entries.
    The directory entries contain information about the files in the volume.

          +-------------------------------------+
      0   |     Next directory block number     |
          +-------------------------------------+
      4   |          Directory entry 1          |
          +-------------------------------------+
     52   |          Directory entry 2          |
          +-------------------------------------+
    100   |          Directory entry 3          |
          +-------------------------------------+
    148   |          Directory entry 4          |
          +-------------------------------------+
    196   |          Directory entry 5          |
          +-------------------------------------+
    244   |              Reserved               |
          +-------------------------------------+

    OS/32 R8.02 Application Level Programmer - Pag 83
    https://bitsavers.org/pdf/interdata/32bit/os32/1988_8.2/48-039F00R03_OS32_R08.2_Application_Level_Programmer_Reference_Manual_1988.pdf
    """

    fs: "OS32Filesystem"
    # Block number of this directory segment
    block_number = 0
    # Next block number
    next_block_number = 0
    # Directory entries
    entries_list: t.List["OS32Path"]

    def __init__(self, fs: "OS32Filesystem"):
        self.fs = fs
        self.entries_list = []

    @classmethod
    def read(cls, fs: "OS32Filesystem", block_number: int) -> "OS32DirectorySegment":
        """
        Read a Volume Directory Block from disk
        """
        self = cls(fs)
        self.block_number = block_number
        buffer = self.fs.read_block(self.block_number)
        self.next_block_number = bytes_to_int(buffer, 0)
        for i in range(0, DIR_ENTRIES):
            dir_entry = OS32Path.read(self.fs, buffer, 4 + DIR_ENTRY_LENGTH * i)
            self.entries_list.append(dir_entry)
        return self

    @classmethod
    def create(cls, fs: "OS32Filesystem", block_number: int) -> "OS32DirectorySegment":
        """
        Create a new Volume Directory Block
        """
        self = cls(fs)
        self.block_number = block_number
        self.next_block_number = 0
        self.entries_list = [OS32Path(fs) for _ in range(DIR_ENTRIES)]
        return self

    def to_bytes(self) -> bytes:
        """
        Convert the directory segment to bytes
        """
        out = bytearray()
        out.extend(int_to_bytes(self.next_block_number))
        for entry in self.entries_list:
            out.extend(entry.to_bytes())
        # Pad the rest of the block with zeros
        out.extend(b"\0" * (OS32_BLOCK_SIZE - len(out)))
        return bytes(out)

    def write(self) -> None:
        """
        Write the directory segment to disk
        """
        self.fs.write_block(self.to_bytes(), self.block_number)

    def get_free_entry(self) -> t.Tuple["OS32DirectorySegment", int]:
        """
        Get a free directory entry in this segment or the next segment
        Returns a tuple of (segment, index) where segment is the directory segment and
        index is the index of the free entry
        """
        for i, entry in enumerate(self.entries_list):
            if not entry.is_permanent:
                return self, i
        # Next directory segment
        if self.next_block_number != 0:
            segment = OS32DirectorySegment.read(self.fs, self.next_block_number)
        else:
            bitmap = self.fs.read_bitmap()
            self.next_block_number = bitmap.allocate(1)[0]
            segment = OS32DirectorySegment.create(self.fs, self.next_block_number)
            bitmap.write()
            self.write()
        return segment.get_free_entry()


class AUFRecord:
    """
    Authorized User File Record

    The authorized user file record is a data structure used by the file
    manager to identify the authorized users of a file.

            +-----------------------------------------------+
     0      |        Account number | Group account number  |
            +-----------------------------------------------+
     4      |                                               |
            |           Password (12 characters)            |
            |                                               |
            +-----------------------------------------------+
    16      |                                               |
            |            Userid (20 characters)             |
            |                                               |
            +-----------------------------------------------+
    36      |        Signon time since last report          |
            +-----------------------------------------------+
    40      |                  Reserved                     |
            +-----------------------------------------------+
    44      |             Date of last report               |
            |                (8 characters)                 |
            +-----------------------------------------------+
    52      |             Total processor time              |
            +-----------------------------------------------+
    56      |           Signon time left (seconds)          |
            +-----------------------------------------------+
    60      |      Processor time left (milliseconds)       |
            +-----------------------------------------------+
    64      |                Privileges                     |
            +-----------------------------------------------+
    68      |                                               |
            |                 Reserved                      |
            |                                               |
            +-----------------------------------------------+
    84      |                                               |
            |                  Filler                       |
            |                                               |
            +-----------------------------------------------+

    Pag 52
    https://bitsavers.org/pdf/interdata/32bit/os32/1984_7.2/48-023F00R03_OS32_R7.02_MTM_System_Planning_and_Operator_Reference_Manual_1984.pdf
    """

    account: int
    group: int
    password: str
    name: str
    privileges: int
    date: str  # alphanumeric string
    total_processor_time: int
    signon_time_left: int  # seconds of signon time to which the account is limited
    processor_time_left: int  # processor time to which the account user is limited

    @classmethod
    def read(cls, buffer: bytes, position: int = 0) -> "AUFRecord":
        self = cls()
        tmp = struct.unpack_from(AUF_ENTRY_FORMAT, buffer, position)
        self.account = tmp[0]
        self.group = tmp[1]
        self.password = tmp[2].decode("utf-8", errors="ignore").strip("\0 ")
        self.name = tmp[3].decode("utf-8", errors="ignore").strip("\0 ")
        self.date = tmp[6].decode("utf-8", errors="ignore").strip("\0 ")
        self.total_processor_time = tmp[7]
        self.signon_time_left = tmp[8]
        self.processor_time_left = tmp[9]
        self.privileges = tmp[10]
        return self

    @property
    def is_valid(self) -> bool:
        """
        Check if the record is valid
        """
        return self.name != ""

    @property
    def time_left(self) -> str:
        """
        Get the signon time left in HH:MM:SS format
        """
        if self.signon_time_left == 0:
            return "   **"
        else:
            return str(timedelta(seconds=self.signon_time_left))

    @property
    def cpu_left(self) -> str:
        """
        Get the CPU time left in HH:MM:SS format
        """
        if self.processor_time_left == 0:
            return "   **"
        else:
            return str(timedelta(seconds=self.processor_time_left))

    def __str__(self) -> str:
        return f"{self.account:>5} {self.group:>5} {self.name:<24} {self.time_left:<12} {self.cpu_left:<9} {self.privileges:08X}  {self.date}"


class OS32RootDirectory(XFERXPath):
    fs: "OS32Filesystem"

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

    def read_dir_segments(self) -> t.Iterator["OS32DirectorySegment"]:
        """
        Read directory segments
        """
        next_block_number = self.fs.directory_block
        while next_block_number != 0:
            segment = OS32DirectorySegment.read(self.fs, next_block_number)
            next_block_number = segment.next_block_number
            yield segment

    def read_entries_list(self) -> t.Iterator["OS32Path"]:
        """
        Read the directory entries from the segments
        """
        for segment in self.read_dir_segments():
            yield from segment.entries_list

    def read_accounts(self) -> t.List[int]:
        """
        Read the accounts in the filesystem
        """
        accounts = set()
        for entry in self.read_entries_list():
            if entry.is_permanent:
                accounts.add(entry.account)
        return sorted(accounts)

    def iterdir(self) -> t.Iterator["OS32AccountDirectory"]:
        """
        Iterate over the account directories in the filesystem
        """
        for account in self.read_accounts():
            yield OS32AccountDirectory(self.fs, account)


class OS32Filesystem(AbstractBlockFilesystem):
    """
    OS/32 Filesystem
    ================

    OS/32 filesystem main structures are:

    - The volume descriptor is a data structure used by the file
      manager to identify the disk volume and to point to structures
      that contain the file and disk space information.
      The first sector of the disk contains the volume descriptor.

    - The bit map, is used to indicate which sectors are in use or defective.

    - The file directory keeps track of all files on the disk.

    OS/32 R7.02 Application Level Programmer - Pag 68
    https://bitsavers.org/pdf/interdata/32bit/os32/1984_7.2/48-039F00R02_OS32_R7.02_Application_Level_Programmer_1984.pdf

    OS/32 R8.02 Application Level Programmer - Pag 76
    https://bitsavers.org/pdf/interdata/32bit/os32/1988_8.2/48-039F00R03_OS32_R08.2_Application_Level_Programmer_Reference_Manual_1988.pdf

    OS/16 MT2 Programmer's Reference Manual - Pag 94
    https://bitsavers.org/pdf/interdata/16bit/os16/29-429R06_OS16MT2_PgmRef_Sep79.pdf


    Contiguous file
    ---------------

    Contiguous files are organized sequentially on disk.
    The maximum length of a contiguous file is fixed when the file is created.
    Records within a contiguous file are 256 bytes (one sector) in length.

    block size = 256 (bytes)
    file length = last_block - first_block + 1 (blocks)

    OS/32 R8.02 Application Level Programmer - Pag 77
    https://bitsavers.org/pdf/interdata/32bit/os32/1988_8.2/48-039F00R03_OS32_R08.2_Application_Level_Programmer_Reference_Manual_1988.pdf

    Indexed Files
    -------------

    The record size is specified by the user when the file is allocated.
    Records are stored in data blocks. Each data block consists of one or more 256-byte contiguous sectors.

    index block size = index_block_size * 256 (bytes)
    block size = block_size * 256 (bytes)

    OS/32 R8.02 Application Level Programmer - Pag 78
    https://bitsavers.org/pdf/interdata/32bit/os32/1988_8.2/48-039F00R03_OS32_R08.2_Application_Level_Programmer_Reference_Manual_1988.pdf

    """

    fs_name = "os32"
    fs_description = "Interdata OS/32 Filesystem"
    fs_platforms = ["interdata"]
    fs_entry_metadata = [
        "account",
        "creation_date",
        "last_mod_date",
        "file_type",
        "index_block_size",
        "block_size",
        "record_length",
        "num_of_records",
        "keys",
    ]

    account: t.Optional[int] = None  # Account number
    volume_name: str = ""  # Volume name
    directory_block: int = 0  # Directory block number
    bitmap_block: int = 0  # Bitmap block number

    def __init__(self, file_or_device: t.Union["AbstractFile", "AbstractDevice"]):
        if isinstance(file_or_device, AbstractFile):
            self.dev = BlockDevice(file_or_device, sector_size=OS32_BLOCK_SIZE)
            if self.dev.sector_size != OS32_BLOCK_SIZE:
                raise OSError(errno.EIO, "Block device sector size must be 256 bytes")
        elif isinstance(file_or_device, BlockDevice):
            self.dev = file_or_device
            if self.dev.sector_size != OS32_BLOCK_SIZE:
                raise OSError(errno.EIO, "Block device sector size must be 256 bytes")
        else:
            raise OSError(errno.EIO, "Not a valid block device")

    def read_32bit_words_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> t.List[int]:
        """
        Read blocks as 32bit words
        """
        buffer = self.read_block(block_number, number_of_blocks)
        if len(buffer) < OS32_BLOCK_SIZE:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        fmt = f">{(number_of_blocks * WORDS_PER_BLOCK)}I"
        return list(struct.unpack(fmt, buffer))

    def write_32bit_words_block(
        self,
        words: t.List[int],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write blocks as 32bit words
        """
        if len(words) < number_of_blocks * WORDS_PER_BLOCK:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        fmt = f">{(number_of_blocks * WORDS_PER_BLOCK)}I"
        buffer = struct.pack(fmt, *words)
        self.write_block(buffer, block_number, number_of_blocks)

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        strict: t.Union[bool, str] = True,
        **kwargs: t.Union[bool, str],
    ) -> "OS32Filesystem":
        self = cls(file_or_dev)
        # Read the Volume Descriptor
        volume_descriptor = VolumeDescriptor.read(self)
        self.volume_name = volume_descriptor.name
        self.directory_block = volume_descriptor.directory_block
        self.bitmap_block = volume_descriptor.bitmap_block
        if strict:
            # Check that all blocks are allocated in the bitmap
            bitmap = self.read_bitmap()
            for block in bitmap.blocks:
                assert not bitmap.is_free(block)
        return self

    def read_bitmap(self) -> OS32Bitmap:
        """
        Read the bitmap
        """
        return OS32Bitmap.read(self)

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
        account: t.Optional[int] = None,
    ) -> t.Iterator[t.Union["OS32Path", "OS32AccountDirectory"]]:
        if account is None:
            account = self.account
        account, filename_pattern = os32_split_fullname(fullname=pattern, wildcard=wildcard, account=account)
        if account is None:
            yield from self.get_root().iterdir()
        else:
            for entry in self.get_root().read_entries_list():
                if (
                    entry.is_permanent
                    and filename_match(entry.name, filename_pattern, wildcard)
                    and account_match(entry.account, account)
                ):
                    yield entry

    @property
    def entries_list(self) -> t.Iterator[t.Union["OS32Path", "OS32AccountDirectory"]]:
        if self.account is None:
            yield from self.get_root().iterdir()
        else:
            for entry in self.get_root().read_entries_list():
                if entry.is_permanent:
                    yield entry

    def get_file_entry(self, fullname: str) -> t.Union["OS32Path", "OS32AccountDirectory"]:
        """
        Get the directory entry for a file
        """
        account, stem = os32_split_fullname(fullname=fullname, wildcard=False, account=self.account)
        if account is None:
            raise FileNotFoundError(errno.ENOENT, "No account number specified")
        if not stem:
            return OS32AccountDirectory(self, account)
        for entry in self.get_root().read_entries_list():
            if entry.is_permanent and entry.name == stem and entry.account == account:
                return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def get_root(self) -> OS32RootDirectory:
        """
        Get the root directory
        """
        return OS32RootDirectory(self)

    def get_free_directory_entry(self) -> t.Tuple[OS32DirectorySegment, int]:
        """
        Get a free directory entry in the filesystem
        Returns a tuple of (segment, index) where segment is the directory segment and
        index is the index of the free entry
        """
        segment = next(self.get_root().read_dir_segments())
        return segment.get_free_entry()

    def write_bytes(
        self,
        fullname: str,
        content: t.Union[bytes, bytearray],
        fork: t.Optional[str] = None,
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        """
        Write content to a file
        """
        metadata = metadata or {}
        file_type = os32_get_file_type_id(metadata.get("file_type"))  # type: ignore
        creation_date: t.Optional[date] = metadata.get("creation_date")  # type: ignore
        if file_mode == ASCII:
            record_length: int = metadata.get("record_length") or DEFAULT_RECORD_LENGTH  # type: ignore
            content, num_of_records = ascii_to_records(content, record_length=record_length)
            metadata["num_of_records"] = num_of_records
        # Create the file
        entry = self.create_file(
            fullname=fullname,
            size=len(content),
            metadata=metadata,
        )
        # Write the content to the file
        with entry.open(file_mode) as f:
            f.write(content)

    def create_file(
        self,
        fullname: str,
        size: int,  # Size in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> OS32Path:
        """
        Create a new file with a given length and file type
        """
        metadata = metadata or {}
        file_type = os32_get_file_type_id(metadata.get("file_type"))  # type: ignore
        creation_date: t.Optional[date] = metadata.get("creation_date")  # type: ignore
        keys: int = metadata.get("keys") or 0  # type: ignore
        num_of_records: int = metadata.get("num_of_records") or 0  # type: ignore

        # Delete the file if it already exists
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass

        bitmap = self.read_bitmap()
        if file_type == CO_FILE_TYPE:
            # Contiguous file, allocate contiguous blocks
            num_of_blocks = math.ceil(size / OS32_BLOCK_SIZE)
            if num_of_blocks == 0:
                num_of_blocks = 1
            blocks = bitmap.allocate(num_of_blocks, contiguous=True)
            first_block = blocks[0]
            last_block = blocks[-1]
            block_size = 1
            index_block_size = 0
            record_length = 0
        elif file_type == IN_FILE_TYPE:
            # Indexed file
            block_size = metadata.get("block_size") or DEFAULT_BLOCK_SIZE  # type: ignore
            index_block_size = metadata.get("index_block_size") or DEFAULT_INDEX_BLOCK_SIZE  # type: ignore
            record_length = metadata.get("record_length") or DEFAULT_RECORD_LENGTH  # type: ignore
            num_of_blocks = math.ceil(size / (OS32_BLOCK_SIZE * block_size))
            block_per_index_block = (WORDS_PER_BLOCK * index_block_size) - 2  # Number of data blocks per index block
            num_of_index_blocks = math.ceil(num_of_blocks / block_per_index_block)
            # Allocate index block
            index_blocks = []
            last_block = 0
            for _ in range(num_of_index_blocks):
                blocks = bitmap.allocate(index_block_size, contiguous=True, start_block=last_block)
                index_blocks.append(blocks[0])
                last_block = blocks[-1]
            # Allocate data blocks
            data_blocks = []
            last_block = 0
            for _ in range(num_of_blocks):
                blocks = bitmap.allocate(block_size, contiguous=True, start_block=last_block)
                data_blocks.append(blocks[0])
                last_block = blocks[-1]
            # Write the index block with the data block addresses
            for i, index_block in enumerate(index_blocks):
                words = [
                    index_blocks[i - 1] if i > 0 else 0,  # Previous index block
                    index_blocks[i + 1] if i < len(index_blocks) - 1 else 0,  # Next index block
                ]
                start = i * block_per_index_block
                end = start + block_per_index_block
                words.extend(data_blocks[start:end])
                # Pad the rest of the index block with zeros
                words.extend([0] * (WORDS_PER_BLOCK * index_block_size - len(words)))
                self.write_32bit_words_block(words, index_block, index_block_size)
            first_block = index_blocks[0]
            last_block = index_blocks[-1]
        else:
            raise NotImplementedError(f"File type {os32_file_type_description(file_type)} not supported")
        bitmap.write()

        # Create the directory entry
        directory_segment, index = self.get_free_directory_entry()
        entry = OS32Path.create(
            fs=self,
            fullname=fullname,
            size=size,
            record_length=record_length,
            creation_date=creation_date,
            file_type=file_type,
            first_block=first_block,
            last_block=last_block,
            block_size=block_size,
            index_block_size=index_block_size,
            num_of_records=num_of_records,
            keys=keys,
        )
        directory_segment.entries_list[index] = entry
        directory_segment.write()
        return entry

    @classmethod
    def initialize(
        cls, file_or_dev: t.Union["AbstractFile", "AbstractDevice"], **kwargs: t.Union[bool, str]
    ) -> "OS32Filesystem":
        """
        Initialized an empty OS/32 filesystem
        """
        self = cls(file_or_dev)
        self.account = None
        try:
            self.volume_name = kwargs["name"].strip().upper() or DEFAULT_VOLUME_NAME  # type: ignore
        except Exception:
            self.volume_name = DEFAULT_VOLUME_NAME
        self.bitmap_block = 1

        # Create the bitmap
        bitmap = OS32Bitmap.create(self)
        bitmap.set_used(0)  # Mark the Volume descriptor as used
        for block in bitmap.blocks:
            bitmap.set_used(block)  # Mark all blocks as used
            assert not bitmap.is_free(block)
        self.directory_block = bitmap.allocate(1)[0]
        bitmap.write()

        # Create the directory
        directory_segment = OS32DirectorySegment.create(self, self.directory_block)
        directory_segment.write()

        # Create the Master File Directory Blocks
        volume_descriptor = VolumeDescriptor.create(self, self.volume_name)
        volume_descriptor.bitmap_block = self.bitmap_block
        volume_descriptor.directory_block = self.directory_block
        volume_descriptor.write()
        return self

    def read_authorized_user_file(self) -> t.Dict[int, AUFRecord]:
        """
        OS/32 MULTI-TERMINAL MONITOR (MTM) SYSTEM PLANNING AND OPERATOR, Pag 52
        """
        result: t.Dict[int, AUFRecord] = {}
        try:
            f = self.open_file(AUF_FILE)
            while True:
                buf = f.read(AUF_ENTRY_LENGTH)
                if not buf:
                    break
                record = AUFRecord.read(buf)
                if record.is_valid:
                    result[record.account] = record
        except OSError:
            pass
        return result

    def show_accounts(self, volume_id: str, options: t.Dict[str, bool]) -> None:
        """
        List the accounts in the authorized user file and on the filesystem

        OS/32 MULTI-TERMINAL MONITOR (MTM) SYSTEM PLANNING AND OPERATOR, Pag 66
        https://bitsavers.org/pdf/interdata/32bit/os32/1984_7.2/48-023F00R03_OS32_R7.02_MTM_System_Planning_and_Operator_Reference_Manual_1984.pdf
        """
        accounts = self.get_root().read_accounts()
        auf_accounts = self.read_authorized_user_file()
        sys.stdout.write(f"  ACT   GRP          NAME           TIME LEFT    CPU LEFT       PRIV      DATE\n")
        for account in sorted(accounts):
            if account in auf_accounts:
                sys.stdout.write(f"{auf_accounts[account]}\n")
            else:
                sys.stdout.write(f"{account:>5}\n")

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List the files in the filesystem

        OS/32 Operator Reference Manual - Pag 134
        https://bitsavers.org/pdf/interdata/32bit/os32/1986_8.1.2/48-030F01R03_OS32_R08.1.2_Operators_Reference_Manual_1986.pdf
        """
        account, _ = os32_split_fullname(fullname=pattern, wildcard=True, account=self.account)
        if account is None:
            self.show_accounts(volume_id, options)
            return
        sys.stdout.write(f"VOLUME= {self.volume_name}\n")
        sys.stdout.write("FILENAME......     TY DBS/IBS RECL. RECORDS CREATED....... LAST WRITTEN.. KEYS\n")
        for x in self.filter_entries_list(pattern, include_all=False, wildcard=True):
            if not isinstance(x, OS32Path):
                continue
            # Print file information
            creation_date = format_time(x.creation_date)
            last_mod_date = format_time(x.last_mod_date)
            if x.is_contiguous:
                # RECORDS - the size of the file in sectors
                dbs_ibs_recl = ""
                records = x.last_block - x.first_block + 1
            elif x.is_indexed or x.is_nonbuffered_indexed:
                # DBS     - the data block size (in sectors)
                # IBS     - the index block size (in sectors)
                # RECL    - the record length in bytes
                # RECORDS - the number of records in the file
                dbs_ibs_recl = f"{x.block_size:>3}/{x.index_block_size:<3} {x.record_length:>5}"
                records = x.num_of_records
            elif x.is_extended_contiguous:
                # RECORDS - the size of the file in sectors
                dbs_ibs_recl = f"{x.block_size:>3}/{x.index_block_size:<3}"
                records = x.num_of_records
            else:  # long record
                # RECORDS - the size of the file in sector
                dbs_ibs_recl = ""  # TODO
                records = x.num_of_records

            sys.stdout.write(
                f" {x.stem:<8}.{x.extension:<3}/{x.account:05} {x.file_type} "
                f"{dbs_ibs_recl:13} "
                f"{records:>7} "
                f"{creation_date} "
                f"{last_mod_date} "
                f"{x.write_key:02X}{x.read_key:02X}\n"
            )
        sys.stdout.write("\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if options.get("diskid"):
            # Display the filesystem information
            volume_descriptor = VolumeDescriptor.read(self)
            sys.stdout.write(str(volume_descriptor))
        elif options.get("free"):
            # Display the free space
            bitmap = self.read_bitmap()
            sys.stdout.write(f"{bitmap}\n")
        elif options.get("bitmap"):
            # Display the bitmap
            bitmap = self.read_bitmap()
            for i in range(0, bitmap.total_bits):
                sys.stdout.write(f"{i:>4} {'[ ]' if bitmap.is_free(i) else '[X]'}  ")
                if i % 16 == 15:
                    sys.stdout.write("\n")
        elif not arg:
            # Display the system directory
            for entry in self.get_root().read_entries_list():
                if options.get("full") or entry.is_permanent:
                    sys.stdout.write(f"{entry}\n")
        else:
            # Display the file information
            entry = self.get_file_entry(arg)  # type: ignore
            sys.stdout.write(entry.examine())

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        """
        Change the current account number
        """
        if entry is None:
            self.account = None
        else:
            assert isinstance(entry, OS32AccountDirectory)
            self.account = entry.account

    def get_pwd(self) -> str:
        """
        Get the current account number
        """
        return str(self.account) if self.account is not None else "/"

    def get_types(self) -> t.List[str]:
        """
        Get the list of the supported file types
        """
        return [x[0] for x in FILE_TYPES.values()]
