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
import struct
import sys
import typing as t
from dataclasses import dataclass
from datetime import date, datetime

from ..abstract import AbstractDirectoryEntry, AbstractFile, AbstractFilesystem
from ..block import BlockDevice
from ..commons import (
    BLOCK_SIZE,
    READ_FILE_FULL,
    bytes_to_word,
    dump_struct,
    filename_match,
    swap_words,
)
from ..uic import ANY_GROUP, ANY_USER, DEFAULT_UIC, UIC
from .dos11fs import dos11_split_fullname
from .rad50 import asc2rad, rad2asc, rad50_word_to_asc

__all__ = [
    "Files11File",
    "Files11DirectoryEntry",
    "Files11Filesystem",
]

HOME_BLOCK = 1  # Home block
INDEXF_SYS = 1  # The index file is the root of the Files-11
BITMAP_SYS = 2  # Storage bitmap file
BADBLK_SYS = 3  # Bad block file
MFD_DIR = 4  # Volume master file directory (000000.DIR)
UC_CNB = 128  # Contiguous as possible flag
SC_DIR = 0x80  # File is a directory

MFD_UIC = UIC.from_str("[0,0]")
MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

DIRECTORY_FILE_ENTRY_FORMAT = "<HHHHHHHH"
DIRECTORY_FILE_ENTRY_LEN = 16
HOME_BLOCK_FORMAT = '<HHHHHHH12s4sHHHH6sbbb7sH2sH14s382sI12s12s12s12s2sH'
FILE_HEADER_FORMAT = '<BBHHHHHH32s'
IDENT_AREA_FORMAT = '<HHHHHH7s6s7s6s7sB'
MAP_AREA_FORMAT = '<BBHHBBBB'


def files11_to_date(val: bytes, tim: t.Optional[bytes] = None) -> t.Optional[date]:
    """
    Translate Files-11 date to Python date
    """
    date_str = val.decode("ascii", errors="ignore")
    year = int(date_str[5:7]) + 1900
    month = MONTHS.index(date_str[2:5]) + 1
    day = int(date_str[0:2])
    if tim is not None:
        tim_str = tim.decode("ascii", errors="ignore")
        hour = int(tim_str[0:2])
        minute = int(tim_str[2:4])
        second = int(tim_str[4:6])
    else:
        hour = 0
        minute = 0
    return datetime(year, month, day, hour, minute, second)


def files11_canonical_filename(fullname: t.Optional[str], wildcard: bool = False) -> str:
    """
    Generate the canonical Files-11 name
    """
    fullname = (fullname or "").upper()
    try:
        filename, extension = fullname.split(".", 1)
    except Exception:
        filename = fullname
        extension = "*" if wildcard else ""
    filename = rad2asc(asc2rad(filename[0:3])) + rad2asc(asc2rad(filename[3:6])) + rad2asc(asc2rad(filename[6:9]))
    extension = rad2asc(asc2rad(extension))
    return f"{filename}.{extension}"


def files11_canonical_fullname(fullname: str, wildcard: bool = False) -> str:
    try:
        if "[" in fullname:
            uic: t.Optional[UIC] = UIC.from_str(fullname)
            fullname = fullname.split("]", 1)[1]
        else:
            uic = None
    except Exception:
        uic = None
    if fullname:
        fullname = files11_canonical_filename(fullname, wildcard=wildcard)
    return f"{uic or ''}{fullname}"


@dataclass
class RetrievalPointer:
    """
    The retrieval pointers map the file blocks to volume blocks.
    Each retrieval pointer describes a consecutively numbered
    group of logical blocks which is part of the file.

    Each retrieval pointer maps virtual blocks through (j + count)
    into logical blocks k through (lbn + count), where
    - count - represent a group of (count + 1) logical blocks
    - lbn - logical block number of the first logical block in the group
    - j - is the total number plus one of virtual blocks represented by
      all preceding retrieval points in this and all preceding
      headers of the file.
    """

    j: int
    count: int
    lbn: int

    def __str__(self) -> str:
        return f"[{self.j}:{self.j+self.count}] => [{self.lbn}:{self.lbn + self.count}]"

    def __repr__(self) -> str:
        return str(self)


