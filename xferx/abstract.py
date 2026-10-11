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
from .device.abstract import AbstractDevice
from .device.block import BlockDevice

__all__ = [
    "AbstractFile",
    "XFERXPath",
    "AbstractFilesystem",
    "AbstractBlockFilesystem",
]

AbstractFileT = t.TypeVar("AbstractFileT", bound="AbstractFile")


class AbstractFile(ABC):
    """Abstract base class for file operations"""

    current_position: int = 0
    sector_size: int | None = None  # Sector size, in bytes
    closed: bool = False

    def __enter__(self: AbstractFileT) -> AbstractFileT:
        return self

    def __exit__(
        self, exc_type: t.Optional[type[BaseException]], exc_val: t.Optional[BaseException], exc_tb: t.Optional[t.Any]
    ) -> None:
        self.close()

    @property
    @abstractmethod
    def name(self) -> str:
        """Final path component"""

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
        buffer: t.Union[bytes, bytearray],
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

    def close(self) -> None:
        """
        Close the file
        """
        self.closed = True

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

    def write(self, data: t.Union[bytes, bytearray]) -> int:
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

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.__dict__})"


class XFERXPath(ABC):
    fs: "AbstractFilesystem"

    def __init__(self, fs: "AbstractFilesystem"):
        self.fs = fs

    @property
    @abstractmethod
    def fullname(self) -> str:
        """Name with path"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Final path component"""

    @property
    @abstractmethod
    def parent(self) -> "XFERXPath":
        """The logical parent of the path."""

    @property
    def creation_date(self) -> t.Optional[date]:
        """Creation date"""
        return None

    def is_dir(self) -> bool:
        """Is a directory"""
        return False

    def is_file(self) -> bool:
        """Is a file"""
        return not self.is_dir()

    def is_mount(self) -> bool:
        """Is root directory"""
        return False

    @property
    def file_type(self) -> t.Optional[str]:
        """File type"""
        return None

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """Get the length in blocks"""
        return 0

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """Get file size in bytes"""
        return 0

    def get_block_size(self) -> int:
        """Get file block size in bytes"""
        return BLOCK_SIZE

    def delete(self) -> bool:
        """Delete the directory entry"""
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> AbstractFile:
        """Open the file"""
        if self.is_dir():
            raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), self.fullname)
        else:
            raise OSError(errno.EPERM, os.strerror(errno.EPERM))

    def read_bytes(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> bytes:
        """Get the content of the file"""
        with self.open(file_mode=file_mode, fork=fork) as f:
            return f.read_block(0, READ_FILE_FULL)[: f.get_size()]

    def read_text(
        self, encoding: str = "ascii", errors: str = "ignore", file_mode: str = ASCII, fork: t.Optional[str] = None
    ) -> str:
        """Get the content of the file as text"""
        data = self.read_bytes(file_mode)
        return data.decode(encoding, errors)

    def iterdir(self) -> t.Iterator["XFERXPath"]:
        """
        Iterate over directory entries
        """
        if self.is_dir():
            raise OSError(errno.EPERM, os.strerror(errno.EPERM))
        else:
            raise NotADirectoryError(f"{self.fullname} is not a directory")

    @property
    def metadata(self) -> t.Dict[str, t.Any]:
        """Get metadata of the directory entry"""
        return {
            k: (value() if callable(value) else value)
            for k in self.fs.fs_entry_metadata
            for value in [getattr(self, k, None)]
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.__dict__})"


