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
from datetime import datetime, timedelta

from ..abstract import XFERXPath, AbstractFile
from ..cache import BlockCache
from ..commons import BLOCK_SIZE, READ_FILE_FULL, dump_struct, filename_match
from ..device.abstract import AbstractDevice
from ..uic import UIC
from .abstract import AbstractRXBlockFilesystem
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
# Master File Directory (MFD) - only one per disk pack
# Group File Directories (GFDs) - up to 255 GFDs per disk pack
# User File Directories (UFDs) - up to 255 UFDs per GFD

# Logical block = 256 16-bit words  (512 bytes)
# Logical Block Number (LBN) = 0..n (23 bit)
# Cluster = a collection of sequetial logical blocks
# Cluster factor = number of logical blocks in a cluster (power of 2)
# Device Cluster = smallest allocation unit on the device
# Device Cluster Size (DCS) = < 16, chosen to keep DCN a 16-bit value
# Device Cluster Number (DCN) = the number of the Device Cluster, a 16 bit value
# Blockette = 8 words
# Each disk block contains 32 blockettes
# xxx File Directory Cluster Map - Blockette 31

WORDS_PER_BLOCK = 256  # Number of words per block
BOOT_BLOCK = 0  # Boot block
LABEL_BLOCK_OFFSET = 0  # Label block offset from MFD/GFD (RDS1.1 and later)
GFD_POINTER_BLOCK_OFFSET = 1  # GFD pointer block offset from MFD (RDS1.1 and later)
UFD_POINTER_BLOCK_OFFSET = 1  # UFD pointer block offset from GFD (RDS1.1 and later)
GFD_NAME_ENTRY_BLOCK_OFFSET = 2  # GFD name entries pointer block (RDS1.1 and later)
DISK_PACK_LABEL_DCN = 1  # Disk pack label DCN
BLOCKETTE_FORMAT = '<HHHHHHHH'  # 8 words blockette
BLOCKETTE_LEN = struct.calcsize(BLOCKETTE_FORMAT)
BLOCKETTE_WORDS = 8  # Number of words in a blockette
BLOCKETTE_MAX = WORDS_PER_BLOCK // BLOCKETTE_WORDS  # Maximum number of blockettes in a block
FDCM_BLOCKETTE = BLOCKETTE_MAX - 1  # Blockette 31 is the File Directory Cluster Map
MFD_ENTRY_FORMAT = '<HHHHBBHHH'
MFD_ENTRY_LEN = struct.calcsize(MFD_ENTRY_FORMAT)
UFD_ENTRY_FORMAT = '<HHHHBBHHH'
UFD_ENTRY_LEN = struct.calcsize(UFD_ENTRY_FORMAT)
assert MFD_ENTRY_LEN == BLOCKETTE_LEN == UFD_ENTRY_LEN
GFD_POINTER_BLOCK_FORMAT = '<255H'
UFD_POINTER_BLOCK_FORMAT = '<255H'
US_UFD = 1 << 6  # USTAT bit 6 - 1 for MFD Name Entry
RDS1_FLAGS = 0o20000  # RDS1.1 or RDS1.2
RDS0_PLVL = 0  # RDS 0 - V7.x and before
RDS11_PLVL = 257  # RDS 1.1 - V8
RDS12_PLVL = 258  # RDS 1.2 - V9.0 and beyond
SAT_FILENAME = "[0,1]SATT.SYS"  # Storage Allocation Table filename
BAD_BLOCK_FILENAME = "[0,1]BADB.SYS"  # Bad Block filename
ACCOUNTING_FILENAME = "[1,2]ACCT.SYS"


class PPN(UIC):
    """
    Programmer Project Number
    The format of PPN if [ggg,uuu] there ggg and uuu are decimal digits
    The value on the left of the comma is represents the project number,
    the value on the right represents the programmer's number within the project.
    """

    GROUP_BITS = 8
    USER_BITS = 8
    IS_OCTAL = False  # Indicates that the PPN is not in octal format


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
    dt = datetime(year, 1, 1) + timedelta(days=day_of_year - 1)
    time_of_day = timedelta(minutes=1440 - utc)
    full_datetime = dt + time_of_day
    return full_datetime


