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
from datetime import date, datetime

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..commons import ASCII, IMAGE, READ_FILE_FULL, dump_struct, filename_match
from ..unix.commons import unix_join, unix_split
from .dgdosfs import (
    ATCHA,
    ATCON,
    ATDIR,
    ATLNK,
    ATPAR,
    ATPER,
    ATRAN,
    ATRP,
    ATWP,
    format_attr,
    rdos_canonical_filename,
    rdos_join,
    rdos_to_date,
)

__all__ = [
    "DGDOSDumpFile",
    "DGDOSDumpFilesystem",
]


# RDOS System Reference - DUMP File format Pag 125
# https://bitsavers.trailing-edge.com/pdf/dg/software/rdos/093-400027-00_DGDOSDump_SystemReference_Oct83.pdf

NAME_BLOCK_ID = 0o377  # Name block
DATA_BLOCK_ID = 0o376  # Data block
ERROR_BLOCK_ID = 0o375  # Error block
END_BLOCK_ID = 0o374  # End block
TIME_BLOCK_ID = 0o373  # Time block
LINK_DATA_BLOCK_ID = 0o372  # Link data block
LINK_ACCESS_ATTRIBUTES_BLOCK_ID = 0o371  # Link access attributes block
END_OF_SEGMENT_BLOCK_ID = 0o370  # End of segment block


class AbstractBlock:
    """
    Every dumpfile begins with a Name Block and ends with an End Block.
    Each subdirectory in the dumpfile also ends with an end block.
    """

    fs: "DGDOSDumpFilesystem"
    position: int

    def __init__(self, fs: "DGDOSDumpFilesystem", position: int):
        self.fs = fs
        self.position = position

    @classmethod
    def read(cls, fs: "DGDOSDumpFilesystem") -> "AbstractBlock":
        position = fs.f.tell()
        block_id = fs.read_byte()
        if block_id == NAME_BLOCK_ID:
            return NameBlock(fs, position)
        elif block_id == DATA_BLOCK_ID:
            return DataBlock(fs, position)
        elif block_id == ERROR_BLOCK_ID:
            return ErrorBlock(fs, position)
        elif block_id == END_BLOCK_ID:
            return EndBlock(fs, position)
        elif block_id == TIME_BLOCK_ID:
            return TimeBlock(fs, position)
        elif block_id == LINK_DATA_BLOCK_ID:
            return LinkDataBlock(fs, position)
        elif block_id == LINK_ACCESS_ATTRIBUTES_BLOCK_ID:
            return LinkAccessAttributesBlock(fs, position)
        elif block_id == END_OF_SEGMENT_BLOCK_ID:
            return EndOfSegmentBlock(fs, position)
        else:
            raise Exception(f"{block_id} is not a valid block type")


class NameBlock(AbstractBlock):
    """
    Name block
    """

    attributes: int  # File attributes
    contiguous: int  # Contiguous block number
    data: bytes  # File name

    def __init__(self, fs: "DGDOSDumpFilesystem", position: int):
        super().__init__(fs, position)
        self.attributes = fs.read_word()
        if self.attributes & ATCON != 0:
            self.contiguous = fs.read_word()
        self.data = fs.read_to_null()


class DataBlock(AbstractBlock):
    """
    Data block
    """

    byte_count: int  # Number of bytes in the block
    checksum: int  # Checksum
    data_position: int  # File position of the data
    data: bytes  # Data

    def __init__(self, fs: "DGDOSDumpFilesystem", position: int):
        super().__init__(fs, position)
        self.byte_count = fs.read_word()
        self.checksum = fs.read_word()
        self.data_position = fs.f.tell()
        self.data = fs.f.read(self.byte_count)


class EndBlock(AbstractBlock):
    """
    End block
    """


class ErrorBlock(AbstractBlock):
    """
    Error block
    """


class TimeBlock(AbstractBlock):
    """
    Time block
    """

    last_access_date: int
    last_modification_date: int
    last_modification_time: int

    def __init__(self, fs: "DGDOSDumpFilesystem", position: int):
        super().__init__(fs, position)
        self.last_access_date = fs.read_word()
        self.last_modification_date = fs.read_word()
        self.last_modification_time = fs.read_word()


class LinkDataBlock(AbstractBlock):
    """
    Link data block
    """

    def __init__(self, fs: "DGDOSDumpFilesystem", position: int):
        super().__init__(fs, position)
        self.dirname = fs.read_to_null()
        self.resfilename = fs.read_to_null()


class LinkAccessAttributesBlock(AbstractBlock):
    """
    Link access attributes block
    """

    def __init__(self, fs: "DGDOSDumpFilesystem", position: int):
        super().__init__(fs, position)
        self.attributes = fs.read_word()


