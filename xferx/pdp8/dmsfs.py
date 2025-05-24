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
import fnmatch
import io
import math
import os
import re
import struct
import sys
import typing as t
from datetime import date

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..block import BlockDevice12Bit
from ..commons import ASCII, IMAGE, READ_FILE_FULL

__all__ = [
    "DMSFile",
    "DMSDirectoryEntry",
    "DMSFilesystem",
]

# PDP-8 4k Disk Monitor System
# https://svn.so-much-stuff.com/svn/trunk/pdp8/src/dec/dec-08-odsma/dec-08-odsma-a-d.pdf

# PDP-8 Disc System Builder
# https://svn.so-much-stuff.com/svn/trunk/pdp8/src/dec/dec-d8-sba/dec-d8-sbab-d.pdf


BLOCK_SIZE_WORD = 129  # Block size (in words)
BYTES_PER_WORD = 2  # Each word is encoded in 2 bytes
DATA_BLOCK_SIZE_WORD = BLOCK_SIZE_WORD - 1  # The last word of the block is the link to the next block

DN_ENTRY_SIZE = 5  # DN entry size (words)
DN_ENTRIES = 25  # Number of directory entries
DN_START = 0o177  # DN start block number

EMPTY_FILE_NUMBER = 0  # Empty file number
RESERVED_FILE_NUMBER = 1  # Reserved for monitor, DN, SAM, and scratch blocks
MAX_FILE_NUMBER = 0o77  # Max file number
MONITOR_FILENAME = "EX C"  # Monitor file name
INVALID_FILENAMES = set(["CALL", "SAVE"])  # Invalid filenames

FILE_TYPE_ASCII = 0o0  # ASCII file (6-bit ASCII)
FILE_TYPE_BIN = 0o1  # Binary file
FILE_TYPE_FTC_BIN = 0o2  # Fortran binary file
FILE_TYPE_SYS_USER = 0o3  # Saved file (SYS or USER)

EXT_SYS = "SYS"  # System file extension
EXT_USER = "USER"  # User file extension
EXT_ASCII = "ASCII"  # ASCII file extension
EXT_BINARY = "BINARY"  # Binary file extension
EXT_FTC_BIN = "FTC BIN"  # Fortran binary file extension (yes, with a space)

RE_FILENAME = re.compile(r"([^!;:]+)\s*(?:\:(\s*\d+))?(?:;(\s*\d+))?")


class DMSFilename:
    """
    Parse the canonical PDP-8 4k Disk Monitor System filename
    """

    filename: str = ""
    program_type: int = 0
    system_program: bool = False
    core_addr: int = 0o200
    entry_point: int = 0

    def __init__(self, fullname: str, file_type: t.Optional[str] = None, wildcard: bool = False):
        """
        Filenames are limited to four characters and can be composed of
        any combination of alphanumeric characters or special characters.
        Extensions are automatically appended and depend on the file type.

        The extensions are:
        - SYS (n) Saved system program file in core bank n
        - USER (n) Saved user program file in core bank n
        - ASCII Source language program file
        - BINARY Binary program file (output from PAL-D Assembler)
        - FTC BIN Interpretive binary file (output from FORTRAN Compiler)

        The filename can be followed by a core address and an entry point:
        filename.extension[:core_addr][;entry_point]
        """
        fullname = fullname.upper()
        match = RE_FILENAME.match(fullname)
        if match:
            fullname = match.group(1)
            if match.group(2):
                self.core_addr = int(match.group(2), 8)
            if match.group(3):
                self.entry_point = int(match.group(3), 8)
        try:
            filename, extension = fullname.rsplit(".", 1)
            extension = extension.upper()
        except:
            filename = fullname
            extension = None
        self.filename = sixbit_word12_to_asc(asc_to_sixbit_word12(filename[0:2]))
        self.filename += sixbit_word12_to_asc(asc_to_sixbit_word12(filename[2:4]))
        # A file name cannot be one of the following: "CALL", "SAVE"
        if self.filename in INVALID_FILENAMES:
            raise OSError(errno.EINVAL, "Invalid filename")
        if file_type == ASCII:
            self.program_type = FILE_TYPE_ASCII
            self.system_program = False
        elif wildcard and extension == "*":
            self.program_type = -1  # Wildcard
        elif extension == EXT_ASCII:
            self.program_type = FILE_TYPE_ASCII
            self.system_program = False
        elif extension == EXT_BINARY:
            self.program_type = FILE_TYPE_BIN
            self.system_program = False
        elif extension == EXT_FTC_BIN:
            self.program_type = FILE_TYPE_FTC_BIN
            self.system_program = False
        elif extension == EXT_SYS:
            self.program_type = FILE_TYPE_SYS_USER
            self.system_program = True
        elif extension == EXT_USER:
            self.program_type = FILE_TYPE_SYS_USER
            self.system_program = False
        else:
            raise OSError(errno.EINVAL, "Invalid file extension")

    def match(self, entry: "DMSDirectoryEntry") -> bool:
        """
        Match the filename with a directory entry
        """
        result = fnmatch.fnmatch(entry.filename.strip(), self.filename.strip())
        if self.program_type != -1:
            result &= self.program_type == entry.program_type and self.system_program == entry.system_program
        return result