class Files11File(AbstractFile):
    header: "Files11FileHeader"
    closed: bool

    def __init__(self, header: "Files11FileHeader"):
        self.header = header
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
            number_of_blocks = self.header.length
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.header.length
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        data = bytearray()
        for i in range(block_number, block_number + number_of_blocks):
            lbn = self.header.map_block(i)
            t = self.header.fs.read_block(lbn)
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
            or block_number + number_of_blocks > self.header.length
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        for i in range(block_number, block_number + number_of_blocks):
            lbn = self.header.map_block(i)
            t = buffer[i * BLOCK_SIZE : (i + 1) * BLOCK_SIZE]
            if len(t) < BLOCK_SIZE:
                t = t + bytes([0] * (BLOCK_SIZE - len(t)))  # Pad with zeros
            self.header.fs.write_block(t, lbn)

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.header.length * BLOCK_SIZE

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
        return str(self.header)


class Files11FileHeader:
    """
    Each file on a Files-11 volume is described by a file header.
    The file header is a block that contains all the information
    necessary to access the file.
    """

    fs: "Files11Filesystem"

    idof: int  # Ident Area Offset
    mpof: int  # Map Area Offset
    fnum: int  # File Number
    fseq: int  # File Sequence Number
    flev: int  # File Structure Level
    fown: int  # File Owner UIC
    fpro: int  # File Protection Code
    fcha: int  # File Characteristics
    ufat: bytes  # User Attribute Area
    # Ident Area
    filename: str  # File Name
    extension: str  # File Type
    fver: int  # Version Number
    rvno: int  # Revision Number
    rvdt: bytes  # Revision Date
    rvti: bytes  # Revision Time
    crdt: bytes  # Creation Date
    crti: bytes  # Creation Time
    exdt: bytes  # Expiration Date
    # Map Area
    esqn: int  # Extension Segment Number
    ervn: int  # Extension Relative Volume Number
    efnu: int  #  Extension File Number
    efsq: int  #  Extension File Sequence Number
    ctsz: int  # Block Count Field Size
    lbsz: int  # LBN Field Size
    use: int  # Map Words in Use
    max: int  # Map Words Available
    retrieval_pointers: t.List[RetrievalPointer]  # Retrieval Pointers
    # FCS File Attribute Block Layout
    rtyp: int  # Record Type
    ratt: int  # Record Attributes
    rsiz: int  # Record Size
    hibk: int  # Highest VBN Allocated
    efbk: int  # End of File Block
    ffby: int  # First Free Byte

    def __init__(self, fs: "Files11Filesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "Files11Filesystem", buffer: bytes, position: int = 0) -> "Files11FileHeader":
        self = cls(fs)
        (
            self.idof,  # 1 byte Ident Area Offset
            self.mpof,  # 1 byte Map Area Offset
            self.fnum,  # 1 word File Number
            self.fseq,  # 1 word File Sequence Number
            self.flev,  # 1 word File Structure Level
            self.fown,  # 1 word File Owner UIC
            self.fpro,  # 1 word File Protection Code
            self.fcha,  # 1 word File Characteristics
            self.ufat,  # 32 bytes User Attribute Area
        ) = struct.unpack_from(FILE_HEADER_FORMAT, buffer, position)
        if self.mpof == 0:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), "")
        # print(f"{self.idof=} {self.mpof=} {self.fnum=}")
        # Ident Area
        # It contains identification and accounting data about the file
        (
            fnam0,  #     1 word  File Name
            fnam1,  #     1 word
            fnam2,  #     1 word
            ftyp,  #      1 word  File Type
            self.fver,  # 1 word  Version Number
            self.rvno,  # 1 word  Revision Number
            self.rvdt,  # 7 bytes Revision Date
            self.rvti,  # 6 bytes Revision Time
            self.crdt,  # 7 bytes Creation Date
            self.crti,  # 6 bytes Creation Time
            self.exdt,  # 7 bytes Expiration Date
            _,
        ) = struct.unpack_from(IDENT_AREA_FORMAT, buffer, position + self.idof * 2)
        self.filename = rad50_word_to_asc(fnam0) + rad50_word_to_asc(fnam1) + rad50_word_to_asc(fnam2)
        self.extension = rad50_word_to_asc(ftyp)
        # Map Area
        # It describes the mapping of virtual blocks of the file to the logical blocks of the volume
        (
            self.esqn,  # 1 byte Extension Segment Number
            self.ervn,  # 1 byte Extension Relative Volume Number
            self.efnu,  # 1 word Extension File Number
            self.efsq,  # 1 word Extension File Sequence Number
            self.ctsz,  # 1 byte Block Count Field Size
            self.lbsz,  # 1 byte LBN Field Size
            self.use,  #  1 byte Map Words in Use
            self.max,  #  1 byte Map Words Available
        ) = struct.unpack_from(MAP_AREA_FORMAT, buffer, position + self.mpof * 2)
        # FCS File Attribute Block
        FCS_FORMAT = "<BBHIIH"
        (
            self.rtyp,  #  1 byte Record Type
            self.ratt,  #  1 byte Record Attributes
            self.rsiz,  #  1 word Record Size
            self.hibk,  #  1 long Highest VBN Allocated
            self.efbk,  #  1 long End of File Block
            self.ffby,  #  1 word First Free Byte
        ) = struct.unpack_from(FCS_FORMAT, self.ufat, 0)
        self.hibk = swap_words(self.hibk)
        self.efbk = swap_words(self.efbk)
        self.parse_map(buffer, position)
        return self

    def parse_map(self, buffer: bytes, position: int = 0) -> None:
        """
        Load the retrieval pointers into self.map
        """
        rtrv = position + self.mpof * 2 + 10
        j = 0
        self.map_length = 0
        self.retrieval_pointers = []
        for i in range(rtrv, rtrv + self.use * 2, 4):
            if self.ctsz == 1 and self.lbsz == 3:  # Format 1
                # Byte 1 contains the high order bits of LBN
                high_lbn = buffer[i]
                # Byte 2 contains the count field
                count = buffer[i + 1] + 1
                # Bytes 3 and 4 contain the low 16 bits of the LBN
                low_lbn = bytes_to_word(buffer[i + 2 : i + 4])
                lbn = (high_lbn << 16) + low_lbn
                self.retrieval_pointers.append(RetrievalPointer(j, count, lbn))
                j = j + count
            else:
                print(self)
                print(self.ctsz, self.lbsz)
                assert False
        self.map_length = j

    def map_block(self, block_number: int) -> int:
        block_number = block_number
        for rp in self.retrieval_pointers:
            if block_number < rp.j + rp.count:
                return rp.lbn - rp.j + block_number
        raise OSError(errno.EIO, os.strerror(errno.EIO))

    @property
    def basename(self) -> str:
        return f"{self.filename}.{self.extension}"

    @property
    def isdir(self) -> bool:
        return bool(self.fcha & SC_DIR) or (self.fnum == MFD_DIR)

    @property
    def length(self) -> int:
        return self.map_length

    @property
    def uic(self) -> UIC:
        return UIC.from_word(self.fown)

    def __str__(self) -> str:
        return f"{self.fnum:>5},{self.fseq:<5} {str(self.uic):9s} {self.basename:>14};{self.fver}  FCHA: {self.fcha:04x}  RTYP: {self.rtyp:1}  Length: {self.length:9}"

    def __repr__(self) -> str:
        return str(self.__dict__)


