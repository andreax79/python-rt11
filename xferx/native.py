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
import glob
import io
import math
import os
import stat
import sys
import typing as t
from datetime import date, datetime

from .abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from .commons import BLOCK_SIZE, READ_FILE_FULL

__all__ = [
    "NativeFile",
    "NativeDirectoryEntry",
    "NativeFilesystem",
]


class NativeFile(AbstractFile):

    f: t.Union[io.BufferedReader, io.BufferedRandom]

    def __init__(self, filename: str):
        self.filename = os.path.abspath(filename)
        try:
            self.f = open(filename, mode="rb+")
            self.readonly = False
        except OSError:
            self.f = open(filename, mode="rb")
            self.readonly = True
        self.size = os.path.getsize(filename)

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        if number_of_blocks == READ_FILE_FULL:
            self.f.seek(0)  # not thread safe...
            return self.f.read()
        elif block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        else:
            position = block_number * BLOCK_SIZE
            self.f.seek(position)  # not thread safe...
            return self.f.read(number_of_blocks * BLOCK_SIZE)

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        """
        if block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        elif self.readonly:
            raise OSError(errno.EROFS, os.strerror(errno.EROFS))
        else:
            self.f.seek(block_number * BLOCK_SIZE)  # not thread safe...
            self.f.write(buffer[0 : number_of_blocks * BLOCK_SIZE])

    def truncate(self, size: t.Optional[int] = None) -> None:
        """
        Resize the file to the given number of bytes.
        If the size is not specified, the current position will be used.
        """
        self.f.truncate(size)
        if size is not None and self.current_position > size:
            self.current_position = size

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.size

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    def close(self) -> None:
        """
        Close the file
        """
        self.f.close()

    def __str__(self) -> str:
        return self.filename


class NativeDirectoryEntry(AbstractDirectoryEntry):

    def __init__(self, fullname: str):
        self.native_fullname = fullname
        self.filename = os.path.basename(fullname)
        self.filename, self.extension = os.path.splitext(self.filename)
        if self.extension.startswith("."):
            self.extension = self.filename[1:]
        self.stat = os.stat(fullname)
        self.length = self.stat.st_size  # length in bytes

    @property
    def creation_date(self) -> date:
        return datetime.fromtimestamp(self.stat.st_mtime)

    @property
    def fullname(self) -> str:
        return self.native_fullname

    @property
    def basename(self) -> str:
        return os.path.basename(self.native_fullname)

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return int(math.ceil(self.get_size() / self.get_block_size()))

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.stat.st_size

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    def delete(self) -> bool:
        """
        Delete the directory entry
        """
        try:
            os.unlink(self.native_fullname)
            return True
        except:
            return False

    def write(self) -> bool:
        """
        Write the directory entry
        """
        raise OSError(errno.EINVAL, "Invalid operation on native filesystem")

    def open(self, file_mode: t.Optional[str] = None) -> NativeFile:
        """
        Open a file
        """
        return NativeFile(self.fullname)

    def __str__(self) -> str:
        return f"{self.fullname:<11} {self.creation_date or '':<6} length: {self.length:>6}"


class NativeFilesystem(AbstractFilesystem):

    @classmethod
    def mount(cls, file: "AbstractFile") -> "AbstractFilesystem":
        assert isinstance(file, NativeFile)
        return cls(base=file.filename)

    def __init__(self, base: t.Optional[str] = None):
        self.base = base or "/"
        if not base:
            self.pwd = os.getcwd()
        elif os.getcwd().startswith(base):
            self.pwd = os.getcwd()[len(base) :]
        else:
            self.pwd = os.path.sep

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
    ) -> t.Iterator["NativeDirectoryEntry"]:
        if not pattern:
            for filename in os.listdir(os.path.join(self.base, self.pwd)):
                try:
                    v = NativeDirectoryEntry(os.path.join(self.base, self.pwd, filename))
                except:
                    v = None
                if v is not None:
                    yield v
        else:
            if not pattern.startswith("/") and not pattern.startswith("\\"):
                pattern = os.path.join(self.base, self.pwd, pattern)
            if os.path.isdir(pattern):
                if not expand:  # don't expand directories
                    yield NativeDirectoryEntry(pattern)
                    return
                pattern = os.path.join(pattern, "*")
            for filename in glob.glob(pattern):
                try:
                    v = NativeDirectoryEntry(filename)
                except:
                    v = None
                if v is not None:
                    yield v

    @property
    def entries_list(self) -> t.Iterator["NativeDirectoryEntry"]:
        dir = self.pwd
        for filename in os.listdir(dir):
            yield NativeDirectoryEntry(os.path.join(dir, filename))

    def get_file_entry(self, fullname: str) -> t.Optional[NativeDirectoryEntry]:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        return NativeDirectoryEntry(fullname)

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: t.Optional[date] = None,
        file_type: t.Optional[str] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        with open(fullname, "wb") as f:
            f.write(content)
        if creation_date:
            # Set the creation and modification date of the file
            ts = datetime.combine(creation_date, datetime.min.time()).timestamp()
            os.utime(fullname, (ts, ts))

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,
        creation_date: t.Optional[date] = None,
        file_type: t.Optional[str] = None,
    ) -> t.Optional[NativeDirectoryEntry]:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        with open(fullname, "wb") as f:
            f.truncate(number_of_blocks * BLOCK_SIZE)
        if creation_date:
            # Set the creation and modification date of the file
            ts = datetime.combine(creation_date, datetime.min.time()).timestamp()
            os.utime(fullname, (ts, ts))
        return NativeDirectoryEntry(fullname)

    def chdir(self, fullname: str) -> bool:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        fullname = os.path.normpath(fullname)
        if os.path.isdir(os.path.join(self.base, fullname)):
            self.pwd = fullname
            # Change the current working directory
            os.chdir(os.path.join(self.base, fullname))
            return True
        else:
            return False

    def isdir(self, fullname: str) -> bool:
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        return os.path.isdir(os.path.join(self.base, fullname))

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        if options.get("brief"):
            # Lists only file names and file types
            for x in self.filter_entries_list(pattern):
                sys.stdout.write(f"{x.basename}\n")
            return
        for x in self.filter_entries_list(pattern):
            mode = x.stat.st_mode
            if stat.S_ISREG(mode):
                type = "%s" % x.length
            elif stat.S_ISDIR(mode):
                type = "DIRECTORY      "
            elif stat.S_ISLNK(mode):
                type = "LINK           "
            elif stat.S_ISFIFO(mode):
                type = "FIFO           "
            elif stat.S_ISSOCK(mode):
                type = "SOCKET         "
            elif stat.S_ISCHR(mode):
                type = "CHAR DEV       "
            elif stat.S_ISBLK(mode):
                type = "BLOCK DEV      "
            else:
                type = "?"
            sys.stdout.write(
                "%15s %19s %s\n"
                % (
                    type,
                    x.creation_date and x.creation_date.strftime("%d-%b-%Y %H:%M ") or "",
                    x.basename,
                )
            )

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        pass

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        stat = os.statvfs(self.base)
        return stat.f_frsize * stat.f_blocks

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        pass

    def close(self) -> None:
        pass

    def get_pwd(self) -> str:
        return self.pwd

    def __str__(self) -> str:
        return self.base