def from_12bit_words_to_bytes(words: list[int], file_mode: str = ASCII) -> bytes:
    """
    Convert 12bit words to bytes

    https://svn.so-much-stuff.com/svn/trunk/pdp8/src/dec/dec-08-odsma/dec-08-odsma-a-d.pdf Pag 105
    """
    result = bytearray()

    if file_mode == ASCII:
        esc = False
        eof = False
        for word in words:
            if word == 0:
                continue
            h = word & 0o77
            l = (word >> 6) & 0o77
            for ch in (l, h):
                if esc:
                    if ch == 0o77:  # ? - Question mark
                        result.append(0o77)
                    elif ch == 0x09:  # I - Tab
                        result.append(0x09)
                    elif ch == 0x0A:  # J - LF (Line Feed)
                        result.append(0x0A)
                    elif ch == 0x0C:  # FF (Form Feed)
                        eof = True
                        break  # end of file
                    elif ch == 0x0D:  # M - CR (Carriage Return)
                        pass
                    # else:
                    #     result.append(32)
                    #     result += f"[{ch:02x}]".encode("ascii")
                    #     result.append(32)
                    esc = False
                elif ch == 0o77:
                    esc = True
                else:
                    if ch < 32:
                        ch += 64
                    result.append(ch)
            if eof:
                break
    else:
        for i in range(0, len(words), 2):
            chr1 = words[i]
            try:
                chr2 = words[i + 1]
            except IndexError:
                chr2 = 0
            chr3 = ((chr2 >> 8) & 0o17) | ((chr1 >> 4) & 0o360)
            result.append(chr1 & 0xFF)
            result.append(chr2 & 0xFF)
            result.append(chr3 & 0xFF)

    return bytes(result)


def from_bytes_to_12bit_words(byte_data: bytes, file_mode: str = "ASCII") -> t.List[int]:
    """
    Convert bytes to 12-bit words.
    """
    words = []

    if file_mode == ASCII:
        buffer = []
        for byte in byte_data:
            byte = byte & 0o177
            if byte == 0x0A:  # LF (Line Feed) => LF + CR
                buffer.append(0o77)
                buffer.append(0x0D)
                buffer.append(0o77)
                buffer.append(0x0A)
            elif byte == 0x0D:  # CR (Carriage Return)
                pass
            elif byte in (0o77, 0x09, 0x0C):
                buffer.append(0o77)
                buffer.append(byte)
            else:
                if byte > 64:
                    byte -= 64
                buffer.append(byte)
        for i in range(0, len(buffer), 2):
            l = buffer[i]
            try:
                h = buffer[i + 1]
            except IndexError:
                h = 0
            word = (h & 0o77) | ((l & 0o77) << 6)
            words.append(word)

    else:
        for i in range(0, len(byte_data), 3):
            chr1 = byte_data[i] & 0xFF
            try:
                chr2 = byte_data[i + 1] & 0xFF
            except IndexError:
                chr2 = 0
            try:
                chr3 = byte_data[i + 2] & 0xFF
            except IndexError:
                chr3 = 0
            words.append(chr1 | ((chr3 & 0o360) << 4))
            words.append(chr2 | ((chr3 & 0o17) << 8))
    return words


def sixbit_word12_to_asc(val: int) -> str:
    """
    Convert six bit ASCII 12 bit word to 2 chars of ASCII
    """
    h = val & 0o77
    l = (val >> 6) & 0o77
    return chr(l + 32) + chr(h + 32)


def asc_to_sixbit_word12(val: str) -> int:
    """
    Convert 2 chars of ASCII back to six bit ASCII 12 bit word
    """
    if len(val) >= 1:
        l = ord(val[0].upper()) - 32
    else:
        l = 0
    if len(val) >= 2:
        h = ord(val[1].upper()) - 32
    else:
        h = 0
    return (l << 6) | h


def oct_dump(words: t.List[int], words_per_line: int = 8) -> None:
    """
    Display contents in octal
    """
    for i in range(0, len(words), words_per_line):
        line = words[i : i + words_per_line]
        ascii_str = "".join([chr(x) if 32 <= x <= 126 else "." for x in from_12bit_words_to_bytes(line)])
        oct_str = " ".join([f"{x:04o}" for x in line])
        sys.stdout.write(f"{i:08o}   {oct_str.ljust(5 * words_per_line)}  {ascii_str}\n")


