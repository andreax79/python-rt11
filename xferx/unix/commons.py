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
import math
import os
import struct
import sys
import typing as t
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from functools import reduce

from ..abstract import AbstractBlockFilesystem, XFERXPath, AbstractFile
from ..commons import (
    BLOCK_SIZE,
    READ_FILE_FULL,
    dump_struct,
    filename_match,
    swap_words,
)
from ..device.abstract import AbstractDevice

__all__ = [
    "Bitmap",
    "Dirent",
    "InodeBitmap",
    "UNIXDirectory",
    "UNIXDirectoryEntry",
    "UNIXFile",
    "UNIXFilesystem",
]

# def unix_canonical_filename(fullname: str, wildcard: bool = False) -> str:
#     """
#     Generate the canonical unix name
#     """
#     # TODO
#     if fullname:
#         fullname = fullname[:V4_FILENAME_LEN]
#     return fullname


def unix_join(a: str, *p: str) -> str:
    """
    Join two or more pathname components
    """
    path = a
    if not p:
        path[:0] + "/"
    for b in p:
        if b.startswith("/"):
            path = b
        elif not path or path.endswith("/"):
            path += b
        else:
            path += "/" + b
    return path


def unix_split(p: str) -> t.Tuple[str, str]:
    """
    Split a pathname
    """
    i = p.rfind("/") + 1
    head, tail = p[:i], p[i:]
    if head and head != "/" * len(head):
        head = head.rstrip("/")
    return head, tail


def l3tol(data: bytes, n: int) -> t.List[int]:
    """
    Convert 3-byte integers
    """
    result: t.List[int] = []
    for i in range(0, n * 3, 3):
        tmp = (data[i + 1] << 0) + (data[i + 2] << 8) + (data[i + 0] << 16)
        result.append(tmp)
    return result


def iterate_words(data: bytes) -> t.Iterator[int]:
    """
    Iterate over words in a byte array
    """
    for i in range(0, len(data), 2):
        yield struct.unpack("H", data[i : i + 2])[0]


def iterate_long(data: bytes) -> t.Iterator[int]:
    """
    Iterate over longs in a byte array
    """
    for i in range(0, len(data), 4):
        yield swap_words(struct.unpack("I", data[i : i + 4])[0])


def format_mode(flags: int, version: int, perms: t.List[t.List[t.Tuple[int, str]]]) -> str:
    result = []

    for column in perms:
        ch = [ch for flag, ch in column if (flags & flag) == flag]
        if ch:
            result.append(ch[0])
    return "".join(result)


def format_time(t: int) -> str:
    mod_time = datetime.fromtimestamp(t)
    six_months_ago = datetime.now() - timedelta(days=6 * 30)
    if mod_time > six_months_ago:
        return mod_time.strftime("%b %d %H:%M")
    else:
        return mod_time.strftime("%b %d %Y ")


class UNIXFile(AbstractFile):
    inode: "UNIXInode"

    def __init__(self, inode: "UNIXInode"):
        self.inode = inode

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = self.inode.get_length()
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.inode.get_length()
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        data = bytearray()
        # Get the blocks to be read
        blocks = list(self.inode.blocks())[block_number : block_number + number_of_blocks]
        # Read the blocks
        for disk_block_number in blocks:
            tmp = self.inode.fs.read_block(disk_block_number)
            data.extend(tmp)
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
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.inode.get_length()
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        # Get the blocks to be written
        blocks = list(self.inode.blocks())[block_number : block_number + number_of_blocks]
        # Write the blocks
        for i, disk_block_number in enumerate(blocks):
            data = buffer[i * BLOCK_SIZE : (i + 1) * BLOCK_SIZE]
            self.inode.fs.write_block(data, disk_block_number)

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.inode.get_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return self.inode.get_block_size()

    def __str__(self) -> str:
        return str(self.inode)


