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
import functools
import math
import operator
import os
import struct
import sys
import typing as t
from datetime import date, datetime, timedelta

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..block import BlockDevice
from ..commons import (
    ASCII,
    BLOCK_SIZE,
    IMAGE,
    READ_FILE_FULL,
    bytes_to_word,
    dump_struct,
    filename_match,
    word_to_bytes,
)
from ..unix.commons import unix_join, unix_split

__all__ = [
    "DGDOSFile",
    "DGDOSFilesystem",
    "rdos_canonical_filename",
    "rdos_get_file_type_id",
]

DISK_ID_BLOCK = 3  # Disk information block
SYS_DR = "SYS.DR"  # System Directory file
SYS_DR_BLOCK = 6  # First index block of SYS.DR (random file)
MAP_DR = "MAP.DR"  # Disk map file
MAP_DR_BLOCK = 15  # First block or MAP.DR (contiguous file)
SCPPA = 6  # Primary partition base address

UFD_ENTRY_FORMAT = "<10s2s12H"  # User File Descriptor Entry
UFD_ENTRY_LEN = struct.calcsize(UFD_ENTRY_FORMAT)
assert UFD_ENTRY_LEN == 36
UFD_LINK_ENTRY_FORMAT = "<10s2sH 10s10s2s"  # User File Descriptor Link Entry
UFD_LINK_ENTRY_LEN = struct.calcsize(UFD_LINK_ENTRY_FORMAT)
assert UFD_LINK_ENTRY_LEN == 36

START_DATE = datetime(1967, 12, 31)  # DGDOS date start

FILE_NAME_LENGTH = 10  # Maximum file name length
FILE_EXTENSION_LENGTH = 2  # Maximum file extension length

INDEX_ENTRIES = BLOCK_SIZE // 2 - 1  # Number of block addresses in an index block
SEQENTIAL_BLOCK_SIZE = BLOCK_SIZE - 2  # Bytes per block for sequential files
SEQENTIAL_BLOCK_SIZE_LARGE_DISK = BLOCK_SIZE - 4  # Bytes per block for sequential files on large disks

# Files attributes/characteristics
# Pag 61
# https://bitsavers.trailing-edge.com/pdf/dg/software/rdos/093-400027-00_RDOS_SystemReference_Oct83.pdf

ATWP = 2**0  #   Write protected - the file cannot be written
ATPER = 2**1  #  Permanent file - the file cannot be deleted or renamed
ATRAN = 2**2  #  Random file
ATCON = 2**3  #  Contiguous file
ATUS2 = 2**5  #  User defined 2
ATUS1 = 2**6  #  User defined 1
ATNRS = 2**8  #  No resolution allowed - the file cannot be linked
ATRES = 2**9  #  Link resolution file
ATDIR = 2**10  # Directory
ATPAR = 2**11  # Disk partition
ATLNK = 2**12  # Link entry
ATSAV = 2**13  # Save file (core image)
ATCHA = 2**14  # Attribute-protected file - the attributes cannot be changed
ATRP = 2**15  #  Read-protected file - the file cannot be read

# Disk ID Block - Disk characteristics

CHDOBL = 2**15  # Disk requires double addressing?
CHTOPL = 2**14  # Top-loader (dual-platter disk subsystem)

# File types

RANDOM_FILE_TYPE = 0  # Random file (indexed file)
CONTIGUOUS_FILE_TYPE = 1  # Contiguous file
SEQUENTIAL_FILE_TYPE = 2  # Sequential file (linked file)

FILE_TYPES = {
    RANDOM_FILE_TYPE: "RANDOM",
    CONTIGUOUS_FILE_TYPE: "CONTIGUOUS",
    SEQUENTIAL_FILE_TYPE: "SEQUENTIAL",
}


def rdos_to_date(date: int, time: int = 0) -> t.Optional[datetime]:
    """
    Convert RDOS date and time to datetime
    """
    if date == 0:
        return None
    return START_DATE + timedelta(
        days=date,
        hours=time >> 8,
        minutes=time % 256,
    )


def date_to_rdos(dt: t.Optional[datetime]) -> t.Tuple[int, int]:
    """
    Convert datetime to RDOS date and time
    """
    if dt is None:
        return 0, 0
    delta = dt - START_DATE
    days = delta.days
    hours = dt.hour
    minutes = dt.minute
    return days, (hours << 8) + minutes


def format_attr(attr: int, long: bool = False) -> str:
    if long:
        if (attr & ATDIR) != 0:  # Directory
            t = "DIR "
        elif (attr & ATPAR) != 0:  # Partition (RDOS only)
            t = "PART"
        elif (attr & ATLNK) != 0:  # Link entry
            t = "LINK"
        elif (attr & ATRAN) != 0:  # Random organized file
            t = "RAND"
        elif (attr & ATCON) != 0:  # Contiguous organized file
            t = "CONT"
        else:  # Sequential organized file
            t = "SEQ "
        return f"[{t}] " + "".join(
            [
                (attr & ATRP) and "R" or "",  #  read protected
                (attr & ATCHA) and "A" or "",  # attribute protected (attributes cannot be changed)
                (attr & ATPER) and "P" or "",  # permanent file (cannot be deleted or renamed)
                (attr & ATWP) and "W" or "",  #  write protected
                (attr & ATSAV) and "S" or "",  # save file (core image)
                (attr & ATUS2) and "&" or "",  # second user defined attribute
                (attr & ATUS1) and "?" or "",  # first user defined attribute
                (attr & ATNRS) and "N" or "",  # no resolution allowed (cannot be linked)
                (attr & ATRES) and "-" or "",  # link resolution file (temporary)
                # I ??? accessible by direct I/O only
            ]
        )

    return "".join(
        [
            (attr & ATRP) and "R" or "",  #  read protected
            (attr & ATCHA) and "A" or "",  # attribute protected (attributes cannot be changed)
            (attr & ATPER) and "P" or "",  # permanent file (cannot be deleted or renamed)
            (attr & ATWP) and "W" or "",  #  write protected
            (attr & ATSAV) and "S" or "",  # save file (core image)
            (attr & ATRAN) and "D" or "",  # random organized file
            (attr & ATCON) and "C" or "",  # contiguous organized file
            (attr & ATUS2) and "&" or "",  # second user defined attribute
            (attr & ATUS1) and "?" or "",  # first user defined attribute
            (attr & ATNRS) and "N" or "",  # no resolution allowed (cannot be linked)
            (attr & ATRES) and "-" or "",  # link resolution file (temporary)
            (attr & ATDIR) and "Y" or "",  # directory
            (attr & ATPAR) and "T" or "",  # partition
            (attr & ATLNK) and "L" or "",  # link entry
            # I ??? accessible by direct I/O only
        ]
    )