class DMSFile(AbstractFile):
    entry: "DMSDirectoryEntry"
    file_mode: str
    closed: bool

    def __init__(self, entry: "DMSDirectoryEntry", file_mode: t.Optional[str] = None):
        self.entry = entry
        if file_mode is not None:
            self.file_mode = file_mode
        else:
            self.file_mode = ASCII if entry.extension.upper() == EXT_ASCII else IMAGE
        self.closed = False

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        length = self.entry.get_length()
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = length
        if self.closed or block_number < 0 or number_of_blocks < 0 or block_number + number_of_blocks > length:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        data = bytearray()
        # Get the blocks to be read
        blocks = list(self.entry.get_blocks())[block_number : block_number + number_of_blocks]
        for disk_block_number in blocks:
            words = self.entry.dn.fs.read_12bit_words_block(disk_block_number)
            # Skip the last word of the block (the link to the next block)
            t = from_12bit_words_to_bytes(words[:-1], self.file_mode)
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
        words = from_bytes_to_12bit_words(buffer, self.file_mode)
        # Get the blocks to be written
        blocks = list(self.entry.get_blocks())[block_number : block_number + number_of_blocks]
        # Write the blocks
        for i, disk_block_number in enumerate(blocks):
            block_words = words[i * DATA_BLOCK_SIZE_WORD : (i + 1) * DATA_BLOCK_SIZE_WORD]
            block_words.extend([0] * (BLOCK_SIZE_WORD - len(block_words)))
            # The last word of the block is the link to the next block
            block_words[-1] = blocks[i + 1] if i + 1 < len(blocks) else 0
            self.entry.dn.fs.write_12bit_words_block(disk_block_number, block_words)

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


class StorageAllocationMapBlock:
    """
    Storage Allocation Map (SAM) Block

    SAM blocks contain a record of which files are occupying
    which blocks on the device.

    https://svn.so-much-stuff.com/svn/trunk/pdp8/src/dec/dec-08-odsma/dec-08-odsma-a-d.pdf Pag 103

               6 bits            6 bits
        +-----------------------------------+
      1 | Block 128       |         Block 0 |
        | ...             |             ... |
        | Block 255       |       Block 127 |
        +-----------------------------------+
        | Link to next SAM block            |
        +-----------------------------------+

    The value of each half-work is the internal file number of the file
    occupying that block.

    Special values:
    0 - Block is free
    1 - Monitor, DN, SAM and scratch blocks
    4 - Loader blocks
    5 - Command decoder blocks
    """

    fs: "DMSFilesystem"
    # Block number
    block_number: int
    # Block sequence number
    block_seq_nr: int = 0
    # Link to next SAM block
    next_sam_block_number: int = 0
    # Storage Allocation Map
    sam: t.List[int] = []  # Block number => file number

    def __init__(self, fs: "DMSFilesystem"):
        self.fs = fs
        self.sam = [0] * 256

    @classmethod
    def read(cls, fs: "DMSFilesystem", block_number: int, block_seq_nr: int) -> "StorageAllocationMapBlock":
        """
        Read a Storage Allocation Map (SAM) Block
        """
        self = cls(fs)
        self.block_number = block_number
        self.block_seq_nr = block_seq_nr
        words = self.fs.read_12bit_words_block(block_number)
        for i in range(0, 128):
            self.sam[i] = words[i] & 0o77
            self.sam[i + 128] = words[i] >> 6
        self.next_sam_block_number = words[128]
        return self

    def write(self) -> None:
        """
        Write the Storage Allocation Map (SAM) Block
        """
        words = []
        assert len(self.sam) == 256
        for i in range(0, 128):
            words.append((self.sam[i] & 0o77) | ((self.sam[i + 128] & 0o77) << 6))
        words.append(self.next_sam_block_number)
        self.fs.write_12bit_words_block(self.block_number, words)

    def set_block(self, block_number: int, file_number: int) -> None:
        """
        Allocate a block to a file
        """
        self.sam[block_number - self.block_seq_nr * 256] = file_number

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        return self.sam.count(EMPTY_FILE_NUMBER)


