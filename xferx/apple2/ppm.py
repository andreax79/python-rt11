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
import typing as t
from datetime import date, datetime

from ..commons import BLOCK_SIZE
from .pascalfs import pascal_to_str, str_to_pascal
from .prodosfs import (
    DEFAULT_ACCESS,
    PAS_FILE_TYPE,
    PASCAL_AREA_STORAGE_TYPE,
    AbstractDirectoryFileEntry,
    FileEntry,
    ProDOSAbstractDirEntry,
    ProDOSBitmap,
    ProDOSFilesystem,
    RegularFileEntry,
    date_to_prodos,
    parse_file_aux_type,
)

PPM_HEADER_BLOCKS = 2  # Pascal Volume Header length in blocks
PPM_HEADER_FORMAT = "<HH4s"
PPM_MAX_VOLUMES = 31
PPM_INFO_FORMAT = "<HHBBH"  # Volume info
PPM_INFO_SIZE = struct.calcsize(PPM_INFO_FORMAT)
PPM_DESCRIPTION_OFFSET = 0x100
PPM_DESCRIPTION_LENGTH = 16  # bytes
PPM_NAME_OFFSET = 0x300
PPM_NAME_LENGTH = 8  # bytes

__all__ = [
    "PPMDirectoryEntry",
    "PPMVolumeEntry",
]


