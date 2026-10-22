"""Microsoft Standalone Disk BASIC FAT8.

Reference: BASIC-80 5.0 Reference Manual (1979), Appendix H.10-H.11.
https://bitsavers.org/pdf/microsoft/cpm/Microsoft_BASIC-80_5.0_Reference_1979.pdf
"""

import errno
import math
import os
import sys
from dataclasses import dataclass
import typing as t

from ..abstract import AbstractBlockFilesystem, AbstractFile, XFERXPath
from ..commons import READ_FILE_FULL, filename_match
from ..device.abstract import AbstractDevice
from ..device.imd import IMDBlockDevice

DIRECTORY_ENTRY_SIZE = 16

FAT_FREE = 0xFF
FAT_RESERVED = 0xFE
FAT_LAST = 0xC0

ATTR_BINARY = 0x80  # Binary file
ATTR_FORCE_READ = 0x40  # Force read after write check
ATTR_EBCDIC = 0x20  # EBCDIC file
ATTR_PROTECTED = 0x10  # Write protected file


@dataclass
class FAT8Geometry:
    sector_size: int  # Size of a sector in bytes (128 or 256)
    sectors_per_track: int  # Number of sectors per track (16 or 26)
    cluster_count: int  # Total number of clusters on the disk
    interleaved_heads: bool = False  # Interleaved heads

    @property
    def sectors_per_cluster(self) -> int:
        return self.sectors_per_track // 2

    def cluster_sector(self, cluster: int) -> int:
        if self.interleaved_heads:
            # Toshiba numbers the first halves of both sides before the second halves.
            cylinder, part = divmod(cluster, 4)
            cluster = cylinder * 4 + (part % 2) * 2 + part // 2
        return cluster * self.sectors_per_cluster


class FAT8File(AbstractFile):
    def __init__(self, entry: "FAT8Path"):
        self.entry = entry
        self.current_position = 0
        self.closed = False

    @property
    def name(self) -> str:
        return self.entry.name

    def read_block(self, block_number: int, number_of_blocks: int = 1) -> bytes:
        sectors = self.entry.sectors()
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = len(sectors) - block_number
        if self.closed or block_number < 0 or number_of_blocks < 0:
            raise OSError(errno.EIO, "Invalid FAT8 file read")
        return b"".join(
            self.entry.fs.read_block(sector) for sector in sectors[block_number : block_number + number_of_blocks]
        )

    def write_block(self, buffer: t.Union[bytes, bytearray], block_number: int, number_of_blocks: int = 1) -> None:
        self.entry.fs.check_writable(self.entry)
        sectors = self.entry.sectors()
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > len(sectors)
            or len(buffer) < number_of_blocks * self.get_block_size()
        ):
            raise OSError(errno.EIO, "Invalid FAT8 file write")
        for i, sector in enumerate(sectors[block_number : block_number + number_of_blocks]):
            start = i * self.get_block_size()
            self.entry.fs.write_block(buffer[start : start + self.get_block_size()], sector)

    def get_size(self) -> int:
        return self.entry.get_size()

    def get_block_size(self) -> int:
        return self.entry.get_block_size()