def rsts_canonical_filename(fullname: t.Optional[str], wildcard: bool = False) -> str:
    """
    Generate the canonical RSTS/E name
    """
    fullname = (fullname or "").upper()
    try:
        stem, extension = fullname.split(".", 1)
    except Exception:
        stem = fullname
        extension = "*" if wildcard else ""
    stem = rad2asc(asc2rad(stem[0:3])) + rad2asc(asc2rad(stem[3:6]))
    extension = rad2asc(asc2rad(extension))
    return f"{stem}.{extension}"


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


def rsts_split_fullname(
    ppn: t.Optional[PPN], fullname: t.Optional[str], wildcard: bool = True
) -> t.Tuple[t.Optional[PPN], t.Optional[str]]:
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
        super().__init__(fs.dev)
        self.fs = fs

    def read_block(self, block_number: int = 0) -> bytes:
        return super().read_block(block_number)

    def read_words_block(self, block_number: int = 0) -> t.List[int]:
        """
        Read a 512 bytes block as 256 16bit words
        """
        data = self.read_block(block_number)
        if not data:
            raise OSError(errno.EIO, f"Failed to read block {block_number}")
        return list(struct.unpack_from("<256H", data))


class RSTSFile(AbstractFile):
    entry: "UFDNameEntry"

    def __init__(self, entry: "UFDNameEntry"):
        self.entry = entry

    @property
    def name(self) -> str:
        """Final path component"""
        return self.entry.name

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = self.entry.account_entry.usiz
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.entry.account_entry.usiz
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        cache = self.entry.fs.new_cache()
        # Get the blocks to be read
        blocks = self.entry.get_blocks(cache=cache)[block_number : block_number + number_of_blocks]
        data = bytearray()
        for dcn in blocks:
            data.extend(cache.read_block(self.entry.fs.dcn_to_lbn(dcn)))
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
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

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

    def __str__(self) -> str:
        return str(self.entry)


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


class RSTSPath(XFERXPath):
    """
    Abstract class for RSTS/E directory entries
    """


class UFDNameEntry(RSTSPath):
    fs: "RSTSFilesystem"
    ppn: PPN = DEFAULT_PPN
    account_entry: UFDAccountEntry
    ulnk: Link  # Link to first name entry in UFD
    stem: str  # File name
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
        self.stem = rad50_word_to_asc(filename1) + rad50_word_to_asc(filename2)
        self.extension = rad50_word_to_asc(filetype)
        return self

    def write_buffer(self, buffer: bytearray, position: int) -> None:
        filename1 = asc_to_rad50_word(self.stem[:3])
        filename2 = asc_to_rad50_word(self.stem[3:6])
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
        words = cache.read_words_block(retrieval_entry_link.block + self.ufd_uar)
        ufd_cluster_map = self.fs.read_ufd_cluster_map(words)

        # Read the retrieval entries
        cluster_dcns: t.List[int] = []
        while not retrieval_entry_link.is_null:
            buffer = cache.read_block(retrieval_entry_link.to_lbn(ufd_cluster_map))
            blockette = struct.unpack_from(BLOCKETTE_FORMAT, buffer, UFD_ENTRY_LEN * retrieval_entry_link.entry)
            retrieval_entry_link = Link(self.fs, blockette[0])
            cluster_dcns += blockette[1:]
        return cluster_dcns

    def get_blocks(self, cache: t.Optional[RTFSBlockCache] = None) -> t.List[int]:
        """
        Get blocks (DCNs)
        """
        if cache is None:
            cache = self.fs.new_cache()
        cluster_dcns = self.read_retrieval_entries(cache=cache)
        number_of_blocks = self.account_entry.usiz
        result: t.List[int] = []
        for i in range(0, number_of_blocks):
            cluster = i // self.account_entry.uclus
            cluster_block = i % self.account_entry.uclus
            dcn = cluster_dcns[cluster] + cluster_block
            result.append(dcn)
        return result

    @property
    def fullname(self) -> str:
        return f"{self.ppn or ''}{self.stem}.{self.extension}"

    @property
    def name(self) -> str:
        return f"{self.stem}.{self.extension}"

    @property
    def parent(self) -> RSTSPath:
        """Parent directory"""
        return self.fs.get_ufd(self.ppn)

    @property
    def creation_date(self) -> datetime:
        return rsts_to_date(self.account_entry.udc, self.account_entry.utc)

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return self.account_entry.usiz

    def get_size(self, fork: t.Optional[str] = None) -> int:
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

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> RSTSFile:
        """
        Open a file
        """
        return RSTSFile(self)

    def is_dir(self) -> bool:
        return False

    @property
    def is_ufd_name_entry(self) -> bool:
        return (self.ustat & 64) == 0

    def __str__(self) -> str:
        date = self.creation_date.strftime("%d-%b-%y %H:%M")
        return f"{self.stem:<6}.{self.extension:<3} {self.account_entry.usiz:>5}   <{self.uprot:>3}> {date}  ULNK: {self.ulnk} UAA: {self.uaa} UAR: {self.uar} USTAT: {self.ustat}"