class PPMDirectoryEntry(AbstractDirectoryFileEntry):
    """
    Pascal ProFile Manager (PPM) Partition
    https://ciderpress2.com/formatdoc/PPM-notes.html

    Pascal ProFile Manager Manual
    https://archive.org/details/a2ppfmm/

    The Pascal Partition is a contiguous file that starts
    at key_pointer and extends to the end of the disk.
    It is created by the Apple Pascal ProFile Manager,
    and it has file type PAS and storage type 4.

    Internally, the Pascal Partition is divided into 1-31 volumes.

      Length                                Offset
             +----------------------------+
     2 block |   Pascal Volume Directory  | key_pointer
             |                            |
             |----------------------------|
             |      Pascal Volume 1       |
             |                            |
             |----------------------------|
             |      Pascal Volume 2       |
             |                            |
             |----------------------------|
             | ...                        |
             |                            | disk last block
             +----------------------------+

    Pascal Volume Directory

       Field                                Byte of
      Length                                Header
             +----------------------------+
     2 byte  |       Size in blocks       | $00
             |                            | $01
             +----------------------------+
     2 byte  |     Number of volumes      | $02
             |                            | $03
             +----------------------------+
     4 byte  |  'PPM' as Pascal string    | $04
             |                            | $07
             |----------------------------|
             /                            /
             |----------------------------| Volume n info
     2 byte  |   Volume n start block     | $00 + n * $8
             |                            |
             |----------------------------|
     2 byte  |      Volume n length       | $02 + n * $8
             |         in blocks          |
             |----------------------------|
     1 byte  |   Volume n default unit    | $04 + n * $8
             |----------------------------|
     1 byte  | Volume n write protection  | $05 + n * $8
             |----------------------------|
     2 byte  |          Reserved          | $06 + n * $8
             |                            |
             |----------------------------|
             /                            /
             |----------------------------|
     16 byte |   Volume n description as  | $100 + n * $16
             |       Pascal string        |
             |----------------------------|
             /                            /
             |----------------------------|
      8 byte |     Volume n name as       | $300 + n * $8
             |       Pascal string        |
             |----------------------------|

    """

    volumes: int = 0  # Allocated volumes (up to 31)
    ppm_name: str = ""  # 'PPM' as Pascal string

    @classmethod
    def read(
        cls, fs: "ProDOSFilesystem", parent: t.Optional["FileEntry"], buffer: bytes, position: int = 0
    ) -> "FileEntry":
        self: PPMDirectoryEntry = super().read(fs, parent, buffer, position)  # type: ignore
        # Read the header
        buffer = self.fs.read_block(self.key_pointer, number_of_blocks=PPM_HEADER_BLOCKS)
        (
            _,  # size in blocks
            self.volumes,  # number of allocated volumes
            ppm_name,  # 'PPM' as Pascal string
        ) = struct.unpack_from(PPM_HEADER_FORMAT, buffer, 0)
        self.ppm_name = pascal_to_str(ppm_name)
        return self

    @classmethod
    def create(
        cls,
        fs: "ProDOSFilesystem",
        parent: t.Optional["AbstractDirectoryFileEntry"],
        filename: str,
        length: int,  # Length in blocks
        bitmap: "ProDOSBitmap",
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # optional creation date
        access: int = DEFAULT_ACCESS,  # optional access
        file_type: t.Optional[str] = None,  # optional file type
        aux_type: int = 0,  # optional aux type
        length_bytes: t.Optional[int] = None,  # optional length int bytes
        resource_length_bytes: t.Optional[int] = None,  # not used
    ) -> "PPMDirectoryEntry":
        """
        Create a new Pascal ProFile Manager (PPM) Partition
        """
        # Allocate the contiguous blocks
        last_block = fs.get_size() // BLOCK_SIZE - 1
        first_block = last_block - length
        for block_number in range(first_block, last_block + 1):
            if not bitmap.is_free(block_number):
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            bitmap.set_used(block_number)
        # Create the entry
        self = cls(fs, parent)
        self.ppm_name = "PPM"
        self.volumes = 0
        self.storage_type = PASCAL_AREA_STORAGE_TYPE
        self.blocks_used = length
        self.length = length_bytes if length_bytes is not None else length * BLOCK_SIZE
        self.filename = filename
        self.key_pointer = first_block
        self.access = access
        if isinstance(creation_date, datetime):
            self.last_mod_date = creation_date
        elif isinstance(creation_date, date):
            self.last_mod_date = datetime.combine(creation_date, datetime.min.time())
        else:
            self.last_mod_date = datetime.now()
        self.raw_creation_date = date_to_prodos(self.last_mod_date)
        self.prodos_file_type, self.aux_type = parse_file_aux_type(
            file_type, default=PAS_FILE_TYPE, default_aux_type=aux_type
        )
        # Write the volume header
        buffer = bytearray(self.fs.read_block(self.key_pointer, number_of_blocks=2))
        struct.pack_into(
            PPM_HEADER_FORMAT,
            buffer,
            0,
            self.blocks_used,
            0,  # volumes
            self.ppm_name.encode("ascii"),
        )
        self.fs.write_block(buffer, self.key_pointer, number_of_blocks=2)
        # Write the entry
        if parent is not None:
            try:
                parent.update_dir_entry(self, create=True)
            except OSError:
                # Directory is full, grow it
                parent.grow(bitmap)
                parent.update_dir_entry(self, create=True)
        return self

    def update_dir_entry(
        self,
        entry: "ProDOSAbstractDirEntry",
        entry_class: t.Type["ProDOSAbstractDirEntry"] = FileEntry,
        create: bool = False,
        delete: bool = False,
    ) -> t.Optional[t.Tuple[int, int]]:
        """
        Update/create/delete a directory entry
        """
        volume_entry: PPMVolumeEntry = entry  # type: ignore
        if create:
            return self.create_volume_entry(volume_entry)
        elif delete:
            return self.delete_volume_entry(volume_entry)
        else:
            return self.update_volume_entry(volume_entry)

    def create_volume_entry(self, volume_entry: "PPMVolumeEntry") -> t.Optional[t.Tuple[int, int]]:
        """
        Create a volume entry in the Pascal Volume Directory
        """
        start = self.key_pointer + PPM_HEADER_BLOCKS
        position = start
        volume_number = 0
        volumes: list[PPMVolumeEntry] = list(self.iterdir())
        for volume in volumes:
            if volume.key_pointer - position >= volume_entry.blocks_used:
                break
            position = volume.key_pointer + volume.blocks_used
        volume_number += 1
        if volume_number > PPM_MAX_VOLUMES:
            raise OSError(errno.ENOSPC, "Too many volumes")
        if position + volume_entry.blocks_used >= self.fs.get_size() // BLOCK_SIZE:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        volume_entry.key_pointer = position
        volume_entry.volume_number = volume_number
        volumes.append(volume_entry)
        self.write_pascal_volume_directory(volumes)
        return (self.key_pointer, volume_number)

    def delete_volume_entry(self, volume_entry: "PPMVolumeEntry") -> t.Optional[t.Tuple[int, int]]:
        """
        Delete a volume entry from the Pascal Volume Directory
        """
        volumes: list[PPMVolumeEntry] = []
        volume_number = 0
        for i, volume in enumerate(self.iterdir(), start=1):
            if volume.key_pointer != volume_entry.key_pointer:
                volumes.append(volume)
            else:
                volume_number = i
        if volume_number == 0:
            return None
        else:
            self.write_pascal_volume_directory(volumes)
            return (self.key_pointer, volume_number)

    def update_volume_entry(self, volume_entry: "PPMVolumeEntry") -> t.Optional[t.Tuple[int, int]]:
        """
        Update a volume entry in the Pascal Volume Directory
        """
        volumes: list[PPMVolumeEntry] = []
        volume_number = 0
        for i, volume in enumerate(self.iterdir(), start=1):
            if volume.key_pointer == volume_entry.key_pointer:
                volumes.append(volume_entry)
                volume_number = i
            else:
                volumes.append(volume)
        if volume_number == 0:
            return None
        else:
            self.write_pascal_volume_directory(volumes)
            return (self.key_pointer, volume_number)

    def write_pascal_volume_directory(self, volumes: t.List["PPMVolumeEntry"]) -> None:
        """
        Write the Pascal Volume Directory to the disk
        """
        volumes = sorted(volumes, key=lambda x: x.key_pointer)
        self.volumes = len(volumes)
        buffer = bytearray(self.fs.read_block(self.key_pointer, number_of_blocks=PPM_HEADER_BLOCKS))
        # Write the header
        struct.pack_into(
            PPM_HEADER_FORMAT,
            buffer,
            0,
            self.blocks_used,
            self.volumes,
            self.ppm_name.encode("ascii"),
        )
        # Write the volumes
        for volume_number, volume in enumerate(volumes, start=1):
            position = volume_number * PPM_INFO_SIZE
            volume.write_buffer(buffer, position)
        self.fs.write_block(bytes(buffer), self.key_pointer, number_of_blocks=PPM_HEADER_BLOCKS)

    def blocks(self, include_indexes: bool = False) -> t.Iterator[int]:
        """
        Iterate over the blocks used by the PPM Partition
        """
        yield from range(self.key_pointer, self.fs.get_size() // BLOCK_SIZE)

    def iterdir(self) -> t.Iterator["PPMVolumeEntry"]:
        """
        Read the volumes from the Pascal Volume Directory
        """
        buffer = self.fs.read_block(self.key_pointer, number_of_blocks=PPM_HEADER_BLOCKS)
        for volume_number in range(1, self.volumes + 1):
            yield PPMVolumeEntry.read(self.fs, self, buffer, volume_number * PPM_INFO_SIZE)

    def grow(self, bitmap: "ProDOSBitmap") -> None:
        """
        Grow the directory (not supported by PPM))
        """
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))