class UNIXInode(ABC):
    fs: "UNIXFilesystem"
    inode_num: int  #       inode number
    flags: int  #           flags
    nlinks: int  #          number of links to file
    uid: int  #             user ID of owner
    gid: t.Optional[int]  # group ID of owner
    size: int  #            size
    addr: t.List[int]  #    block numbers or device numbers
    atime: int = 0  #       time of last access
    mtime: int = 0  #       time of last modification
    ctime: int = 0  #       time of last change to the inode

    def __init__(self, fs: "UNIXFilesystem"):
        self.fs = fs

    @classmethod
    @abstractmethod
    def read(cls, fs: "UNIXFilesystem", inode_num: int, buffer: bytes, position: int = 0) -> "UNIXInode":
        pass

    def write(self) -> None:
        """
        Write inode
        """
        self.fs.dev.f.seek(BLOCK_SIZE * 2 + (self.inode_num - 1) * self.fs.inode_size)
        buffer = bytearray(self.fs.inode_size)
        self.write_buffer(buffer)
        self.fs.dev.f.write(buffer)
        self.fs.dev.f.flush()

    def write_buffer(self, buffer: bytearray, position: int = 0) -> None:
        """
        Write the inode to the buffer
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    @abstractmethod
    def blocks(self) -> t.Iterator[int]:
        pass

    @abstractmethod
    def is_dir(self) -> bool:
        pass

    @abstractmethod
    def is_file(self) -> bool:
        pass

    @abstractmethod
    def is_allocated(self) -> bool:
        pass

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return int(math.ceil(self.get_size() / self.get_block_size()))

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

    def __repr__(self) -> str:
        return str(self.__dict__)


class UNIXDirectoryEntry(XFERXPath):
    fs: "UNIXFilesystem"
    _inode: t.Optional["UNIXInode"]
    inode_num: int  # Inode number
    filename: str  # File name
    dirname: str  # Parent directory name

    def __init__(self, fs: "UNIXFilesystem", fullname: str, inode_num: int, inode: t.Optional["UNIXInode"] = None):
        self.fs = fs
        self.dirname, self.filename = unix_split(fullname)
        self.inode_num = inode_num
        self._inode = inode

    @classmethod
    def link(
        cls,
        fs: "UNIXFilesystem",
        parent: "UNIXDirectoryEntry",
        filename: str,
        inode: "UNIXInode",
    ) -> "UNIXDirectoryEntry":
        """
        Creates a new hard link an existing inode
        """
        self = cls(fs, unix_join(parent.fullname, filename), inode.inode_num, inode)
        # Create a directory entry for the new file
        # parent = self.fs.get_file_entry(self.dirname)
        directory = self.fs.directory_class.read(fs, parent.inode)
        directory.entries.append(self.dirent())
        directory.write()
        # Increment the parent link count
        self.inode.nlinks += 1
        inode.write()
        # Increment the child link count
        inode.nlinks += 1
        inode.write()
        return self

    @property
    def inode(self) -> "UNIXInode":
        if self._inode is None:
            self._inode = self.fs.read_inode(self.inode_num)
        return self._inode

    def is_dir(self) -> bool:
        """Is a directory"""
        return self.inode.is_dir()

    def is_mount(self) -> bool:
        """Is root directory"""
        return self.inode_num == self.fs.root_inode

    def is_empty(self) -> bool:
        return not self.inode.is_allocated()

    @property
    def fullname(self) -> str:
        return unix_join(self.dirname, self.filename)

    @property
    def name(self) -> str:
        return self.filename

    @property
    def parent(self) -> "UNIXDirectoryEntry":
        """The logical parent"""
        return self.fs.get_file_entry(self.dirname)

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """Get the length in blocks"""
        return self.inode.get_length()

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """Get file size in bytes"""
        return self.inode.get_size()

    def get_block_size(self) -> int:
        """Get file block size in bytes"""
        return self.inode.get_block_size()

    @property
    def creation_date(self) -> t.Optional[date]:
        """
        Creation date
        """
        return datetime.fromtimestamp(self.inode.mtime)

    @property
    def last_access(self) -> t.Optional[date]:
        """
        Last access date
        """
        return datetime.fromtimestamp(self.inode.atime)

    @property
    def last_mod_date(self) -> t.Optional[date]:
        """
        Last modification date
        """
        return datetime.fromtimestamp(self.inode.mtime)

    @property
    def unix_uid(self) -> int:
        """
        User ID of owner
        """
        return self.inode.uid

    @property
    def unix_gid(self) -> t.Optional[int]:
        """
        Group ID of owner
        """
        return self.inode.gid

    @property
    def unix_flags(self) -> int:
        """
        Unix flags
        """
        return self.inode.flags

    def dirent(self) -> "Dirent":
        """
        Return a Dirent object for this entry
        """
        return Dirent(self.inode_num, self.filename)

    def write(self) -> bool:
        """
        Write the directory entry
        """
        parent = self.fs.get_file_entry(self.dirname)
        directory = self.fs.directory_class.read(self.fs, parent.inode)
        for i, entry in enumerate(directory.entries):
            if entry.inode_num == self.inode_num:
                # Update the existing entry
                directory.entries[i] = self.dirent()
                directory.write()
                return True
        return False

    def unlink(self) -> bool:
        """
        Unlink the directory entry
        Does not delete the inode, just removes the entry from the directory
        """
        # Delete the directory entry
        parent = self.fs.get_file_entry(self.dirname)
        directory = self.fs.directory_class.read(self.fs, parent.inode)
        tmp = list(directory.entries)
        directory.entries = [x for x in directory.entries if x.inode_num != self.inode_num]
        if len(directory.entries) == len(tmp):
            # Entry not found
            return False
        directory.write()
        # Update the directory link count
        if parent.inode.nlinks > 1:  # never set the parent link to 0
            parent.inode.nlinks = parent.inode.nlinks - 1
            parent.inode.write()
        # Update the link count
        if self.inode.nlinks > 0:
            self.inode.nlinks = self.inode.nlinks - 1
        self.inode.write()
        return True

    def delete(self) -> bool:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> UNIXFile:
        """
        Open a file
        """
        return UNIXFile(self.inode)

    def __lt__(self, other: "UNIXDirectoryEntry") -> bool:
        return self.filename < other.filename

    def __gt__(self, other: "UNIXDirectoryEntry") -> bool:
        return self.filename > other.filename

    def __str__(self) -> str:
        return f"{self.inode_num:>5} {self.name}"


class Dirent:
    """
    Directory entry structure
    """

    inode_num: int  # Inode number
    filename: str  # File name

    def __init__(self, inode_num: int, filename: str):
        self.inode_num = inode_num
        self.filename = filename

    def __str__(self) -> str:
        return f"{self.inode_num:>5} {self.filename}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Dirent):
            return False
        return self.__dict__ == other.__dict__


class UNIXDirectory:
    """
    UNIX Directory
    """

    fs: "UNIXFilesystem"
    inode: "UNIXInode"  # Inode of the directory
    entries: t.List["Dirent"]  # List of directory entries

    def __init__(self, fs: "UNIXFilesystem", inode: "UNIXInode"):
        self.fs = fs
        self.inode = inode

    @classmethod
    def read(cls, fs: "UNIXFilesystem", inode: "UNIXInode") -> "UNIXDirectory":
        """
        Read the directory
        """
        self = UNIXDirectory(fs, inode)
        if self.inode.is_dir():
            self.entries = []
            with UNIXFile(self.inode) as f:
                try:
                    while True:
                        data = f.read(struct.calcsize(self.fs.dir_format))
                        inode_num, name = struct.unpack_from(self.fs.dir_format, data)
                        name_ascii = name.decode("ascii", errors="ignore").rstrip("\x00")
                        self.entries.append(Dirent(inode_num, name_ascii))
                except IOError:
                    pass
        return self

    def write(self) -> None:
        """
        Write the directory
        """
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))


class UNIXFilesystem(AbstractBlockFilesystem):
    """
    UNIX Filesystem
    """

    version: int  # UNIX version
    inode_size: int  #
    dir_format: str
    root_inode: int  # Root inode number
    unix_inode_class: t.Type["UNIXInode"]
    directory_class: t.Type["UNIXDirectory"] = UNIXDirectory
    pwd: str
    inode_list_blocks: int  # number of blocks devoted to the i-list
    volume_size: int  # size in blocks of entire volume
    free_blocks_in_list: int  # number of free blocks in the free list
    free_inodes_in_list: int  # number of free i-numbers in the inode array
    inodes: int = 0  # number of inodes
    first_inode: int = 1  # first inode number
    perms: t.List[t.List[t.Tuple[int, str]]]  # permissions

    @classmethod
    @abstractmethod
    def mount(
        cls, file_or_dev: t.Union["AbstractFile", "AbstractDevice"], **kwargs: t.Union[bool, str]
    ) -> "UNIXFilesystem":
        """
        Mount the filesystem from a file or device
        """
        pass

    def read_inode(self, inode_num: int) -> UNIXInode:
        """
        Read inode by number
        """
        self.dev.f.seek(BLOCK_SIZE * 2 + (inode_num - 1) * self.inode_size)
        data = self.dev.f.read(self.inode_size)
        return self.unix_inode_class.read(self, inode_num, data)

    def get_inode(self, path: str) -> t.Optional["UNIXInode"]:
        """
        Get inode by path
        """
        path = unix_join(self.pwd, path) if not path.startswith("/") else path
        parts = path.split("/")
        inode_num = self.root_inode

        while True:
            # Read inode
            inode = self.read_inode(inode_num)
            if not parts:
                # No more parts, inode found
                return inode if inode.is_allocated() else None
            elif not inode.is_dir():
                # More parts and not a directory, not found
                return None
            # Get next part
            filename = parts.pop(0)
            if filename:
                # Search for the name in the directory
                found = False
                for dirent in self.list_dir(inode):
                    if dirent.inode_num > 0 and dirent.filename == filename:
                        inode_num = dirent.inode_num
                        found = True
                        break
                if not found:
                    return None

    def get_root(self) -> "UNIXDirectoryEntry":
        """
        Get the root directory entry
        """
        inode = self.read_inode(self.root_inode)
        return UNIXDirectoryEntry(self, "/", self.root_inode, inode)

    def list_dir(self, inode: UNIXInode) -> t.List[Dirent]:
        directory = self.directory_class.read(self, inode)
        return directory.entries

    def read_dir_entries(self, dirname: str) -> t.Iterator["UNIXDirectoryEntry"]:
        inode = self.get_inode(dirname)
        if inode:
            for dirent in self.list_dir(inode):
                if dirent.inode_num > 0:
                    fullname = unix_join(dirname, dirent.filename)
                    yield UNIXDirectoryEntry(self, fullname, dirent.inode_num)

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["UNIXDirectoryEntry"]:
        if not pattern and expand:
            pattern = "*"
        if pattern and pattern.startswith("/"):
            absolute_path = pattern
        else:
            absolute_path = unix_join(self.pwd, pattern or "")
        if self.is_dir(absolute_path):
            if not expand:
                yield self.get_file_entry(absolute_path)  # type: ignore
                return
            dirname = pattern
            pattern = "*"
        else:
            dirname, pattern = unix_split(absolute_path)
        for entry in self.read_dir_entries(dirname):  # type: ignore
            if filename_match(entry.name, pattern, wildcard):
                yield entry

    @property
    def entries_list(self) -> t.Iterator["UNIXDirectoryEntry"]:
        yield from self.read_dir_entries(self.pwd)

    def get_file_entry(self, fullname: str) -> UNIXDirectoryEntry:
        inode = self.get_inode(fullname)
        if not inode:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return UNIXDirectoryEntry(self, fullname, inode.inode_num, inode)

    def create_file(
        self,
        fullname: str,
        size: int,  # Size in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> UNIXDirectoryEntry:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def read_uids(self) -> t.Dict[int, str]:
        """
        Read the uid -> name map
        """
        result: t.Dict[int, str] = {}
        filename = "/etc/uids" if self.version < 3 else "/etc/passwd"
        try:
            for line in self.read_text(filename).split("\n"):
                if self.version < 3:
                    try:
                        name, uid = line.split(":", 1)
                        result[int(uid)] = name
                    except Exception:
                        pass
                else:
                    try:
                        name, _, uid, _ = line.split(":", 3)
                        result[int(uid)] = name
                    except Exception:
                        pass
        except Exception:
            pass
        return result

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List directory entries
        """
        entries = sorted(self.filter_entries_list(pattern, include_all=True, wildcard=True))
        if not entries:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), pattern)
        uids = self.read_uids()
        blocks = reduce(lambda x, y: x + y, [x.inode.get_length() for x in entries])
        if self.version < 3:
            sys.stdout.write(f"total {blocks:>4}\n")
        else:
            sys.stdout.write(f"blocks = {blocks}\n")

        for x in entries:
            if not options.get("full") and x.name.startswith("."):
                pass
            mode = format_mode(x.inode.flags, self.version, self.perms)
            time = format_time(x.inode.mtime)
            uid = uids.get(x.inode.uid, str(x.inode.uid))
            if self.version < 3:
                sys.stdout.write(
                    f"{x.inode_num:>3} {mode} {x.inode.nlinks:>2} {uid:<6} {x.inode.size:>6} {time} {x.name}\n"
                )
            else:
                sys.stdout.write(
                    f"{x.inode_num:>5} {mode}{x.inode.nlinks:>2} {uid:<6}{x.inode.size:>7} {time} {x.name}\n"
                )

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if arg:
            if arg.isnumeric():
                # Dump the inode by number
                inode_num = int(arg)
                inode: t.Optional[UNIXInode] = self.read_inode(inode_num)
            else:
                # Dump the inode by path
                inode = self.get_inode(arg)
            if inode:
                if hasattr(inode, "examine"):
                    sys.stdout.write(inode.examine())  # type: ignore
                else:
                    sys.stdout.write(dump_struct(inode.__dict__, newline=True))
                if inode.is_dir():
                    # Dump the directory entries
                    sys.stdout.write("Directory entries:\n")
                    for dirent in self.list_dir(inode):
                        if dirent.inode_num > 0:
                            child_inode: t.Optional[UNIXInode] = self.read_inode(dirent.inode_num)
                            sys.stdout.write(f"{child_inode} {dirent.filename}\n")
        else:
            # Dump the entire filesystem
            sys.stdout.write(dump_struct(self.__dict__, newline=True))
            for i in range(1, self.inodes + 1):
                inode = self.read_inode(i)
                sys.stdout.write(f"{inode}\n")

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        """
        Change the current directory
        """
        if entry is None:
            self.pwd = "/"
        else:
            assert isinstance(entry, UNIXDirectoryEntry)
            self.pwd = entry.fullname

    def get_pwd(self) -> str:
        """
        Get the current directory
        """
        return self.pwd


