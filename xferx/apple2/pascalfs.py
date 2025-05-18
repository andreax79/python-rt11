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
import struct
import sys
import typing as t
from datetime import date

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..commons import BLOCK_SIZE, READ_FILE_FULL, dump_struct, filename_match
from .disk import AppleDisk

__all__ = [
    "PascalFile",
    "PascalDirectoryEntry",
    "PascalFilesystem",
]

# Apple II Pascal Filesystem
# https://archive.org/details/apple-ii-pascal-1.3/page/n803/mode/2up

FILENAME_LEN = 15  # max filename length
DIR_BLOCK = 2  # directory first block
DIR_SIZE = 4  # 4 blocks
MAX_DIR_ENTRIES = 77  # max directory entries
VOLUME_DIRECTORY_ENTRY_FORMAT = "<HHH8sHHHH4s"
VOLUME_DIRECTORY_ENTRY_SIZE = struct.calcsize(VOLUME_DIRECTORY_ENTRY_FORMAT)
DIRECTORY_ENTRY_FORMAT = "<HHH16sHH"
DIRECTORY_ENTRY_SIZE = struct.calcsize(DIRECTORY_ENTRY_FORMAT)
DEFAULT_VOLUME_NAME = "PASCAL"

# Diskette file type, Pag 34
# https://archive.org/details/Apple_Pascal_Operating_System_Reference_Manual_HQ/page/n33/mode/2up

FILE_TYPE_UNTYPED = 0  # Untyped file
FILE_TYPE_BAD = 1  # Bad block
FILE_TYPE_CODE = 2  # Machine-executable code
FILE_TYPE_TEXT = 3  # Human-readable text file
FILE_TYPE_INFO = 4  # (not used)
FILE_TYPE_DATA = 5  # Data file
FILE_TYPE_GRAF = 6  # (not used)
FILE_TYPE_FOTO = 7  # (not used)
FILE_TYPE_SECUREDIR = 8  # (not used)

FILE_TYPES = {
    FILE_TYPE_BAD: "BAD",
    FILE_TYPE_CODE: "CODE",
    FILE_TYPE_TEXT: "TEXT",
    FILE_TYPE_INFO: "INFO",
    FILE_TYPE_DATA: "DATA",
    FILE_TYPE_GRAF: "GRAF",
    FILE_TYPE_FOTO: "FOTO",
}


def pascal_to_str(buffer: bytes) -> str:
    """
    Convert a Pascal string to a Python string
    """
    length = buffer[0]
    return buffer[1 : length + 1].decode("ascii", errors="ignore")


def str_to_pascal(val: str) -> bytes:
    """
    Convert a Python string to a Pascal string
    """
    length = len(val)
    return bytes([length]) + val.encode("ascii", errors="ignore")


def pascal_to_date(val: int) -> t.Optional[date]:
    """
    Translate Pascal date to Python date
    """
    if val == 0:
        return None
    year = (val >> 9) & 0x7F
    day = (val >> 4) & 0x1F
    month = val & 0x0F
    year = year + 1900 if year >= 80 else year + 2000
    try:
        return date(year, month, day)
    except:
        return None


def date_to_pascal(val: t.Optional[date]) -> int:
    """
    Translate Python date to Pascal date
    """
    if val is None:
        return 0
    year = val.year
    year = year - 2000 if year >= 2000 else year - 1900
    return (year << 9) | (val.day << 4) | val.month


def pascal_canonical_filename(fullname: t.Optional[str], wildcard: bool = False) -> t.Optional[str]:
    """
    Generate the canonical Pascal filename
    """
    if fullname:
        fullname = fullname[:FILENAME_LEN].upper()
    return fullname


def pascal_get_raw_file_type(file_type: t.Optional[str], default: int = FILE_TYPE_TEXT) -> int:
    """
    Get the file type id from a string
    """
    if not file_type:
        return default
    file_type = file_type.upper()
    for file_id, file_str in FILE_TYPES.items():
        if file_str == file_type:
            return file_id
    raise Exception("?KMON-F-Invalid file type specified with option")