class PPMVolumeEntry(RegularFileEntry):
    """
    Volume (a contiguous area) in the
    Pascal ProFile Manager (PPM) Partition
    """

    volume_number: int = 0  # Volume number (1-31)
    volume_name: str = ""  # Volume name (8 characters)
    description: str = ""  # Volume description (15 characters)
    default_unit: int = 0  # Default unit number
    write_protection: int = 0  # Write protection flag

    @classmethod
    def read(
        cls,
        fs: "ProDOSFilesystem",
        parent: t.Optional["FileEntry"],
        buffer: bytes,
        position: int = 0,
    ) -> "PPMVolumeEntry":
        assert parent is not None
        self = PPMVolumeEntry(fs, parent)
        self.volume_number = position // PPM_INFO_SIZE
        (
            self.key_pointer,
            self.blocks_used,
            self.default_unit,
            self.write_protection,
            _,
        ) = struct.unpack_from(PPM_INFO_FORMAT, buffer, self.volume_number * PPM_INFO_SIZE)
        description_position = PPM_DESCRIPTION_OFFSET + self.volume_number * PPM_DESCRIPTION_LENGTH
        self.description = pascal_to_str(buffer[description_position : description_position + 8])
        volume_name_position = PPM_NAME_OFFSET + self.volume_number * PPM_NAME_LENGTH
        self.volume_name = pascal_to_str(buffer[volume_name_position : volume_name_position + 8])
        self.filename = self.volume_name
        self.prodos_file_type = parent.prodos_file_type
        self.access = parent.access
        self.length = self.blocks_used * BLOCK_SIZE
        self.storage_type = PASCAL_AREA_STORAGE_TYPE
        return self

    @classmethod
    def create(
        cls,
        fs: "ProDOSFilesystem",
        parent: t.Optional["AbstractDirectoryFileEntry"],
        filename: str,
        length: int,  # Length in blocks
        bitmap: "ProDOSBitmap",  # not used
        creation_date: t.Optional[t.Union[date, datetime]] = None,  # not used
        access: int = DEFAULT_ACCESS,  # not used
        file_type: t.Optional[str] = None,  # not used
        aux_type: int = 0,  # not used
        length_bytes: t.Optional[int] = None,  # not used
        resource_length_bytes: t.Optional[int] = None,  # not used
    ) -> "PPMVolumeEntry":
        """
        Create a new Pascal Volume
        """
        # Create the entry
        self = cls(fs, parent)
        self.volume_name = filename
        self.description = ""
        self.default_unit = 0
        self.write_protection = 0
        self.storage_type = PASCAL_AREA_STORAGE_TYPE
        self.blocks_used = length
        self.length = length * BLOCK_SIZE
        self.access = access
        # Write the entry
        if parent is not None:
            parent.update_dir_entry(self, create=True)  # Set volume_number and position
        return self

    def write_buffer(self, buffer: bytearray, position: int = 0) -> None:
        """
        Write the Volume entry to a buffer
        """
        volume_number = position // PPM_INFO_SIZE
        struct.pack_into(
            PPM_INFO_FORMAT,
            buffer,
            position,
            self.key_pointer,
            self.blocks_used,
            self.default_unit,
            self.write_protection,
            0,  # reserved
        )
        # Description
        description_position = PPM_DESCRIPTION_OFFSET + volume_number * PPM_DESCRIPTION_LENGTH
        description = str_to_pascal(self.description)[:PPM_DESCRIPTION_LENGTH]
        buffer[description_position : description_position + len(description)] = description
        # Name
        volume_name_position = PPM_NAME_OFFSET + volume_number * PPM_NAME_LENGTH
        volume_name = str_to_pascal(self.volume_name)[:PPM_NAME_LENGTH]
        buffer[volume_name_position : volume_name_position + len(volume_name)] = volume_name

    def delete(self) -> bool:
        """
        Delete the directory end
        """
        if not isinstance(self.parent, PPMDirectoryEntry):
            return False
        if not self.parent.update_dir_entry(self, delete=True):
            return False
        return True

    def write(self) -> bool:
        """
        Write the directory entry
        """
        if not isinstance(self.parent, PPMDirectoryEntry):
            return False
        if not self.parent.update_dir_entry(self):
            return False
        return True

    def blocks(self, include_indexes: bool = False) -> t.Iterator[int]:
        yield from range(self.key_pointer, self.key_pointer + self.blocks_used)

    def __str__(self) -> str:
        return f"{self.filename:<16} Default unit: #{self.default_unit:<2d}     {self.key_pointer:>7} {self.blocks_used:>7} blocks  Description: {self.description:<16}"

    def __repr__(self) -> str:
        return str(self.__dict__)
