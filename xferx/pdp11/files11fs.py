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
import re
import struct
import sys
import typing as t
from abc import abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..abstract import XFERXPath, AbstractFile
from ..commons import (
    BLOCK_SIZE,
    READ_FILE_FULL,
    bytes_to_word,
    dump_struct,
    filename_match,
    swap_words,
)
from ..device.abstract import AbstractDevice
from ..uic import DEFAULT_UIC, UIC
from .abstract import AbstractRXBlockFilesystem
from .rad50 import asc2rad, rad2asc, rad50_word_to_asc

__all__ = [
    "Files11File",
    "Files11Path",
    "Files11Filesystem",
]

HOME_BLOCK = 1  # Home block
INDEXF_SYS = 1  # File ID 1,1,0 - Index file
BITMAP_SYS = 2  # File ID 2,2,0 - Storage bitmap file
BADBLK_SYS = 3  # File ID 3,3,0 - Bad block file
MFD_DIR = 4  #    File ID 4,4,0 - Volume Master File Directory (000000.DIR)
CORIMG_SYS = 5  # File ID 5,5,0 - System core image fileS
VOLSET_SYS = 6  # File ID 6,6,0 - Volume set list file (ODS-2 and later)
CONTIN_SYS = 7  # File ID 7,7,0 - Standard continuation file (ODS-2 and later)
BACKUP_SYS = 8  # File ID 8,8,0 - Backup journal file (ODS-2 and later)
BADLOG_SYS = 9  # File ID 9,9,0 - Pending bad block log file (ODS-2 and later)

UC_CNB = 128  # Contiguous as possible flag
SC_DIR = 0x80  # File is a directory

MFD_DIR_NAME = "000000"
MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

ODS1_DIRECTORY_FILE_ENTRY_FORMAT = "<HHHHHHHH"
ODS1_DIRECTORY_FILE_ENTRY_LEN = 16
HOME_BLOCK_FORMAT = '<HHHHHHH12s4sHHHH6sbbb7sH2sH14s382sI12s12s12s12s2sH'
FILE_HEADER_FORMAT = '<BBHHHHHH32s'
IDENT_AREA_FORMAT = '<HHHHHH7s6s7s6s7sB'
MAP_AREA_FORMAT = '<BBHHBBBB'