def format_long_filetype(file_type: int) -> str:
    """
    Format file type
    """
    if file_type == FILE_TYPE_BAD:
        return "Bad disk"
    elif file_type == FILE_TYPE_CODE:
        return "Codefile"
    elif file_type == FILE_TYPE_TEXT:
        return "Textfile"
    elif file_type == FILE_TYPE_INFO:
        return "Infofile"
    elif file_type == FILE_TYPE_DATA:
        return "Datafile"
    elif file_type == FILE_TYPE_GRAF:
        return "Graffile"
    elif file_type == FILE_TYPE_FOTO:
        return "Fotofile"
    else:
        return "ILLEGAL"


class PascalFile(AbstractFile):
    entry: "PascalDirectoryEntry"
    closed: bool

    def __init__(self, entry: "PascalDirectoryEntry"):
        self.entry = entry
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
            number_of_blocks = self.entry.length
        if self.closed or block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        if block_number + number_of_blocks > self.entry.length:
            number_of_blocks = self.entry.length - block_number
        return self.entry.fs.read_block(
            self.entry.start_block + block_number,
            number_of_blocks,
        )

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
            or block_number + number_of_blocks > self.entry.length
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        self.entry.fs.write_block(
            buffer,
            self.entry.start_block + block_number,
            number_of_blocks,
        )

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


