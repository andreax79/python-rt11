# Copyright (C) 2014 Andrea <andrea.bonomi@gmail.com>

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
from datetime import date, datetime

from ..abstract import AbstractBlockFilesystem, XFERXPath, AbstractFile
from ..commons import (
    ASCII,
    BLOCK_SIZE,
    DATA_FORK,
    IMAGE,
    READ_FILE_FULL,
    RESOURCE_FORK,
    dump_struct,
    filename_match,
    format_size,
)
from ..device.abstract import AbstractDevice
from ..device.block import BlockDevice
from .commons import AppleSingle, FinderInfo
from .disk_copy import DiskCopy

__all__ = [
    "MacintoshFilesystem",
    "mfs_to_date",
    "date_to_mfs",
]

SYSTEM_START_BLOCK = 0  # System start block
MASTER_DIRECTORY_BLOCK = 2  # Master directory block

MAP_POSITION = 64  # Position of the block map in the Master Directory Block
VOLUME_INFO_FORMAT = '>HLLH HHHH LLHL HB'
VOLUME_INFO_SIZE = struct.calcsize(VOLUME_INFO_FORMAT)
VOLUME_SIGNATURE = 0xD2D7  # Volume signature
VOLUME_NAME_MAX_LENGTH = MAP_POSITION - VOLUME_INFO_SIZE  # Maximum volume name length
assert VOLUME_INFO_SIZE == 37

FILE_ENTRY_FORMAT = '>BB 4s4sHLH LHLL HLLLLB'
FILE_ENTRY_SIZE = struct.calcsize(FILE_ENTRY_FORMAT)
assert FILE_ENTRY_SIZE == 51

UNUSED_BLOCK = 0  # Block is unused
LAST_BLOCK = 1  # Block is the last block of the file
DIRECTORY_BLOCK = 0xFFF  # Block is a directory block

DATETIME_DELTA = int(math.ceil(365.25 * (1970 - 1904))) * 86400
FILE_NAME_LENGTH = 63

FORKS = [DATA_FORK, RESOURCE_FORK]

DEFAULT_VOLUME_NAME = "XFERX"  # Default volume name
DEFAULT_DIRECTORY_LEN = 12  # Default directory length in blocks
DEFAULT_ALLOCATION_BLOCK_SIZE = 1024  # Default allocation block size in bytes
DEFAULT_CLUMP_SIZE = 8192  # Default clump size in bytes


def mfs_to_date(date: int) -> t.Optional[datetime]:
    """
    Convert Macintosh File System date to datetime
    """
    if date == 0:
        return None
    return datetime.fromtimestamp(date - DATETIME_DELTA)


def date_to_mfs(dt: t.Union[datetime, date, None]) -> int:
    """
    Convert datetime to Macintosh File System date
    """
    if dt is None:
        return 0
    if not isinstance(dt, datetime):
        dt = datetime.combine(dt, datetime.min.time())
    return int(dt.timestamp()) + DATETIME_DELTA


def mfs_canonical_filename(name: t.Optional[str], wildcard: bool = False) -> str:
    """
    Generate the canonical Macintosh File System name
    """
    if not name:
        return ""
    else:
        return name.strip()[:FILE_NAME_LENGTH]


def check_fork(fork: t.Optional[str], default: str = DATA_FORK) -> str:
    """
    Check if the fork is valid
    """
    if not fork:
        return default
    fork = fork.upper()
    if fork not in FORKS:
        raise OSError(errno.EINVAL, f"Invalid fork {fork}, must be one of {','.join(FORKS)}")
    return fork


class File(AbstractFile):
    entry: "MacintoshPath"
    file_mode: str
    fork: str

    def __init__(self, entry: "MacintoshPath", file_mode: t.Optional[str] = None, fork: t.Optional[str] = None):
        self.entry = entry
        self.file_mode = file_mode or IMAGE
        self.fork = check_fork(fork)

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        The size of each block is the allocation block size of the filesystem
        """
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = self.entry.get_length(self.fork)
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.entry.get_length(self.fork)
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        data = bytearray()
        # Get the blocks to be read
        allocation_blocks = list(self.entry.allocation_blocks(self.fork))[
            block_number : block_number + number_of_blocks
        ]
        mul = self.entry.get_block_size() // BLOCK_SIZE
        # Read the blocks
        for allocation_block_number in allocation_blocks:
            disk_block_number = self.fs.allocation_block_num + (allocation_block_number - 2) * mul
            buffer = self.entry.fs.read_block(disk_block_number, mul)
            data.extend(buffer)
        # Convert to ASCII if needed
        if self.file_mode == ASCII:
            data = data.replace(b"\r", b"\n")
        return bytes(data)

    def write_block(
        self,
        buffer: t.Union[bytes, bytearray],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        The size of each block is the allocation block size of the filesystem
        """
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.entry.get_length(self.fork)
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        block_size = self.get_block_size()
        mul = block_size // BLOCK_SIZE
        # Convert to ASCII if needed
        if self.file_mode == ASCII:
            buffer = buffer.replace(b"\n", b"\r")
        # Get the blocks to be written
        allocation_blocks = list(self.entry.allocation_blocks(self.fork))[
            block_number : block_number + number_of_blocks
        ]
        # Write the blocks
        for i, allocation_block_number in enumerate(allocation_blocks):
            data = buffer[i * block_size : (i + 1) * block_size]
            disk_block_number = self.fs.allocation_block_num + (allocation_block_number - 2) * mul
            self.entry.fs.write_block(data, disk_block_number, number_of_blocks=mul)

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.entry.get_size(self.fork)

    def get_block_size(self) -> int:
        """
        Get file allocation block size in bytes
        """
        return self.entry.get_block_size()

    @property
    def fs(self) -> "MacintoshFilesystem":
        """
        Get the filesystem
        """
        return self.entry.fs

    def __str__(self) -> str:
        return self.entry.fullname


