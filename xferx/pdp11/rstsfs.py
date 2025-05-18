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
import os
import struct
import sys
import typing as t
from datetime import date, datetime, timedelta

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..block import BlockDevice
from ..cache import BlockCache
from ..commons import BLOCK_SIZE, READ_FILE_FULL, dump_struct, filename_match
from ..uic import ANY_GROUP, ANY_USER, UIC
from .rad50 import asc2rad, asc_to_rad50_word, rad2asc, rad50_word_to_asc

__all__ = [
    "RSTSFile",
    "RSTSFilesystem",
]

# RSTS/E Monitor Internals, Michael Mayfield
# http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf
# RSTS/E V8.0 Internals Manual
# https://bitsavers.org/pdf/dec/pdp11/rsts_e/V08/AA-CL35A-TE_8.0intern_Sep84.pdf

# 3-level directory hierarchy:
# Master File Directory (MFD)
# Group File Directories (GFDs)
# User File Directories (UFDs)

BOOT_BLOCK = 0  # Boot block
LABEL_BLOCK_OFFSET = 0  # Label block offset from MFD/GFD (RDS1.1 and later)
GFD_POINTER_BLOCK_OFFSET = 1  # GFD pointer block offset from MFD (RDS1.1 and later)
UFD_POINTER_BLOCK_OFFSET = 1  # UFD pointer block offset from GFD (RDS1.1 and later)
GFD_NAME_ENTRY_BLOCK_OFFSET = 2  # GFD name entries pointer block (RDS1.1 and later)
DISK_PACK_LABEL_DCN = 1  # Disk pack label DCN
BLOCKETTE_FORMAT = '<HHHHHHHH'  # 8 words blockette
BLOCKETTE_LEN = struct.calcsize(BLOCKETTE_FORMAT)
DISK_PACK_LABEL_FORMAT = BLOCKETTE_FORMAT
MFD_ENTRY_FORMAT = '<HHHHBBHHH'
MFD_ENTRY_LEN = struct.calcsize(MFD_ENTRY_FORMAT)
UFD_ENTRY_FORMAT = '<HHHHBBHHH'
UFD_ENTRY_LEN = struct.calcsize(UFD_ENTRY_FORMAT)
assert MFD_ENTRY_LEN == BLOCKETTE_LEN == UFD_ENTRY_LEN
GFD_POINTER_BLOCK_FORMAT = '<255H'
UFD_POINTER_BLOCK_FORMAT = '<255H'
CLUSTER_MAP_POS = 0o760
US_UFD = 1 << 6  # USTAT bit 6 - 1 for MFD Name Entry
RDS1_FLAGS = 0o20000  # RDS1.1 or RDS1.2
RDS0_PLVL = 0  # RDS 0 - V7.x and before
RDS11_PLVL = 257  # RDS 1.1 - V8
RDS12_PLVL = 258  # RDS 1.2 - V9.0 and beyond
SAT_FILENAME = "[0,1]SATT.SYS'"  # Storage Allocation Table filename
BAD_BLOCK_FILENAME = "[0,1]BADB.SYS'"  # Bad Block filename


class PPN(UIC):
    """
    Programmer Project Number
    The format of PPN if [ggg,uuu] there ggg and uuu are decimal digits
    The value on the left of the comma is represents the project number,
    the value on the right represents the programmer's number within the project.
    """

    @classmethod
    def from_str(cls, code_str: str) -> "PPN":
        code_str = code_str.split("[")[1].split("]")[0]
        project_str, user_str = code_str.split(",")
        if project_str == "*":
            project = ANY_GROUP
        else:
            project = int(project_str) & 0xFF
        if user_str == "*":
            user = ANY_USER
        else:
            user = int(user_str) & 0xFF
        return cls(project, user)

    @classmethod
    def from_word(cls, code_int: int) -> "PPN":
        project = code_int >> 8
        user = code_int & 0xFF
        return cls(project, user)

    def to_wide_str(self) -> str:
        g = f"{self.group}" if self.group != ANY_GROUP else "*"
        u = f"{self.user}" if self.user != ANY_USER else "*"
        return f"[{g:>3},{u:<3}]"

    def __str__(self) -> str:
        g = f"{self.group}" if self.group != ANY_GROUP else "*"
        u = f"{self.user}" if self.user != ANY_USER else "*"
        return f"[{g},{u}]"

    def __repr__(self) -> str:
        return str(self)


DEFAULT_PPN = PPN.from_str("[1,1]")
ACCOUNT_1_1_PPN = PPN.from_str("[1,1]")


