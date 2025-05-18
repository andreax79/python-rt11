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
import math
import os
import sys
import typing as t
from abc import ABC, abstractmethod
from datetime import date
from enum import Enum

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..commons import ASCII, IMAGE, READ_FILE_FULL, filename_match
from .block import (
    BlockDevice18Bit,
    from_18bit_words_to_bytes,
    from_bytes_to_18bit_words,
)
from .codes import (
    BAUDOT_TO_ASCII,
    fiodec_to_str,
    read_baudot_string,
    str_to_baudot,
    str_to_fiodec,
)

__all__ = [
    "DECSysFile",
    "DECSysDirectoryEntry",
    "DECSysFilesystem",
]


# DECSys-7 Operating Manual
# http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf

# Technical Notes on DECsys
# https://simh.trailing-edge.com/docs/decsys.pdf

DECTAPE_BLOCKS = 384  # Number of blocks on a DECtape
WORDS_PER_BLOCK = 256  # Number of words per block
LINKED_FILE_WORDS_PER_BLOCK = 254  # Number of words per block in a linked file

TAPE_LABEL_BLOCK = 1  # Tape label block number
PROGRAM_DIRECTORY_BLOCK = 2  # Program directory block number
LIBRARY_DIRECTORY_BLOCK = 3  # Library directory block number
KEYBOARD_MONITOR_BLOCK = 4  # Keyboard monitor block number
KEYBOARD_MONITOR_SIZE = 3  # Keyboard monitor size in blocks
FIRST_FILE_BLOCK = KEYBOARD_MONITOR_BLOCK + KEYBOARD_MONITOR_SIZE  # First block usable for files
LAST_FILE_BLOCK = DECTAPE_BLOCKS - 2  # Last block usable for files

SYSTEM_ENTRY_SIZE = 5  # System program entry size, in words
WORKING_ENTRY_SIZE = 6  # Working program entry size, in words


# File types
class FileType(Enum):
    SYSTEM = 1  # System program
    WORKING = 2  # Working program
    LIBRARY = 3  # Library program
    FORTRAN = 20  # Working program - Fortran source code
    ASSEMBLER = 21  # Working program - Assembler source code
    BINARY = 22  # Working program - Binary code

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
    Generate the canonical DECSys filename
    """
    chars = set(BAUDOT_TO_ASCII.values())
    if wildcard:
        chars.add("*")
    return "".join([x for x in fullname.upper().strip() if x in chars])


def decsys_split_fullname(fullname: str, wildcard: bool = True) -> t.Tuple[t.Optional[FileType], str]:
    """
    Split file type and filename
    (e.g. "F,HELLO" -> FileType.FORTRAN, "HELLO")
    """
    if "," in fullname:
        file_type, fullname = fullname.split(",", 1)
        raw_file_type: t.Optional[FileType] = FileType.from_str(file_type)  # type: ignore
    else:
        raw_file_type = None
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
    entry: "DECSysDirectoryEntry"
    file_mode: str  # ASCII of IMAGE
    closed: bool

    def __init__(self, entry: "DECSysDirectoryEntry", file_mode: t.Optional[str] = None):
        self.entry = entry
        if file_mode is not None:
            self.file_mode = file_mode
        else:
            self.file_mode = entry.raw_file_type.file_mode
        self.closed = False

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
        if self.entry.raw_file_type == FileType.SYSTEM:
            # Contiguous files - the first block starts with:
            # word 0 - two's complement of number of words
            # word 1 - initial load address – 1
            num_words = 0
            data = bytearray()
            for i, next_block_number in enumerate(self.entry.get_blocks()):
                if i >= block_number:
                    words = self.entry.directory.fs.read_18bit_words_block(next_block_number)
                    if i == 0:  # first block
                        num_words_comp = words[0]
                        num_words = 0x40000 - num_words_comp
                        words = words[2:]  # Skip the first 2 words
                    words = words[:num_words]
                    num_words -= len(words)
                    t = from_18bit_words_to_bytes(words, self.file_mode)
                    data.extend(t)
                    number_of_blocks -= 1
                    if number_of_blocks == 0 or num_words <= 0:
                        break
        else:
            # Linked files - each block starts with:
            # word 0 - block number of next block in the file (0 if last)
            # word 1 - two's complement of number of words used in this block
            data = bytearray()
            for i, next_block_number in enumerate(self.entry.get_blocks()):
                if i >= block_number:
                    words = self.entry.directory.fs.read_18bit_words_block(next_block_number)
                    num_words_comp = words[1]
                    num_words = 0x40000 - num_words_comp
                    words = words[2 : 2 + num_words]  # Skip the first 2 words
                    t = from_18bit_words_to_bytes(words, self.file_mode)
                    data.extend(t)
                    number_of_blocks -= 1
                    if number_of_blocks == 0:
                        break
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
        if self.closed or block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        words = from_bytes_to_18bit_words(buffer, self.file_mode)
        self.write_18bit_words_block(words, block_number, number_of_blocks)

    def write_18bit_words_block(
        self,
        words: t.List[int],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        """
        if self.entry.raw_file_type == FileType.SYSTEM:
            # Contiguous files
            raise OSError(errno.EROFS, os.strerror(errno.EROFS))
        else:
            # Linked files
            blocks = self.entry.get_blocks()
            for i in range(block_number, block_number + number_of_blocks):
                next_block_number = blocks[i + 1] if i < len(blocks) - 1 else 0
                block_words = words[i * LINKED_FILE_WORDS_PER_BLOCK : (i + 1) * LINKED_FILE_WORDS_PER_BLOCK]
                num_words = len(block_words)
                num_words_comp = 0x40000 - num_words
                block_words = (
                    [next_block_number, num_words_comp]
                    + block_words
                    + ([0] * (LINKED_FILE_WORDS_PER_BLOCK - len(block_words)))
                )
                self.entry.directory.fs.write_18bit_words_block(blocks[i], block_words)

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
        if self.entry.raw_file_type == FileType.SYSTEM:
            return WORDS_PER_BLOCK * 3
        else:
            return LINKED_FILE_WORDS_PER_BLOCK * 3

    def close(self) -> None:
        """
        Close the file
        """
        self.closed = True

    def __str__(self) -> str:
        return self.entry.fullname


