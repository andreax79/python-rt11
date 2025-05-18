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
import struct
import sys
import typing as t
from abc import abstractmethod
from datetime import date, datetime

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..commons import BLOCK_SIZE, IMAGE, READ_FILE_FULL, dump_struct, filename_match
from .commons import ProDOSFileInfo, decode_apple_single, encode_apple_single
from .disk import AppleDisk

__all__ = [
    "ProDOSFile",
    "ProDOSFilesystem",
]

FILENAME_LEN = 15
POINTERS_FORMAT = "<HH"
POINTERS_SIZE = struct.calcsize(POINTERS_FORMAT)
VOLUME_DIRECTORY_HEADER_FORMAT = "<B15s8sIBBBBBHHH"
SUBDIRECTORY_HEADER_FORMAT = "<B15s8sIBBBBBHHBB"
ENTRY_FORMAT = "<B15sBHHBBBIBBBHIH"
ENTRY_SIZE = struct.calcsize(ENTRY_FORMAT)
assert struct.calcsize(VOLUME_DIRECTORY_HEADER_FORMAT) == struct.calcsize(SUBDIRECTORY_HEADER_FORMAT)
assert struct.calcsize(VOLUME_DIRECTORY_HEADER_FORMAT) == struct.calcsize(ENTRY_FORMAT)
EXTENDED_ENTRY_FORMAT = "<BHHBBB"
EXTENDED_DATA_FORK_POS = 0x0  # Data fork offset
EXTENDED_RESOURCE_FORK_POS = 0x100  # Resource fork offset
VOLUME_DIRECTORY_BLOCK = 2  #  Volume Directory Block
DEFAULT_DIRECTORY_BLOCKS = 3  # Default directory length in block
DEFAULT_VOLUME_NAME = "PRODOS"
PASCAL_AREA_NAME = "PASCAL.AREA"

INACTIVE_STORAGE_TYPE = 0x0  # Inactive entry
SEEDLING_FILE_STORAGE_TYPE = 0x1  # Seedling file
SAPLING_FILE_STORAGE_TYPE = 0x2  # Sapling file
TREE_FILE_STORAGE_TYPE = 0x3  # Tree file
PASCAL_AREA_STORAGE_TYPE = 0x4  # Pascal area
EXTENDED_FILE_STORAGE_TYPE = 0x5  # Extended file
DIRECTORY_FILE_SOURCE_TYPE = 0xD  # Directory file
SUBDIRECTORY_HEADER_STORAGE_TYPE = 0xE  # Subdirectory
VOLUME_DIRECTORY_HEADER_STORAGE_TYPE = 0xF  # volume directory header

# Access Attribute Field
#
#                            +--------  Invisible (GS/OS addition)
#                            |   +-------- Write-Enable
#                            |   |   +---- Read-Enable
#                            |   |   |
#  +----------------------------------+
#  | D | R | B | Reserved | I | W | R |
#  +----------------------------------+
#    |   |    |
#    |   |    +----------------------- Backup
#    |   +---------------------------- Rename-Enable
#    +-------------------------------- Destroy-Enable

ACCESS_DESTROY_ENABLE = 0x80  # Destroy enable
ACCESS_RENAME_ENABLE = 0x40  # Rename enable
ACCESS_BACKUP_ENABLE = 0x20  # Backup enable
ACCESS_FILE_INVISIBLE = 0x04  # Invisible (GS/OS addition)
ACCESS_WRITE_ENABLE = 0x02  # Write enable
ACCESS_READ_ENABLE = 0x01  # Read enable
DEFAULT_ACCESS = (
    ACCESS_DESTROY_ENABLE | ACCESS_RENAME_ENABLE | ACCESS_BACKUP_ENABLE | ACCESS_WRITE_ENABLE | ACCESS_READ_ENABLE
)

STORAGE_TYPES = {
    INACTIVE_STORAGE_TYPE: "Inactive",
    SEEDLING_FILE_STORAGE_TYPE: "Seedling file",
    SAPLING_FILE_STORAGE_TYPE: "Sapling file",
    TREE_FILE_STORAGE_TYPE: "Tree file",
    PASCAL_AREA_STORAGE_TYPE: "Pascal area",
    EXTENDED_FILE_STORAGE_TYPE: "Extended file",
    DIRECTORY_FILE_SOURCE_TYPE: "Directory file",
    SUBDIRECTORY_HEADER_STORAGE_TYPE: "Subdirectory",
    VOLUME_DIRECTORY_HEADER_STORAGE_TYPE: "Volume Directory Header",
}

# fmt: off
FILE_TYPES = {
    0X00: '   ', 0X01: 'BAD', 0X04: 'TXT', 0X06: 'BIN', 0X07: 'FNT',
    0X08: 'FOT', 0X09: 'BA3', 0X0A: 'DA3', 0X0B: 'WPF', 0X0C: 'SOS',
    0X0F: 'DIR', 0X19: 'ADB', 0X1A: 'AWP', 0X1B: 'ASP', 0X20: 'TDM',
    0X21: 'IPS', 0X22: 'UPV', 0X29: '3SD', 0X2A: '8SC', 0X2B: '8OB',
    0X2C: '8IC', 0X2D: '8LD', 0X2E: 'P8C', 0X41: 'OCR', 0X42: 'FTD',
    0X50: 'GWP', 0X51: 'GSS', 0X52: 'GDB', 0X53: 'DRW', 0X54: 'GDP',
    0X55: 'HMD', 0X56: 'EDU', 0X57: 'STN', 0X58: 'HLP', 0X59: 'COM',
    0X5A: 'CFG', 0X5B: 'ANM', 0X5C: 'MUM', 0X5D: 'ENT', 0X5E: 'DVU',
    0X60: 'PRE', 0X66: 'NCF', 0X6B: 'BIO', 0X6D: 'DVR', 0X6E: 'PRE',
    0X6F: 'HDV', 0X70: 'SN2', 0X71: 'KMT', 0X72: 'DSR', 0X73: 'BAN',
    0X74: 'CG7', 0X75: 'TNJ', 0X76: 'SA7', 0X77: 'KES', 0X78: 'JAP',
    0X79: 'CSL', 0X7A: 'TME', 0X7B: 'TLB', 0X7C: 'MR7', 0X7D: 'MLR',
    0X7E: 'MMM', 0X7F: 'JCP', 0X80: 'GES', 0X81: 'GEA', 0X82: 'GEO',
    0X83: 'GED', 0X84: 'GEF', 0X85: 'GEP', 0X86: 'GEI', 0X87: 'GEX',
    0X89: 'GEV', 0X8B: 'GEC', 0X8C: 'GEK', 0X8D: 'GEW', 0XA0: 'WP ',
    0XAB: 'GSB', 0XAC: 'TDF', 0XAD: 'BDF', 0XB0: 'SRC', 0XB1: 'OBJ',
    0XB2: 'LIB', 0XB3: 'S16', 0XB4: 'RTL', 0XB5: 'EXE', 0XB6: 'PIF',
    0XB7: 'TIF', 0XB8: 'NDA', 0XB9: 'CDA', 0XBA: 'TOL', 0XBB: 'DRV',
    0XBC: 'LDF', 0XBD: 'FST', 0XBF: 'DOC', 0XC0: 'PNT', 0XC1: 'PIC',
    0XC2: 'ANI', 0XC3: 'PAL', 0XC5: 'OOG', 0XC6: 'SCR', 0XC7: 'CDV',
    0XC8: 'FON', 0XC9: 'FND', 0XCA: 'ICN', 0XD5: 'MUS', 0XD6: 'INS',
    0XD7: 'MDI', 0XD8: 'SND', 0XDB: 'DBM', 0XE0: 'LBR', 0XE2: 'ATK',
    0XEE: 'R16', 0XEF: 'PAS', 0XF0: 'CMD', 0XF1: 'OVL', 0XF2: 'UD2',
    0XF3: 'UD3', 0XF4: 'UD4', 0XF5: 'BAT', 0XF6: 'UD6', 0XF7: 'UD7',
    0XF8: 'PRG', 0XF9: 'P16', 0XFA: 'INT', 0XFB: 'IVR', 0XFC: 'BAS',
    0XFD: 'VAR', 0XFE: 'REL', 0XFF: 'SYS'
}
# fmt: on
TXT_FILE_TYPE = 0x04  # Text file type
BIN_FILE_TYPE = 0x06  # Binary file type
DIR_FILE_TYPE = 0x0F  # Directory file type
PAS_FILE_TYPE = 0xEF  # Pascal file type


def prodos_to_date(val: int) -> t.Optional[datetime]:
    """
    Translate ProDOS date to Python date

            +-------------------------------+
            | 7   6   5   4   3   2   1   0 |
            +-------------------------------+
    Byte 0  | Y | Y | Y | Y | Y | Y | Y | M | Year (7bit), Month (1bit)
    Byte 1  | M | M | M | D | D | D | D | D | Month (3bit), Day (5bit)
    Byte 3  | 0 | 0 | 0 | H | H | H | H | H | Hour (5bit)
    Byte 4  | 0 | 0 | M | M | M | M | M | M | Minute (6bit)
            +-------------------------------+

    """
    buffer = val.to_bytes(4, byteorder="little")
    year = (buffer[1] >> 1) + 1900
    month = ((buffer[1] & 1) << 3) + (buffer[0] >> 5)
    day = buffer[0] & 0x1F
    hour = buffer[3]
    minute = buffer[2]

    try:
        return datetime(year, month, day, hour, minute)
    except Exception:
        return None


