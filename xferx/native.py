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
import io
import math
import os
import sys
import threading
import typing as t
from datetime import date, datetime
from pathlib import Path, PureWindowsPath

from .abstract import XFERXPath, AbstractFile, AbstractFilesystem
from .commons import BLOCK_SIZE, READ_FILE_FULL, format_size
from .device.abstract import AbstractDevice

__all__ = [
    "NativeFile",
    "NativeDirectoryEntry",
    "NativeFilesystem",
]


def path_to_str(path: Path) -> str:
    if isinstance(path, PureWindowsPath):
        # Windows path handling
        pwd = str(path)
        if len(pwd) >= 2 and pwd[1] == ":":
            pwd = pwd[2:]  # Remove drive letter
        pwd = pwd.replace("/", "\\")  # Ensure backslashes
        if not pwd.startswith("\\"):
            pwd = "\\" + pwd
        return pwd
    else:
        # POSIX path handling
        pwd = str(path)
        if not pwd.startswith("/"):
            pwd = "/" + pwd
        return pwd


class NativeFile(AbstractFile):
    f: t.Union[io.BufferedReader, io.BufferedRandom]
    sector_size = BLOCK_SIZE

    def __init__(self, filename_or_path: t.Union[str, Path]):
        if isinstance(filename_or_path, Path):
            native_path = filename_or_path
        else:
            native_path = Path(filename_or_path)
        self.filename = path_to_str(native_path.resolve())
        try:
            self.f = native_path.open(mode="rb+")
            self.readonly = False
        except OSError:
            self.f = native_path.open(mode="rb")
            self.readonly = True
        self.size = native_path.stat().st_size
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        """Final path component"""
        return os.path.basename(self.filename)

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        if number_of_blocks == READ_FILE_FULL:
            with self._lock:
                self.f.seek(0)
                return self.f.read()
        if block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        with self._lock:
            position = block_number * self.sector_size
            self.f.seek(position)
            return self.f.read(number_of_blocks * self.sector_size)
            # TODO check
            # buffer = self.f.read(number_of_blocks * self.sector_size)
            # if not buffer:
            #     raise OSError(errno.EIO, os.strerror(errno.EIO))
            # return buffer

    def write_block(
        self,
        buffer: t.Union[bytes, bytearray],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        """
        if block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        if self.readonly:
            raise OSError(errno.EROFS, os.strerror(errno.EROFS))
        with self._lock:
            self.f.seek(block_number * self.sector_size)
            self.f.write(buffer[0 : number_of_blocks * self.sector_size])
            self.f.flush()

    def truncate(self, size: t.Optional[int] = None) -> None:
        """
        Resize the file to the given number of bytes.
        If the size is not specified, the current position will be used.
        """
        self.f.truncate(size)
        if size is not None and self.current_position > size:
            self.current_position = size
        self.f.flush()

    def flush(self) -> None:
        """
        Flush the file's internal buffer to disk
        """
        self.f.flush()

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.size

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return self.sector_size

    def close(self) -> None:
        """
        Close the file
        """
        self.f.close()

    def __str__(self) -> str:
        return self.filename


class NativeDirectoryEntry(XFERXPath):
    fs: "NativeFilesystem"
    native_path: Path
    native_fullname: str
    filename: str
    extension: str
    stat: os.stat_result
    length: int

    def __init__(self, fs: "NativeFilesystem", native_path: Path) -> None:
        self.fs = fs
        self.native_path = native_path
        self.native_fullname = path_to_str(self.native_path)
        self.filename = self.native_path.stem
        self.extension = self.native_path.suffix
        if self.extension.startswith("."):
            self.extension = self.extension[1:]
        self.stat = self.native_path.stat()
        self.length = self.stat.st_size  # length in bytes

    @property
    def creation_date(self) -> datetime:
        return datetime.fromtimestamp(self.stat.st_ctime)

    @property
    def access_date(self) -> datetime:
        return datetime.fromtimestamp(self.stat.st_atime)

    @property
    def last_mod_date(self) -> datetime:
        return datetime.fromtimestamp(self.stat.st_mtime)

    @property
    def fullname(self) -> str:
        return self.native_fullname

    @property
    def name(self) -> str:
        return self.native_path.name

    def is_file(self) -> bool:
        """
        Check if the entry is a regular file
        """
        return self.native_path.is_file()

    def is_dir(self) -> bool:
        """
        Check if the entry is a directory
        """
        return self.native_path.is_dir()

    def is_symlink(self) -> bool:
        """
        Check if the entry is a symbolic link
        """
        return self.native_path.is_symlink()

    @property
    def entry_type(self) -> t.Optional[str]:
        """
        Entry type
        """
        if self.native_path.is_file():
            return "FILE"
        elif self.native_path.is_dir():
            return "DIRECTORY"
        elif self.native_path.is_symlink():
            return "LINK"
        elif self.native_path.is_fifo():
            return "FIFO"
        elif self.native_path.is_socket():
            return "SOCKET"
        elif self.native_path.is_char_device():
            return "CHAR DEV"
        elif self.native_path.is_block_device():
            return "BLOCK DEV"
        else:
            return None

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return int(math.ceil(self.get_size() / self.get_block_size()))

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get file size in bytes
        """
        return self.stat.st_size

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    @property
    def parent(self) -> "NativeDirectoryEntry":
        """
        Get the parent directory entry
        """
        parent_path = self.native_path.parent
        return NativeDirectoryEntry(self.fs, parent_path)

    def delete(self) -> bool:
        """
        Delete the directory entry
        """
        try:
            if self.is_dir():
                self.native_path.rmdir()
            else:
                self.native_path.unlink()
            return True
        except:
            return False

    def write(self) -> bool:
        """
        Write the directory entry
        """
        raise OSError(errno.EINVAL, "Invalid operation on native filesystem")

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> NativeFile:
        """
        Open a file
        """
        return NativeFile(self.fullname)

    def iterdir(
        self,
        pattern: t.Optional[str] = None,
        include_all: bool = False,
        wildcard: bool = True,
    ) -> t.Iterator["NativeDirectoryEntry"]:
        """
        Iterate over all entries in the User File Directory
        """
        if not self.is_dir():
            raise NotADirectoryError(self.fullname)
        for path in self.native_path.glob(pattern or "*"):
            if not include_all and path.name in (".", ".."):
                continue
            try:
                yield NativeDirectoryEntry(self.fs, path)
            except:
                pass

    def __str__(self) -> str:
        return f"{self.fullname:<11} {self.last_mod_date or '':<6} length: {self.length:>6}"


class NativeFilesystem(AbstractFilesystem):
    fs_name = "native"
    fs_description = "Native Filesystem"
    fs_entry_metadata = [
        "creation_date",
        "last_access",
        "last_mod_date",
    ]

    base_path: Path
    pwd: str

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        **kwargs: t.Union[bool, str],
    ) -> "NativeFilesystem":
        if not isinstance(file_or_dev, NativeFile):
            raise OSError(errno.EIO, "Not a native file")
        return cls(base_path=Path(file_or_dev.filename))

    def __init__(self, base_path: t.Optional[Path] = None):
        if base_path:
            self.base_path = base_path.resolve()
            self.pwd = path_to_str(self.base_path)
        else:
            cwd = Path.cwd()
            self.base_path = Path(cwd.anchor)
            self.pwd = path_to_str(cwd)

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
    ) -> t.Iterator["NativeDirectoryEntry"]:
        if not pattern:
            # List all entries in the current directory
            current_path = self.base_path / self.pwd
            for path in current_path.iterdir():
                try:
                    yield NativeDirectoryEntry(self, path)
                except:
                    pass

        else:
            pattern_path = self.base_path / self.pwd / pattern

            # Check if the pattern is a directory
            if pattern_path.is_dir():
                if not expand:  # don't expand directories
                    yield NativeDirectoryEntry(self, pattern_path)
                    return
                pattern_path = pattern_path / "*"

            # glob the pattern
            parent = pattern_path.parent
            glob_pattern = pattern_path.name
            for path in parent.glob(glob_pattern):
                try:
                    yield NativeDirectoryEntry(self, path)
                except:
                    pass

    @property
    def entries_list(self) -> t.Iterator["NativeDirectoryEntry"]:
        dir_path = Path(self.pwd)
        for path in dir_path.iterdir():
            yield NativeDirectoryEntry(self, path)

    def get_file_entry(self, fullname: str) -> "NativeDirectoryEntry":
        """
        Get the file entry for a given path
        """
        path = self.base_path / self.pwd / fullname
        return NativeDirectoryEntry(self, path)

    def get_root(self) -> "NativeDirectoryEntry":
        return NativeDirectoryEntry(self, self.base_path)

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
        path = self.base_path / self.pwd / fullname
        path.write_bytes(content)

        creation_date: t.Union[date, datetime, None] = metadata.get("creation_date")
        if creation_date:
            # Set the creation and modification date of the file
            ts = datetime.combine(creation_date, datetime.min.time()).timestamp()
            os.utime(str(path), (ts, ts))

    def create_file(
        self,
        fullname: str,
        size: int = 0,
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> NativeDirectoryEntry:
        """
        Create a new file with a given length
        """
        metadata = metadata or {}
        path = self.base_path / self.pwd / fullname

        with path.open("wb") as f:
            f.truncate(size)

        creation_date: t.Optional[date] = metadata.get("creation_date")
        if creation_date:
            # Set the creation and modification date of the file
            ts = datetime.combine(creation_date, datetime.min.time()).timestamp()
            os.utime(str(path), (ts, ts))
        return NativeDirectoryEntry(self, path)

    def create_directory(
        self,
        fullname: str,
        options: t.Dict[str, t.Union[bool, str]],
    ) -> NativeDirectoryEntry:
        """
        Create a directory
        """
        path = (self.base_path / self.pwd / fullname).resolve()
        path.mkdir(parents=True, exist_ok=False)  # May raise OSError in case of errors
        return NativeDirectoryEntry(self, path)

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        """
        Change the current working directory
        """
        if not entry:
            self.pwd = path_to_str(self.base_path)
            os.chdir(self.base_path)
        else:
            assert isinstance(entry, NativeDirectoryEntry)
            path = (self.base_path / self.pwd / entry.native_path).resolve()
            if not path.is_dir():
                raise NotADirectoryError(path)
            self.pwd = path_to_str(path)
            os.chdir(path)

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List directory entries
        """
        for x in self.filter_entries_list(pattern):
            et = f"{format_size(x.get_size()):>15s}" if x.is_file() else x.entry_type
            dt = x.last_mod_date.strftime("%d-%b-%Y %H:%M ") if x.last_mod_date else ""
            sys.stdout.write(f"{et:>15s} {dt:>19s} {x.name}\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        pass

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.base_path.stat().st_size

    def get_pwd(self) -> str:
        """
        Get the current working directory
        """
        return self.pwd

    def __str__(self) -> str:
        return str(self.base_path)