class AbstractFilesystem:
    """Abstract base class for filesystem implementations"""

    fs_name: str  # Filesystem name
    fs_description: str  # Filesystem description
    fs_platforms: t.List[str] = []  # Filesystem platforms
    fs_forks: t.List[str] = []  # Supported file forks
    fs_entry_metadata: t.List[str] = []  # Supported metadata for file entries

    dev: AbstractDevice  # Device from which the filesystem is mounted
    target: t.Optional[str] = None  # Login volume for the mounted filesystem
    source: t.Optional[str] = None  # Source of the mounted filesystem (e.g., file path)

    def __init__(self, dev: "AbstractDevice"):
        self.dev = dev

    @classmethod
    @abstractmethod
    def mount(
        cls, file_or_dev: t.Union["AbstractFile", "AbstractDevice"], **kwargs: t.Union[bool, str]
    ) -> "AbstractFilesystem":
        """Mount the filesystem"""
        pass

    @classmethod
    def initialize(
        cls, file_or_dev: t.Union["AbstractFile", "AbstractDevice"], **kwargs: t.Union[bool, str]
    ) -> "AbstractFilesystem":
        """Initialize the filesystem"""
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    @abstractmethod
    def filter_entries_list(
        self, pattern: t.Optional[str], include_all: bool = False, expand: bool = True
    ) -> t.Iterator["XFERXPath"]:
        """Filter directory entries based on a pattern"""

    @property
    @abstractmethod
    def entries_list(self) -> t.Iterator["XFERXPath"]:
        """Property to get an iterator of directory entries"""

    @abstractmethod
    def get_file_entry(self, fullname: str) -> "XFERXPath":
        """Get the directory entry for a file"""

    @abstractmethod
    def get_root(self) -> "XFERXPath":
        """Get the root directory entry"""

    def create_file(
        self,
        fullname: str,
        size: int,  # Size in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> "XFERXPath":
        """Create a new file"""
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def create_directory(
        self,
        fullname: str,
        options: t.Dict[str, t.Union[str, bool]],
    ) -> "XFERXPath":
        """Create a new directory"""
        raise OSError(errno.ENOSYS, os.strerror(errno.ENOSYS))

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        """Change the current directory"""
        raise NotImplementedError(f"chdir is not supported on {self.fs_name}")

    def get_pwd(self) -> str:
        """Get the current directory"""
        return ""

    def is_dir(self, fullname: str) -> bool:
        """Check if the given path is a directory"""
        try:
            entry = self.get_file_entry(fullname)
            return entry.is_dir()
        except Exception:
            return False

    def path_join(self, path: str, *paths: str) -> str:
        """Join directory path and filename"""
        return os.path.join(path, *paths)  # TODO
        # if dirpath and not dirpath.endswith("/"):
        #     dirpath += "/"
        # return f"{dirpath}{filename}"

    @abstractmethod
    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """List directory contents"""

    @abstractmethod
    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        """Examine the filesystem"""

    def get_size(self) -> int:
        """Get filesystem size in bytes"""
        return self.dev.get_size()

    def close(self) -> None:
        """Close the filesystem"""
        self.dev.close()

    def exists(self, fullname: str) -> bool:
        """Check if the given path exists"""
        try:
            self.get_file_entry(fullname)
            return True
        except FileNotFoundError:
            return False

    def open_file(
        self, fullname: str, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None
    ) -> "AbstractFile":
        """Open a file"""
        entry = self.get_file_entry(fullname)
        return entry.open(file_mode=file_mode, fork=fork)

    def read_bytes(self, fullname: str, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> bytes:
        """Get the content of a file"""
        entry = self.get_file_entry(fullname)
        return entry.read_bytes(file_mode=file_mode, fork=fork)

    def write_bytes(
        self,
        fullname: str,
        content: t.Union[bytes, bytearray],
        fork: t.Optional[str] = None,
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        """Write content to a file"""
        # Create the file
        entry = self.create_file(fullname=fullname, size=len(content), metadata=metadata)
        # Write the content to the file
        with entry.open(file_mode) as f:
            f.write(content)

    def read_text(
        self,
        fullname: str,
        encoding: str = "ascii",
        errors: str = "ignore",
        file_mode: str = ASCII,
        fork: t.Optional[str] = None,
    ) -> str:
        """Get the content of a file as text"""
        data = self.read_bytes(fullname, file_mode=file_mode, fork=fork)
        return data.decode(encoding, errors)

    def dump(
        self,
        fullname: t.Optional[str],
        start: t.Optional[int] = None,
        end: t.Optional[int] = None,
        fork: t.Optional[str] = None,
    ) -> None:
        """Dump the content of a file or a range of blocks"""
        if fullname:
            with self.open_file(fullname, file_mode=IMAGE, fork=fork) as f:
                block_number = start if start is not None else 0
                while True:
                    try:
                        data = f.read_block(block_number)
                    except OSError:
                        break
                    sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                    hex_dump(data)
                    block_number += 1
                    if end is not None and block_number > end:
                        break
        elif hasattr(self, "read_block"):
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

    def show_accounts(self, volume_id: str, options: t.Dict[str, bool]) -> None:
        """
        Listing of all the accounts on the volume
        """
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.__dict__})"

    def __str__(self) -> str:
        return f"{self.fs_name} ({self.dev})"


class AbstractBlockFilesystem(AbstractFilesystem):
    """Abstract base class for block-based filesystems"""

    dev: BlockDevice

    def __init__(self, file_or_device: t.Union["AbstractFile", "AbstractDevice"]):
        if isinstance(file_or_device, AbstractFile):
            self.dev = BlockDevice(file_or_device)
        elif isinstance(file_or_device, BlockDevice):
            self.dev = file_or_device
        else:
            raise OSError(errno.EIO, "Not a valid block device")

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        return self.dev.read_block(block_number, number_of_blocks)

    def write_block(
        self,
        buffer: t.Union[bytes, bytearray],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        self.dev.write_block(buffer, block_number, number_of_blocks)
