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
from enum import Enum

from ..abstract import XFERXPath, AbstractFile, AbstractFilesystem
from ..commons import ASCII, IMAGE, READ_FILE_FULL, filename_match
from ..device.abstract import AbstractDevice
from ..device.block_18bit import (
    BYTES_PER_WORD_18BIT,
    BlockDevice18Bit,
    from_18bit_words_to_bytes,
    from_bytes_to_18bit_words,
)
from .codes import (
    BAUDOT_TO_ASCII,
    LABEL_END_WORD,
    fiodec_to_str,
    read_baudot_string,
    str_to_baudot,
    str_to_fiodec,
)
from .opcodes import disassemble_pdp7

__all__ = [
    "DECSysFile",
    "DECSysPath",
    "DECSysFilesystem",
]


# DECsys-7 Operating Manual
# https://bitsavers.org/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf

# Technical Notes on DECsys
# https://simh.trailing-edge.com/docs/decsys.pdf

DECTAPE_BLOCKS = 384  # Number of blocks on a DECtape
WORDS_PER_BLOCK = 256  # Number of words per block
LINKED_FILE_WORDS_PER_BLOCK = 254  # Number of words per block in a linked file

TAPE_LABEL_BLOCK = 1  # Tape label block number
PROGRAM_DIRECTORY_BLOCK = 2  # File directory block number
LIBRARY_DIRECTORY_BLOCK = 3  # Library directory block number
KEYBOARD_MONITOR_BLOCK = 4  # Keyboard monitor block number
KEYBOARD_MONITOR_SIZE = 3  # Keyboard monitor size in blocks
FIRST_FILE_BLOCK = KEYBOARD_MONITOR_BLOCK + KEYBOARD_MONITOR_SIZE  # First block usable for files
LAST_FILE_BLOCK = DECTAPE_BLOCKS - 2  # Last block usable for files

SYSTEM_ENTRY_SIZE = 5  # System file entry size, in words
WORKING_ENTRY_SIZE = 6  # User file entry size, in words

SYSTEM_HEADER_SIZE = 2  # System file header size, in words


# File types
class FileType(Enum):
    SYSTEM = 1  # System file
    WORKING = 2  # User file
    LIBRARY = 3  # Library file
    # The following ane not real file types, but are used to identify the forks and keyword monitor
    FORTRAN = 20  # User file - Fortran source code
    ASSEMBLER = 21  # User file - Assembler source code
    BINARY = 22  # User file - Binary code
    KMON = 99  # Keyboard monitor

    def __str__(self) -> str:
        return self.name

    @property
    def short(self) -> str:
        return self.name[0]

    @property
    def file_mode(self) -> str:
        if self in [FileType.FORTRAN, FileType.ASSEMBLER]:
            return ASCII
        else:
            return IMAGE

    @classmethod
    def from_str(cls, value: str) -> "FileType":
        value = value.upper()
        for x in cls:
            if x.name.startswith(value):
                return x
        raise ValueError(f"Invalid file type: {value}")


def decsys_canonical_filename(fullname: str, wildcard: bool = False) -> str:
    """
    Generate the canonical DECsys filename
    """
    chars = set(BAUDOT_TO_ASCII.values())
    if wildcard:
        chars.add("*")
    return "".join([x for x in fullname.upper().strip() if x in chars])


def decsys_split_fullname(
    fullname: str,
    wildcard: bool = True,
    metadata: t.Optional[t.Dict[str, t.Any]] = None,
) -> t.Tuple[t.Optional[FileType], str]:
    """
    Split file type and filename
    (e.g. "F,HELLO" -> FileType.FORTRAN, "HELLO")
    If the file type is not specified, try to get it from the metadata dictionary
    If the file type is specified both in the fullname and in the metadata, check that they match
    """
    metadata = metadata or {}
    mft = metadata.get("file_type")
    metadata_file_type = FileType.from_str(mft) if mft else None  # type: ignore
    if "," in fullname:
        file_type, fullname = fullname.split(",", 1)
        raw_file_type: t.Optional[FileType] = FileType.from_str(file_type)  # type: ignore
        if metadata_file_type and raw_file_type != metadata_file_type:
            raise ValueError(f"File type mismatch: {raw_file_type} != {metadata_file_type}")
    else:
        raw_file_type = metadata_file_type
    return raw_file_type, decsys_canonical_filename(fullname, wildcard=wildcard)


def oct_dump(words: t.List[int], words_per_line: int = 8) -> None:
    """
    Display contents in octal, fiodec and baudot
    """
    for i in range(0, len(words), words_per_line):
        line = words[i : i + words_per_line]
        fiodec = "".join([x if 32 <= ord(x) <= 126 else "." for x in fiodec_to_str(line)])
        baudot = "".join([x if 32 <= ord(x) <= 126 else "." for x in read_baudot_string(line)[0]])
        oct_str = " ".join([f"{x:06o}" for x in line])
        sys.stdout.write(f"{i:08o}   {oct_str.ljust(5 * words_per_line)}  {fiodec:24}  {baudot}\n")