class Files11DirectoryEntry(AbstractDirectoryEntry):
    """
    Each directory entry contains the following:

    File ID  The File ID of the file that this directory entry represents.
    Name     The name of the file, up to 9 characters.
    Type     The type of the file, up to 3 characters.
    Version  The version number of the file
    """

    fs: "Files11Filesystem"
    _header: t.Optional["Files11FileHeader"]

    fnum: int  # File Number
    fseq: int  # File Sequence Number
    fvol: int  # Relative Volume Number
    filename: str  # File Name
    extension: str  # File Type
    fver: int  # Version Number
    uic: UIC = DEFAULT_UIC

    def __init__(self, fs: "Files11Filesystem", uic: UIC):
        self.fs = fs
        self.uic = uic
        self._header = None

    @classmethod
    def read(cls, fs: "Files11Filesystem", buffer: bytes, position: int, uic: UIC) -> "Files11DirectoryEntry":
        self = cls(fs, uic)
        (
            self.fnum,  # 1 word File Number
            self.fseq,  # 1 word File Sequence Number
            self.fvol,  # 1 word Relative Volume Number
            fnam0,  #     1 word File Name
            fnam1,  #     1 word
            fnam2,  #     1 word
            ftyp,  #      1 word File Type
            self.fver,  # 1 word File Version
        ) = struct.unpack_from(DIRECTORY_FILE_ENTRY_FORMAT, buffer, position)
        self.filename = rad50_word_to_asc(fnam0) + rad50_word_to_asc(fnam1) + rad50_word_to_asc(fnam2)
        self.extension = rad50_word_to_asc(ftyp)
        return self

    @property
    def header(self) -> "Files11FileHeader":
        if self._header is None:
            self._header = self.fs.read_file_header(self.fnum)
        return self._header

    @property
    def file_id(self) -> str:
        """Each file in a volume set is uniquely identified by a File ID"""
        return f"{self.fnum},{self.fseq},{self.fvol}"

    @property
    def is_empty(self) -> bool:
        """
        If the file number of the File ID field is zero,
        then this record is empty.
        """
        return self.fnum == 0

    @property
    def fullname(self) -> str:
        return f"{self.uic or ''}{self.filename}.{self.extension}"

    @property
    def basename(self) -> str:
        return f"{self.filename}.{self.extension}"

    @property
    def creation_date(self) -> t.Optional[date]:
        return files11_to_date(self.header.crdt, self.header.crti)

    def get_length(self) -> int:
        """
        Get the length in blocks
        """
        return self.header.length

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

    def open(self, file_mode: t.Optional[str] = None) -> Files11File:
        """
        Open a file
        """
        return Files11File(self.header)

    def __str__(self) -> str:
        return f"File ID {'(' + self.file_id + ')':16} Name: {self.basename:12} Ver: {self.fver} FCHA: {self.header.fcha:04x} RTYP: {self.header.rtyp:1} Length: {self.header.length:9}"


