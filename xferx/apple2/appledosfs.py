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
from ..commons import (
    ASCII,
    IMAGE,
    READ_FILE_FULL,
    dump_struct,
    filename_match,
    hex_dump,
)
from .commons import ProDOSFileInfo, decode_apple_single, encode_apple_single
from .disk import SECTOR_SIZE, AppleDisk, TrackSector

__all__ = [
    "AppleDOSFile",
    "AppleDOSDirectoryEntry",
    "AppleDOSFilesystem",
]

# Apple II AppleDOS Filesystem
# https://archive.org/details/apple-ii-pascal-1.3/page/n803/mode/2up

FILENAME_LEN = 30  # max filename length

NEXT_TRACK_OFFSET = 0x01
NEXT_SECTOR_OFFSET = 0x02
TRACK_SECTOR_OFFSET = 0x0C

VTOC_TRACK = 17  # Track of VTOC
VTOC_SECTOR = 0  # Sector of VTOC
VTOC_ADDRESS = TrackSector(VTOC_TRACK, VTOC_SECTOR)
VTOC_FORMAT = "<BBBB2sB32sB8sBB2sBBH"
VTOC_BITMAP_OFFSET = struct.calcsize(VTOC_FORMAT)
VTOC_BITMAP_TRACK_SIZE = 4  # 4 bytes per track
VTOC_MAX_TRACKS = (
    SECTOR_SIZE - VTOC_BITMAP_OFFSET
) // VTOC_BITMAP_TRACK_SIZE  # Maximum number of tracks, limited by the bitmap size

DEFAULT_VOLUME_NUMBER = 254  # Default volume number
DEFAULT_DOS_VERSION = 3  # Default DOS version (DOS 3.3)
DEFAULT_DOS_TYPE = 4  # Default DOS type (DOS 3.3)

RESERVED_TRACKS = 3  # Number of reserved tracks
DATA_SECTORS_PER_TRACK_SECTOR_LIST = 122

FILE_DESCRIPTIVE_ENTRY_OFFSET = 0x0B
FILE_DESCRIPTIVE_ENTRY_FORMAT = "<BBB30sH"
FILE_DESCRIPTIVE_ENTRY_SIZE = struct.calcsize(FILE_DESCRIPTIVE_ENTRY_FORMAT)
FILE_DESCRIPTIVE_ENTRY_PER_SECTOR = 7

FILE_TYPE_TEXT = 0x00  # Text file
FILE_TYPE_INTEGER_BASIC = 0x01  # Integer BASIC file
FILE_TYPE_APPLESOFT_BASIC = 0x02  # Applesoft BASIC file
FILE_TYPE_BINARY = 0x04  # Binary file
FILE_TYPE_SPECIAL = 0x08  # Type S file
FILE_TYPE_RELOCABLE = 0x10  # Relocatable object module file
FILE_TYPE_A = 0x20  # New type A file
FILE_TYPE_B = 0x40  # New type B file

LOCKED_FLAG = 0x80
DELETED_TRACK = 0xFF

FILE_TYPES = {
    FILE_TYPE_TEXT: "T",
    FILE_TYPE_INTEGER_BASIC: "I",
    FILE_TYPE_APPLESOFT_BASIC: "A",
    FILE_TYPE_BINARY: "B",
    FILE_TYPE_SPECIAL: "S",
    FILE_TYPE_RELOCABLE: "R",
    FILE_TYPE_A: "a",
    FILE_TYPE_B: "b",
}

PRODOS_TXT_FILE_TYPE = 0x04  # Text file type (mapped to T)
PRODOS_BIN_FILE_TYPE = 0x06  # Binary file type (mapped to B)
PRODOS_INT_FILE_TYPE = 0xFA  # Integer BASIC file type (mapped to I)
PRODOS_BAS_FILE_TYPE = 0xFC  # Applesoft BASIC file type (mapped to A)
PRODOS_REL_FILE_TYPE = 0xFE  # Relocable object file type (mapped to R)

BINARY_FILE_FORMAT = "<HH"  # Address/Length
BASIC_FILE_FORMAT = "<H"  # Length


def appledos_canonical_filename(fullname: t.Optional[str], wildcard: bool = False) -> t.Optional[str]:
    """
    Generate the canonical AppleDOS filename
    """
    if fullname:
        fullname = fullname[:FILENAME_LEN].upper()
    return fullname


def appledos_get_raw_file_type(file_type: t.Optional[str], default: int = FILE_TYPE_TEXT) -> int:
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


def appledos_filename_to_raw_filename(filename: str) -> bytes:
    """
    Convert the filename to a raw filename
    """
    raw_filename = bytes([ord(x) ^ 0x80 for x in filename[:FILENAME_LEN].upper()])
    raw_filename += b"\xa0" * (FILENAME_LEN - len(raw_filename))
    return raw_filename