class DECSysDirectoryEntry(AbstractDirectoryEntry):
    """
    System program Directory Entry
    ------------------------------

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

    Working program Directory Entry
    -------------------------------

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

    Library Directory Entry
    ----------------------

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

    directory: "DECSysAbstractDirectory"
    filename: str  # Filename
    raw_file_type: FileType  # Raw file type
    block_number: int = 0  # Program / relocable binary / library block number
    fortran_block_number: int = 0  # Fortran block number
    assembler_block_number: int = 0  # Assembler block number
    starting_address: int = 0  # Starting address
    entry_length: int  # Entry length, in words (5 for system, 6 for working programs)

    def __init__(self, directory: "DECSysAbstractDirectory"):
        self.directory = directory

    @classmethod
    def read(cls, directory: "DECSysAbstractDirectory", words: t.List[int], position: int) -> "DECSysDirectoryEntry":
        if words[position] == FileType.SYSTEM.value:
            # System program
            return SystemDirectoryEntry.read(directory, words, position)
        else:
            # Working program
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
    def basename(self) -> str:
        """Filename"""
        return self.filename

    @abstractmethod
    def get_blocks(self, file_type: t.Optional[FileType] = None) -> t.List[int]:
        """
        Get the blocks used by the file
        """
        pass

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
        return WORDS_PER_BLOCK * 3

    def delete(self) -> bool:
        """
        Delete the directory entry
        """
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
        if self.raw_file_type == FileType.FORTRAN:
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
        blocks, first_free_block = self.directory.fs.allocate_space(number_of_blocks)
        # Update the file entry
        if self.raw_file_type == FileType.FORTRAN:
            self.fortran_block_number = blocks[0] if blocks else 0
        elif self.raw_file_type == FileType.ASSEMBLER:
            self.assembler_block_number = blocks[0] if blocks else 0
        else:  # FileType.BINARY, FileType.LIBRARY
            self.block_number = blocks[0] if blocks else 0
        # Write the directory and update the first free block
        if isinstance(self.directory, ProgramDirectory):
            self.directory.first_free_block = first_free_block
            self.directory.write()
        else:
            self.directory.write()
            directory = ProgramDirectory(self.directory.fs)
            directory.first_free_block = first_free_block
            directory.write()

    @property
    def file_type(self) -> t.Optional[str]:
        """File type"""
        return str(self.raw_file_type)

    def open(self, file_type: t.Optional[str] = None) -> DECSysFile:
        """
        Open a file
        """
        return DECSysFile(self, file_type)

    def read_bytes(self, file_type: t.Optional[str] = None) -> bytes:
        """Get the content of the file"""
        if file_type is None:
            if self.raw_file_type in [FileType.FORTRAN, FileType.ASSEMBLER]:
                file_type = ASCII
            else:
                file_type = IMAGE
        # Always read the file as IMAGE
        f = self.open(IMAGE)
        try:
            data = f.read_block(0, READ_FILE_FULL)
            if file_type == IMAGE:
                return data
            else:
                # Convert IMAGE => words => ASCII
                words = from_bytes_to_18bit_words(data, file_type=IMAGE)
                return fiodec_to_str(words).encode('ascii')
        finally:
            f.close()


class SystemDirectoryEntry(DECSysDirectoryEntry):
    """
    System program Directory Entry
    ------------------------------

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
        self.starting_address = words[position + 4] - 1
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
            + [self.block_number, self.starting_address + 1]
        )

    def get_blocks(self, file_type: t.Optional[FileType] = None) -> t.List[int]:
        """
        System files are stored as contiguous files
        word 0 - two's complement of number of words
        word 1 - initial load address – 1
        If the last word of the file is != 0o777777, the file continues in the next block
        """
        block_number = self.block_number
        if block_number == 0:
            return []
        num_blocks = 0
        last_word = 0
        while last_word != 0o777777:
            buffer = self.directory.fs.read_18bit_words_block(block_number + num_blocks)
            num_words = 0x40000 - buffer[0]
            t_num_blocks = math.ceil((num_words + 2) / 256)
            if t_num_blocks > 1:
                # Read the last block to check the last word
                buffer = self.directory.fs.read_18bit_words_block(block_number + num_blocks + t_num_blocks - 1)
            last_word = buffer[(num_words + 2) % 256]
            num_blocks += t_num_blocks
        return list(range(block_number, block_number + num_blocks))

    def __str__(self) -> str:
        return f"{self.filename:<15} Type: {self.file_type}  Block: {self.block_number:04}  Addr: {self.starting_address:>06}"