def date_to_prodos(dt: t.Optional[t.Union[date, datetime]]) -> int:
    """
    Translate Python date to ProDOS date
    """
    if dt is None:
        return 0
    else:
        buffer = [
            (dt.month & 0x7) << 5 | (dt.day & 0x1F),
            ((((dt.year - 1900) << 1) & 0xFF) | ((dt.month >> 3) & 0x1)),
            dt.minute if isinstance(dt, datetime) else 0,
            dt.hour if isinstance(dt, datetime) else 0,
        ]
        return int.from_bytes(buffer, byteorder="little")


def format_time(dt: t.Optional[datetime]) -> str:
    """
    Format date and time
    """
    if dt is None:
        return "               "
    else:
        day = dt.strftime("%d-%b-%y").lstrip("0").upper()
        return f"{day:>9} {dt.hour:2}:{dt.minute:02}"


def prodos_canonical_filename(fullname: str, wildcard: bool = False) -> str:
    """
    Generate the canonical filename name
    """
    if fullname:
        fullname = fullname[:FILENAME_LEN].upper()
    return fullname


def prodos_join(a: str, *p: str) -> str:
    """
    Join two or more pathname components
    """
    path = a
    if not p:
        path[:0] + "/"
    for b in p:
        if b.startswith("/"):
            path = b
        elif not path or path.endswith("/"):
            path += b
        else:
            path += "/" + b
    return path


def prodos_split(p: str) -> t.Tuple[str, str]:
    """
    Split a pathname
    """
    i = p.rfind("/") + 1
    head, tail = p[:i], p[i:]
    if head and head != "/" * len(head):
        head = head.rstrip("/")
    return head, tail


def prodos_normpath(path: str, pwd: str) -> str:
    """
    Normalize path
    """
    if not path:
        return path
    if not path.startswith("/"):
        path = prodos_join(pwd, path)
    parts: t.List[str] = []
    for part in path.split("/"):
        if not part or part == ".":
            continue
        part = prodos_canonical_filename(part)
        if part != ".." or (not parts) or (parts and parts[-1] == ".."):
            parts.append(part)
        elif parts:
            parts.pop()
    return "/" + "/".join(parts)


def parse_file_aux_type(file_type: t.Optional[str], default: int = 0, default_aux_type: int = 0) -> t.Tuple[int, int]:
    """
    Get the file type, aux type from a string like FILE_TYPE,AUX_TYPE

    Examples:

    BIN,$2000   - BIN file type ($06), aux type 2000 (hexadecimal)
    TXT,5000    - TXT file type ($04), aux type 5000 (decimal)
    TXT         - TXT file type ($04), aux type 0000 (default)
    $04         - TXT file type ($04), aux type 0000 (default)
    """
    if not file_type:
        return default, default_aux_type
    file_id = None
    file_type = file_type.upper()
    try:
        file_type, aux_type_str = file_type.split(",", 1)
        aux_type_str = aux_type_str.strip()
        if aux_type_str.startswith("$"):
            aux_type = int(aux_type_str[1:], 16)
        else:
            aux_type = int(aux_type_str)
    except Exception:
        aux_type = default_aux_type
    if file_type.startswith("$"):
        try:
            file_id = int(file_type[1:], 16)
        except Exception:
            pass
    else:
        for file_id, file_str in FILE_TYPES.items():
            if file_str == file_type:
                break
    if file_id is None:
        raise Exception("?KMON-F-Invalid file type specified with option")
    return file_id, aux_type


def format_file_type(file_type: int) -> str:
    """
    Format file type
    """
    return FILE_TYPES.get(file_type, f"${file_type:02X}")


def format_access(access: int) -> str:
    return (
        ("D" if (access & ACCESS_DESTROY_ENABLE) == ACCESS_DESTROY_ENABLE else "-")
        + ("R" if (access & ACCESS_RENAME_ENABLE) == ACCESS_RENAME_ENABLE else "-")
        + ("B" if (access & ACCESS_BACKUP_ENABLE) == ACCESS_BACKUP_ENABLE else "-")
        + ("I" if (access & ACCESS_FILE_INVISIBLE) == ACCESS_FILE_INVISIBLE else "-")
        + ("W" if (access & ACCESS_WRITE_ENABLE) == ACCESS_WRITE_ENABLE else "-")
        + ("R" if (access & ACCESS_READ_ENABLE) == ACCESS_READ_ENABLE else "-")
    )


class ProDOSFile(AbstractFile):
    entry: "FileEntry"
    closed: bool

    def __init__(self, entry: "FileEntry"):
        self.entry = entry
        self.closed = False

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
            if disk_block_number == 0:  # sparse file
                t = bytes(BLOCK_SIZE)
            else:
                t = self.entry.fs.read_block(disk_block_number)
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
            or block_number + number_of_blocks > self.entry.get_length()
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        # Get the blocks to be written
        blocks = list(self.entry.blocks())[block_number : block_number + number_of_blocks]
        # Write the blocks
        for i, disk_block_number in enumerate(blocks):
            if disk_block_number == 0:
                # TODO: write spase file
                raise OSError(errno.ENOSYS, os.strerror(errno.ENOSYS))
            data = buffer[i * BLOCK_SIZE : (i + 1) * BLOCK_SIZE]
            self.entry.fs.write_block(data, disk_block_number)

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
        return str(self.entry)