class FAT8Path(XFERXPath):
    """
    FAT8 Directory entry

        +---------------------------------------+
     0  |                                       |
        /               File Name               /   9 bytes
        |                                       |
        +---------------------------------------+
     9  |              Attributes               |   1 byte
        +---------------------------------------+
    10  |             First Cluster             |   1 byte
        +---------------------------------------+
    11  |          Reserved (unused)            |   5 bytes
        +---------------------------------------+

    If the first byte of a filename is zero, that file entry slot is free.
    If the first byte is 255, that slot is the last occupied slot in the directory.

    BASIC-80 5.0 Reference Manual (1979), Appendix H.10-H.11, Pag 165
    https://bitsavers.org/pdf/microsoft/cpm/Microsoft_BASIC-80_5.0_Reference_1979.pdf
    """

    fs: "FAT8Filesystem"

    def __init__(self, fs: "FAT8Filesystem", slot: int = -1, raw: bytes = b""):
        super().__init__(fs)
        self.slot = slot
        self.raw = raw

    @property
    def fullname(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        return self.raw[:9].decode("ascii").rstrip().upper() if self.slot >= 0 else ""

    @property
    def parent(self) -> "FAT8Path":
        return self.fs.get_root()

    @property
    def attributes(self) -> int:
        return self.raw[9] if self.slot >= 0 else 0

    @property
    def file_type(self) -> t.Optional[str]:
        return "BINARY" if self.attributes & ATTR_BINARY else "ASCII"

    def is_binary(self) -> bool:
        """Binary file"""
        return bool(self.attributes & ATTR_BINARY)

    def is_force_read_after_write_check(self) -> bool:
        """Force read after write check"""
        return bool(self.attributes & ATTR_FORCE_READ)

    def is_ebcdic(self) -> bool:
        """EBCDIC file"""
        return bool(self.attributes & ATTR_EBCDIC)

    def is_protected(self) -> bool:
        """Write protected file"""
        return bool(self.attributes & ATTR_PROTECTED)

    def is_dir(self) -> bool:
        return self.slot < 0

    def is_mount(self) -> bool:
        return self.is_dir()

    def iterdir(self) -> t.Iterator["FAT8Path"]:
        if not self.is_dir():
            raise NotADirectoryError(self.name)
        yield from self.fs.entries_list

    def sectors(self) -> t.List[int]:
        if self.is_dir():
            raise IsADirectoryError(self.name)
        fat = self.fs.read_fat()
        return self.fs.chain_sectors(self.raw[10], fat)

    def get_length(self, fork: t.Optional[str] = None) -> int:
        return len(self.sectors()) if not self.is_dir() else 0

    def get_size(self, fork: t.Optional[str] = None) -> int:
        return self.get_length() * self.get_block_size()

    def get_block_size(self) -> int:
        return self.fs.geometry.sector_size

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> FAT8File:
        if self.is_dir():
            raise IsADirectoryError(self.name)
        return FAT8File(self)

    def delete(self) -> bool:
        self.fs.check_writable(self)
        fat, entries = self.fs.read_state()
        if self.slot < 0:
            raise OSError(errno.EPERM, "Cannot delete the root directory")
        if not any(entry.slot == self.slot and entry.raw == self.raw for entry in entries):
            raise FileNotFoundError(errno.ENOENT, "FAT8 directory entry has changed", self.name)
        clusters, _ = self.fs.cluster_chain(self.raw[10], fat)
        for cluster in clusters:
            fat[cluster] = FAT_FREE
        self.fs.write_entry(self.slot, b"\0" + self.raw[1:])
        self.fs.write_fat(fat)
        return True

    def __str__(self) -> str:
        return f"{self.name:<9} {self.get_length():>6}  {self.file_type:<6}  {self.attributes:02X}"


class FAT8Filesystem(AbstractBlockFilesystem):
    """
    Microsoft Standalone Disk BASIC FAT8.

    BASIC-80 5.0 Reference Manual (1979), Appendix H.10-H.11, Pag 164
    https://bitsavers.org/pdf/microsoft/cpm/Microsoft_BASIC-80_5.0_Reference_1979.pdf
    """

    fs_name = "fat8"
    fs_description = "Microsoft Standalone Disk BASIC FAT8"
    fs_platforms = ["8080", "z80"]
    fs_entry_metadata = ["file_type", "attributes"]

    geometry: FAT8Geometry
    directory_track: int

    @classmethod
    def mount(
        cls, file_or_dev: t.Union[AbstractFile, AbstractDevice], **kwargs: t.Union[bool, str]
    ) -> "FAT8Filesystem":
        self = cls(file_or_dev)
        if self.dev.read_block(0).startswith(b"IMD "):
            self.dev = IMDBlockDevice(self.dev.f)
        size = self.dev.get_size()
        # size = 327680
        geometries = []
        if isinstance(self.dev, IMDBlockDevice):
            geometries.append(FAT8Geometry(self.dev.sector_size, self.dev.sectors_per_track, self.dev.track_count * 2))
            if self.dev.sectors_per_track == 16 and self.dev.track_count in (70, 80):
                geometries.append(FAT8Geometry(self.dev.sector_size, 16, 140, True))
        else:
            for sector_size in (128, 256):
                for tracks in (35, 40, 70, 80):
                    if size == sector_size * 16 * tracks:
                        geometries.append(FAT8Geometry(sector_size, 16, tracks * 2))
                if size == sector_size * 26 * 77:
                    geometries.append(FAT8Geometry(sector_size, 26, 154))
                if size in (sector_size * 16 * 70, sector_size * 16 * 80):
                    geometries.append(FAT8Geometry(sector_size, 16, 140, True))
        errors = []
        for geometry in geometries:
            if geometry.sector_size not in (128, 256) or geometry.sectors_per_track not in (16, 26):
                continue
            if geometry.cluster_count > FAT_LAST:
                continue
            self.geometry = geometry
            self.dev.sector_size = geometry.sector_size
            for track in range(geometry.cluster_count // 2):
                self.directory_track = track
                # Reserved directory clusters identify candidate metadata tracks.
                try:
                    fat = self.read_block(self.fat_sector, self.fat_sectors)
                except OSError as exc:
                    errors.append(str(exc))
                    continue
                if len(fat) < geometry.cluster_count:
                    continue
                reserved = [
                    c
                    for c in range(geometry.cluster_count)
                    if geometry.cluster_sector(c) // geometry.sectors_per_track == track
                ]
                if len(reserved) != 2 or any(fat[c] != FAT_RESERVED for c in reserved):
                    continue
                try:
                    self.read_state()
                except OSError as exc:
                    errors.append(str(exc))
                    continue
                return self
        detail = f": {errors[0]}" if errors else ""
        raise OSError(errno.EIO, f"Unsupported or invalid FAT8 disk layout{detail}")

    @classmethod
    def initialize(
        cls, file_or_dev: t.Union[AbstractFile, AbstractDevice], **kwargs: t.Union[bool, str]
    ) -> "FAT8Filesystem":
        """Format a disk image with an empty FAT8 directory and allocation table."""
        self = cls(file_or_dev)
        if self.dev.read_block(0).startswith(b"IMD "):
            self.dev = IMDBlockDevice(self.dev.f)
        size = self.dev.get_size()
        geometries = []
        for sector_size in (128, 256):
            for tracks in (35, 40, 70, 80):
                if size == sector_size * 16 * tracks:
                    geometries.append(FAT8Geometry(sector_size, 16, tracks * 2))
            if size == sector_size * 26 * 77:
                geometries.append(FAT8Geometry(sector_size, 26, 154))
            if size in (sector_size * 16 * 70, sector_size * 16 * 80):
                geometries.append(FAT8Geometry(sector_size, 16, 140, True))
        sector_size_option = kwargs.get("sector_size")
        if sector_size_option is not None:
            try:
                requested_sector_size = int(sector_size_option)
            except (TypeError, ValueError):
                raise ValueError("FAT8 sector_size must be 128 or 256")
            geometries = [g for g in geometries if g.sector_size == requested_sector_size]
        if isinstance(self.dev, IMDBlockDevice):
            geometries.insert(
                0,
                FAT8Geometry(self.dev.sector_size, self.dev.sectors_per_track, self.dev.track_count * 2),
            )
        geometry = next(
            (
                candidate
                for candidate in geometries
                if candidate.sector_size in (128, 256)
                and candidate.sectors_per_track in (16, 26)
                and candidate.cluster_count <= FAT_LAST
            ),
            None,
        )
        if geometry is None:
            raise OSError(errno.EINVAL, "Unsupported FAT8 disk size or sector size")
        self.geometry = geometry
        self.dev.sector_size = geometry.sector_size
        self.directory_track = 0
        if self.directory_sectors <= 0:
            raise OSError(errno.EINVAL, "FAT8 geometry has no directory space")

        total_sectors = size // geometry.sector_size
        for block in range(0, total_sectors, 64):
            count = min(64, total_sectors - block)
            self.write_block(bytes(count * geometry.sector_size), block, count)

        directory = self.directory_sector
        self.write_block(
            bytes([0xFF]) * (self.directory_sectors * geometry.sector_size), directory, self.directory_sectors
        )

        fat = bytearray([FAT_FREE]) * geometry.cluster_count
        reserved = [
            cluster
            for cluster in range(geometry.cluster_count)
            if geometry.cluster_sector(cluster) // geometry.sectors_per_track == self.directory_track
        ]
        if len(reserved) != 2:
            raise OSError(errno.EINVAL, "FAT8 geometry must reserve two directory clusters")
        for cluster in reserved:
            fat[cluster] = FAT_RESERVED
        self.write_fat(fat)
        return cls.mount(self.dev)

    @property
    def fat_sectors(self) -> int:
        return math.ceil(self.geometry.cluster_count / self.geometry.sector_size)

    @property
    def directory_sectors(self) -> int:
        return self.geometry.sectors_per_track - 1 - 3 * self.fat_sectors

    @property
    def directory_sector(self) -> int:
        return self.directory_track * self.geometry.sectors_per_track

    @property
    def fat_sector(self) -> int:
        return self.directory_sector + self.directory_sectors + 1

    def read_fat(self) -> bytearray:
        """
        Read the FAT8 allocation table
        """
        copies = [self.read_block(self.fat_sector + i * self.fat_sectors, self.fat_sectors) for i in range(3)]
        count = self.geometry.cluster_count
        if any(len(fat) != self.fat_sectors * self.geometry.sector_size for fat in copies):
            raise OSError(errno.EIO, "Truncated FAT8 allocation table")
        if any(fat[:count] != copies[0][:count] for fat in copies[1:]):
            raise OSError(errno.EIO, "FAT8 allocation table copies disagree")
        fat = bytearray(copies[0][:count])
        if any(
            value >= count
            and value not in (FAT_FREE, FAT_RESERVED)
            and not FAT_LAST <= value <= FAT_LAST + self.geometry.sectors_per_cluster
            for value in fat
        ):
            raise OSError(errno.EIO, "Invalid FAT8 allocation table value")
        return fat

    def cluster_chain(self, first: int, fat: bytearray) -> t.Tuple[t.List[int], int]:
        clusters: t.List[int] = []
        seen: t.Set[int] = set()
        cluster = first
        while True:
            if cluster >= len(fat) or cluster in seen:
                raise OSError(errno.EIO, "Invalid or cyclic FAT8 cluster chain")
            seen.add(cluster)
            clusters.append(cluster)
            value = fat[cluster]
            if FAT_LAST <= value <= FAT_LAST + self.geometry.sectors_per_cluster:
                return clusters, value - FAT_LAST
            if value in (FAT_FREE, FAT_RESERVED):
                raise OSError(errno.EIO, "FAT8 file references a free or reserved cluster")
            cluster = value

    def chain_sectors(self, first: int, fat: bytearray) -> t.List[int]:
        clusters, last = self.cluster_chain(first, fat)
        return [
            self.geometry.cluster_sector(cluster) + offset
            for i, cluster in enumerate(clusters)
            for offset in range(last if i == len(clusters) - 1 else self.geometry.sectors_per_cluster)
        ]

    def read_state(self) -> t.Tuple[bytearray, t.List[FAT8Path]]:
        fat = self.read_fat()
        entries = list(self.entries_list)
        occupied: t.Set[int] = set()
        names: t.Set[str] = set()
        for entry in entries:
            if entry.name in names:
                raise OSError(errno.EIO, "Duplicate FAT8 filename")
            names.add(entry.name)
            clusters, _ = self.cluster_chain(entry.raw[10], fat)
            if occupied.intersection(clusters):
                raise OSError(errno.EIO, "Cross-linked FAT8 files")
            occupied.update(clusters)
        return fat, entries

    @property
    def entries_list(self) -> t.Iterator[FAT8Path]:
        data = self.read_block(self.directory_sector, self.directory_sectors)
        if len(data) != self.directory_sectors * self.geometry.sector_size:
            raise OSError(errno.EIO, "Truncated FAT8 directory")
        for position in range(0, len(data), DIRECTORY_ENTRY_SIZE):
            raw = data[position : position + DIRECTORY_ENTRY_SIZE]
            if raw[0] == 0xFF:
                break
            if raw[0] == 0:
                continue
            if any(c < 32 or c > 126 for c in raw[:9]) or not raw[:9].strip() or raw[9] & 0x0F:
                raise OSError(errno.EIO, "Invalid FAT8 directory entry")
            yield FAT8Path(self, position // DIRECTORY_ENTRY_SIZE, raw)

    def filter_entries_list(
        self, pattern: t.Optional[str], include_all: bool = False, expand: bool = True
    ) -> t.Iterator[FAT8Path]:
        pattern = pattern.upper() if pattern else None
        for entry in self.entries_list:
            if filename_match(entry.name, pattern, wildcard=True):
                yield entry

    def get_root(self) -> FAT8Path:
        """
        Get the root directory
        """
        return FAT8Path(self)

    def get_file_entry(self, fullname: str) -> FAT8Path:
        """
        Get a file entry by fullname
        """
        if fullname in ("", "/"):
            return self.get_root()
        for entry in self.entries_list:
            if entry.name == fullname.upper():
                return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)

    def check_writable(self, entry: t.Optional[FAT8Path] = None) -> None:
        info = self.read_block(self.directory_sector + self.directory_sectors)
        if len(info) != self.geometry.sector_size:
            raise OSError(errno.EIO, "Truncated FAT8 information sector")
        if entry is not None and entry.slot >= 0:
            sector, offset = divmod(entry.slot * DIRECTORY_ENTRY_SIZE, self.geometry.sector_size)
            data = self.read_block(self.directory_sector + sector)
            if data[offset : offset + DIRECTORY_ENTRY_SIZE] != entry.raw:
                raise FileNotFoundError(errno.ENOENT, "FAT8 directory entry has changed", entry.name)
        if info[0] & ATTR_PROTECTED or (entry is not None and entry.attributes & ATTR_PROTECTED):
            raise PermissionError(errno.EACCES, "FAT8 disk or file is write protected")

    def write_fat(self, fat: bytearray) -> None:
        for copy in range(3):
            sector = self.fat_sector + copy * self.fat_sectors
            data = bytearray(self.read_block(sector, self.fat_sectors))
            data[: len(fat)] = fat
            self.write_block(data, sector, self.fat_sectors)

    def write_entry(self, slot: int, raw: bytes) -> None:
        sector, offset = divmod(slot * DIRECTORY_ENTRY_SIZE, self.geometry.sector_size)
        data = bytearray(self.read_block(self.directory_sector + sector))
        data[offset : offset + DIRECTORY_ENTRY_SIZE] = raw
        self.write_block(data, self.directory_sector + sector)

    def create_file(self, fullname: str, size: int, metadata: t.Optional[t.Dict[str, t.Any]] = None) -> FAT8Path:
        fullname = fullname.upper()
        if (
            not 1 <= len(fullname) <= 9
            or fullname != fullname.strip()
            or any(ord(c) < 33 or ord(c) > 126 or c in "/\\:*?" for c in fullname)
            or size < 0
        ):
            raise OSError(errno.EINVAL, "FAT8 requires a filename of 1-9 characters and a nonnegative size")
        fat, entries = self.read_state()
        old = next((entry for entry in entries if entry.name == fullname), None)
        self.check_writable(old)
        metadata = metadata or {}
        attributes = metadata.get("attributes", old.attributes if old else ATTR_BINARY)
        if not isinstance(attributes, int) or attributes < 0 or attributes > 0xF0 or attributes & 0x0F:
            raise ValueError("Invalid FAT8 file attributes")
        file_type = metadata.get("file_type")
        if file_type is not None:
            if not isinstance(file_type, str) or file_type.upper() not in self.get_types():
                raise ValueError("FAT8 file type must be ASCII or BINARY")
            attributes = (attributes & ~ATTR_BINARY) | (ATTR_BINARY if file_type.upper() == "BINARY" else 0)
        old_clusters = self.cluster_chain(old.raw[10], fat)[0] if old else []
        available = [c for c, value in enumerate(fat) if value == FAT_FREE or c in old_clusters]
        sectors = (size + self.geometry.sector_size - 1) // self.geometry.sector_size
        needed = max(1, (sectors + self.geometry.sectors_per_cluster - 1) // self.geometry.sectors_per_cluster)
        directory = self.read_block(self.directory_sector, self.directory_sectors)
        slot = (
            old.slot
            if old
            else next(
                (
                    i // DIRECTORY_ENTRY_SIZE
                    for i in range(0, len(directory), DIRECTORY_ENTRY_SIZE)
                    if directory[i] in (0, 255)
                ),
                -1,
            )
        )
        if len(available) < needed or slot < 0:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        allocated = available[:needed]
        for cluster in old_clusters:
            fat[cluster] = FAT_FREE
        for i, cluster in enumerate(allocated):
            fat[cluster] = (
                allocated[i + 1]
                if i + 1 < needed
                else FAT_LAST + sectors - (needed - 1) * self.geometry.sectors_per_cluster
            )
        raw = fullname.encode("ascii").ljust(9, b" ") + bytes([attributes, allocated[0]])
        raw += old.raw[11:] if old else b"\xff" * 5
        # Clear newly allocated storage before publishing the directory entry.
        for sector in self.chain_sectors(allocated[0], fat):
            self.write_block(bytes(self.geometry.sector_size), sector)
        self.write_fat(fat)
        self.write_entry(slot, raw)
        if directory[slot * DIRECTORY_ENTRY_SIZE] == 255 and (slot + 1) * DIRECTORY_ENTRY_SIZE < len(directory):
            self.write_entry(slot + 1, b"\xff" * DIRECTORY_ENTRY_SIZE)
        return FAT8Path(self, slot, raw)

    def get_types(self) -> t.List[str]:
        return ["ASCII", "BINARY"]

    def write_bytes(
        self,
        fullname: str,
        content: t.Union[bytes, bytearray],
        fork: t.Optional[str] = None,
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        if fork:
            raise ValueError("FAT8 files do not have forks")
        metadata = dict(metadata or {})
        attributes = metadata.get("attributes")
        protect = isinstance(attributes, int) and bool(attributes & ATTR_PROTECTED)
        if isinstance(attributes, int) and protect:
            metadata["attributes"] = attributes & ~ATTR_PROTECTED
        entry = self.create_file(fullname, len(content), metadata)
        with entry.open(file_mode) as file:
            file.write(content)
        if protect:
            self.write_entry(entry.slot, entry.raw[:9] + bytes([entry.attributes | ATTR_PROTECTED]) + entry.raw[10:])

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        if entry is not None and not entry.is_mount():
            raise NotADirectoryError(entry.fullname)

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        entries = list(self.filter_entries_list(pattern))
        for entry in entries:
            sys.stdout.write(f"{str(entry)}\n")
        free = self.read_fat().count(FAT_FREE)
        sys.stdout.write(f"{len(entries)} Files, {free} Free clusters\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if arg:
            sys.stdout.write(f"{self.get_file_entry(arg)}\n")
        else:
            sys.stdout.write(
                f"Sector size:     {self.geometry.sector_size}\n"
                f"Sectors/track:   {self.geometry.sectors_per_track}\n"
                f"Clusters:        {self.geometry.cluster_count}\n"
                f"Directory track: {self.directory_track}\n\n"
            )
            for entry in self.entries_list:
                sys.stdout.write(f"{entry.slot:3} {entry}\n")