def rsts_to_date(udc: int, utc: int) -> datetime:
    """
    Translate RSTS/E date and time to Python datetime

    udc is (year-1970) * 100 + (day of the year)
    utc is the number of minutes before midnight
    """
    year = (udc // 1000) + 1970
    day_of_year = udc % 1000
    date = datetime(year, 1, 1) + timedelta(days=day_of_year - 1)
    time_of_day = timedelta(minutes=1440 - utc)
    full_datetime = date + time_of_day
    return full_datetime


def rsts_canonical_filename(fullname: t.Optional[str], wildcard: bool = False) -> str:
    """
    Generate the canonical RSTS/E name
    """
    fullname = (fullname or "").upper()
    try:
        filename, extension = fullname.split(".", 1)
    except Exception:
        filename = fullname
        extension = "*" if wildcard else ""
    filename = rad2asc(asc2rad(filename[0:3])) + rad2asc(asc2rad(filename[3:6]))
    extension = rad2asc(asc2rad(extension))
    return f"{filename}.{extension}"


def rsts_canonical_fullname(fullname: str, wildcard: bool = False) -> str:
    try:
        if "[" in fullname:
            ppn: t.Optional[PPN] = PPN.from_str(fullname)
            fullname = fullname.split("]", 1)[1]
        else:
            ppn = None
    except Exception:
        ppn = None
    if fullname:
        fullname = rsts_canonical_filename(fullname, wildcard=wildcard)
    return f"{ppn or ''}{fullname}"


def rsts_split_fullname(ppn: PPN, fullname: t.Optional[str], wildcard: bool = True) -> t.Tuple[PPN, t.Optional[str]]:
    if fullname:
        if "[" in fullname:
            try:
                ppn = PPN.from_str(fullname)
                fullname = fullname.split("]", 1)[1]
            except Exception:
                return ppn, fullname
        if fullname:
            fullname = rsts_canonical_filename(fullname, wildcard=wildcard)
    return ppn, fullname


class RTFSBlockCache(BlockCache):

    def __init__(self, fs: "RSTSFilesystem"):
        super().__init__(fs.f)
        self.fs = fs

    def read_block(self, block_number: int = 0, dcn: t.Optional[int] = None) -> bytes:
        if dcn is not None:
            block_number = self.fs.dcn_to_lbn(dcn)
        return super().read_block(block_number)


class RSTSFile(AbstractFile):
    ufd_name_entry: "UFDNameEntry"
    closed: bool

    def __init__(self, ufd_name_entry: "UFDNameEntry"):
        self.ufd_name_entry = ufd_name_entry
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
            number_of_blocks = self.ufd_name_entry.account_entry.usiz
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.ufd_name_entry.account_entry.usiz
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        cache = self.ufd_name_entry.fs.new_cache()
        cluster_dcns = self.ufd_name_entry.read_retrieval_entries(cache=cache)
        data = bytearray()
        for i in range(block_number, block_number + number_of_blocks):
            cluster = i // self.ufd_name_entry.account_entry.uclus
            cluster_block = i % self.ufd_name_entry.account_entry.uclus
            dcn = cluster_dcns[cluster] + cluster_block
            data.extend(cache.read_block(dcn=dcn))
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
        return self.ufd_name_entry.get_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return self.ufd_name_entry.get_block_size()

    def close(self) -> None:
        """
        Close the file
        """
        self.closed = True

    def __str__(self) -> str:
        return str(self.ufd_name_entry)


class Link:
    """
    Pointer between directory entries.

    http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 39
    """

    flags: int  #   Flags
    entry: int  #   Entry within the directory block (0-31)
    cluster: int  # Cluster number within the cluster map (0-6)
    block: int  #   Block number within the directory cluster

    def __init__(self, fs: "RSTSFilesystem", ulnk: int):
        self.fs = fs
        self.flags = ulnk & 0b1111  # 4 bits
        ulnk >>= 4
        self.entry = ulnk & 0b11111  # 5 bits
        ulnk >>= 5
        self.cluster = ulnk & 0b111  # 3 bits
        ulnk >>= 3
        self.block = ulnk & 0b1111  # 4 bits

    @property
    def ulnk(self) -> int:
        return (
            (self.block & 0b1111) << 12
            | (self.cluster & 0b111) << 9
            | (self.entry & 0b11111) << 4
            | self.flags & 0b1111
        )

    @property
    def is_null(self) -> bool:
        return self.ulnk == 0

    def to_lbn(self, cluster_map: t.List[int]) -> int:
        """
        Translate the link to a Logical Block Number according to the provided cluster map
        """
        return cluster_map[self.cluster] + self.block

    def __str__(self) -> str:
        return f"{self.block:>02},{self.cluster:>01},{self.entry:>02}"

    def __repr__(self) -> str:
        return f"{self.block:>02},{self.cluster:>01},{self.entry:>02} ({self.flags:04b})"


class UFDLabelEntry:
    """
    The UFD Label Entry is the root of the UFD directory structure.

    http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 33
    """

    fs: "RSTSFilesystem"
    ulnk: Link  # Link to first name entry in UFD
    ppn: PPN  # Project Programmer Number
    ufd: str  # User File Directory label

    def __init__(self, fs: "RSTSFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "RSTSFilesystem", buffer: bytes, position: int = 0) -> "UFDLabelEntry":
        self = UFDLabelEntry(fs)
        blockette = struct.unpack_from(BLOCKETTE_FORMAT, buffer, position)
        self.ulnk = Link(self.fs, blockette[0])  # Link to first name entry in UFD
        self.ppn = PPN.from_word(blockette[6])  # Project Programmer Number
        self.ufd = rad50_word_to_asc(blockette[7])
        return self

    def write_buffer(self, buffer: bytearray, position: int = 0) -> None:
        rad50_ufd = asc_to_rad50_word(self.ufd)
        struct.pack_into(
            BLOCKETTE_FORMAT,
            buffer,
            position,
            self.ulnk.ulnk,
            -1,  # Always -1 to mark this entry in use
            0,  # Unused
            0,  # Unused
            0,  # Unused
            0,  # Unused
            self.ppn.to_word(),
            rad50_ufd,
        )

    @property
    def is_ufd_label(self) -> bool:
        return self.ufd == "UFD"

    def __str__(self) -> str:
        return f"{self.ppn.to_wide_str()} ULNK: {self.ulnk} {self.ufd}"


class UFDAccountEntry:
    """
    The UFD Accounting Entry contains information about the size,
    creation date, and cluster size of the file.

    http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 35
    """

    fs: "RSTSFilesystem"
    ulnk: Link  # Link to attribute entry
    udla: int  # Date of last access
    usiz: int  # Size in blocks
    udc: int  # Creation date
    utc: int  # Creation time
    urst: str  # Runtime system name
    uclus: int  # File cluster size

    def __init__(self, fs: "RSTSFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "RSTSFilesystem", buffer: bytes, position: int) -> "UFDAccountEntry":
        self = UFDAccountEntry(fs)
        (
            ulnk,  # 2 bytes Link to attribute entry
            self.udla,  # 2 bytes Date of last access
            self.usiz,  # 2 bytes Size in blocks
            self.udc,  #  2 bytes Creation date
            self.utc,  #  2 bytes Creation time
            urst1,  # 2 bytes Runtime system name (word 1)
            urst2,  # 2 bytes Runtime system name (word 2)
            self.uclus,  # 2 bytes File cluster size
        ) = struct.unpack_from(BLOCKETTE_FORMAT, buffer, position)
        self.ulnk = Link(self.fs, ulnk)
        self.urst = rad50_word_to_asc(urst1) + rad50_word_to_asc(urst2)
        return self

    def write_buffer(self, buffer: bytearray, position: int) -> None:
        urst1 = asc_to_rad50_word(self.urst[:3])
        urst2 = asc_to_rad50_word(self.urst[3:6])
        struct.pack_into(
            BLOCKETTE_FORMAT,
            buffer,
            position,
            self.ulnk.ulnk,
            self.udla,
            self.usiz,
            self.udc,
            self.utc,
            urst1,
            urst2,
            self.uclus,
        )

    def __str__(self) -> str:
        return f"USIZ: {self.usiz} UCLUS: {self.uclus:}"


class UFDNameEntry(AbstractDirectoryEntry):

    fs: "RSTSFilesystem"
    ppn: PPN = DEFAULT_PPN
    account_entry: UFDAccountEntry
    ulnk: Link  # Link to first name entry in UFD
    filename: str  # File name
    extension: str  # File type
    uprot: int  # Protection code
    ustat: int  # Status
    uacnt: int  # Access count
    uaa: Link  # Link to accounting entry
    uar: Link  # Link to the first retrieval entry

    def __init__(self, fs: "RSTSFilesystem", ppn: PPN, ufd_uar: int):
        self.fs = fs
        self.ppn = ppn
        self.ufd_uar = ufd_uar

    @classmethod
    def read(cls, fs: "RSTSFilesystem", ppn: PPN, ufd_uar: int, buffer: bytes, position: int) -> "UFDNameEntry":
        self = UFDNameEntry(fs, ppn, ufd_uar)
        (
            ulnk,  #       2 bytes  Link to first name entry in MFD
            filename1,  #  2 bytes  File name (1st word)
            filename2,  #  2 bytes  File name (2nd word)
            filetype,  #   2 bytes  File type
            self.ustat,  # 1 byte   Status
            self.uprot,  # 1 byte   Protection code
            self.uacnt,  # 2 bytes  Access count
            uaa,  #        2 bytes  Link to accounting entry
            uar,  #        2 bytes  Link to the first retrieval entry
        ) = struct.unpack_from(UFD_ENTRY_FORMAT, buffer, position)
        self.ulnk = Link(self.fs, ulnk)
        self.uaa = Link(self.fs, uaa)
        self.uar = Link(self.fs, uar)
        self.filename = rad50_word_to_asc(filename1) + rad50_word_to_asc(filename2)
        self.extension = rad50_word_to_asc(filetype)
        return self

    def write_buffer(self, buffer: bytearray, position: int) -> None:
        filename1 = asc_to_rad50_word(self.filename[:3])
        filename2 = asc_to_rad50_word(self.filename[3:6])
        filetype = asc_to_rad50_word(self.extension)
        struct.pack_into(
            UFD_ENTRY_FORMAT,
            buffer,
            position,
            self.ulnk.ulnk,
            filename1,
            filename2,
            filetype,
            self.ustat,
            self.uprot,
            self.uacnt,
            self.uaa.ulnk,
            self.uar.ulnk,
        )

    def read_retrieval_entries(self, cache: t.Optional[RTFSBlockCache] = None) -> t.List[int]:
        """
            The retrieval entries provide the information necessary to locate the file blocks on the disk.

            +-------------------------------------+
         0  |     Link to next Retrieval Entry    |
            +-------------------------------------+
         2  |          DCN of cluster 0           |
            +-------------------------------------+
            |                 ...                 |
            +-------------------------------------+
        16  |          DCN of cluster 6           |
            +-------------------------------------+

            http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 37
        """
        if cache is None:
            cache = self.fs.new_cache()

        # Read the UFD cluster map
        retrieval_entry_link = self.uar
        buffer = cache.read_block(retrieval_entry_link.block + self.ufd_uar)
        ufd_cluster_map = self.fs.read_ufd_cluster_map(buffer)

        # Read the retrieval entries
        cluster_dcns: t.List[int] = []
        while not retrieval_entry_link.is_null:
            buffer = cache.read_block(retrieval_entry_link.to_lbn(ufd_cluster_map))
            blockette = struct.unpack_from(BLOCKETTE_FORMAT, buffer, UFD_ENTRY_LEN * retrieval_entry_link.entry)
            retrieval_entry_link = Link(self.fs, blockette[0])
            cluster_dcns += blockette[1:]
        return cluster_dcns

    @property
    def fullname(self) -> str:
        return f"{self.ppn or ''}{self.filename}.{self.extension}"

    @property
    def basename(self) -> str:
        return f"{self.filename}.{self.extension}"

    @property
    def creation_date(self) -> datetime:
        return rsts_to_date(self.account_entry.udc, self.account_entry.utc)

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return self.account_entry.usiz

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.get_length() * self.get_block_size()

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return BLOCK_SIZE

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

    def open(self, file_mode: t.Optional[str] = None) -> RSTSFile:
        """
        Open a file
        """
        return RSTSFile(self)

    @property
    def is_ufd_name_entry(self) -> bool:
        return (self.ustat & 64) == 0

    def __str__(self) -> str:
        date = self.creation_date.strftime("%d-%b-%y %H:%M")
        return f"{self.filename:<6}.{self.extension:<3} {self.account_entry.usiz:>5}   <{self.uprot:>3}> {date}  ULNK: {self.ulnk} UAA: {self.uaa} UAR: {self.uar} USTAT: {self.ustat}"


class MFDNameEntry:
    """
    The MFD Name Entry is used to catalog all the accounts.
    Each account on the disk has a MFD Name Entry associated with it.

    http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 20
    """

    fs: "RSTSFilesystem"
    ulnk: Link  # Link to first name entry in UFD
    ppn: PPN  # Project Programmer Number
    passwd: str  # Password
    uprot: int  # Protection code
    ustat: int  # Status
    uacnt: int  # Access count
    uaa: Link  # Link to accounting entry
    uar: int  # DCN of the first cluster of the user's UFD

    def __init__(self, fs: "RSTSFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "RSTSFilesystem", buffer: bytes, position: int = 0) -> "MFDNameEntry":
        self = MFDNameEntry(fs)
        (
            ulnk,  #       2 bytes  Link to first name entry in MFD
            unam,  #       2 bytes  PPN project / PPN programmer
            passwd0,  #    2 bytes  Password (first word)
            passwd1,  #    2 bytes  Password (second word)
            self.ustat,  # 1 byte   Status
            self.uprot,  # 1 byte   Unused (protection code)
            self.uacnt,  # 2 bytes  Access count
            uaa,  #        2 bytes  Link to accounting entry
            self.uar,  #   2 bytes  DCN of the first cluster of the user's UFD
        ) = struct.unpack_from(MFD_ENTRY_FORMAT, buffer, position)
        self.ulnk = Link(self.fs, ulnk)
        self.uaa = Link(self.fs, uaa)
        self.ppn = PPN.from_word(unam)
        self.passwd = rad50_word_to_asc(passwd0) + rad50_word_to_asc(passwd1)
        return self

    def write_buffer(self, buffer: bytearray, position: int = 0) -> None:
        passwd0 = asc_to_rad50_word(self.passwd[:3])
        passwd1 = asc_to_rad50_word(self.passwd[3:6])
        struct.pack_into(
            MFD_ENTRY_FORMAT,
            buffer,
            position,
            self.ulnk.ulnk,
            self.ppn.to_word(),
            passwd0,
            passwd1,
            self.ustat,
            self.uprot,
            self.uacnt,
            self.uaa.ulnk,
            self.uar,
        )

    @property
    def is_mfd_name_entry(self) -> bool:
        return (self.ustat & US_UFD) != 0

    def __str__(self) -> str:
        return f"{self.ppn.to_wide_str()} {self.passwd:<9} ULNK: {self.ulnk} USTAT: {self.ustat} UACNT: {self.uacnt} UAA: {self.uaa} UAR: {self.uar:>5}"


class GFD:
    """
    Group File Directory (RDS1.1 or later)

    http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 23
    """

    fs: "RSTSFilesystem"
    mfd: "MFD"
    group: int  # Group number
    dcn: int  # DCN of GFD
    gfd_cluster_size: int  # MFD cluster size
    gfd_cluster_map: t.List[int]  # MFD cluster map (DCN of MFD clusters 0 - 6)
    ufd_pointer_map: t.List[int]  # Pointers to User File Directories
    name_entry_pointer_map: t.List[Link]  # Links to name entries

    def __init__(self, mfd: "MFD", group: int):
        self.fs = mfd.fs
        self.mfd = mfd
        self.group = group
        self.dcn = mfd.gfd_pointer_map[group]

    @classmethod
    def read(cls, mfd: "MFD", group: int, cache: t.Optional[RTFSBlockCache] = None) -> "GFD":
        if cache is None:
            cache = mfd.fs.new_cache()
        self = GFD(mfd, group)
        buffer = cache.read_block(dcn=self.dcn + LABEL_BLOCK_OFFSET)
        self.read_mfd_cluster_map(buffer)
        buffer = cache.read_block(dcn=self.dcn + UFD_POINTER_BLOCK_OFFSET)
        self.ufd_pointer_map = list(struct.unpack_from(UFD_POINTER_BLOCK_FORMAT, buffer, 0))
        buffer = cache.read_block(dcn=self.dcn + GFD_NAME_ENTRY_BLOCK_OFFSET)
        self.name_entry_pointer_map = [Link(mfd.fs, x) for x in struct.unpack_from(UFD_POINTER_BLOCK_FORMAT, buffer, 0)]
        return self

    def read_mfd_cluster_map(self, buffer: bytes) -> None:
        """
            Read MFD cluster map.
            The MFD cluster map contains pointer to each cluster in the MFD.

            +-------------------------------------+
         0  |          MFD cluster size           |
            +-------------------------------------+
         2  |       DCN of MFD cluster 0          |
            +-------------------------------------+
            |                 ...                 |
            +-------------------------------------+
        16  |       DCN of MFD cluster 6          |
            +-------------------------------------+

            http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 22

        """
        blockette = struct.unpack_from(BLOCKETTE_FORMAT, buffer, CLUSTER_MAP_POS)
        self.gfd_cluster_size = blockette[0]
        self.gfd_cluster_map = list(blockette[1:])

    def read_gfd_name_entries(self, cache: t.Optional[RTFSBlockCache] = None) -> t.Iterator["MFDNameEntry"]:
        if cache is None:
            cache = self.fs.new_cache()
        for user, link in enumerate(self.name_entry_pointer_map):
            if not link.is_null:
                buffer = cache.read_block(link.to_lbn(self.gfd_cluster_map))
                yield MFDNameEntry.read(self.fs, buffer, MFD_ENTRY_LEN * link.entry)

    def read_dir_entries(
        self,
        ppn: t.Optional[PPN] = None,
        cache: t.Optional[RTFSBlockCache] = None,
    ) -> t.Iterator["UFDNameEntry"]:
        if ppn is not None and ppn.group != ANY_GROUP and ppn.group != self.group:
            return
        if cache is None:
            cache = self.fs.new_cache()
        for user, ufd_pointer in enumerate(self.ufd_pointer_map):
            if ufd_pointer != 0 and (ppn is None or ppn.user == ANY_USER or ppn.user == user):
                ufd_ppn = PPN(group=self.group, user=user)
                buffer = cache.read_block(dcn=ufd_pointer)
                ufd_label = UFDLabelEntry.read(self.fs, buffer)
                yield from self.fs.read_ufd_name_entries(ufd_label.ulnk, ufd_pointer, ppn=ufd_ppn, cache=cache)


class MFD:
    """
    Master File Directory (RDS1.1 or later)

    http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 25
    """

    fs: "RSTSFilesystem"
    gfd_pointer_map: t.List[int]  # Pointers to Group File Directories (RDS1.1)
    mdcn: int  # DCN of MFD

    def __init__(self, fs: "RSTSFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "RSTSFilesystem", mdcn: int) -> "MFD":
        self = MFD(fs)
        self.mdcn = mdcn

        # Read MFD label block
        buffer = self.fs.read_block(dcn=self.mdcn + LABEL_BLOCK_OFFSET)
        self.fs.read_mfd_cluster_map(buffer)  # Read cluster map from MFD label block

        # Read the GFD pointer block
        buffer = self.fs.read_block(dcn=self.mdcn + GFD_POINTER_BLOCK_OFFSET)
        self.gfd_pointer_map = list(struct.unpack_from(GFD_POINTER_BLOCK_FORMAT, buffer, 0))
        return self

    def read_gfds(self, ppn: t.Optional[PPN] = None, cache: t.Optional[RTFSBlockCache] = None) -> t.Iterator["GFD"]:
        """
        Read GFDs (Group File Directories)
        """
        if cache is None:
            cache = self.fs.new_cache()
        for group, dcn in enumerate(self.gfd_pointer_map):
            if dcn != 0 and (ppn is None or ppn.group == ANY_GROUP or ppn.group == group):
                yield GFD.read(self, group, cache)


class RSTSFilesystem(AbstractFilesystem, BlockDevice):
    """
    RSTS/E Filesystem

    RDS0 directory structure:

    MFD Label ---> MFD Name ---> MFD Name --> ...
                      |
                      |
                     \|/
                  UFD Label ---> UFD Name --> UFD Name --> ...
                                    |
                                   \|/
                               UFD Account
                                    |
                                   \|/
                                Retrieval --> Retrieval --> ...

    RDS1.x directory structure:

    MFD Label ---> MFD
                    |
                    +---> GFD ---> UFD Name --> UFD Name --> ...
                    |      |
                    |      +----> UFD Name --> UFD Name --> ...
                    |      |         |
                    |      |        \|/
                    |      |    UFD Account
                    |      |         |
                    |      |        \|/
                    |      |     Retrieval --> Retrieval --> ...
                    |      |
                    |      +----> UFD Name --> UFD Name --> ...
                    |      ...
                    |
                    +---> GFD ---> UFD Name --> UFD Name --> ...
                    ...

    """

    fs_name = "rsts"
    fs_description = "PDP-11 RSTS/E"

    ppn: PPN  # Current Project Programmer Number

    device_size: int  # Device size in blocks
    dcs: int  # Device Cluster Size
    plvl: int  # Revision level (RDS1.1)
    ppcs: int  # Pack cluster size
    pstat: int  # Pack status
    pckid: str  # Pack ID
    structure_level: str  # RDS level
    mfd_cluster_size: int  # MFD cluster size
    mfd_cluster_map: t.List[int]  # MFD cluster map (DCN of MFD clusters 0 - 6)
    mfd_first_name_entry: Link  # Link to first name entry in MFD (RDS0)
    mfd: t.Optional[MFD] = None  # Master File Directory (RDS1.1 or later)

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        self.read_disk_pack_label()
        self.ppn = DEFAULT_PPN
        if strict:
            # Check if the SATT.SYS file exists
            try:
                self.get_file_entry(SAT_FILENAME)
            except FileNotFoundError:
                raise OSError(errno.EIO, "SATT.SYS file not found")
        return self

    def new_cache(self) -> "RTFSBlockCache":
        return RTFSBlockCache(self)

    def compute_dcs(self) -> int:
        """
        Compute DCS (Device Cluster Size)
        It is calculated such that all clusters on the disk
        can be specified by a 16-bit number
        """
        d = (self.device_size - 1) >> 16
        dcs = 1
        while d:
            d >>= 1
            dcs <<= 1
        return dcs

    def dcn_to_lbn(self, dcn: int) -> int:
        """
        Convert DCN (Device Cluster Number)
        to LBN (Logical Block Number)
        """
        return dcn * self.dcs

    def read_disk_pack_label(self) -> None:
        """Read disk pack label"""
        self.device_size = self.get_size() // BLOCK_SIZE
        self.dcs = self.compute_dcs()

        buffer = self.read_block(dcn=DISK_PACK_LABEL_DCN)
        (
            self.ulnk,  #   2 bytes  Link to first name entry in MFD (RDS0)
            _,  #           2 bytes  Unused (-1)
            mdcn,  #        2 bytes  DCN of MFD (RDS1.1)
            self.plvl,  #   2 bytes  Revision level (RDS1.1)
            self.ppcs,  #   2 bytes  Pack cluster size
            self.pstat,  #  2 bytes  Pack status
            pckid0,  #      2 bytes  Pack ID first word
            pckid1,  #      2 bytes  Pack ID second word
        ) = struct.unpack_from(DISK_PACK_LABEL_FORMAT, buffer, 0)
        self.pckid = rad50_word_to_asc(pckid0) + rad50_word_to_asc(pckid1)
        self.mfd_first_name_entry = Link(self, self.ulnk)

        if self.pstat & RDS1_FLAGS:  # RDS1.x
            if self.plvl == RDS11_PLVL:
                self.structure_level = "RDS1.1"
            else:
                self.structure_level = "RDS1.2"
            self.mfd = MFD.read(self, mdcn)
        else:  # RDS0
            self.plvl = RDS0_PLVL  # Set RDS level to 0
            self.structure_level = "RDS0"
            self.read_mfd_cluster_map(buffer)

    def read_mfd_cluster_map(self, buffer: bytes) -> None:
        """
            Read MFD cluster map.
            The MFD cluster map contains pointer to each cluster in the MFD.

            +-------------------------------------+
         0  |          MFD cluster size           |
            +-------------------------------------+
         2  |       DCN of MFD cluster 0          |
            +-------------------------------------+
            |                 ...                 |
            +-------------------------------------+
        16  |       DCN of MFD cluster 6          |
            +-------------------------------------+

            http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 22

        """
        blockette = struct.unpack_from(BLOCKETTE_FORMAT, buffer, CLUSTER_MAP_POS)
        self.mfd_cluster_size = blockette[0]
        self.mfd_cluster_map = list(blockette[1:])

    def read_ufd_cluster_map(self, buffer: bytes) -> t.List[int]:
        """
            Read UFD cluster map.
            The UFD cluster map contains pointer to each cluster in the UFD.

            +-------------------------------------+
         0  |          UFD cluster size           |
            +-------------------------------------+
         2  |       DCN of UFD cluster 0          |
            +-------------------------------------+
            |                 ...                 |
            +-------------------------------------+
        16  |       DCN of UFD cluster 6          |
            +-------------------------------------+

            http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 38

        """
        blockette = struct.unpack_from(BLOCKETTE_FORMAT, buffer, CLUSTER_MAP_POS)
        return list(blockette[1:])

    def read_mfd_name_entries(self, cache: t.Optional[RTFSBlockCache] = None) -> t.Iterator["MFDNameEntry"]:
        """
        Read MFD name entries
        http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 20
        """
        if cache is None:
            cache = self.new_cache()
        if self.mfd is not None:  # RDS1.x
            for gfd in self.mfd.read_gfds(cache=cache):
                yield from gfd.read_gfd_name_entries(cache=cache)
        else:  # RDS0
            link = self.mfd_first_name_entry
            while not link.is_null:
                buffer = cache.read_block(link.to_lbn(self.mfd_cluster_map))
                mfd_entry = MFDNameEntry.read(self, buffer, MFD_ENTRY_LEN * link.entry)
                link = mfd_entry.ulnk
                if mfd_entry.is_mfd_name_entry:
                    yield mfd_entry

    def read_ufd_name_entries(
        self,
        link: Link,
        ufd_uar: int,
        ppn: PPN,
        cache: t.Optional[RTFSBlockCache] = None,
    ) -> t.Iterator["UFDNameEntry"]:
        """
        Read UFD name entries
        http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 20
        """
        if cache is None:
            cache = self.new_cache()
        buffer = cache.read_block(link.block + ufd_uar)
        ufd_cluster_map = self.read_ufd_cluster_map(buffer)
        while not link.is_null:
            buffer = cache.read_block(link.to_lbn(ufd_cluster_map))
            ufd_entry = UFDNameEntry.read(self, ppn, ufd_uar, buffer, UFD_ENTRY_LEN * link.entry)
            buffer = cache.read_block(ufd_entry.uaa.to_lbn(ufd_cluster_map))
            ufd_entry.account_entry = UFDAccountEntry.read(self, buffer, UFD_ENTRY_LEN * ufd_entry.uaa.entry)
            link = ufd_entry.ulnk
            yield ufd_entry

    def read_ufd_label_entry(self, dcn: int) -> "UFDLabelEntry":
        """
        Read UFD label entry
        http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 33
        """
        buffer = self.read_block(dcn=dcn)
        return UFDLabelEntry.read(self, buffer)

    def read_block(
        self,
        block_number: int = 0,
        number_of_blocks: int = 1,
        dcn: t.Optional[int] = None,
    ) -> bytes:
        if dcn is not None:
            block_number = self.dcn_to_lbn(dcn)
        return super().read_block(block_number, number_of_blocks)

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def read_dir_entries(self, ppn: PPN, cache: t.Optional[RTFSBlockCache] = None) -> t.Iterator["UFDNameEntry"]:
        if cache is None:
            cache = self.new_cache()
        if self.mfd is not None:  # RDS1.x
            for gfd in self.mfd.read_gfds(ppn=ppn, cache=cache):
                yield from gfd.read_dir_entries(ppn=ppn, cache=cache)
        else:  # RDS0
            for mfd_entry in self.read_mfd_name_entries(cache):
                match = True
                if ppn.group != ANY_GROUP:
                    match &= mfd_entry.ppn.group == ppn.group
                if ppn.user != ANY_USER:
                    match &= mfd_entry.ppn.user == ppn.user
                if match:
                    if mfd_entry.ppn == ACCOUNT_1_1_PPN:
                        for ufd_entry in self.read_ufd_name_entries(
                            mfd_entry.ulnk, mfd_entry.uar, ppn=mfd_entry.ppn, cache=cache
                        ):
                            if ufd_entry.is_ufd_name_entry:
                                yield ufd_entry
                    elif mfd_entry.uar != 0:
                        ufd_label = self.read_ufd_label_entry(mfd_entry.uar)
                        yield from self.read_ufd_name_entries(
                            ufd_label.ulnk,
                            mfd_entry.uar,
                            ppn=mfd_entry.ppn,
                            cache=cache,
                        )

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
        ppn: t.Optional[PPN] = None,
    ) -> t.Iterator["UFDNameEntry"]:
        if ppn is None:
            ppn = self.ppn
        ppn, pattern = rsts_split_fullname(fullname=pattern, wildcard=wildcard, ppn=ppn)
        for entry in self.read_dir_entries(ppn=ppn):
            if filename_match(entry.basename, pattern, wildcard):
                yield entry

    @property
    def entries_list(self) -> t.Iterator[UFDNameEntry]:
        yield from self.read_dir_entries(ppn=self.ppn)

    def get_file_entry(self, fullname: str) -> UFDNameEntry:
        fullname = rsts_canonical_fullname(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        ppn, basename = rsts_split_fullname(fullname=fullname, wildcard=False, ppn=self.ppn)
        try:
            return next(self.filter_entries_list(basename, wildcard=False, ppn=ppn))
        except StopIteration:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def write_bytes(
        self,
        fullname: str,
        content: bytes,
        creation_date: t.Optional[date] = None,
        file_type: t.Optional[str] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def create_file(
        self,
        fullname: str,
        number_of_blocks: int,  # length in blocks
        creation_date: t.Optional[date] = None,  # optional creation date
        file_type: t.Optional[str] = None,
    ) -> t.Optional[UFDNameEntry]:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        if options.get("uic"):
            # Listing of all PPN
            for mfd_entry in self.read_mfd_name_entries():
                sys.stdout.write(f"{mfd_entry.ppn}\n")
            return
        files = 0
        blocks = 0
        ppn, pattern = rsts_split_fullname(fullname=pattern, wildcard=True, ppn=self.ppn)
        if not options.get("brief"):
            sys.stdout.write(f" Name .Ext  Size    Prot   Date       {volume_id}:{ppn}\n")
        for x in self.filter_entries_list(pattern, ppn=ppn, include_all=True, wildcard=True):
            if options.get("brief"):
                # Lists only file names and file types
                sys.stdout.write(f"{x.filename:<6}.{x.extension:<3}\n")
            else:
                date = x.creation_date.strftime("%d-%b-%y %H:%M")
                sys.stdout.write(
                    f"{x.filename:<6}.{x.extension:<3} {x.account_entry.usiz:>5}   <{x.uprot:>3}> {date}\n"
                )
                blocks += x.account_entry.usiz
                files += 1
        if options.get("brief"):
            return
        sys.stdout.write("\n")
        sys.stdout.write(f" Total of {blocks} blocks in {files} files in {volume_id}:{ppn}\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        ppn = None
        if arg and "[" in arg:
            try:
                ppn = PPN.from_str(arg)
                arg = arg.split("]", 1)[1]
            except Exception:
                return
        if ppn is not None:
            for mfd_entry in self.read_mfd_name_entries():
                match = True
                if ppn.group != ANY_GROUP:
                    match &= mfd_entry.ppn.group == ppn.group
                if ppn.user != ANY_USER:
                    match &= mfd_entry.ppn.user == ppn.user
                if match:
                    sys.stdout.write(f"{ppn}\n")
                    sys.stdout.write(dump_struct(mfd_entry.__dict__))
                    sys.stdout.write("\n\nUDF Entries Label\n\n")
                    if mfd_entry.ppn == ACCOUNT_1_1_PPN and self.structure_level == "RDS0":
                        for ufd_entry in self.read_ufd_name_entries(mfd_entry.ulnk, mfd_entry.uar, mfd_entry.ppn):
                            if ufd_entry.is_ufd_name_entry:
                                sys.stdout.write(f"{ufd_entry}\n")
                    else:
                        ufd_label = self.read_ufd_label_entry(mfd_entry.uar)
                        for ufd_entry in self.read_ufd_name_entries(ufd_label.ulnk, mfd_entry.uar, mfd_entry.ppn):
                            sys.stdout.write(f"{ufd_entry}\n")

        else:
            sys.stdout.write("Disk Pack Label\n\n")
            sys.stdout.write(dump_struct(self.__dict__))
            sys.stdout.write("\n\nMFD Cluster Map\n\n")
            for i, dcn in enumerate(self.mfd_cluster_map):
                if dcn:
                    sys.stdout.write(f"Cluster {i} -> DCN {dcn}\n")
            if self.mfd is not None:
                sys.stdout.write("\nGFD Pointer Map\n\n")
                for i, dcn in enumerate(self.mfd.gfd_pointer_map):
                    if dcn:
                        sys.stdout.write(f"Cluster {i} -> DCN {dcn}\n")
            # Listing of all PPN
            sys.stdout.write("\nProject Programmer Numbers\n\n")
            for mfd_entry in self.read_mfd_name_entries():
                sys.stdout.write(f"{mfd_entry}\n")

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
        Change the current Project Programmer Number
        """
        try:
            self.ppn = PPN.from_str(fullname)
            return True
        except Exception:
            return False

    def get_pwd(self) -> str:
        return str(self.ppn)