class VolumeDirectory:

    fs: "PascalFilesystem"
    start_block: int  # Start block
    following_block: int  # Last block + 1
    raw_file_type: int = 0  # File type
    volume_name: str  # Volume name
    number_of_blocks: int  # Number of blocks
    number_of_files: int  # Number of files
    last_access_time: int  # Last access time
    raw_most_recently_date: int  # Most recently date setting
    directory_entries: t.List["PascalDirectoryEntry"]  # 77 entries, even if empty/unused

    def __init__(self, fs: "PascalFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "PascalFilesystem") -> "VolumeDirectory":
        """
        Read the volume directory from disk
        """
        self = cls(fs)
        buffer = fs.read_block(DIR_BLOCK, number_of_blocks=DIR_SIZE)
        (
            self.start_block,
            self.following_block,
            self.raw_file_type,
            volume_name,
            self.number_of_blocks,
            self.number_of_files,
            self.last_access_time,
            self.raw_most_recently_date,
            _,
        ) = struct.unpack_from(VOLUME_DIRECTORY_ENTRY_FORMAT, buffer, 0)
        self.volume_name = pascal_to_str(volume_name)
        # Read directory entries
        self.directory_entries = [
            PascalDirectoryEntry.read(self.fs, buffer, i * DIRECTORY_ENTRY_SIZE) for i in range(1, MAX_DIR_ENTRIES + 1)
        ]
        return self

    def write(self) -> None:
        """ "
        Write the volume directory to disk
        """
        buffer = bytearray(BLOCK_SIZE * DIR_SIZE)
        # Update number of files
        entries = [entry for entry in self.iterdir() if entry.filename]
        self.number_of_files = len(entries)
        # Write volume directory entry
        struct.pack_into(
            VOLUME_DIRECTORY_ENTRY_FORMAT,
            buffer,
            0,
            self.start_block,
            self.following_block,
            self.raw_file_type,
            str_to_pascal(self.volume_name),
            self.number_of_blocks,
            self.number_of_files,
            self.last_access_time,
            self.raw_most_recently_date,
            b"\0" * 4,
        )
        # Write directory entries
        for i, entry in enumerate(sorted(entries), start=1):
            struct.pack_into(
                DIRECTORY_ENTRY_FORMAT,
                buffer,
                i * DIRECTORY_ENTRY_SIZE,
                entry.start_block,
                entry.following_block,
                entry.raw_file_type,
                str_to_pascal(entry.filename),
                entry.last_block_bytes,
                entry.raw_mod_date,
            )
        self.directory_entries = entries + [
            PascalDirectoryEntry(self.fs) for _ in range(MAX_DIR_ENTRIES - len(entries))
        ]
        self.fs.write_block(bytes(buffer), DIR_BLOCK, number_of_blocks=DIR_SIZE)

    @property
    def most_recently_date(self) -> t.Optional[date]:
        return pascal_to_date(self.raw_most_recently_date)

    def iterdir(self, include_empty_area: bool = False) -> t.Iterator["PascalDirectoryEntry"]:
        """
        Iterate over directory entries
        """
        count = self.number_of_files
        following_block = self.following_block
        for entry in self.directory_entries:
            if count == 0:
                break
            if include_empty_area:
                # Add empty area between files
                free_blocks = entry.start_block - following_block
                if free_blocks > 0:
                    free_entry = PascalDirectoryEntry(
                        self.fs,
                        start_block=following_block,
                        following_block=entry.start_block,
                    )
                    yield free_entry
            if entry.filename:
                count -= 1
                yield entry
            following_block = entry.following_block

        if include_empty_area:
            # Add empty area at the end
            free_blocks = self.number_of_blocks - following_block
            if free_blocks > 0:
                free_entry = PascalDirectoryEntry(
                    self.fs,
                    start_block=following_block,
                    following_block=self.number_of_blocks,
                )
                yield free_entry

    def allocate_space(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,  # optional file type
        last_block_bytes: int = 0,  # number of bytes in last block
    ) -> "PascalDirectoryEntry":
        """
        Allocate space for a new file
        """
        if self.number_of_files >= MAX_DIR_ENTRIES:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        # Search for empty space
        for entry in self.iterdir(include_empty_area=True):
            # Check if there is enough space
            if entry.is_empty and entry.length >= number_of_blocks:
                # Create the new entry
                entry.filename = fullname
                entry.last_block_bytes = last_block_bytes
                entry.raw_file_type = pascal_get_raw_file_type(file_type)
                entry.raw_mod_date = date_to_pascal(creation_date or date.today())
                entry.following_block = entry.start_block + number_of_blocks
                # Update volume directory
                self.directory_entries = self.directory_entries + [entry]
                self.number_of_files += 1
                return entry
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def __str__(self) -> str:
        try:
            date_str = self.most_recently_date.strftime("%d-%b-%y").lstrip("0")  # type: ignore
        except Exception:
            date_str = ""
        return f"Volume: {self.volume_name}  Blocks: {self.number_of_blocks}  Files: {self.number_of_files}  Access time: {self.last_access_time}  Set date: {date_str}"


class PascalDirectoryEntry(AbstractDirectoryEntry):

    fs: "PascalFilesystem"
    start_block: int  # Start block
    following_block: int  # Last block + 1
    filename: str  # Filename
    last_block_bytes: int  # Number of bytes used in last block
    raw_file_type: int  # File type
    raw_mod_date: int  # Modification date

    def __init__(
        self,
        fs: "PascalFilesystem",
        start_block: int = 0,
        following_block: int = 0,
        filename: str = "",
        last_block_bytes: int = 0,
        raw_file_type: int = 0,
        raw_mod_date: int = 0,
    ):
        self.fs = fs
        self.start_block = start_block
        self.following_block = following_block
        self.filename = filename
        self.last_block_bytes = last_block_bytes
        self.raw_file_type = raw_file_type
        self.raw_mod_date = raw_mod_date

    @classmethod
    def read(cls, fs: "PascalFilesystem", buffer: bytes, position: int) -> "PascalDirectoryEntry":
        self = cls(fs)
        (
            self.start_block,  # start block
            self.following_block,  # last block + 1
            self.raw_file_type,  # file type
            filename,  # filename
            self.last_block_bytes,  # number of bytes in last block
            self.raw_mod_date,  # modification date
        ) = struct.unpack_from(DIRECTORY_ENTRY_FORMAT, buffer, position)
        self.filename = pascal_to_str(filename)
        return self

    @property
    def length(self) -> int:
        """
        Length in blocks
        """
        return self.following_block - self.start_block

    @property
    def is_empty(self) -> bool:
        return self.length == 0 or not self.filename

    @property
    def file_type(self) -> t.Optional[str]:
        """
        File type (e.g. DATA)
        """
        return FILE_TYPES.get(self.raw_file_type)

    @property
    def long_file_type(self) -> str:
        """
        File type long description (e.g. Datafile)
        """
        return format_long_filetype(self.raw_file_type)

    @property
    def fullname(self) -> str:
        return self.filename

    @property
    def basename(self) -> str:
        return self.filename

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return self.length

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return (self.length - 1) * self.get_block_size() + self.last_block_bytes

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    @property
    def creation_date(self) -> t.Optional[date]:
        return pascal_to_date(self.raw_mod_date)

    def delete(self) -> bool:
        """
        Delete the file
        """
        volume_dir = VolumeDirectory.read(self.fs)
        for entry in volume_dir.directory_entries:
            if entry.start_block == self.start_block and entry.filename == self.filename:
                self.start_block = entry.start_block = 0
                self.following_block = entry.following_block = 0
                self.filename = entry.filename = ""
                self.last_block_bytes = entry.last_block_bytes = 0
                self.raw_file_type = entry.raw_file_type = 0
                self.raw_mod_date = entry.raw_mod_date = 0
                volume_dir.write()
                return True
        return False

    def write(self) -> bool:
        """
        Write the directory entry
        """
        volume_dir = VolumeDirectory.read(self.fs)
        for entry in volume_dir.directory_entries:
            if entry.start_block == self.start_block:
                volume_dir.write()
                return True
        return False

    def open(self, file_mode: t.Optional[str] = None) -> PascalFile:
        """
        Open a file
        """
        return PascalFile(self)

    def __lt__(self, other: "PascalDirectoryEntry") -> bool:
        return self.start_block < other.start_block

    def __gt__(self, other: "PascalDirectoryEntry") -> bool:
        return self.start_block > other.start_block

    def __str__(self) -> str:
        date = self.creation_date and self.creation_date.strftime("%d-%b-%y") or ""
        if self.creation_date and not self.is_empty:
            date = self.creation_date.strftime("%d-%b-%y").lstrip("0")
        else:
            date = ""
        fullname = self.fullname if self.fullname else "< UNUSED >"
        return f"{fullname:<15} {self.length:>6}  {date:>9} {self.start_block:>4} -> {self.following_block:>4}  {self.last_block_bytes:>3}  {self.raw_file_type} {self.long_file_type}"

    def __repr__(self) -> str:
        return str(self)


class PascalFilesystem(AbstractFilesystem, AppleDisk):
    """
    Apple II Pascal Filesystem
    """

    fs_name = "pascal"
    fs_description = "Apple II Pascal"

    volume_name: str = ""  # Volume name

    def __init__(self, file: "AbstractFile"):
        super().__init__(file, rx_device_support=False)

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        # Read volume dir
        volume_dir = VolumeDirectory.read(self)
        if not volume_dir.volume_name:
            self.prodos_order = True
            volume_dir = VolumeDirectory.read(self)
            if not volume_dir.volume_name:
                raise OSError(errno.EIO, os.strerror(errno.EIO))
        self.volume_name = volume_dir.volume_name
        return self

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["PascalDirectoryEntry"]:
        if pattern:
            pattern = pascal_canonical_filename(pattern, wildcard=wildcard)
        volume_dir = VolumeDirectory.read(self)
        for entry in volume_dir.iterdir(include_empty_area=include_all):
            if filename_match(entry.basename, pattern, wildcard):
                yield entry

    @property
    def entries_list(self) -> t.Iterator["PascalDirectoryEntry"]:
        volume_dir = VolumeDirectory.read(self)
        yield from volume_dir.iterdir()

    def get_file_entry(self, fullname: str) -> PascalDirectoryEntry:
        fullname = pascal_canonical_filename(fullname)  # type: ignore
        for entry in self.entries_list:
            if entry.fullname == fullname and not entry.is_empty:
                return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def read_bytes(self, fullname: str, file_type: t.Optional[str] = None) -> bytes:  # fullname=filename+ext
        entry = self.get_file_entry(fullname)
        return self.read_block(entry.start_block, entry.length)

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
        number_of_blocks = int(math.ceil(len(content) / BLOCK_SIZE))
        last_block_bytes = len(content) % BLOCK_SIZE
        entry = self.create_file(
            fullname=fullname,
            number_of_blocks=number_of_blocks,
            creation_date=creation_date,
            file_type=file_type,
            last_block_bytes=last_block_bytes,
        )
        if entry is not None:
            content = content + (b"\0" * BLOCK_SIZE)  # pad with zeros
            f = entry.open(file_mode)
            try:
                f.write_block(content, block_number=0, number_of_blocks=number_of_blocks)
            finally:
                f.close()

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,  # optional file type
        last_block_bytes: int = 0,  # number of bytes in last block
    ) -> t.Optional[PascalDirectoryEntry]:
        fullname = pascal_canonical_filename(fullname)  # type: ignore
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass
        volume_dir = VolumeDirectory.read(self)
        entry = volume_dir.allocate_space(
            fullname=fullname,
            number_of_blocks=number_of_blocks,
            creation_date=creation_date,
            file_type=file_type,
            last_block_bytes=last_block_bytes,
        )
        volume_dir.write()
        return entry

    def chdir(self, fullname: str) -> bool:
        return False

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        volume_dir = VolumeDirectory.read(self)
        files = 0  # Number of listed files
        blocks = 0  # Used blocks
        unused = 0  # Unused blocks
        largest_unused = 0  # Largest unused block
        if not options.get("brief"):
            sys.stdout.write(f"{volume_id}:\n")
        if pattern:
            pattern = pascal_canonical_filename(pattern, wildcard=True)
        for x in volume_dir.iterdir(include_empty_area=True):
            if options.get("brief"):
                if filename_match(x.basename, pattern, wildcard=True):
                    sys.stdout.write(f"{x.fullname}\n")
            else:
                if x.is_empty:
                    if x.length > largest_unused:
                        largest_unused = x.length
                    unused = unused + x.length
                    if not pattern:
                        sys.stdout.write(f"{'< UNUSED >':<15} {x.length:>6}  {'':>9} {x.start_block:>4}\n")
                else:
                    blocks = blocks + x.length
                    date = x.creation_date and x.creation_date.strftime("%d-%b-%y") or ""
                    if x.creation_date:
                        date = x.creation_date.strftime("%d-%b-%y").lstrip("0")
                    else:
                        date = ""
                    if filename_match(x.basename, pattern, wildcard=True):
                        files = files + 1
                        sys.stdout.write(
                            f"{x.fullname:<15} {x.length:>6}  {date:>9} {x.start_block:>4}  {x.last_block_bytes:>3}  {x.long_file_type}\n"
                        )
        if not options.get("brief"):
            sys.stdout.write(
                f"{files}/{volume_dir.number_of_files} files <listed/in dir>, {blocks} blocks used, {unused} unused, {largest_unused} in largest\n"
            )

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if arg:
            # Dump by path
            entry = self.get_file_entry(arg)
            entry_dict = dict(entry.__dict__)
            del entry_dict["fs"]
            sys.stdout.write(dump_struct(entry_dict) + "\n")
        else:
            # Dump the entire filesystem
            volume_dir = VolumeDirectory.read(self)
            volume_dir_dict = dict(volume_dir.__dict__)
            del volume_dir_dict["fs"]
            del volume_dir_dict["directory_entries"]
            sys.stdout.write(dump_struct(volume_dir_dict))
            sys.stdout.write("\n\n")
            sys.stdout.write("Nr  Filename        Blocks  Date     Start     End Size  File type\n")
            sys.stdout.write("--  --------        ------  ----     -----     --- ----  ---------\n")
            for i, entry in enumerate(volume_dir.directory_entries, start=1):
                # Skip null entries
                if (
                    entry.filename
                    or entry.length
                    or entry.raw_file_type
                    or entry.start_block
                    or entry.following_block
                    or entry.last_block_bytes
                    or entry.raw_mod_date
                    or options.get("full")
                ):
                    sys.stdout.write(f"{i:>2}# {entry}\n")

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        """
        Initialize the filesystem
        """
        try:
            volume_name = kwargs["name"].strip().upper() or DEFAULT_VOLUME_NAME  # type: ignore
        except Exception:
            volume_name = DEFAULT_VOLUME_NAME
        volume_dir = VolumeDirectory(self)
        volume_dir.start_block = 0
        volume_dir.following_block = DIR_BLOCK + DIR_SIZE
        volume_dir.raw_file_type = 0
        volume_dir.volume_name = volume_name
        volume_dir.number_of_blocks = self.f.get_size() // BLOCK_SIZE
        volume_dir.number_of_files = 0
        volume_dir.last_access_time = 0
        volume_dir.raw_most_recently_date = date_to_pascal(date.today())
        volume_dir.directory_entries = [PascalDirectoryEntry(self, 0, 0) for _ in range(MAX_DIR_ENTRIES)]
        volume_dir.write()
        self.volume_name = volume_name

    def close(self) -> None:
        self.f.close()

    def get_pwd(self) -> str:
        return ""

    def get_types(self) -> t.List[str]:
        """
        Get the list of the supported file types
        """
        return list(FILE_TYPES.values())

    def __str__(self) -> str:
        return str(self.f)