class MacintoshPath(XFERXPath):
    """
    File Directory Entry

    Each file entry has an unique file number uses to identify the file.

    Byte
        +----------------------------------------+
     0  | Flags                                  |  1 byte
        +----------------------------------------+
     1  | Version number                         |  1 bytes
        +----------------------------------------+           ---
     2  | File type                              |  4 bytes   |
        |                                        |            |
        +----------------------------------------+            |
     6  | File creator                           |  4 bytes   |
        |                                        |            |  16 bytes
        +----------------------------------------+            |
    10  | Finder flags                           |  2 bytes   |  Finder
        +----------------------------------------+            |  info
    12  | Icon position                          |  4 bytes   |
        |                                        |            |
        +----------------------------------------+            |
    16  | Folder number                          |  2 bytes   |
        +----------------------------------------+           ---
    18  | File number                            |  2 bytes
        +----------------------------------------+
    22  | First allocation block of data fork    |  2 bytes
        +----------------------------------------+
    24  | Logical end-of-file of data fork       |  4 bytes
        +----------------------------------------+
    28  | Physical end-of-file of data fork      |  4 bytes
        +----------------------------------------+
    32  | First allocation block of res. fork    |  2 bytes
        +----------------------------------------+
    34  | Logical end-of-file of resource fork   |  4 bytes
        +----------------------------------------+
    38  | Physical end-of-file of resource fork  |  4 bytes
        +----------------------------------------+
    42  | Date and time of creation              |  4 bytes
        +----------------------------------------+
    46  | Date and time of last modification     |  4 bytes
        +----------------------------------------+
    50  | Length of file name                    |  1 byte
        +----------------------------------------+
    51  | Characters of file name                |
        |                                        |
        +----------------------------------------+

    Inside Macintosh, Pag II-123
    https://www.weihenstephan.org/~michaste/pagetable/mac/Inside_Macintosh.pdf
    """

    fs: "MacintoshFilesystem"
    flags: int  # bit 7=1 if entry used; bit 0=1 if file locked
    version: int  # version number
    raw_file_type: bytes  # file type (4 characters)
    raw_file_creator: bytes  # file creator (4 characters)
    mac_finder_flags: int  # finder flags
    mac_icon_position: int  # icon position
    folder_number: int  # folder number
    file_number: int  # file number
    data_allocation_block: int  # first allocation block of data fork
    data_size: int  # data length in bytes
    data_physical_size: int  # data physical length in bytes
    res_allocation_block: int  # first allocation block of resource fork
    res_size: int  # resource length in bytes
    res_physical_size: int  # resource physical length in bytes
    raw_creation_date: int  # date and time of creation
    raw_last_mod_date: int  # date and time of last modification
    filename: str  # characters of file name
    entry_length: int  # length of the entry in bytes (including filename and padding)

    def __init__(self, fs: "MacintoshFilesystem"):
        self.fs = fs

    @classmethod
    def read(
        cls,
        fs: "MacintoshFilesystem",
        buffer: bytes,
        position: int,
    ) -> "MacintoshPath":
        """
        Read a File Directory Entry from the buffer
        """
        self = MacintoshPath(fs)
        (
            self.flags,  # flags
            self.version,  # version number
            self.raw_file_type,  # file type
            self.raw_file_creator,  # file creator
            self.mac_finder_flags,  # finder flags
            self.mac_icon_position,  # icon position
            self.folder_number,  # folder number
            self.file_number,  # file number
            self.data_allocation_block,  # first allocation block of data fork
            self.data_size,  # data length in bytes
            self.data_physical_size,  # data physical length in bytes
            self.res_allocation_block,  # first allocation block of resource fork
            self.res_size,  # resource length in bytes
            self.res_physical_size,  # resource physical length in bytes
            self.raw_creation_date,  # date and time of creation
            self.raw_last_mod_date,  # date and time of last modification
            filename_length,  # length of file name
        ) = struct.unpack_from(FILE_ENTRY_FORMAT, buffer, position)
        tmp = buffer[position + FILE_ENTRY_SIZE : position + FILE_ENTRY_SIZE + filename_length]
        self.filename = tmp.decode("macroman", errors="ignore").strip("\0")
        self.entry_length = FILE_ENTRY_SIZE + filename_length + ((filename_length + 1) % 2)
        return self

    @classmethod
    def create(
        cls,
        fs: "MacintoshFilesystem",
        fullname: str,
        data_size: int,  # data fork length in bytes
        res_size: int = 0,  # resource fork length in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> "MacintoshPath":
        """
        Create a new regular file
        """
        metadata = metadata or {}
        master_directory_block = MasterDirectoryBlock.read(fs.dev, fs.master_directory_len)
        file_directory = FileDirectory.read(fs)
        self = cls(fs)
        self.filename = mfs_canonical_filename(fullname)
        # version
        try:
            self.version = int(metadata["version"]) & 0xFF  # type: ignore
        except Exception:
            self.version = 0
        # file type
        try:
            self.raw_file_type: str = metadata["file_type"][0:4].encode("macroman", errors="ignore")  # type: ignore
        except Exception:
            self.raw_file_type = b"????"
        # file creator
        try:
            self.raw_file_creator: str = metadata["file_creator"][0:4].encode("macroman", errors="ignore")  # type: ignore
        except Exception:
            self.raw_file_creator = b"????"
        # finder flags
        try:
            self.mac_finder_flags = int(metadata["mac_finder_flags"]) & 0xFFFF  # type: ignore
        except Exception:
            self.mac_finder_flags = 0x100
        # icon position
        try:
            self.mac_icon_position = int(metadata["mac_icon_position"]) & 0xFFFF  # type: ignore
        except Exception:
            self.mac_icon_position = 0
        self.flags = 0x80  # flags
        self.folder_number = 0  # folder number
        # file number
        if file_directory.entries_list:
            self.file_number = max([x.file_number for x in file_directory.entries_list]) + 1
        else:
            self.file_number = 1
        # date and time of creation
        try:
            self.raw_creation_date = date_to_mfs(metadata["creation_date"])  # type: ignore
        except Exception:
            self.raw_creation_date = date_to_mfs(datetime.now())
        # date and time of last modification
        try:
            self.raw_last_mod_date = date_to_mfs(metadata["last_mod_date"])  # type: ignore
        except Exception:
            self.raw_last_mod_date = self.raw_creation_date
        # data fork
        data_number_of_blocks = (data_size + self.fs.allocation_block_size - 1) // self.fs.allocation_block_size
        data_blocks = master_directory_block.allocate(data_number_of_blocks)
        self.data_size = data_size
        self.data_physical_size = data_number_of_blocks * self.fs.allocation_block_size
        self.data_allocation_block = data_blocks[0] if data_blocks else 0
        # resource fork
        res_number_of_blocks = (res_size + self.fs.allocation_block_size - 1) // self.fs.allocation_block_size
        res_blocks = master_directory_block.allocate(res_number_of_blocks)
        self.res_size = res_size
        self.res_physical_size = res_number_of_blocks * self.fs.allocation_block_size
        self.res_allocation_block = res_blocks[0] if res_blocks else 0
        # Update the number of files
        master_directory_block.volume_information.number_of_files += 1
        master_directory_block.volume_information.next_file_number = self.file_number + 1
        # Write the Master Directory Block
        master_directory_block.write()
        # Write the File Directory
        file_directory.entries_list.append(self)
        file_directory.write()
        return self

    def write_buffer(self, buffer: bytearray, position: int) -> int:
        """
        Write the File Directory Entry to the buffer
        Return the new position in the buffer
        """
        self.compute_entry_length()
        if self.is_empty():
            struct.pack_into(">H", buffer, position, 0)
            return position + 2
        filename = self.filename.encode("macroman", errors="ignore")[:FILE_NAME_LENGTH]
        filename_length = len(filename)
        struct.pack_into(
            FILE_ENTRY_FORMAT,
            buffer,
            position,
            self.flags,  # flags
            self.version,  # version number
            self.raw_file_type,  # file type
            self.raw_file_creator,  # file creator
            self.mac_finder_flags,  # finder flags
            self.mac_icon_position,  # icon position
            self.folder_number,  # folder number
            self.file_number,  # file number
            self.data_allocation_block,  # first allocation block of data fork
            self.data_size,  # data length in bytes
            self.data_physical_size,  # data physical length in bytes
            self.res_allocation_block,  # first allocation block of resource fork
            self.res_size,  # resource length in bytes
            self.res_physical_size,  # resource physical length in bytes
            self.raw_creation_date,  # date and time of creation
            self.raw_last_mod_date,  # date and time of last modification
            filename_length,  # length of file name
        )
        buffer[position + FILE_ENTRY_SIZE : position + FILE_ENTRY_SIZE + filename_length] = filename
        return position + self.entry_length

    def is_empty(self) -> bool:
        # flags bit 7 = 1 if entry used
        return (self.flags & 0x80) == 0

    @property
    def fullname(self) -> str:
        return self.filename

    @property
    def name(self) -> str:
        return self.filename

    @property
    def parent(self) -> "FileDirectory":
        """The logical parent"""
        return self.fs.get_root()

    @property
    def creation_date(self) -> t.Optional[datetime]:
        """
        Creation date
        """
        return mfs_to_date(self.raw_creation_date)

    @property
    def last_mod_date(self) -> t.Optional[datetime]:
        """
        Last modification date
        """
        return mfs_to_date(self.raw_last_mod_date)

    @property
    def file_type(self) -> str:
        """
        File type
        """
        return self.raw_file_type.decode("macroman", errors="ignore")

    @property
    def file_creator(self) -> str:
        """
        File creator
        """
        return self.raw_file_creator.decode("macroman", errors="ignore")

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return int(math.ceil(self.get_size(fork) / BLOCK_SIZE))

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get file size in bytes
        """
        fork = check_fork(fork)
        if fork == RESOURCE_FORK:
            return self.res_size
        else:
            return self.data_size

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return self.fs.allocation_block_size

    def resize_fork(self, new_size: int, fork: str = DATA_FORK) -> None:
        """
        Resize the specified fork
        """
        fork = check_fork(fork)
        master_directory_block = MasterDirectoryBlock.read(self.fs.dev, self.fs.master_directory_len)
        if fork == DATA_FORK:
            current_physical_size = self.data_physical_size
            allocation_block_number = self.data_allocation_block
        else:
            current_physical_size = self.res_physical_size
            allocation_block_number = self.res_allocation_block
        current_number_of_blocks = (
            current_physical_size + self.fs.allocation_block_size - 1
        ) // self.fs.allocation_block_size
        new_number_of_blocks = (new_size + self.fs.allocation_block_size - 1) // self.fs.allocation_block_size

        if new_number_of_blocks > current_number_of_blocks:  # Grow the file
            # Need to allocate more blocks
            additional_blocks = new_number_of_blocks - current_number_of_blocks
            new_blocks = master_directory_block.allocate(additional_blocks)
            # Link the new blocks to the existing chain
            if allocation_block_number == 0:
                # No blocks allocated yet
                if fork == DATA_FORK:
                    self.data_allocation_block = new_blocks[0]
                else:
                    self.res_allocation_block = new_blocks[0]
            else:
                # Find the last block in the current chain
                last_block = allocation_block_number
                last_block = list(self.allocation_blocks(fork, master_directory_block))[-1]
                master_directory_block.set_allocation_block(last_block, new_blocks[0])

        elif new_number_of_blocks < current_number_of_blocks:  # Shrink the file
            # Need to free some blocks
            blocks_to_free = current_number_of_blocks - new_number_of_blocks
            if allocation_block_number == 0:
                raise OSError(errno.EIO, "No blocks allocated to free")
            # Find the block before the first block to be freed
            prev_block = None
            current_block = allocation_block_number
            for _ in range(new_number_of_blocks):
                prev_block = current_block
                current_block = master_directory_block.get_allocation_block(current_block)
                if current_block in (UNUSED_BLOCK, DIRECTORY_BLOCK):
                    raise OSError(errno.EIO, "Invalid allocation block chain")
                elif current_block == LAST_BLOCK:
                    break
            # Free the blocks starting from current_block
            block_to_free = current_block
            for _ in range(blocks_to_free):
                if block_to_free in (UNUSED_BLOCK, DIRECTORY_BLOCK, LAST_BLOCK):
                    break
                next_block = master_directory_block.get_allocation_block(block_to_free)
                master_directory_block.set_allocation_block(block_to_free, UNUSED_BLOCK)
                master_directory_block.volume_information.free_allocation_blocks += 1
                block_to_free = next_block
            # Mark the last block of the resized file as LAST_BLOCK
            if new_number_of_blocks == 0:
                # No blocks allocated anymore
                if fork == DATA_FORK:
                    self.data_allocation_block = 0
                else:
                    self.res_allocation_block = 0
            else:
                if prev_block is None:
                    raise OSError(errno.EIO, "Invalid allocation block chain")
                master_directory_block.set_allocation_block(prev_block, LAST_BLOCK)

        # Update the logical and physical sizes
        if fork == DATA_FORK:
            self.data_size = new_size
            self.data_physical_size = new_number_of_blocks * self.fs.allocation_block_size
        else:
            self.res_size = new_size
            self.res_physical_size = new_number_of_blocks * self.fs.allocation_block_size
        # Write the Master Directory Block and the File Directory Entry
        master_directory_block.write()
        self.write()

    def delete(self) -> bool:
        """
        Delete the file
        """
        master_directory_block = MasterDirectoryBlock.read(self.fs.dev, self.fs.master_directory_len)
        # Free the allocation blocks
        for block in list(self.allocation_blocks(fork=DATA_FORK, master_directory_block=master_directory_block)):
            master_directory_block.set_allocation_block(block, UNUSED_BLOCK)
            master_directory_block.volume_information.free_allocation_blocks += 1
        for block in list(self.allocation_blocks(fork=RESOURCE_FORK, master_directory_block=master_directory_block)):
            master_directory_block.set_allocation_block(block, UNUSED_BLOCK)
            master_directory_block.volume_information.free_allocation_blocks += 1
        # Mark the entry as unused
        found = False
        file_directory = FileDirectory.read(self.fs)
        for entry in file_directory.entries_list:
            if entry.file_number == self.file_number:
                entry.flags &= 0x7F  # Mark the entry as unused
                entry.filename = ""  # Clear the filename
                found = True
        if not found:
            return False
        # Update the number of files
        if master_directory_block.volume_information.number_of_files > 0:
            master_directory_block.volume_information.number_of_files -= 1
        # Write the file directory and the allocation map
        file_directory.write()
        master_directory_block.write()
        return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        found = False
        file_directory = FileDirectory.read(self.fs)
        for entry in file_directory.entries_list:
            if entry.file_number == self.file_number:
                entry.flags = self.flags
                entry.version = self.version
                entry.raw_file_type = self.raw_file_type
                entry.raw_file_creator = self.raw_file_creator
                entry.mac_finder_flags = self.mac_finder_flags
                entry.mac_icon_position = self.mac_icon_position
                entry.folder_number = self.folder_number
                entry.data_allocation_block = self.data_allocation_block
                entry.data_size = self.data_size
                entry.data_physical_size = self.data_physical_size
                entry.res_allocation_block = self.res_allocation_block
                entry.res_size = self.res_size
                entry.res_physical_size = self.res_physical_size
                entry.raw_creation_date = self.raw_creation_date
                entry.raw_last_mod_date = self.raw_last_mod_date
                entry.filename = self.filename
                found = True
        if not found:
            return False
        file_directory.write()
        return True

    def compute_entry_length(self) -> None:
        """
        Compute the length of the entry in bytes (including filename and padding)
        """
        if self.is_empty():
            self.entry_length = 2
        else:
            filename_length = len(self.filename.encode("macroman", errors="ignore")[:FILE_NAME_LENGTH])
            self.entry_length = FILE_ENTRY_SIZE + filename_length + ((filename_length + 1) % 2)

    def examine(self) -> str:
        data = {
            "Filename": self.fullname,
            "File number": self.file_number,
            "File flags": f"{self.flags:04X}",
            "Version": f"{self.version:02X}",
            "Creation date": self.creation_date,
            "Last modification date": self.last_mod_date,
            "File type": self.raw_file_type.decode("macroman", errors="ignore"),
            "File creator": self.raw_file_creator.decode("macroman", errors="ignore"),
            "Finder flags": f"{self.mac_finder_flags:04X}",
            "Icon position": f"{self.mac_icon_position:04X}",
            "Folder number": self.folder_number,
            "Data allocation block": self.data_allocation_block,
            "Data size": f"{self.data_size} (physical {self.data_physical_size})",
            "Data allocation blocks": list(self.allocation_blocks(DATA_FORK)),
            "Resource allocation block": self.res_allocation_block,
            "Resource size": f"{self.res_size} (physical {self.res_physical_size})",
            "Resource allocation blocks": list(self.allocation_blocks(RESOURCE_FORK)),
        }
        return dump_struct(data, width=30, format_label=False, newline=True)

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> File:
        """
        Open a file
        """
        return File(self, file_mode, fork)

    def allocation_blocks(
        self,
        fork: str = DATA_FORK,
        master_directory_block: t.Optional["MasterDirectoryBlock"] = None,
    ) -> t.Iterator[int]:
        """
        Generator to get the allocation blocks of the file

        Note: it returns the allocation block numbers, not the disk block numbers
        """
        fork = check_fork(fork)
        if master_directory_block is None:
            master_directory_block = MasterDirectoryBlock.read(self.fs.dev, self.fs.master_directory_len)
        if fork == RESOURCE_FORK:
            allocation_block_number = self.res_allocation_block
        else:
            allocation_block_number = self.data_allocation_block
        if allocation_block_number == 0:
            return
        yield allocation_block_number
        while True:
            allocation_block_number = master_directory_block.get_allocation_block(allocation_block_number)
            if allocation_block_number in (UNUSED_BLOCK, DIRECTORY_BLOCK):
                raise OSError(errno.EIO, "Invalid allocation block chain")
            elif allocation_block_number == LAST_BLOCK:
                break
            else:
                yield allocation_block_number

    def read_bytes(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> bytes:
        """
        Get the individual fork data or the data/resource as AppleSingle
        """
        if file_mode == ASCII and not fork:
            fork = DATA_FORK
        if fork:
            fork = check_fork(fork)
        if not file_mode:
            file_mode = IMAGE
        if fork:
            # Return the individual fork
            data = super().read_bytes(file_mode=file_mode, fork=fork)
        else:
            # Return the data and resource forks as AppleSingle
            data = super().read_bytes(file_mode=file_mode, fork=DATA_FORK)
            resource = super().read_bytes(file_mode=file_mode, fork=RESOURCE_FORK)
            finder_info = FinderInfo(
                raw_file_type=self.raw_file_type,
                raw_file_creator=self.raw_file_creator,
                finder_flags=self.mac_finder_flags,
                icon_position=self.mac_icon_position,
                folder_number=self.folder_number,
            )
            data = AppleSingle(data=data, resource=resource, finder_info=finder_info).write()
        return data

    def __str__(self) -> str:
        file_type = self.raw_file_type.decode("macroman", errors="ignore").replace("\0", " ")
        file_creator = self.raw_file_creator.decode("macroman", errors="ignore").replace("\0", " ")
        return (
            f"{self.file_number:4d}  {self.flags:04X}  {file_type:4} {file_creator:4}    "
            f"{self.data_allocation_block:8d} {self.data_size:9d}   "
            f"{self.res_allocation_block:8d} {self.res_size:9d}   "
            f"{self.last_mod_date}   {self.filename}"
        )

    def __repr__(self) -> str:
        return str(self)


class FileDirectory(XFERXPath):
    """
    File Directory

    The file directory contains a list of file entries.
    """

    fs: "MacintoshFilesystem"
    entries_list: t.List["MacintoshPath"]

    def __init__(self, fs: "MacintoshFilesystem"):
        super().__init__(fs)
        self.entries_list = []

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
    def parent(self) -> "FileDirectory":
        """The logical parent"""
        return self

    @classmethod
    def read(cls, fs: "MacintoshFilesystem") -> "FileDirectory":
        """
        Read File Directory
        """
        self = cls(fs)
        self.entries_list = []
        for block_num in self.fs.file_directory_blocks:
            # Read file directory block
            buffer = self.fs.read_block(block_num)
            position = 0
            # Read entries in the block
            while position + FILE_ENTRY_SIZE < BLOCK_SIZE:
                if buffer[position] == 0:
                    position += 2
                else:
                    entry = MacintoshPath.read(self.fs, buffer, position)
                    self.entries_list.append(entry)
                    position += entry.entry_length
        return self

    def write(self) -> None:
        """
        Write the File Directory
        """
        entries_list = list(self.entries_list)
        entry = entries_list.pop(0) if entries_list else None
        if entry is not None:
            entry.compute_entry_length()
        for block_num in self.fs.file_directory_blocks:
            # Write directory block
            buffer = bytearray(BLOCK_SIZE)
            position = 0
            if entry is not None:
                while entry is not None and position + entry.entry_length < BLOCK_SIZE:
                    position = entry.write_buffer(buffer, position)
                    entry = entries_list.pop(0) if entries_list else None
                    if entry is not None:
                        entry.compute_entry_length()
            self.fs.write_block(buffer, block_num)

    def iterdir(self) -> t.Iterator[MacintoshPath]:
        """
        Iterate over the entries in the directory
        """
        yield from self.entries_list


class VolumeInformation:
    """
    Volume Information (first 64 bytes of Master Directory Block)

          +-------------------------------------+
     0    | Always 0xD2D7                       |
          +-------------------------------------+
     2    | Date and time of initialization     |
          +-------------------------------------+
     6    | Date and time of last backup        |
          +-------------------------------------+
    10    | Volume attributes                   |
          +-------------------------------------+
    12    | Number of files in directory        |
          +-------------------------------------+
    14    | First block of directory            |
          +-------------------------------------+
    16    | Length of directory in blocks       |
          +-------------------------------------+
    18    | Allocation blocks on volume         |
          +-------------------------------------+
    20    | Size of allocation blocks           |
          +-------------------------------------+
    24    | Number of bytes to allocate         |
          +-------------------------------------+
    28    | First allocation block in block map |
          +-------------------------------------+
    30    | Next unused file number             |
          +-------------------------------------+
    34    | Number of unused allocation blocks  |
          +-------------------------------------+
    36    | Length of volume name               |
          +-------------------------------------+
    37    | Characters of volume name           |
          /                                     /
          |                                     |
          +-------------------------------------+
    """

    dev: BlockDevice
    signature: int  # Always 0xD2D7
    raw_creation_date: int  # Date and time of initialization
    raw_backup_date: int  # Date and time of last backup
    attributes: int  # Volume attributes
    number_of_files: int  # Number of files in directory
    directory_block_num: int  # First block of directory
    directory_len: int  # Length of directory in blocks
    allocation_blocks: int  # Number of allocation blocks on volume
    allocation_block_size: int  # Size of allocation blocks (in bytes)
    clump_size: int  # Number of bytes to allocate when increasing the size of a file
    allocation_block_num: int  # First allocation block in block map
    next_file_number: int  # Next unused file number
    free_allocation_blocks: int  # Number of unused allocation blocks
    volume_name: str  # Volume name

    def __init__(self, dev: BlockDevice):
        self.dev = dev

    @property
    def master_directory_len(self) -> int:
        """
        Length of Master Directory Block in blocks
        """
        return self.directory_block_num - MASTER_DIRECTORY_BLOCK

    @classmethod
    def read(cls, dev: BlockDevice, buffer: t.Optional[bytes] = None) -> "VolumeInformation":
        self = cls(dev)
        if buffer is None:
            buffer = dev.read_block(MASTER_DIRECTORY_BLOCK)
        (
            self.signature,  # Always 0xD2D7
            self.raw_creation_date,  # Date and time of initialization
            self.raw_backup_date,  # Date and time of last backup
            self.attributes,  # Volume attributes
            self.number_of_files,  # Number of files in directory
            self.directory_block_num,  # First block of directory
            self.directory_len,  # Length of directory in blocks
            self.allocation_blocks,  # Number of allocation blocks on volume
            self.allocation_block_size,  # Size of allocation blocks (in bytes)
            self.clump_size,  # Number of bytes to allocate when increasing the size of a file
            self.allocation_block_num,  # First allocation block in block map
            self.next_file_number,  # Next unused file number
            self.free_allocation_blocks,  # Number of unused allocation blocks
            volume_name_length,  # Length of volume name
        ) = struct.unpack_from(VOLUME_INFO_FORMAT, buffer, 0)
        if volume_name_length > VOLUME_NAME_MAX_LENGTH:
            volume_name_length = VOLUME_NAME_MAX_LENGTH
        tmp = buffer[VOLUME_INFO_SIZE : VOLUME_INFO_SIZE + volume_name_length]
        self.volume_name = tmp.decode("macroman", errors="ignore").strip("\0")
        return self

    @classmethod
    def create(
        cls,
        dev: BlockDevice,
        volume_name: str,
        allocation_block_size: int = DEFAULT_ALLOCATION_BLOCK_SIZE,
        directory_len: int = DEFAULT_DIRECTORY_LEN,
    ) -> "VolumeInformation":
        """
        Create a new Volume Information
        """
        self = cls(dev)
        allocation_blocks = dev.get_size() // allocation_block_size
        mdb_size = MAP_POSITION + (allocation_blocks * 12 // 8)
        mdb_size_in_blocks = (mdb_size + BLOCK_SIZE - 1) // BLOCK_SIZE
        directory_block_num = MASTER_DIRECTORY_BLOCK + mdb_size_in_blocks
        directory_len = DEFAULT_DIRECTORY_LEN
        allocation_block_num = directory_block_num + directory_len
        allocation_blocks -= (allocation_block_num // 12 * 8) + 1
        self.signature = VOLUME_SIGNATURE
        self.raw_creation_date = date_to_mfs(datetime.now())
        self.raw_backup_date = self.raw_creation_date
        self.attributes = 0
        self.number_of_files = 0
        self.directory_block_num = directory_block_num
        self.directory_len = directory_len
        self.allocation_blocks = allocation_blocks
        self.allocation_block_size = allocation_block_size
        self.clump_size = DEFAULT_CLUMP_SIZE
        self.allocation_block_num = directory_block_num + directory_len
        self.next_file_number = 1
        self.free_allocation_blocks = allocation_blocks
        self.volume_name = volume_name[:VOLUME_NAME_MAX_LENGTH]
        return self

    def write_buffer(self, buffer: bytearray) -> None:
        struct.pack_into(
            VOLUME_INFO_FORMAT,
            buffer,
            0,
            self.signature,  # Always 0xD2D7
            self.raw_creation_date,  # Date and time of initialization
            self.raw_backup_date,  # Date and time of last backup
            self.attributes,  # Volume attributes
            self.number_of_files,  # Number of files in directory
            self.directory_block_num,  # First block of directory
            self.directory_len,  # Length of directory in blocks
            self.allocation_blocks,  # Number of allocation blocks on volume
            self.allocation_block_size,  # Size of allocation blocks (in bytes)
            self.clump_size,  # Number of bytes to allocate when increasing the size of a file
            self.allocation_block_num,  # First allocation block in block map
            self.next_file_number,  # Next unused file number
            self.free_allocation_blocks,  # Number of unused allocation blocks
            len(self.volume_name),  # Length of volume name
        )
        buffer[VOLUME_INFO_SIZE : VOLUME_INFO_SIZE + len(self.volume_name)] = self.volume_name.encode(
            "macroman", errors="ignore"
        )

    def examine(self) -> str:
        data = dict(self.__dict__)
        data["creation_date"] = str(mfs_to_date(self.raw_creation_date))
        data["backup_date"] = str(mfs_to_date(self.raw_backup_date))
        return dump_struct(data, exclude=["raw_creation_date", "raw_backup_date"], width=30, newline=True)


class MasterDirectoryBlock:
    """
    Master Directory Block

    Byte
          +-------------------------------------+
     0    | Volume Information                  |
          /                                     /
    63    |                                     |
          +-------------------------------------+
    64    | Volume Allocation Map               |
          /                                     /
          |                                     |
          +-------------------------------------+
          | Unused                              |
          /                                     /
          +-------------------------------------+

    The Volume Information is 64 bytes long.
    The Volume Allocation Map starts at byte 64 of the Master Directory Block.
    Each allocation block is represented by 12 bits in the map.
    The first entry in the block map is for block number 2.

    Each entry can have the following values:
    - 0         unused
    - 1         last block in a file
    - 2-4094    next block in a file
    - 4095      directory block
    """

    dev: BlockDevice
    master_directory_len: int  # Length of Master Directory Block in blocks
    volume_information: VolumeInformation
    blocks: bytearray  # Raw blocks of Master Directory Block

    def __init__(self, dev: BlockDevice):
        self.dev = dev

    @classmethod
    def read(cls, dev: BlockDevice, master_directory_len: int) -> "MasterDirectoryBlock":
        self = cls(dev)
        self.master_directory_len = master_directory_len
        # Read the Master Directory Block
        self.blocks = bytearray(dev.read_block(MASTER_DIRECTORY_BLOCK, master_directory_len))
        # Read the Volume Information
        self.volume_information = VolumeInformation.read(dev, bytes(self.blocks[:MAP_POSITION]))
        return self

    @classmethod
    def create(
        cls,
        dev: BlockDevice,
        volume_name: str,
        allocation_block_size: int = DEFAULT_ALLOCATION_BLOCK_SIZE,
        directory_len: int = DEFAULT_DIRECTORY_LEN,
    ) -> "MasterDirectoryBlock":
        """
        Create a new Master Directory Block
        """
        self = cls(dev)
        # Create the Volume Information
        self.volume_information = VolumeInformation.create(
            self.dev,
            volume_name,
            allocation_block_size,
            directory_len,
        )
        self.master_directory_len = self.volume_information.master_directory_len
        # Create empty blocks
        self.blocks = bytearray(BLOCK_SIZE * self.master_directory_len)
        # Write the Master Directory Block
        self.write()
        return self

    def write(self) -> None:
        """
        Write the Master Directory Block (including Volume Information)
        """
        # Update the Volume Information
        self.volume_information.write_buffer(self.blocks)
        # Write the Master Directory Block
        self.dev.write_block(self.blocks, MASTER_DIRECTORY_BLOCK, self.master_directory_len)

    def get_file_directory_blocks(self, allocation_block_size: int, allocation_block_num: int) -> t.List[int]:
        """
        Get the list of blocks containing the File Directory

        The File Directory blocks are:
        - the blocks specified in the Volume Information (directory_block_num .. directory_block_num + directory_len)
        - the blocks marked as DIRECTORY_BLOCK in the allocation map
        """
        blocks = [self.volume_information.directory_block_num + i for i in range(self.volume_information.directory_len)]
        mul = allocation_block_size // BLOCK_SIZE
        for i in range(len(self.blocks) * 8 // 12):
            if self.get_allocation_block(i + 2) == DIRECTORY_BLOCK:
                disk_block_number = allocation_block_num + (i - 2) * mul
                blocks += [x for x in range(disk_block_number, disk_block_number + mul)]
        return blocks

    def get_allocation_block(self, allocation_block_number: int) -> int:
        """
        Get the allocation block value
        """
        location = ((allocation_block_number - 2) * 12 / 8) + MAP_POSITION
        if location == math.ceil(location):
            pos = int(location)
            return int.from_bytes(self.blocks[pos : pos + 2], 'big') >> 4
        else:
            pos = int(math.floor(location))
            return int.from_bytes(self.blocks[pos : pos + 2], 'big') & 0xFFF

    def set_allocation_block(self, allocation_block_number: int, value: int) -> None:
        """
        Set the allocation block value
        """
        location = ((allocation_block_number - 2) * 12 / 8) + MAP_POSITION
        if location == math.ceil(location):
            pos = int(location)
            current = int.from_bytes(self.blocks[pos : pos + 2], 'big') & 0x000F
            self.blocks = self.blocks[0:pos] + ((value << 4) | current).to_bytes(2, 'big') + self.blocks[pos + 2 :]
        else:
            pos = int(math.floor(location))
            current = int.from_bytes(self.blocks[pos : pos + 2], 'big') & 0xFFF0
            self.blocks = self.blocks[0:pos] + (value | current).to_bytes(2, 'big') + self.blocks[pos + 2 :]

    def allocate(self, number_of_blocks: int) -> t.List[int]:
        """
        Allocate blocks
        """
        if number_of_blocks <= 0:
            return []
        blocks: t.List[int] = []
        for i in range(len(self.blocks) * 8 // 12):
            if self.get_allocation_block(i + 2) == UNUSED_BLOCK:
                # Found a free block
                start_block = i + 2
                blocks.append(start_block)
                if len(blocks) == number_of_blocks:
                    break
        if len(blocks) < number_of_blocks:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        # Mark the blocks as used
        for i in range(len(blocks)):
            if i == len(blocks) - 1:
                self.set_allocation_block(blocks[i], LAST_BLOCK)
            else:
                self.set_allocation_block(blocks[i], blocks[i + 1])
        self.volume_information.free_allocation_blocks -= number_of_blocks
        return blocks


class MacintoshFilesystem(AbstractBlockFilesystem):
    """
    Macintosh File System

    Block

       +-------------------------------------+
    0  | System Startup Information          |
    1  | (zero if not startup disk)          |
       +-------------------------------------+
    2  | Master Directory Block              |  Volume Information
       /                                     /  ..................
       |                                     |  Block Map
       +-------------------------------------+
       | File Directory                      | <-- directory_block_num
       /                                     /
       |                                     |
       +-------------------------------------+
       | File Contents                       | <-- allocation_block_num
       /                                     /
       |                                     |
       +-------------------------------------+

    Inside Macintosh, Pag II-120
    https://www.weihenstephan.org/~michaste/pagetable/mac/Inside_Macintosh.pdf
    """

    fs_name = "mfs"
    fs_description = "Macintosh File System"
    fs_platforms = ["macintosh"]
    fs_forks = [DATA_FORK, RESOURCE_FORK]
    fs_entry_metadata = [
        "creation_date",
        "file_creator",
        "file_type",
        "flags",
        "last_mod_date",
        "mac_finder_flags",
        "mac_icon_position",
        "version",
    ]

    allocation_block_size: int  # Size of allocation blocks (in bytes)
    allocation_block_num: int  # First allocation block in block map
    master_directory_len: int  # Length of Master Directory Block in blocks
    file_directory_blocks: t.List[int]  # List of blocks containing the File Directory

    def __init__(self, file_or_device: t.Union["AbstractFile", "AbstractDevice"]):
        if isinstance(file_or_device, AbstractFile):
            try:
                self.dev = DiskCopy(file_or_device)
            except Exception:
                self.dev = BlockDevice(file_or_device)
        elif isinstance(file_or_device, BlockDevice):
            self.dev = file_or_device
        else:
            raise OSError(errno.EIO, f"Invalid device type for {self.fs_description} filesystem")

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        strict: t.Union[bool, str] = True,
        **kwargs: t.Union[bool, str],
    ) -> "MacintoshFilesystem":
        self = cls(file_or_dev)
        # Read the Master Directory Block
        volume_information = VolumeInformation.read(self.dev)
        if volume_information.signature != VOLUME_SIGNATURE:
            raise OSError(errno.EINVAL, "Not a valid MFS volume")
        self.allocation_block_size = volume_information.allocation_block_size
        self.allocation_block_num = volume_information.allocation_block_num
        self.master_directory_len = volume_information.master_directory_len
        # Get the file directory blocks
        master_directory_block = MasterDirectoryBlock.read(self.dev, self.master_directory_len)
        self.file_directory_blocks = master_directory_block.get_file_directory_blocks(
            self.allocation_block_size, self.allocation_block_num
        )
        return self

    @classmethod
    def initialize(
        cls, file_or_dev: t.Union["AbstractFile", "AbstractDevice"], **kwargs: t.Union[bool, str]
    ) -> "MacintoshFilesystem":
        """
        Create an empty filesystem
        """
        self = cls(file_or_dev)
        try:
            volume_name = kwargs["name"].strip()[:VOLUME_NAME_MAX_LENGTH] or DEFAULT_VOLUME_NAME  # type: ignore
        except Exception:
            volume_name = DEFAULT_VOLUME_NAME
        # Writes 0 to the system startup information block
        self.write_block(bytearray(BLOCK_SIZE * 2), block_number=0, number_of_blocks=1)
        # Create the Master Directory Block
        master_directory_block = MasterDirectoryBlock.create(
            self.dev, volume_name, DEFAULT_ALLOCATION_BLOCK_SIZE, DEFAULT_DIRECTORY_LEN
        )
        volume_information = master_directory_block.volume_information
        self.allocation_block_size = volume_information.allocation_block_size
        self.allocation_block_num = volume_information.allocation_block_num
        self.master_directory_len = volume_information.master_directory_len
        self.file_directory_blocks = master_directory_block.get_file_directory_blocks(
            self.allocation_block_size, self.allocation_block_num
        )
        # Create the File Directory blocks
        file_directory = FileDirectory(self)
        file_directory.write()
        return self

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["MacintoshPath"]:
        if pattern:
            pattern = pattern.upper()
        if not pattern and expand:
            pattern = "*"
        for entry in self.get_root().iterdir():
            if filename_match(entry.name.upper(), pattern, wildcard):
                yield entry

    @property
    def entries_list(self) -> t.Iterator["MacintoshPath"]:
        yield from self.get_root().iterdir()

    def get_file_entry(self, fullname: str) -> MacintoshPath:
        """
        Get the directory entry for a file
        """
        for entry in self.get_root().iterdir():
            if entry.name.upper() == fullname.upper():
                return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def get_root(self) -> FileDirectory:
        """
        Get the root directory
        """
        return FileDirectory.read(self)

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

        if fork is not None:
            # Update a single fork
            fork = check_fork(fork)
            try:
                entry = self.get_file_entry(fullname)
                entry.resize_fork(len(content), fork=fork)
            except FileNotFoundError:
                entry = self.create_file(
                    fullname=fullname,
                    size=len(content) if fork == DATA_FORK else 0,
                    res_size=len(content) if fork == RESOURCE_FORK else 0,
                    metadata=metadata,
                )
            # Write the content to the specified fork
            with entry.open(file_mode, fork) as f:
                f.write(content)
            return

        # Check if the file is an AppleSingle file and extract the content and metadata
        try:
            aps = AppleSingle.read(content)
            if aps.finder_info is not None:
                # Get the finder info
                if metadata.get("file_type") is None:
                    metadata["file_type"] = aps.finder_info.raw_file_type.decode("macroman", errors="ignore")
                if metadata.get("file_creator") is None:
                    metadata["file_creator"] = aps.finder_info.raw_file_creator.decode("macroman", errors="ignore")
                if metadata.get("mac_finder_flags") is None:
                    metadata["mac_finder_flags"] = aps.finder_info.finder_flags
                if metadata.get("mac_icon_position") is None:
                    metadata["mac_icon_position"] = aps.finder_info.icon_position
                if metadata.get("mac_folder_number") is None:
                    metadata["mac_folder_number"] = aps.finder_info.folder_number
            content = aps.data or b""
            resource = aps.resource or b""
        except ValueError:
            resource = b""

        # Set default file type and creator for ASCII files
        if file_mode == ASCII:
            if "file_type" not in metadata:
                metadata["file_type"] = "TEXT"
            if "file_creator" not in metadata:
                metadata["file_creator"] = "ttxt"

        # Create the file
        entry = self.create_file(
            fullname=fullname,
            size=len(content),
            res_size=len(resource),
            metadata=metadata,
        )
        # Write the content to the data fork
        with entry.open(file_mode) as f:
            f.write(content)
        # Write the content to the resource fork
        if resource:
            with entry.open(file_mode, fork=RESOURCE_FORK) as f:
                f.write(resource)

    def create_file(
        self,
        fullname: str,
        size: int,  # Data fork size in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
        res_size: int = 0,  # Resource firk size in bytes
    ) -> MacintoshPath:
        """
        Create a new file with a given length in number of blocks
        """
        metadata = metadata or {}
        fullname = mfs_canonical_filename(fullname)
        # Delete the file if it already exists
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass
        # Create the file
        entry = MacintoshPath.create(
            fs=self,
            fullname=fullname,
            data_size=size,
            res_size=res_size,
            metadata=metadata,
        )
        return entry

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List files in the directory

        Pag 441
        https://bitsavers.org/pdf/apple/mac/developer/MPW/MPW_2.0/MPW_2.0_Reference_1987.pdf
        """
        sys.stdout.write("Name                  Type Crtr  Size    Flags      Last-Mod-Date     Creation-Date\n")
        sys.stdout.write("--------------------  ---- ---- ------ ---------- ----------------- -----------------\n")
        for x in self.filter_entries_list(pattern, include_all=True, wildcard=True):
            # Print file information
            # filename, byte length, attributes, last modification date, last access date, address, use count
            filename = f"'{x.name}'" if " " in x.name else x.name
            file_type = x.raw_file_type.decode("macroman", errors="ignore").replace("\0", " ")
            file_creator = x.raw_file_creator.decode("macroman", errors="ignore").replace("\0", " ")
            size = format_size(x.get_size(fork=DATA_FORK) + x.get_size(fork=RESOURCE_FORK), si=False, precision="3.0f")
            flags = ""  #  "lvbspoimad"  # TODO
            # l - Locked
            # v - Invisible
            # b - Bundle
            # s - System
            # p - Protected
            # o - Stationery pad
            # i - Initialized
            # m - Mounted
            # a - Alias
            # d - Desktop
            creation_date = x.creation_date.strftime("%m/%d/%y %I:%M %p").lstrip("0") if x.creation_date else ""
            last_mod_date = x.last_mod_date.strftime("%m/%d/%y %I:%M %p").lstrip("0") if x.last_mod_date else ""
            sys.stdout.write(
                f"{filename:<20}  {file_type:<4} {file_creator:<4} {size:>6} {flags:10} {last_mod_date:>17} {creation_date:>17}\n"
            )
        sys.stdout.write("\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if options.get("diskid"):
            # Display the volume information
            volume_information = VolumeInformation.read(self.dev)
            sys.stdout.write(volume_information.examine())
        if options.get("bitmap"):
            # Examine the allocation map
            master_directory_block = MasterDirectoryBlock.read(self.dev, self.master_directory_len)
            for i in range(0, (len(master_directory_block.blocks) * 8 // 12) + 2):
                value = master_directory_block.get_allocation_block(i)
                if value == UNUSED_BLOCK:
                    label = "FREE"
                elif value == LAST_BLOCK:
                    label = "LAST"
                elif value == DIRECTORY_BLOCK:
                    label = "DIR "
                else:
                    label = f"{value:4d}"
                # Skip the first two allocation blocks
                if i < 2:
                    sys.stdout.write(" " * 13)
                else:
                    sys.stdout.write(f"{i:04d} [{label}]  ")
                if i % 8 == 7:
                    sys.stdout.write("\n")
            sys.stdout.write("\n")
        elif not arg:
            sys.stdout.write("\n*Volume Information\n")
            volume_information = VolumeInformation.read(self.dev)
            sys.stdout.write(volume_information.examine())
            # Display the file directory
            sys.stdout.write("\n*File Directory\n")
            sys.stdout.write(
                "Num  Flags Type Crtr   Data Block Data Size  Res Block  Res Size   Last Mod Date         Filename\n"
            )
            sys.stdout.write(
                "---- ----- ---- ----   ---------- --------- ---------- ---------   -------------------   --------\n"
            )
            file_directory = FileDirectory.read(self)
            for entry in file_directory.entries_list:
                sys.stdout.write(f"{entry}\n")
        else:
            # Display the file information
            entry = self.get_file_entry(arg)  # type: ignore
            sys.stdout.write(entry.examine())