class WorkingDirectoryEntry(DECSysDirectoryEntry):
    """
    Working program Directory Entry
    -------------------------------

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
        Text files and library files are stored as linked files
        word 0 - block number of next block in the file (0 if last)
        word 1 - two's complement of number of words used in this block
        """
        # For working files, get the FORTRAN, Assembler of Binary fork
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
            next_block_number = self.directory.fs.read_18bit_words_block(next_block_number)[0]
        return blocks

    def __str__(self) -> str:
        if self.raw_file_type == FileType.FORTRAN:  # Fortran source code
            addr = f"Block: {self.fortran_block_number:04}"
        elif self.raw_file_type == FileType.ASSEMBLER:  # Assembler source code
            addr = f"Block: {self.assembler_block_number:04}"
        elif self.raw_file_type == FileType.BINARY:  # Binary code
            addr = f"Block: {self.block_number:04}"
        elif self.raw_file_type == FileType.WORKING:  # Working program
            addr = f"Fortran: {self.fortran_block_number:04}  Asm: {self.assembler_block_number:04}  Binary: {self.block_number:04}"
        else:
            addr = ""
        return f"{self.filename:<15} Type: {self.file_type}  {addr}"


class LibraryDirectoryEntry(DECSysDirectoryEntry):
    """
    Library Directory Entry
    ----------------------

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
        Text files and library files are stored as linked files
        word 0 - block number of next block in the file (0 if last)
        word 1 - two's complement of number of words used in this block
        """
        next_block_number = self.block_number
        blocks = []
        while next_block_number:
            blocks.append(next_block_number)
            next_block_number = self.directory.fs.read_18bit_words_block(next_block_number)[0]
        return blocks

    def __str__(self) -> str:
        return f"{self.filename:<15} Type: {self.file_type}  Block: {self.block_number:04}"