class StorageAllocationMap:
    """
    Storage Allocation Map (SAM)
    """

    fs: "DMSFilesystem"
    files_blocks: t.Dict[int, t.List[int]]  # file number => blocks
    sam_blocks: t.List["StorageAllocationMapBlock"]

    def __init__(self, fs: "DMSFilesystem"):
        self.fs = fs
        self.files_blocks = {}

    @classmethod
    def read(cls, fs: "DMSFilesystem") -> "StorageAllocationMap":
        self = StorageAllocationMap(fs)
        self.sam_blocks = []
        for sam_nr, sam in enumerate(self.read_storage_allocation_map_blocks()):
            for block_number, file_number in enumerate(sam.sam, start=sam_nr * 256):
                if file_number:
                    if file_number not in self.files_blocks:
                        self.files_blocks[file_number] = []
                    self.files_blocks[file_number].append(block_number)
            self.sam_blocks.append(sam)
        return self

    def write(self) -> None:
        """
        Write the Storage Allocation Map (SAM) Blocks
        """
        for sam in self.sam_blocks:
            sam.write()

    def read_storage_allocation_map_blocks(self) -> t.Iterator["StorageAllocationMapBlock"]:
        """
        Read the Storage Allocation Map (SAM) Blocks
        """
        next_block_number = self.fs.first_sam_block_number
        block_seq_nr = 0
        while next_block_number != 0:
            sam = StorageAllocationMapBlock.read(self.fs, next_block_number, block_seq_nr)
            next_block_number = sam.next_sam_block_number
            block_seq_nr += 1
            yield sam

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        free_blocks = 0
        for sam in self.sam_blocks:
            free_blocks += sam.free()
        return free_blocks

    def set_block(self, block_number: int, file_number: int) -> None:
        """
        Allocate a block to a file
        """
        for sam_nr, sam in enumerate(self.sam_blocks):
            if block_number >= sam_nr * 256 and block_number < (sam_nr + 1) * 256:
                sam.set_block(block_number, file_number)
                if file_number not in self.files_blocks:
                    self.files_blocks[file_number] = []
                self.files_blocks[file_number].append(block_number)
                break

    def allocate_space(
        self,
        fullname: str,
        length: int,  # length in blocks
    ) -> int:
        """
        Allocate space for a file, return the new file number
        """
        if length > self.free():
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), fullname)
        # Find a free file number
        used_file_numbers = set(self.files_blocks.keys())
        new_file_number = None
        for i in range(2, MAX_FILE_NUMBER + 1):  # Skip reserved file numbers
            if i not in used_file_numbers:
                new_file_number = i
                break
        if new_file_number is None:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), fullname)
        # Allocate space
        blocks = []
        for sam_nr, sam in enumerate(self.sam_blocks):
            for block_number, file_number in enumerate(sam.sam, start=sam_nr * 256):
                if file_number == 0:
                    blocks.append(block_number)
                    sam.set_block(block_number, new_file_number)
                    length -= 1
                    if length == 0:
                        break
            if length == 0:
                break
        self.files_blocks[new_file_number] = blocks
        return new_file_number

    def free_space(self, file_number: int) -> None:
        """
        Free block allocated to a file
        """
        for sam_nr, sam in enumerate(self.sam_blocks):
            for block_number, fn in enumerate(sam.sam, start=sam_nr * 256):
                if fn == file_number:
                    sam.set_block(block_number, EMPTY_FILE_NUMBER)
        if file_number in self.files_blocks:
            del self.files_blocks[file_number]


class DMSDirectoryEntry(AbstractDirectoryEntry):
    """
    Directory Name Entry

    https://svn.so-much-stuff.com/svn/trunk/pdp8/src/dec/dec-08-odsma/dec-08-odsma-a-d.pdf Pag 102

        +-----------------------------------+
      0 | File name char 1-2                |
      1 | File name char 3-4                |
      2 | Low core addr / -1 not contiguous |
      3 | Entry point                       |
      4 | Flags                             |
        +-----------------------------------+

    The flags are as follows:
         0 1 2 3 4 5 6 7 8 9 10 11

         0-1  - Program type
         2-4  - Extended memory bits
         5    - System program
         6-11 - File number

    """

    dn: "DirectorNameBlock"
    low_core_addr: int = 0  # Core address low bits
    high_core_addr: int = 0  # Core bank (core address high bits)
    entry_point: int  # Entry point
    filename: str = ""  # Filename (4 chars)
    file_number: int = 0  # 0-63 (6 bits), 0 = Empty
    system_program: bool = False  # System/User program
    program_type: int = 0  # FILE_TYPE_ASCII, FILE_TYPE_BIN, FILE_TYPE_FTC_BIN, FILE_TYPE_SYS_USER

    def __init__(self, dn: "DirectorNameBlock", sam: "StorageAllocationMap"):
        self.dn = dn
        self.sam = sam

    @classmethod
    def read(
        cls,
        dn: "DirectorNameBlock",
        sam: "StorageAllocationMap",
        words: t.List[int],
        position: int,
    ) -> "DMSDirectoryEntry":
        self = cls(dn, sam)
        n1 = words[0 + position]  # Filename char 1-2
        n2 = words[1 + position]  # Filename char 3-4
        self.low_core_addr = words[2 + position]  # Low core addr / -1 not contiguous
        self.entry_point = words[3 + position]  # Entry point
        flags = words[4 + position]  # Flags
        self.filename = sixbit_word12_to_asc(n1) + sixbit_word12_to_asc(n2)
        self.program_type = flags >> 10
        self.high_core_addr = (flags >> 7) & 0o7
        self.file_number = flags & 0o77
        self.system_program = bool(flags >> 6 & 1)
        return self

    def to_words(self) -> t.List[int]:
        """
        Write the directory entry
        """
        flags = self.program_type << 10 | self.high_core_addr << 7 | self.system_program << 6 | self.file_number
        return [
            asc_to_sixbit_word12(self.filename[0:2]),
            asc_to_sixbit_word12(self.filename[2:4]),
            self.low_core_addr & 0o7777,
            self.entry_point & 0o7777,
            flags & 0o7777,
        ]

    def read_bytes(self, file_mode: t.Optional[str] = None) -> bytes:
        """Get the content of the file"""
        if file_mode is None:
            if self.program_type == FILE_TYPE_ASCII:
                file_mode = ASCII
            else:
                file_mode = IMAGE
        # Always read the file as IMAGE
        f = self.open(IMAGE)
        try:
            data = f.read_block(0, READ_FILE_FULL)
            if file_mode == ASCII:
                words = from_bytes_to_12bit_words(data, file_mode=IMAGE)
                return from_12bit_words_to_bytes(words, file_mode=ASCII)
            else:
                return data
        finally:
            f.close()

    @property
    def is_empty(self) -> bool:
        return self.file_number == EMPTY_FILE_NUMBER

    @property
    def extension(self) -> str:
        if self.program_type == FILE_TYPE_ASCII:  # ASCII file (6-bit ASCII)
            return EXT_ASCII
        elif self.program_type == FILE_TYPE_BIN:  # Binary file
            return EXT_BINARY
        elif self.program_type == FILE_TYPE_FTC_BIN:  # Fortran binary file
            return EXT_FTC_BIN
        else:  # Saved file (SYS or USER)
            return EXT_SYS if self.system_program else EXT_USER

    @property
    def fullname(self) -> str:
        return f"{self.filename}.{self.extension}"

    @property
    def basename(self) -> str:
        return self.fullname

    def get_blocks(self) -> t.List[int]:
        return self.sam.files_blocks.get(self.file_number, [])

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return len(self.get_blocks())

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.get_length() * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        # The last word of the block is the link to the next block
        return (BLOCK_SIZE_WORD - 1) * BYTES_PER_WORD

    @property
    def creation_date(self) -> t.Optional[date]:
        return None

    def delete(self) -> bool:
        """
        Delete the file
        """
        if self.file_number in (EMPTY_FILE_NUMBER, RESERVED_FILE_NUMBER):
            return False
        else:
            self.sam.free_space(self.file_number)
            self.sam.write()
            self.low_core_addr = 0
            self.entry_point = 0
            self.filename = ""
            self.file_number = EMPTY_FILE_NUMBER
            self.system_program = False
            self.high_core_addr = 0
            self.program_type = 0
            self.dn.write()
            return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        self.dn.write()
        return True

    def open(self, file_mode: t.Optional[str] = None) -> DMSFile:
        """
        Open a file
        """
        return DMSFile(self, file_mode)

    def __str__(self) -> str:
        return f"{self.fullname:<14} #{self.file_number:02d}  {self.low_core_addr:04o}  {self.entry_point:>04o}  {self.high_core_addr:>o}"

    def __repr__(self) -> str:
        return str(self)