class Files11Filesystem(AbstractFilesystem, BlockDevice):
    """
    Files-11 Filesystem
    """

    fs_name = "files11"
    fs_description = "PDP-11 Files-11"

    uic: UIC  # current User Identification Code

    ibsz: int  #     2 bytes  Index File Bitmap Size
    iblb: int  #     4 bytes  Index File Bitmap LBN
    fmax: int  #     2 bytes  Maximum Number of Files
    sbcl: int  #     2 bytes  Storage Bitmap Cluster Factor
    dvty: int  #     2 bytes  Disk Device Type
    vlev: int  #     2 bytes  Volume Structure Level
    vnam: bytes  #  12 bytes  Volume Name
    vown: int  #     2 bytes  Volume Owner UIC
    vpro: int  #     2 bytes  Volume Protection Code
    vcha: int  #     2 bytes  Volume Characteristics
    dfpr: int  #     2 bytes  Default File Protection
    wisz: int  #     1 byte   Default Window Size
    fiex: int  #     1 byte   Default File Extend
    lruc: int  #     1 byte   Directory Pre-Access Limit
    revd: int  #     7 bytes  Date of Last Home Block Revision
    revc: int  #     2 bytes  Count of Home Block Revisions
    chk1: int  #     2 bytes  First Checksum
    vdat: bytes  #  14 bytes  Volume Creation Date
    pksr: int  #     4 bytes  Pack Serial Number
    indn: bytes  #  12 bytes  Volume Name
    indo: bytes  #  12 bytes  Volume Owner
    indf: bytes  #  12 bytes  Format Type  - DECFILE11A
    chk2: int  #     2 bytes  Second Checksum

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        self.uic = DEFAULT_UIC
        self.read_home()
        if strict:
            # Check the bitmap size
            if not self.ibsz:
                raise OSError(errno.EIO, os.strerror(errno.EIO))
            # Check the volume structure level
            # if self.vlev not in (0o401, 0o402):
            #     raise OSError(errno.EIO, os.strerror(errno.EIO))
            # Check the index file
            indexfs = self.read_file_header(INDEXF_SYS)
            if indexfs.fnum != INDEXF_SYS:
                raise OSError(errno.EIO, os.strerror(errno.EIO))
        return self

    def read_home(self) -> None:
        """Read home block"""
        t = self.read_block(HOME_BLOCK)
        (
            self.ibsz,  #   2 bytes  Index File Bitmap Size
            iblb_h,  #      2 bytes  Index File Bitmap LBN (high order)
            iblb_l,  #      2 bytes  Index File Bitmap LBN (low order)
            self.fmax,  #   2 bytes  Maximum Number of Files
            self.sbcl,  #   2 bytes  Storage Bitmap Cluster Factor
            self.dvty,  #   2 bytes  Disk Device Type
            self.vlev,  #   2 bytes  Volume Structure Level
            self.vnam,  #  12 bytes  Volume Name
            _,  #           4 bytes  Unused
            self.vown,  #   2 bytes  Volume Owner UIC
            self.vpro,  #   2 bytes  Volume Protection Code
            self.vcha,  #   2 bytes  Volume Characteristics
            self.dfpr,  #   2 bytes  Default File Protection
            _,  #           6 bytes  Unused
            self.wisz,  #   1 byte   Default Window Size
            self.fiex,  #   1 byte   Default File Extend
            self.lruc,  #   1 byte   Directory Pre-Access Limit
            self.revd,  #   7 bytes  Date of Last Home Block Revision
            self.revc,  #   2 bytes  Count of Home Block Revisions
            _,  #           2 bytes  Unused
            self.chk1,  #   2 bytes  First Checksum
            self.vdat,  #  14 bytes  Volume Creation Date
            _,  #         382 bytes  Unused
            self.pksr,  #   4 bytes  Pack Serial Number
            _,  #          12 bytes  Unused
            self.indn,  #  12 bytes  Volume Name
            self.indo,  #  12 bytes  Volume Owner
            self.indf,  #  12 bytes  Format Type  - DECFILE11A
            _,  #           2 bytes  Unused
            self.chk2,  #   2 bytes  Second Checksum
        ) = struct.unpack(HOME_BLOCK_FORMAT, t)
        self.uic = UIC.from_word(self.vown)
        self.iblb = (iblb_h << 16) + iblb_l
        # print(f"{self.ibsz=} {self.iblb=}")

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def read_file_header(self, file_number: int) -> Files11FileHeader:
        """
        Read file header by file number
        """
        if file_number <= 16:
            # The first 16 file headers are logically contiguous
            # with the index file bitmap
            block_number = self.iblb + file_number
        else:
            # The other files must be located through the mapping
            # data in the index file header
            indexfs = self.read_file_header(INDEXF_SYS)
            block_number = indexfs.map_block(file_number)
        buffer = self.read_block(block_number)
        file_header = Files11FileHeader.read(self, buffer)
        assert file_header.flev == 0o401
        return file_header

    def read_directory(self, file_number: int, uic: UIC) -> t.Iterator["Files11DirectoryEntry"]:
        """
        Read directory by file number
        """
        header = self.read_file_header(file_number)
        try:
            f = Files11File(header)
            buffer = f.read_block(0, READ_FILE_FULL)
            for pos in range(0, len(buffer), DIRECTORY_FILE_ENTRY_LEN):
                entry = Files11DirectoryEntry.read(self, buffer, position=pos, uic=uic)
                if not entry.is_empty:
                    yield entry
        finally:
            f.close()

    def read_dir_entries(self, uic: UIC) -> t.Iterator["Files11DirectoryEntry"]:
        if uic == MFD_UIC:
            # Master File Directory
            yield from self.read_directory(MFD_DIR, MFD_UIC)
        elif not uic.has_wildcard:
            # Get UIC directory file number
            uic_dir = f"{uic.group:03o}{uic.user:03o}.DIR"
            uic_dir_entry = None
            for entry in self.read_directory(MFD_DIR, MFD_UIC):
                if entry.basename == uic_dir:
                    uic_dir_entry = entry
            if uic_dir_entry is None:
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(uic))
            yield from self.read_directory(uic_dir_entry.fnum, uic=uic)
        else:
            # Filter directories
            g = f"{uic.group:03o}" if uic.group != ANY_GROUP else None
            u = f"{uic.user:03o}" if uic.user != ANY_USER else None
            uic_dir_entry = None
            for entry in self.read_directory(MFD_DIR, MFD_UIC):
                if (
                    (entry.header.isdir)
                    and (g is None or entry.filename[0:3] == g)
                    and (u is None or entry.filename[3:6] == u)
                ):
                    uic_dir_entry = entry
                    dir_file_number = uic_dir_entry.fnum
                    yield from self.read_directory(dir_file_number, uic=uic)
            if uic_dir_entry is None:
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(uic))

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
        uic: t.Optional[UIC] = None,
    ) -> t.Iterator["Files11DirectoryEntry"]:
        if uic is None:
            uic = self.uic
        uic, pattern = dos11_split_fullname(fullname=pattern, wildcard=wildcard, uic=uic)
        for entry in self.read_dir_entries(uic=uic):
            if filename_match(entry.basename, pattern, wildcard) and not entry.is_empty:
                yield entry

    @property
    def entries_list(self) -> t.Iterator["Files11DirectoryEntry"]:
        for entry in self.read_dir_entries(uic=self.uic):
            yield entry

    def get_file_entry(self, fullname: str) -> Files11DirectoryEntry:
        fullname = files11_canonical_fullname(fullname)
        if not fullname:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        uic, basename = dos11_split_fullname(fullname=fullname, wildcard=False, uic=self.uic)
        try:
            return next(self.filter_entries_list(basename, wildcard=False, uic=uic))
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
    ) -> t.Optional[Files11DirectoryEntry]:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        if options.get("uic"):
            # Listing of all UIC
            pattern = "[0,0]*.DIR"
        files = 0
        blocks = 0
        allocated = 0
        uic, pattern = dos11_split_fullname(fullname=pattern, wildcard=True, uic=self.uic)
        if not options.get("brief"):
            dt = datetime.today().strftime('%y-%b-%d %H:%M').upper()
            sys.stdout.write(f"DIRECTORY {volume_id}:{uic}\n{dt}\n\n")
        for x in self.filter_entries_list(pattern, uic=uic, include_all=True, wildcard=True):
            if x.is_empty:
                continue
            fullname = f"{x.filename}.{x.extension};{x.fver}"
            if options.get("brief"):
                # Lists only file names and file types
                sys.stdout.write(f"{fullname}\n")
                continue
            date = x.creation_date and x.creation_date.strftime("%d-%b-%y %H:%M").upper() or ""
            attr = "C" if UC_CNB & x.header.fcha else ""
            length = f"{x.header.length}."
            sys.stdout.write(f"{fullname:<19s} {length:<7s} {attr:1}  {date:>9s}\n")
            blocks += x.header.length
            allocated += x.header.length
            files += 1
        if options.get("brief"):
            return
        sys.stdout.write("\n")
        sys.stdout.write(f"TOTAL OF {blocks}./{allocated}. BLOCKS IN {files}. FILES\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        uic = None
        if arg and "[" in arg:
            try:
                uic = UIC.from_str(arg)
                arg = arg.split("]", 1)[1]
            except Exception:
                return
        if arg:
            self.dump(arg)
        elif uic is not None:
            for entry in self.read_dir_entries(uic):
                sys.stdout.write(f"{entry}\n")
        else:
            sys.stdout.write("Home Block\n\n")
            sys.stdout.write(dump_struct(self.__dict__))
            indexfs = self.read_file_header(INDEXF_SYS)
            sys.stdout.write("\n\nINDEXF.SYS Header\n\n")
            sys.stdout.write(dump_struct(indexfs.__dict__))
            f = Files11File(indexfs)
            sys.stdout.write(f"\n\nINDEXF.SYS {f.header.length}\n\n")
            for i in range(1, f.header.length):
                try:
                    h = self.read_file_header(i)
                    sys.stdout.write(f"{h}\n")
                except FileNotFoundError:
                    pass
            f.close()

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
        Change the current User Identification Code
        """
        try:
            self.uic = UIC.from_str(fullname)
            return True
        except Exception:
            return False

    def get_pwd(self) -> str:
        return str(self.uic)