def files11_to_date(val: bytes, tim: t.Optional[bytes] = None) -> t.Optional[date]:
    """
    Translate Files-11 date to Python date
    """
    if len(val) == 8:  # ODS-2 format: 8 bytes little-endian ticks since 1858-11-17
        ticks = int.from_bytes(val, "little")
        if not ticks:
            return None
        try:
            return datetime(1858, 11, 17) + timedelta(microseconds=ticks // 10)
        except OverflowError:
            return None
    else:  # ODS-1 format: 7 bytes ASCII "DDMMMYY"
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


def files11_canonical_filename(fullname: t.Optional[str], wildcard: bool, ods_version: int) -> str:
    """
    Generate the canonical Files-11 name
    """
    fullname = (fullname or "").upper()
    if ods_version != 1:
        return fullname
    else:
        try:
            stem, extension = fullname.split(".", 1)
        except Exception:
            stem = fullname
            extension = "*" if wildcard else ""
        stem = rad2asc(asc2rad(stem[0:3])) + rad2asc(asc2rad(stem[3:6])) + rad2asc(asc2rad(stem[6:9]))
        extension = rad2asc(asc2rad(extension))
        return f"{stem}.{extension}"


def files11_split_version(fullname: t.Optional[str]) -> t.Tuple[t.Optional[str], t.Optional[int]]:
    """
    Split a Files-11 file name into its components: name and version.
    """
    if fullname is None:
        return (None, None)
    try:
        filename, version_str = fullname.rsplit(";", 1)
        version = int(version_str)
        return (filename, version)
    except Exception:
        return (fullname, None)


def files11_split_fullname(
    fullname: t.Optional[str],  # [DIR]FILENAME.EXT;VERSION
    default_path: str,
    wildcard: bool,
    ods_version: int,
) -> t.Tuple[str, t.Optional[str], t.Optional[int]]:  # path, filename, version
    """
    Split a Files-11 full name into its components: path, filename, and version.

    Two Level Directory Hierarchy

    A path of "[n,m]" associates with a directory name of "nnnmmm.DIR;1",
    where nnn and mmm are n and m padded out to three octal digits.

    Multi-level Directory Hierarchy

    A path has the format "[name1.name2.name3]".
    Each name in the list translates to a directory file name of the form "name.DIR;1"
    """
    if fullname is None:
        return default_path, None, None

    # Split the directory specification from the filename.
    match = re.fullmatch(r"(\[[^\]]*\])?(.*?)(?:;(\d+))?", fullname)
    version = None
    if not match:
        fullname = files11_canonical_filename(fullname, wildcard=wildcard, ods_version=ods_version)
        return default_path, fullname, version

    directory, filename, version_str = match.groups()
    try:
        version = int(version_str)
    except Exception:
        version = None

    if wildcard and not filename:
        filename = "*.*"
    if not directory:
        filename = files11_canonical_filename(filename, wildcard=wildcard, ods_version=ods_version)
        return default_path, filename, version

    if filename:
        filename = files11_canonical_filename(filename, wildcard=wildcard, ods_version=ods_version)
    directory = directory[1:-1]  # Remove the enclosing brackets

    # Two-level directory hierarchy: [n,m]
    if "," in directory:
        parts = directory.split(",")
        if len(parts) != 2:
            raise ValueError(f"Invalid two-level directory path: [{directory}]")
        try:
            n, m = (int(part.strip(), 8) for part in parts)
        except ValueError as exc:
            raise ValueError(f"Invalid octal directory path: [{directory}]") from exc
        if not (0 <= n <= 0o777 and 0 <= m <= 0o777):
            raise ValueError(f"Directory components must fit in three octal digits: [{directory}]")
        path = f"{n:03o}{m:03o}"
        return path, filename, version

    # Multi-level directory hierarchy: [name1.name2.name3...]
    else:
        path = directory.upper()
        return path, filename, version


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
        return f"[{self.j}:{self.j + self.count}] => [{self.lbn}:{self.lbn + self.count}]"

    def __repr__(self) -> str:
        return str(self)


class Files11File(AbstractFile):
    header: "Files11FileHeader"

    def __init__(self, header: "Files11FileHeader"):
        self.header = header

    @property
    def name(self) -> str:
        """Final path component"""
        return self.header.name

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
            if lbn < 0:
                data.extend(bytes(BLOCK_SIZE))
            else:
                t = self.header.fs.read_block(lbn)
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

    def __str__(self) -> str:
        return str(self.header)


class Files11FileHeader:
    fs: "Files11Filesystem"
    map_length: int

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
    stem: str  # File Name
    extension: str  # File Type
    version: int  # Version Number
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

    def map_block(self, block_number: int) -> int:
        block_number = block_number
        for rp in self.retrieval_pointers:
            if block_number < rp.j + rp.count:
                return rp.lbn - rp.j + block_number
        raise OSError(errno.EIO, os.strerror(errno.EIO))

    @property
    def name(self) -> str:
        return f"{self.stem}.{self.extension}"

    def is_dir(self) -> bool:
        """Is a directory"""
        return bool(self.fcha & SC_DIR) or (self.fnum == MFD_DIR)

    @property
    def length(self) -> int:
        return self.map_length

    @property
    def uic(self) -> UIC:
        return UIC.from_word(self.fown)

    def __str__(self) -> str:
        return f"{self.fnum:>5},{self.fseq:<5} {self.name:>14};{self.version}  FCHA: {self.fcha:04x}  RTYP: {self.rtyp:1}  Length: {self.length:9}"

    def __repr__(self) -> str:
        return str(self.__dict__)


class ODS1FileHeader(Files11FileHeader):
    """
    Each file on a Files-11 volume is described by a file header.
    The file header is a block that contains all the information
    necessary to access the file.

    Header Area

    +-------------------+-------------------+
    |  Map Area Offset  | Ident Area Offset |   1 word
    +-------------------+-------------------+
    |              File Number              |   1 word
    +-------------------+-------------------+
    |         File Sequence Number          |   1 word
    +-------------------+-------------------+
    |         File Structure Level          |   1 word
    +-------------------+-------------------+
    |            File Owner UIC             |   1 word
    +-------------------+-------------------+
    |            File Protection            |   1 word
    +-------------------+-------------------+
    |   System Char's   |    User Char's    |   1 word
    +-------------------+-------------------+
    |                                       |   H.UFAT
    +-                 -+-                 -+
    |                                       |
    +-                 -+-                 -+
    |                                       |
    +-                 -+-                 -+
    /       User File Attribute Area        /
    +-                 -+-                 -+
    |                                       |
    +-                 -+-                 -+
    |                                       |
    +-                 -+-                 -+
    |                                       |
    +-------------------+-------------------+

    Ident Area

    +-------------------+-------------------+
    |                                       |   I.FNAM
    +-                 -+-                 -+
    |               File Name               |
    +-                 -+-                 -+
    |                                       |
    +-------------------+-------------------+
    |               File Type               |   1 word
    +-------------------+-------------------+
    |            Version Number             |   1 word
    +-------------------+-------------------+
    |            Revision Number            |   1 word
    +-------------------+-------------------+
    |                                       |   I.RVDT
    +-                 -+-                 -+
    |             Revision Date             |
    +-                 -+-                 -+
    |                                       |
    +-------------------+-                 -+
    |                   |                   |
    +-                  +-------------------+
    |                                       |
    +-                 -+-                 -+
    |             Revision Time             |
    +-                 -+-                 -+
    +-------------------+-                 -+
    |                   |                   |
    +-                  +-------------------+
    |                                       |
    +-                 -+-                 -+
    |             Creation Date             |
    +-                 -+-                 -+
    |                                       |
    +-------------------+-------------------+
    |                                       |   I.CRTI
    +-                 -+-                 -+
    |             Creation Time             |
    +-                 -+-                 -+
    |                                       |
    +-------------------+-------------------+
    |                                       |   I.EXDT
    +-                 -+-                 -+
    |             Revision Date             |
    +-                 -+-                 -+
    |                                       |
    +-------------------+-                 -+
    |       Unused      |                   |
    +-------------------+-------------------+   S.IDHD

    Map Area

    +-------------------+-------------------+
    |   Extension RVN   | Extension Seq Num |   1 word
    +-------------------+-------------------+
    |         Extension File Number         |   1 word
    +-------------------+-------------------+
    |     Extension File Sequence Number    |   1 word
    +-------------------+-------------------+
    |  LBN Field Size   | Count Field Size  |   1 word
    +-------------------+-------------------+
    |  Map Words Avail. | Map Words in Use  |   1 word
    +-------------------+-------------------+
    |                                       |
    |                                       |
    /          Retrieval Pointers           /
    |                                       |
    |                                       |
    +-------------------+-------------------+
    |         File Header Checksum          |   1 word
    +-------------------+-------------------+

    https://bitsavers.org/pdf/dec/pdp11/rsx11m_s/Files-11_ODS-1_Spec_Sep86.txt

    """

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
            self.version,  # 1 word  Version Number
            self.rvno,  # 1 word  Revision Number
            self.rvdt,  # 7 bytes Revision Date
            self.rvti,  # 6 bytes Revision Time
            self.crdt,  # 7 bytes Creation Date
            self.crti,  # 6 bytes Creation Time
            self.exdt,  # 7 bytes Expiration Date
            _,
        ) = struct.unpack_from(IDENT_AREA_FORMAT, buffer, position + self.idof * 2)
        self.stem = rad50_word_to_asc(fnam0) + rad50_word_to_asc(fnam1) + rad50_word_to_asc(fnam2)
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
        Load the retrieval pointers
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


