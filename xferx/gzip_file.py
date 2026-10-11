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
import os
import struct
import threading
import typing as t
import zlib
from gzip import _GzipReader

from .abstract import AbstractFile
from .commons import BLOCK_SIZE, READ_FILE_FULL, cache

__all__ = ["GzipFile"]


class GzipFile(AbstractFile):
    sector_size = BLOCK_SIZE
    file_name: str

    def __init__(self, gzip_file: AbstractFile):
        self.file_name = gzip_file.name
        self._buffered_reader = io.BufferedReader(_GzipReader(gzip_file))
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        """Final path component"""
        return self.file_name

    def read(self, size: t.Optional[int] = None) -> bytes:
        """Read bytes from the file, starting at the current position"""
        if size is None:
            size = -1
        return self._buffered_reader.read(size)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> None:
        """Move the current position in the file to a new location"""
        self._buffered_reader.seek(offset, whence)

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
                self.seek(0)
                return self.read()
        if block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        with self._lock:
            position = block_number * self.sector_size
            self.seek(position)
            return self.read(number_of_blocks * self.sector_size)

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

    def truncate(self, size: t.Optional[int] = None) -> None:
        """
        Resize the file to the given number of bytes.
        If the size is not specified, the current position will be used.
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def flush(self) -> None:
        """
        Flush the file's internal buffer to disk
        """
        pass

    @cache
    def get_size(self) -> int:
        """Get the size of the uncompressed data in bytes"""
        self._buffered_reader.seek(0)  # rewind
        size = 0
        while True:
            chunk = self.read(1024 * 1024)  # 1 MB chunks
            if not chunk:
                return size
            size += len(chunk)

    def get_block_size(self) -> int:
        """Get file block size in bytes"""
        return self.sector_size

    def close(self) -> None:
        """
        Close the file
        """
        self._buffered_reader.close()