class UserFileDirectory(RSTSPath):
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
    def read(cls, fs: "RSTSFilesystem", buffer: bytes, position: int = 0) -> "UserFileDirectory":
        self = UserFileDirectory(fs)
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
    def name(self) -> str:
        return str(self.ppn)

    @property
    def fullname(self) -> str:
        return self.name

    @property
    def parent(self) -> XFERXPath:
        """Parent directory"""
        return self.fs.get_root()

    # @property
    # def creation_date(self) -> datetime:
    #     return rsts_to_date(self.account_entry.udc, self.account_entry.utc)

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

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> RSTSFile:
        """
        Open a file
        """
        raise OSError(errno.EISDIR, os.strerror(errno.EISDIR))

    def is_dir(self) -> bool:
        return True

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
        words = cache.read_words_block(self.fs.dcn_to_lbn(self.dcn + LABEL_BLOCK_OFFSET))
        self.read_mfd_cluster_map(words)
        buffer = cache.read_block(self.fs.dcn_to_lbn(self.dcn + UFD_POINTER_BLOCK_OFFSET))
        self.ufd_pointer_map = list(struct.unpack_from(UFD_POINTER_BLOCK_FORMAT, buffer, 0))
        buffer = cache.read_block(self.fs.dcn_to_lbn(self.dcn + GFD_NAME_ENTRY_BLOCK_OFFSET))
        self.name_entry_pointer_map = [Link(mfd.fs, x) for x in struct.unpack_from(UFD_POINTER_BLOCK_FORMAT, buffer, 0)]
        return self

    def read_mfd_cluster_map(self, words: t.List[int]) -> None:
        """
            Read MFD cluster map.
            The MFD cluster map contains pointer to each cluster in the MFD.

        Word
            +-------------------------------------+
         0  |          MFD cluster size           |
            +-------------------------------------+
         1  |       DCN of MFD cluster 0          |
            +-------------------------------------+
            |                 ...                 |
            +-------------------------------------+
         7  |       DCN of MFD cluster 6          |
            +-------------------------------------+

            http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 22
            https://bitsavers.org/pdf/dec/pdp11/rsts_e/V08/AA-CL35A-TE_8.0intern_Sep84.pdf Pag 111

        """
        blockette = words[FDCM_BLOCKETTE * BLOCKETTE_WORDS : (FDCM_BLOCKETTE + 1) * BLOCKETTE_WORDS]
        self.gfd_cluster_size = blockette[0]
        self.gfd_cluster_map = list(blockette[1:])

    def iterdir(self, cache: t.Optional[RTFSBlockCache] = None) -> t.Iterator["UserFileDirectory"]:
        if cache is None:
            cache = self.fs.new_cache()
        for user, link in enumerate(self.name_entry_pointer_map):
            if not link.is_null:
                buffer = cache.read_block(link.to_lbn(self.gfd_cluster_map))
                yield UserFileDirectory.read(self.fs, buffer, MFD_ENTRY_LEN * link.entry)

    def read_dir_entries(
        self,
        ppn: t.Optional[PPN] = None,
        cache: t.Optional[RTFSBlockCache] = None,
    ) -> t.Iterator["UFDNameEntry"]:
        if ppn is not None and ppn.group != PPN.ANY_GROUP and ppn.group != self.group:
            return
        if cache is None:
            cache = self.fs.new_cache()
        for user, ufd_pointer in enumerate(self.ufd_pointer_map):
            if ufd_pointer != 0 and (ppn is None or ppn.user == PPN.ANY_USER or ppn.user == user):
                ufd_ppn = PPN(group=self.group, user=user)
                buffer = cache.read_block(self.fs.dcn_to_lbn(ufd_pointer))
                ufd_label = UFDLabelEntry.read(self.fs, buffer)
                yield from self.fs.read_ufd_name_entries(ufd_label.ulnk, ufd_pointer, ppn=ufd_ppn, cache=cache)