class AppleDOSFile(AbstractFile):
    entry: "AppleDOSDirectoryEntry"
    file_mode: str
    closed: bool

    def __init__(self, entry: "AppleDOSDirectoryEntry", file_mode: t.Optional[str] = None):
        self.entry = entry
        self.closed = False
        if file_mode is None:
            self.file_mode = ASCII if entry.raw_file_type == FILE_TYPE_TEXT else IMAGE
        elif file_mode is ASCII or file_mode == FILE_TYPES[FILE_TYPE_TEXT]:
            self.file_mode = ASCII
        else:
            self.file_mode = IMAGE

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
        # Get the blocks to be read
        blocks = list(self.entry.blocks())[block_number : block_number + number_of_blocks]
        # Read the blocks
        for disk_block_number in blocks:
            buffer = self.entry.fs.read_sector(disk_block_number)
            data.extend(buffer)
        # Convert to ASCII if needed
        if self.file_mode == ASCII:
            return bytes([0x0A if x == 0x8D else x & 0x7F for x in data])
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
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.entry.get_length()
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        # Convert to ASCII if needed
        if self.file_mode == ASCII:
            buffer = bytes([0x8D if x == 0x0A else x | 0x80 for x in buffer])
        # Get the blocks to be written
        blocks = list(self.entry.blocks())[block_number : block_number + number_of_blocks]
        # Write the blocks
        for i, disk_block_number in enumerate(blocks):
            data = buffer[i * SECTOR_SIZE : (i + 1) * SECTOR_SIZE]
            self.entry.fs.write_sector(data, disk_block_number)

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