class DECSysFile(AbstractFile):
    entry: "DECSysPath"
    file_mode: str  # ASCII of IMAGE

    def __init__(self, entry: "DECSysPath", file_mode: t.Optional[str] = None):
        self.entry = entry
        if file_mode is not None:
            self.file_mode = file_mode
        else:
            self.file_mode = entry.raw_file_type.file_mode

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
        i_blocks = list(enumerate(self.entry.get_blocks()))[block_number : block_number + number_of_blocks]
        if self.entry.is_contiguous():
            # System files (contiguous) - the first block starts with 2 words header:
            # word 0 - two's complement of number of words
            # word 1 - initial load address – 1
            num_words = 0
            for i, block_number in i_blocks:
                words = self.entry.fs.read_words_block(block_number)
                if i == 0:  # first block - read the header
                    num_words = 0x40000 - words[0] + SYSTEM_HEADER_SIZE
                words = words[:num_words]
                num_words -= len(words)
                t = from_18bit_words_to_bytes(words, self.file_mode)
                data.extend(t)
        else:
            # Linked files - each block starts with:
            # word 0 - block number of next block in the file (0 if last)
            # word 1 - two's complement of number of words used in this block
            for _, block_number in i_blocks:
                words = self.entry.fs.read_words_block(block_number)
                num_words_comp = words[1]
                num_words = 0x40000 - num_words_comp
                words = words[2 : 2 + num_words]  # Skip the first 2 words
                t = from_18bit_words_to_bytes(words, self.file_mode)
                data.extend(t)
        return bytes(data)

    def write_block(
        self,
        buffer: t.Union[bytes, bytearray],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        """
        if self.closed or block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        words = from_bytes_to_18bit_words(buffer, self.file_mode)
        self.write_words_block(words, block_number, number_of_blocks)

    def write_words_block(
        self,
        words: t.List[int],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        """
        blocks = list(self.entry.get_blocks())
        i_blocks = list(enumerate(blocks))[block_number : block_number + number_of_blocks]
        if self.entry.is_contiguous():
            # System files (contiguous)
            # word 0 - two's complement of number of words
            # word 1 - initial load address – 1
            if block_number == 0:
                # Update the header
                words[0] = 0x40000 - len(words) + 2
            for i, block_number in i_blocks:
                block_words = words[i * WORDS_PER_BLOCK : (i + 1) * WORDS_PER_BLOCK]
                block_words = block_words + ([0o777777] * (WORDS_PER_BLOCK - len(block_words)))  # Pad with 0o777777
                self.entry.fs.write_words_block(block_number, block_words)
        else:
            # Linked files - each block starts with:
            # word 0 - block number of next block in the file (0 if last)
            # word 1 - two's complement of number of words used in this block
            for i, block_number in i_blocks:
                next_block_number = blocks[i + 1] if i < len(blocks) - 1 else 0
                block_words = words[i * LINKED_FILE_WORDS_PER_BLOCK : (i + 1) * LINKED_FILE_WORDS_PER_BLOCK]
                num_words = len(block_words)
                num_words_comp = 0x40000 - num_words
                block_words = (
                    [next_block_number, num_words_comp]
                    + block_words
                    + ([0] * (LINKED_FILE_WORDS_PER_BLOCK - len(block_words)))
                )
                self.entry.fs.write_words_block(block_number, block_words)

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return self.entry.get_length()

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.get_length() * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        if self.entry.is_contiguous():
            return WORDS_PER_BLOCK * 3
        else:
            return LINKED_FILE_WORDS_PER_BLOCK * 3

    def __str__(self) -> str:
        return self.entry.fullname


class DECSysPath(XFERXPath):
    """
    System file Directory Entry
    ---------------------------

    System files are stored as contiguous files.

    Word

        +-----------------------------------+
      0 | File type (1 = SYSTEM)            |
        +-----------------------------------+
      1 | Filename (BAUDOT)                 |
      2 |                                   |
        +-----------------------------------+
      3 | Block number                      |
        +-----------------------------------+
      4 | Starting address                  |
        +-----------------------------------+

    User file Directory Entry
    -------------------------

    User files are stored as linked files.

    Word

        +-----------------------------------+
      0 | File type (2 = WORKING)           |
        +-----------------------------------+
      1 | Filename (BAUDOT)                 |
      2 |                                   |
        +-----------------------------------+
      3 | FORTRAN Block number              |
        +-----------------------------------+
      4 | Assembler Block number            |
        +-----------------------------------+
      5 | Relocatable binary Block number   |
        +-----------------------------------+

    Library file Directory Entry
    ----------------------------

    Library files are stored as linked files.

        +-----------------------------------+
      1 | Filename (BAUDOT)                 |
        /                                   /
      n |                                   |
        +-----------------------------------+
    n+1 | 777777                            |
        +-----------------------------------+
    n+2 | Block number                      |
        +-----------------------------------+
    n+3 | 777777                            |
        +-----------------------------------+

    """

    fs: "DECSysFilesystem"
    directory: "DECSysAbstractDirectory"
    filename: str  # Filename
    raw_file_type: FileType  # Raw file type
    block_number: int = 0  # Program / relocable binary / library block number
    fortran_block_number: int = 0  # Fortran block number
    assembler_block_number: int = 0  # Assembler block number
    decsys_starting_address: int = 0  # Starting address (system files)
    entry_length: int  # Directory entry length, in words (5 for system, 6 for user files)

    def __init__(self, directory: "DECSysAbstractDirectory"):
        self.directory = directory
        self.fs = directory.fs

    @classmethod
    def read(cls, directory: "DECSysAbstractDirectory", words: t.List[int], position: int) -> "DECSysPath":
        if words[position] == FileType.SYSTEM.value:
            # System file
            return SystemDirectoryEntry.read(directory, words, position)
        else:
            # User file
            return WorkingDirectoryEntry.read(directory, words, position)

    @abstractmethod
    def to_words(self) -> t.List[int]:
        """
        Dump the directory entry to words
        """
        pass

    @property
    def fullname(self) -> str:
        """Type,Filename"""
        return f"{self.raw_file_type.short},{self.filename}"

    @property
    def name(self) -> str:
        """Filename"""
        return self.filename

    @property
    def parent(self) -> "DECSysRootDirectory":
        """Parent directory"""
        return self.fs.get_root()

    def is_contiguous(self) -> bool:
        """
        Return True if the file is contiguous
        """
        return self.raw_file_type in (FileType.SYSTEM, FileType.KMON)

    @abstractmethod
    def get_blocks(self, file_type: t.Optional[FileType] = None) -> t.List[int]:
        """
        Get the blocks used by the file
        """
        pass

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return len(self.get_blocks())

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get file size in bytes
        """
        return self.get_length() * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return WORDS_PER_BLOCK * 3

    def delete(self) -> bool:
        """
        Delete the directory entry
        """
        if self.raw_file_type == FileType.KMON:
            return False
        l = len(self.directory.entries)
        self.directory.entries = [x for x in self.directory.entries if x.filename != self.filename]
        if len(self.directory.entries) < l:
            self.directory.write()
            return True
        else:
            return False

    def write(self) -> bool:
        """
        Write the directory entry
        """
        self.directory.write()
        return True

    def deallocate(self) -> None:
        """
        Deallocate the blocks used by the file
        """
        if self.raw_file_type == FileType.KMON:
            return  # The keyboard monitor is always present in blocks 4-6
        elif self.raw_file_type == FileType.FORTRAN:
            self.fortran_block_number = 0
        elif self.raw_file_type == FileType.ASSEMBLER:
            self.assembler_block_number = 0
        else:  # FileType.BINARY, FileType.LIBRARY
            self.block_number = 0
        # Write the directory entry
        self.directory.write()

    def allocate(self, number_of_blocks: int) -> None:
        """
        Allocate blocks for the file
        """
        # Allocate space
        if self.raw_file_type == FileType.KMON:
            return  # The keyboard monitor is always present in blocks 4-6
        elif self.raw_file_type == FileType.SYSTEM:
            blocks, first_free_block = self.fs.allocate_contiguous_space(number_of_blocks)
        else:
            blocks, first_free_block = self.fs.allocate_space(number_of_blocks)
        # Update the file entry
        if self.raw_file_type == FileType.FORTRAN:
            self.fortran_block_number = blocks[0] if blocks else 0
        elif self.raw_file_type == FileType.ASSEMBLER:
            self.assembler_block_number = blocks[0] if blocks else 0
        else:  # FileType.BINARY, FileType.LIBRARY, FileType.SYSTEM
            self.block_number = blocks[0] if blocks else 0
        # Write the directory and update the first free block
        if isinstance(self.directory, ProgramDirectory):
            self.directory.first_free_block = first_free_block
            self.directory.write()
        else:
            self.directory.write()
            directory = ProgramDirectory(self.fs)
            directory.first_free_block = first_free_block
            directory.write()

    @property
    def file_type(self) -> t.Optional[str]:
        """File type"""
        return str(self.raw_file_type)

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> DECSysFile:
        """
        Open a file
        """
        return DECSysFile(self, file_mode)

    def read_bytes(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> bytes:
        """Get the content of the file"""
        if file_mode is None:
            if self.raw_file_type in [FileType.FORTRAN, FileType.ASSEMBLER]:
                file_mode = ASCII
            else:
                file_mode = IMAGE
        # Always read the file as IMAGE
        with self.open(IMAGE) as f:
            data = f.read_block(0, READ_FILE_FULL)
            if file_mode == IMAGE:
                return data
            words = from_bytes_to_18bit_words(data, file_type=IMAGE)
            if self.raw_file_type in [FileType.FORTRAN, FileType.ASSEMBLER]:
                # Convert words to ASCII
                return fiodec_to_str(words).encode('ascii')
            # Disassemble system files
            num_of_words = 0x40000 - words[0] + SYSTEM_HEADER_SIZE
            addr = words[1]
            header = f"/ load: {addr:06o} start: {self.decsys_starting_address:06o} words: {num_of_words}\n"
            return header.encode('ascii') + disassemble_pdp7(
                words[2 : num_of_words + 2], addr + 1, self.decsys_starting_address
            )


class KeyboardMonitorEntry(DECSysPath):
    """
    Keyboard Monitor Directory Entry

    This is not a real directory entry, but represent the keyboard monitor
    that is always present on the disk in blocks 4 - 6.
    """

    def __init__(self, directory: "DECSysAbstractDirectory"):
        super().__init__(directory)
        self.filename = "KMON"
        self.block_number = KEYBOARD_MONITOR_BLOCK
        self.entry_length = 0
        self.raw_file_type = FileType.KMON
        self.decsys_starting_address = 0o16125

    def to_words(self) -> t.List[int]:
        return []

    def get_blocks(self, file_type: t.Optional[FileType] = None) -> t.List[int]:
        return list(range(self.block_number, self.block_number + KEYBOARD_MONITOR_SIZE))

    def __str__(self) -> str:
        return f"{self.filename:<15} Type: {self.file_type}  Block: {self.block_number:04}  Addr: {self.decsys_starting_address:>06}"


class SystemDirectoryEntry(DECSysPath):
    """
    System file Directory Entry
    ----------------------------

    System files are stored as contiguous files.

    Word

        +-----------------------------------+
      0 | File type ( 1 = SYSTEM)           |
        +-----------------------------------+
      1 | Filename (BAUDOT)                 |
      2 |                                   |
        +-----------------------------------+
      3 | Block number                      |
        +-----------------------------------+
      4 | Starting address                  |
        +-----------------------------------+
    """

    @classmethod
    def read(cls, directory: "DECSysAbstractDirectory", words: t.List[int], position: int) -> "SystemDirectoryEntry":
        self = cls(directory)
        raw_filename = words[position + 1 : position + 3]
        self.filename, _ = read_baudot_string(raw_filename, 0)
        self.block_number = words[position + 3]
        self.decsys_starting_address = words[position + 4] - 1
        self.entry_length = SYSTEM_ENTRY_SIZE
        self.raw_file_type = FileType.SYSTEM
        return self

    def to_words(self) -> t.List[int]:
        """
        Dump the directory entry to words
        """
        return (
            [self.raw_file_type.value]
            + str_to_baudot(self.filename, length=2)
            + [self.block_number, self.decsys_starting_address + 1]
        )

    def get_blocks(self, file_type: t.Optional[FileType] = None) -> t.List[int]:
        """
        System files are stored as contiguous files
        word 0 - two's complement of number of words
        word 1 - initial load address – 1
        """
        block_number = self.block_number
        if block_number == 0:
            return []
        # # Read the header from the first block
        # words = self.fs.read_words_block(block_number)
        # num_words = 0x40000 - words[0]
        # num_blocks = (num_words + 2 + WORDS_PER_BLOCK - 1) // WORDS_PER_BLOCK
        # return list(range(block_number, block_number + num_blocks))
        # If the last word of the file is != 0o777777, the file continues in the next block
        num_blocks = 0
        last_word = 0
        while last_word != 0o777777:
            buffer = self.fs.read_words_block(block_number + num_blocks)
            num_words = 0x40000 - buffer[0]
            t_num_blocks = (num_words + 2 + 256 - 1) // 256
            if t_num_blocks > 1:
                # Read the last block to check the last word
                buffer = self.fs.read_words_block(block_number + num_blocks + t_num_blocks - 1)
            last_word = buffer[(num_words + 2) % 256]
            num_blocks += t_num_blocks
        return list(range(block_number, block_number + num_blocks))

    def __str__(self) -> str:
        return f"{self.filename:<15} Type: {self.file_type}  Block: {self.block_number:04}  Addr: {self.decsys_starting_address:>06}"


class WorkingDirectoryEntry(DECSysPath):
    """
    User file Directory Entry
    -------------------------

    User files are stored as linked files.

    Word

        +-----------------------------------+
      0 | File type ( 2 = WORKING)          |
        +-----------------------------------+
      1 | Filename (BAUDOT)                 |
      2 |                                   |
        +-----------------------------------+
      3 | FORTRAN Block number              |
        +-----------------------------------+
      4 | Assembler Block number            |
        +-----------------------------------+
      5 | Relocatable binary Block number   |
        +-----------------------------------+
    """

    @classmethod
    def read(cls, directory: "DECSysAbstractDirectory", words: t.List[int], position: int) -> "WorkingDirectoryEntry":
        self = cls(directory)
        raw_filename = words[position + 1 : position + 3]
        self.filename, _ = read_baudot_string(raw_filename, 0)
        self.fortran_block_number = words[position + 3]
        self.assembler_block_number = words[position + 4]
        self.block_number = words[position + 5]
        self.entry_length = WORKING_ENTRY_SIZE
        self.raw_file_type = FileType.WORKING
        return self

    def to_words(self) -> t.List[int]:
        """
        Dump the directory entry to words
        """
        return (
            [FileType.WORKING.value]
            + str_to_baudot(self.filename, length=2)
            + [self.fortran_block_number, self.assembler_block_number, self.block_number]
        )

    def get_blocks(self, file_type: t.Optional[FileType] = None) -> t.List[int]:
        """
        User and library files are stored as linked files
        word 0 - block number of next block in the file (0 if last)
        word 1 - two's complement of number of words used in this block
        """
        # For user files, get the FORTRAN, Assembler of Binary fork
        file_type = file_type or self.raw_file_type
        if file_type == FileType.FORTRAN:
            block_number = self.fortran_block_number
        elif file_type == FileType.ASSEMBLER:
            block_number = self.assembler_block_number
        else:
            block_number = self.block_number
        next_block_number = block_number
        blocks = []
        while next_block_number:
            blocks.append(next_block_number)
            next_block_number = self.fs.read_words_block(next_block_number)[0]
        return blocks

    def __str__(self) -> str:
        if self.raw_file_type == FileType.FORTRAN:  # Fortran source code
            addr = f"Block: {self.fortran_block_number:04}"
        elif self.raw_file_type == FileType.ASSEMBLER:  # Assembler source code
            addr = f"Block: {self.assembler_block_number:04}"
        elif self.raw_file_type == FileType.BINARY:  # Binary code
            addr = f"Block: {self.block_number:04}"
        elif self.raw_file_type == FileType.WORKING:  # User file
            addr = f"Fortran: {self.fortran_block_number:04}  Asm: {self.assembler_block_number:04}  Binary: {self.block_number:04}"
        else:
            addr = ""
        return f"{self.filename:<15} Type: {self.file_type}  {addr}"


class LibraryDirectoryEntry(DECSysPath):
    """
    Library Directory Entry
    -----------------------

    A library directory entry represents a library subroutine.
    Library files are stored as linked files.

        +-----------------------------------+
      1 | Filename (BAUDOT)                 |
        /                                   /
      n |                                   |
        +-----------------------------------+
    n+1 | 777777                            |
        +-----------------------------------+
    n+2 | Block number                      |
        +-----------------------------------+
    n+3 | 777777                            |
        +-----------------------------------+
    """

    @classmethod
    def read(cls, directory: "DECSysAbstractDirectory", words: t.List[int], position: int) -> "LibraryDirectoryEntry":
        assert isinstance(directory, LibraryDirectory)
        # Directory entries names are variable length,
        # depending on the number of entry points
        self = cls(directory)
        self.filename, pp = read_baudot_string(words, position)
        self.block_number = words[pp + 1]
        self.entry_length = pp - position + 3
        self.raw_file_type = FileType.LIBRARY
        return self

    def to_words(self) -> t.List[int]:
        """
        Dump the directory entry to words
        """
        # Directory entries names are variable length,
        # depending on the number of entry points
        words = str_to_baudot(self.filename) + [
            0o777777,
            self.block_number,
            0o777777,
        ]
        self.entry_length = len(words)
        return words

    def get_blocks(self, file_type: t.Optional[FileType] = None) -> t.List[int]:
        """
        User and library files are stored as linked files
        word 0 - block number of next block in the file (0 if last)
        word 1 - two's complement of number of words used in this block
        """
        next_block_number = self.block_number
        blocks = []
        while next_block_number:
            blocks.append(next_block_number)
            next_block_number = self.fs.read_words_block(next_block_number)[0]
        return blocks

    def __str__(self) -> str:
        return f"{self.filename:<15} Type: {self.file_type}  Block: {self.block_number:04}"


class DECSysAbstractDirectory(ABC):
    """
    Abstract Directory - the two concrete directories are:
    - Program Directory -> system and user files
    - Library Directory -> library subroutine files
    """

    fs: "DECSysFilesystem"
    entries: t.List["DECSysPath"]

    def __init__(self, fs: "DECSysFilesystem"):
        self.fs = fs

    @classmethod
    @abstractmethod
    def read(cls, fs: "DECSysFilesystem") -> "DECSysAbstractDirectory":
        """
        Read the Directory from disk
        """
        pass

    @abstractmethod
    def write(self) -> None:
        """
        Write the Directory to the disk
        """
        pass


class ProgramDirectory(DECSysAbstractDirectory):
    """
    Program Directory (DECtape block 2)

    The library directory stores the directory entries for system and working files.

    Word

        +-----------------------------------+
      0 | Directory Length (words)          |
        +-----------------------------------+
      1 | Program Directory entries         |
        /                                   /
        |                                   |
        +-----------------------------------+
    255 | First free block number           |
        +-----------------------------------+
    """

    first_free_block: int  # First free block number

    @classmethod
    def read(cls, fs: "DECSysFilesystem") -> "ProgramDirectory":
        """
        Read Program Directory entries

        https://simh.trailing-edge.com/docs/decsys.pdf  Pag 2
        https://bitsavers.org/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf  Pag 12
        """
        self = ProgramDirectory(fs)
        self.entries = [KeyboardMonitorEntry(self)]  # Add the keyboard monitor entry
        words = self.fs.read_words_block(PROGRAM_DIRECTORY_BLOCK)
        dir_length = words[0]  # Directory length, in words
        self.first_free_block = words[255]  # First free block number
        position = 1
        while position < len(words) - 5 and position < dir_length:
            entry = DECSysPath.read(self, words, position)
            position += entry.entry_length
            self.entries.append(entry)
        return self

    def write(self) -> None:
        """
        Write the Program Directory to the disk
        """
        words = [0]
        for entry in self.entries:
            words += entry.to_words()
        words[0] = len(words) - 1  # Directory length, in words
        words += [0] * (255 - len(words))  # pad
        words += [self.first_free_block]  # First free block number
        self.fs.write_words_block(PROGRAM_DIRECTORY_BLOCK, words)


class LibraryDirectory(DECSysAbstractDirectory):
    """
    Library Directory (DECtape block 3)

    The library directory stores the directory entries for the library subroutines.

    Word

        +-----------------------------------+
      0 | Directory Length (words)          |
        +-----------------------------------+
      1 | Library Directory entries         |
        /                                   /
        |                                   |
        +-----------------------------------+
    """

    @classmethod
    def read(cls, fs: "DECSysFilesystem") -> "LibraryDirectory":
        """
        Read Library Directory entries

        https://simh.trailing-edge.com/docs/decsys.pdf  Pag 2
        https://bitsavers.org/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf  Pag 12
        """
        self = LibraryDirectory(fs)
        self.entries = []
        words = self.fs.read_words_block(LIBRARY_DIRECTORY_BLOCK)
        dir_length = words[0]  # Directory length, in words
        position = 1
        while position < len(words) - 5 and position < dir_length:
            entry = LibraryDirectoryEntry.read(self, words, position)
            position += entry.entry_length
            self.entries.append(entry)
        return self

    def write(self) -> None:
        """
        Write the Library Directory to the disk
        """
        words = [0]
        for entry in self.entries:
            words += entry.to_words()
        words[0] = len(words) - 1  # Directory length, in words
        words += [0] * (256 - len(words))  # pad
        self.fs.write_words_block(LIBRARY_DIRECTORY_BLOCK, words)


class DECSysRootDirectory(XFERXPath):
    fs: "DECSysFilesystem"

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
    def parent(self) -> "DECSysRootDirectory":
        """Parent directory"""
        return self

    def iterdir(self) -> t.Iterator["DECSysPath"]:
        """
        Read directory entries

        https://simh.trailing-edge.com/docs/decsys.pdf  Pag 2
        https://bitsavers.org/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf  Pag 12
        """
        program_directory = ProgramDirectory.read(self.fs)
        yield from program_directory.entries
        library_directory = LibraryDirectory.read(self.fs)
        yield from library_directory.entries


class DECSysFilesystem(AbstractFilesystem):
    """
    The DECsys filesystem uses DECtape as storage device,
    with 384 blocks of 256 18-bit words each.

    The filesystem has two distinct directories:
    - Program Directory (block 2) for system and user files
    - Library Directory (block 3) for library subroutine files

    The system files are stored as contiguous files,
    while user and library files are stored as linked files.

    The users files can have three different "forks":
    - FORTRAN source code (FIODEC text)
    - Assembler source code (FIODEC text)
    - Relocatable binary code (18-bit words binary)
    The users files are stored as linked files.

    The first free block is stored in the Program Directory.
    The filesystem does not have a free space map.

    The DECtape layout is the following:

    Block

            +-------------------+
      0     | Unused            |
            +-------------------+
      1     | Tape label        |  (tape name and date)
            +-------------------+
      2     | Program directory |  (system and user files entries)
            +-------------------+
      3     | Library directory |  (library subroutine entries)
            +-------------------+
      4     | Keyboard          |  (loaded by the bootstrap)
      6     | Monitor           |
            +-------------------+
      7     | Files             |
            /                   /
    382     |                   |
            +-------------------+
    383     | Unused            |
            +-------------------+

    https://bitsavers.org/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf  Pag 12
    https://simh.trailing-edge.com/docs/decsys.pdf Pag 1
    """

    fs_name = "decsys"
    fs_description = "PDP-7 DECsys"
    fs_platforms = ["pdp-7"]
    fs_entry_metadata = [
        "file_type",
        "decsys_starting_address",
    ]

    dev: BlockDevice18Bit

    def __init__(self, file_or_device: t.Union["AbstractFile", "AbstractDevice"]):
        if isinstance(file_or_device, AbstractFile):
            self.dev = BlockDevice18Bit(file_or_device, words_per_block=WORDS_PER_BLOCK)
        elif isinstance(file_or_device, BlockDevice18Bit):
            self.dev = file_or_device
        else:
            raise OSError(errno.EIO, f"Invalid device type for {self.fs_description} filesystem")

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        strict: t.Union[bool, str] = True,
        **kwargs: t.Union[bool, str],
    ) -> "DECSysFilesystem":
        """
        Mount the filesystem from a file or device
        """
        self = cls(file_or_dev)
        if strict:
            if self.get_size() // WORDS_PER_BLOCK // 4 != DECTAPE_BLOCKS:
                raise OSError(errno.EINVAL, "Invalid DECsys tape size")
        return self

    def read_words_block(
        self,
        block_number: int,
    ) -> t.List[int]:
        """
        Read a 256 bytes block as 18bit words
        """
        return self.dev.read_words_block(block_number)

    def write_words_block(
        self,
        block_number: int,
        words: t.List[int],
    ) -> None:
        """
        Write 256 18bit words as a block
        """
        self.dev.write_words_block(block_number, words)

    def read_tape_label(self) -> t.Tuple[str, str]:
        """
        Read tape label

        The tape label contains alphanumeric strings which identify the tape name and
        the date on which it was updated.

        The labels are of two Baudot strings, padded with 0’s to an 18b boundary and
        terminated by a word of all ones (0o777777).

        https://simh.trailing-edge.com/docs/decsys.pdf  Pag 2
        https://bitsavers.org/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf  Pag 12
        """
        words = self.read_words_block(TAPE_LABEL_BLOCK)
        tape_name, position = read_baudot_string(words, 0)
        tape_date, _ = read_baudot_string(words, position + 1)
        return tape_name, tape_date

    def write_tape_label(self, tape_name: str, tape_date: str) -> None:
        """
        Write tape label
        """
        words = str_to_baudot(tape_name) + [LABEL_END_WORD] + str_to_baudot(tape_date) + [LABEL_END_WORD]
        words += [0] * (WORDS_PER_BLOCK - len(words))
        self.write_words_block(TAPE_LABEL_BLOCK, words)

    @property
    def entries_list(self) -> t.Iterator["DECSysPath"]:
        yield from self.get_root().iterdir()

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["DECSysPath"]:
        if pattern:
            raw_file_type, pattern = decsys_split_fullname(pattern, wildcard=True)
        else:
            raw_file_type = None
        for entry in self.get_root().iterdir():
            if filename_match(entry.name, pattern, wildcard):
                # Filter by file type
                if raw_file_type is not None:
                    if raw_file_type in [FileType.ASSEMBLER, FileType.FORTRAN, FileType.BINARY]:
                        if entry.raw_file_type != FileType.WORKING:
                            continue
                    elif raw_file_type != entry.raw_file_type:
                        continue
                    # Assign the file type to the entry
                    entry.raw_file_type = raw_file_type
                yield entry

    def get_file_entry(self, fullname: str) -> DECSysPath:
        """
        Get the directory entry for a file
        """
        # Split file type and filename (e.g. "F,HELLO" -> FileType.FORTRAN, "HELLO")
        raw_file_type, filename = decsys_split_fullname(fullname)
        for entry in self.get_root().iterdir():
            if entry.filename == filename:
                # Filter by file type
                if raw_file_type is not None:
                    if raw_file_type in [FileType.ASSEMBLER, FileType.FORTRAN, FileType.BINARY]:
                        if entry.raw_file_type != FileType.WORKING:
                            continue
                    elif raw_file_type != entry.raw_file_type:
                        continue
                    # Assign the file type to the entry
                    entry.raw_file_type = raw_file_type
                return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def get_root(self) -> DECSysRootDirectory:
        """
        Get the root directory
        """
        return DECSysRootDirectory(self)

    def get_allocated_blocks(self) -> t.List[int]:
        """
        Get the list of allocated blocks
        """
        blocks = list(range(0, FIRST_FILE_BLOCK))
        for entry in self.get_root().iterdir():
            if entry.raw_file_type == FileType.WORKING:
                for raw_file_type in [FileType.FORTRAN, FileType.ASSEMBLER, FileType.BINARY]:
                    blocks.extend(entry.get_blocks(raw_file_type))
            else:
                blocks.extend(entry.get_blocks())
        return blocks

    def allocate_space(self, number_of_blocks: int) -> t.Tuple[t.List[int], int]:
        """
        Allocate blocks for a file
        Return the list of allocated blocks and the first free block number
        """
        allocated_blocks = self.get_allocated_blocks()
        blocks = []
        for block in range(FIRST_FILE_BLOCK, LAST_FILE_BLOCK + 1):
            if block not in allocated_blocks:
                blocks.append(block)
                if len(blocks) == number_of_blocks:
                    break
        if len(blocks) < number_of_blocks:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        # Write the linked blocks
        for i in range(0, len(blocks)):
            next_block_number = blocks[i + 1] if i < len(blocks) - 1 else 0
            block_words = [0] * LINKED_FILE_WORDS_PER_BLOCK
            num_words = len(block_words)
            num_words_comp = 0x40000 - num_words
            block_words = (
                [next_block_number, num_words_comp]
                + block_words
                + ([0] * (LINKED_FILE_WORDS_PER_BLOCK - len(block_words)))
            )
            self.write_words_block(blocks[i], block_words)
        first_free_block = max(allocated_blocks + blocks) + 1
        return blocks, first_free_block

    def allocate_contiguous_space(self, number_of_blocks: int) -> t.Tuple[t.List[int], int]:
        """
        Allocate contiguous blocks for a file
        Return the list of allocated blocks and the first free block number
        """
        allocated_blocks = self.get_allocated_blocks()
        blocks = []
        for block in range(FIRST_FILE_BLOCK, LAST_FILE_BLOCK + 1 - number_of_blocks):
            if all(b not in allocated_blocks for b in range(block, block + number_of_blocks)):
                blocks = list(range(block, block + number_of_blocks))
                break
        if len(blocks) < number_of_blocks:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        # Write the words 0o777777 into the blocks
        empty_block_words = [0o777777] * WORDS_PER_BLOCK
        for block_number in blocks:
            self.write_words_block(block_number, empty_block_words)
        # Update the first free block
        first_free_block = max(allocated_blocks + blocks) + 1
        return blocks, first_free_block

    def create_file(
        self,
        fullname: str,
        size: int,  # Size in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> DECSysPath:
        """
        Create a new file with a given length in number of blocks
        The number of blocks can be specified in the metadata dictionary ("number_of_blocks" key).
        If not specified, the number of blocks is calculated from the size in bytes.
        """
        metadata = metadata or {}
        number_of_words: int = metadata.get("number_of_words")  # type: ignore
        if number_of_words is None:
            num_of_words = (size + BYTES_PER_WORD_18BIT - 1) // BYTES_PER_WORD_18BIT
        number_of_blocks: int = metadata.get("number_of_blocks")  # type: ignore
        if number_of_blocks is None:
            number_of_blocks = (num_of_words + LINKED_FILE_WORDS_PER_BLOCK - 1) // LINKED_FILE_WORDS_PER_BLOCK
        # TODO use fork?
        raw_file_type, filename = decsys_split_fullname(fullname, metadata=metadata)
        if raw_file_type is None:
            raise ValueError("Specify the file type as FILE_TYPE,FILENAME")
        # If the file already exists, deallocate the blocks
        try:
            entry: DECSysPath = self.get_file_entry(fullname)  # type: ignore
            entry.deallocate()
        except FileNotFoundError:
            # Create a new entry
            directory = LibraryDirectory(self) if raw_file_type == FileType.LIBRARY else ProgramDirectory.read(self)
            if raw_file_type == FileType.SYSTEM:
                entry = SystemDirectoryEntry(directory)
            elif raw_file_type == FileType.LIBRARY:
                entry = LibraryDirectoryEntry(directory)
            else:
                entry = WorkingDirectoryEntry(directory)
            entry.filename = filename
            entry.raw_file_type = raw_file_type
            directory.entries.append(entry)
        # Update metadata
        if entry.raw_file_type == FileType.SYSTEM:
            entry.decsys_starting_address = metadata.get("decsys_starting_address", 0)
        # Allocate space
        entry.allocate(number_of_blocks)
        # For system files, write the header in the first block
        if entry.raw_file_type == FileType.SYSTEM:
            # System files - the first block starts with two's complement of number of words
            words = [0] * WORDS_PER_BLOCK
            words[0] = 0x40000 - number_of_words
            self.write_words_block(entry.block_number, words)
        return entry

    def write_bytes(
        self,
        fullname: str,
        content: t.Union[bytes, bytearray],
        fork: t.Optional[str] = None,
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        metadata = metadata or {}
        # TODO use fork?
        raw_file_type, filename = decsys_split_fullname(fullname, metadata=metadata)
        if raw_file_type is None:
            raise ValueError("Specify the file type as FILE_TYPE,FILENAME")
        if file_mode is None:
            if raw_file_type in [FileType.FORTRAN, FileType.ASSEMBLER]:
                file_mode = ASCII
            else:
                file_mode = IMAGE
        if file_mode == ASCII:
            words = str_to_fiodec(content.decode('ascii'))
        else:
            words = from_bytes_to_18bit_words(content, file_type=IMAGE)
        metadata["number_of_words"] = len(words)
        metadata["number_of_blocks"] = (len(words) + LINKED_FILE_WORDS_PER_BLOCK + 1) // LINKED_FILE_WORDS_PER_BLOCK
        entry = self.create_file(fullname, len(content), metadata)
        with entry.open(file_mode) as f:
            f.write_words_block(words, block_number=0, number_of_blocks=metadata["number_of_blocks"])

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List directory entries
        """
        entries = self.filter_entries_list(pattern, wildcard=True)
        if not entries:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), pattern)
        # Show the tape labels
        tape_name, tape_date = self.read_tape_label()
        sys.stdout.write(f"{tape_name}  {tape_date}\n")
        for x in entries:
            if x.raw_file_type == FileType.KMON:
                pass  # Do not show the keyboard monitor entry
            elif x.raw_file_type == FileType.SYSTEM:
                sys.stdout.write(f"{x.name} S {x.block_number:04}\n")
            elif x.raw_file_type == FileType.LIBRARY:
                sys.stdout.write(f"{x.name}, L {x.block_number:04}\n")
            else:
                # For user files, starting tape-block numbers are shown for,
                # the FORTRAN source, the assembly source, the relocatable binary
                sys.stdout.write(
                    f"{x.name} W {x.fortran_block_number:04},{x.assembler_block_number:04},{x.block_number:04}\n"
                )

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if options.get("bitmap"):
            allocated_blocks = sorted(self.get_allocated_blocks())
            tmp = ", ".join([f"{x:04}" for x in allocated_blocks])
            sys.stdout.write(f"Allocated Blocks: {tmp}\n")
        elif arg:
            entries = self.filter_entries_list(arg, wildcard=True)
            for entry in entries:
                sys.stdout.write(f"Filename:                 {entry.filename}\n")
                sys.stdout.write(f"File type:                {entry.file_type}\n")
                if entry.raw_file_type in (FileType.SYSTEM, FileType.KMON):
                    sys.stdout.write(f"Blocks:                   {entry.get_blocks()}\n")
                    sys.stdout.write(f"Starting address:         {entry.decsys_starting_address:>06}\n")
                    words = self.read_words_block(entry.block_number)
                    num_words = 0x40000 - words[0]
                    sys.stdout.write(f"Length (words):           {num_words}\n")
                    initial_load_address = words[1]
                    sys.stdout.write(f"Initial load address:     {initial_load_address:>06}\n")
                elif entry.raw_file_type == FileType.LIBRARY:
                    sys.stdout.write(f"Blocks:                   {entry.get_blocks()}\n")
                elif entry.raw_file_type == FileType.KMON:
                    sys.stdout.write(f"Blocks:                   {entry.get_blocks()}\n")
                else:
                    if entry.raw_file_type in (FileType.WORKING, FileType.FORTRAN):
                        sys.stdout.write(f"FORTRAN Blocks:           {entry.get_blocks(FileType.FORTRAN)}\n")
                    if entry.raw_file_type in (FileType.WORKING, FileType.ASSEMBLER):
                        sys.stdout.write(f"Assembler Blocks:         {entry.get_blocks(FileType.ASSEMBLER)}\n")
                    if entry.raw_file_type in (FileType.WORKING, FileType.BINARY):
                        sys.stdout.write(f"Binary Blocks:            {entry.get_blocks(FileType.BINARY)}\n")
                sys.stdout.write("\n")
        else:
            tape_name, tape_date = self.read_tape_label()
            sys.stdout.write(f"Label 1:                  {tape_name}\n")
            sys.stdout.write(f"Label 2:                  {tape_date}\n")
            program_directory = ProgramDirectory.read(self)
            sys.stdout.write(f"First free block number:  {program_directory.first_free_block:04}\n\n")
            sys.stdout.write("Filename        Type     Address  Length\n")
            sys.stdout.write("--------        ----     -------  ------\n")
            for entry in self.get_root().iterdir():
                if entry.raw_file_type == FileType.WORKING:
                    for raw_file_type in [FileType.FORTRAN, FileType.ASSEMBLER, FileType.BINARY]:
                        blocks = entry.get_blocks(raw_file_type)
                        length = len(blocks)
                        file_type = str(raw_file_type)
                        block = blocks[0] if blocks else 0
                        filename = f"{file_type[0]},{entry.filename}"
                        sys.stdout.write(f"{filename:<15} {file_type:<10}  {block:04}  {length:>6}\n")
                else:
                    file_type = str(entry.raw_file_type)
                    blocks = entry.get_blocks()
                    length = len(blocks)
                    block = blocks[0] if blocks else 0
                    filename = f"{file_type[0]},{entry.filename}"
                    sys.stdout.write(f"{filename:<15} {file_type:<10}  {block:04}  {length:>6}\n")

    def dump(
        self,
        fullname: t.Optional[str],
        start: t.Optional[int] = None,
        end: t.Optional[int] = None,
        fork: t.Optional[str] = None,
    ) -> None:
        """Dump the content of a file or a range of blocks"""
        if fullname:
            entry = self.get_file_entry(fullname)
            if start is None:
                start = 0
            blocks = entry.get_blocks()
            if end is None or end > len(blocks) - 1:
                end = entry.get_length() - 1
            for block_number in range(start, end + 1):
                words = self.read_words_block(blocks[block_number])
                sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                oct_dump(words)
        else:
            if start is None:
                start = 0
                if end is None:  # full disk
                    end = (self.get_size() // WORDS_PER_BLOCK // 4) - 1
            elif end is None:  # one single block
                end = start
            for block_number in range(start, end + 1):
                words = self.read_words_block(block_number)
                sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                oct_dump(words)

    @classmethod
    def initialize(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        device_type: t.Union[bool, str] = "",
        **kwargs: t.Union[bool, str],
    ) -> "DECSysFilesystem":
        """
        Create an empty DECsys filesystem
        """
        self = cls(file_or_dev)
        if self.get_size() // WORDS_PER_BLOCK // 4 != DECTAPE_BLOCKS:
            raise OSError(errno.EINVAL, "Invalid DECsys tape size")
        # Write the label block
        tape_name: str = kwargs.get("name", "XFERX")  # type: ignore
        tape_date: str = date.today().strftime("%d %b %Y").upper()
        self.write_tape_label(tape_name, tape_date)
        # Write empty directories
        words = [0] * WORDS_PER_BLOCK
        self.write_words_block(PROGRAM_DIRECTORY_BLOCK, words)
        self.write_words_block(LIBRARY_DIRECTORY_BLOCK, words)
        return self

    def get_types(self) -> t.List[str]:
        """
        Get the list of the supported file types
        """
        return [
            IMAGE,
            ASCII,
        ]
