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
from datetime import date, datetime, timedelta
from functools import reduce
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from .commons import BLOCK_SIZE, filename_match, hex_dump

__all__ = [
    "UNIXFile",
    "UNIXDirectoryEntry",
    "UNIXFilesystem",
]

READ_FILE_FULL = -1
V6_SUPER_BLOCK = 1  # Superblock
V6_SUPER_BLOCK_FORMAT = '<HHH 100H H 100H B B B 2H'

V1_ALL = 0o100000  # i-node is allocated
V1_DIR = 0o040000  # directory
V1_MOD = 0o020000  # file has been modified (always on)
V1_LRG = 0o010000  # large file

V1_SUID = 0o000040  # set user ID on execution
V1_XOWN = 0o000020  # executable
V1_ROWN = 0o000010  # read, owner
V1_WOWN = 0o000004  # write, owner
V1_ROTH = 0o000002  # read, non-owner
V1_WOTH = 0o000001  # write, non-owner

V6_ALL = 0o100000  # i-node is allocated
V6_BLK = 0o060000  # block device
V6_DIR = 0o040000  # directory
V6_CHR = 0o020000  # character device
V6_LRG = 0o010000  # large file

V6_SUID = 0o4000  # set user ID on execution
V6_SGID = 0o2000  # set group ID on execution
V6_STXT = 0o1000  # sticky bit

V6_ROWN = 0o400  # read by owner
V6_WOWN = 0o200  # write by owner
V6_XOWN = 0o100  # execute by owner
V6_RGRP = 0o040  # read by group
V6_WGRP = 0o020  # write by group
V6_XGRP = 0o010  # execute by group
V6_ROTH = 0o004  # read by other
V6_WOTH = 0o002  # write by other
V6_XOTH = 0o001  # execute by other

V1_INODE_FORMAT = "<HBBH 16s II H"
V1_FILENAME_LEN = 8
V1_INODE_SIZE = 32
V1_NADDR = 8
V1_DIR_FORMAT = f"H{V1_FILENAME_LEN}s"
V1_ROOT_INODE = 41
assert struct.calcsize(V1_INODE_FORMAT) == V1_INODE_SIZE

V6_INODE_FORMAT = "<HBBBBH 16s II"
V6_FILENAME_LEN = 14
V6_INODE_SIZE = 32
V6_NADDR = 8
V6_DIR_FORMAT = f"H{V6_FILENAME_LEN}s"
V6_ROOT_INODE = 1
assert struct.calcsize(V6_INODE_FORMAT) == V6_INODE_SIZE

V7_INODE_FORMAT = "<HHHH HH 40s III"
V7_FILENAME_LEN = 14
V7_INODE_SIZE = 64
V7_NADDR = 13
V7_DIR_FORMAT = f"H{V7_FILENAME_LEN}s"
V7_ROOT_INODE = 2
assert struct.calcsize(V7_INODE_FORMAT) == V7_INODE_SIZE


def unix_canonical_filename(fullname: str, wildcard: bool = False) -> str:
    """
    Generate the canonical unix name
    """
    # TODO
    if fullname:
        fullname = fullname[:V6_FILENAME_LEN]
    return fullname


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


def unix_split(p: str) -> Tuple[str, str]:
    """
    Split a pathname
    """
    i = p.rfind("/") + 1
    head, tail = p[:i], p[i:]
    if head and head != "/" * len(head):
        head = head.rstrip("/")
    return head, tail


def l3tol(data: bytes, n: int) -> List[int]:
    """
    Convert 3-byte integers
    """
    result: List[int] = []
    for i in range(0, n * 3, 3):
        t = (data[i + 1] << 0) + (data[i + 2] << 8) + (data[i + 0] << 16)
        result.append(t)
    return result


V1_PERMS = [
    [(V1_LRG, "l"), (0, "s")],
    [(V1_DIR, "d"), (V1_SUID, "s"), (V1_XOWN, "x"), (0, "-")],
    [(V1_ROWN, "r"), (0, "-")],
    [(V1_WOWN, "w"), (0, "-")],
    [(V1_ROTH, "r"), (0, "-")],
    [(V1_WOTH, "w"), (0, "-")],
]