def swap_bytes(data: bytes) -> bytes:
    """
    Swap the bytes in a byte array
    """
    length = len(data)
    word_length = length // 2
    words = struct.unpack(f">{word_length}H", data[: word_length * 2])
    result = struct.pack(f"<{word_length}H", *words)
    # Append last byte if length is odd
    if length % 2:
        result += data[-1:]
    return result


def rdos_get_file_type_id(file_type: t.Optional[str], default: int = RANDOM_FILE_TYPE) -> int:
    """
    Get the file type id from a string
    """
    if not file_type:
        return default
    file_type = file_type.upper()
    for file_id, file_str in FILE_TYPES.items():
        if file_str == file_type:
            return file_id
    raise Exception("?KMON-F-Invalid file type specified with option")


def rdos_join(a: str, *p: str) -> str:
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


def rdos_normpath(path: str, pwd: str) -> str:
    """
    Normalize path
    """
    if not path:
        return path
    if not path.startswith("/"):
        path = rdos_join(pwd, path)
    parts: t.List[str] = []
    for part in path.split("/"):
        if not part:
            pass
        elif part == ".":
            pass
        elif part == "..":
            if parts:
                parts.pop()
        else:
            part = rdos_canonical_filename(part)
            parts.append(part)
    return "/" + "/".join(parts)


def rdos_canonical_filename(basename: t.Optional[str], wildcard: bool = False) -> str:
    """
    Generate the canonical RDOS name
    """

    def filter_fn(s: str) -> bool:
        if wildcard and s == "*":
            return True
        return str.isalnum(s) or s == "$"

    if not basename:
        return ""

    try:
        filename, extension = basename.split(".", 1)
    except Exception:
        filename = basename
        extension = ""

    filename = "".join(filter(filter_fn, filename.upper()))[:FILE_NAME_LENGTH]
    extension = "".join(filter(filter_fn, extension.upper()))[:FILE_EXTENSION_LENGTH]
    if extension:
        return f"{filename}.{extension}"
    else:
        return filename


def bytes_to_ascii(val: bytes) -> str:
    """
    Convert bytes to ascii string
    """
    return swap_bytes(val).decode("ascii", errors="ignore").strip("\0")


def ascii_to_bytes(val: str, length: int) -> bytes:
    """
    Convert ascii string to bytes, padding with null bytes
    """
    return swap_bytes(val.encode("ascii").ljust(length, b"\0")[:length])


def filename_hash(filename: str, extension: str, frame_size: int = 0xFFFF) -> int:
    tmp = ascii_to_bytes(filename.upper(), FILE_NAME_LENGTH)
    t0 = sum([x * (0o400 if i % 2 == 1 else 1) for i, x in enumerate(tmp)])
    tmp = ascii_to_bytes(extension.upper(), FILE_EXTENSION_LENGTH)
    t1 = sum([x * (0o400 if i % 2 == 1 else 1) for i, x in enumerate(tmp)])
    return (t0 + t1) % 0xFFFF % frame_size


def words_dump(data: t.List[int], words_per_line: int = 8) -> None:
    """
    Display words in hexadecimal
    """
    is_zero = False
    for i in range(0, len(data), words_per_line):
        line = data[i : i + words_per_line]
        if not any(line):
            if not is_zero:
                sys.stdout.write("***\n")
            is_zero = True
            continue
        is_zero = False
        hex_str = "   ".join([f"{x:04X}" for x in line])
        tmp: t.List[int] = functools.reduce(operator.iconcat, [(x % 256, x >> 8) for x in line], [])
        ascii_str = "".join([chr(x) if 32 <= x <= 126 else "." for x in tmp])
        sys.stdout.write(f"{i:>6X}   {hex_str.ljust(3 * words_per_line)} {ascii_str}\n")