class IndexBlock:

    fs: "ProDOSFilesystem"
    block_number: int  # Index block number
    indexes: t.List[int]  # 256 block indexes

    def __init__(self, fs: "ProDOSFilesystem", block_number: int):
        self.fs = fs
        self.block_number = block_number
        self.indexes = [0] * 256

    @classmethod
    def read(cls, fs: "ProDOSFilesystem", block_number: int) -> "IndexBlock":
        """
        Read an index block from the disk
        """
        self = IndexBlock(fs, block_number)
        if block_number == 0:  # sparse file
            buffer = bytes(BLOCK_SIZE)
        else:
            buffer = self.fs.read_block(block_number)
        self.indexes = [int(buffer[i]) + int(buffer[i + 256]) * 256 for i in range(0, BLOCK_SIZE // 2)]
        return self

    def write(self) -> None:
        """
        Write the index block to the disk
        """
        if self.block_number == 0:
            # TODO: write spase file
            raise OSError(errno.ENOSYS, os.strerror(errno.ENOSYS))
        buffer = bytearray(BLOCK_SIZE)
        for i, index in enumerate(self.indexes):
            buffer[i] = index & 0xFF
            buffer[i + 256] = index >> 8
        self.fs.write_block(buffer, self.block_number)


class ProDOSAbstractDirEntry(AbstractDirectoryEntry):
    """
    ProDOS Abstract Directory Entry

    The subclasses are:

    +-- VolumeDirectoryHeader - Volume directory header
    |
    +-- SubdirectoryHeader - Subdirectory header
    |
    +-- FileEntry - Abstract file entry
            |
            +-- RegularFileEntry - Regular file
            |       |
            |       +-- ExtendedFileFork - File fork
            |
            +-- ExtendedFileEntry - Extended file with data and resource forks
            |
            +-- PPMVolumeEntry - Volume of the Pascal ProFile Manager (PPM) Partition
            |
            +-- AbstractDirectoryFileEntry - Abstract directory entry
                    |
                    +-- DirectoryFileEntry - Directory
                    |
                    +-- PPMDirectoryEntry - Pascal Area (PPM Partition)
    """

    parent: t.Optional["FileEntry"]  # Parent directory
    storage_type: int = 0  # Storage type
    filename: str = ""  # Volume/subdirectory/file name
    version: int = 0  #  0 in ProDOS 1.0
    min_version: int = 0  #  0 in ProDOS 1.0
    raw_creation_date: int = 0  # Date and time of creation

    def __init__(self, fs: "ProDOSFilesystem", parent: t.Optional["FileEntry"]):
        self.fs = fs
        self.parent = parent

    @classmethod
    def read(
        cls, fs: "ProDOSFilesystem", parent: t.Optional["FileEntry"], buffer: bytes, position: int = 0
    ) -> t.Optional["ProDOSAbstractDirEntry"]:
        from .ppm import PPMDirectoryEntry

        storage_type = buffer[position] >> 4
        if storage_type == 0:
            return None
        elif storage_type == VOLUME_DIRECTORY_HEADER_STORAGE_TYPE:
            return VolumeDirectoryHeader.read(fs, parent, buffer, position)
        elif storage_type == SUBDIRECTORY_HEADER_STORAGE_TYPE:
            return SubdirectoryHeader.read(fs, parent, buffer, position)
        elif storage_type in (
            SEEDLING_FILE_STORAGE_TYPE,
            SAPLING_FILE_STORAGE_TYPE,
            TREE_FILE_STORAGE_TYPE,
        ):
            return RegularFileEntry.read(fs, parent, buffer, position)
        elif storage_type == DIRECTORY_FILE_SOURCE_TYPE:
            return DirectoryFileEntry.read(fs, parent, buffer, position)
        elif storage_type == PASCAL_AREA_STORAGE_TYPE:
            return PPMDirectoryEntry.read(fs, parent, buffer, position)
        elif storage_type == EXTENDED_FILE_STORAGE_TYPE:
            return ExtendedFileEntry.read(fs, parent, buffer, position)
        else:
            print(f"Unknown storage type {storage_type}")
            return None

    @property
    def fullname(self) -> str:
        if self.parent:
            return prodos_join(self.parent.fullname, self.filename)
        else:
            return self.filename

    @property
    def basename(self) -> str:
        return self.filename

    @property
    def storage_type_name(self) -> str:
        return STORAGE_TYPES.get(self.storage_type, f"{self.storage_type:02X}")

    @property
    def creation_date(self) -> t.Optional[datetime]:
        return prodos_to_date(self.raw_creation_date)

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    def delete(self) -> bool:
        """
        Delete the directory entry
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def write(self) -> bool:
        """
        Write the directory entry
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return 0

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return 0

    def open(self, file_mode: t.Optional[str] = None) -> ProDOSFile:
        """
        Open a file
        """
        raise OSError(errno.EISDIR, os.strerror(errno.EISDIR))

    @abstractmethod
    def write_buffer(self, buffer: bytearray, position: int = 0) -> None:
        """
        Write the entry to a buffer
        """
        pass

    def __lt__(self, other: "ProDOSAbstractDirEntry") -> bool:
        return self.filename < other.filename

    def __gt__(self, other: "ProDOSAbstractDirEntry") -> bool:
        return self.filename > other.filename


class VolumeDirectoryHeader(ProDOSAbstractDirEntry):
    """
       Field                                Byte of
      Length                                Block
             +----------------------------+
     1 byte  | storage_type | name_length | $04
             |----------------------------|
             |                            | $05
             /                            /
    15 bytes /        file_name           /
             |                            | $13
             |----------------------------|
             |                            | $14
             /                            /
     8 bytes /          reserved          /
             |                            | $1B
             |----------------------------|
             |                            | $1C
             |          creation          | $1D
     4 bytes |        date & time         | $1D
             |                            | $1F
             |----------------------------|
     1 byte  |          version           | $20
             |----------------------------|
     1 byte  |        min_version         | $21
             |----------------------------|
     1 byte  |           access           | $22
             |----------------------------|
     1 byte  |        entry_length        | $23
             |----------------------------|
     1 byte  |     entries_per_block      | $24
             |----------------------------|
             |                            | $25
     2 bytes |         file_count         | $26
             |----------------------------|
             |                            | $27
     2 bytes |      bit_map_pointer       | $28
             |----------------------------|
             |                            | $29
     2 bytes |        total_blocks        | $2A
             +----------------------------+

    """

    access: int = 0  #  Determines whether this directory can be read, written, destroyed, and renamed
    entry_length: int = 0  # The length in bytes of each entry in this directory (39)
    entries_per_block: int = 0  # The number of entries in each block of the directory file (13)
    file_count: int = 0  # The number of files in this directory
    bit_map_pointer: int = 0  # Volume bitmap pointer
    total_blocks: int = 0  # The total number of blocks on the volume
    reserved: bytes = b""  # Reserved for future use

    @classmethod
    def read(
        cls, fs: "ProDOSFilesystem", parent: t.Optional["FileEntry"], buffer: bytes, position: int = 0
    ) -> "VolumeDirectoryHeader":
        self = VolumeDirectoryHeader(fs, parent)
        (
            storage_type_name_length,
            raw_filename,
            self.reserved,
            self.raw_creation_date,
            self.version,
            self.min_version,
            self.access,
            self.entry_length,
            self.entries_per_block,
            self.file_count,
            self.bit_map_pointer,
            self.total_blocks,
        ) = struct.unpack_from(VOLUME_DIRECTORY_HEADER_FORMAT, buffer, position)
        self.storage_type = storage_type_name_length >> 4
        filename_length = storage_type_name_length & 0x0F
        self.filename = raw_filename[:filename_length].decode("ascii", errors="ignore")
        return self

    def write_buffer(self, buffer: bytearray, position: int = 0) -> None:
        """
        Write the entry to a buffer
        """
        storage_type_name_length = (self.storage_type << 4) | len(self.filename)
        raw_filename = self.filename.ljust(15, "\0").encode("ascii")
        struct.pack_into(
            VOLUME_DIRECTORY_HEADER_FORMAT,
            buffer,
            position,
            storage_type_name_length,
            raw_filename,
            self.reserved,
            self.raw_creation_date,
            self.version,
            self.min_version,
            self.access,
            self.entry_length,
            self.entries_per_block,
            self.file_count,
            self.bit_map_pointer,
            self.total_blocks,
        )

    def __str__(self) -> str:
        access = format_access(self.access)
        return f"{self.filename:<15}  [{self.storage_type:X}] ---,----- {access}          {self.file_count:>7} files                    {format_time(self.creation_date)}"

    def __repr__(self) -> str:
        return str(self.__dict__)


class SubdirectoryHeader(ProDOSAbstractDirEntry):
    """
       Field                                Byte of
      Length                                Block
             +----------------------------+
     1 byte  | storage_type | name_length | $04
             |----------------------------|
             |                            | $05
             /                            /
    15 bytes /         file_name          /
             |                            | $13
             |----------------------------|
             |                            | $14
             /                            /
     8 bytes /          reserved          /
             |                            | $1B
             |----------------------------|
             |                            | $1C
             |          creation          | $1D
     4 bytes |        date & time         | $1D
             |                            | $1F
             |----------------------------|
     1 byte  |          version           | $20
             |----------------------------|
     1 byte  |        min_version         | $21
             |----------------------------|
     1 byte  |           access           | $22
             |----------------------------|
     1 byte  |        entry_length        | $23
             |----------------------------|
     1 byte  |     entries_per_block      | $24
             |----------------------------|
             |                            | $25
     2 bytes |         file_count         | $26
             |----------------------------|
             |                            | $27
     2 bytes |       parent_pointer       | $28
             |----------------------------|
     1 byte  |    parent_entry_number     | $29
             |----------------------------|
     1 byte  |    parent_entry_length     | $2A
             +----------------------------+
    """

    access: int  #  Determines whether this subdirectory can be read, written, destroyed, and renamed
    entry_length: int  # The length in bytes of each entry in this directory (39)
    entries_per_block: int  # The number of entries in each block of the directory file (13)
    file_count: int  # The number of files in this directory
    parent_pointer: int  # Parent directory pointer
    parent_entry_number: int  # Parent entry number
    parent_entry_length: int  # Parent entry length
    reserved: bytes  # Reserved for future use

    @classmethod
    def read(
        cls, fs: "ProDOSFilesystem", parent: t.Optional["FileEntry"], buffer: bytes, position: int = 0
    ) -> "SubdirectoryHeader":
        self = SubdirectoryHeader(fs, parent)
        (
            storage_type_name_length,
            raw_filename,
            self.reserved,
            self.raw_creation_date,
            self.version,
            self.min_version,
            self.access,
            self.entry_length,
            self.entries_per_block,
            self.file_count,
            self.parent_pointer,
            self.parent_entry_number,
            self.parent_entry_length,
        ) = struct.unpack_from(SUBDIRECTORY_HEADER_FORMAT, buffer, position)
        self.storage_type = storage_type_name_length >> 4
        filename_length = storage_type_name_length & 0x0F
        self.filename = raw_filename[:filename_length].decode("ascii", errors="ignore")
        return self

    def write_buffer(self, buffer: bytearray, position: int = 0) -> None:
        """
        Write the entry to a buffer
        """
        storage_type_name_length = (self.storage_type << 4) | len(self.filename)
        raw_filename = self.filename.ljust(15, "\0").encode("ascii")
        struct.pack_into(
            SUBDIRECTORY_HEADER_FORMAT,
            buffer,
            position,
            storage_type_name_length,
            raw_filename,
            self.reserved,
            self.raw_creation_date,
            self.version,
            self.min_version,
            self.access,
            self.entry_length,
            self.entries_per_block,
            self.file_count,
            self.parent_pointer,
            self.parent_entry_number,
            self.parent_entry_length,
        )

    def __str__(self) -> str:
        access = format_access(self.access)
        return f"{self.filename:<15}  [{self.storage_type:X}] ---,----- {access}          {self.file_count:>7} files                    {format_time(self.creation_date)}"

    def __repr__(self) -> str:
        return str(self.__dict__)


class FileEntry(ProDOSAbstractDirEntry):
    """
       Field                                Entry
      Length                                Offset
             +----------------------------+
     1 byte  | storage_type | name_length | $00
             |----------------------------|
             |                            | $01
             /                            /
    15 bytes /         file_name          /
             |                            | $0F
             |----------------------------|
     1 byte  |         file_type          | $10
             |----------------------------|
             |                            | $11
     2 bytes |        key_pointer         | $12
             |----------------------------|
             |                            | $13
     2 bytes |        blocks_used         | $14
             |----------------------------|
             |                            | $15
     3 bytes |            EOF             |
             |   (total number of bytes)  | $16
             |----------------------------|
             |                            | $18
             |          creation          |
     4 bytes |        date & time         |
             |                            | $1B
             |----------------------------|
     1 byte  |          version           | $1C
             |----------------------------|
     1 byte  |        min_version         | $1D
             |----------------------------|
     1 byte  |           access           | $1E
             |----------------------------|
             |                            | $1F
     2 bytes |          aux_type          | $20
             |----------------------------|
             |                            | $21
             |                            |
     4 bytes |          last mod          |
             |                            | $24
             |----------------------------|
             |                            | $25
     2 bytes |       header_pointer       | $26
             +----------------------------+

    """

    prodos_file_type: int = 0
    key_pointer: int = 0
    blocks_used: int = 0  # Number of blocks used by the file
    length: int = 0  # Total number of bytes
    access: int = 0  #  Determines whether this file can be read, written, destroyed, and renamed
    aux_type: int = 0  # A general-purpose field for storing additional information about the file
    last_mod_date: t.Optional[datetime] = None  # Date and time of last modification
    header_pointer: int = 0  # Block address of the key block of the directory

    @classmethod
    def read(
        cls, fs: "ProDOSFilesystem", parent: t.Optional["FileEntry"], buffer: bytes, position: int = 0
    ) -> "FileEntry":
        self = cls(fs, parent)
        (
            storage_type_name_length,
            raw_filename,
            self.prodos_file_type,
            self.key_pointer,
            self.blocks_used,
            eof0,
            eof1,
            eof2,
            self.raw_creation_date,
            self.version,
            self.min_version,
            self.access,
            self.aux_type,
            raw_last_mod_date,
            self.header_pointer,
        ) = struct.unpack_from(ENTRY_FORMAT, buffer, position)
        self.storage_type = storage_type_name_length >> 4
        self.last_mod_date = prodos_to_date(raw_last_mod_date)
        filename_length = storage_type_name_length & 0x0F
        self.filename = raw_filename[:filename_length].decode("ascii", errors="ignore")
        self.length = (eof2 << 16) | (eof1 << 8) | eof0
        return self

    @classmethod
    @abstractmethod
    def create(
        cls,
        fs: "ProDOSFilesystem",
        parent: t.Optional["AbstractDirectoryFileEntry"],
        filename: str,
        length: int,  # Length in blocks
        bitmap: "ProDOSBitmap",
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # optional creation date
        access: int = DEFAULT_ACCESS,  # optional access
        file_type: t.Optional[str] = None,  # optional file type
        aux_type: int = 0,  # optional aux type
        length_bytes: t.Optional[int] = None,  # optional length int bytes
        resource_length_bytes: t.Optional[int] = None,  # not used
    ) -> "FileEntry":
        """
        Create a new entry
        """
        pass

    def write_buffer(self, buffer: bytearray, position: int = 0) -> None:
        """
        Write the entry to a buffer
        """
        storage_type_name_length = (self.storage_type << 4) | len(self.filename)
        raw_filename = self.filename.ljust(15, "\0").encode("ascii")
        eof0 = self.length & 0xFF
        eof1 = (self.length >> 8) & 0xFF
        eof2 = (self.length >> 16) & 0xFF
        raw_last_mod_date = date_to_prodos(self.last_mod_date)
        struct.pack_into(
            ENTRY_FORMAT,
            buffer,
            position,
            storage_type_name_length,
            raw_filename,
            self.prodos_file_type,
            self.key_pointer,
            self.blocks_used,
            eof0,
            eof1,
            eof2,
            self.raw_creation_date,
            self.version,
            self.min_version,
            self.access,
            self.aux_type,
            raw_last_mod_date,
            self.header_pointer,
        )

    def read_bytes(self, file_mode: t.Optional[str] = None) -> bytes:
        """Get the content of the file"""
        data = super().read_bytes(IMAGE)
        if len(data) < self.length:  # sparse file - pad with zeros
            data += bytes(self.length - len(data))
        if self.prodos_file_type == BIN_FILE_TYPE:
            prodos_file_info = ProDOSFileInfo(self.access, self.prodos_file_type, self.aux_type)
            data = encode_apple_single(prodos_file_info, data)
        return data

    @abstractmethod
    def blocks(self, include_indexes: bool = False) -> t.Iterator[int]:
        """
        Iterate over the blocks of the file
        """
        pass

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return self.blocks_used

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.length

    def open(self, file_mode: t.Optional[str] = None) -> ProDOSFile:
        """
        Open a file
        """
        return ProDOSFile(self)

    def delete(self) -> bool:
        """
        Delete the file
        """
        if not isinstance(self.parent, AbstractDirectoryFileEntry):
            return False
        allocated_blocks = [x for x in self.blocks(include_indexes=True) if x != 0]
        if not self.parent.update_dir_entry(self, delete=True):
            return False
        # Update the bitmap
        bitmap = self.fs.read_bitmap()
        for block in allocated_blocks:
            bitmap.set_free(block)
        bitmap.write()
        return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        if not isinstance(self.parent, AbstractDirectoryFileEntry):
            return False
        if not self.parent.update_dir_entry(self):
            return False
        return True

    def __str__(self) -> str:
        access = format_access(self.access)
        file_type = format_file_type(self.prodos_file_type)
        return f"{self.filename:<15}  [{self.storage_type:X}] {file_type},${self.aux_type:04X} {access}  {self.key_pointer:>7} {self.blocks_used:>7} blocks  {self.length:>9} bytes  {format_time(self.last_mod_date)}  {format_time(self.creation_date)}"

    def __repr__(self) -> str:
        return str(self.__dict__)


class RegularFileEntry(FileEntry):
    """
    Regular file (seedling, sapling, tree)
    """

    @classmethod
    def create(
        cls,
        fs: "ProDOSFilesystem",
        parent: t.Optional["AbstractDirectoryFileEntry"],
        filename: str,
        length: int,  # Length in blocks
        bitmap: "ProDOSBitmap",
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # optional creation date
        access: int = DEFAULT_ACCESS,  # optional access
        file_type: t.Optional[str] = None,  # optional file type
        aux_type: int = 0,  # optional aux type
        length_bytes: t.Optional[int] = None,  # optional length int bytes
        resource_length_bytes: t.Optional[int] = None,  # not used
    ) -> "FileEntry":
        """
        Create a new regular file
        """
        # Calculate how many blocks are needed
        if length <= 1:
            blocks_used = 1
            storage_type = SEEDLING_FILE_STORAGE_TYPE
        elif length <= 256:
            blocks_used = length + 1  # add index block
            storage_type = SAPLING_FILE_STORAGE_TYPE
        else:
            blocks_used = length + ((length + 255) >> 8) + 1  # add index blocks and master index block
            storage_type = TREE_FILE_STORAGE_TYPE
        # Check free space and allocate blocks
        if bitmap.free() < blocks_used:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        allocated_blocks = bitmap.allocate(blocks_used)
        # Create the entry
        self = cls(fs, parent)
        self.storage_type = storage_type
        self.blocks_used = blocks_used  # blocks_used includes both index blocks and data blocks.
        self.length = length_bytes if length_bytes is not None else length * BLOCK_SIZE
        self.filename = filename
        self.key_pointer = allocated_blocks.pop(0)
        self.access = access
        if isinstance(creation_date, datetime):
            self.last_mod_date = creation_date
        elif isinstance(creation_date, date):
            self.last_mod_date = datetime.combine(creation_date, datetime.min.time())
        else:
            self.last_mod_date = datetime.now()
        self.raw_creation_date = date_to_prodos(self.last_mod_date)
        self.prodos_file_type, self.aux_type = parse_file_aux_type(file_type, default_aux_type=aux_type)
        # Block allocation
        if self.is_seedling_file():  # Seedling file
            pass
        elif self.is_sapling_file():  # Sapling file
            index_block = IndexBlock(self.fs, self.key_pointer)
            # blocks_used includes both index blocks and data blocks.
            for i in range(0, self.blocks_used - 1):
                index_block.indexes[i] = allocated_blocks.pop(0)
            index_block.write()
        elif self.is_tree_file():  # Tree file
            master_index_block = IndexBlock(self.fs, self.key_pointer)
            for i in range(0, len(master_index_block.indexes)):
                if not allocated_blocks:
                    break
                index_block_number = allocated_blocks.pop(0)
                master_index_block.indexes[i] = index_block_number
                index_block = IndexBlock(self.fs, index_block_number)
                for index in range(0, len(index_block.indexes)):
                    if not allocated_blocks:
                        break
                    index_block.indexes[index] = allocated_blocks.pop(0)
                index_block.write()
            master_index_block.write()
        # Write the entry
        if parent is not None:
            try:
                parent.update_dir_entry(self, create=True)
            except OSError:
                # Directory is full, grow it
                parent.grow(bitmap)
                parent.update_dir_entry(self, create=True)
        return self

    def is_seedling_file(self) -> bool:
        """
        A seedling file is a standard file that contains
        no more than 512 bytes
        (no more than 1 block)
        """
        return self.storage_type == SEEDLING_FILE_STORAGE_TYPE

    def is_sapling_file(self) -> bool:
        """ "
        A sapling file is a standard file that contains
        more than 512 and no more than 128K bytes
        (more than 1 and no more than 256 blocks)
        """
        return self.storage_type == SAPLING_FILE_STORAGE_TYPE

    def is_tree_file(self) -> bool:
        """
        A tree file is a standard file that contains
        more than 128K bytes (more than 256 blocks)
        """
        return self.storage_type == TREE_FILE_STORAGE_TYPE

    def blocks(self, include_indexes: bool = False) -> t.Iterator[int]:
        """
        Iterate over the blocks of the file
        """
        if self.is_seedling_file():  # Seedling file
            yield self.key_pointer
        elif self.is_sapling_file():  # Sapling file
            if include_indexes:
                yield self.key_pointer
            index_block = IndexBlock.read(self.fs, self.key_pointer)
            # blocks_used includes both index blocks and data blocks
            for i in range(0, self.blocks_used - 1):
                yield index_block.indexes[i]
        elif self.is_tree_file():  # Tree file
            if include_indexes:
                yield self.key_pointer
            master_index_block = IndexBlock.read(self.fs, self.key_pointer)
            remaining_blocks = self.blocks_used - 1
            for index_block_number in master_index_block.indexes:
                if remaining_blocks == 0:
                    break
                remaining_blocks -= 1
                if include_indexes:
                    yield index_block_number
                index_block = IndexBlock.read(self.fs, index_block_number)
                for index in index_block.indexes:
                    if remaining_blocks == 0:
                        break
                    remaining_blocks -= 1
                    yield index


class AbstractDirectoryFileEntry(FileEntry):

    @abstractmethod
    def update_dir_entry(
        self,
        entry: "ProDOSAbstractDirEntry",
        entry_class: t.Type["ProDOSAbstractDirEntry"] = FileEntry,
        create: bool = False,
        delete: bool = False,
    ) -> t.Optional[t.Tuple[int, int]]:
        pass

    @abstractmethod
    def iterdir(self) -> t.Iterator["ProDOSAbstractDirEntry"]:
        """
        Iterate over the directory entries
        """
        pass

    @abstractmethod
    def grow(self, bitmap: "ProDOSBitmap") -> None:
        """
        Grow the directory
        """
        pass


class DirectoryFileEntry(AbstractDirectoryFileEntry):
    """
    Subdirectory entry
    """

    @classmethod
    def create(
        cls,
        fs: "ProDOSFilesystem",
        parent: t.Optional["AbstractDirectoryFileEntry"],
        filename: str,
        length: int,  # Length in blocks
        bitmap: "ProDOSBitmap",
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # optional creation date
        access: int = DEFAULT_ACCESS,  # optional access
        file_type: t.Optional[str] = None,  # unused
        aux_type: int = 0,  # unused
        length_bytes: t.Optional[int] = None,  # unused
        resource_length_bytes: t.Optional[int] = None,  # unused
    ) -> "FileEntry":
        """
        Create a new directory
        """
        blocks = bitmap.allocate(length)
        # Create the directory entry in the parent directory
        self = DirectoryFileEntry(fs, parent)
        self.filename = filename
        self.storage_type = DIRECTORY_FILE_SOURCE_TYPE
        self.prodos_file_type = DIR_FILE_TYPE
        self.raw_creation_date = date_to_prodos(creation_date or datetime.now())
        self.access = access
        self.key_pointer = blocks[0]
        self.blocks_used = len(blocks)
        self.length = self.blocks_used * BLOCK_SIZE
        # Directory blocks setup
        for i, block_nr in enumerate(blocks):
            block_data = bytearray(BLOCK_SIZE)
            struct.pack_into(
                POINTERS_FORMAT,
                block_data,
                0,
                blocks[(i - 1)] if i > 0 else 0,
                blocks[(i + 1)] if i + 1 < len(blocks) else 0,
            )
            fs.write_block(block_data, block_nr)
        # Write the entry
        if parent is not None:
            try:
                (parent_pointer, parent_entry_number) = parent.update_dir_entry(self, create=True)  # type: ignore
            except OSError:
                # Directory is full, grow it
                parent.grow(bitmap)
                parent.update_dir_entry(self, create=True)

        # Write the header entry
        header = SubdirectoryHeader(fs, self)
        header.storage_type = SUBDIRECTORY_HEADER_STORAGE_TYPE
        header.filename = filename
        header.raw_creation_date = date_to_prodos(creation_date or datetime.now())
        header.version = parent.version if parent is not None else 0
        header.min_version = parent.min_version if parent is not None else 0
        header.access = access
        header.entry_length = ENTRY_SIZE
        header.entries_per_block = BLOCK_SIZE // header.entry_length
        header.parent_entry_length = ENTRY_SIZE
        header.file_count = 0
        header.parent_pointer = parent_pointer if parent_pointer is not None else 0
        header.parent_entry_number = parent_entry_number if parent_entry_number is not None else 0
        header.reserved = b"\0" * 8
        self.update_dir_entry(header, create=True)
        return self

    def blocks(self, include_indexes: bool = False) -> t.Iterator[int]:
        """
        A directory is a linked list of blocks:
        each blocks contains a pointer to the next/previous block
        """
        block_nr = self.key_pointer
        while block_nr != 0:
            yield block_nr
            block_data = self.fs.read_block(block_nr)
            _, block_nr = struct.unpack_from(POINTERS_FORMAT, block_data, 0)  # preceding, succeeding block numbers

    def iterdir(self) -> t.Iterator["ProDOSAbstractDirEntry"]:
        """
        Iterate over the directory entries
        """
        # Read the directory entries list
        block_nr = self.key_pointer
        while block_nr != 0:
            block_data = self.fs.read_block(block_nr)
            # Read the pointer fields
            _, block_nr = struct.unpack_from(POINTERS_FORMAT, block_data, 0)  # preceding, succeeding block numbers
            # Read the entries
            for position in range(POINTERS_SIZE, BLOCK_SIZE - ENTRY_SIZE, ENTRY_SIZE):
                entry = ProDOSAbstractDirEntry.read(self.fs, self, block_data, position)
                if entry:
                    yield entry

    def get_header(self) -> t.Union[VolumeDirectoryHeader, SubdirectoryHeader]:
        """
        Get the directory header
        """
        for entry in self.iterdir():
            if isinstance(entry, (VolumeDirectoryHeader, SubdirectoryHeader)):
                return entry
        raise OSError(errno.ENOENT, os.strerror(errno.ENOENT))

    def update_dir_entry(
        self,
        entry: "ProDOSAbstractDirEntry",
        entry_class: t.Type["ProDOSAbstractDirEntry"] = FileEntry,
        create: bool = False,
        delete: bool = False,
    ) -> t.Optional[t.Tuple[int, int]]:
        """
        Update/create/delete a directory entry
        """
        # Read the directory entries list
        block_nr = self.key_pointer
        found = False
        entry_number = 0
        while block_nr != 0:
            block_data = bytearray(self.fs.read_block(block_nr))
            # Read the entries
            for position in range(POINTERS_SIZE, BLOCK_SIZE - ENTRY_SIZE, ENTRY_SIZE):
                # Add the entry
                if create:
                    if block_data[position] == 0:
                        found = True
                        entry.write_buffer(block_data, position)
                        break
                else:
                    child = ProDOSAbstractDirEntry.read(self.fs, self, block_data, position)
                    if isinstance(child, entry_class) and child.filename == entry.filename:
                        found = True
                        if delete:
                            # Delete the entry (fill the entry with zeros)
                            struct.pack_into(f"<{ENTRY_SIZE}B", block_data, position, *([0] * ENTRY_SIZE))
                        else:
                            # Update the entry
                            entry.write_buffer(block_data, position)
                        break
                entry_number += 1
            # If the entry was found, write the block back
            if found:
                self.fs.write_block(block_data, block_nr)
                break
            # Read the pointer fields
            _, block_nr = struct.unpack_from(POINTERS_FORMAT, block_data, 0)  # preceding, succeeding block numbers
            entry_number = 0
        if not found:
            if create:
                # Raise an error if the directory is full
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            else:
                return None
        if (create or delete) and not isinstance(entry, (VolumeDirectoryHeader, SubdirectoryHeader)):
            # Update the number of file in the header
            header = self.get_header()
            if create:
                header.file_count += 1
            elif header.file_count > 1:
                header.file_count -= 1
            self.update_dir_entry(header, entry_class=type(header))
        return block_nr, entry_number

    def grow(self, bitmap: "ProDOSBitmap") -> None:
        """
        Grow the directory
        """
        # Read the directory entries list
        block_nr = self.key_pointer
        preceding_block_nr = 0
        preceding_preceding_block_nr = 0
        while block_nr != 0:
            block_data = bytearray(self.fs.read_block(block_nr))
            preceding_preceding_block_nr = preceding_block_nr
            preceding_block_nr, block_nr = struct.unpack_from(POINTERS_FORMAT, block_data, 0)
        # Allocate a new block
        block_nr = bitmap.allocate(1)[0]
        # Update the preceding block
        struct.pack_into(
            POINTERS_FORMAT,
            block_data,
            0,
            preceding_preceding_block_nr,
            block_nr,
        )
        self.fs.write_block(block_data, preceding_block_nr)
        # Update the new block
        block_data = bytearray(BLOCK_SIZE)
        struct.pack_into(
            POINTERS_FORMAT,
            block_data,
            0,
            preceding_block_nr,
            0,
        )
        self.fs.write_block(block_data, block_nr)

    def delete(self) -> bool:
        """
        Delete the directory and its entries
        """
        # Delete the directory entries
        for subentry in self.iterdir():
            if isinstance(subentry, FileEntry):
                subentry.delete()
        # Delete the directory
        return super().delete()

    def open(self, file_mode: t.Optional[str] = None) -> ProDOSFile:
        """
        Open a file
        """
        raise OSError(errno.EISDIR, os.strerror(errno.EISDIR))


class VolumeDirectoryFileEntry(DirectoryFileEntry):
    """
    Dummy Volume Directory entry
    """

    storage_type: int = DIRECTORY_FILE_SOURCE_TYPE
    key_pointer: int = VOLUME_DIRECTORY_BLOCK
    header: VolumeDirectoryHeader

    @classmethod
    def create_volume_directory(
        cls,
        fs: "ProDOSFilesystem",
        volume_name: str,
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # optional creation date
    ) -> "VolumeDirectoryFileEntry":
        """
        Create a new Volume Directory
        """
        filename = f"/{volume_name}"
        blocks = list(range(VOLUME_DIRECTORY_BLOCK, VOLUME_DIRECTORY_BLOCK + DEFAULT_DIRECTORY_BLOCKS))
        # Create the directory entry
        self = VolumeDirectoryFileEntry(fs, parent=None)
        self.filename = filename
        self.raw_creation_date = date_to_prodos(creation_date or datetime.now())
        self.access = DEFAULT_ACCESS
        # Directory blocks setup
        for i, block_nr in enumerate(blocks):
            block_data = bytearray(BLOCK_SIZE)
            struct.pack_into(
                POINTERS_FORMAT,
                block_data,
                0,
                blocks[(i - 1)] if i > 0 else 0,
                blocks[(i + 1)] if i + 1 < len(blocks) else 0,
            )
            fs.write_block(block_data, block_nr)
        # Write the header entry
        self.header = VolumeDirectoryHeader(fs, self)
        self.header.storage_type = VOLUME_DIRECTORY_HEADER_STORAGE_TYPE
        self.header.filename = volume_name.strip("/").upper()
        self.header.raw_creation_date = date_to_prodos(creation_date or datetime.now())
        self.header.version = 0
        self.header.min_version = 0
        self.header.access = DEFAULT_ACCESS
        self.header.entry_length = ENTRY_SIZE
        self.header.entries_per_block = BLOCK_SIZE // self.header.entry_length
        self.header.file_count = 0
        self.header.bit_map_pointer = fs.bit_map_pointer
        self.header.total_blocks = fs.total_blocks
        if not self.update_dir_entry(self.header, create=True):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return self


class ExtendedFileEntry(FileEntry):
    """
    Extended file with a data fork and a resource fork
    """

    @classmethod
    def create(
        cls,
        fs: "ProDOSFilesystem",
        parent: t.Optional["AbstractDirectoryFileEntry"],
        filename: str,
        length: int,  # Length in blocks (unused)
        bitmap: "ProDOSBitmap",
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # optional creation date
        access: int = DEFAULT_ACCESS,  # optional access
        file_type: t.Optional[str] = None,  # optional file type
        aux_type: int = 0,  # optional aux type
        length_bytes: t.Optional[int] = None,  # optional length int bytes
        resource_length_bytes: t.Optional[int] = None,  # length of the resource fork in bytes
    ) -> "FileEntry":
        """
        Create a new extended file entry
        """
        blocks_used = 1
        # Check free space and allocate blocks
        if bitmap.free() < blocks_used:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        allocated_blocks = bitmap.allocate(blocks_used)
        # Create the entry
        self = ExtendedFileEntry(fs, parent)
        self.storage_type = EXTENDED_FILE_STORAGE_TYPE
        self.blocks_used = 1
        self.length = BLOCK_SIZE
        self.filename = filename
        self.key_pointer = allocated_blocks.pop(0)
        self.access = access
        if isinstance(creation_date, datetime):
            self.last_mod_date = creation_date
        elif isinstance(creation_date, date):
            self.last_mod_date = datetime.combine(creation_date, datetime.min.time())
        else:
            self.last_mod_date = datetime.now()
        self.raw_creation_date = date_to_prodos(self.last_mod_date)
        self.prodos_file_type, self.aux_type = parse_file_aux_type(file_type, default_aux_type=aux_type)
        # Create forks
        data_fork_entry = ExtendedFileFork.create(
            fs=self.fs,
            parent=None,
            filename="DATA.FORK",
            length=int(math.ceil((length_bytes or 0) / BLOCK_SIZE)),
            bitmap=bitmap,
            creation_date=creation_date,
            access=access,
            file_type=file_type,
            aux_type=aux_type,
            length_bytes=length_bytes or 0,
        )
        resource_fork_entry = ExtendedFileFork.create(
            fs=self.fs,
            parent=None,
            filename="RESOURCE.FORK",
            length=int(math.ceil((resource_length_bytes or 0) / BLOCK_SIZE)),
            bitmap=bitmap,
            creation_date=creation_date,
            access=access,
            file_type=file_type,
            aux_type=aux_type,
            length_bytes=resource_length_bytes or 0,
        )
        # Write block
        extended_key_block = bytearray(BLOCK_SIZE)
        data_fork_entry.write_buffer(extended_key_block, EXTENDED_DATA_FORK_POS)
        resource_fork_entry.write_buffer(extended_key_block, EXTENDED_RESOURCE_FORK_POS)
        self.fs.write_block(extended_key_block, self.key_pointer)
        # Write the entry
        if parent is not None:
            try:
                parent.update_dir_entry(self, create=True)
            except OSError:
                # Directory is full, grow it
                parent.grow(bitmap)
                parent.update_dir_entry(self, create=True)
        return self

    def blocks(self, include_indexes: bool = False) -> t.Iterator[int]:
        """
        Iterate over the blocks
        """
        if include_indexes:
            yield self.key_pointer

    def iterdir(self) -> t.Iterator["ProDOSAbstractDirEntry"]:
        """
        An extended file acts like a directory with two entries: DATA.FORK and RESOURCE.FORK
        """
        extended_key_block = self.fs.read_block(self.key_pointer)
        yield ExtendedFileFork.read(self.fs, self, extended_key_block, EXTENDED_DATA_FORK_POS)
        yield ExtendedFileFork.read(self.fs, self, extended_key_block, EXTENDED_RESOURCE_FORK_POS)

    def open(self, file_mode: t.Optional[str] = None, fork: str = "DATA.FORK") -> ProDOSFile:
        """
        Open the data fork / resource fork
        """
        extended_key_block = self.fs.read_block(self.key_pointer)
        if fork.upper() == "RESOURCE.FORK":
            resource_fork = ExtendedFileFork.read(self.fs, self, extended_key_block, EXTENDED_RESOURCE_FORK_POS)
            return ProDOSFile(resource_fork)
        else:
            data_fork = ExtendedFileFork.read(self.fs, self, extended_key_block, EXTENDED_DATA_FORK_POS)
            return ProDOSFile(data_fork)

    def read_bytes(self, file_mode: t.Optional[str] = None) -> bytes:
        """
        Get the data/resource/metadata as AppleSingle
        """
        extended_key_block = self.fs.read_block(self.key_pointer)
        prodos_file_info = ProDOSFileInfo(self.access, self.prodos_file_type, self.aux_type)
        data_fork = ExtendedFileFork.read(self.fs, self, extended_key_block, EXTENDED_DATA_FORK_POS)
        data = data_fork.read_bytes()
        resource_fork = ExtendedFileFork.read(self.fs, self, extended_key_block, EXTENDED_RESOURCE_FORK_POS)
        resource = resource_fork.read_bytes()
        return encode_apple_single(prodos_file_info, data, resource)


class ExtendedFileFork(RegularFileEntry):
    """
    For extended files, the extended key block entry
    contains mini-directory entries for both the data and resource forks.
    The format for mini-entries is as follows:

       Field                                Entry
      Length                                Offset
             +----------------------------+
     1 byte  | storage_type               | $00
             |----------------------------|
             |                            | $01
     2 bytes |        key_pointer         | $02
             |----------------------------|
             |                            | $03
     2 bytes |        blocks_used         | $04
             |----------------------------|
             |                            | $05
     3 bytes |            EOF             |
             |   (total number of bytes)  | $07
             +----------------------------+

    """

    @classmethod
    def read(
        cls,
        fs: "ProDOSFilesystem",
        parent: t.Optional["FileEntry"],
        buffer: bytes,
        position: int = 0,
    ) -> "ExtendedFileFork":
        assert parent is not None
        self = ExtendedFileFork(fs, parent)
        if position == EXTENDED_DATA_FORK_POS:
            self.filename = "DATA.FORK"
        else:
            self.filename = "RESOURCE.FORK"
        self.prodos_file_type = parent.prodos_file_type
        self.access = parent.access
        (
            self.storage_type,
            self.key_pointer,
            self.blocks_used,
            eof0,
            eof1,
            eof2,
        ) = struct.unpack_from(EXTENDED_ENTRY_FORMAT, buffer, position)
        self.length = (eof2 << 16) | (eof1 << 8) | eof0
        return self

    def write_buffer(self, buffer: bytearray, position: int = 0) -> None:
        """
        Write the entry to a buffer
        """
        eof0 = self.length & 0xFF
        eof1 = (self.length >> 8) & 0xFF
        eof2 = (self.length >> 16) & 0xFF
        struct.pack_into(
            EXTENDED_ENTRY_FORMAT,
            buffer,
            position,
            self.storage_type,
            self.key_pointer,
            self.blocks_used,
            eof0,
            eof1,
            eof2,
        )

    def __str__(self) -> str:
        return f"{self.filename:<15}  [{self.storage_type:X}] ---,------        {self.key_pointer:>7} {self.blocks_used:>7} blocks  {self.length:>9} bytes"

    def __repr__(self) -> str:
        return str(self.__dict__)


class ProDOSBitmap:

    fs: "ProDOSFilesystem"
    bitmaps: t.List[int]
    bitmap_blocks: int  # Number of bitmap blocks

    def __init__(self, fs: "ProDOSFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "ProDOSFilesystem") -> "ProDOSBitmap":
        """
        Read the bitmap blocks
        """
        self = ProDOSBitmap(fs)
        self.bitmap_blocks = cls.calculate_bitmap_size(fs)
        # Read the bitmap blocks
        self.bitmaps = []
        for block_number in range(fs.bit_map_pointer, fs.bit_map_pointer + self.bitmap_blocks):
            buffer = fs.read_block(block_number)
            if not buffer:
                raise OSError(errno.EIO, os.strerror(errno.EIO))
            self.bitmaps += list(buffer)
        return self

    @classmethod
    def create(cls, fs: "ProDOSFilesystem") -> "ProDOSBitmap":
        """
        Create the bitmap blocks
        """
        self = ProDOSBitmap(fs)
        self.bitmap_blocks = cls.calculate_bitmap_size(fs)
        self.bitmaps = [0] * BLOCK_SIZE * self.bitmap_blocks
        # Mark the bitmap blocks of the volume as free
        for i in range(0, int(math.ceil(self.fs.get_size() / BLOCK_SIZE))):
            self.set_free(i)
        # Mark the first blocks as used
        for i in range(0, VOLUME_DIRECTORY_BLOCK + DEFAULT_DIRECTORY_BLOCKS + 1):
            self.set_used(i)
        return self

    @classmethod
    def calculate_bitmap_size(cls, fs: "ProDOSFilesystem") -> int:
        """
        Calculate the number of blocks in the bitmap
        """
        bitmap_bytes = fs.total_blocks // 8
        if fs.total_blocks % 8 > 0:
            bitmap_bytes += 1
        bitmap_blocks = bitmap_bytes // BLOCK_SIZE
        if bitmap_bytes % BLOCK_SIZE > 0:
            bitmap_blocks += 1
        return bitmap_blocks

    def write(self) -> None:
        """
        Write the bitmap blocks
        """
        for i in range(0, self.bitmap_blocks):
            buffer = bytes(self.bitmaps[i * BLOCK_SIZE : (i + 1) * BLOCK_SIZE])
            self.fs.write_block(buffer, self.fs.bit_map_pointer + i)

    @property
    def total_bits(self) -> int:
        """
        Return the bitmap length in bit
        """
        return len(self.bitmaps) * 8

    def is_free(self, block_number: int) -> bool:
        """
        Check if the block is free
        """
        int_index = block_number // 8
        bit_position = 7 - (block_number % 8)
        bit_value = self.bitmaps[int_index]
        return (bit_value & (1 << bit_position)) != 0

    def set_free(self, block_number: int) -> None:
        """
        Mark the block as free
        """
        int_index = block_number // 8
        bit_position = 7 - (block_number % 8)
        self.bitmaps[int_index] |= 1 << bit_position

    def set_used(self, block_number: int) -> None:
        """
        Mark the block as used
        """
        int_index = block_number // 8
        bit_position = 7 - (block_number % 8)
        self.bitmaps[int_index] &= ~(1 << bit_position)

    def allocate(self, size: int) -> t.List[int]:
        """
        Allocate blocks
        """
        blocks = []
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
        return self.fs.total_blocks - self.free()

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        free = 0
        for block in self.bitmaps:
            free += block.bit_count()
        return free


class ProDOSFilesystem(AbstractFilesystem, AppleDisk):
    """
    Apple II ProDOSo (Professional Disk Operating System)
    Apple III SOS (Sophisticated Operating System) Filesystem

    Blocks on a Volume

    +-----------------------------------   ----------------------------------   -------------------
    |         |         |   Block 2   |     |   Block n    |  Block n + 1  |     |    Block p    |
    | Block 0 | Block 1 |   Volume    | ... |    Volume    |    Volume     | ... |    Volume     | Other
    | Loader  | Loader  |  Directory  |     |  Directory   |    Bit Map    |     |    Bit Map    | Files
    |         |         | (Key Block) |     | (Last Block) | (First Block) |     | (Last Block)  |
    +-----------------------------------   ----------------------------------   -------------------

    http://www.easy68k.com/paulrsm/6502/PDOS8TRM.HTM#B

    Format of information on volume (SOS 1.2)
    https://apple3.org/Documents/Manuals/Apple%20III%20SOS%20Reference%20Manual%20Volume%201%20-%20How%20SOS%20Works.PDF Pag 49

    """

    fs_name = "prodos"
    fs_description = "Apple II ProDOS"

    pwd: str  # Current working directory
    volume_name: str  # Volume name
    total_blocks: int  # The total number of blocks on the volume
    bit_map_pointer: int  # Volume bitmap pointer
    root: "DirectoryFileEntry"  # Root directory entry

    def __init__(self, file: "AbstractFile"):
        super().__init__(file, rx_device_support=False)

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        self.pwd = "/"
        # Dummy root directory entry
        self.root = VolumeDirectoryFileEntry(self, parent=None)
        # Read the Volume Directory Header
        if not self.read_volume_directory_header():
            # Try ProDOS order
            self.prodos_order = True
            if not self.read_volume_directory_header():
                raise OSError(errno.EIO, os.strerror(errno.EIO))
        return self

    def read_volume_directory_header(self) -> bool:
        """
        Read the Volume Directory Header
        Returns True if the volume directory header is found
        """
        for entry in self.root.iterdir():
            if isinstance(entry, VolumeDirectoryHeader):
                self.volume_name = entry.filename
                self.root.filename = f"/{self.volume_name}"
                self.pwd = self.root.filename
                self.total_blocks = entry.total_blocks
                self.bit_map_pointer = entry.bit_map_pointer
                return True
        return False

    def read_bitmap(self) -> ProDOSBitmap:
        """
        Read the bitmap blocks
        """
        return ProDOSBitmap.read(self)

    def prepare_filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Tuple[str, str]:
        if not pattern:
            pattern = "*"
        absolute_path = prodos_join(self.pwd, pattern) if not pattern.startswith("/") else pattern
        if self.isdir(absolute_path) and expand:
            dirname = pattern
            pattern = "*"
        else:
            dirname, pattern = prodos_split(absolute_path)
        pattern = prodos_canonical_filename(pattern, wildcard)
        return dirname, pattern

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["ProDOSAbstractDirEntry"]:
        dirname, pattern = self.prepare_filter_entries_list(pattern, include_all, expand, wildcard)
        for entry in self.get_file_entry(dirname, AbstractDirectoryFileEntry).iterdir():  # type: ignore
            if filename_match(entry.basename, pattern, wildcard) and (include_all or isinstance(entry, FileEntry)):
                yield entry

    @property
    def entries_list(self) -> t.Iterator["ProDOSAbstractDirEntry"]:
        yield from self.get_file_entry(self.pwd, AbstractDirectoryFileEntry).iterdir()  # type: ignore

    def get_file_entry(
        self, path: str, entry_class: t.Type[ProDOSAbstractDirEntry] = ProDOSAbstractDirEntry
    ) -> "ProDOSAbstractDirEntry":
        """
        Get the file entry for a given path
        """
        path = prodos_normpath(path, self.pwd)
        # Check if path starts with the volume name
        if not path.startswith(f"/{self.volume_name}/") and path != f"/{self.volume_name}":
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
        path = path[len(self.volume_name) + 2 :]
        entry: t.Optional[ProDOSAbstractDirEntry]
        parent: t.Optional[ProDOSAbstractDirEntry]
        entry = parent = self.root
        for part in path.split("/"):
            if not part:
                continue
            if not hasattr(parent, "iterdir"):
                raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
            entry = None
            if not hasattr(parent, "iterdir"):
                raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
            for child in parent.iterdir():  # type: ignore
                if child.basename == part and isinstance(child, entry_class):
                    entry = child
                    break
            if entry is None:
                break
            parent = entry
        if entry is None:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
        return entry

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # optional creation date
        file_type: t.Optional[str] = None,
        file_mode: t.Optional[str] = None,
        access: t.Optional[int] = None,  # optional access
        aux_type: t.Optional[int] = None,  # optional auxiliary type
    ) -> None:
        """
        Write content to a file
        """
        # Check if the file is an AppleSingle file and extract the content and metadata
        resource: t.Optional[bytes] = None
        try:
            content, resource, prodos_file_info = decode_apple_single(content)
            if prodos_file_info is not None:
                if file_type is None:
                    file_type = format_file_type(prodos_file_info.file_type)
                if access is None:
                    access = prodos_file_info.access
                if aux_type is None:
                    aux_type = prodos_file_info.aux_type
        except ValueError:
            pass

        length_bytes = len(content)
        number_of_blocks = int(math.ceil(length_bytes / BLOCK_SIZE))
        # If file has a resource fork, create an extended file
        entry = self.create_file(
            fullname=fullname,
            number_of_blocks=number_of_blocks,
            creation_date=creation_date,
            file_type=file_type,
            access=access,
            aux_type=aux_type,
            length_bytes=length_bytes,
            resource_length_bytes=len(resource) if resource is not None else None,
        )
        if entry is not None:
            if resource is not None:
                # Write the resource fork
                resource_number_of_blocks = int(math.ceil(len(resource) / BLOCK_SIZE))
                resource = resource + (b"\0" * BLOCK_SIZE)  # pad with zeros
                f = entry.open(file_type, fork="RESOURCE.FORK")  # type: ignore
                try:
                    f.write_block(resource, block_number=0, number_of_blocks=resource_number_of_blocks)
                finally:
                    f.close()
            # Write the data fork
            content = content + (b"\0" * BLOCK_SIZE)  # pad with zeros
            f = entry.open(file_mode)
            try:
                f.write_block(content, block_number=0, number_of_blocks=number_of_blocks)
            finally:
                f.close()

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # optional creation date
        file_type: t.Optional[str] = None,
        access: t.Optional[int] = None,  # optional access
        aux_type: t.Optional[int] = None,  # optional auxiliary type
        length_bytes: t.Optional[int] = None,  # optional length in bytes
        resource_length_bytes: t.Optional[int] = None,  # optional resource fork length in bytes
    ) -> t.Optional["FileEntry"]:
        """
        Create a new file with a given length in number of blocks
        """
        from .ppm import PPMDirectoryEntry, PPMVolumeEntry

        fullname = prodos_normpath(fullname, self.pwd)
        dirname, filename = prodos_split(fullname)
        # Delete the file if it already exists
        try:
            self.get_file_entry(fullname, FileEntry).delete()  # type: ignore
        except FileNotFoundError:
            pass
        # Get parent directory
        parent: AbstractDirectoryFileEntry = self.get_file_entry(dirname, FileEntry)  # type: ignore
        if not isinstance(parent, AbstractDirectoryFileEntry):
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), dirname)
        # Create the file
        bitmap = self.read_bitmap()
        file_entry_cls: t.Type[FileEntry]
        if isinstance(parent, PPMDirectoryEntry):
            # If the parent is a Pascal Area, create a Pascal Volume
            file_entry_cls = PPMVolumeEntry
        elif filename == PASCAL_AREA_NAME:
            # If the name is PASCAL.AREA, create a Pascal Area
            file_entry_cls = PPMDirectoryEntry
        elif resource_length_bytes is not None:
            # If file has a resource fork, create an extended file
            file_entry_cls = ExtendedFileEntry
        else:
            file_entry_cls = RegularFileEntry
        entry = file_entry_cls.create(
            fs=self,
            parent=parent,
            filename=filename,
            length=number_of_blocks,
            bitmap=bitmap,
            creation_date=creation_date,
            access=access if access is not None else DEFAULT_ACCESS,
            file_type=file_type,
            aux_type=aux_type if aux_type is not None else 0,
            length_bytes=length_bytes if length_bytes is not None else number_of_blocks * BLOCK_SIZE,
            resource_length_bytes=resource_length_bytes,
        )
        bitmap.write()
        return entry

    def create_directory(
        self,
        fullname: str,
        options: t.Dict[str, t.Union[bool, str]],
    ) -> t.Optional["DirectoryFileEntry"]:
        """
        Create a new directory
        """
        fullname = prodos_normpath(fullname, self.pwd)
        dirname, filename = prodos_split(fullname)
        # Check if the directory already exists
        try:
            self.get_file_entry(fullname)
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST))
        except FileNotFoundError:
            pass
        # Get parent directory
        parent = self.get_file_entry(dirname, FileEntry)  # type: ignore
        if not isinstance(parent, DirectoryFileEntry):
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), dirname)
        # Create the directory
        bitmap = self.read_bitmap()
        directory: DirectoryFileEntry = DirectoryFileEntry.create(
            fs=self,
            parent=parent,
            filename=filename,
            length=DEFAULT_DIRECTORY_BLOCKS,
            bitmap=bitmap,
        )  # type: ignore
        bitmap.write()
        return directory

    def isdir(self, fullname: str) -> bool:
        """
        Check if the path is a directory
        """
        try:
            self.get_file_entry(fullname, AbstractDirectoryFileEntry)  # type: ignore
            return True
        except FileNotFoundError:
            return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        dirname, pattern = self.prepare_filter_entries_list(pattern, include_all=False, wildcard=True)
        entry: AbstractDirectoryFileEntry = self.get_file_entry(dirname, AbstractDirectoryFileEntry)  # type: ignore
        if not options.get("brief"):
            sys.stdout.write(f"\n{entry.basename}\n")
            sys.stdout.write("\n NAME           TYPE  BLOCKS  MODIFIED         CREATED          ENDFILE SUBTYPE\n\n")
        for x in entry.iterdir():
            if filename_match(x.basename, pattern, wildcard=True) and (isinstance(x, FileEntry)):
                if options.get("brief"):
                    # Lists only file names
                    sys.stdout.write(f"{x.basename}\n")
                else:
                    file_type = format_file_type(x.prodos_file_type)
                    locked = " " if (x.access & ACCESS_WRITE_ENABLE) == ACCESS_WRITE_ENABLE else "*"
                    last_mod_date = format_time(x.last_mod_date)
                    creation_date = format_time(x.creation_date)
                    if x.prodos_file_type == TXT_FILE_TYPE:
                        sub_type = f"R={x.aux_type:>5}"
                    elif x.prodos_file_type == BIN_FILE_TYPE:
                        sub_type = f"A=${x.aux_type:04X}"
                    else:
                        sub_type = ""
                    sys.stdout.write(
                        f"{locked}{x.filename:<15} {file_type} {x.blocks_used:>7} {last_mod_date}  {creation_date} {x.length:>9} {sub_type}\n"
                    )
        if not options.get("brief"):
            bitmap = self.read_bitmap()
            free = bitmap.free()
            used = bitmap.used()
            total = free + used
            sys.stdout.write(f"\nBLOCKS FREE:{free:>5}     BLOCKS USED:{used:>5}     TOTAL BLOCKS:  {total}\n\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        H1 = "Filename         File Type     Access  Address   Blocks             Size        Created          Modified\n"
        H2 = "--------         ---------     ------- -------   -------            ----        -------          --------\n"
        if arg:
            # Dump by path
            entry = self.get_file_entry(arg, ProDOSAbstractDirEntry)  # type: ignore
            if entry:
                entry_dict = dict(entry.__dict__)
                del entry_dict["fs"]
                entry_dict["storage_type_name"] = entry.storage_type_name
                if hasattr(entry, "blocks"):
                    entry_dict["blocks"] = list(entry.blocks())  # type: ignore
                sys.stdout.write(dump_struct(entry_dict) + "\n")
                if hasattr(entry, "iterdir"):
                    # Dump the directory entries
                    sys.stdout.write(f"\n{H1}{H2}")
                    for child in entry.iterdir():  # type: ignore
                        sys.stdout.write(f"{child}\n")
        else:
            # Dump the entire filesystem
            sys.stdout.write(dump_struct(self.__dict__))
            sys.stdout.write(f"\n\n{H1}{H2}")
            for x in self.filter_entries_list("*", include_all=True, wildcard=True):
                sys.stdout.write(f"{x}\n")

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        """
        Initialize the filesystem
        """
        try:
            volume_name = kwargs["name"].strip().upper() or DEFAULT_VOLUME_NAME  # type: ignore
        except Exception:
            volume_name = DEFAULT_VOLUME_NAME
        # Bitmap
        self.total_blocks = int(math.ceil(self.get_size() / BLOCK_SIZE))
        bitmap = ProDOSBitmap.create(self)
        self.bit_map_pointer = bitmap.allocate(1)[0]
        bitmap.write()
        # Dummy Volume Directory entry
        self.root = VolumeDirectoryFileEntry.create_volume_directory(self, volume_name)
        self.pwd = self.root.filename
        self.volume_name = volume_name

    def close(self) -> None:
        self.f.close()

    def chdir(self, fullname: str) -> bool:
        """
        Change the current directory
        """
        fullname = prodos_normpath(fullname, self.pwd)
        if self.isdir(fullname):
            self.pwd = fullname
            return True
        else:
            return False

    def get_pwd(self) -> str:
        """
        Get the current directory
        """
        return self.pwd

    def get_types(self) -> t.List[str]:
        """
        Get the list of the supported file types
        """
        return list(FILE_TYPES.values())
