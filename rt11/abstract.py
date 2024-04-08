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

from abc import ABC, abstractmethod
from datetime import date
from typing import Iterator, Optional

__all__ = [
    "AbstractFile",
    "AbstractDirectoryEntry",
    "AbstractFilesystem",
]


class AbstractFile(ABC):
    """Abstract base class for file operations"""

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
    def close(self) -> None:
        """Close the file"""


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
    @abstractmethod
    def creation_date(self) -> Optional[date]:
        """Creation date"""

    @abstractmethod
    def delete(self) -> bool:
        """Delete the file"""


class AbstractFilesystem(object):
    """Abstract base class for filesystem implementations"""

    @abstractmethod
    def filter_entries_list(
        self, pattern: Optional[str], include_all: bool = False
    ) -> Iterator["AbstractDirectoryEntry"]:
        """Filter directory entries based on a pattern"""

    @property
    @abstractmethod
    def entries_list(self) -> Iterator["AbstractDirectoryEntry"]:
        """Property to get an iterator of directory entries"""

    @abstractmethod
    def get_file_entry(self, fullname: str) -> Optional["AbstractDirectoryEntry"]:
        """Get the directory entry for a file"""

    @abstractmethod
    def open_file(self, fullname: str) -> "AbstractFile":
        """Open a file"""

    @abstractmethod
    def read_bytes(self, fullname: str) -> bytes:
        """Get the content of a file"""

    @abstractmethod
    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: Optional[date] = None,
    ) -> None:
        """Write content to a file"""

    @abstractmethod
    def create_file(
        self,
        fullname: str,
        length: int,
        creation_date: Optional[date] = None,
    ) -> Optional["AbstractDirectoryEntry"]:
        """Create a new file with a given length in number of blocks"""

    @abstractmethod
    def chdir(self, fullname: str) -> bool:
        """Change the current directory"""

    @abstractmethod
    def isdir(self, fullname: str) -> bool:
        """Check if the given path is a directory"""

    @abstractmethod
    def exists(self, fullname: str) -> bool:
        """Check if the given path exists"""

    @abstractmethod
    def dir(self, pattern: Optional[str]) -> None:
        """List directory contents"""

    @abstractmethod
    def examine(self, block: Optional[str]) -> None:
        """Examine the filesytem"""

    @abstractmethod
    def initialize(self) -> None:
        """Initialize the filesytem"""

    @abstractmethod
    def close(self) -> None:
        """Close the filesytem"""

    @abstractmethod
    def get_pwd(self) -> str:
        """Get the current directory"""
