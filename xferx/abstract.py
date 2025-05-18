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
import os
import sys
import typing as t
from abc import ABC, abstractmethod
from datetime import date

from .commons import ASCII, BLOCK_SIZE, IMAGE, READ_FILE_FULL, hex_dump

__all__ = [
    "AbstractFile",
    "AbstractDirectoryEntry",
    "AbstractFilesystem",
]


class AbstractFile(ABC):
    """Abstract base class for file operations"""

    current_position: int = 0

    @abstractmethod
    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """Read block(s) of data from the file"""

    @abstractmethod
    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """Write block(s) of data to the file"""

    @abstractmethod
    def get_size(self) -> int:
        """Get file size in bytes."""

    @abstractmethod
    def get_block_size(self) -> int:
        """Get file block size in bytes"""

    @abstractmethod
    def close(self) -> None:
        """Close the file"""

    def read(self, size: t.Optional[int] = None) -> bytes:
        """Read bytes from the file, starting at the current position"""
        data = bytearray()
        block_size = self.get_block_size()
        while size is None or len(data) < size:
            # Calculate current block and offset within the block
            block_number = self.current_position // block_size
            block_offset = self.current_position % block_size
            # print(f"{block_number=} {block_offset=}")
            # Read the next block
            block_data = self.read_block(block_number)
            # No more data
            if not block_data:
                break
            # Calculate the data to append
            if size is None:
                data_to_append = block_data[block_offset:]
            else:
                remaining_size = size - len(data)
                data_to_append = block_data[block_offset : block_offset + remaining_size]
            data.extend(data_to_append)
            self.current_position += len(data_to_append)
            # If size is specified and we have read enough data, break the loop
            if (size is not None and len(data) >= size) or (len(data_to_append) == 0):
                break
        return bytes(data)

    def write(self, data: bytes) -> int:
        """Write bytes to the file at the current position"""
        data_length = len(data)
        written = 0
        block_size = self.get_block_size()
        while written < data_length:
            # Calculate current block and offset within the block
            block_number = self.current_position // block_size
            block_offset = self.current_position % block_size
            # Read the current block
            block_data = bytearray(self.read_block(block_number))
            if not block_data:
                block_data = bytearray(block_size)
            # Calculate the amount of data to write in the current block
            remaining_block_space = block_size - block_offset
            data_to_write = data[written : written + remaining_block_space]
            # Write the data to the block
            block_data[block_offset : block_offset + len(data_to_write)] = data_to_write
            # Write the block to the device
            self.write_block(block_data, block_number)
            # Update current position and written count
            self.current_position += len(data_to_write)
            written += len(data_to_write)
        return written

    def seek(self, offset: int, whence: int = 0) -> None:
        """Move the current position in the file to a new location"""
        if whence == os.SEEK_SET:  # Absolute file positioning
            self.current_position = offset
        elif whence == os.SEEK_CUR:  # Seek relative to the current position
            self.current_position += offset
        elif whence == os.SEEK_END:  # Seek relative to the file's end
            self.current_position = self.get_size() + offset
        else:
            raise ValueError
        # Ensure the current position is not negative
        if self.current_position < 0:
            self.current_position = 0

    def tell(self) -> int:
        """Get current file position"""
        return self.current_position

    def truncate(self, size: t.Optional[int] = None) -> None:
        """
        Resize the file to the given number of bytes.
        If the size is not specified, the current position will be used.
        """
        if size is not None and self.current_position > size:
            self.current_position = size


class AbstractDirectoryEntry(ABC):

    @property
    @abstractmethod
    def fullname(self) -> str:
        """Name with path"""

    @property
    @abstractmethod
    def basename(self) -> str:
        """Final path component"""

    @property
    def creation_date(self) -> t.Optional[date]:
        """Creation date"""
        return None

    @property
    def file_type(self) -> t.Optional[str]:
        """File type"""
        return None

    @abstractmethod
    def get_length(self) -> int:
        """Get the length in blocks"""

    @abstractmethod
    def get_size(self) -> int:
        """Get file size in bytes"""

    @abstractmethod
    def get_block_size(self) -> int:
        """Get file block size in bytes"""

    @abstractmethod
    def delete(self) -> bool:
        """Delete the directory entry"""

    @abstractmethod
    def write(self) -> bool:
        """Write the directory entry"""

    @abstractmethod
    def open(self, file_mode: t.Optional[str] = None) -> AbstractFile:
        """Open the file"""

    def read_bytes(self, file_mode: t.Optional[str] = None) -> bytes:
        """Get the content of the file"""
        f = self.open(file_mode)
        try:
            return f.read_block(0, READ_FILE_FULL)[: f.get_size()]
        finally:
            f.close()

    def read_text(self, encoding: str = "ascii", errors: str = "ignore", file_mode: str = ASCII) -> str:
        """Get the content of the file as text"""
        data = self.read_bytes(file_mode)
        return data.decode(encoding, errors)