class DirectorNameBlock:
    """
    Directory Name (DN) Block

    https://svn.so-much-stuff.com/svn/trunk/pdp8/src/dec/dec-08-odsma/dec-08-odsma-a-d.pdf Pag 102

        +-----------------------------------+
      0 | First scratch block number        | | First |  Disk = 0o373, DecTape = 0o005
      1 | 2-Digit version number            | | DN    |
      2 | First SAM block number            | | Only  |  = 0o200
        +-----------------------------------+
      3 | DN Entry 1                        |
        | ...                               |
        | DN Entry 25                       |
        +-----------------------------------+
        | Link to next directory name       |
        +-----------------------------------+
    """

    fs: "DMSFilesystem"
    # Block number
    block_number: int = 0
    # Block sequence number
    block_seq_nr: int = 0
    # First scratch block number
    first_scratch_block_number: int = 0
    # Version number
    version_number: int = 0
    # First SAM block number
    first_sam_block_number: int = 0
    # Link to next directory name
    next_directory_name: int = 0
    # Directory entries
    entries: t.Dict[int, "DMSDirectoryEntry"]  # File number => DMSDirectoryEntry

    def __init__(self, fs: "DMSFilesystem"):
        self.fs = fs
        self.entries = {}

    @classmethod
    def read(
        cls, fs: "DMSFilesystem", block_number: int, block_seq_nr: int, sam: t.Optional["StorageAllocationMap"] = None
    ) -> "DirectorNameBlock":
        """
        Read a Directory Name Block from disk
        """
        self = cls(fs)
        words = self.fs.read_12bit_words_block(block_number)
        self.block_number = block_number
        self.block_seq_nr = block_seq_nr
        self.first_scratch_block_number = words[0]
        self.version_number = words[1]
        self.first_sam_block_number = words[2]
        self.next_directory_name = words[3 + DN_ENTRIES * DN_ENTRY_SIZE]
        if sam is not None:
            for position in range(3, 3 + DN_ENTRIES * DN_ENTRY_SIZE, DN_ENTRY_SIZE):
                dir_entry = DMSDirectoryEntry.read(self, sam, words, position)
                if not dir_entry.is_empty:
                    self.entries[dir_entry.file_number] = dir_entry
        return self

    def write(self) -> None:
        """
        Write Directory Name Block
        """
        words = [
            self.first_scratch_block_number,
            self.version_number,
            self.first_sam_block_number,
        ]
        for file_number in range(self.block_seq_nr * DN_ENTRIES + 1, (self.block_seq_nr + 1) * DN_ENTRIES + 1):
            entry = self.entries.get(file_number)
            if entry is not None and not entry.is_empty:
                words.extend(entry.to_words())
            else:
                words.extend([0] * DN_ENTRY_SIZE)
        words.append(self.next_directory_name)
        assert len(words) == BLOCK_SIZE_WORD
        self.fs.write_12bit_words_block(self.block_number, words)

    @property
    def first_file_number(self) -> int:
        """
        First file number in this segment
        """
        return self.block_seq_nr * DN_ENTRIES + 1

    @property
    def last_file_number(self) -> int:
        """
        Last file number in this segment
        """
        return min((self.block_seq_nr + 1) * DN_ENTRIES, MAX_FILE_NUMBER)

    @property
    def number_of_entries(self) -> int:
        """
        Number of directory entries in this segment
        """
        return len(self.entries_list)

    @property
    def entries_list(self) -> t.List["DMSDirectoryEntry"]:
        return [x for x in self.entries.values() if not x.is_empty]

    def __str__(self) -> str:
        buf = io.StringIO()
        buf.write("\n*Directory Name Block\n")
        buf.write(f"Block number:          {self.block_number:>5}\n")
        buf.write(f"First scratch block:   {self.first_scratch_block_number:>5}\n")
        buf.write(f"Version number:        {self.version_number:>5}\n")
        buf.write(f"First SAM block:       {self.first_sam_block_number:>5}\n")
        buf.write(f"Next dir name:         {self.next_directory_name:>5}\n")
        if self.entries_list:
            buf.write("\nFilename       Num  Low   Entry Core  Blocks")
            buf.write("\n                    Core  Point Bank")
            buf.write("\n--------       ---  ----  ----- ----  -----\n")
            for i, x in enumerate(self.entries.values()):
                buf.write(f"{x}     {x.get_blocks()}\n")
        return buf.getvalue()