class ODS2FileHeader(Files11FileHeader):
    """
    ODS-2 file header

    Header Area

    +-------------------+-------------------+
    |  Map Area Offset  | Ident Area Offset |   1 word
    +-------------------+-------------------+
    | Resv. Area Offset |  ACL Area Offset  |   1 word
    +-------------------+-------------------+
    |          File Segment Number          |   1 word
    +---------------------------------------+
    |         File Structure Level          |   1 word
    +---------------------------------------+
    |                                       |
    |                File ID                |   3 words
    |                                       |
    +---------------------------------------+
    |                                       |
    |           Extension File ID           |   3 words
    |                                       |
    +---------------------------------------+
    |                                       |  16 words
    |                                       |
    |                                       |
    |         Record Attributes Area        |
    |                                       |
    |                                       |
    |                                       |
    +---------------------------------------+
    |          File Characteristics         |   2 words
    |                                       |
    +---------------------------------------+
    |            Record Protection          |   1 word
    +-------------------+-------------------+
    |    Access Level   | Map Words in Use  |   1 word
    +-------------------+-------------------+
    |            File Owner UIC             |   2 words
    |                                       |
    +---------------------------------------+
    |            File Protection            |   1 word
    +---------------------------------------+
    |                                       |
    |         File Header Back Link         |   3 words
    |                                       |
    +---------------------------------------+
    |         Journal Control Flags         |   1 word
    +---------------------------------------+
    |              (not used)               |   1 word
    +---------------------------------------+
    |          File Highwater Mark          |   1 words
    |                                       |
    +---------------------------------------+
    |                                       |  40 words
    /              (not used)               /
    |                                       |
    +---------------------------------------+
    |                                       |  10 words
    |                                       |
    |      Security Classification Mask     |
    |                                       |
    |                                       |
    +---------------------------------------+

    Ident Area

    +---------------------------------------+
    |                                       |  10 words
    |                                       |
    |               File Name               |
    |           (first 20 bytes)            |
    |                                       |
    +---------------------------------------+
    |            Revision Number            |   1 word
    +---------------------------------------+
    |                                       |   4 words
    |             Creation Date             |
    |                                       |
    |                                       |
    +---------------------------------------+
    |                                       |   4 words
    |             Revision Date             |
    |                                       |
    |                                       |
    +---------------------------------------+
    |                                       |   4 words
    |            Expiration Date            |
    |                                       |
    |                                       |
    +---------------------------------------+
    |                                       |   4 words
    |              Backup Date              |
    |                                       |
    |                                       |
    +---------------------------------------+
    |                                       |   33 words
    |          File Name (remainder)        |
    /         (remaining 66 Bytes)          /
    |                                       |
    +---------------------------------------+

    Map, ACL, and Reserved Areas

    +---------------------------------------+
    |                                       |
    |                                       |
    |                                       |
    |          Retrieval Pointers           |
    |                                       |
    |                                       |
    |                                       |
    +---------------------------------------+
    |                                       |
    |                                       |
    |                                       |
    |          Access control List          |
    |                                       |
    |                                       |
    |                                       |
    +---------------------------------------+
    |                                       |
    |                                       |
    |                                       |
    |             Reserved Area             |
    |                                       |
    |                                       |
    |                                       |
    +---------------------------------------+
    |         File Header Checksum          |   1 word
    +---------------------------------------+

    """

    acof: int  # ASCII Area Offset
    rsof: int  # Retrieval Pointer Area Offset
    fvol: int  # File Volume Number

    @classmethod
    def read(cls, fs: "Files11Filesystem", buffer: bytes, position: int = 0) -> "ODS2FileHeader":
        self = cls(fs)
        self.idof, self.mpof, self.acof, self.rsof = struct.unpack_from("<BBBB", buffer, position)
        self.esqn, self.flev = struct.unpack_from("<HH", buffer, position + 4)
        self.fnum, self.fseq, fid_extension = struct.unpack_from("<HHH", buffer, position + 8)
        self.fvol = fid_extension & 0xFF
        if fs.fmax >= 65536:
            self.fnum |= (fid_extension >> 8) << 16
        self.efnu, self.efsq, extension_fid = struct.unpack_from("<HHH", buffer, position + 14)
        self.ervn = extension_fid & 0xFF
        if fs.fmax >= 65536:
            self.efnu |= (extension_fid >> 8) << 16
        self.ufat = buffer[position + 20 : position + 52]
        self.fcha = struct.unpack_from("<I", buffer, position + 52)[0]
        self.fpro = struct.unpack_from("<H", buffer, position + 64)[0]
        self.fown = struct.unpack_from("<I", buffer, position + 60)[0] & 0xFFFF
        self.use = buffer[position + 58]
        self.max = max(0, self.acof - self.mpof)
        self.rtyp, self.ratt, self.rsiz = struct.unpack_from("<BBH", self.ufat)
        self.hibk, self.efbk = struct.unpack_from("<II", self.ufat, 4)
        self.hibk = swap_words(self.hibk)
        self.efbk = swap_words(self.efbk)
        self.ffby = struct.unpack_from("<H", self.ufat, 12)[0]
        self.ctsz = 0
        self.lbsz = 0
        ident = position + self.idof * 2
        raw_name = buffer[ident : ident + 86].decode("ascii", errors="ignore").strip()
        raw_name = raw_name.split("\x00", 1)[0].strip()
        name, separator, version = raw_name.rpartition(";")
        if separator and version.isdigit():
            self.version = int(version)
        else:
            name = raw_name
            self.version = 1
        self.stem, dot, self.extension = name.rpartition(".")
        if not dot:
            self.stem, self.extension = name, ""
        self.rvno = 0
        self.rvdt = b""
        self.rvti = b""
        self.crdt = buffer[ident + 22 : ident + 30]
        self.crti = b""
        self.exdt = b""
        self.retrieval_pointers = []
        self.parse_map(buffer, position)
        return self

    def parse_map(self, buffer: bytes, position: int = 0) -> None:
        """
        Load the retrieval pointers
        """
        cursor = position + self.mpof * 2
        end = cursor + self.use * 2
        virtual_block = 0
        while cursor + 2 <= end:
            first_word = int.from_bytes(buffer[cursor : cursor + 2], "little")
            format_code = first_word >> 14
            if format_code == 0:
                cursor += 2
                continue
            if format_code == 1 and cursor + 4 <= end:
                count = (first_word & 0xFF) + 1
                lbn = (((first_word >> 8) & 0x3F) << 16) | int.from_bytes(buffer[cursor + 2 : cursor + 4], "little")
                cursor += 4
            elif format_code == 2 and cursor + 6 <= end:
                count = (first_word & 0x3FFF) + 1
                lbn = int.from_bytes(buffer[cursor + 2 : cursor + 6], "little")
                cursor += 6
            elif format_code == 3 and cursor + 8 <= end:
                count = (((first_word & 0x3FFF) << 16) | int.from_bytes(buffer[cursor + 2 : cursor + 4], "little")) + 1
                lbn = int.from_bytes(buffer[cursor + 4 : cursor + 8], "little")
                cursor += 8
            else:
                raise OSError(errno.EIO, "Invalid ODS-2 retrieval pointer")
            sparse_lbn = 0x3FFFFF if format_code == 1 else 0xFFFFFFFF
            self.retrieval_pointers.append(RetrievalPointer(virtual_block, count, -1 if lbn == sparse_lbn else lbn))
            virtual_block += count
        self.map_length = max(virtual_block, self.hibk)

    def is_dir(self) -> bool:
        return bool(self.fcha & (1 << 13)) or self.fnum == MFD_DIR

    def __str__(self) -> str:
        return f"{self.fnum:>5},{self.fseq:<5} {self.name:>14};{self.version}  FCHA: {self.fcha:08x}  Length: {self.length:9}"