class Bitmap:
    # fs: "ProDOSFilesystem"
    bitmaps: t.List[int]
    # bitmap_blocks: int  # Number of bitmap blocks

    # def __init__(self, fs: "ProDOSFilesystem"):
    #     self.fs = fs
    #
    # @classmethod
    # def read(cls, fs: "ProDOSFilesystem") -> "ProDOSBitmap":
    #     """
    #     Read the bitmap blocks
    #     """
    #     self = ProDOSBitmap(fs)
    #     self.bitmap_blocks = cls.calculate_bitmap_size(fs)
    #     # Read the bitmap blocks
    #     self.bitmaps = []
    #     for block_number in range(fs.bit_map_pointer, fs.bit_map_pointer + self.bitmap_blocks):
    #         buffer = fs.read_block(block_number)
    #         if not buffer:
    #             raise OSError(errno.EIO, os.strerror(errno.EIO))
    #         self.bitmaps += list(buffer)
    #     return self
    #
    # @classmethod
    # def create(cls, fs: "ProDOSFilesystem") -> "ProDOSBitmap":
    #     """
    #     Create the bitmap blocks
    #     """
    #     self = ProDOSBitmap(fs)
    #     self.bitmap_blocks = cls.calculate_bitmap_size(fs)
    #     self.bitmaps = [0] * BLOCK_SIZE * self.bitmap_blocks
    #     # Mark the bitmap blocks of the volume as free
    #     for i in range(0, int(math.ceil(self.fs.get_size() / BLOCK_SIZE))):
    #         self.set_free(i)
    #     # Mark the first blocks as used
    #     for i in range(0, VOLUME_DIRECTORY_BLOCK + DEFAULT_DIRECTORY_BLOCKS + 1):
    #         self.set_used(i)
    #     return self

    # @classmethod
    # def calculate_bitmap_size(cls, fs: "ProDOSFilesystem") -> int:
    #     """
    #     Calculate the number of blocks in the bitmap
    #     """
    #     bitmap_bytes = fs.total_blocks // 8
    #     if fs.total_blocks % 8 > 0:
    #         bitmap_bytes += 1
    #     bitmap_blocks = bitmap_bytes // BLOCK_SIZE
    #     if bitmap_bytes % BLOCK_SIZE > 0:
    #         bitmap_blocks += 1
    #     return bitmap_blocks

    # def write(self) -> None:
    #     """
    #     Write the bitmap blocks
    #     """
    #     for i in range(0, self.bitmap_blocks):
    #         buffer = bytes(self.bitmaps[i * BLOCK_SIZE : (i + 1) * BLOCK_SIZE])
    #         self.fs.write_block(buffer, self.fs.bit_map_pointer + i)

    @property
    def total_bits(self) -> int:
        """
        Return the bitmap length in bit
        """
        return len(self.bitmaps) * 8

    def is_free(self, block_number: int) -> bool:
        """
        Check if the block is free
        """
        int_index = block_number // 8
        bit_position = 7 - (block_number % 8)
        bit_value = self.bitmaps[int_index]
        return (bit_value & (1 << bit_position)) != 0

    def set_free(self, block_number: int) -> None:
        """
        Mark the block as free
        """
        int_index = block_number // 8
        bit_position = 7 - (block_number % 8)
        self.bitmaps[int_index] |= 1 << bit_position

    def set_used(self, block_number: int) -> None:
        """
        Mark the block as used
        """
        int_index = block_number // 8
        bit_position = 7 - (block_number % 8)
        self.bitmaps[int_index] &= ~(1 << bit_position)

    def allocate(self, size: int) -> t.List[int]:
        """
        Allocate blocks
        """
        blocks = []
        for block in range(0, self.total_bits):
            if self.is_free(block):
                self.set_used(block)
                blocks.append(block)
            if len(blocks) == size:
                break
        if len(blocks) < size:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        return blocks

    def used(self) -> int:
        """
        Count the number of used blocks
        """
        return self.total_bits - self.free()

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        free = 0
        for block in self.bitmaps:
            free += block.bit_count()
        return free