class AppleDOSVTOC:
    """
    Volume Table Of Contents

    The VTOC contains information about the diskette and
    the bitmap of the free sectors per track.
    It is always located on track 17, sector 0.

    https://archive.org/details/Beneath_Apple_DOS_alt/page/n19/mode/2up

       Field                                Byte of
      Length                                Sector
             +----------------------------+
     1 byte  |        DOS type            | $00
             +----------------------------+
     1 byte  |       catalog track        | $01
             +----------------------------+
     1 byte  |       catalog sector       | $02
             +----------------------------+
     1 byte  |        DOS version         | $03
             +----------------------------+
     2 byte  |        reserved            | $04
             |                            | $05
             +----------------------------+
     1 byte  |       volume number        | $06
             +----------------------------+
     32 byte |        reserved            | $07
             |                            | $26
             +----------------------------+
     1 byte  |  max track/sector pairs    | $27
             +----------------------------+
     8 byte  |        reserved            | $28
             |                            | $2F
             +----------------------------+
     1 byte  |   last track allocated     | $30
             +----------------------------+
     1 byte  |  allocation direction      | $31
             +----------------------------+
     2 byte  |        reserved            | $32
             |                            | $33
             +----------------------------+
     1 byte  |   number of tracks on disk | $34
             +----------------------------+
     1 byte  |   sectors per track        | $35
             +----------------------------+
     2 byte  |   bytes per sector         | $36
             |                            | $37
             +----------------------------+
     4 byte  | bitmap of track 0          | $38
             |                            | $3B
             +----------------------------+
     4 byte  | bitmap of track 1          | $3C
             |                            | $3F
             +----------------------------+
             | ...                        |
             +----------------------------+
             | bitmap of track n          |
             |                            |
             +----------------------------+

    """

    fs: "AppleDOSFilesystem"
    dos_type: int = 0  # DOS type
    catalog_address: TrackSector  # first catalog address
    dos_version: int = 0  # DOS version
    volume_number: int = 0  # volume number
    max_ts_pairs: int = 0  # max track/sector pairs per sector
    last_track_allocated: int = 0  # last track allocated
    allocation_direction: int = 0  # direction of track allocation (+1 or -1)
    number_of_tracks: int = 0  # number of tracks on disk
    sectors_per_track: int = 0  # sectors per track
    bytes_per_sector: int = 0  # bytes per sector
    reserved_1: bytes  # reserver - 2 bytes
    reserved_2: bytes  # reserver - 32 bytes
    reserved_3: bytes  # reserver - 8 bytes
    reserved_4: bytes  # reserver - 2 bytes
    bitmaps: t.List[int]  # bitmap blocks (32 bits per track)

    def __init__(self, fs: "AppleDOSFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "AppleDOSFilesystem") -> "AppleDOSVTOC":
        """
        Read the VTOC sector
        """
        self = AppleDOSVTOC(fs)
        buffer = fs.read_sector(VTOC_ADDRESS)
        (
            self.dos_type,  # DOS type
            catalog_track,  # track of catalog sector
            catalog_sector,  # sector of catalog sector
            self.dos_version,  # DOS version
            self.reserved_1,  # reserved
            self.volume_number,  # volume number
            self.reserved_2,  # reserved
            self.max_ts_pairs,  # max track/sector pairs per sector
            self.reserved_3,  # reserved
            self.last_track_allocated,  # last track allocated
            self.allocation_direction,  # allocation direction
            self.reserved_4,  # reserved
            self.number_of_tracks,  # number of tracks on disk
            self.sectors_per_track,  # sectors per track
            self.bytes_per_sector,  # bytes per sector
        ) = struct.unpack_from(VTOC_FORMAT, buffer, 0)
        self.catalog_address = TrackSector(catalog_track, catalog_sector)
        # Read the bitmap blocks
        bitmap_format = f">{self.number_of_tracks}I"
        self.bitmaps = list(struct.unpack_from(bitmap_format, buffer, VTOC_BITMAP_OFFSET))
        return self

    @classmethod
    def create(cls, fs: "AppleDOSFilesystem") -> "AppleDOSVTOC":
        """
        Create the VTOC sector
        """
        self = AppleDOSVTOC(fs)
        # Initialize the VTOC sector
        self.dos_type = DEFAULT_DOS_TYPE  # DOS type
        self.catalog_address = TrackSector(VTOC_TRACK, fs.sectors_per_track - 1)
        self.dos_version = DEFAULT_DOS_VERSION  # DOS version
        self.reserved_1 = b"\0" * 2  # reserved
        self.volume_number = DEFAULT_VOLUME_NUMBER  # volume number
        self.reserved_2 = b"\0" * 32  # reserved
        self.max_ts_pairs = DATA_SECTORS_PER_TRACK_SECTOR_LIST  # max track/sector pairs per sector
        self.reserved_3 = b"\0" * 8  # reserved
        self.last_track_allocated = VTOC_TRACK  # last track allocated
        self.allocation_direction = 255  # allocation direction
        self.reserved_4 = b"\0" * 2  # reserved
        self.number_of_tracks = fs.number_of_tracks  # number of tracks on disk
        if fs.number_of_tracks > VTOC_MAX_TRACKS:
            self.number_of_tracks = VTOC_MAX_TRACKS
            fs.number_of_tracks = self.number_of_tracks
        self.sectors_per_track = fs.sectors_per_track  # sectors per track
        self.bytes_per_sector = SECTOR_SIZE  # bytes per sector
        # Initialize the bitmap
        b = ((1 << self.sectors_per_track) - 1) << (32 - self.sectors_per_track)
        self.bitmaps = [b] * self.number_of_tracks
        # The first 3 tracks are reserved for the bootstrap image
        for track in range(0, RESERVED_TRACKS):
            self.bitmaps[track] = 0
        # The track 17 is reserved for the VTOC and the catalog
        self.bitmaps[VTOC_TRACK] = 0
        return self

    def write(self) -> None:
        """
        Write the VTOC sector
        """
        buffer = bytearray(SECTOR_SIZE)
        struct.pack_into(
            VTOC_FORMAT,
            buffer,
            0,
            self.dos_type,  # DOS type
            self.catalog_address.track,  # track of catalog sector
            self.catalog_address.sector,  # sector of catalog sector
            self.dos_version,  # DOS version
            self.reserved_1,  # reserved
            self.volume_number,  # volume number
            self.reserved_2,  # reserved
            self.max_ts_pairs,  # max track/sector pairs per sector
            self.reserved_3,  # reserved
            self.last_track_allocated,  # last track allocated
            self.allocation_direction,  # allocation direction
            self.reserved_4,  # reserved
            self.number_of_tracks,  # number of tracks on disk
            self.sectors_per_track,  # sectors per track
            self.bytes_per_sector,  # bytes per sector
        )
        # Write the bitmap blocks
        bitmap_format = f">{self.number_of_tracks}I"
        struct.pack_into(bitmap_format, buffer, VTOC_BITMAP_OFFSET, *self.bitmaps)
        self.fs.write_sector(buffer, VTOC_ADDRESS)

    @property
    def is_valid(self) -> bool:
        """
        Check if the VTOC is valid
        """
        return self.bytes_per_sector == SECTOR_SIZE

    @property
    def total_bits(self) -> int:
        """
        Return the bitmap length in bit
        """
        return len(self.bitmaps) * 8

    def is_free(self, address: TrackSector) -> bool:
        """
        Check if the sector is free
        """
        bit_value = self.bitmaps[address.track]
        bit_position = address.sector + (32 - self.sectors_per_track)
        return (bit_value & (1 << bit_position)) != 0

    def set_free(self, address: TrackSector) -> None:
        """
        Mark the sector as free
        """
        bit_position = address.sector + (32 - self.sectors_per_track)
        self.bitmaps[address.track] |= 1 << bit_position

    def set_used(self, address: TrackSector) -> None:
        """
        Mark the sector as used
        """
        bit_position = address.sector + (32 - self.sectors_per_track)
        self.bitmaps[address.track] &= ~(1 << bit_position)

    def allocate(self, size: int) -> t.List[TrackSector]:
        """
        Allocate sectors
        """
        if self.free() < size:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        sectors: t.List[TrackSector] = []
        # Get the last allocated track
        track = self.last_track_allocated
        while size > 0:
            # Calculate the next track
            if self.allocation_direction == 1:
                track += 1
                if track >= self.number_of_tracks:
                    track = 1  # skip track 0
            else:
                track -= 1
                if track <= 0:
                    track = self.number_of_tracks - 1
            # Allocate sectors
            for sector in range(self.sectors_per_track, 0, -1):
                address = TrackSector(track, sector)
                if self.is_free(address):
                    self.set_used(address)
                    sectors.append(address)
                    size -= 1
                if size == 0:
                    break
            if track == self.last_track_allocated:
                break
        # Update the last allocated track
        self.last_track_allocated = track
        if len(sectors) < size:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        return sectors

    def used(self) -> int:
        """
        Count the number of used sectors
        """
        return self.number_of_tracks * self.sectors_per_track - self.free()

    def free(self) -> int:
        """
        Count the number of free sectors
        """
        free = 0
        for block in self.bitmaps:
            free += block.bit_count()
        return free

    def __str__(self) -> str:
        return dump_struct(self.__dict__, exclude=["fs", "bitmaps"])


class AppleDOSCatalog:
    """
    Each catalog sector contains up to 7 file entries.
    Each sector has a track/sector pointer in bytes 01 and 02
    which points to the next catalog sector.
    The last catalog sector has a 0/0 pointer to indicate that there
    are no more catalog sectors in the chain.

       Field                                Byte of
      Length                                Sector
             +----------------------------+
     1 byte  |        reserved            | $00
             |----------------------------|
     1 byte  |       next track           | $01
             |----------------------------|
     1 byte  |       next sector          | $02
             +----------------------------+
             |                            | $03
     7 byte  |        reserved            |
             |                            |
             +----------------------------+
     34 byte |      file entry 1          | $0B
     each    |          ...               |
     238     |      file entry 7          |
     total   +----------------------------+

    https://archive.org/details/Beneath_Apple_DOS_alt/page/n19/mode/2up
    """

    fs: "AppleDOSFilesystem"
    catalog_addresses: t.List[TrackSector]  # catalog sectors addresses
    directory_entries: t.List["AppleDOSDirectoryEntry"]  # 7 file entries per catalog sector

    def __init__(self, fs: "AppleDOSFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "AppleDOSFilesystem", catalog_address: t.Optional[TrackSector] = None) -> "AppleDOSCatalog":
        """
        Read the catalog from the disk
        """
        self = cls(fs)
        self.catalog_addresses = []
        self.directory_entries = []
        address = catalog_address if catalog_address is not None else self.fs.catalog_address
        while address.track != 0:
            # Read the catalog sector
            self.catalog_addresses.append(address)
            buffer = self.fs.read_sector(address)
            # Read 7 file entries
            for position in range(FILE_DESCRIPTIVE_ENTRY_OFFSET, SECTOR_SIZE, FILE_DESCRIPTIVE_ENTRY_SIZE):
                entry = AppleDOSDirectoryEntry.read(self.fs, buffer, position)
                self.directory_entries.append(entry)
            address = TrackSector(buffer[NEXT_TRACK_OFFSET], buffer[NEXT_SECTOR_OFFSET])
        return self

    @classmethod
    def create(cls, fs: "AppleDOSFilesystem", vtoc: "AppleDOSVTOC") -> "AppleDOSCatalog":
        """
        Create an empty catalog on the remaining sectors of VTOC track
        """
        self = cls(fs)
        self.catalog_addresses = [
            TrackSector(VTOC_TRACK, sector) for sector in range(self.fs.sectors_per_track - 1, 0, -1)
        ]
        self.directory_entries = [
            AppleDOSDirectoryEntry.create(self.fs)
            for x in range(0, FILE_DESCRIPTIVE_ENTRY_PER_SECTOR * len(self.catalog_addresses))
        ]
        return self

    def write(self) -> None:
        """
        Write the catalog to the disk
        """
        entries = list(self.directory_entries)
        for i in range(0, len(self.catalog_addresses)):
            buffer = bytearray(SECTOR_SIZE)
            # Update the link to next catalog sector
            if i + 1 < len(self.catalog_addresses):
                next_address = self.catalog_addresses[i + 1]
                buffer[NEXT_TRACK_OFFSET] = next_address.track
                buffer[NEXT_SECTOR_OFFSET] = next_address.sector
            else:
                buffer[NEXT_TRACK_OFFSET] = 0
                buffer[NEXT_SECTOR_OFFSET] = 0
            # Update the catalog sector
            for j in range(0, FILE_DESCRIPTIVE_ENTRY_PER_SECTOR):
                if not entries:
                    break
                entry = entries.pop(0)
                entry.write_buffer(buffer, FILE_DESCRIPTIVE_ENTRY_OFFSET + j * FILE_DESCRIPTIVE_ENTRY_SIZE)
            # Write the catalog sector
            self.fs.write_sector(buffer, self.catalog_addresses[i])

    def iterdir(self, include_deleted: bool = False) -> t.Iterator["AppleDOSDirectoryEntry"]:
        """
        Iterate over directory entries
        """
        for entry in self.directory_entries:
            if not entry.is_empty and (include_deleted or not entry.is_deleted):
                yield entry

    def search_empty_entry(self) -> t.Optional["AppleDOSDirectoryEntry"]:
        """
        Search for an empty or deleted catalog entry
        """
        # Search for an empty catalog entry
        for entry in self.directory_entries:
            if entry.is_empty:
                return entry
        # Search for a delete catalog entry
        for entry in self.directory_entries:
            if entry.is_deleted:
                return entry
        return None

    def create_file(
        self,
        vtoc: "AppleDOSVTOC",
        fullname: str,
        number_of_blocks: int,  # length in blocks
        file_type: t.Optional[str],  # optional file type
    ) -> "AppleDOSDirectoryEntry":
        """
        Create a new file
        """
        # Allocate sectors
        trac_sector_list_num = max(math.ceil(number_of_blocks / DATA_SECTORS_PER_TRACK_SECTOR_LIST), 1)
        sectors = vtoc.allocate(number_of_blocks + trac_sector_list_num)
        trac_sector_list_sectors = sectors[:trac_sector_list_num]
        data_sectors = sectors[trac_sector_list_num:]
        # Prepare the track/sector list
        for i in range(trac_sector_list_num):
            buffer = bytearray(SECTOR_SIZE)
            # Update the link to next track/sector list sector
            if i + 1 < trac_sector_list_num:
                next_address = trac_sector_list_sectors[i + 1]
                buffer[NEXT_TRACK_OFFSET] = next_address.track
                buffer[NEXT_SECTOR_OFFSET] = next_address.sector
            else:
                buffer[NEXT_TRACK_OFFSET] = 0
                buffer[NEXT_SECTOR_OFFSET] = 0
            # Write up to 120 Track/Sector pairs
            for j in range(TRACK_SECTOR_OFFSET, SECTOR_SIZE, 2):
                if not data_sectors:
                    break
                data_address = data_sectors.pop(0)
                buffer[j] = data_address.track
                buffer[j + 1] = data_address.sector
            # Write the track/sector list sector
            self.fs.write_sector(buffer, trac_sector_list_sectors[i])
        # Search for an empty catalog entry
        entry = self.search_empty_entry()
        if entry is None:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        # Update the entry
        entry.is_deleted = False
        entry.is_locked = False
        entry.address = sectors[0]
        entry.filename = appledos_canonical_filename(fullname)  # type: ignore
        entry.raw_filename = appledos_filename_to_raw_filename(fullname)  # type: ignore
        entry.length = number_of_blocks + trac_sector_list_num
        entry.raw_file_type = appledos_get_raw_file_type(file_type)
        return entry

    def __str__(self) -> str:
        return dump_struct(self.__dict__, exclude=["fs", "directory_entries"])


class AppleDOSDirectoryEntry(AbstractDirectoryEntry):
    """
    Apple DOS 3.x Directory Entry

       Field                                Byte of
      Length                                Sector
             +----------------------------+
     1 byte  |    T/S List Sector track   | $00
             |----------------------------|
     1 byte  |   T/S List Sector sector   | $01
             +----------------------------+
     1 byte  |     file type and flag     | $02
             +----------------------------+
             |                            | $03
     30 byte |         filename           |
             |                            | $20
             +----------------------------+
     2 byte  |          file              | $21
             |         length             | $22
             +----------------------------+
    """

    fs: "AppleDOSFilesystem"
    address: TrackSector  # Track/Sector List address
    raw_file_type: int  # File type
    is_locked: bool  # Locked
    is_deleted: bool  # Deleted
    filename: str  # Filename
    raw_filename: bytes  # Raw filename
    length: int  # Length in sectors

    def __init__(self, fs: "AppleDOSFilesystem"):
        self.fs = fs

    @classmethod
    def read(cls, fs: "AppleDOSFilesystem", buffer: bytes, position: int) -> "AppleDOSDirectoryEntry":
        self = cls(fs)
        (
            track,  # track
            sector,  # sector
            raw_file_type,  # file type
            raw_filename,  # filename
            self.length,  # length
        ) = struct.unpack_from(FILE_DESCRIPTIVE_ENTRY_FORMAT, buffer, position)
        self.raw_file_type = raw_file_type & 0x7F
        self.is_locked = bool(raw_file_type & LOCKED_FLAG)
        if track == DELETED_TRACK:
            # The original track number is copied to the last byte of the filename
            track = raw_filename[-1]
            raw_filename = raw_filename[:-1]
            self.is_deleted = True
        else:
            self.is_deleted = False
        self.address = TrackSector(track, sector)
        self.raw_filename = raw_filename
        self.filename = "".join([chr(x & 0x7F) for x in raw_filename]).strip()
        return self

    @classmethod
    def create(cls, fs: "AppleDOSFilesystem") -> "AppleDOSDirectoryEntry":
        self = cls(fs)
        self.address = TrackSector(0, 0)
        self.raw_file_type = 0
        self.is_locked = False
        self.is_deleted = False
        self.filename = ""
        self.raw_filename = b""
        self.length = 0
        return self

    def write_buffer(self, buffer: bytearray, position: int = 0) -> None:
        """
        Write the directory entry
        """
        track = self.address.track
        sector = self.address.sector
        raw_filename = appledos_filename_to_raw_filename(self.filename)
        raw_file_type = self.raw_file_type | (LOCKED_FLAG if self.is_locked else 0)
        if self.is_deleted:
            track = DELETED_TRACK
            raw_filename = raw_filename[:-1] + bytes([self.address.track])
        struct.pack_into(
            FILE_DESCRIPTIVE_ENTRY_FORMAT,
            buffer,
            position,
            track,
            sector,
            raw_file_type,
            raw_filename,
            self.length,
        )

    @property
    def is_empty(self) -> bool:
        """
        If track is 0, the entry is assumed to never have been used and
        is available for use.
        """
        return self.address.track == 0

    @property
    def file_type(self) -> t.Optional[str]:
        """
        File type (e.g. DATA)
        """
        return FILE_TYPES.get(self.raw_file_type)

    def read_bytes(self, file_mode: t.Optional[str] = None) -> bytes:
        """Get the content of the file"""
        data = super().read_bytes()
        if self.file_type == "B":
            # Binary File Format on Disk
            # +------------------+-----------------+------------------
            # | Address (2 byte) | Length (2 byte) | Memory image ...
            # +------------------+-----------------+------------------
            # https://archive.org/details/beneath-apple-dos-prodos-2020/page/42/mode/2up
            address, length = struct.unpack_from(BINARY_FILE_FORMAT, data, 0)
            prodos_file_info = ProDOSFileInfo(0xFF, PRODOS_BIN_FILE_TYPE, address)
            data = encode_apple_single(prodos_file_info, data[struct.calcsize(BINARY_FILE_FORMAT) :])
        elif self.file_type == "A" or self.file_type == "I":
            # Integer/Applesoft Basic File Format on Disk
            # +-----------------+--------------------------
            # | Length (2 byte) | Program memory image ...
            # +-----------------+--------------------------
            # https://archive.org/details/beneath-apple-dos-prodos-2020/page/44/mode/2up
            length = struct.unpack_from(BASIC_FILE_FORMAT, data, 0)[0]
            data = data[struct.calcsize(BASIC_FILE_FORMAT) : -length]
        return data

    def blocks(self, include_indexes: bool = False) -> t.Iterator[TrackSector]:
        """
        Iterate over the sectors of the file

        Each file is associated with a "Track/Sector List"
        It contains a list of track/sector pointer pairs
        which sequentially list the data sectors which make up the file.

        https://archive.org/details/Beneath_Apple_DOS_alt/page/n21/mode/2up
        """
        address = self.address
        while address.track != 0:
            if include_indexes:
                yield address
            # Read the T/S list sector
            buffer = self.fs.read_sector(address)
            # Read up to 120 more Track/Sector pairs
            for i in range(TRACK_SECTOR_OFFSET, SECTOR_SIZE, 2):
                data_address = TrackSector(buffer[i], buffer[i + 1])
                if data_address.track == 0 and data_address.sector == 0:
                    break
                yield data_address
            # Track and sector number of next T/S List sector
            address = TrackSector(buffer[NEXT_TRACK_OFFSET], buffer[NEXT_SECTOR_OFFSET])

    @property
    def fullname(self) -> str:
        return self.filename

    @property
    def basename(self) -> str:
        return self.filename

    def get_length(self) -> int:
        """
        Get the length in sectors
        """
        return self.length

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        return self.length * SECTOR_SIZE

    def get_block_size(self) -> int:
        """
        Get file sector size in bytes
        """
        return SECTOR_SIZE

    def delete(self) -> bool:
        """
        Delete the file
        """
        # Update the catalog
        found = False
        catalog = AppleDOSCatalog.read(self.fs)
        for entry in catalog.iterdir():
            if entry.address == self.address:
                entry.is_deleted = True
                found = True
                break
        if not found:
            return False
        catalog.write()
        # Update the bitmap
        vtoc = AppleDOSVTOC.read(self.fs)
        for address in self.blocks(include_indexes=True):
            if address.track != 0 or address.sector != 0:
                vtoc.set_free(address)
        vtoc.write()
        return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        catalog = AppleDOSCatalog.read(self.fs)
        for entry in catalog.iterdir():
            if entry.address == self.address:
                catalog.write()
                return True
        return False

    def open(self, file_mode: t.Optional[str] = None) -> AppleDOSFile:
        """
        Open a file
        """
        return AppleDOSFile(self, file_mode)

    def __str__(self) -> str:
        return (
            f"Track: {self.address.track:>3}  Sector: {self.address.sector:>3}  Length: {self.length:>5}  "
            f"File type: {self.file_type}  {'Deleted' if self.is_deleted else 'Locked' if self.is_locked else '':<7} "
            f"{self.filename}"
        )

    def __repr__(self) -> str:
        return str(self)


class AppleDOSFilesystem(AbstractFilesystem, AppleDisk):
    """
    Apple II DOS 3.x Filesystem

    https://archive.org/details/Beneath_Apple_DOS_alt/page/n17/mode/2up
    https://archive.org/details/beneath-apple-dos-prodos-2020/page/30/mode/2up
    """

    fs_name = "appledos"
    fs_description = "Apple II DOS 3.x"

    catalog_address: TrackSector  # first catalog address
    number_of_tracks: int  # Number of tracks on disk
    sectors_per_track: int  # Sectors per track

    def __init__(self, file: "AbstractFile"):
        super().__init__(file, rx_device_support=False)

    @classmethod
    def mount(cls, file: "AbstractFile", strict: bool = True) -> "AbstractFilesystem":
        self = cls(file)
        vtoc: t.Optional[AppleDOSVTOC] = None
        # Read VTOC in DOS order
        self.prodos_order = False
        try:
            dos_vtoc = AppleDOSVTOC.read(self)
            dos_files = len(AppleDOSCatalog.read(self, dos_vtoc.catalog_address).directory_entries)
        except Exception:
            dos_vtoc = None
            dos_files = 0
        self.prodos_order = True
        # Read VTOC in ProDOS order
        try:
            prodos_vtoc = AppleDOSVTOC.read(self)
            prodos_files = len(AppleDOSCatalog.read(self, prodos_vtoc.catalog_address).directory_entries)
        except Exception:
            prodos_vtoc = None
            prodos_files = 0
        # Choose the order with the most files
        if prodos_files > dos_files:
            self.prodos_order = True
            vtoc: AppleDOSVTOC = prodos_vtoc  # type: ignore
        else:
            self.prodos_order = False
            vtoc = dos_vtoc  # type: ignore
        if vtoc is None or not vtoc.is_valid:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        self.catalog_address = vtoc.catalog_address
        if vtoc.number_of_tracks > 0:
            self.number_of_tracks = vtoc.number_of_tracks
        if vtoc.sectors_per_track > 0:
            self.sectors_per_track = vtoc.sectors_per_track
        return self

    def filter_entries_list(
        self,
        pattern: t.Optional[str],
        include_all: bool = False,
        expand: bool = True,
        wildcard: bool = True,
    ) -> t.Iterator["AppleDOSDirectoryEntry"]:
        if pattern:
            pattern = appledos_canonical_filename(pattern, wildcard=wildcard)
        catalog = AppleDOSCatalog.read(self)
        for entry in catalog.iterdir(include_deleted=include_all):
            if filename_match(entry.basename, pattern, wildcard):
                yield entry

    @property
    def entries_list(self) -> t.Iterator["AppleDOSDirectoryEntry"]:
        catalog = AppleDOSCatalog.read(self)
        yield from catalog.iterdir()

    def get_file_entry(self, fullname: str, include_deleted: bool = False) -> AppleDOSDirectoryEntry:
        """
        Get the file entry for a given path
        """
        fullname = appledos_canonical_filename(fullname)  # type: ignore
        catalog = AppleDOSCatalog.read(self)
        for entry in catalog.iterdir(include_deleted=include_deleted):
            if entry.fullname == fullname:
                return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

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
        # Check if the file is an AppleSingle file and extract the content and metadata
        try:
            content, _, prodos_file_info = decode_apple_single(content)
            if prodos_file_info is not None and file_type is None:
                # Map ProDOS file type to DOS file type
                if prodos_file_info.file_type == PRODOS_TXT_FILE_TYPE:
                    file_type = "T"
                elif prodos_file_info.file_type == PRODOS_BIN_FILE_TYPE:
                    # Binary File Format on Disk
                    # +------------------+-----------------+------------------
                    # | Address (2 byte) | Length (2 byte) | Memory image ...
                    # +------------------+-----------------+------------------
                    # https://archive.org/details/beneath-apple-dos-prodos-2020/page/42/mode/2up
                    file_type = "B"
                    header = struct.pack(BINARY_FILE_FORMAT, prodos_file_info.aux_type, len(content))
                    content = header + content
                elif prodos_file_info.file_type in (PRODOS_INT_FILE_TYPE, PRODOS_BAS_FILE_TYPE):
                    # Integer/Applesoft Basic File Format on Disk
                    # +-----------------+--------------------------
                    # | Length (2 byte) | Program memory image ...
                    # +-----------------+--------------------------
                    # https://archive.org/details/beneath-apple-dos-prodos-2020/page/44/mode/2up
                    if prodos_file_info.file_type == PRODOS_INT_FILE_TYPE:
                        file_type = "I"
                    else:
                        file_type = "A"
                    header = struct.pack(BASIC_FILE_FORMAT, len(content))
                    content = header + content
                elif prodos_file_info.file_type == PRODOS_REL_FILE_TYPE:
                    file_type = "R"
        except ValueError:
            pass

        number_of_blocks = int(math.ceil(len(content) / SECTOR_SIZE))
        entry = self.create_file(
            fullname=fullname,
            number_of_blocks=number_of_blocks,
            creation_date=creation_date,
            file_type=file_type,
        )
        if entry is not None:
            content = content + (b"\0" * SECTOR_SIZE)  # pad with zeros
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
    ) -> t.Optional[AppleDOSDirectoryEntry]:
        """
        Create a new file
        """
        fullname = appledos_canonical_filename(fullname)  # type: ignore
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass
        vtoc = AppleDOSVTOC.read(self)
        catalog = AppleDOSCatalog.read(self)
        entry = catalog.create_file(
            vtoc=vtoc,
            fullname=fullname,
            number_of_blocks=number_of_blocks,
            file_type=file_type,
        )
        vtoc.write()
        catalog.write()
        return entry

    def chdir(self, fullname: str) -> bool:
        return False

    def isdir(self, fullname: str) -> bool:
        return False

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        if not options.get("brief"):
            sys.stdout.write("DISK VOLUME\n\n")
        if pattern:
            pattern = appledos_canonical_filename(pattern, wildcard=True)
        for x in self.filter_entries_list(pattern, include_all=options.get("full", False)):
            if options.get("brief"):
                sys.stdout.write(f"{x.fullname}\n")
            else:
                prefix = "X" if x.is_deleted else "*" if x.is_locked else " "
                sys.stdout.write(f"{prefix}{x.file_type} {x.length:>03} {x.fullname}\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        """
        Examine the filesystem
        """
        if options.get("bitmap"):
            # Examine the bitmap
            vtoc = AppleDOSVTOC.read(self)
            for track, bits in enumerate(vtoc.bitmaps):
                sys.stdout.write(f"Track {track:>3}: {bits:032b}\n")
        elif not arg:
            # Examine the entire filesystem
            vtoc = AppleDOSVTOC.read(self)
            sys.stdout.write(
                dump_struct(
                    vtoc.__dict__,
                    exclude=["fs", "bitmaps"],
                    include=["catalog_address"],
                )
            )
            sys.stdout.write("\n")
            catalog = AppleDOSCatalog.read(self)
            sys.stdout.write(
                dump_struct(
                    catalog.__dict__,
                    exclude=["fs", "directory_entries"],
                    include=["catalog_address"],
                )
            )
            sys.stdout.write("\n\nDirectory entries:\n")
            for i, entry in enumerate(catalog.directory_entries):
                if not entry.is_empty:
                    sys.stdout.write(f"{i:>3}#  {entry}\n")
        else:
            # Examine by path
            entry = self.get_file_entry(arg, include_deleted=True)
            entry_dict = dict(entry.__dict__)
            entry_dict["address"] = str(entry.address)
            entry_dict["blocks"] = list(entry.blocks())  # type: ignore
            sys.stdout.write(dump_struct(entry_dict, exclude=["fs"]) + "\n")
            vtoc = AppleDOSVTOC.read(self)
            for sector in entry.blocks(include_indexes=True):
                sys.stdout.write(f"Sector {sector} is {'free' if vtoc.is_free(sector) else 'used'}\n")

    def dump(self, fullname: t.Optional[str], start: t.Optional[int] = None, end: t.Optional[int] = None) -> None:
        """
        Dump the content of a file or a range of blocks
        """
        if fullname:
            if start is None:
                start = 0
            if end is None:
                entry = self.get_file_entry(fullname)
                end = entry.get_length() - 1
            f = self.open_file(fullname, file_mode=IMAGE)
            try:
                for block_number in range(start, end + 1):
                    data = f.read_block(block_number)
                    sys.stdout.write(f"\nBLOCK NUMBER   {block_number:08}\n")
                    if entry.raw_file_type == FILE_TYPE_TEXT:  # type: ignore
                        # Remove the high bit for text files
                        data = bytes([x & 0x7F for x in data])
                    hex_dump(data)
            finally:
                f.close()
        else:
            if start is None:
                start = 0
            if end is None:
                if start == 0:
                    end = self.number_of_tracks
                else:
                    end = start + 1
            for track in range(start, end):
                for sector in range(0, self.sectors_per_track):
                    data = self.read_sector(TrackSector(track, sector))
                    sys.stdout.write(f"\nTRACK {track:02}  SECTOR {sector:02}\n")
                    hex_dump(data)

    def get_size(self) -> int:
        """
        Get filesystem size in bytes
        """
        return self.f.get_size()

    def initialize(self, **kwargs: t.Union[bool, str]) -> None:
        """
        Initialize the filesystem
        """
        vtoc = AppleDOSVTOC.create(self)
        catalog = AppleDOSCatalog.create(self, vtoc)
        catalog.write()
        vtoc.write()
        self.catalog_address = vtoc.catalog_address
        self.number_of_tracks = vtoc.number_of_tracks
        self.sectors_per_track = vtoc.sectors_per_track

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