class Files11Path(XFERXPath):
    fs: "Files11Filesystem"
    _header: t.Optional["Files11FileHeader"]

    fnum: int  # File Number
    fseq: int  # File Sequence Number
    fvol: int  # Relative Volume Number
    stem: str  # File Name
    extension: str  # File Type
    version: int  # Version Number
    uic: UIC = DEFAULT_UIC
    parent_dir: "Files11Path"

    def __init__(self, fs: "Files11Filesystem", parent_dir: t.Optional["Files11Path"]):
        self.fs = fs
        self.parent_dir = parent_dir if parent_dir is not None else self
        assert isinstance(self.parent_dir, Files11Path)
        self._header = None

    @property
    def header(self) -> "Files11FileHeader":
        if self._header is None:
            self._header = self.fs.read_file_header(self.fnum)
        return self._header

    @property
    def file_id(self) -> str:
        """Each file in a volume set is uniquely identified by a File ID"""
        return f"{self.fnum},{self.fseq},{self.fvol}"

    def is_empty(self) -> bool:
        """
        If the file number of the File ID field is zero,
        then this record is empty.
        """
        return self.fnum == 0

    def is_dir(self) -> bool:
        """Is a directory"""
        return self.header.is_dir()

    def is_mount(self) -> bool:
        """Is root directory"""
        return self.fnum == MFD_DIR

    @property
    def parent(self) -> "Files11Path":
        """The logical parent"""
        return self.parent_dir

    def get_ancestors(self) -> t.List["Files11Path"]:
        """
        Get the list of ancestors for this directory entry
        """
        ancestors = []
        node = self.parent_dir
        while node is not self:
            if node.parent_dir is node:
                break
            ancestors.append(node)
            node = node.parent_dir
        return ancestors

    @property
    def fullname(self) -> str:
        if self.is_mount():
            return f"[{self.name}]"
        elif self.is_dir():
            path = ".".join([f"{x.stem}" for x in [self] + self.get_ancestors()])
            return f"[{path}]"
        else:
            path = ".".join([f"{x.stem}" for x in self.get_ancestors()])
            return f"[{path}]{self.name}"

    @property
    def name(self) -> str:
        return f"{self.stem}.{self.extension}"

    @property
    def creation_date(self) -> t.Optional[date]:
        return files11_to_date(self.header.crdt, self.header.crti)

    def dir_uic(self) -> UIC:
        if not self.is_dir():
            raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR))
        return UIC(int(self.stem[0:3], 8), int(self.stem[3:6], 8))

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return self.header.length

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

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> Files11File:
        """
        Open a file
        """
        return Files11File(self.header)

    @abstractmethod
    def iterdir(self) -> t.Iterator["Files11Path"]:
        pass

    def get_child(self, name: str) -> "Files11Path":
        """
        Get a child entry by name and version.
        If the version is not specified, the highest version of the file is returned.
        """
        if not self.is_dir():
            raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), self.fullname)
        name, version = files11_split_version(name)  # type: ignore
        name = files11_canonical_filename(name, wildcard=False, ods_version=self.fs.ods_version)
        if version is not None:
            # Get the specific version of the file
            for entry in self.iterdir():
                if (
                    filename_match(entry.name, name, wildcard=False)
                    and not entry.is_empty()
                    and entry.version == version
                ):
                    return entry
        else:
            # Get the highest version of the file
            result: t.Optional[Files11Path] = None
            for entry in self.iterdir():
                if (
                    filename_match(entry.name, name, wildcard=False)
                    and not entry.is_empty()
                    and (result is None or entry.version > result.version)
                ):
                    result = entry
            if result is not None:
                return result
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), name)

    def __str__(self) -> str:
        return f"File ID {'(' + self.file_id + ')':16} Name: {self.name:13} Ver: {self.version} FCHA: {self.header.fcha:04x} RTYP: {self.header.rtyp:1} Length: {self.header.length:9}"


