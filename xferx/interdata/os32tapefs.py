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
import os
import struct
import sys
import typing as t
from datetime import datetime, timedelta

from ..abstract import XFERXPath, AbstractFile, AbstractFilesystem
from ..commons import ASCII, READ_FILE_FULL, filename_match, hex_dump
from ..device.abstract import AbstractDevice
from ..device.tape import Tape
from .os32fs import (
    account_match,
    ascii_to_records,
    date_to_os32,
    format_time,
    os32_file_type_description,
    os32_get_file_type_id,
    os32_split_fullname,
    os32_to_date,
    records_to_ascii,
    ANY_ACCOUNT,
    FILE_TYPES,
    CO_FILE_TYPE,
    EC_FILE_TYPE,
    IN_FILE_TYPE,
    NB_FILE_TYPE,
    LR_FILE_TYPE,
    IT_FILE_TYPE,
    OS32_BLOCK_SIZE,
    DEFAULT_RECORD_LENGTH,
    DEFAULT_BLOCK_SIZE,
    DEFAULT_INDEX_BLOCK_SIZE,
)

__all__ = [
    "OS32TapeFile",
    "OS32TapeFilesystem",
]

VOLUME_HEADER_FORMAT = ">4s I HH 2BH I I 4s8s3sB"
VOLUME_HEADER_SIZE = 80
FILE_INFORMATION_BLOCK_FORMAT = ">HH BBH HH 8s 3sb HH I I I 16s I HH I"
FILE_INFORMATION_BLOCK_SIZE = 80
DEFAULT_BUFFER_SIZE = 12288
DEFAULT_VOLUME_NAME = "OS32"


def os32_header_to_date(n: int) -> t.Optional[datetime]:
    """
    Convert an OS/32 header date format (used in the volume header) to a datetime
    The header date format is different from the one used in the File Information Block
    """
    try:
        t = n >> 16
        year = 1900 + (t >> 9)
        if year < 1970:
            year += 100
        month = t // 32 % 16
        day = t % 32
        t = n & 0xFFFF
        h = t // 60
        m = t % 60
        return datetime(year, month, day, h, m)
    except Exception:
        return None


def date_to_os32_header(dt: t.Optional[datetime]) -> int:
    """
    Convert a datetime object to the OS/32 header date format (used in the volume header).
    """
    if dt is None:
        return 0
    year = dt.year - 1900
    if year >= 100:
        year -= 100
    month = dt.month
    day = dt.day
    h = dt.hour
    m = dt.minute
    return ((year << 9) | (month << 5) | day) << 16 | (h * 60 + m)