class MFD(XFERXPath):
    """
    Master File Directory (RDS1.1 or later)

    http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 25
    """

    fs: "RSTSFilesystem"
    gfd_pointer_map: t.List[int]  # Pointers to Group File Directories (RDS1.1)
    dcn: int  # Device Cluster Number of the MFD

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
    def parent(self) -> XFERXPath:
        """The logical parent"""
        return self

    @classmethod
    def read(cls, fs: "RSTSFilesystem", dcn: int) -> "MFD":
        self = MFD(fs)
        self.dcn = dcn

        # Read MFD label block
        words = self.fs.read_words_block(self.fs.dcn_to_lbn(self.dcn + LABEL_BLOCK_OFFSET))
        self.fs.read_mfd_cluster_map(words)  # Read cluster map from MFD label block

        # Read the GFD pointer block
        buffer = self.fs.read_block(self.fs.dcn_to_lbn(self.dcn + GFD_POINTER_BLOCK_OFFSET))
        self.gfd_pointer_map = list(struct.unpack_from(GFD_POINTER_BLOCK_FORMAT, buffer, 0))
        return self

    def read_gfds(self, ppn: t.Optional[PPN] = None, cache: t.Optional[RTFSBlockCache] = None) -> t.Iterator["GFD"]:
        """
        Read GFDs (Group File Directories)
        """
        if cache is None:
            cache = self.fs.new_cache()
        for group, dcn in enumerate(self.gfd_pointer_map):
            if dcn != 0 and (ppn is None or ppn.group == PPN.ANY_GROUP or ppn.group == group):
                yield GFD.read(self, group, cache)

    def iterdir(
        self, ppn: t.Optional[PPN] = None, cache: t.Optional[RTFSBlockCache] = None
    ) -> t.Iterator["UserFileDirectory"]:
        """
        Read MFD name entries
        http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 20
        """
        if cache is None:
            cache = self.fs.new_cache()
        for gfd in self.read_gfds(ppn=ppn, cache=cache):
            yield from gfd.iterdir(cache=cache)


class RDS0RootDirectory(XFERXPath):
    fs: "RSTSFilesystem"

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
    def parent(self) -> XFERXPath:
        """The logical parent"""
        return self

    def iterdir(
        self, ppn: t.Optional[PPN] = None, cache: t.Optional[RTFSBlockCache] = None
    ) -> t.Iterator["UserFileDirectory"]:
        """
        Read MFD name entries
        http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 20
        """
        if cache is None:
            cache = self.fs.new_cache()
        link = self.fs.mfd_first_name_entry
        while not link.is_null:
            buffer = cache.read_block(link.to_lbn(self.fs.mfd_cluster_map))
            mfd_entry = UserFileDirectory.read(self.fs, buffer, MFD_ENTRY_LEN * link.entry)
            link = mfd_entry.ulnk
            if mfd_entry.is_mfd_name_entry:
                match = True
                if ppn is not None:
                    if ppn.group != PPN.ANY_GROUP:
                        match &= mfd_entry.ppn.group == ppn.group
                    if ppn.user != PPN.ANY_USER:
                        match &= mfd_entry.ppn.user == ppn.user
                if match:
                    yield mfd_entry