class ODS1DirectoryEntry(Files11Path):
    """
    ODS-1 directory entry

    Each ODS-1 directory entry contains the following:

    Word
             +-------------------+-------------------+
       0     |                                       |   3 words
             |                File ID                |
             |                                       |
             +-------------------+-------------------+
       3     |                                       |   3 words
             |         Name (9 Radix-50 chars)       |
             |                                       |
             +-------------------+-------------------+
       5     |         Type (3 Radix-50 chars)       |   1 word
             +-------------------+-------------------+
       6     |                Version                |   1 word
             +-------------------+-------------------+

    Files-11 On-Disk Structure Specification
    4.2 Directory Structure
    """

    @classmethod
    def read(cls, fs: "Files11Filesystem", buffer: bytes, position: int, parent: "Files11Path") -> "ODS1DirectoryEntry":
        self = cls(fs, parent)
        (
            self.fnum,  # 1 word File Number
            self.fseq,  # 1 word File Sequence Number
            self.fvol,  # 1 word Relative Volume Number
            fnam0,  #     1 word File Name
            fnam1,  #     1 word
            fnam2,  #     1 word
            ftyp,  #      1 word File Type
            self.version,  # 1 word File Version
        ) = struct.unpack_from(ODS1_DIRECTORY_FILE_ENTRY_FORMAT, buffer, position)
        self.stem = rad50_word_to_asc(fnam0) + rad50_word_to_asc(fnam1) + rad50_word_to_asc(fnam2)
        self.extension = rad50_word_to_asc(ftyp)
        return self

    def iterdir(self) -> t.Iterator["Files11Path"]:
        """
        Iterate over directory entries
        """
        if not self.is_dir():
            raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), self.fullname)
        header = self.fs.read_file_header(self.fnum)
        with Files11File(header) as f:
            buffer = f.read_block(0, READ_FILE_FULL)
            for pos in range(0, len(buffer), ODS1_DIRECTORY_FILE_ENTRY_LEN):
                entry = ODS1DirectoryEntry.read(self.fs, buffer, position=pos, parent=self)
                if not entry.is_empty():
                    yield entry