class EndOfSegmentBlock(AbstractBlock):
    """
    End of segment block
    """

    def __init__(self, fs: "DGDOSDumpFilesystem", position: int):
        super().__init__(fs, position)
        self.t = fs.read_word()
        self.segment_number = ord(fs.f.read(1))
        self.filename = fs.read_to_null()


class DGDOSDumpFile(AbstractFile):
    entry: "DGDOSDumpEntry"
    closed: bool

    def __init__(self, entry: "DGDOSDumpEntry", file_mode: t.Optional[str] = None):
        self.entry = entry
        self.closed = False
        self.file_mode = file_mode or IMAGE

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
        for i in range(block_number, block_number + number_of_blocks):
            self.entry.fs.f.seek(self.entry.addresses[i])
            data.extend(self.entry.fs.f.read(self.entry.block_size))
        # data = self.entry.fs.f.read(number_of_blocks * BLOCK_SIZE)
        # Convert to ASCII if needed
        if self.file_mode == ASCII:
            return bytes([0x0A if x == 0x0D else x for x in data])
        else:
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
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return self.entry.get_length()

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


class DGDOSDumpEntry(AbstractDirectoryEntry):
    fs: "DGDOSDumpFilesystem"
    parent: t.Optional["DGDOSDumpEntry"]  # Parent directory/partition
    filename: str = ""
    extension: str = ""
    attributes: int = 0
    link_access_attributes: int = 0
    size: int = 0  # Size in bytes
    block_size: int = 0
    addresses: t.List[int] = []  # List of block file position
    last_access_date: int = 0  # days since 1967-12-31
    last_modification_date: int = 0  # days since 1967-12-31
    last_modification_time: int = 0  # hour (high byte) and minute (low byte)
    target: str = ""  # link target

    def __init__(self, fs: "DGDOSDumpFilesystem", name_block: NameBlock, parent: t.Optional["DGDOSDumpEntry"] = None):
        self.fs = fs
        basename = name_block.data.decode("ascii", "ignore").rstrip("\0")
        try:
            self.filename, self.extension = basename.split(".", 1)
        except Exception:
            self.filename = basename
            self.extension = ""
        self.attributes = name_block.attributes
        self.parent = parent
        self.addresses = []

    @classmethod
    def create(
        cls,
        fs: "DGDOSDumpFilesystem",
        parent: t.Optional["DGDOSDumpEntry"],
        filename: str,
        length: int,  # Length in blocks
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # optional creation date
        length_bytes: t.Optional[int] = None,  # optional length int bytes
    ) -> "DGDOSDumpEntry":
        """
        Create a new regular file
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def write_buffer(self, buffer: bytearray, position: int) -> None:
        """
        Write the UFD entry to the buffer
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    @property
    def is_random(self) -> bool:
        """
        Check if the file is random organized
        """
        return self.attributes & ATRAN != 0

    @property
    def is_contiguous(self) -> bool:
        """
        Check if the file is contiguous organized
        """
        return self.attributes & ATCON != 0

    @property
    def is_sequential(self) -> bool:
        """
        Check if the file is sequential organized
        """
        return not self.is_random and not self.is_contiguous and not self.is_link

    @property
    def is_link(self) -> bool:
        """
        Check if the file is a link
        """
        return self.attributes & ATLNK != 0

    @property
    def is_directory(self) -> bool:
        """
        Check if the file is a directory
        """
        return self.attributes & ATDIR != 0

    @property
    def is_partition(self) -> bool:
        """
        Check if the file is a partition
        """
        return self.attributes & ATPAR != 0

    @property
    def is_empty(self) -> bool:
        return self.filename == "" and self.extension == ""

    @property
    def fullname(self) -> str:
        if self.parent:
            return rdos_join(self.parent.fullname, self.basename)
        else:
            return self.basename

    @property
    def basename(self) -> str:
        return f"{self.filename}.{self.extension}"

    @property
    def last_access(self) -> t.Optional[date]:
        """
        Last access date
        """
        return rdos_to_date(self.last_access_date)

    @property
    def creation_date(self) -> t.Optional[date]:
        """
        Last modification date
        """
        return rdos_to_date(self.last_modification_date, self.last_modification_time)

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return int(math.ceil(self.size / self.block_size))

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.size

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return self.block_size

    def delete(self) -> bool:
        """
        Delete the directory entry
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def write(self) -> bool:
        """
        Write the directory entry
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def examine(self) -> str:
        if self.is_directory:  # Directory
            file_type = "Directory"
        elif self.is_partition:  # Partition
            file_type = "Partition"
        elif self.is_link:  # Link entry
            file_type = "Link"
        elif self.is_random:  # Random organized file
            file_type = "Random file"
        elif self.is_contiguous:  # Contiguous organized file
            file_type = "Contiguous file"
        else:  # Sequential organized file
            file_type = "Sequential file"
        if self.is_link:
            data: t.Dict[str, t.Any] = {
                "Filename": self.fullname,
                "File type": file_type,
                "Creation date": str(self.creation_date),
                "Target": self.target,
            }
        else:
            data = {
                "Filename": self.fullname,
                "File type": file_type,
                "Creation date": str(self.creation_date),
                "Last access": str(self.last_access),
                "Address": self.addresses,
                "File size": f"{self.get_size()}",
                "Write protected": self.attributes & ATWP != 0,
                "Read protected": self.attributes & ATRP != 0,
                "Immutable attribs": self.attributes & ATCHA != 0,
                "Permanent": self.attributes & ATPER != 0,
                "Link attributes": format_attr(self.link_access_attributes),
            }
        return dump_struct(data) + "\n"

    def open(self, file_mode: t.Optional[str] = None) -> DGDOSDumpFile:
        """
        Open a file
        """
        return DGDOSDumpFile(self, file_mode)

    def __str__(self) -> str:
        attr = format_attr(self.attributes, long=True)
        if self.is_link:
            return f"{self.filename:>10s}.{self.extension:<2s} {attr:<12}  -> {self.target}"
        else:
            uftlkl = format_attr(self.link_access_attributes)
            if uftlkl:
                attr = f"{attr}/{uftlkl}"
            creation_date = self.creation_date.strftime("%m/%d/%y") if self.creation_date else ""
            return (
                # f"{self.filename:>10s}.{self.extension:<2s} "
                f"{self.fullname:<30s} "
                f"{attr:<12} "
                f"{self.get_size():>10d}  "
                f"{creation_date:<8}  "
                # f"{self.addresses[0]:>10}"
            )

    def __repr__(self) -> str:
        return str(self)


