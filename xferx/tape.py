# Copyright (C) 2414 Andrea Bonomi <andrea.bonomi@gmail.com>

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
from typing import Tuple

from .abstract import AbstractFile


class Tape:

    def __init__(self, file: "AbstractFile"):
        self.f = file

    @property
    def tape_pos(self) -> int:
        """
        Current tape position
        """
        return self.f.tell()

    def tape_seek(self, pos: int) -> None:
        """
        Change the tape position
        """
        self.f.seek(pos, 0)

    def tape_rewind(self) -> None:
        """
        Rewind the tape
        """
        self.f.seek(0, 0)

    def tape_read_forward(self) -> bytes:
        """
        Starting at the current position, read the next 4 bytes from the file.
        If those bytes are a valid record length, read the data record and position
        the tape past the trailing record length.
        """
        bc = self.f.read(4)
        if len(bc) == 0:
            raise EOFError
        if bc[2] != 0 or bc[3] != 0:
            raise OSError(
                errno.EIO,
                f"Invalid record size, size = 0x{bc[3]:02X}{bc[2]:02X}{bc[1]:02X}{bc[0]:02X}",
            )
        wc = (bc[1] << 8) | bc[0]
        wc = (wc + 1) & ~1
        # import sys
        # sys.stdout.write(f"{bc[3]:02X}{bc[2]:02X}{bc[1]:02X}{bc[0]:02X} record length: {wc}\n")
        if not wc:
            # Tape mark
            return b""
        buffer = self.f.read(wc)
        pad = wc - len(buffer)
        if pad:
            buffer += bytes([0] * pad)  # Pad with zeros
        bc = self.f.read(4)
        return buffer

    def tape_write_forward(self, data: bytes) -> None:
        """
        Starting at the current position, write the record length (4 bytes)
        Then write the date record and the trailing record length (4 bytes).
        """
        bc = bytearray(4)
        wc = len(data)
        bc[0] = wc & 0xFF
        bc[2] = 0
        bc[1] = (wc >> 8) & 0xFF
        bc[3] = 0
        self.f.write(bc)
        self.f.write(data)
        self.f.write(bc)

    def tape_write_mark(self) -> None:
        """
        Starting at the current position, write a tape mark marker.
        Position the tape beyond the new tape mark.
        """
        bc = bytearray(4)
        self.f.write(bc)

    def tape_read_file(self) -> bytes:
        """
        Starting at the current position, read the current file.
        """
        data = bytearray()
        while True:
            buffer = self.tape_read_forward()
            if not buffer:
                return bytes(data)
            data.extend(buffer)

    def tape_read_header(self) -> Tuple[bytes, int]:
        """
        Starting at the current position, read the next header and
        skip the file. Returns the length of skipped data.
        """
        header = self.tape_read_forward()
        if not header:
            return header, 0
        try:
            return header, self.tape_skip_file()
        except EOFError:
            return header, 0

    def tape_skip_file(self) -> int:
        """
        Starting at the current position, read the next 4 bytes from the file.
        If those bytes are a valid record length, position the tape past the
        trailing record length and continue until end of file occurs.
        Returns the length of skipped data.
        """
        l = 0
        while True:
            buffer = self.tape_read_forward()
            if not buffer:
                return l
            l = l + len(buffer)