class ODS2DirectoryEntry(Files11Path):
    """
    ODS-2 directory entry

    Each directory record consists of the following:

    +---------------------------------------+
    |           Record Byte Count           |   1 word
    +---------------------------------------+
    |             Version Limit             |   1 word
    +-------------------+-------------------+
    |  Name Byte Count  |       Flags       |   1 word
    +-------------------+-------------------+
    |                                       |   Variable
    /           File Name String            /
    |                                       |
    +---------------------------------------+
    |                                       |   Variable
    /              Value Field              /
    |                                       |
    +---------------------------------------+

    Files-11 On-Disk Structure Specification
    4.2 Directory Structure
    """

    @classmethod
    def read(cls, fs: "Files11Filesystem", buffer: bytes, position: int, parent: "Files11Path") -> "ODS2DirectoryEntry":
        self = cls(fs, parent)
        (
            _,  #                1 word Flags
            name_length,  #      1 word File Name Length
        ) = struct.unpack_from("<BB", buffer, position + 4)
        raw_name = buffer[position + 6 : position + 6 + name_length].decode("ascii", errors="ignore")
        self.stem, dot, self.extension = raw_name.rpartition(".")
        if not dot:
            self.stem, self.extension = raw_name, ""
        value_position = position + 6 + ((name_length + 1) & ~1)
        (
            self.version,  #  1 word File Version
            self.fnum,  #     1 word File Number
            self.fseq,  #     1 word File Sequence Number
            fid_extension,  # 1 word File ID Extension
        ) = struct.unpack_from("<HHHH", buffer, value_position)
        self.fvol = fid_extension & 0xFF
        if fs.fmax >= 65536:
            self.fnum |= (fid_extension >> 8) << 16
        return self

    def iterdir(self) -> t.Iterator["Files11Path"]:
        """
        Iterate over directory entries
        """
        if not self.is_dir():
            raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), self.fullname)
        header = self.fs.read_file_header(self.fnum)
        with Files11File(header) as f:
            buffer = f.read_block(0, READ_FILE_FULL)
            for block_start in range(0, len(buffer), BLOCK_SIZE):
                block_end = min(block_start + BLOCK_SIZE, len(buffer))
                pos = block_start
                while pos + 2 <= block_end:
                    # Length in bytes of the record (excluding the length field itself)
                    record_size = int.from_bytes(buffer[pos : pos + 2], "little")
                    if record_size in (0, 0xFFFF):
                        break
                    record_end = pos + 2 + record_size
                    if record_end > block_end or record_size < 12:
                        break
                    entry_type = buffer[pos + 4] & 0x07
                    if entry_type == 0:
                        name_length = buffer[pos + 5]
                        value_pos = pos + 6 + ((name_length + 1) & ~1)
                        while value_pos + 8 <= record_end:
                            entry = ODS2DirectoryEntry.read(self.fs, buffer, position=pos, parent=self)
                            entry.version, entry.fnum, entry.fseq, fid_extension = struct.unpack_from(
                                "<HHHH", buffer, value_pos
                            )
                            entry.fvol = fid_extension & 0xFF
                            if self.fs.fmax >= 65536:
                                entry.fnum |= (fid_extension >> 8) << 16
                            if not entry.is_empty():
                                yield entry
                            value_pos += 8
                    pos = record_end