class OS32TapeFile(AbstractFile):
    entry: "FileInformationBlock"
    size: int  # size in bytes
    number_of_blocks: int  # number of 256 bytes blocks
    content: bytes  # file content

    def __init__(self, entry: "FileInformationBlock"):
        self.entry = entry
        entry.fs.dev.tape_seek(entry.tape_pos)
        entry.fs.dev.tape_read_forward()  # skip the header
        self.content = entry.fs.dev.tape_read_file()
        self.size = len(self.content)
        self.number_of_blocks = math.ceil(self.size / OS32_BLOCK_SIZE)

    @property
    def name(self) -> str:
        """Final path component"""
        return self.entry.name

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = self.number_of_blocks
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.number_of_blocks
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return self.content[block_number * OS32_BLOCK_SIZE : (block_number + number_of_blocks) * OS32_BLOCK_SIZE]

    def write_block(
        self,
        buffer: t.Union[bytes, bytearray],
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
        Get file block size in bytes (always 256 bytes)
        """
        return OS32_BLOCK_SIZE

    def __str__(self) -> str:
        return self.entry.fullname


class FileInformationBlock(XFERXPath):
    """
    File Information Block (FIB) - 80 bytes

    Guessed layout based on some sample tapes.

        +-------------------------------------+------------------+------------------+
     0  |             Attributes              |               4 ???                 |
        +-------------------------------------+------------------+------------------+
     4  |    Write key     |    Read key      |            Record length            |
        +-------------------------------------+------------------+------------------+
     8  |               0 ???                 |    0 (CO) / Number of records ???   |
        +---------------------------------------------------------------------------+
    12  |                                Filename                                   |
        |                                                                           |
        +--------------------------------------------------------+------------------+
    20  |              Extension                                 |  Account (low)   |
        +-------------------------------------+------------------+------------------+
    24  | Num of records H (CO) / Block size  | Num of record L / Index block size  |
        +-------------------------------------+-------------------------------------+
    28  |                             Date file allocated                           |
        +---------------------------------------------------------------------------+
    32  |                            Date file last written                         |
        +---------------------------------------------------------------------------+
    36  |                               Number of records                           |
        +---------------------------------------------------------------------------+
    40  |                                                                           |
        |                             16 x 0 or 0x20 ???                            |
        |                                                                           |
        |                                                                           |
        +---------------------------------------------------------------------------+
    56  |                                    ???                                    |
        +-------------------------------------+-------------------------------------+
    60  |               0 ???                 |             0x4000 ???              |
        +-------------------------------------+-------------------------------------+
    64  |                          Date file last assigned                          |
        +---------------------------------------------------------------------------+
    68  |                                                                           |
        /                                    ...                                    /
    76  |                                                                           |
        +---------------------------------------------------------------------------+

    """

    fs: "OS32TapeFilesystem"
    attributes: int = 0  # File attributes
    _u1: int = 0x4  # Unknown 1 ( 16bit, always 4 ?)
    write_key: int = 0
    read_key: int = 0
    record_length: int = 0  # Record length, in bytes
    _u8: int = 0  # Unknown 2  ( 16 bit, always 0 ?)
    _u10: int = 0  # Unknown 3  ( 16 bit, 0 for contiguous files, number of records for indexed files )
    _u40: bytes = b"\0" * 16  # Unknown 4  ( 16 chars - always 0x0 or 0x20 )
    _u56: int = 0x0902  # Unknown 8 ( 32 bit - 0x300, 0x302, 0x902 ? )
    _u60: int = 0x1300  # Unknown 9 ( 16 bit - always 0 ?)
    _u62: int = 0x4000  # Unknown 10  ( 16 bit - always 0x4000, in all the examples I have seen)
    num_of_records: int = 0  # Number of disk records
    stem: str = ""  # File name, up to 8 characters
    extension: str = ""  # File extension, up to 3 characters
    account: int = 0  # Account number, 0-65535
    index_block_size: int = 0  # Size of the index block, in number of 256 bytes sectors
    block_size: int = 0  # Block size, in number of 256 bytes sectors
    raw_creation_date: int = 0  # Creation date and time
    raw_last_mod_date: int = 0  # Last modification date and time
    raw_last_assigned_date: int = 0
    tape_pos: int = 0  # tape position (before file header)
    tape_size: int = 0  # tape size (in bytes)
    raw_buffer: bytes = b""  # raw FIB buffer, preserved for write-back

    def __init__(self, fs: "OS32TapeFilesystem", tape_pos: int, tape_size: int):
        self.fs = fs
        self.tape_pos = tape_pos
        self.tape_size = tape_size

    @classmethod
    def read(
        cls,
        fs: "OS32TapeFilesystem",
        buffer: bytes,
        tape_pos: int,
        tape_size: int,
    ) -> "FileInformationBlock":
        self = FileInformationBlock(fs, tape_pos, tape_size)
        self.raw_buffer = bytes(buffer)  # preserve original bytes for write-back
        (
            self.attributes,
            self._u1,
            self.write_key,
            self.read_key,
            self.record_length,
            self._u8,
            self._u10,
            stem,
            extension,
            self.account,
            self.index_block_size,
            self.block_size,
            self.raw_creation_date,
            self.raw_last_mod_date,
            self.num_of_records,
            self._u40,
            self._u56,
            self._u60,
            self._u62,
            self.raw_last_assigned_date,
        ) = struct.unpack_from(FILE_INFORMATION_BLOCK_FORMAT, buffer, 0)
        self.stem = stem.decode("ascii", errors="ignore").rstrip("\0 ")
        self.extension = extension.decode("ascii", errors="ignore").rstrip("\0 ")
        if self.is_contiguous:
            self.num_of_records = (self.index_block_size << 16) | self.block_size
            self.index_block_size = 0
            self.block_size = 0
        return self

    @classmethod
    def create(
        cls,
        fs: "OS32TapeFilesystem",
        stem: str,
        extension: str,
        account: int,
        file_type: int = CO_FILE_TYPE,
        record_length: int = 0,
        block_size: int = 0,
        index_block_size: int = 0,
        num_of_records: int = 0,
        keys: int = 0,
        creation_date: t.Optional[datetime] = None,
        tape_size: int = 0,
    ) -> "FileInformationBlock":
        """
        Create a new FileInformationBlock.
        """
        now = datetime.now()
        self = cls(fs, tape_pos=0, tape_size=tape_size)
        self.attributes = 0xC0A0 | file_type & 7
        self._u1 = 0x4
        self._u62 = 0x4000
        self.write_key = (keys >> 8) & 0xFF
        self.read_key = keys & 0xFF
        self.record_length = record_length
        self.num_of_records = num_of_records
        self.stem = stem.upper()[:8]
        self.extension = extension.upper()[:3]
        self.account = account
        if self.is_contiguous:
            self.index_block_size = 0
            self.block_size = 0
        else:
            self.index_block_size = index_block_size
            self.block_size = block_size
        self.raw_creation_date = date_to_os32(creation_date or now)
        self.raw_last_mod_date = date_to_os32(now)
        self.raw_last_assigned_date = self.raw_last_mod_date
        self.raw_buffer = b""  # force reconstruction via to_bytes()
        return self

    def to_bytes(self) -> bytes:
        """
        Serialize the FIB back to a byte buffer (80 bytes).
        Uses the original raw buffer when available to preserve unknown fields.
        """
        if self.raw_buffer:
            return self.raw_buffer
        # Construct FIB from scratch (for newly created entries)
        buffer = bytearray(FILE_INFORMATION_BLOCK_SIZE)
        filename_bytes = self.stem.upper().encode("ascii").ljust(8, b" ")[:8]
        extension_bytes = self.extension.upper().encode("ascii").ljust(3, b" ")[:3]
        # For CO (contiguous) files the number of records is stored as
        # (index_block_size << 16) | block_size
        # index_block_size field; num_of_records field is left as 0.
        if self.is_contiguous:
            num_records_field = 0
            index_block_size_field = self.num_of_records >> 16
            block_size_field = self.num_of_records & 0xFFFF
        else:
            num_records_field = self.num_of_records
            index_block_size_field = self.index_block_size
            block_size_field = self.block_size
        struct.pack_into(
            FILE_INFORMATION_BLOCK_FORMAT,
            buffer,
            0,
            self.attributes,
            self._u1,  # unknown
            self.write_key,
            self.read_key,
            self.record_length,
            self._u8,  # unknown
            num_records_field,
            filename_bytes,
            extension_bytes,
            self.account,
            index_block_size_field,
            block_size_field,
            self.raw_creation_date,
            self.raw_last_mod_date,
            self.num_of_records,
            self._u40,  # unknown
            self._u56,  # unknown
            self._u60,  # unknown
            self._u62,  # unknown
            self.raw_last_assigned_date,
        )
        return bytes(buffer)

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
    def parent(self) -> "OS32TapeRootDirectory":
        """The logical parent"""
        return self.fs.get_root()

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

    def is_dir(self) -> bool:
        return False

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
        return self.attributes & 7

    @property
    def file_type(self) -> t.Optional[str]:
        """
        File type
        """
        try:
            return FILE_TYPES[self.raw_file_type][0]
        except KeyError:
            return "??"

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in 256 bytes blocks
        """
        if self.is_contiguous:
            return self.num_of_records
        else:
            return self.num_of_records * self.record_length // OS32_BLOCK_SIZE

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get file size in bytes
        """
        if self.is_contiguous:
            return self.num_of_records * OS32_BLOCK_SIZE
        else:
            return self.num_of_records * self.record_length

    def get_block_size(self) -> int:
        """
        Get file block size in bytes (always 256 bytes)
        """
        return OS32_BLOCK_SIZE

    def delete(self) -> bool:
        """
        Delete the file by rewriting the entire tape without this entry.
        """
        entries = [
            (entry, data)
            for entry, data in self.fs.read_files_data()
            if not (filename_match(entry.name, self.name, False) and account_match(entry.account, self.account))
        ]
        self.fs._rewrite_tape(entries)
        return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> OS32TapeFile:
        """
        Open a file
        """
        return OS32TapeFile(self)

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
        buf.write(f"Attributes:              ${self.attributes:x}\n")
        if not self.is_contiguous:
            buf.write(f"Block size:              {self.block_size} sectors\n")
            buf.write(f"Index block size:        {self.index_block_size} sectors\n")
        if self.is_indexed or self.is_nonbuffered_indexed:
            buf.write(f"Number of records:       {self.num_of_records}\n")
        else:
            buf.write(f"Number of sectors:       {self.num_of_records}\n")
        buf.write(f"Size:                    {self.get_size()} bytes\n")
        buf.write(f"Tape size:               {self.tape_size} bytes\n")
        buf.write(
            f"Unknown:                 ${self._u1:x} ${self._u8:x} ${self._u10:x} ${self._u40.hex()} ${self._u56:x} ${self._u60:x} ${self._u62:x}\n"
        )
        buf.write(str(self.metadata) + "\n")
        return buf.getvalue()

    def __str__(self) -> str:
        creation_date = format_time(self.creation_date)
        last_mod_date = format_time(self.last_mod_date)
        if self.is_contiguous:
            return (
                f"{self.stem:<8s}.{self.extension:<3s}/{self.account:05d} {self.file_type:>2s} "
                f"               "
                f"{self.num_of_records:>7} {creation_date} {last_mod_date} "
                f"{self.write_key:02X}{self.read_key:02X}"
            )
        else:
            return (
                f"{self.stem:<8s}.{self.extension:<3s}/{self.account:05d} {self.file_type:>2s} "
                f"{self.block_size:>4}/{self.index_block_size:<3} {self.record_length:>5} "
                f"{self.num_of_records:>7} {creation_date} {last_mod_date} "
                f"{self.write_key:02X}{self.read_key:02X}"
            )

    def __repr__(self) -> str:
        return str(self)


class VolumeHeader:
    """
    Volume header - 80 bytes

    Guessed layout based on some sample tapes.

        +---------------------------------------------------------------------------+
     0  |                                Volume name                                |
        +---------------------------------------------------------------------------+
     4  |                                    ???                                    |
        +-------------------------------------+------------------+------------------+
     8  |                ???                  |             Buffer size             |
        +-------------------------------------+------------------+------------------+
    12  | Backup revision  | Backup update    |                 ???                 |
        +---------------------------------------------------------------------------+
    16  |                               Creation date                               |
        +---------------------------------------------------------------------------+
    20  |                                    ???                                    |
        +---------------------------------------------------------------------------+
    24  |                            Selected file volume                           |
        +---------------------------------------------------------------------------+
    28  |                             Selected Filename                             |
        |                                                                           |
        +--------------------------------------------------------+------------------+
    36  |           Selected Extension                           | Selected Account |
        +-------------------------------------+------------------+------------------+

    """

    name: str = ""  # Volume name
    _u4: int = 0  # Unknown 1
    _u8: int = 0  # Unknown 2
    _u14: int = 0  # Unknown 3
    _u20: int = 0  # Unknown 4
    revision: int = 0  # Backup revision number
    update: int = 0  # Backup update number
    creation_date: t.Optional[datetime] = None  # Creation date
    buffer_size: int = 0  # Size of the buffer used to transfer data
    select_vol: str = ""  # Select volume name
    select_filename: str = ""  # Select file name
    select_extension: str = ""  # Select file extension
    select_account: int = 0  # Select account number

    @classmethod
    def read(cls, fs: "OS32TapeFilesystem") -> "VolumeHeader":
        """
        Read the volume header from the tape
        """
        fs.dev.tape_rewind()
        buffer = fs.dev.tape_read_forward()
        if len(buffer) != VOLUME_HEADER_SIZE:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        self = VolumeHeader()
        (
            name,
            self._u4,
            self._u8,
            self.buffer_size,
            self.revision,
            self.update,
            self._u14,
            creation_date,
            self._u20,
            select_vol,
            select_filename,
            select_extension,
            self.select_account,
        ) = struct.unpack_from(VOLUME_HEADER_FORMAT, buffer, 0)
        self.name = name.decode("ascii", errors="ignore").rstrip("\0 ")
        self.select_vol = select_vol.decode("ascii", errors="ignore").rstrip("\0 ")
        self.select_filename = select_filename.decode("ascii", errors="ignore").rstrip("\0 ")
        self.select_extension = select_extension.decode("ascii", errors="ignore").rstrip("\0 ")
        # The date format is different from the one used in the File Information Block
        self.creation_date = os32_header_to_date(creation_date)
        return self

    @classmethod
    def create(cls, fs: "OS32TapeFilesystem", name: str) -> "VolumeHeader":
        """
        Create a new volume header
        """
        self = VolumeHeader()
        self.name = name.upper()[:4]
        self._u4 = 0x10000
        self._u8 = 0
        self.buffer_size = fs.buffer_size or DEFAULT_BUFFER_SIZE
        self.revision = 9
        self.update = 2
        self._u14 = 0x2000
        self.creation_date = datetime.now()
        self._u20 = 0
        self.select_vol = ""
        self.select_filename = ""
        self.select_extension = ""
        self.select_account = 0
        return self

    def _prepare_select(self, value: t.Optional[str], length: int) -> bytes:
        """
        Prepare a select field for serialization
        """
        if not value:
            return b"\0" * length
        else:
            return value.encode("ascii").ljust(length, b" ")[:length]

    def to_bytes(self) -> bytes:
        """
        Serialize the volume header to a byte buffer (80 bytes)
        """
        buffer = bytearray(VOLUME_HEADER_SIZE)
        struct.pack_into(
            VOLUME_HEADER_FORMAT,
            buffer,
            0,
            self.name.encode("ascii").ljust(4, b" ")[:4],
            self._u4,
            self._u8,
            self.buffer_size,
            self.revision,
            self.update,
            self._u14,
            date_to_os32_header(self.creation_date),
            self._u20,
            self._prepare_select(self.select_vol, 4),
            self._prepare_select(self.select_filename, 8),
            self._prepare_select(self.select_extension, 3),
            self.select_account,
        )
        return bytes(buffer)

    @property
    def rev(self) -> str:
        """
        Backup revision
        """
        return f"{self.revision:02}-{self.update:02}"

    def __str__(self) -> str:
        creation_date = format_time(self.creation_date)
        if self.select_vol:
            select = (
                f"\nSelect:  {self.select_vol}:{self.select_filename}.{self.select_extension}/{self.select_account}"
            )
        else:
            select = ""
        return (
            f"Volume: {self.name:>4}:   "
            f"Date Created: {creation_date}   "
            f"Backup Rev.: {self.revision:02}-{self.update:02}    "
            f"Size: {self.buffer_size / 1024:>6.2f}K\n"
            f"Unknown: ${self._u4:08X} ${self._u8:04X} ${self._u14:04X} ${self._u20:08X}"
            f"{select}"
        )


class OS32TapeRootDirectory(XFERXPath):
    fs: "OS32TapeFilesystem"

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
    def parent(self) -> "OS32TapeRootDirectory":
        """The logical parent"""
        return self

    def iterdir(self) -> t.Iterator["FileInformationBlock"]:
        """
        Read File Information Blocks (FIBs) from the tape
        """
        self.fs.dev.tape_rewind()
        volume_header = self.fs.dev.tape_read_forward()
        assert len(volume_header) == VOLUME_HEADER_SIZE, "Invalid volume header size"
        try:
            while True:
                tape_pos = self.fs.dev.tape_pos
                fib, tape_size = self.fs.dev.tape_read_header()
                if fib and len(fib) == FILE_INFORMATION_BLOCK_SIZE:
                    yield FileInformationBlock.read(self.fs, fib, tape_pos, tape_size)
        except EOFError:
            pass


class OS32TapeFilesystem(AbstractFilesystem):
    """
    Disk Backup Utility Magnetic Tape Format

        +-------------------------------------+
        |          Volume header              |  80 bytes
        +-------------------------------------+
        |     File Information Block (FIB)    |  80 bytes
        +-------------------------------------+
        |              Data                   |
        +-------------------------------------+
        |               EOF                   |
        +-------------------------------------+
        |     File Information Block (FIB)    |  80 bytes
        +-------------------------------------+
        |              Data                   |
        +-------------------------------------+
        |               EOF                   |
        /               ...                   /
        |               EOv                   |
        +-------------------------------------+

    OS/32 System Support Utilities Reference Manual, Pag 35
    https://bitsavers.org/pdf/interdata/32bit/os32/1984_7.2/48-031F00R02_SysSupportUtil_1984.pdf
    """

    fs_name = "os32mt"
    fs_description = "Interdata OS/32 Backup Tape Format"
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

    dev: Tape
    buffer_size: int = 0  # Size of the buffer used to transfer data

    def __init__(self, file_or_device: t.Union["AbstractFile", "AbstractDevice"]):
        if isinstance(file_or_device, AbstractFile):
            self.dev = Tape(file_or_device)
        elif isinstance(file_or_device, Tape):
            self.dev = file_or_device
        else:
            raise OSError(errno.EIO, f"Invalid device type for {self.fs_description} filesystem")

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        strict: t.Union[bool, str] = True,
        **kwargs: t.Union[bool, str],
    ) -> "OS32TapeFilesystem":
        """
        Mount the filesystem from a file or device
        """
        self = cls(file_or_dev)
        header = VolumeHeader.read(self)
        self.buffer_size = header.buffer_size
        return self

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
        account: t.Optional[int] = None,
    ) -> t.Iterator["FileInformationBlock"]:
        if account is None:
            account = ANY_ACCOUNT
        account, filename_pattern = os32_split_fullname(fullname=pattern, wildcard=wildcard, account=account)
        for entry in self.get_root().iterdir():
            if filename_match(entry.name, filename_pattern, wildcard) and account_match(entry.account, account):
                yield entry

    @property
    def entries_list(self) -> t.Iterator["FileInformationBlock"]:
        """
        Read File Information Blocks (FIBs) from the tape
        """
        yield from self.get_root().iterdir()

    def get_file_entry(self, fullname: str) -> "FileInformationBlock":
        """
        Get the directory entry for a file
        """
        account, stem = os32_split_fullname(fullname=fullname, wildcard=False, account=ANY_ACCOUNT)
        try:
            return next(self.filter_entries_list(stem, account=account, wildcard=False))
        except StopIteration:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def get_root(self) -> OS32TapeRootDirectory:
        """
        Get the root directory of the filesystem
        """
        return OS32TapeRootDirectory(self)

    def show_accounts(self, volume_id: str, options: t.Dict[str, bool]) -> None:
        """
        Listing of all UIC
        """
        accounts = set()
        for entry in self.get_root().iterdir():
            accounts.add(entry.account)
        sys.stdout.write(f"  ACT")
        for account in sorted(accounts):
            sys.stdout.write(f"{account}\n")

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List directory entries
        """
        volume_header = VolumeHeader.read(self)
        creation_date = format_time(volume_header.creation_date)
        sys.stdout.write(f"\nInput:     {volume_id}:\n\n")
        sys.stdout.write("           Header Information from Input Tape:\n\n")
        sys.stdout.write(
            f"           Volume: {volume_header.name:>4}:   "
            f"Date Created {creation_date}   "
            f"BACKUP Rev.: {volume_header.revision:02}-{volume_header.update:02}\n\n"
        )
        if volume_header.select_vol:
            select = f"{volume_header.select_vol}:{volume_header.select_filename}.{volume_header.select_extension}/{volume_header.select_account}"
        else:
            select = "** All **"
        sys.stdout.write(f"           Size: {volume_header.buffer_size / 1024:.2f}K   Select: {select}\n\n")
        sys.stdout.write("Files Selected:\n\n")
        sys.stdout.write("Filename......... Type Dbs/Ibs Lrecl Records Date Created.. Date Written.. Keys\n\n")

        count = 0
        for x in self.filter_entries_list(pattern, include_all=False, wildcard=True):
            count += 1
            # Print file information
            creation_date = format_time(x.creation_date)
            last_mod_date = format_time(x.last_mod_date)
            if x.is_indexed or x.is_nonbuffered_indexed:
                # DBS     - the data block size (in sectors)
                # IBS     - the index block size (in sectors)
                # RECL    - the record length in bytes
                dbs_ibs_recl = f"{x.block_size:>3}/{x.index_block_size:<3} {x.record_length:>5}"
            elif x.is_extended_contiguous:
                dbs_ibs_recl = f"{x.block_size:>3}/{x.index_block_size:<3}"
            else:
                dbs_ibs_recl = ""  # TODO

            sys.stdout.write(
                f"{x.stem:<8}.{x.extension:<3}/{x.account:05} {x.file_type}  "
                f"{dbs_ibs_recl:13} "
                f"{x.num_of_records:>7} "
                f"{creation_date} "
                f"{last_mod_date} "
                f"{x.write_key:02X}{x.read_key:02X}\n"
            )
        sys.stdout.write(f"{count:5} Files Selected\n\n")
        sys.stdout.write("\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if arg:
            # Display the file information
            entry = self.get_file_entry(arg)  # type: ignore
            sys.stdout.write(entry.examine())
            hex_dump(entry.raw_buffer)
            return
        # Volume header
        sys.stdout.write(f"Volume Header:\n\n")
        sys.stdout.write(str(VolumeHeader.read(self)) + "\n")
        self.dev.tape_rewind()
        volume_header = self.dev.tape_read_forward()
        hex_dump(volume_header)
        assert len(volume_header) == VOLUME_HEADER_SIZE, "Invalid volume header size"
        # File Information Blocks
        sys.stdout.write(f"\nFile Information Blocks:\n")
        try:
            while True:
                tape_pos = self.dev.tape_pos
                fib, tape_size = self.dev.tape_read_header()
                if fib:
                    entry = FileInformationBlock.read(self, fib, tape_pos, tape_size)
                    sys.stdout.write(f"\n{entry}\n")
                    hex_dump(fib)
        except EOFError:
            pass

    def read_files_data(self) -> t.Iterator[t.Tuple["FileInformationBlock", bytes]]:
        """
        Iterate over all files on the tape
        """
        self.dev.tape_rewind()
        self.dev.tape_read_forward()  # skip volume header
        try:
            while True:
                tape_pos = self.dev.tape_pos
                header = self.dev.tape_read_forward()
                if not header:
                    # tape mark - skip and continue (handles double tape mark at EOV)
                    continue
                data = self.dev.tape_read_file()
                entry = FileInformationBlock.read(self, header, tape_pos, len(data))
                yield entry, data
        except EOFError:
            pass

    def _rewrite_tape(self, entries: t.List[t.Tuple["FileInformationBlock", bytes]]) -> None:
        """
        Rewrite the entire tape with the given list of (FIB, data) pairs.
        The volume header is preserved unchanged.
        """
        # Read the volume header raw bytes before truncating
        self.dev.tape_rewind()
        volume_header_raw = self.dev.tape_read_forward()
        # Truncate the tape
        self.dev.tape_rewind()
        self.dev.tape_truncate(0)
        # Write the volume header back
        self.dev.tape_write_forward(volume_header_raw)
        # Write each file: FIB header + data blocks + tape mark
        for entry, data in entries:
            self.dev.tape_write_forward(entry.to_bytes())
            for i in range(0, len(data), self.buffer_size):
                block = data[i : i + self.buffer_size]
                self.dev.tape_write_forward(block)
            self.dev.tape_write_mark()
        # Write EOV (double tape mark)
        self.dev.tape_write_mark()
        self.dev.tape_truncate()

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
        if file_type != CO_FILE_TYPE:
            record_length: int = metadata.get("record_length") or DEFAULT_RECORD_LENGTH  # type: ignore
            if file_mode == ASCII and record_length > 0:
                content, num_of_records = ascii_to_records(content, record_length=record_length)
                metadata["num_of_records"] = num_of_records
        self.create_file(fullname=fullname, size=len(content), metadata=metadata, content=content)

    def create_file(
        self,
        fullname: str,
        size: int,
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
        content: t.Optional[t.Union[bytes, bytearray]] = None,
    ) -> "FileInformationBlock":
        """
        Create (or replace) a file on the tape by rewriting the entire tape.
        """
        metadata = metadata or {}
        account: int = metadata.get("account") or 0  # type: ignore
        file_type = os32_get_file_type_id(metadata.get("file_type"))  # type: ignore
        keys: int = metadata.get("keys") or 0  # type: ignore
        creation_date: t.Optional[datetime] = metadata.get("creation_date")  # type: ignore
        if file_type == CO_FILE_TYPE:
            num_of_records: int = math.ceil(size / OS32_BLOCK_SIZE)
            record_length: int = OS32_BLOCK_SIZE
            block_size: int = 0
            index_block_size: int = 0
        else:
            num_of_records = metadata.get("num_of_records") or 0  # type: ignore
            record_length = metadata.get("record_length") or DEFAULT_RECORD_LENGTH  # type: ignore
            block_size = metadata.get("block_size") or DEFAULT_BLOCK_SIZE  # type: ignore
            index_block_size = metadata.get("index_block_size") or DEFAULT_INDEX_BLOCK_SIZE  # type: ignore
        name: str
        account, name = os32_split_fullname(fullname=fullname, wildcard=False, account=account)  # type: ignore
        if "." in name:
            stem, extension = name.split(".", 1)
        else:
            stem, extension = name, ""
        # Collect all existing files, excluding any with the same name/account
        entries = [
            (entry, data)
            for entry, data in self.read_files_data()
            if not (filename_match(entry.name, name, False) and account_match(entry.account, account))
        ]
        # Build the new File Information Block
        new_entry = FileInformationBlock.create(
            fs=self,
            stem=stem,
            extension=extension,
            account=account,
            file_type=file_type,
            record_length=record_length,
            block_size=block_size,
            index_block_size=index_block_size,
            num_of_records=num_of_records,
            keys=keys,
            creation_date=creation_date,
            tape_size=size,
        )
        # Prepare file data
        file_data: bytes = bytes(content) if content is not None else b"\0" * size
        entries.append((new_entry, file_data))
        # Rewrite the tape
        self._rewrite_tape(entries)
        return new_entry

    @classmethod
    def initialize(
        cls, file_or_dev: t.Union["AbstractFile", "AbstractDevice"], **kwargs: t.Union[bool, str]
    ) -> "OS32TapeFilesystem":
        """
        Initialize the filesystem
        """
        try:
            volume_name = kwargs["name"].strip().upper() or DEFAULT_VOLUME_NAME  # type: ignore
        except Exception:
            volume_name = DEFAULT_VOLUME_NAME
        self = cls(file_or_dev)
        self.dev.tape_rewind()
        self.dev.tape_truncate()
        # Write the header
        header = VolumeHeader.create(self, volume_name)
        self.dev.tape_write_forward(header.to_bytes())
        # Write EOV (double tape mark)
        self.dev.tape_write_mark()
        self.dev.tape_truncate()
        return self