class InodeBitmap:
    bitmaps: t.List[int]

    @property
    def total_bits(self) -> int:
        """
        Return the bitmap length in bit
        """
        return len(self.bitmaps) * 8

    def is_free(self, block_number: int) -> bool:
        """
        Check if the inode is free
        """
        int_index = block_number // 8
        bit_position = 7 - (block_number % 8)
        bit_value = self.bitmaps[int_index]
        return (bit_value & (1 << bit_position)) == 0

    def set_free(self, block_number: int) -> None:
        """
        Mark the inode as free
        """
        int_index = block_number // 8
        bit_position = 7 - (block_number % 8)
        self.bitmaps[int_index] &= ~(1 << bit_position)

    def set_used(self, block_number: int) -> None:
        """
        Mark the inode as used
        """
        int_index = block_number // 8
        bit_position = 7 - (block_number % 8)
        self.bitmaps[int_index] |= 1 << bit_position

    def allocate(self) -> int:
        """
        Allocate an inode
        """
        for block in range(0, self.total_bits):
            if self.is_free(block):
                self.set_used(block)
                return block
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def free(self) -> int:
        """
        Count the number of free inodes
        """
        return self.total_bits - self.used()

    def used(self) -> int:
        """
        Count the number of used inodes
        """
        free = 0
        for block in self.bitmaps:
            free += block.bit_count()
        return free