class DMSFilesystem(AbstractFilesystem, BlockDevice12Bit):
    """
    PDP-8 4k Disk Monitor System Filesystem

    https://svn.so-much-stuff.com/svn/trunk/pdp8/src/dec/dec-08-odsma/dec-08-odsma-a-d.pdf Pag 97

    - Blocks are 129 12-bit words (258 bytes)
    - Skip the first word of disk

    Directory Name (DN)
    Storage Allocation Map (SAM)

    Disk (pag 98)
    ====

    Block
           +---------------+
    0o177  |   DN1 (USER)  |
           +---------------+
    0o200  |   SAM1 (USER) |
           +---------------+
    0o201  |   DN2 (USER)  |
           +---------------+
    0o202  |   DN3 (USER)  |
           +---------------+
           |               |
    data   /               /
           |               |
           +---------------+
    0o373  |   Scratch     |
    0o374  |   Scratch     |
    0o375  |   Scratch     |
           +---------------+

    Dectape (Pag 100)
    =======

    Block
           +---------------+
    0o177  |   DN1 (USER)  |
           +---------------+
    0o200  |   SAM1 (USER) |
           +---------------+
    0o201  |   DN2 (USER)  |
           +---------------+
    0o202  |   SAM2 (USER) |
           +---------------+
    0o203  |   SAM3 (USER) |
           +---------------+
    0o204  |   SAM4 (USER) |
           +---------------+
    0o205  |   SAM5 (USER) |
           +---------------+
    0o206  |   SAM6 (USER) |
           +---------------+
    0o207  |   DN3 (USER)  |
           +---------------+

    """

    fs_name = "dms"
    fs_description = "PDP-8 4k Disk Monitor System"

    version_string: str  # Version
    first_scratch_block_number: int  # First scratch block number
    first_sam_block_number: int  # First SAM block number

    def __init__(self, file: "AbstractFile"):
        super().__init__(file)
        self.is_rx_12bit = False
        self.is_rx = False

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        # Read the first Directory Name block
        dn = DirectorNameBlock.read(self, DN_START, 0)
        self.first_scratch_block_number = dn.first_scratch_block_number
        self.first_sam_block_number = dn.first_sam_block_number
        self.version_string = sixbit_word12_to_asc(dn.version_number)
        if strict:
            sam = StorageAllocationMap.read(self)
            reserved_blocks = sam.files_blocks.get(RESERVED_FILE_NUMBER, [])
            if not reserved_blocks or not self.first_scratch_block_number in reserved_blocks:
                raise OSError(errno.EIO, os.strerror(errno.EIO))
        return self

    def read_12bit_words_block(self, block_number: int) -> t.List[int]:
        """
        Read a block as 129 12bit words

        - Blocks are 129 12-bit words (258 bytes)
        - Skip the first word of disk
        """
        position = block_number * BLOCK_SIZE_WORD * BYTES_PER_WORD + BYTES_PER_WORD
        self.f.seek(position)
        data = self.f.read(BLOCK_SIZE_WORD * BYTES_PER_WORD)
        return [x & 0o7777 for x in struct.unpack(f"<{BLOCK_SIZE_WORD}H", data)]

    def write_12bit_words_block(self, block_number: int, words: t.List[int]) -> None:
        """
        Write a block as 129 12bit words

        - Blocks are 129 12-bit words (258 bytes)
        - Skip the first word of disk
        """
        assert len(words) == BLOCK_SIZE_WORD
        data = struct.pack(f"<{BLOCK_SIZE_WORD}H", *words)
        position = block_number * BLOCK_SIZE_WORD * BYTES_PER_WORD + BYTES_PER_WORD
        self.f.seek(position)
        self.f.write(data)

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
        sam: t.Optional["StorageAllocationMap"] = None,
    ) -> t.Iterator["DMSDirectoryEntry"]:
        dms_filename = DMSFilename(pattern, wildcard=wildcard) if pattern else None
        for dn in self.read_directory_name_blocks(sam):
            for entry in dn.entries_list:
                if dms_filename is None or dms_filename.match(entry):
                    yield entry

    @property
    def entries_list(self) -> t.Iterator["DMSDirectoryEntry"]:
        for dn in self.read_directory_name_blocks():
            for entry in dn.entries_list:
                yield entry

    def get_file_entry(self, fullname: str) -> DMSDirectoryEntry:
        """
        Get the directory entry for a file
        """
        dms_filename = DMSFilename(fullname)
        for dn in self.read_directory_name_blocks():
            for entry in dn.entries_list:
                if (
                    entry.filename.strip() == dms_filename.filename.strip()
                    and entry.program_type == dms_filename.program_type
                ):
                    return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def read_bytes(self, fullname: str, file_mode: t.Optional[str] = None) -> bytes:
        """Get the content of a file"""
        if file_mode is None:
            dms_filename = DMSFilename(fullname)
            if dms_filename.program_type == FILE_TYPE_ASCII:
                file_mode = ASCII
            else:
                file_mode = IMAGE
        # Always read the file as IMAGE
        f = self.open_file(fullname, IMAGE)
        try:
            data = f.read_block(0, READ_FILE_FULL)
            if file_mode == ASCII:
                # Convert IMAGE => words => ASCII
                words = from_bytes_to_12bit_words(data, file_mode=IMAGE)
                return from_12bit_words_to_bytes(words, file_mode=ASCII)
            else:
                return data
        finally:
            f.close()

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
        dms_filename = DMSFilename(fullname)
        if file_mode is None:
            if dms_filename.program_type == FILE_TYPE_ASCII:
                file_mode = ASCII
            else:
                file_mode = IMAGE
        if file_mode == ASCII:
            # Append FF (form feed) if not present
            if not content.endswith(b'\x0c\x0c'):
                content = content + b'\x0c\x0c'
            # Convert ASCII => words
            words = from_bytes_to_12bit_words(content, file_mode=ASCII)
        else:
            # Convert IMAGE => words
            words = from_bytes_to_12bit_words(content, file_mode=IMAGE)
        # Allocate space
        number_of_blocks = int(math.ceil(len(words) * 1.0 / DATA_BLOCK_SIZE_WORD))
        entry = self.create_file(fullname, number_of_blocks, creation_date, file_type)
        # Write blocks
        if entry is not None:
            blocks = entry.get_blocks()
            for i, block in enumerate(blocks):
                block_words = words[i * DATA_BLOCK_SIZE_WORD : (i + 1) * DATA_BLOCK_SIZE_WORD]
                block_words.extend([0] * (BLOCK_SIZE_WORD - len(block_words)))
                # The last word of the block is the link to the next block
                block_words[-1] = blocks[i + 1] if i + 1 < len(blocks) else 0
                entry.dn.fs.write_12bit_words_block(block, block_words)

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,
    ) -> t.Optional[DMSDirectoryEntry]:
        """
        Create a new file with a given length in number of blocks
        """
        # Delete the file if it already exists
        dms_filename = DMSFilename(fullname)
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass
        # Allocate space
        sam = StorageAllocationMap.read(self)
        file_number = sam.allocate_space(fullname, number_of_blocks)
        # Create entry
        entry = None
        for dn in self.read_directory_name_blocks():
            if dn.first_file_number <= file_number <= dn.last_file_number:
                entry = DMSDirectoryEntry(dn, sam)
                entry.filename = dms_filename.filename
                entry.file_number = file_number
                entry.program_type = dms_filename.program_type
                entry.system_program = dms_filename.system_program
                entry.high_core_addr = (dms_filename.core_addr >> 12) & 0o7
                entry.low_core_addr = dms_filename.core_addr & 0o7777
                entry.entry_point = dms_filename.entry_point
                dn.entries[file_number] = entry
                dn.write()
                break
        if entry is not None:
            sam.write()
        return entry

    def isdir(self, fullname: str) -> bool:
        return False

    def chdir(self, fullname: str) -> bool:
        return False

    def read_directory_name_blocks(
        self, sam: t.Optional["StorageAllocationMap"] = None
    ) -> t.Iterator["DirectorNameBlock"]:
        """
        Read the Directory Name Blocks
        """
        if sam is None:
            sam = StorageAllocationMap.read(self)
        next_block_number = DN_START
        block_seq_nr = 0
        while next_block_number != 0:
            dn = DirectorNameBlock.read(self, next_block_number, block_seq_nr, sam)
            next_block_number = dn.next_directory_name
            block_seq_nr += 1
            yield dn

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        return StorageAllocationMap.read(self).free()

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        sam = StorageAllocationMap.read(self)
        if not options.get("brief"):
            # Number of free blocks
            sys.stdout.write(f"\nFB={sam.free():>04o}\n")
            sys.stdout.write("\n")
            sys.stdout.write("NAME  TYPE    BLK\n")
            sys.stdout.write("\n")
            sys.stdout.write(f"{self.version_string}\n")
        for x in self.filter_entries_list(pattern, include_all=True, sam=sam):
            if x.file_number == RESERVED_FILE_NUMBER and not options.get("full"):
                continue
            if options.get("brief"):
                # Lists only file name and extension
                sys.stdout.write(f"{x.filename:<4}.{x.extension}\n")
            else:
                # Filename, extension, core bank (for SYS/USER files) and length in blocks (in octal)
                if x.program_type == FILE_TYPE_SYS_USER:
                    fullname = f"{x.filename:<4}.{x.extension:<4}({x.high_core_addr:>o})"
                else:
                    fullname = f"{x.filename:<4}.{x.extension:<7}"
                sys.stdout.write(f"{fullname} {x.get_length():>04o}\n")
        sys.stdout.write("\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if arg:
            sam = StorageAllocationMap.read(self)
            sys.stdout.write("Filename       Num  Low   Entry Core\n")
            sys.stdout.write("                    Core  Point Bank\n")
            sys.stdout.write("--------       ---  ----  ----- ----\n")
            for entry in self.filter_entries_list(arg, include_all=True, sam=sam):
                sys.stdout.write(f"{entry}\n")
        else:
            for dn in self.read_directory_name_blocks():
                sys.stdout.write(f"{dn}\n")

    def dump(self, fullname: t.Optional[str], start: t.Optional[int] = None, end: t.Optional[int] = None) -> None:
        """Dump the content of a file or a range of blocks"""
        if fullname:
            entry = self.get_file_entry(fullname)
            if start is None:
                start = 0
            if end is None:
                end = entry.get_length() - 1
            blocks = entry.get_blocks()
            for block_number in range(start, end + 1):
                words = self.read_12bit_words_block(blocks[block_number])
                print(f"\nBLOCK NUMBER   {block_number:08}")
                oct_dump(words)
        else:
            if start is None:
                start = 0
                if end is None:  # full disk
                    end = self.get_size() // BLOCK_SIZE_WORD // BYTES_PER_WORD - 1
            elif end is None:  # one single block
                end = start
            for block_number in range(start, end + 1):
                words = self.read_12bit_words_block(block_number)
                print(f"\nBLOCK NUMBER   {block_number:08}")
                oct_dump(words)

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        """
        Create an empty PDP-8 4k Disk Monitor filesystem
        """
        version_string = "AF"
        scratch_blocks = [251, 252, 253, 254, 255]
        dn_blocks = [DN_START, DN_START + 2, DN_START + 3]
        sam_blocks = [DN_START + 1]
        # Initialize this instance
        self.first_scratch_block_number = scratch_blocks[0]
        self.first_sam_block_number = sam_blocks[0]
        self.version_string = version_string
        # Create SAM
        for i, block_number in enumerate(dn_blocks):
            sam_block = StorageAllocationMapBlock.read(self, block_number, i)
            if i < len(sam_blocks) - 1:
                sam_block.next_sam_block_number = sam_blocks[i + 1]
        # Allocate blocks
        sam = StorageAllocationMap.read(self)
        for block_number in scratch_blocks + dn_blocks + sam_blocks:
            sam.set_block(block_number, RESERVED_FILE_NUMBER)
        sam.write()
        # Create DNs
        for i, block_number in enumerate(dn_blocks):
            dn = DirectorNameBlock.read(self, block_number, i)
            if i == 0:
                dn.first_scratch_block_number = scratch_blocks[0]
                dn.first_sam_block_number = sam_blocks[0]
                dn.version_number = asc_to_sixbit_word12(version_string)
                entry = DMSDirectoryEntry(dn, sam)
                entry.filename = MONITOR_FILENAME
                entry.file_number = RESERVED_FILE_NUMBER
                entry.program_type = FILE_TYPE_SYS_USER
                entry.system_program = True
                entry.high_core_addr = 0
                entry.low_core_addr = 0o7000
                entry.entry_point = 0o7000
                dn.entries[entry.file_number] = entry
            if i < len(dn_blocks) - 1:
                dn.next_directory_name = dn_blocks[i + 1]
            dn.write()
        sam.write()

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def close(self) -> None:
        self.f.close()

    def get_pwd(self) -> str:
        return ""

    def get_types(self) -> t.List[str]:
        """
        Get the list of the supported file types
        """
        return [
            EXT_SYS,
            EXT_USER,
            EXT_ASCII,
            EXT_BINARY,
            EXT_FTC_BIN,
        ]

    def __str__(self) -> str:
        return str(self.f)