class DECSysAbstractDirectory(ABC):
    """
    Abstract Directory - the two concrete directories
    are Program Directory and Library Directory
    """

    fs: "DECSysFilesystem"
    entries: t.List["DECSysDirectoryEntry"]

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
    Program Directory (Block 2)

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
        http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf  Pag 12
        """
        self = ProgramDirectory(fs)
        self.entries = []
        words = self.fs.read_18bit_words_block(PROGRAM_DIRECTORY_BLOCK)
        dir_length = words[0]  # Directory length, in words
        self.first_free_block = words[255]  # First free block number
        position = 1
        while position < len(words) - 5 and position < dir_length:
            entry = DECSysDirectoryEntry.read(self, words, position)
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
        self.fs.write_18bit_words_block(PROGRAM_DIRECTORY_BLOCK, words)


class LibraryDirectory(DECSysAbstractDirectory):
    """
    Library Directory (Block 3)

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
        http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf  Pag 12
        """
        self = LibraryDirectory(fs)
        self.entries = []
        words = self.fs.read_18bit_words_block(LIBRARY_DIRECTORY_BLOCK)
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
        self.fs.write_18bit_words_block(LIBRARY_DIRECTORY_BLOCK, words)


class DECSysFilesystem(AbstractFilesystem, BlockDevice18Bit):
    """

    DECtapes prepared for the DECSYS-7 user contains
    384 blocks of 256 18-bit words each.

    Dectape
    =======

    Block

            +-------------------+
      0     | Unused            |
            +-------------------+
      1     | Tape label        |
            +-------------------+
      2     | Program directory |
            +-------------------+
      3     | Library directory |
            +-------------------+
      4     | Keyboard          |
      6     | Monitor           |
            +-------------------+
      7     | Files             |
            /                   /
    382     |                   |
            +-------------------+
    383     | Unused            |
            +-------------------+

    """

    fs_name = "decsys"
    fs_description = "PDP-7 DECSys"
    words_per_block = WORDS_PER_BLOCK

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        if strict:
            if self.get_size() // WORDS_PER_BLOCK // 4 != DECTAPE_BLOCKS:
                raise OSError(errno.EINVAL, "Invalid DECSys tape size")
        return self

    def read_tape_label(self) -> t.Tuple[str, str]:
        """
        Read tape label

        The tape label contains alphanumeric strings which identify the tape name and
        the date on which it was updated.

        The labels are of two Baudot strings, padded with 0’s to an 18b boundary and
        terminated by a word of all ones (0o777777).

        https://simh.trailing-edge.com/docs/decsys.pdf  Pag 2
        http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf  Pag 12
        """
        words = self.read_18bit_words_block(TAPE_LABEL_BLOCK)
        label1, position = read_baudot_string(words, 0)
        label2, _ = read_baudot_string(words, position + 1)
        return label1, label2

    def read_dir_entries(self) -> t.Iterator["DECSysDirectoryEntry"]:
        """
        Read directory entries

        https://simh.trailing-edge.com/docs/decsys.pdf  Pag 2
        http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf  Pag 12
        """
        program_directory = ProgramDirectory.read(self)
        yield from program_directory.entries
        library_directory = LibraryDirectory.read(self)
        yield from library_directory.entries

    @property
    def entries_list(self) -> t.Iterator["DECSysDirectoryEntry"]:
        yield from self.read_dir_entries()

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["DECSysDirectoryEntry"]:
        if pattern:
            raw_file_type, pattern = decsys_split_fullname(pattern, wildcard=True)
        else:
            raw_file_type = None
        for entry in self.read_dir_entries():
            if filename_match(entry.basename, pattern, wildcard):
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

    def get_file_entry(self, fullname: str) -> DECSysDirectoryEntry:
        """
        Get the directory entry for a file
        """
        # Split file type and filename (e.g. "F,HELLO" -> FileType.FORTRAN, "HELLO")
        raw_file_type, filename = decsys_split_fullname(fullname)
        for entry in self.read_dir_entries():
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

    def get_allocated_blocks(self) -> t.List[int]:
        """
        Get the list of allocated blocks
        """
        blocks = list(range(0, FIRST_FILE_BLOCK))
        for entry in self.read_dir_entries():
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
            block_words = [8] * LINKED_FILE_WORDS_PER_BLOCK
            num_words = len(block_words)
            num_words_comp = 0x40000 - num_words
            block_words = (
                [next_block_number, num_words_comp]
                + block_words
                + ([0] * (LINKED_FILE_WORDS_PER_BLOCK - len(block_words)))
            )
            self.write_18bit_words_block(blocks[i], block_words)
        first_free_block = max(allocated_blocks + blocks) + 1
        return blocks, first_free_block

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,
    ) -> t.Optional[DECSysDirectoryEntry]:
        """
        Create a new file with a given length in number of blocks
        """
        raw_file_type, filename = decsys_split_fullname(fullname)
        if raw_file_type is None:
            raise ValueError("Specify the file type as FILE_TYPE,FILENAME")
        if raw_file_type == FileType.SYSTEM:
            raise ValueError("SYSTEM files are not supported")
        if raw_file_type == FileType.LIBRARY:
            raw_file_type = FileType.BINARY
        # If the file already exists, deallocate the blocks
        try:
            entry: DECSysDirectoryEntry = self.get_file_entry(fullname)  # type: ignore
            entry.deallocate()
        except FileNotFoundError:
            # Create a new entry
            directory = LibraryDirectory(self) if raw_file_type == FileType.LIBRARY else ProgramDirectory.read(self)
            if raw_file_type == FileType.SYSTEM:
                # entry = SystemDirectoryEntry(directory)
                raise ValueError("SYSTEM files are not supported")
            elif raw_file_type == FileType.LIBRARY:
                entry = LibraryDirectoryEntry(directory)
            else:
                entry = WorkingDirectoryEntry(directory)
            entry.filename = filename
            entry.raw_file_type = raw_file_type
            directory.entries.append(entry)
        # Allocate space
        entry.allocate(number_of_blocks)
        return entry

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: t.Optional[date] = None,
        file_type: t.Optional[str] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        words = str_to_fiodec(content.decode('ascii'))
        number_of_blocks = int(math.ceil(len(content) / LINKED_FILE_WORDS_PER_BLOCK))
        entry = self.create_file(fullname, number_of_blocks, creation_date, file_type)
        if entry is not None:
            f = entry.open(file_type)
            try:
                f.write_18bit_words_block(words, block_number=0, number_of_blocks=number_of_blocks)
            finally:
                f.close()

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        entries = self.filter_entries_list(pattern, wildcard=True)
        if not entries:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), pattern)
        # Read tape label
        if not options.get("brief"):
            label1, label2 = self.read_tape_label()
            sys.stdout.write(f"{label1}  {label2}\n")
        for x in entries:
            if options.get("brief"):
                sys.stdout.write(f"{x.basename}\n")
            elif x.raw_file_type == FileType.SYSTEM:
                sys.stdout.write(f"{x.basename} S {x.block_number:04}\n")
            elif x.raw_file_type == FileType.LIBRARY:
                sys.stdout.write(f"{x.basename}, L {x.block_number:04}\n")
            else:
                # For working programs, starting tape-block numbers are shown for,
                # the FORTRAN source, the assembly source, the relocatable binary
                sys.stdout.write(
                    f"{x.basename} W {x.fortran_block_number:04},{x.assembler_block_number:04},{x.block_number:04}\n"
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
                if entry.raw_file_type == FileType.SYSTEM:
                    sys.stdout.write(f"Blocks:                   {entry.get_blocks()}\n")
                    sys.stdout.write(f"Starting address:         {entry.starting_address:>06}\n")
                elif entry.raw_file_type == FileType.LIBRARY:
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
            label1, label2 = self.read_tape_label()
            sys.stdout.write(f"Label 1:                  {label1}\n")
            sys.stdout.write(f"Label 2:                  {label2}\n")
            program_directory = ProgramDirectory.read(self)
            sys.stdout.write(f"First free block number:  {program_directory.first_free_block:04}\n\n")
            sys.stdout.write("Filename        Type     Address  Length\n")
            sys.stdout.write("--------        ----     -------  ------\n")
            for entry in self.read_dir_entries():
                if entry.raw_file_type == FileType.WORKING:
                    for raw_file_type in [FileType.FORTRAN, FileType.ASSEMBLER, FileType.BINARY]:
                        blocks = entry.get_blocks(raw_file_type)
                        length = len(blocks)
                        file_type = str(raw_file_type)
                        block = blocks[0] if blocks else 0
                        filename = f"{file_type[0]},{entry.filename}"
                        sys.stdout.write(f"{filename:<15} {file_type:<10}  {block:04}  {length}\n")
                else:
                    file_type = str(entry.raw_file_type)
                    blocks = entry.get_blocks()
                    length = len(blocks)
                    block = blocks[0] if blocks else 0
                    filename = f"{file_type[0]},{entry.filename}"
                    sys.stdout.write(f"{filename:<15} {file_type:<10}  {block:04}  {length}\n")

    def dump(self, fullname: t.Optional[str], start: t.Optional[int] = None, end: t.Optional[int] = None) -> None:
        """Dump the content of a file or a range of blocks"""
        if fullname:
            entry = self.get_file_entry(fullname)
            if start is None:
                start = 0
            blocks = entry.get_blocks()
            if end is None or end > len(blocks) - 1:
                end = entry.get_length() - 1
            for block_number in range(start, end + 1):
                words = self.read_18bit_words_block(blocks[block_number])
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
                words = self.read_18bit_words_block(block_number)
                sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                oct_dump(words)

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
            IMAGE,
            ASCII,
        ]

    def __str__(self) -> str:
        return str(self.f)