class DGDOSBitmap:

    fs: "DGDOSFilesystem"
    blocks: t.List[int]
    bitmaps: t.List[int]
    num_of_words: int

    def __init__(self, fs: "DGDOSFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "DGDOSFilesystem", parent_dir: t.Optional["UserFileDescriptor"] = None) -> "DGDOSBitmap":
        """
        Read the bitmap blocks
        """
        self = DGDOSBitmap(fs)
        entry = self.fs.get_ufd(parent_dir, MAP_DR)
        if not entry:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), MAP_DR)
        self.num_of_words = entry.get_size() // 2
        self.blocks = list(entry.blocks())
        self.bitmaps = []
        for block in self.blocks:
            words = fs.read_16bit_words_block(block)
            if not words:
                raise OSError(errno.EIO, f"Failed to read block {block}")
            self.bitmaps.extend(words)
        self.bitmaps = self.bitmaps[: self.num_of_words]
        return self

    def write(self) -> None:
        """
        Write the bitmap blocks to the disk
        """
        for i, block in enumerate(self.blocks):
            words = self.bitmaps[i * BLOCK_SIZE // 2 : (i + 1) * BLOCK_SIZE // 2]
            if len(words) != BLOCK_SIZE // 2:
                words += [0] * (BLOCK_SIZE // 2 - len(words))
            self.fs.write_16bit_words(words, block)

    @property
    def total_bits(self) -> int:
        """
        Return the bitmap length in bit
        """
        return len(self.bitmaps) * 16

    def is_free(self, block_number: int) -> bool:
        """
        Check if a block is free
        """
        if block_number < SCPPA:
            return False
        bit_index = block_number - SCPPA
        int_index = bit_index // 16
        bit_position = bit_index % 16
        bit_value = self.bitmaps[int_index]
        return (bit_value & (1 << bit_position)) == 0

    def set_free(self, block_number: int) -> None:
        """
        Mark a block as free
        """
        if block_number < SCPPA:
            return
        bit_index = block_number - SCPPA
        int_index = bit_index // 16
        bit_position = bit_index % 16
        self.bitmaps[int_index] &= ~(1 << bit_position)

    def set_used(self, block_number: int) -> None:
        """
        Allocate a block
        """
        if block_number < SCPPA:
            return
        bit_index = block_number - SCPPA
        int_index = bit_index // 16
        bit_position = bit_index % 16
        self.bitmaps[int_index] |= 1 << bit_position

    def max_contiguous_blocks(self) -> int:
        """
        Find the maximum number of contiguous blocks
        """
        current_size = 0
        max_size = 0
        for block in range(SCPPA, self.total_bits + SCPPA):
            if self.is_free(block):
                current_size += 1
                max_size = max(max_size, current_size)
            else:
                current_size = 0
        return max_size

    def find_contiguous_blocks(self, size: int) -> int:
        """
        Find contiguous blocks, return the first block number
        """
        current_size = 0
        start_index = -1
        for block in range(SCPPA, self.total_bits + SCPPA):
            if self.is_free(block):
                if current_size == 0:
                    start_index = block
                current_size += 1
                if current_size == size:
                    return start_index
            else:
                current_size = 0
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
            for block in range(SCPPA, self.total_bits + SCPPA):
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
        return len(self.bitmaps) * 16 - self.used()

    def __str__(self) -> str:
        free = self.free()
        used = self.used()
        max_contiguous_blocks = self.max_contiguous_blocks()
        return f"LEFT: {free:<6} USED: {used:<6} MAX. CONTIGUOUS: {max_contiguous_blocks:6}"


class DGDOSFile(AbstractFile):
    entry: "UserFileDescriptor"
    closed: bool

    def __init__(self, entry: "UserFileDescriptor", file_mode: t.Optional[str] = None):
        self.entry = entry
        self.closed = False
        self.file_mode = file_mode or IMAGE

    def read_16bit_words_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> t.List[int]:
        """
        Read 16bit words from file
        """
        buffer = self.read_block(block_number, number_of_blocks)
        if len(buffer) < BLOCK_SIZE:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return list(struct.unpack("<256H", buffer))

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
            buffer = swap_bytes(self.entry.fs.read_block(disk_block_number))
            data.extend(buffer)
        # Convert to ASCII if needed
        if self.file_mode == ASCII:
            return bytes([0x0A if x == 0x0D else x for x in data])
        else:
            return bytes(data)

    def write_16bit_words(
        self,
        words: t.List[int],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write 16bit words to the file
        """
        buffer = struct.pack("<256H", *words)
        self.write_block(buffer, block_number, number_of_blocks)

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
        # Convert to ASCII if needed
        if self.file_mode == ASCII:
            buffer = bytes([0x0D if x == 0x0A else x for x in buffer])
        block_size = self.get_block_size()
        # Get the blocks to be written
        blocks = list(self.entry.blocks())[block_number : block_number + number_of_blocks]
        # Write the blocks
        for i, disk_block_number in enumerate(blocks):
            data = swap_bytes(buffer[i * block_size : (i + 1) * block_size])
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

    def close(self) -> None:
        """
        Close the file
        """
        self.closed = True

    def __str__(self) -> str:
        return self.entry.fullname


class UserFileDescriptor(AbstractDirectoryEntry):
    """
    User File Descriptor

    Word
        +-------------------------------------+
     0  |               File                  |
     4  |               name                  |
        +-------------------------------------+
     5  |            Extension                |
        +-------------------------------------+
     6  |   Attributes and characteristics    |
        +-------------------------------------+
     7  |       Link access attributes        |
        +-------------------------------------+
     8  |    Number of last block in file     |
        +-------------------------------------+
     9  |        Bytes in last block          |
        +-------------------------------------+
    10  |   First Block / Index Block number  |
        +-------------------------------------+
    11  |       Last access year/day          |
        +-------------------------------------+
    12  |    Last modification year/day       |
        +-------------------------------------+
    13  |   Hour and minute of last mod.      |
        +-------------------------------------+
    14  |        Variable information         |
    15  |                                     |
        +-------------------------------------+
    16  |           Use count                 |
        +-------------------------------------+
    17  |       Device code DCT link          |
        +-------------------------------------+

    Device code DCT link:
    - bits 10—16 - device code of device that holds file
    - bits 0—2   - for large disks, contain the high order of the disk address.

    Pag 25
    https://bitsavers.trailing-edge.com/pdf/dg/software/rdos/093-400027-00_RDOS_SystemReference_Oct83.pdf
    """

    sys_dir_block: "SystemDirectoryBlock"
    parent: t.Optional["UserFileDescriptor"]  # Parent directory/partition
    filename: str = ""
    extension: str = ""
    attributes: int = 0
    link_access_attributes: int = 0
    number_of_last_block: int = 0
    bytes_in_last_block: int = 0
    address: int = 0  # first block address for a sequential/contiguous file, index address for a random file
    last_access_date: int = 0  # days since 1967-12-31
    last_modification_date: int = 0  # days since 1967-12-31
    last_modification_time: int = 0  # hour (high byte) and minute (low byte)
    var1: int = 0
    var2: int = 0
    use_count: int = 0
    device_code: int = 0
    target: str = ""  # link target

    def __init__(self, sys_dir_block: "SystemDirectoryBlock", parent: t.Optional["UserFileDescriptor"] = None):
        self.sys_dir_block = sys_dir_block
        self.parent = parent

    @classmethod
    def read(
        cls,
        sys_dir_block: "SystemDirectoryBlock",
        parent: t.Optional["UserFileDescriptor"],
        buffer: bytes,
        position: int,
    ) -> "UserFileDescriptor":  # DOS Course Handouts, Pag 14
        # https://bitsavers.trailing-edge.com/pdf/dg/software/rdos/093-400027-00_RDOS_SystemReference_Oct83.pdf
        self = UserFileDescriptor(sys_dir_block, parent)
        (
            filename,
            extension,
            self.attributes,  #              1 word  Attributes and characteristics
            self.link_access_attributes,  #  1 word  Link access attributes
            self.number_of_last_block,  #    1 word  Number of last block in file
            self.bytes_in_last_block,  #     1 word  Byte count in last block
            self.address,  #                 1 byte  Address
            self.last_access_date,  #        1 byte  Year and day of last access
            self.last_modification_date,  #  1 word  Year and day of last modification
            self.last_modification_time,  #  1 word  Hour and minute of last modification
            self.var1,  #                    1 word  UFD variable information
            self.var2,  #                    1 word  UFD variable information
            self.use_count,  #               1 word  Use count
            self.device_code,  #             1 word  Device code DCT link
        ) = struct.unpack_from(UFD_ENTRY_FORMAT, buffer, position)
        if filename[0:2] == b'\0\0':  # deleted file/empty entry
            self.filename = ""
            self.extension = ""
        else:
            self.filename = bytes_to_ascii(filename)
            self.extension = bytes_to_ascii(extension)
        if self.is_link:
            (
                _,  # filename
                _,  # extension
                _,  # attributes,
                link_dir,
                link_name,
                link_ext,
            ) = struct.unpack_from(UFD_LINK_ENTRY_FORMAT, buffer, position)
            link_dir = bytes_to_ascii(link_dir)
            link_name = bytes_to_ascii(link_name)
            link_ext = bytes_to_ascii(link_ext)
            if link_dir:
                self.target = f"{link_dir}:{link_name}.{link_ext}"
            else:
                self.target = f"{link_name}.{link_ext}"
            self.number_of_last_block = 0
            self.bytes_in_last_block = 0
            self.last_modification_date = 0
            self.last_modification_time = 0
            self.last_access_date = 0
            self.var1 = 0
            self.var2 = 0
            self.use_count = 0
            self.device_code = 0
            pass
        return self

    @classmethod
    def create(
        cls,
        fs: "DGDOSFilesystem",
        parent: t.Optional["UserFileDescriptor"],
        filename: str,
        length: int,  # Length in blocks
        bitmap: "DGDOSBitmap",
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # optional creation date
        file_type: t.Optional[str] = None,  # optional file type
        length_bytes: t.Optional[int] = None,  # optional length int bytes
    ) -> "UserFileDescriptor":
        """
        Create a new regular file
        """
        raw_file_type = rdos_get_file_type_id(file_type)
        if raw_file_type == RANDOM_FILE_TYPE:
            attributes = ATRAN
            index_blocks = (length - 1) // INDEX_ENTRIES + 1
            blocks_used = length + index_blocks
        elif raw_file_type == CONTIGUOUS_FILE_TYPE:
            attributes = ATCON
            blocks_used = length
        else:  # SEQUENTIAL_FILE_TYPE:
            attributes = 0
            blocks_used = length
        # Check the filename
        filename = rdos_canonical_filename(filename)
        if filename in [SYS_DR, MAP_DR]:
            raise OSError(errno.EEXIST, os.strerror(errno.EEXIST), filename)
        try:
            filename, extension = filename.split(".", 1)
        except Exception:
            extension = ""
        # Check free space and allocate blocks
        if raw_file_type == CONTIGUOUS_FILE_TYPE:
            allocated_blocks = bitmap.allocate(blocks_used, contiguous=True)
        else:
            if bitmap.free() < blocks_used:
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            allocated_blocks = bitmap.allocate(blocks_used)
        # Get a free System Directory entry
        system_directory = SystemDirectory(fs, parent)
        entry = system_directory.get_free_entry(hash=filename_hash(filename, extension, fs.frame_size))
        if entry is None:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), "No free entries in SYS.DR")
        # Create the entry
        entry.filename = filename
        entry.extension = extension
        entry.attributes = attributes
        entry.address = allocated_blocks[0]
        entry.number_of_last_block = length - 1
        if length_bytes is not None:
            entry.bytes_in_last_block = length_bytes % BLOCK_SIZE
            if entry.bytes_in_last_block == 0:
                entry.bytes_in_last_block = BLOCK_SIZE
        else:
            entry.bytes_in_last_block = BLOCK_SIZE
        if creation_date is None:
            creation_date = datetime.now()
        entry.last_modification_date, entry.last_modification_time = date_to_rdos(creation_date)  # type: ignore
        entry.last_access_date = entry.last_modification_date
        # TODO
        entry.device_code = 27  # TODO
        # Block allocation
        if entry.is_random:  # Random file
            index_blocks = (length - 1) // INDEX_ENTRIES + 1  # Number of index blocks
            for i in range(index_blocks):
                words = allocated_blocks[index_blocks + i * INDEX_ENTRIES : index_blocks + (i + 1) * INDEX_ENTRIES]
                words = words + [0] * (INDEX_ENTRIES - len(words))
                if i < index_blocks - 1:
                    words.append(allocated_blocks[i + 1])  # Link to next index block
                else:
                    words.append(0)  # Last block
                index_block_number = allocated_blocks[i]
                fs.write_16bit_words(words, index_block_number)
        elif entry.is_sequential:  # Sequential file
            for i, block_number in enumerate(allocated_blocks):
                words = [0] * (BLOCK_SIZE // 2 - 1)
                if i < len(allocated_blocks) - 1:
                    words.append(allocated_blocks[i + 1])
                else:
                    words.append(0)  # Last block
                fs.write_16bit_words(words, block_number)
        # Write the entry
        entry.sys_dir_block.number_of_files += 1
        entry.sys_dir_block.write()
        return entry

    def write_buffer(self, buffer: bytearray, position: int) -> None:
        """
        Write the UFD entry to the buffer
        """
        filename = ascii_to_bytes(self.filename, FILE_NAME_LENGTH)
        extension = ascii_to_bytes(self.extension, FILE_EXTENSION_LENGTH)
        if self.is_link:
            try:
                link_dir, link_name = self.target.split(":", 1)
            except Exception:
                link_dir = ""
                link_name = self.target
            try:
                link_name, link_ext = link_name.split(".", 1)
            except Exception:
                link_ext = ""
            buffer[position : position + UFD_LINK_ENTRY_LEN] = struct.pack(
                UFD_LINK_ENTRY_FORMAT,
                filename,
                extension,
                self.attributes,
                ascii_to_bytes(link_dir, FILE_NAME_LENGTH),
                ascii_to_bytes(link_name, FILE_NAME_LENGTH),
                ascii_to_bytes(link_ext, FILE_EXTENSION_LENGTH),
            )
        else:
            buffer[position : position + UFD_ENTRY_LEN] = struct.pack(
                UFD_ENTRY_FORMAT,
                filename,
                extension,
                self.attributes,
                self.link_access_attributes,
                self.number_of_last_block,
                self.bytes_in_last_block,
                self.address,
                self.last_access_date,
                self.last_modification_date,
                self.last_modification_time,
                self.var1,
                self.var2,
                self.use_count,
                self.device_code,
            )

    @property
    def fs(self) -> "DGDOSFilesystem":
        """
        Get the filesystem
        """
        return self.sys_dir_block.fs

    @property
    def is_random(self) -> bool:
        """
        Check if the file is random organized
        """
        return self.attributes & ATRAN != 0

    @property
    def is_contiguous(self) -> bool:
        """
        Check if the file is contiguous organized
        """
        return self.attributes & ATCON != 0

    @property
    def is_sequential(self) -> bool:
        """
        Check if the file is sequential organized
        """
        return not self.is_random and not self.is_contiguous and not self.is_link

    @property
    def is_link(self) -> bool:
        """
        Check if the file is a link
        """
        return self.attributes & ATLNK != 0

    @property
    def is_directory(self) -> bool:
        """
        Check if the file is a directory
        """
        return self.attributes & ATDIR != 0

    @property
    def is_partition(self) -> bool:
        """
        Check if the file is a partition
        """
        return self.attributes & ATPAR != 0

    @property
    def is_empty(self) -> bool:
        return self.filename == "" and self.extension == ""

    @property
    def fullname(self) -> str:
        if self.parent:
            return rdos_join(self.parent.fullname, self.basename)
        else:
            return self.basename

    @property
    def basename(self) -> str:
        return f"{self.filename}.{self.extension}"

    @property
    def last_access(self) -> t.Optional[date]:
        """
        Last access date
        """
        return rdos_to_date(self.last_access_date)

    @property
    def creation_date(self) -> t.Optional[date]:
        """
        Last modification date
        """
        return rdos_to_date(self.last_modification_date, self.last_modification_time)

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        if self.bytes_in_last_block == 0:
            return self.number_of_last_block
        else:
            return self.number_of_last_block + 1

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.number_of_last_block * BLOCK_SIZE + self.bytes_in_last_block

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        if self.is_sequential:  # Sequential file block size
            if self.fs.double_addressing:
                return SEQENTIAL_BLOCK_SIZE_LARGE_DISK
            else:
                return SEQENTIAL_BLOCK_SIZE
        else:
            return BLOCK_SIZE

    def filename_hash(self) -> int:
        if self.is_empty:
            return 0
        return filename_hash(self.filename, self.extension, self.fs.frame_size)

    def delete(self) -> bool:
        """
        Delete the file
        """
        # If this is a directory, delete the directory entries
        if self.is_directory:
            for subentry in self.fs.read_dir_entries(self):
                if subentry.basename != MAP_DR and self.address != subentry.address:
                    subentry.delete()
        # Deallocate the blocks
        bitmap = self.fs.read_bitmap(self.parent)
        for block_number in self.blocks(include_indexes=True):
            bitmap.set_free(block_number)
        bitmap.write()
        # Deallocate the entry
        self.filename = ""
        self.extension = ""
        self.attributes = 0
        self.link_access_attributes = 0
        self.number_of_last_block = 0
        self.address = 0
        self.last_access_date = 0
        self.last_modification_date = 0
        self.last_modification_time = 0
        self.var1 = 0
        self.var2 = 0
        self.use_count = 0
        self.device_code = 0
        # Decrement the number of files in the System Directory Block
        if self.sys_dir_block.number_of_files > 0:
            self.sys_dir_block.number_of_files -= 1
        # Write the System Directory Block
        self.sys_dir_block.write()
        return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        self.sys_dir_block.write()
        return True

    def examine(self) -> str:
        if self.is_directory:  # Directory
            file_type = "Directory"
        elif self.is_partition:  # Partition
            file_type = "Partition"
        elif self.is_link:  # Link entry
            file_type = "Link"
        elif self.is_random:  # Random organized file
            file_type = "Random file"
        elif self.is_contiguous:  # Contiguous organized file
            file_type = "Contiguous file"
        else:  # Sequential organized file
            file_type = "Sequential file"
        if self.is_link:
            data: t.Dict[str, t.Any] = {
                "Filename": self.fullname,
                "File type": file_type,
                "Creation date": str(self.creation_date),
                "Target": self.target,
            }
        else:
            data = {
                "Filename": self.fullname,
                "File type": file_type,
                "Creation date": str(self.creation_date),
                "Last access": str(self.last_access),
                "Address": self.address,
                "Blocks ": self.get_length(),
                "File size": f"{self.get_size()} ({self.number_of_last_block} blocks + {self.bytes_in_last_block} bytes)",
                "Write protected": self.attributes & ATWP != 0,
                "Read protected": self.attributes & ATRP != 0,
                "Immutable attribs": self.attributes & ATCHA != 0,
                "Permanent": self.attributes & ATPER != 0,
                "Link attributes": format_attr(self.link_access_attributes),
                "Blocks": list(self.blocks()),
            }
        data["Filename hash"] = self.filename_hash()
        return dump_struct(data) + "\n"

    def open(self, file_mode: t.Optional[str] = None) -> DGDOSFile:
        """
        Open a file
        """
        return DGDOSFile(self, file_mode)

    def blocks(self, include_indexes: bool = False) -> t.Iterator[int]:
        if self.is_random:
            # Random file
            # Pag 22
            # https://bitsavers.trailing-edge.com/pdf/dg/software/rdos/093-400027-00_RDOS_SystemReference_Oct83.pdf
            index_block_number = self.address
            while index_block_number:
                if include_indexes:
                    yield index_block_number
                words = self.fs.read_16bit_words_block(index_block_number)
                index_block_number = words.pop()  # Link to the next file index block
                for block_address in words:
                    if block_address != 0:
                        yield block_address
        elif self.is_contiguous:
            # Contiguous file
            # Pag 23
            # https://bitsavers.trailing-edge.com/pdf/dg/software/rdos/093-400027-00_RDOS_SystemReference_Oct83.pdf
            length = self.get_length()
            for block_address in range(self.address, self.address + length):
                yield block_address
        else:
            # Sequential file
            # Pag 21
            # https://bitsavers.trailing-edge.com/pdf/dg/software/rdos/093-400027-00_RDOS_SystemReference_Oct83.pdf
            block_number = self.address
            while block_number:
                yield block_number
                words = self.fs.read_16bit_words_block(block_number)
                block_number = words[-1]
                if self.fs.double_addressing:
                    block_number += words[-2] << 16
                    # print(">>>", words[-2], words[-1], block_number, block_number)
                if block_number == 0:
                    break

    def __str__(self) -> str:
        attr = format_attr(self.attributes, long=True)
        if self.is_link:
            return f"{self.filename:>10s}.{self.extension:<2s} {attr:<12}  -> {self.target}"
        else:
            uftlkl = format_attr(self.link_access_attributes)
            if uftlkl:
                attr = f"{attr}/{uftlkl}"
            creation_date = self.creation_date.strftime("%m/%d/%y") if self.creation_date else ""
            return (
                f"{self.filename:>10s}.{self.extension:<2s} "
                f"{attr:<12} "
                f"{self.get_size():>10d}  "
                f"{creation_date:<8}  "
                f"{self.address:>6} {self.use_count:>5} {self.device_code:>5}"
            )

    def __repr__(self) -> str:
        return str(self)


class SystemDirectoryBlock:
    """
    System Directory Block

    Word
          +-------------------------------------+
    0     |          Number of files            |
          +-------------------------------------+
    1     |    User File Descriptor (UFD) #0    |
    17    | .                                   |
          +-------------------------------------+
    18    |    User File Descriptor (UFD) #1    |
    35    | .                                   |
          +-------------------------------------+
    36    |              UFDs ...               |
          | .                                   |
          +-------------------------------------+
    254   |     Max number UFD in this block    |
          +-------------------------------------+

    RDOS System Reference - Pag 24
    https://bitsavers.trailing-edge.com/pdf/dg/software/rdos/093-400027-00_RDOS_SystemReference_Oct83.pdf
    """

    sys_dir: "SystemDirectory"  # System Directory
    block_number: int = 0  # Block number
    number_of_files: int = 0  # Number of files in this block directory
    max_ufd: int = 0  # Max number of UFD in this block
    entries_list: t.List["UserFileDescriptor"] = []  # List of UFD entries

    def __init__(self, sys_dir: "SystemDirectory"):
        self.sys_dir = sys_dir

    @classmethod
    def read(cls, sys_dir: "SystemDirectory", block_number: int) -> "SystemDirectoryBlock":
        self = cls(sys_dir)
        self.block_number = block_number
        buffer = self.fs.read_block(block_number)
        self.number_of_files = bytes_to_word(buffer, 0)  # first word
        self.max_ufd = bytes_to_word(buffer[-2:], 0)  # last word
        self.entries_list = []
        for position in range(2, BLOCK_SIZE - 4 - UFD_ENTRY_LEN, UFD_ENTRY_LEN):
            ufd = UserFileDescriptor.read(self, sys_dir.dir_ufd, buffer, position)
            self.entries_list.append(ufd)
        return self

    def to_bytes(self) -> bytes:
        """
        Write the UFD entries to a buffer
        """
        buffer = bytearray(BLOCK_SIZE)
        # Update the number of files
        self.number_of_files = len([x for x in self.entries_list if not x.is_empty])
        buffer[0:2] = word_to_bytes(self.number_of_files)
        # Write the UFD entries
        for i, entry in enumerate(self.entries_list):
            entry.write_buffer(buffer, 2 + i * UFD_ENTRY_LEN)
        # Write the last word
        buffer[-2:] = word_to_bytes(self.max_ufd)
        return bytes(buffer)

    def write(self) -> None:
        """
        Write the System Directory Block to the disk
        """
        self.fs.write_block(self.to_bytes(), self.block_number)

    def iterdir(self) -> t.Iterator["UserFileDescriptor"]:
        """
        Iterate over directory entries
        """
        for entry in self.entries_list:
            if not entry.is_empty:
                yield entry

    @property
    def fs(self) -> "DGDOSFilesystem":
        """
        Get the filesystem
        """
        return self.sys_dir.fs

    def __str__(self) -> str:
        return f"Block {self.block_number} ({self.number_of_files} entries, {self.max_ufd})"


class SystemDirectory:
    """
    System Directory (SYS.DR)
    """

    fs: "DGDOSFilesystem"
    dir_ufd: t.Optional["UserFileDescriptor"] = None  # Directory entry

    def __init__(self, fs: "DGDOSFilesystem", dir_ufd: t.Optional["UserFileDescriptor"] = None):
        self.fs = fs
        self.dir_ufd = dir_ufd

    def blocks(self) -> t.Iterator[int]:
        """
        Get the blocks of the System Directory
        (SYS.DR is a random file)
        """
        index_block_number = self.dir_ufd.address if self.dir_ufd else SYS_DR_BLOCK
        while index_block_number:
            words = self.fs.read_16bit_words_block(index_block_number)
            index_block_number = words.pop()
            for block_number in words:
                if block_number != 0:
                    yield block_number

    def read_system_directory(self) -> t.Iterator[SystemDirectoryBlock]:
        """
        Read System Directory blocks
        """
        for block_number in self.blocks():
            yield SystemDirectoryBlock.read(self, block_number)

    def get_free_entry(self, hash: int) -> t.Optional["UserFileDescriptor"]:
        """
        Get a free System Directory entry
        """
        blocks = list(self.blocks())
        # TODO check
        for d in (0, -1):
            block_number = blocks[(hash + d) % len(blocks)]
            sys_dir_block = SystemDirectoryBlock.read(self, block_number)
            for entry in sys_dir_block.entries_list:
                if entry.is_empty:
                    return entry
        # for sys_dir_block in self.read_system_directory():
        #     for entry in sys_dir_block.entries_list:
        #         if entry.is_empty:
        #             return entry
        # TODO overlay
        return None


class DiskInformationBlock:
    """
    Disk Information Block

    Word
          +-------------------------------------+
    0     |          Revision number            |
          +-------------------------------------+
    1     |             Checksum                |
          +-------------------------------------+
    2     |         Number of heads             |
          +-------------------------------------+
    3     |       Number of sectors/track       |
          +-------------------------------------+
    4     |   Number of blocks/device high      |
          +-------------------------------------+
    5     |   Number of blocks/device low       |
          +-------------------------------------+
    6     |            Frame size               |
          +-------------------------------------+
    7     |           Characteristics           |
          +-------------------------------------+

    Frame size is the number of blocks initially allocated
    for the system directory.
    """

    fs: "DGDOSFilesystem"
    revision: int = 0
    checksum: int = 0
    heads: int = 0
    sectors: int = 0
    blocks: int = 0
    frame_size: int = 0
    characteristics: int = 0

    def __init__(self, fs: "DGDOSFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "DGDOSFilesystem") -> "DiskInformationBlock":
        self = cls(fs)
        (
            self.revision,
            self.checksum,
            self.heads,
            self.sectors,
            blocks_h,
            blocks_l,
            self.frame_size,
            self.characteristics,
        ) = self.fs.read_16bit_words_block(DISK_ID_BLOCK)[0:8]
        self.blocks = (blocks_h << 16) + blocks_l + SCPPA
        return self

    def is_double_addressing(self) -> bool:
        """
        Check if the disk requires double addressing
        """
        return self.characteristics & CHDOBL != 0

    def is_top_loader(self) -> bool:
        """
        Check if the disk is a top loader (dual-platter disk subsystem)
        """
        return self.characteristics & CHTOPL != 0

    def __str__(self) -> str:
        return (
            "\n*Disk Information Block\n"
            f"Revision:          {self.revision}\n"
            f"Checksum:          ${self.checksum:04x}\n"
            f"Heads:             {self.heads}\n"
            f"Sectors/track:     {self.sectors}\n"
            f"Blocks:            {self.blocks}\n"
            f"Frame size:        {self.frame_size}\n"
            f"Characteristics:   {self.characteristics:016b} (${self.characteristics:04x})\n"
            f"Double addressing: {self.is_double_addressing()}\n"
            f"Top loader:        {self.is_top_loader()}\n"
        )


class DGDOSFilesystem(AbstractFilesystem, BlockDevice):
    """
    DGDOS Filesystem


    Block
          +-------------------------------------+
    0     |            Bootstrap block          |
    1     |                                     |
          +-------------------------------------+
    2     |                                     |
          +-------------------------------------+
    3     |               Disk ID               |
          +-------------------------------------+
    4     |         Bad block pool index        |
    5     |                                     |
          +-------------------------------------+
    6     |    First index block of SYS.DR      |
          +-------------------------------------+
    7     |               Swap                  |
    14    |                                     |
          +-------------------------------------+
    15    |           MAP.DR blocks             |
    n     |                                     |
          +-------------------------------------+
    n+1   |            BOOTSYS.OL               |
    m     |                                     |
          +--------------------------------------

    RDOS System Reference - Pag 17
    https://bitsavers.trailing-edge.com/pdf/dg/software/rdos/093-400027-00_RDOS_SystemReference_Oct83.pdf
    """

    fs_name = "rdos"
    fs_description = "Data General Nova DOS/RDOS Filesystem"

    double_addressing: bool = False  # Disk requires double addressing
    top_loader: bool = False  # Disk is a top loader
    frame_size: int = 1  # Frame size
    heads: int = 0  # Number of heads
    sectors_per_track: int = 0  # Number of sectors per track
    swap: bool = False  # Swap bytes
    pwd: str = "/"  # Current working directory

    def physical_block(self, logical_block: int) -> int:
        """
        Convert logical block number to physical block number
        """
        # TODO check if the condition is correct
        if self.heads == 0 or self.sectors_per_track == 0 or not self.top_loader:
            return logical_block
        else:
            h = self.heads * self.sectors_per_track
            return logical_block + (logical_block // h) * h

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        block_number = self.physical_block(block_number)
        buffer = super().read_block(block_number, number_of_blocks)
        if self.swap:
            buffer = swap_bytes(buffer)
        return buffer

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        block_number = self.physical_block(block_number)
        if self.swap:
            buffer = swap_bytes(buffer)
        super().write_block(buffer, block_number, number_of_blocks)

    def read_16bit_words_block(self, block_number: int) -> t.List[int]:
        """
        Read 256 16bit words from a block
        """
        buffer = self.read_block(block_number)
        if len(buffer) < BLOCK_SIZE:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return list(struct.unpack("<256H", buffer))

    def write_16bit_words(self, words: t.List[int], block_number: int) -> None:
        """
        Write 256 16bit words to a block
        """
        buffer = struct.pack("<256H", *words)
        self.write_block(buffer, block_number)

    def check_map_dr(self) -> bool:
        """
        Check if the MAP.DR file is present
        """
        try:
            for tmp in self.read_dir_entries():
                if tmp.basename == MAP_DR:
                    return True
        except Exception:
            pass
        return False

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        # Read the Disk Information Block
        disk_id = DiskInformationBlock.read(self)
        if disk_id.revision > 16:
            self.swap = True
            disk_id = DiskInformationBlock.read(self)
        self.double_addressing = disk_id.is_double_addressing()
        self.top_loader = disk_id.is_top_loader()
        self.frame_size = disk_id.frame_size
        self.heads = disk_id.heads
        self.sectors_per_track = disk_id.sectors
        if strict:
            # Check if the file is a valid DOS/RDOS filesystem
            if not self.check_map_dr():
                raise OSError(errno.EIO, "MAP.DR not found")
        return self

    def read_dir_entries(
        self,
        dir_ufd: t.Optional["UserFileDescriptor"] = None,
    ) -> t.Iterator["UserFileDescriptor"]:
        """
        Read System Directory entries
        """
        system_directory = SystemDirectory(self, dir_ufd)
        for sys_dir_block in system_directory.read_system_directory():
            yield from sys_dir_block.iterdir()

    def read_bitmap(self, parent: t.Optional[UserFileDescriptor] = None) -> DGDOSBitmap:
        """
        Read the bitmap
        """
        if parent is None:
            parent = self.get_file_entry(self.pwd)
        return DGDOSBitmap.read(self, parent)

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["UserFileDescriptor"]:
        if pattern:
            pattern = pattern.upper()
        if not pattern and expand:
            pattern = "*"
        if pattern and pattern.startswith("/"):
            absolute_path = pattern
        else:
            absolute_path = unix_join(self.pwd, pattern or "")
        if self.isdir(absolute_path):
            if not expand:
                yield self.get_file_entry(absolute_path)
                return
            dirname = pattern
            pattern = "*"
        else:
            dirname, pattern = unix_split(absolute_path)
        if dirname == "/":  # Root directory
            dir_ufd: t.Optional[UserFileDescriptor] = None
        else:
            dir_ufd = self.get_file_entry(dirname)  # type: ignore
        for entry in self.read_dir_entries(dir_ufd):
            if filename_match(entry.basename, pattern, wildcard):
                yield entry

    def get_ufd(self, dir_ufd: t.Optional[UserFileDescriptor], basename: str) -> UserFileDescriptor:
        """
        Get User File Descriptor for a file in a directory
        """
        basename = rdos_canonical_filename(basename).rstrip(".")
        for entry in self.read_dir_entries(dir_ufd):
            if filename_match(entry.basename.rstrip("."), basename, wildcard=False):
                return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), basename)

    @property
    def entries_list(self) -> t.Iterator["UserFileDescriptor"]:
        yield from self.read_dir_entries()

    def get_file_entry(self, fullname: str) -> UserFileDescriptor:
        """
        Get the directory entry for a file
        """
        fullname = unix_join(self.pwd, fullname) if not fullname.startswith("/") else fullname
        parts = [x for x in fullname.split("/") if x]
        if not parts:
            parts = [SYS_DR]  # Default to SYS.DR
        entry: t.Optional[UserFileDescriptor] = None
        for i, part in enumerate(parts):
            if entry is not None and not (entry.is_directory or entry.is_partition):  # is a directory?
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
            entry = self.get_ufd(entry, part)
        if entry is None:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return entry

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: t.Optional[date] = None,
        file_type: t.Optional[str] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        """
        Write content to a file
        """
        raw_file_type = rdos_get_file_type_id(file_type)
        # Determine the number of blocks
        if raw_file_type == SEQUENTIAL_FILE_TYPE:
            block_size = SEQENTIAL_BLOCK_SIZE_LARGE_DISK if self.double_addressing else SEQENTIAL_BLOCK_SIZE
        else:
            block_size = BLOCK_SIZE
        number_of_blocks = int(math.ceil(len(content) * 1.0 / block_size))
        # Create the file
        entry = self.create_file(
            fullname=fullname,
            number_of_blocks=number_of_blocks,
            creation_date=creation_date,
            file_type=file_type,
            length_bytes=len(content),
        )
        # Write the content to the file
        if entry is not None:
            f = entry.open(file_mode)
            try:
                f.write(content)
            finally:
                f.close()

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,
        length_bytes: t.Optional[int] = None,  # optional length in bytes
    ) -> t.Optional[UserFileDescriptor]:
        """
        Create a new file with a given length in number of blocks
        """
        fullname = rdos_normpath(fullname, self.pwd)
        dirname, filename = unix_split(fullname)
        # Delete the file if it already exists
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass
        # Get parent directory
        try:
            parent: UserFileDescriptor = self.get_file_entry(dirname)
        except FileNotFoundError:
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), dirname)
        # Create the file
        bitmap = self.read_bitmap(parent)
        entry = UserFileDescriptor.create(
            fs=self,
            parent=parent,
            filename=filename,
            length=number_of_blocks,
            bitmap=bitmap,
            creation_date=creation_date,
            # access=access if access is not None else DEFAULT_ACCESS,
            file_type=file_type,
            # aux_type=aux_type if aux_type is not None else 0,
            length_bytes=length_bytes if length_bytes is not None else number_of_blocks * BLOCK_SIZE,
        )
        bitmap.write()
        return entry

    def isdir(self, fullname: str) -> bool:
        try:
            entry = self.get_file_entry(fullname)
            return entry.is_directory or entry.is_partition
        except FileNotFoundError:
            return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        for x in self.filter_entries_list(pattern, include_all=True, wildcard=True):
            if options.get("brief"):
                # For brief mode, print only the file name
                sys.stdout.write(f"{x.basename}\n")
            elif x.is_link:
                # Print link information
                # filename, target
                sys.stdout.write(f"{x.basename:<13s}             {x.target}\n")
            else:
                # Print file information
                # filename, byte length, attributes, last modification date, last access date, address, use count
                attr = format_attr(x.attributes)
                uftlkl = format_attr(x.link_access_attributes)
                if uftlkl:
                    attr = f"{attr}/{uftlkl}"
                creation_date = x.creation_date.strftime("%m/%d/%y %H:%M") if x.creation_date else ""
                access_date = x.last_access.strftime("%m/%d/%y") if x.last_access else ""
                sys.stdout.write(
                    f"{x.basename:<13s}{x.get_size():>10d}  {attr:<7} {creation_date:<14} {access_date:<8}  [{x.address:06o}] {x.use_count:>5}\n"
                )
        sys.stdout.write("\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if options.get("diskid"):
            # Display the filesystem information
            disk_id = DiskInformationBlock.read(self)
            sys.stdout.write(str(disk_id))
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
            sys.stdout.write("\n*System Directory\n")
            system_directory = SystemDirectory(self)
            for i, sys_dir_block in enumerate(system_directory.read_system_directory()):
                if options.get("full"):
                    sys.stdout.write(f"** #{i} {sys_dir_block}\n")
                for entry in sys_dir_block.entries_list:
                    if options.get("full") or not entry.is_empty:
                        sys.stdout.write(f"{entry} -- {entry.filename_hash() }\n")
                        # sys.stdout.write(f"{entry}\n")
        else:
            # Display the file information
            entry = self.get_file_entry(arg)  # type: ignore
            sys.stdout.write(entry.examine())

    def dump(self, fullname: t.Optional[str], start: t.Optional[int] = None, end: t.Optional[int] = None) -> None:
        """Dump the content of a file or a range of blocks"""
        if fullname:
            if start is None:
                start = 0
            if end is None:
                entry = self.get_file_entry(fullname)
                end = entry.get_length() - 1
            f: DGDOSFile = t.cast(DGDOSFile, self.open_file(fullname, file_mode=IMAGE))
            try:
                for block_number in range(start, end + 1):
                    data = f.read_16bit_words_block(block_number)
                    sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                    words_dump(data)
            finally:
                f.close()
        else:
            if start is None:
                start = 0
            if end is None:
                if start == 0:
                    end = self.get_size() // BLOCK_SIZE
                else:
                    end = start
            for block_number in range(start, end + 1):
                data = self.read_16bit_words_block(block_number)
                sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                words_dump(data)

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def close(self) -> None:
        self.f.close()

    def chdir(self, fullname: str) -> bool:
        """
        Change the current directory
        """
        fullname = rdos_normpath(fullname, self.pwd)
        if not self.isdir(fullname):
            return False
        # Check if the directory is valid
        if any(x == SYS_DR for x in fullname.split("/")):
            return False
        self.pwd = fullname
        return True

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

    def __str__(self) -> str:
        return str(self.f)