V6_PERMS = [
    [(V6_BLK, "b"), (V6_DIR, "d"), (V6_CHR, "c"), (0, "-")],
    [(V6_ROWN, "r"), (0, "-")],
    [(V6_WOWN, "w"), (0, "-")],
    [(V6_SUID, "s"), (V6_XOWN, "x"), (0, "-")],
    [(V6_RGRP, "r"), (0, "-")],
    [(V6_WGRP, "w"), (0, "-")],
    [(V6_SGID, "s"), (V6_XGRP, "x"), (0, "-")],
    [(V6_ROTH, "r"), (0, "-")],
    [(V6_WOTH, "w"), (0, "-")],
    [(V6_XOTH, "x"), (0, "-")],
    [(V6_STXT, "t"), (0, " ")],
]


def format_mode(flags: int, version: int) -> str:
    result = []
    if version == 1:
        perms = V1_PERMS
    else:
        perms = V6_PERMS
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
    closed: bool

    def __init__(self, inode: "UNIXInode"):
        self.inode = inode
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
            number_of_blocks = self.inode.length
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.inode.length
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        data = bytearray()
        for i, next_block_number in enumerate(self.inode.blocks()):
            if i >= block_number:
                t = self.inode.fs.read_block(next_block_number)
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
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.inode.size

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

    def close(self) -> None:
        """
        Close the file
        """
        self.closed = True

    def __str__(self) -> str:
        return str(self.inode)