class StorageAllocationTable:
    """
    Storage Allocation Table

    Pag 136
    https://bitsavers.org/pdf/dec/pdp11/rsts_e/V08/AA-CL35A-TE_8.0intern_Sep84.pdf
    """

    fs: "RSTSFilesystem"
    blocks: t.List[int]  # Bitmap DNCs
    num_of_pack_clusters: int  # Number of pack clusters
    num_of_words: int  # Number of words in each bitmap block
    bitmaps: t.List[int]

    def __init__(self, fs: "RSTSFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "RSTSFilesystem") -> "StorageAllocationTable":
        """
        Read the bitmap blocks
        """
        self = StorageAllocationTable(fs)
        satt_sys: UFDNameEntry = fs.get_file_entry(SAT_FILENAME)  # type: ignore

        # Check SAT size
        self.num_of_pack_clusters = (fs.device_size - fs.device_cluster_size) // fs.pack_cluster_size
        satt_size = ((self.num_of_pack_clusters + (BLOCK_SIZE * 8) - 1) & -(BLOCK_SIZE * 8)) // BLOCK_SIZE // 8
        if satt_sys.get_length() != satt_size:
            raise OSError(errno.EIO, f"Invalid {SAT_FILENAME} size, expected size is {satt_size} blocks")

        cache = fs.new_cache()
        self.blocks = satt_sys.get_blocks(cache=cache)
        self.bitmaps = []
        for dcn in self.blocks:
            words = cache.read_words_block(self.fs.dcn_to_lbn(dcn))
            if not words:
                raise OSError(errno.EIO, f"Failed to read block {dcn}")
            self.bitmaps.extend(words)
        return self

    # @classmethod
    # def new(cls, fs: "DOS11Filesystem", first_bitmap_block: int) -> "DOS11Bitmap":
    #     """
    #     Create a new bitmap
    #     """
    #     self = DOS11Bitmap(fs)
    #     number_of_blocks = self.fs.get_size() // BLOCK_SIZE
    #     total_num_of_words = math.ceil(number_of_blocks / 16)
    #     if total_num_of_words <= MAX_WORDS_PER_BITMAP:
    #         self.num_of_words = total_num_of_words  # Number of words in each bitmap block
    #         bitmap_blocks = 1  # Only one bitmap block needed
    #     else:
    #         self.num_of_words = MAX_WORDS_PER_BITMAP  # Maximum number of words in a bitmap block
    #         bitmap_blocks = math.ceil(total_num_of_words / self.num_of_words)
    #     self.blocks = list(range(first_bitmap_block, first_bitmap_block + bitmap_blocks))
    #     self.bitmaps = [0] * self.num_of_words * bitmap_blocks
    #     # Mark the bitmap blocks as used
    #     self.set_used(0)  # Block 0 is always used
    #     self.set_used(self.fs.mfd_block1)  # MFD block 1
    #     self.set_used(self.fs.mfd_block2)  # MFD block 2
    #     for block in self.blocks:
    #         self.set_used(block)
    #     for block in range(number_of_blocks, self.total_bits):
    #         self.set_used(block)
    #     self.write()
    #     return self
    #
    # def write(self) -> None:
    #     """
    #     Write the bitmap blocks
    #     """
    #     for bitmap_num in range(0, len(self.blocks)):
    #         next_block = self.blocks[bitmap_num + 1] if bitmap_num < len(self.blocks) - 1 else 0
    #         words = [
    #             next_block,  #        1 word  Next bitmap block number
    #             bitmap_num + 1,  #    1 word  Map block number
    #             self.num_of_words,  # 1 word  Number of words of map
    #             self.blocks[0],  #    1 word  First bitmap block number
    #         ]
    #         words += self.bitmaps[bitmap_num * self.num_of_words : (bitmap_num + 1) * self.num_of_words]
    #         words = pad_words(words, WORDS_PER_BLOCK)  # Fill the rest with zeros
    #         self.fs.write_words_block(self.blocks[bitmap_num], words)
    #
    @property
    def total_bits(self) -> int:
        """
        Return the bitmap length in bit
        """
        return self.num_of_pack_clusters

    def is_free(self, bit_index: int) -> bool:
        """
        Check if a block is free
        """
        int_index = bit_index // 16
        bit_position = bit_index % 16
        bit_value = self.bitmaps[int_index]
        return (bit_value & (1 << bit_position)) == 0

    def set_used(self, bit_index: int) -> None:
        """
        Mark a block as used
        """
        int_index = bit_index // 16
        bit_position = bit_index % 16
        self.bitmaps[int_index] |= 1 << bit_position

    def set_free(self, bit_index: int) -> None:
        """
        Mark a block as free
        """
        int_index = bit_index // 16
        bit_position = bit_index % 16
        self.bitmaps[int_index] &= ~(1 << bit_position)

    def find_contiguous_blocks(self, size: int) -> int:
        """
        Find contiguous blocks, return the first block number
        """
        current_run = 0
        start_index = -1
        for i in range(self.total_bits - 1, -1, -1):
            if self.is_free(i):
                if current_run == 0:
                    start_index = i
                current_run += 1
                if current_run == size:
                    return start_index - size + 1
            else:
                current_run = 0
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    def allocate(self, size: int, contiguous: bool = False) -> t.List[int]:
        """
        Allocate contiguous or sparse blocks
        """
        blocks = []
        if contiguous and size != 1:
            start_block = self.find_contiguous_blocks(size)
            for block in range(start_block, start_block + size):
                self.set_used(block)
                blocks.append(block)
        else:
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
        used = 0
        for block in self.bitmaps:
            used += block.bit_count()
        return used

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        return len(self.bitmaps) * 16 - self.used()

    def __str__(self) -> str:
        free = self.free()
        used = self.used()
        return f"Free blocks: {free:<6} Used blocks: {used:<6}"


class RSTSFilesystem(AbstractRXBlockFilesystem):
    """
    RSTS/E Filesystem

    RDS0 directory structure:

    MFD Label ---> MFD Name ---> MFD Name --> ...
                      |
                      |
                      .
                  UFD Label ---> UFD Name --> UFD Name --> ...
                                    |
                                    v
                               UFD Account
                                    |
                                    v
                                Retrieval --> Retrieval --> ...

    RDS1.x directory structure:

    MFD Label ---> MFD
                    |
                    +---> GFD ---> UFD Name --> UFD Name --> ...
                    |      |
                    |      +----> UFD Name --> UFD Name --> ...
                    |      |         |
                    |      |         v
                    |      |    UFD Account
                    |      |         |
                    |      |         v
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
    fs_platforms = ["pdp11"]
    fs_entry_metadata = [
        "uprot",
        "ustat",
        "creation_date",
    ]

    ppn: t.Optional[PPN]  # Current Project Programmer Number

    device_size: int  # Device size in blocks
    device_cluster_size: int  # Device Cluster Size
    revision: int  # Revision level (RDS1.1)
    pack_cluster_size: int  # Pack cluster size
    pack_status: int  # Pack status
    pack_id: str  # Pack ID
    structure_level: str  # RDS level
    mfd_cluster_size: int  # MFD cluster size
    mfd_cluster_map: t.List[int]  # MFD cluster map (DCN of MFD clusters 0 - 6)
    mfd_first_name_entry: Link  # Link to first name entry in MFD (RDS0)
    mfd: t.Optional[MFD] = None  # Master File Directory (RDS1.1 or later)

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        strict: t.Union[bool, str] = True,
        **kwargs: t.Union[bool, str],
    ) -> "RSTSFilesystem":
        self = cls(file_or_dev)
        self.read_disk_pack_label()
        self.ppn = None
        if strict:
            # Check if the SATT.SYS file exists
            try:
                self.get_file_entry(SAT_FILENAME)
            except FileNotFoundError:
                raise OSError(errno.EIO, "SATT.SYS file not found")
        return self

    def new_cache(self) -> "RTFSBlockCache":
        return RTFSBlockCache(self)

    def compute_device_cluster_size(self) -> int:
        """
        Compute Device Cluster Size (DCS)
        The Device Cluster is the smallest allocation unit on the device.
        The DCS is calculated such that all clusters on the disk
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
        return dcn * self.device_cluster_size

    def read_disk_pack_label(self) -> None:
        """Read disk pack label"""
        self.device_size = self.get_size() // BLOCK_SIZE
        self.device_cluster_size = self.compute_device_cluster_size()

        # Read the Disk Pack Label block
        words = self.read_words_block(self.dcn_to_lbn(DISK_PACK_LABEL_DCN))
        self.mfd_first_name_entry = Link(self, words[0])  # Link to first name entry in MFD (RDS0)
        assert words[1] == 65535  # contains 65535 (-1) to show that this entry is in use
        mfd_dcn = words[2]  # Device Cluster Number of MFD (RDS1.1)
        self.revision = words[3]  # Revision level (RDS1.1)
        self.pack_cluster_size = words[4]  # Pack cluster size
        self.pack_status = words[5]  # Pack status
        self.pack_id = rad50_word_to_asc(words[6]) + rad50_word_to_asc(words[7])  # Pack ID first and second word

        if self.pack_status & RDS1_FLAGS:  # RDS1.x
            if self.revision == RDS11_PLVL:
                self.structure_level = "RDS1.1"
            else:
                self.structure_level = "RDS1.2"
            self.mfd = MFD.read(self, mfd_dcn)
        else:  # RDS0
            self.revision = RDS0_PLVL  # Set RDS level to 0
            self.structure_level = "RDS0"
            self.read_mfd_cluster_map(words)

    def read_mfd_cluster_map(self, words: t.List[int]) -> None:
        """
            Read MFD cluster map.
            The MFD cluster map contains pointer to each cluster in the MFD.

        Word
            +-------------------------------------+
         0  |          MFD cluster size           |
            +-------------------------------------+
         1  |       DCN of MFD cluster 0          |
            +-------------------------------------+
            |                 ...                 |
            +-------------------------------------+
         7  |       DCN of MFD cluster 6          |
            +-------------------------------------+

            http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 22
            https://bitsavers.org/pdf/dec/pdp11/rsts_e/V08/AA-CL35A-TE_8.0intern_Sep84.pdf Pag 111

        """
        blockette = words[FDCM_BLOCKETTE * BLOCKETTE_WORDS : (FDCM_BLOCKETTE + 1) * BLOCKETTE_WORDS]
        self.mfd_cluster_size = blockette[0]
        self.mfd_cluster_map = list(blockette[1:])

    def read_ufd_cluster_map(self, words: t.List[int]) -> t.List[int]:
        """
            Read UFD cluster map.
            The UFD cluster map contains pointer to each cluster in the UFD.

        Word
            +-------------------------------------+
         0  |          UFD cluster size           |
            +-------------------------------------+
         1  |       DCN of UFD cluster 0          |
            +-------------------------------------+
            |                 ...                 |
            +-------------------------------------+
         7  |       DCN of UFD cluster 6          |
            +-------------------------------------+

            http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf Pag 38

        """
        blockette = words[FDCM_BLOCKETTE * BLOCKETTE_WORDS : (FDCM_BLOCKETTE + 1) * BLOCKETTE_WORDS]
        return list(blockette[1:])

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
        words = cache.read_words_block(link.block + ufd_uar)
        ufd_cluster_map = self.read_ufd_cluster_map(words)
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
        buffer = self.read_block(self.dcn_to_lbn(dcn))
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
        buffer: t.Union[bytes, bytearray],
        block_number: int,
        number_of_blocks: int = 1,
        dcn: t.Optional[int] = None,
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def read_words_block(
        self,
        block_number: int = 0,
    ) -> t.List[int]:
        """
        Read a 512 bytes block as 256 16bit words
        """
        data = self.read_block(block_number)
        if not data:
            raise OSError(errno.EIO, f"Failed to read block {block_number}")
        return list(struct.unpack_from("<256H", data))

    def write_words_block(
        self,
        block_number: int,
        words: t.List[int],
    ) -> None:
        """
        Write 256 16bit words as a 512 bytes block
        """
        data = struct.pack("<256H", *words)
        self.write_block(data, block_number)

    def read_bitmap(self) -> StorageAllocationTable:
        bitmap = StorageAllocationTable.read(self)
        return bitmap

    def read_dir_entries(self, ppn: t.Optional[PPN], cache: t.Optional[RTFSBlockCache] = None) -> t.Iterator[RSTSPath]:
        if cache is None:
            cache = self.new_cache()
        if ppn is None:
            yield from self.get_root().iterdir(cache=cache)
        elif self.mfd is not None:  # RDS1.x
            for gfd in self.mfd.read_gfds(ppn=ppn, cache=cache):
                yield from gfd.read_dir_entries(ppn=ppn, cache=cache)
        else:  # RDS0
            for mfd_entry in self.get_root().iterdir(ppn=ppn, cache=cache):
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
    ) -> t.Iterator[RSTSPath]:
        if ppn is None:
            ppn = self.ppn
        ppn, pattern = rsts_split_fullname(fullname=pattern, wildcard=wildcard, ppn=ppn)
        for entry in self.read_dir_entries(ppn=ppn):
            if filename_match(entry.name, pattern, wildcard):
                yield entry

    @property
    def entries_list(self) -> t.Iterator[RSTSPath]:
        yield from self.read_dir_entries(ppn=self.ppn)

    def get_file_entry(self, fullname: str) -> RSTSPath:
        fullname = rsts_canonical_fullname(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        ppn, name = rsts_split_fullname(fullname=fullname, wildcard=False, ppn=self.ppn)
        if ppn is None:
            raise FileNotFoundError(errno.ENOENT, "No PPN specified")
        if name is None:
            return self.get_ufd(ppn)
        else:
            try:
                return next(self.filter_entries_list(name, wildcard=False, ppn=ppn))
            except StopIteration:
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def get_ufd(self, ppn: PPN) -> UserFileDirectory:
        """
        Get the User File Directory for a given PPN
        """
        try:
            return next(self.read_dir_entries(ppn=ppn))  # type: ignore
        except StopIteration:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), ppn)

    def get_root(self) -> t.Union[RDS0RootDirectory, MFD]:
        if self.mfd is not None:
            return self.mfd
        else:
            return RDS0RootDirectory(self)

    def show_accounts(self, volume_id: str, options: t.Dict[str, bool]) -> None:
        """
        Listing of all PPN
        """
        for mfd_entry in self.get_root().iterdir():
            sys.stdout.write(f"{mfd_entry.ppn}\n")
        return

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List directory entries
        """
        files = 0
        blocks = 0
        ppn, pattern = rsts_split_fullname(fullname=pattern, wildcard=True, ppn=self.ppn)
        if ppn is None:
            self.show_accounts(volume_id, options)
            return
        sys.stdout.write(f" Name .Ext  Size    Prot   Date       {volume_id}:{ppn}\n")
        for x in self.filter_entries_list(pattern, ppn=ppn, include_all=True, wildcard=True):
            if isinstance(x, UFDNameEntry):
                dt = x.creation_date.strftime("%d-%b-%y %H:%M")
                sys.stdout.write(f"{x.stem:<6}.{x.extension:<3} {x.account_entry.usiz:>5}   <{x.uprot:>3}> {dt}\n")
                blocks += x.account_entry.usiz
                files += 1
        sys.stdout.write("\n")
        sys.stdout.write(f" Total of {blocks} blocks in {files} files in {volume_id}:{ppn}\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        ppn = None
        if options.get("bitmap"):
            # Display the bitmap
            bitmap = self.read_bitmap()
            for i in range(0, bitmap.total_bits):
                sys.stdout.write(f"{i:>4d} {'[ ]' if bitmap.is_free(i) else '[X]'}  ")
                if i % 16 == 15:
                    sys.stdout.write("\n")
            sys.stdout.write(f"\nUsed blocks: {bitmap.used()}\n")
        elif arg and "[" in arg:
            try:
                ppn = PPN.from_str(arg)
                arg = arg.split("]", 1)[1]
            except Exception:
                return
        if ppn is not None:
            for mfd_entry in self.get_root().iterdir():
                match = True
                if ppn.group != PPN.ANY_GROUP:
                    match &= mfd_entry.ppn.group == ppn.group
                if ppn.user != PPN.ANY_USER:
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
            sys.stdout.write("Disk Pack Label\n")
            sys.stdout.write("---------------\n\n")
            sys.stdout.write(dump_struct(self.__dict__))
            sys.stdout.write("\n\nMFD Cluster Map\n")
            sys.stdout.write("---------------\n\n")
            for i, dcn in enumerate(self.mfd_cluster_map):
                if dcn:
                    sys.stdout.write(f"Cluster {i} -> DCN {dcn}\n")
            if self.mfd is not None:
                sys.stdout.write("\nGFD Pointer Map\n")
                sys.stdout.write("---------------\n\n")
                for i, dcn in enumerate(self.mfd.gfd_pointer_map):
                    if dcn:
                        sys.stdout.write(f"Cluster {i} -> DCN {dcn}\n")
            # Listing of all PPN
            sys.stdout.write("\nProject Programmer Numbers\n")
            sys.stdout.write("--------------------------\n\n")
            for mfd_entry in self.get_root().iterdir():
                sys.stdout.write(f"{mfd_entry}\n")

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        """
        Change the current Project Programmer Number
        """
        if entry is None:
            self.uic = None
        else:
            assert isinstance(entry, UserFileDirectory)
            self.ppn = entry.ppn

    def get_pwd(self) -> str:
        """
        Get the current Project Programmer Number
        """
        return str(self.ppn) if self.ppn is not None else ""

    def path_join(self, path: str, *paths: str) -> str:
        """
        Join PPN and filename
        """
        paths = [x for x in paths if x]  # type: ignore
        if not paths:
            return path
        try:
            ppn = PPN.from_str(path)
        except Exception:
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
        if len(paths) > 1:
            raise OSError(errno.EINVAL, "Can only join PPN and filename")
        return f"{ppn}{paths[0]}"