class Files11Filesystem(AbstractRXBlockFilesystem):
    """
    Files-11 Filesystem

    https://bitsavers.org/pdf/dec/pdp11/rsx11m_s/Files-11_ODS-1_Spec_Sep86.txt
    """

    fs_name = "files11"
    fs_description = "Files-11"
    fs_platforms = ["pdp11", "vax"]
    fs_entry_metadata = [
        "creation_date",
        "version",
    ]

    pwd: str  #  Current working directory
    ods_version: int  # Files-11 On-Disk Structure Specification Version

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
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        strict: t.Union[bool, str] = True,
        **kwargs: t.Union[bool, str],
    ) -> "Files11Filesystem":
        self = cls(file_or_dev)
        self.pwd = MFD_DIR_NAME
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
                raise OSError(errno.EIO, "Error reading index file")
        return self

    def read_home(self) -> None:
        """Read home block"""
        t = self.read_block(HOME_BLOCK)
        structure_level = struct.unpack_from("<H", t, 12)[0]
        self.ods_version = structure_level >> 8
        if self.ods_version == 1:
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
            self.iblb = (iblb_h << 16) + iblb_l
        else:
            self.vlev = structure_level
            self.sbcl = struct.unpack_from("<H", t, 14)[0]
            self.iblb = struct.unpack_from("<I", t, 24)[0]
            self.fmax = struct.unpack_from("<I", t, 28)[0]
            self.ibsz = struct.unpack_from("<H", t, 32)[0]
            self.dvty = struct.unpack_from("<H", t, 36)[0]
            self.vnam = t[476:488]

    def write_block(
        self,
        buffer: t.Union[bytes, bytearray],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    def read_file_header(self, file_number: int) -> Files11FileHeader:
        """
        Read file header by file number
        """
        if self.ods_version == 1:
            if file_number <= 16:
                # The first 16 file headers are logically contiguous
                # with the index file bitmap
                block_number = self.iblb + file_number
            else:
                # The other files must be located through the mapping
                # data in the index file header
                indexfs = self.read_file_header(INDEXF_SYS)
                block_number = indexfs.map_block(file_number)
        else:  # ODS-2
            if file_number <= 16:
                block_number = self.iblb + self.ibsz + file_number - 1
            else:
                indexfs = self.read_file_header(INDEXF_SYS)
                vbn = 4 * self.sbcl + self.ibsz + file_number
                block_number = indexfs.map_block(vbn - 1)
        buffer = self.read_block(block_number)
        if self.ods_version == 1:
            file_header = ODS1FileHeader.read(self, buffer)
            assert file_header.flev == 0o401
        else:
            file_header = ODS2FileHeader.read(self, buffer)
            if file_header.fnum == 0:
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), f"{file_number}")
            if file_header.flev >> 8 != 2 or file_header.fnum != file_number:
                raise OSError(errno.EIO, "Error reading ODS-2 file header")
        return file_header

    @property
    def entries_list(self) -> t.Iterator[Files11Path]:
        try:
            parent = self.get_file_entry(self.pwd)
            yield from parent.iterdir()
        except FileNotFoundError:
            pass

    def get_parent_and_name(self, fullname: t.Optional[str], wildcard: bool) -> t.Tuple[Files11Path, t.Optional[str]]:
        """
        Get the parent directory and filename from fullname
        """
        path, name, version = files11_split_fullname(
            fullname=fullname,
            default_path=self.pwd,
            wildcard=wildcard,
            ods_version=self.ods_version,
        )
        if not path:
            raise FileNotFoundError(errno.ENOENT, "No directory specified", fullname)
        # Traverse the path to find the directory entry
        parts = path.split(".")
        parent: Files11Path = self.get_root()
        for part in parts:
            if not part:
                continue
            part = files11_canonical_filename(f"{part}.DIR", wildcard=wildcard, ods_version=self.ods_version)
            parent = parent.get_child(part)
            if not parent.is_dir():
                raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), part)
        if version is not None:
            name = f"{name};{version}"
        return parent, name

    def get_file_entry(self, fullname: str) -> Files11Path:
        """
        Get a file entry by fullname
        """
        parent, name = self.get_parent_and_name(fullname=fullname, wildcard=False)
        if not name:
            return parent  # Return the directory itself
        else:
            return parent.get_child(name)  # Return the child entry by name

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["Files11Path"]:
        parent, name = self.get_parent_and_name(fullname=pattern, wildcard=wildcard)
        name, version = files11_split_version(name)
        for entry in parent.iterdir():
            if (
                filename_match(entry.name, name, wildcard)
                and not entry.is_empty()
                and (version is None or entry.version == version)
            ):
                yield entry

    def get_root(self) -> Files11Path:
        """
        Get the root directory
        """
        if self.ods_version == 1:
            root: Files11Path = ODS1DirectoryEntry(self, None)
        else:
            root = ODS2DirectoryEntry(self, None)
        root.fnum = MFD_DIR  # File Number
        root.fseq = MFD_DIR  # File Sequence Number
        root.fvol = 0  # Relative Volume Number
        root.stem = "000000"  # File Name
        root.extension = "DIR"  # File Type
        root.version = 1  # Version Number
        root._header = None
        return root

    def show_accounts(self, volume_id: str, options: t.Dict[str, bool]) -> None:
        """
        Listing of all UIC
        """
        if "uic" in options:
            del options["uic"]
        self.dir(volume_id, pattern="[0,0]*.DIR", options=options)

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List directory entries
        """
        files = 0
        blocks = 0
        allocated = 0

        parent, name = self.get_parent_and_name(fullname=pattern, wildcard=True)
        name, version = files11_split_version(name)
        dt = datetime.today().strftime('%y-%b-%d %H:%M').upper()
        sys.stdout.write(f"DIRECTORY {volume_id}:{parent.fullname}\n{dt}\n\n")
        for x in parent.iterdir():
            if (
                filename_match(x.name, name, wildcard=True)
                and not x.is_empty()
                and (version is None or x.version == version)
            ):
                fullname = f"{x.stem}.{x.extension};{x.version}"
                date = x.creation_date and x.creation_date.strftime("%d-%b-%y %H:%M").upper() or ""
                attr = "C" if UC_CNB & x.header.fcha else ""
                length = f"{x.header.length}."
                sys.stdout.write(f"{fullname:<19s} {length:<7s} {attr:1}  {date:>9s}\n")
                blocks += x.header.length
                allocated += x.header.length
                files += 1
        sys.stdout.write("\n")
        sys.stdout.write(f"TOTAL OF {blocks}./{allocated}. BLOCKS IN {files}. FILES\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        uic = None
        if arg:
            entry = self.get_file_entry(arg)
            sys.stdout.write(dump_struct(entry.__dict__))
            sys.stdout.write("\n\n")
            if entry.is_dir():
                for x in entry.iterdir():
                    sys.stdout.write(f"{x}\n")
        else:
            sys.stdout.write("Home Block\n\n")
            sys.stdout.write(dump_struct(self.__dict__))
            indexfs = self.read_file_header(INDEXF_SYS)
            sys.stdout.write("\n\nINDEXF.SYS Header\n\n")
            sys.stdout.write(dump_struct(indexfs.__dict__))
            with Files11File(indexfs) as f:
                sys.stdout.write(f"\n\nINDEXF.SYS {f.header.length}\n\n")
                try:
                    for i in range(1, f.header.length):
                        try:
                            h = self.read_file_header(i)
                            sys.stdout.write(f"{h}\n")
                        except FileNotFoundError:
                            pass
                except OSError:
                    pass

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        """
        Change the current User Identification Code
        """
        if entry is None:
            entry = self.get_root()
        assert isinstance(entry, Files11Path)
        if not entry.is_dir():
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), entry.fullname)
        self.pwd = entry.stem

    def get_pwd(self) -> str:
        """
        Get the current working directory
        """
        return self.pwd