class UNIXInode:

    fs: "UNIXFilesystem"
    inode_num: int  #  inode number
    flags: int  #      flags
    nlinks: int  #     number of links to file
    uid: int  #        user ID of owner
    gid: int  #        group ID of owner
    size: int  #       size
    addr: List[int]  # block numbers or device numbers
    atime: int  #      time of last access
    mtime: int  #      time of last modification

    def __init__(self, fs: "UNIXFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "UNIXFilesystem", inode_num: int, buffer: bytes, position: int = 0) -> "UNIXInode":
        self = UNIXInode(fs)
        self.inode_num = inode_num
        if fs.version == 1:
            (
                self.flags,  #   1 word  flags
                self.nlinks,  #  1 byte  number of links to file
                self.uid,  #     1 byte  user ID of owner
                self.size,  #    1 word  size
                addr,  #         8 words block numbers or device numbers
                self.atime,  #   1 long  time of last access
                self.mtime,  #   1 long  time of last modification
                _,  #             1 word  unused
            ) = struct.unpack_from(V1_INODE_FORMAT, buffer, position)
            self.addr = struct.unpack_from(f"{V1_NADDR}H", addr)
            self.gid = None

        elif fs.version == 6:
            (
                self.flags,  #   1 word  flags
                self.nlinks,  #  1 byte  number of links to file
                self.uid,  #     1 byte  user ID of owner
                self.gid,  #     1 byte  group ID of owner
                sz0,  #          1 byte  high byte of 24-bit size
                sz1,  #          1 word  low word of 24-bit size
                addr,  #         8 words block numbers or device numbers
                self.atime,  #   1 long  time of last access
                self.mtime,  #   1 long  time of last modification
            ) = struct.unpack_from(V6_INODE_FORMAT, buffer, position)
            self.addr = struct.unpack_from(f"{V6_NADDR}H", addr)
            self.size = (sz0 << 16) + sz1  # byte + short

        elif fs.version == 7:
            (
                self.flags,  #    1 word  flags
                self.nlinks,  #   1 word  number of links to file
                self.uid,  #      1 word  user ID of owner
                self.gid,  #      1 word  group ID of owner
                sz0,  #           1 word  high word of size
                sz1,  #           1 word  low word of size
                addr,  #          40 chars disk block addresses
                self.atime,  #    1 long  time of last access
                self.mtime,  #    1 long  time of last modification
                self.mtime,  #    1 long  time created
            ) = struct.unpack_from(V7_INODE_FORMAT, buffer, position)
            self.addr = l3tol(addr, V7_NADDR)
            self.size = (sz0 << 16) + sz1  # byte + short

        else:
            raise ValueError(f"Invalid version {fs.version}")

        return self

    def blocks(self) -> Iterator[int]:
        # if self.size > BIGGEST_NOT_HUGE_SIZE:
        #     raise HugeFileError("huge files not implemented")
        if self.is_large:
            for block_number in self.addr:
                if block_number == 0:
                    break
                indirect_block = self.fs.read_block(block_number)
                for i in range(0, len(indirect_block), 2):
                    n = struct.unpack("H", indirect_block[i : i + 2])[0]
                    if n == 0:
                        return
                    yield n
        else:
            for block_number in self.addr:
                if block_number == 0:
                    break
                yield block_number

    def read_bytes(self) -> bytes:
        data = bytearray()
        for block_number in self.blocks():
            t = self.fs.read_block(block_number)
            data.extend(t)
        return bytes(data)[: self.size]

    @property
    def isdir(self) -> bool:
        if self.fs.version == 1:
            return (self.flags & V1_DIR) == V1_DIR
        else:
            return (self.flags & V6_DIR) == V6_DIR

    @property
    def is_regular_file(self) -> bool:
        return not self.isdir

    @property
    def is_large(self) -> bool:
        if self.fs.version == 1:
            return bool(self.flags & V1_LRG)
        else:
            return bool(self.flags & V6_LRG)

    @property
    def is_allocated(self) -> bool:
        if self.fs.version == 1:
            return bool(self.flags & V1_ALL)
        elif self.fs.version == 6:
            return bool(self.flags & V6_ALL)
        else:
            return bool(self.flags)

    @property
    def length(self) -> int:
        return int(math.ceil(self.size / BLOCK_SIZE))

    def __str__(self) -> str:
        if not self.is_allocated:
            return f"{self.inode_num:>4}# ---"
        mode = format_mode(self.flags, version=self.fs.version)
        if self.fs.version < 4:
            return f"{self.inode_num:>4}# {self.uid:>3}  nlinks: {self.nlinks} size: {self.size} {mode} flags: {self.flags}"
        else:
            return f"{self.inode_num:>4}# {self.uid:>3},{self.gid:<3}  nlinks: {self.nlinks} size: {self.size} {mode} flags: {self.flags}"

    def __repr__(self) -> str:
        return str(self.__dict__)


class UNIXDirectoryEntry(AbstractDirectoryEntry):

    fs: "UNIXFilesystem"
    _inode: Optional["UNIXInode"]
    inode_num: int  # Inode number
    filename: str  # File name
    dirname: str  # Parent directory name

    def __init__(self, fs: "UNIXFilesystem", fullname: str, inode_num: int, inode: Optional["UNIXInode"] = None):
        self.fs = fs
        self.dirname, self.filename = unix_split(fullname)
        self.inode_num = inode_num
        self._inode = inode

    @property
    def inode(self) -> "UNIXInode":
        if self._inode is None:
            self._inode = self.fs.read_inode(self.inode_num)
        return self._inode

    @property
    def is_empty(self) -> bool:
        return not self.inode.is_allocated

    @property
    def fullname(self) -> str:
        return unix_join(self.dirname, self.filename)

    @property
    def basename(self) -> str:
        return self.filename

    @property
    def creation_date(self) -> Optional[date]:
        return datetime.fromtimestamp(self.inode.mtime)

    def delete(self) -> bool:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def __lt__(self, other: "UNIXDirectoryEntry") -> bool:
        return self.filename < other.filename

    def __gt__(self, other: "UNIXDirectoryEntry") -> bool:
        return self.filename > other.filename

    def __str__(self) -> str:
        return f"{self.basename} {self.inode_num}"


class UNIXFilesystem(AbstractFilesystem):
    """
    UNIX Filesystem
    """

    version: int  # UNIX version
    inode_size: int
    pwd: str
    isize: int  # number of blocks devoted to the i-list
    fsize: int  # first block not potentially available for file allocation
    nfree: int
    ninode: int  # number of free i-numbers in the inode array

    def __init__(self, file: "AbstractFile", version: int):
        self.f = file
        self.version = version
        self.pwd = "/"
        if self.version == 1:
            self.inode_size = V1_INODE_SIZE
            self.dir_format = V1_DIR_FORMAT
            self.root_inode = V1_ROOT_INODE
        elif self.version == 6:
            self.inode_size = V6_INODE_SIZE
            self.dir_format = V6_DIR_FORMAT
            self.root_inode = V6_ROOT_INODE
        elif self.version == 7:
            self.inode_size = V7_INODE_SIZE
            self.dir_format = V7_DIR_FORMAT
            self.root_inode = V7_ROOT_INODE
        else:
            raise ValueError(f"Invalid version {self.version}")
        self.read_superblock()

    def read_superblock(self) -> None:
        """Read superblock"""
        if self.version == 6:
            superblock_data = self.read_block(V6_SUPER_BLOCK)
            superblock = struct.unpack_from(V6_SUPER_BLOCK_FORMAT, superblock_data, 0)
            self.isize = superblock[0]
            self.fsize = superblock[1]
            self.nfree = superblock[2]
            self.free = superblock[3:103]
            self.ninode = superblock[103]
            self.inode = superblock[104:204]
            _ = superblock[204]  # flock
            _ = superblock[205]  # ilock
            _ = superblock[206]  # flag to indicate that the super-block has changed and should be written
            # self.time = (superblock[207] << 16)+ superblock[208]
        elif self.version == 7:
            pass  # TODO

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        return self.f.read_block(block_number, number_of_blocks)

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def read_inode(self, inode: int) -> UNIXInode:
        """
        Read inode by number
        """
        self.f.seek(BLOCK_SIZE * 2 + (inode - 1) * self.inode_size)
        data = self.f.read(self.inode_size)
        return UNIXInode.read(self, inode, data)

    def get_inode(self, path: str, inode_num: int = -1) -> Optional["UNIXInode"]:
        """
        Get inode by path
        """
        if inode_num == -1:
            inode_num = self.root_inode
        if path and path[0] == "/":
            return self.get_inode(path.strip("/"))
        inode = self.read_inode(inode_num)
        if not path:
            if inode.is_allocated:
                inode.inode_num = inode_num
                return inode
            return None
        if inode.isdir:
            name, tail = path.split("/", 1) if "/" in path else (path, "")
            for no, nm in self.list_dir(inode):
                if nm != name:
                    continue
                return self.get_inode(tail, no)
        return None

    def list_dir(self, inode: UNIXInode) -> List[Tuple[int, str]]:
        if not inode.isdir:
            return []
        files = []
        data = inode.read_bytes()
        for i in range(0, len(data), struct.calcsize(self.dir_format)):
            inum, name = struct.unpack_from(self.dir_format, data, i)
            if inum > 0:
                name = name.decode("ascii", errors="ignore").rstrip("\x00")
                files.append((inum, name))
        return files

    def read_dir_entries(self, dirname: str) -> Iterator["UNIXDirectoryEntry"]:
        inode = self.get_inode(dirname)
        if inode:
            for inode_num, filename in self.list_dir(inode):
                fullname = unix_join(dirname, filename)
                yield UNIXDirectoryEntry(self, fullname, inode_num)

    def filter_entries_list(
        self,
        pattern: Optional[str],
        include_all: bool = False,
        wildcard: bool = True,
    ) -> Iterator["UNIXDirectoryEntry"]:
        if not pattern:
            yield from self.read_dir_entries(self.pwd)
        else:
            if not pattern.startswith("/"):
                dirname = self.pwd
            elif self.isdir(pattern):
                dirname = pattern
                pattern = "*"
            else:
                dirname, pattern = unix_split(pattern)
            for entry in self.read_dir_entries(dirname):
                if filename_match(entry.basename, pattern, wildcard):
                    yield entry

    @property
    def entries_list(self) -> Iterator["UNIXDirectoryEntry"]:
        yield from self.read_dir_entries(self.pwd)

    def get_file_entry(self, fullname: str) -> Optional[UNIXDirectoryEntry]:
        inode = self.get_inode(fullname)
        if not inode:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return UNIXDirectoryEntry(self, fullname, inode.inode_num, inode)

    def open_file(self, fullname: str) -> UNIXFile:
        """
        Open a file
        """
        entry = self.get_file_entry(fullname)
        if not entry:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return UNIXFile(entry.inode)

    def read_bytes(self, fullname: str) -> bytes:
        """
        Get the content of a file
        """
        f = self.open_file(fullname)
        try:
            data = f.read_block(0, READ_FILE_FULL)
            return data[: f.inode.size]
        finally:
            f.close()

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: Optional[date] = None,
        file_type: Optional[str] = None,
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def create_file(
        self,
        fullname: str,
        length: int,  # length in blocks
        creation_date: Optional[date] = None,  # optional creation date
        file_type: Optional[str] = None,
    ) -> Optional[UNIXDirectoryEntry]:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def isdir(self, fullname: str) -> bool:
        inode = self.get_inode(fullname)
        return inode is not None and inode.isdir

    def exists(self, fullname: str) -> bool:
        """
        Check if the given path exists
        """
        return self.get_file_entry(fullname) is not None

    def read_uids(self) -> Dict[int, str]:
        """
        Read the uid -> name map
        """
        result: Dict[int, str] = {}
        filename = "/etc/uids" if self.version < 3 else "/etc/passwd"
        try:
            for line in self.read_bytes(filename).decode("ascii", errors="ignore").split("\n"):
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

    def dir(self, volume_id: str, pattern: Optional[str], options: Dict[str, bool]) -> None:
        entries = sorted(self.filter_entries_list(pattern, include_all=True, wildcard=True))
        if not entries:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), pattern)
        uids = self.read_uids()
        if not options.get("brief"):
            blocks = reduce(lambda x, y: x + y, [x.inode.length for x in entries])
            if self.version < 3:
                sys.stdout.write(f"total {blocks:>4}\n")
            else:
                sys.stdout.write(f"blocks = {blocks}\n")
        for x in entries:
            if not options.get("full") and x.basename.startswith("."):
                pass
            elif options.get("brief"):
                # Lists only file names
                sys.stdout.write(f"{x.basename}\n")
            else:
                mode = format_mode(x.inode.flags, self.version)
                time = format_time(x.inode.mtime)
                uid = uids.get(x.inode.uid, str(x.inode.uid))
                if self.version < 3:
                    sys.stdout.write(
                        f"{x.inode_num:>3} {mode} {x.inode.nlinks:>2} {uid:<6} {x.inode.size:>6} {time} {x.basename}\n"
                    )
                else:
                    sys.stdout.write(
                        f"{x.inode_num:>5} {mode}{x.inode.nlinks:>2} {uid:<6} {x.inode.size:>6} {time} {x.basename}\n"
                    )

    def dump(self, name_or_block: str) -> None:
        if name_or_block.isnumeric():
            data = self.read_block(int(name_or_block))
        else:
            data = self.read_bytes(name_or_block)
        hex_dump(data)

    def examine(self, name_or_block: Optional[str]) -> None:
        def dump_struct(d: Dict[str, Any]) -> str:
            result: List[str] = []
            for k, v in d.items():
                if type(v) in (int, str, bytes, list):
                    if len(k) < 6:
                        label = k.upper() + ":"
                    else:
                        label = k.replace("_", " ").title() + ":"
                    result.append(f"{label:20s}{v}")
            return "\n".join(result)

        if name_or_block:
            self.dump(name_or_block)
        else:
            sys.stdout.write(dump_struct(self.__dict__))
            sys.stdout.write("\n")
            self.isize = 10
            for i in range(1, self.isize * 32 + 1):
                inode = self.read_inode(i)
                sys.stdout.write(f"{inode}\n")

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def close(self) -> None:
        self.f.close()

    def chdir(self, fullname: str) -> bool:
        """
        Change the current directory
        """
        if not fullname.startswith("/"):
            fullname = unix_join(self.pwd, fullname)
        fullname = os.path.normpath(fullname)
        if self.isdir(fullname):
            self.pwd = fullname
            return True
        else:
            return False

    def get_pwd(self) -> str:
        """
        Get the current directory
        """
        return self.pwd