class AbstractFilesystem:
    """Abstract base class for filesystem implementations"""

    fs_name: str  # Filesystem name
    fs_description: str  # Filesystem description

    @classmethod
    @abstractmethod
    def mount(cls, file: "AbstractFile") -> "AbstractFilesystem":
        pass

    @abstractmethod
    def filter_entries_list(
        self, pattern: t.Optional[str], include_all: bool = False, expand: bool = True
    ) -> t.Iterator["AbstractDirectoryEntry"]:
        """Filter directory entries based on a pattern"""

    @property
    @abstractmethod
    def entries_list(self) -> t.Iterator["AbstractDirectoryEntry"]:
        """Property to get an iterator of directory entries"""

    @abstractmethod
    def get_file_entry(self, fullname: str) -> "AbstractDirectoryEntry":
        """Get the directory entry for a file"""

    @abstractmethod
    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: t.Optional[date] = None,
        file_type: t.Optional[str] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        """Write content to a file"""

    @abstractmethod
    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,
        creation_date: t.Optional[date] = None,
        file_type: t.Optional[str] = None,
    ) -> t.Optional["AbstractDirectoryEntry"]:
        """Create a new file with a given length in number of blocks"""

    def create_directory(
        self,
        fullname: str,
        options: t.Dict[str, t.Union[str, bool]],
    ) -> t.Optional["AbstractDirectoryEntry"]:
        """Create a new directory"""
        raise OSError(errno.ENOSYS, os.strerror(errno.ENOSYS))

    @abstractmethod
    def chdir(self, fullname: str) -> bool:
        """Change the current directory"""

    @abstractmethod
    def isdir(self, fullname: str) -> bool:
        """Check if the given path is a directory"""

    @abstractmethod
    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """List directory contents"""

    @abstractmethod
    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        """Examine the filesystem"""

    @abstractmethod
    def get_size(self) -> int:
        """Get filesystem size in bytes"""

    @abstractmethod
    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        """Initialize the filesystem"""

    @abstractmethod
    def close(self) -> None:
        """Close the filesystem"""

    @abstractmethod
    def get_pwd(self) -> str:
        """Get the current directory"""

    def exists(self, fullname: str) -> bool:
        """Check if the given path exists"""
        try:
            self.get_file_entry(fullname)
            return True
        except FileNotFoundError:
            return False

    def open_file(self, fullname: str, file_mode: t.Optional[str] = None) -> "AbstractFile":
        """Open a file"""
        entry = self.get_file_entry(fullname)
        return entry.open(file_mode)

    def read_bytes(self, fullname: str, file_mode: t.Optional[str] = None) -> bytes:
        """Get the content of a file"""
        entry = self.get_file_entry(fullname)
        return entry.read_bytes(file_mode)

    def read_text(self, fullname: str, encoding: str = "ascii", errors: str = "ignore", file_mode: str = ASCII) -> str:
        """Get the content of a file as text"""
        data = self.read_bytes(fullname, file_mode)
        return data.decode(encoding, errors)

    def dump(self, fullname: t.Optional[str], start: t.Optional[int] = None, end: t.Optional[int] = None) -> None:
        """Dump the content of a file or a range of blocks"""
        # TODO: Check block range
        if fullname:
            if start is None:
                start = 0
            if end is None:
                entry = self.get_file_entry(fullname)
                end = entry.get_length() - 1
            f = self.open_file(fullname, file_mode=IMAGE)
            try:
                for block_number in range(start, end + 1):
                    data = f.read_block(block_number)
                    sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                    hex_dump(data)
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
                data = self.read_block(block_number)
                sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                hex_dump(data)

    def get_types(self) -> t.List[str]:
        """
        Get the list of the supported file types
        """
        return []