class DGDOSDumpFilesystem(AbstractFilesystem):
    """
    Data General DOS/RDOS DUMP

    RDOS System Reference - Pag 125
    https://bitsavers.trailing-edge.com/pdf/dg/software/rdos/093-400027-00_DGDOSDump_SystemReference_Oct83.pdf
    """

    fs_name = "dump"
    fs_description = "Data General DOS/RDOS DUMP"

    f: "AbstractFile"
    pwd: str = "/"  # Current working directory

    def __init__(self, file: "AbstractFile"):
        self.f = file

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        return self.f.read_block(block_number, number_of_blocks)

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        if strict:
            self.f.seek(0)
            if not self.read_byte() == NAME_BLOCK_ID:
                raise OSError(errno.EIO, "Invalid dump file")
        return self

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["DGDOSDumpEntry"]:
        if pattern:
            pattern = pattern.upper()
        if not pattern and expand:
            pattern = "*"
        if pattern and pattern.startswith("/"):
            absolute_path = pattern
        else:
            absolute_path = unix_join(self.pwd, pattern or "")
        if self.isdir(absolute_path):
            if not expand:
                yield self.get_file_entry(absolute_path)
                return
            dirname = pattern
            pattern = "*"
        else:
            dirname, pattern = unix_split(absolute_path)
        if dirname == "/":  # Root directory
            dir_ufd: t.Optional[DGDOSDumpEntry] = None
        else:
            dir_ufd = self.get_file_entry(dirname)  # type: ignore
        for entry in self.read_dir_entries(dir_ufd):
            if filename_match(entry.basename, pattern, wildcard):
                yield entry

    def get_ufd(self, parent: t.Optional[DGDOSDumpEntry], basename: str) -> DGDOSDumpEntry:
        """
        Get User File Descriptor for a file in a directory
        """
        basename = rdos_canonical_filename(basename).rstrip(".")
        for entry in self.read_dir_entries(parent):
            if filename_match(entry.basename.rstrip("."), basename, wildcard=False):
                return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), basename)

    @property
    def entries_list(self) -> t.Iterator["DGDOSDumpEntry"]:
        yield from self.read_dir_entries()

    def get_file_entry(self, fullname: str) -> DGDOSDumpEntry:
        """
        Get the directory entry for a file
        """
        fullname = unix_join(self.pwd, fullname) if not fullname.startswith("/") else fullname
        parts = [x for x in fullname.split("/") if x]
        if not parts:
            parts = []
        entry: t.Optional[DGDOSDumpEntry] = None
        for i, part in enumerate(parts):
            if entry is not None and not (entry.is_directory or entry.is_partition):  # is a directory?
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
            entry = self.get_ufd(entry, part)
        if entry is None:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return entry

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
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,
        length_bytes: t.Optional[int] = None,  # optional length in bytes
    ) -> t.Optional[DGDOSDumpEntry]:
        """
        Create a new file with a given length in number of blocks
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        for x in self.filter_entries_list(pattern, include_all=True, wildcard=True):
            if options.get("brief"):
                # For brief mode, print only the file name
                sys.stdout.write(f"{x.basename}\n")
            elif x.is_link:
                # Print link information
                # filename, target
                sys.stdout.write(f"{x.basename:<13s}             {x.target}\n")
            else:
                # Print file information
                # filename, byte length, attributes, last modification date, last access date
                attr = format_attr(x.attributes)
                uftlkl = format_attr(x.link_access_attributes)
                if uftlkl:
                    attr = f"{attr}/{uftlkl}"
                creation_date = x.creation_date.strftime("%m/%d/%y %H:%M") if x.creation_date else ""
                access_date = x.last_access.strftime("%m/%d/%y") if x.last_access else ""
                sys.stdout.write(
                    f"{x.basename:<13s}{x.get_size():>10d}  {attr:<7} {creation_date:<14} {access_date:<8}\n"
                )
        sys.stdout.write("\n")

    def read_byte(self) -> int:
        """
        Read a byte from the dump file
        """
        buffer = self.f.read(1)
        if len(buffer) == 0:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return ord(buffer)

    def read_word(self) -> int:
        """
        Read a word from the dump file
        """
        buffer = self.f.read(2)
        if len(buffer) < 2:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        return buffer[0] << 8 | buffer[1]

    def read_to_null(self) -> bytes:
        """
        Read from the dump file until a null byte is found
        """
        buffer = bytearray()
        while True:
            byte = self.f.read(1)
            if not byte or ord(byte) == 0:
                break
            buffer.append(ord(byte))
        return bytes(buffer)

    def read_blocks(self) -> t.Iterator[AbstractBlock]:
        self.f.seek(0)
        while True:
            yield AbstractBlock.read(self)

    def read_dir_entries(self, parent: t.Optional[DGDOSDumpEntry] = None) -> t.Iterator[DGDOSDumpEntry]:
        parents = []
        entry: DGDOSDumpEntry = None  # type: ignore
        for block in self.read_blocks():
            if isinstance(block, NameBlock):
                # Name block - create a new entry
                if entry is not None:
                    # Yield the previous entry
                    position = self.f.tell()  # Save current dump file position
                    if entry.is_directory:
                        parents.append(entry)
                    elif entry.is_partition:
                        parents = [entry]
                    if (parent is None and entry.parent is None) or (
                        (entry.parent is not None)
                        and (parent is not None)
                        and (entry.parent.fullname == parent.fullname)
                    ):
                        yield entry
                    self.f.seek(position)  # Restore dump file position
                entry = DGDOSDumpEntry(self, block, parent=parents[-1] if parents else None)
            elif isinstance(block, DataBlock):
                # Data block - save the block address in the entry
                entry.size += block.byte_count
                if entry.block_size < block.byte_count:
                    entry.block_size = block.byte_count
                entry.addresses.append(block.data_position)
            elif isinstance(block, TimeBlock):
                # Time block - set the last access and modification date in the entry
                entry.last_access_date = block.last_access_date
                entry.last_modification_date = block.last_modification_date
                entry.last_modification_time = block.last_modification_time
            elif isinstance(block, LinkDataBlock):
                # Link data block - set the link target in the entry
                dirname = block.dirname.decode("ascii", "ignore").rstrip("\0")
                resfilename = block.resfilename.decode("ascii", "ignore").rstrip("\0")
                if dirname:
                    entry.target = f"{dirname}:{resfilename}"
                else:
                    entry.target = f"{resfilename}"
            elif isinstance(block, LinkAccessAttributesBlock):
                # Link access attributes block - set the link access attributes in the entry
                entry.link_access_attributes = block.attributes
            elif isinstance(block, EndBlock):
                # End block - end of the directory entry/dump file
                if parents:
                    parents.pop()
                else:
                    break
            # elif isinstance(block, EndOfSegmentBlock):
            #     raise TODO
        # Yield the previous entry
        if entry is not None:
            yield entry

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if not arg:
            for entry in self.read_dir_entries():
                if options.get("full") or not entry.is_empty:
                    sys.stdout.write(f"{entry}\n")
        else:
            # Display the file information
            entry = self.get_file_entry(arg)  # type: ignore
            sys.stdout.write(entry.examine())

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def close(self) -> None:
        self.f.close()

    def chdir(self, fullname: str) -> bool:
        """
        Change the current directory
        """
        return False

    def get_pwd(self) -> str:
        """
        Get the current directory
        """
        return ""

    def get_types(self) -> t.List[str]:
        """
        Get the list of the supported file types
        """
        return []

    def __str__(self) -> str:
        return str(self.f)
