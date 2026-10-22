import errno
import math
import os
import sys
import struct
import typing as t
from datetime import datetime

from ..abstract import AbstractBlockFilesystem, AbstractFile, XFERXPath
from ..commons import READ_FILE_FULL, filename_match
from ..device.abstract import AbstractDevice
from ..device.imd import IMDBlockDevice

DIR_ENTRY_FORMAT = "<8s3sBBBHHHHHHHI"
DIR_ENTRY_LEN = struct.calcsize(DIR_ENTRY_FORMAT)
assert DIR_ENTRY_LEN == 32, "FAT12 directory entry size mismatch"

BLOCK_SIZE = 512
SECTOR_SIZE_OFFSET = 11

ATTR_READ_ONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_VOLUME_LABEL = 0x08
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_DEVICE = 0x40

FAT_FREE = 0x000  # 0x000 - Free cluster
FAT_BAD = 0xFF7  # 0xFF7 - Bad cluster
FAT_EOC = 0xFF8  # 0xFF8-0xFFF - Last cluster in a file
FAT_RESERVED_START = 0xFF0  # 0xFF0-0xFF6 - Reserved values
FAT_RESERVED_END = 0xFF6

DEFAULT_VOLUME_NAME = "XFERX"


def format_attr(attr: int) -> str:
    """
    Format the file attributes as a string
    """
    return "".join(
        [
            (attr & ATTR_ARCHIVE) and "A" or "-",  # archive
            (attr & ATTR_DIRECTORY) and "D" or "-",  # directory
            (attr & ATTR_VOLUME_LABEL) and "V" or "-",  # volume label
            (attr & ATTR_SYSTEM) and "S" or "-",  # system
            (attr & ATTR_HIDDEN) and "H" or "-",  # hidden
            (attr & ATTR_READ_ONLY) and "R" or "-",  # read only
        ]
    )


def fat_canonical_filename(fullname: t.Optional[str], wildcard: bool) -> str:
    """
    Generate the canonical Files-11 name
    """
    if fullname == "" or fullname == "." or fullname == "..":
        return fullname
    fullname = (fullname or "").upper()
    try:
        stem, extension = fullname.split(".", 1)
    except Exception:
        stem = fullname
        extension = ""
    if wildcard and not extension:
        extension = "*"
    stem = stem[:8].rstrip()
    extension = extension[:3].rstrip()
    return f"{stem}.{extension}"


def fat_split_fullname(
    fullname: t.Optional[str],
    default_path: str,
    wildcard: bool,
) -> t.Tuple[str, t.Optional[str]]:  # path, filename
    """
    Split a FAT12 fullname into path and filename
    """
    fullname = (fullname or "").upper()
    if "/" in fullname:
        path, filename = fullname.rsplit("/", 1)
    else:
        path, filename = default_path, fullname
    filename = fat_canonical_filename(filename, wildcard)
    return path, filename


def fat_to_date(d: int, t: int) -> t.Optional[datetime]:
    """Convert FAT date and time to datetime."""
    if d == 0 and t == 0:
        return None
    year = (d >> 9) + 1980
    month = (d >> 5) & 0x0F
    day = d & 0x1F
    hour = t >> 11
    minute = (t >> 5) & 0x3F
    second = (t & 0x1F) * 2
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def date_to_fat(dt: datetime) -> t.Tuple[int, int]:
    """Convert datetime to FAT date and time."""
    d = (dt.year - 1980) << 9 | dt.month << 5 | dt.day
    t = dt.hour << 11 | dt.minute << 5 | (dt.second // 2)
    return d, t


def format_time(dt: t.Optional[datetime]) -> str:
    """
    Format date and time
    """
    if not dt:
        return " " * 14
    tmp = dt.strftime("%m-%d-%y %H:%M")
    return f"{tmp:>14}"


class FAT12File(AbstractFile):
    def __init__(self, entry: "FAT12Path"):
        self.entry = entry
        self.current_position = 0
        self.closed = False

    @property
    def name(self) -> str:
        """Final path component"""
        return self.entry.name

    def read_block(self, block_number: int, number_of_blocks: int = 1) -> bytes:
        """Read block(s) of data from the file"""
        if self.closed or block_number < 0 or (number_of_blocks < 0 and number_of_blocks != READ_FILE_FULL):
            raise OSError(errno.EIO, "Invalid FAT12 file read")
        sectors = self.entry.sectors()
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = len(sectors) - block_number
        return b"".join(
            self.entry.fs.read_block(sector) for sector in sectors[block_number : block_number + number_of_blocks]
        )

    def write_block(self, buffer: t.Union[bytes, bytearray], block_number: int, number_of_blocks: int = 1) -> None:
        """Write block(s) of data to the file"""
        sectors = self.entry.sectors()
        if (
            self.closed
            or self.entry.is_dir()
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > len(sectors)
            or len(buffer) < number_of_blocks * self.get_block_size()
        ):
            raise OSError(errno.EIO, "Invalid FAT12 file write")
        self.entry.fs.check_writable(self.entry)
        for index, sector in enumerate(sectors[block_number : block_number + number_of_blocks]):
            start = index * self.get_block_size()
            self.entry.fs.write_block(buffer[start : start + self.get_block_size()], sector)

    def get_size(self) -> int:
        """Get file size in bytes."""
        return self.entry.get_size()

    def get_block_size(self) -> int:
        """Get file block size in bytes"""
        return self.entry.get_block_size()


class FAT12Path(XFERXPath):
    fs: "FAT12Filesystem"
    stem: str  # File Name
    extension: str  # File Extension
    attributes: int  # File Attributes
    file_size: int  # File Size in bytes
    first_cluster: int  # First Cluster Number
    raw_creation_time: int  # Creation time
    raw_creation_date: int  # Creation date
    raw_last_mod_time: int  # Last modified time
    raw_last_mod_date: int  # Last modified date
    slot: int
    raw: bytes
    _parent: "FAT12Path"

    @classmethod
    def read(cls, parent: "FAT12Path", buffer: bytes, position: int) -> t.Optional["FAT12Path"]:
        self = cls(parent.fs)
        self._parent = parent
        self.slot = position // DIR_ENTRY_LEN
        self.raw = buffer[position : position + DIR_ENTRY_LEN]

        (
            stem,  # File Name
            ext,  # File Extension
            self.attributes,  # File Attributes
            nt_reserved,
            creation_tenths,
            self.raw_creation_time,
            self.raw_creation_date,
            access_date,
            cluster_high,  # FAT32
            self.raw_last_mod_time,
            self.raw_last_mod_date,
            self.first_cluster,
            self.file_size,
        ) = struct.unpack_from(DIR_ENTRY_FORMAT, buffer, position)

        # 0x00 means no more entries
        if buffer[position] == 0x00:
            return None
        # Deleted entries begin with 0xE5
        deleted = buffer[position] == 0xE5
        self.stem = stem.decode("ascii", errors="replace").rstrip()
        if deleted:
            self.stem = f"\xe5{self.stem[1:]}"
        self.extension = ext.decode("ascii", errors="replace").rstrip()
        return self

    @property
    def fullname(self) -> str:
        """
        Name with path
        """
        if self.is_mount():
            return ""
        else:
            return f"{self.parent.fullname}/{self.name}"

    @property
    def name(self) -> str:
        """
        Final path component
        """
        if self.extension:
            return f"{self.stem}.{self.extension}"
        else:
            return self.stem

    @property
    def match_name(self) -> str:
        """
        Name used for matching with patterns
        """
        if self.stem == "." or self.stem == "..":
            return self.stem
        else:
            return f"{self.stem}.{self.extension}"

    @property
    def parent(self) -> "FAT12Path":
        return self._parent

    def is_deleted(self) -> bool:
        """Is a deleted file"""
        return self.stem.startswith("\xe5")

    def is_readonly(self) -> bool:
        """Is a read-only file"""
        return bool(self.attributes & ATTR_READ_ONLY)

    def is_hidden(self) -> bool:
        """Is a hidden file"""
        return bool(self.attributes & ATTR_HIDDEN)

    def is_system(self) -> bool:
        """Is a system file"""
        return bool(self.attributes & ATTR_SYSTEM)

    def is_volume_label(self) -> bool:
        """Is a volume label"""
        return bool(self.attributes & ATTR_VOLUME_LABEL)

    def is_archive(self) -> bool:
        """Is an archive file"""
        return bool(self.attributes & ATTR_ARCHIVE)

    def is_file(self) -> bool:
        """Is a file"""
        return not self.is_dir() and not self.is_volume_label()

    def is_dir(self) -> bool:
        """Is a directory"""
        return bool(self.attributes & ATTR_DIRECTORY)

    def is_mount(self) -> bool:
        """Is root directory"""
        return self.is_dir() and self.parent is self

    @property
    def creation_date(self) -> t.Optional[datetime]:
        """
        Creation date
        """
        return fat_to_date(self.raw_creation_date, self.raw_creation_time)

    @property
    def last_mod_date(self) -> t.Optional[datetime]:
        """
        Last modification date
        """
        return fat_to_date(self.raw_last_mod_date, self.raw_last_mod_date)

    def iterdir(
        self,
        pattern: t.Optional[str] = None,
        include_all: bool = False,
        wildcard: bool = True,
    ) -> t.Iterator["FAT12Path"]:
        """
        Iterate over directory entries
        """
        if not self.is_dir():
            raise NotADirectoryError(self.name)
        pattern = pattern.upper() if pattern else None
        data = self.read_bytes()  # Read the directory entries
        for position in range(0, len(data), DIR_ENTRY_LEN):
            raw = data[position : position + DIR_ENTRY_LEN]
            if len(raw) < DIR_ENTRY_LEN or raw[0] == 0:
                break
            entry = FAT12Path.read(self, data, position)
            if entry is None:
                break
            if include_all or not (entry.is_volume_label() or entry.is_deleted()):
                if pattern is None or filename_match(entry.match_name, pattern, wildcard):
                    yield entry

    def get_child(self, name: str) -> "FAT12Path":
        """
        Get a child entry by name
        """
        if not self.is_dir():
            raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), self.fullname)
        name = fat_canonical_filename(name, wildcard=False)
        for entry in self.iterdir():
            if (
                filename_match(entry.match_name, name, wildcard=False)
                and not entry.is_volume_label()
                and not entry.is_deleted()
            ):
                return entry
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), name)

    def sectors(self) -> t.List[int]:
        """
        Get the list of sectors occupied by the file
        """
        if self.is_dir():
            if self.is_mount():  # Root directory
                return list(
                    range(self.fs.root_directory_sector, self.fs.root_directory_sector + self.fs.root_directory_sectors)
                )
            else:  # Subdirectory
                clusters = self.fs.cluster_chain(self.first_cluster)
                sectors = [
                    self.fs.cluster_sector(cluster) + offset
                    for cluster in clusters
                    for offset in range(self.fs.sectors_per_cluster)
                ]
                return sectors
        else:  # Regular file
            size = self.get_size()
            if size == 0:
                return []
            clusters = self.fs.cluster_chain(self.first_cluster)
            sectors = [
                self.fs.cluster_sector(cluster) + offset
                for cluster in clusters
                for offset in range(self.fs.sectors_per_cluster)
            ]
            required = math.ceil(size / self.fs.sector_size)
            if len(sectors) < required:
                raise OSError(errno.EIO, "Truncated FAT12 file cluster chain")
            return sectors[:required]

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return len(self.sectors())

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get file size in bytes
        """
        if self.is_dir():
            return self.get_length() * self.fs.sector_size
        else:
            return self.file_size

    def get_block_size(self) -> int:
        """
        Get file block size in bytes
        """
        return self.fs.sector_size

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> FAT12File:
        """
        Open a file
        """
        return FAT12File(self)

    def delete(self) -> bool:
        """
        Delete a file or an empty directory
        """
        self.fs.check_writable(self)
        if self.is_mount():
            raise OSError(errno.EPERM, "Cannot delete the root directory")
        if self.is_dir() and any(
            not child.is_deleted() and child.name not in (".", "..") for child in self.iterdir(include_all=True)
        ):
            raise OSError(errno.ENOTEMPTY, os.strerror(errno.ENOTEMPTY), self.fullname)
        fat = bytearray(self.fs.fat)
        if self.first_cluster:
            for cluster in self.fs.cluster_chain(self.first_cluster):
                self.fs.set_fat_entry(fat, cluster, FAT_FREE)
        self.parent.write_directory_entry(self.slot, b"\xe5" + self.raw[1:])
        self.fs.write_fat(fat)
        self.fs.fat = bytes(fat)
        return True

    def write_directory(self, data: t.Union[bytes, bytearray]) -> None:
        """
        Write directory contents
        """
        if not self.is_dir():
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), self.fullname)
        required_sectors = max(1, math.ceil(len(data) / self.fs.sector_size))
        sectors = self.sectors()
        if self.is_mount():  # Root directory
            if required_sectors > len(sectors):
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        else:  # Subdirectory
            clusters = self.fs.cluster_chain(self.first_cluster)
            required_clusters = math.ceil(required_sectors / self.fs.sectors_per_cluster)
            # Allocate additional space if needed
            if required_clusters > len(clusters):
                fat = bytearray(self.fs.fat)
                available = [
                    cluster for cluster in range(2, self.fs.cluster_count + 2) if self.fs.fat_entry(cluster) == FAT_FREE
                ]
                extra = required_clusters - len(clusters)
                if len(available) < extra:
                    raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
                for index, cluster in enumerate(available[:extra]):
                    self.fs.set_fat_entry(fat, cluster, available[index + 1] if index + 1 < extra else FAT_EOC | 0x7)
                self.fs.set_fat_entry(fat, clusters[-1], available[0])
                self.fs.write_fat(fat)
                sectors = self.sectors()
        capacity = len(sectors) * self.fs.sector_size
        output = bytes(data[:capacity]).ljust(capacity, b"\0")
        for index, sector in enumerate(sectors):
            start = index * self.fs.sector_size
            self.fs.write_block(output[start : start + self.fs.sector_size], sector)

    def read_directory_entry(self, slot: int) -> bytes:
        """
        Read a directory entry by slot number
        """
        if not self.is_dir():
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), self.fullname)
        sectors = self.sectors()
        byte_offset = slot * DIR_ENTRY_LEN
        sector_index, offset = divmod(byte_offset, self.fs.sector_size)
        if sector_index >= len(sectors):
            raise OSError(errno.EIO, "FAT12 directory entry is out of range")
        return self.fs.read_block(sectors[sector_index])[offset : offset + DIR_ENTRY_LEN]

    def write_directory_entry(self, slot: int, raw: bytes) -> None:
        """
        Write a directory entry by slot number
        """
        if not self.is_dir():
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), self.fullname)
        if len(raw) != DIR_ENTRY_LEN:
            raise ValueError("FAT12 directory entries must be 32 bytes")
        sectors = self.sectors()
        byte_offset = slot * DIR_ENTRY_LEN
        sector_index, offset = divmod(byte_offset, self.fs.sector_size)
        if sector_index >= len(sectors):
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        data = bytearray(self.fs.read_block(sectors[sector_index]))
        data[offset : offset + DIR_ENTRY_LEN] = raw
        self.fs.write_block(data, sectors[sector_index])

    def examine(self) -> str:
        """
        Examine the file entry
        """
        return (
            f"Name:                 {self.name}\n"
            f"Size:                 {self.get_size()} bytes\n"
            f"Attributes:           {format_attr(self.attributes)}\n"
            f"Creation date:        {format_time(self.creation_date)}\n"
            f"Last modified date:   {format_time(self.last_mod_date)}\n"
            f"First cluster:        {self.first_cluster}\n"
            f"Sectors:              {self.sectors()}\n"
        )

    def __str__(self) -> str:
        return f"{self.name:<16}  {self.get_size():>8}  {format_attr(self.attributes)}  {format_time(self.last_mod_date)}  {self.first_cluster:>6}"


class FAT12RootDirectory(FAT12Path):
    def __init__(self, fs: "FAT12Filesystem"):
        super().__init__(fs)
        self.stem = ""
        self.extension = ""
        self.attributes = ATTR_DIRECTORY
        self.file_size = 0
        self.first_cluster = 0
        self.raw_creation_time = 0
        self.raw_creation_date = 0
        self.raw_last_mod_time = 0
        self.raw_last_mod_date = 0
        self._parent = self


class FAT12Filesystem(AbstractBlockFilesystem):
    fs_name = "fat12"
    fs_description = "FAT12"
    fs_platforms = ["x86"]
    fs_entry_metadata = [
        "creation_date",
        "last_mod_date",
        "id_hidden",
        "is_readonly",
        "is_system",
        "is_archive",
    ]

    sector_size: int
    sectors_per_cluster: int
    reserved_sectors: int
    fat_count: int
    sectors_per_fat: int
    root_entry_count: int
    root_directory_sector: int
    root_directory_sectors: int
    data_sector: int
    cluster_count: int
    total_sectors: int
    media_descriptor: int
    fat: bytes
    volume_serial: str
    volume_label: str

    pwd: str  #  Current working directory

    @classmethod
    def mount(
        cls, file_or_dev: t.Union[AbstractFile, AbstractDevice], **kwargs: t.Union[bool, str]
    ) -> "FAT12Filesystem":
        self = cls(file_or_dev)
        if self.dev.read_block(0).startswith(b"IMD "):
            self.dev = IMDBlockDevice(self.dev.f)
        # Read the volume boot record
        self.read_volume_boot_record()
        self.pwd = "/"
        self.dev.sector_size = self.sector_size
        # Read the FAT allocation table
        self.read_fat()
        # Retrieve the volume label from the root directory
        self.volume_label = "NO NAME"
        for entry in self.get_root().iterdir(include_all=True):
            if entry.attributes == ATTR_VOLUME_LABEL:
                self.volume_label = entry.stem
                break
        return self

    def read_volume_boot_record(self) -> None:
        """
        Read the volume boot record
        """
        boot = self.read_block(0)
        if len(boot) < BLOCK_SIZE or boot[510:512] != b"\x55\xaa":
            raise OSError(errno.EIO, "Invalid FAT12 boot sector")

        self.sector_size = int.from_bytes(boot[SECTOR_SIZE_OFFSET:0x0D], "little")
        self.sectors_per_cluster = boot[0x0D]
        self.reserved_sectors = int.from_bytes(boot[0x0E:0x10], "little")
        self.fat_count = boot[0x10]
        self.root_entry_count = int.from_bytes(boot[0x11:0x13], "little")
        total16 = int.from_bytes(boot[0x13:0x15], "little")
        self.total_sectors = total16 or int.from_bytes(boot[32:36], "little")
        self.media_descriptor = boot[0x15]
        self.sectors_per_fat = int.from_bytes(boot[0x16:0x18], "little")

        root_directory_bytes = self.root_entry_count * DIR_ENTRY_LEN
        self.root_directory_sectors = math.ceil(root_directory_bytes / self.sector_size)
        self.root_directory_sector = self.reserved_sectors + self.fat_count * self.sectors_per_fat
        self.data_sector = self.root_directory_sector + self.root_directory_sectors
        data_sectors = self.total_sectors - self.data_sector
        if data_sectors <= 0:
            raise OSError(errno.EIO, "Invalid FAT12 volume size")
        self.cluster_count = data_sectors // self.sectors_per_cluster
        if self.cluster_count >= 4085:
            raise OSError(errno.EIO, "Volume is not FAT12")
        self.volume_serial = f"{int.from_bytes(boot[39:41], 'little'):04X}-{int.from_bytes(boot[41:43], 'little'):04X}"

    def read_fat(self) -> None:
        """
        Read the FAT allocation table
        """
        fat_bytes = self.sectors_per_fat * self.sector_size
        if math.ceil((self.cluster_count + 2) * 1.5) > fat_bytes:
            raise OSError(errno.EIO, "Truncated FAT12 allocation table")
        copies = [
            self.read_block(self.reserved_sectors + index * self.sectors_per_fat, self.sectors_per_fat)
            for index in range(self.fat_count)
        ]
        if any(len(copy) != fat_bytes for copy in copies):
            raise OSError(errno.EIO, "Truncated FAT12 allocation table")
        if any(copy != copies[0] for copy in copies[1:]):
            raise OSError(errno.EIO, "FAT12 allocation table copies disagree")
        self.fat = copies[0]
        if self.fat[0] != self.media_descriptor or self.fat[1] != 0xFF or self.fat[2] & 0x0F != 0x0F:
            raise OSError(errno.EIO, "Invalid FAT12 reserved entries")

    @classmethod
    def initialize(
        cls, file_or_dev: t.Union[AbstractFile, AbstractDevice], **kwargs: t.Union[bool, str]
    ) -> "FAT12Filesystem":
        """
        Format a disk image with an empty FAT12 volume
        """
        self = cls(file_or_dev)
        self.pwd = "/"
        if self.dev.read_block(0).startswith(b"IMD "):
            self.dev = IMDBlockDevice(self.dev.f)
        size = self.dev.get_size()

        try:
            volume_name = kwargs["name"].strip().upper() or DEFAULT_VOLUME_NAME  # type: ignore
        except Exception:
            volume_name = DEFAULT_VOLUME_NAME

        if size % BLOCK_SIZE:
            raise OSError(errno.EINVAL, f"FAT12 disk size must be a multiple of {BLOCK_SIZE} bytes")
        total_sectors = size // BLOCK_SIZE
        if total_sectors <= 0 or total_sectors > 0xFFFFFFFF:
            raise OSError(errno.EINVAL, "Unsupported FAT12 disk size")

        sector_size = BLOCK_SIZE
        sectors_per_cluster = 2 if total_sectors <= 1440 else 1
        root_entry_count = 112 if total_sectors <= 1440 else 224
        reserved_sectors = 1
        fat_count = 2
        root_directory_sectors = math.ceil(root_entry_count * DIR_ENTRY_LEN / sector_size)
        sectors_per_fat = 1
        while True:
            data_sector = reserved_sectors + fat_count * sectors_per_fat + root_directory_sectors
            cluster_count = (total_sectors - data_sector) // sectors_per_cluster
            required_fat_sectors = math.ceil(math.ceil((cluster_count + 2) * 3 / 2) / sector_size)
            if required_fat_sectors == sectors_per_fat:
                break
            sectors_per_fat = required_fat_sectors
            if sectors_per_fat <= 0 or data_sector >= total_sectors:
                raise OSError(errno.EINVAL, "Disk is too small for a FAT12 volume")

        while cluster_count >= 4085:
            sectors_per_cluster *= 2
            if sectors_per_cluster > 128:
                raise OSError(errno.EINVAL, "Disk is too large for FAT12")
            data_sector = reserved_sectors + fat_count * sectors_per_fat + root_directory_sectors
            cluster_count = (total_sectors - data_sector) // sectors_per_cluster
            sectors_per_fat = math.ceil(math.ceil((cluster_count + 2) * 3 / 2) / sector_size)

        if cluster_count <= 0:
            raise OSError(errno.EINVAL, "Disk is too small for a FAT12 volume")
        data_sector = reserved_sectors + fat_count * sectors_per_fat + root_directory_sectors

        if total_sectors == 640:
            media_descriptor, sectors_per_track = 0xFF, 8
        elif total_sectors == 720:
            media_descriptor, sectors_per_track = 0xFD, 9
        elif total_sectors == 1440:
            media_descriptor, sectors_per_track = 0xF9, 9
        elif total_sectors == 2400:
            media_descriptor, sectors_per_track = 0xF9, 15
        else:
            media_descriptor, sectors_per_track = 0xF0, 18

        boot = bytearray(sector_size)
        boot[:0x03] = b"\xeb\x3c\x90"
        boot[0x03:0x0B] = volume_name.encode("ascii", "replace").ljust(8, b" ")[:8]
        boot[0x0B:0x0D] = sector_size.to_bytes(2, "little")
        boot[0x0D] = sectors_per_cluster
        boot[0x0E:0x10] = reserved_sectors.to_bytes(2, "little")
        boot[0x10] = fat_count
        boot[0x11:0x13] = root_entry_count.to_bytes(2, "little")
        if total_sectors <= 0xFFFF:
            boot[0x13:0x15] = total_sectors.to_bytes(2, "little")
        boot[0x15] = media_descriptor
        boot[0x16:0x18] = sectors_per_fat.to_bytes(2, "little")
        boot[0x18:0x1A] = sectors_per_track.to_bytes(2, "little")
        boot[0x1A:0x1C] = (2).to_bytes(2, "little")
        boot[0x20:0x24] = total_sectors.to_bytes(4, "little")
        boot[0x24] = 0
        boot[0x26] = 0x29
        boot[0x26:0x2B] = b"XF12"
        boot[0x2B:0x36] = b"NO NAME    "
        boot[0x36:0x3E] = b"FAT12   "
        boot[0x1FE:0x200] = b"\x55\xaa"

        self.dev.sector_size = sector_size
        for block in range(0, total_sectors, 64):
            count = min(64, total_sectors - block)
            self.write_block(bytes(count * sector_size), block, count)
        self.write_block(boot, 0)
        fat = bytearray(sectors_per_fat * sector_size)
        fat[:3] = bytes((media_descriptor, 0xFF, 0xFF))
        self.write_block(fat, reserved_sectors, sectors_per_fat)
        self.write_block(fat, reserved_sectors + sectors_per_fat, sectors_per_fat)
        return cls.mount(self.dev)

    def cluster_sector(self, cluster: int) -> int:
        return self.data_sector + (cluster - 2) * self.sectors_per_cluster

    def fat_entry(self, cluster: int) -> int:
        """
        Get the FAT entry for a given cluster number
        """
        offset = cluster + cluster // 2
        if offset + 1 >= len(self.fat):
            raise OSError(errno.EIO, "FAT12 cluster number is out of range")
        value = self.fat[offset] | self.fat[offset + 1] << 8
        return value >> 4 if cluster & 1 else value & 0x0FFF

    @staticmethod
    def set_fat_entry(fat: bytearray, cluster: int, value: int) -> None:
        offset = cluster + cluster // 2
        if cluster & 1:
            fat[offset] = (fat[offset] & 0x0F) | ((value << 4) & 0xF0)
            fat[offset + 1] = (value >> 4) & 0xFF
        else:
            fat[offset] = value & 0xFF
            fat[offset + 1] = (fat[offset + 1] & 0xF0) | ((value >> 8) & 0x0F)

    def write_fat(self, fat: bytearray) -> None:
        """Write all FAT copies while preserving their padding bytes."""
        for index in range(self.fat_count):
            sector = self.reserved_sectors + index * self.sectors_per_fat
            self.write_block(fat, sector, self.sectors_per_fat)
        self.fat = bytes(fat)

    def check_writable(self, entry: t.Optional[FAT12Path] = None) -> None:
        if entry is not None and not entry.is_mount():
            current = entry.parent.read_directory_entry(entry.slot)
            if current != entry.raw:
                raise FileNotFoundError(errno.ENOENT, "FAT12 directory entry has changed", entry.name)

    def _allocate_clusters(self, fat: bytearray, count: int, reusable: t.Set[int]) -> t.List[int]:
        available = [
            cluster
            for cluster in range(2, self.cluster_count + 2)
            if self.fat_entry(cluster) == FAT_FREE or cluster in reusable
        ]
        if len(available) < count:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        allocated = available[:count]
        for index, cluster in enumerate(allocated):
            self.set_fat_entry(fat, cluster, allocated[index + 1] if index + 1 < count else FAT_EOC | 0x7)
        return allocated

    @staticmethod
    def _encode_name(name: str) -> t.Tuple[bytes, bytes]:
        name = name.upper()
        if name in (".", ".."):
            return name.encode("ascii").ljust(8, b" "), b" " * 3
        if name.count(".") > 1:
            raise OSError(errno.EINVAL, "FAT12 requires a valid 8.3 filename")
        stem, separator, extension = name.partition(".")
        invalid = '<>"/\\[]:;|=,+*?'
        if (
            not stem
            or len(stem) > 8
            or len(extension) > 3
            or any(ord(char) < 33 or ord(char) > 126 or char in invalid for char in stem + extension)
        ):
            raise OSError(errno.EINVAL, "FAT12 requires a valid 8.3 filename")
        return stem.encode("ascii").ljust(8, b" "), extension.encode("ascii").ljust(3, b" ")

    def _new_directory_entry(
        self, stem: bytes, extension: bytes, attributes: int, first_cluster: int, file_size: int
    ) -> bytes:
        now = datetime.now()
        fat_date, fat_time = date_to_fat(now)
        return struct.pack(
            DIR_ENTRY_FORMAT,
            stem,
            extension,
            attributes,
            0,
            0,
            fat_time,
            fat_date,
            fat_date,
            0,
            fat_time,
            fat_date,
            first_cluster,
            file_size,
        )

    def create_file(self, fullname: str, size: int, metadata: t.Optional[t.Dict[str, t.Any]] = None) -> FAT12Path:
        if size < 0:
            raise OSError(errno.EINVAL, "FAT12 file size must be nonnegative")
        parent, _ = self.get_parent_and_name(fullname, wildcard=False)
        self.check_writable(parent)
        name = (fullname or "").rsplit("/", 1)[-1].upper()
        if not name or name in (".", ".."):
            raise OSError(errno.EINVAL, "Invalid FAT12 filename")
        stem, extension = self._encode_name(name)
        canonical_name = f"{stem.decode('ascii').rstrip()}.{extension.decode('ascii').rstrip()}".rstrip(".")
        old = None
        try:
            old = parent.get_child(canonical_name)
        except FileNotFoundError:
            pass
        if old is not None and old.is_dir():
            raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), old.fullname)
        self.check_writable(old)
        metadata = metadata or {}
        attributes = metadata.get("attributes", old.attributes if old else ATTR_ARCHIVE)
        if (
            not isinstance(attributes, int)
            or not 0 <= attributes <= 0xFF
            or attributes & (ATTR_DIRECTORY | ATTR_VOLUME_LABEL)
        ):
            raise ValueError("Invalid FAT12 file attributes")
        old_clusters = self.cluster_chain(old.first_cluster) if old is not None and old.first_cluster else []
        fat = bytearray(self.fat)
        for cluster in old_clusters:
            self.set_fat_entry(fat, cluster, FAT_FREE)
        clusters_needed = math.ceil(size / (self.sector_size * self.sectors_per_cluster))
        allocated = self._allocate_clusters(fat, clusters_needed, set(old_clusters)) if clusters_needed else []
        directory_data = bytearray(b"".join(self.read_block(sector) for sector in parent.sectors()))
        if old is None:
            slot = next(
                (
                    offset // DIR_ENTRY_LEN
                    for offset in range(0, len(directory_data), DIR_ENTRY_LEN)
                    if directory_data[offset] in (0x00, 0xE5)
                ),
                -1,
            )
        else:
            slot = old.slot
        if slot < 0:
            if parent.is_mount():
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            slot = len(directory_data) // DIR_ENTRY_LEN
            directory_data.extend(bytes(DIR_ENTRY_LEN))
        entry = self._new_directory_entry(stem, extension, attributes, allocated[0] if allocated else 0, size)
        offset = slot * DIR_ENTRY_LEN
        directory_data[offset : offset + DIR_ENTRY_LEN] = entry
        for cluster in allocated:
            sector = self.cluster_sector(cluster)
            for index in range(self.sectors_per_cluster):
                self.write_block(bytes(self.sector_size), sector + index)
        if allocated or old_clusters:
            self.write_fat(fat)
        parent.write_directory(directory_data)
        if offset + 2 * DIR_ENTRY_LEN <= len(directory_data) and directory_data[offset + DIR_ENTRY_LEN] == 0:
            parent.write_directory_entry(slot + 1, bytes(DIR_ENTRY_LEN))
        return parent.get_child(canonical_name)

    def create_directory(self, fullname: str, options: t.Dict[str, t.Union[str, bool]]) -> FAT12Path:
        parent, _ = self.get_parent_and_name(fullname, wildcard=False)
        name = (fullname or "").rsplit("/", 1)[-1].upper()
        if not name or name in (".", ".."):
            raise OSError(errno.EINVAL, "Invalid FAT12 directory name")
        stem, extension = self._encode_name(name)
        canonical_name = f"{stem.decode('ascii').rstrip()}.{extension.decode('ascii').rstrip()}".rstrip(".")
        try:
            parent.get_child(canonical_name)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), canonical_name)
        self.check_writable(parent)
        directory_data = bytearray(b"".join(self.read_block(sector) for sector in parent.sectors()))
        slot = next(
            (
                offset // DIR_ENTRY_LEN
                for offset in range(0, len(directory_data), DIR_ENTRY_LEN)
                if directory_data[offset] in (0x00, 0xE5)
            ),
            -1,
        )
        if slot < 0:
            if parent.is_mount():
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            slot = len(directory_data) // DIR_ENTRY_LEN
            directory_data.extend(bytes(DIR_ENTRY_LEN))
        fat = bytearray(self.fat)
        allocated = self._allocate_clusters(fat, 1, set())
        first_cluster = allocated[0]
        self.write_fat(fat)
        dot = self._new_directory_entry(b".       ", b"   ", ATTR_DIRECTORY, first_cluster, 0)
        parent_cluster = 0 if parent.is_mount() else parent.first_cluster
        dotdot = self._new_directory_entry(b"..      ", b"   ", ATTR_DIRECTORY, parent_cluster, 0)
        new_directory = FAT12Path(self)
        new_directory._parent = parent
        new_directory.stem = stem.decode("ascii").rstrip()
        new_directory.extension = extension.decode("ascii").rstrip()
        new_directory.attributes = ATTR_DIRECTORY
        new_directory.file_size = 0
        new_directory.first_cluster = first_cluster
        new_directory.raw_creation_time = 0
        new_directory.raw_creation_date = 0
        new_directory.raw_last_mod_time = 0
        new_directory.raw_last_mod_date = 0
        new_directory.slot = slot
        new_directory.raw = self._new_directory_entry(stem, extension, ATTR_DIRECTORY, first_cluster, 0)
        new_directory.write_directory(dot + dotdot)
        offset = slot * DIR_ENTRY_LEN
        directory_data[offset : offset + DIR_ENTRY_LEN] = new_directory.raw
        parent.write_directory(directory_data)
        return parent.get_child(canonical_name)

    def cluster_chain(self, first_cluster: int) -> t.List[int]:
        """
        Get the list of clusters in a file's cluster chain
        """
        if first_cluster < 2 or first_cluster >= self.cluster_count + 2:
            raise OSError(errno.EIO, "Invalid FAT12 starting cluster")
        result: t.List[int] = []
        visited: t.Set[int] = set()
        cluster = first_cluster
        while True:
            if cluster in visited or cluster >= self.cluster_count + 2:
                raise OSError(errno.EIO, "Invalid or cyclic FAT12 cluster chain")
            visited.add(cluster)
            result.append(cluster)
            next_cluster = self.fat_entry(cluster)
            if next_cluster >= FAT_EOC:
                return result
            if next_cluster == 0 or next_cluster == 1 or FAT_RESERVED_START <= next_cluster <= FAT_RESERVED_END:
                raise OSError(errno.EIO, "Invalid FAT12 cluster chain")
            cluster = next_cluster

    @property
    def entries_list(self) -> t.Iterator[FAT12Path]:
        """
        Property to get an iterator of directory entries
        """
        try:
            parent = self.get_file_entry(self.pwd)
            yield from parent.iterdir()
        except FileNotFoundError:
            pass

    def filter_entries_list(
        self, pattern: t.Optional[str], include_all: bool = False, expand: bool = True
    ) -> t.Iterator[FAT12Path]:
        """
        Filter directory entries based on a pattern
        """
        parent, pattern = self.get_parent_and_name(fullname=pattern, wildcard=False)
        if expand and pattern:
            # If the name is a directory, expand it to list its contents
            try:
                entry = parent.get_child(pattern)
                if entry.is_dir():
                    parent = entry
                    pattern = "*.*"
            except FileNotFoundError:
                pass
        if not pattern:
            pattern = "*.*"
        pattern = fat_canonical_filename(pattern, wildcard=True)
        yield from parent.iterdir(pattern=pattern, include_all=include_all, wildcard=True)

    def get_root(self) -> FAT12Path:
        """
        Get the root directory
        """
        return FAT12RootDirectory(self)

    def get_parent_and_name(self, fullname: t.Optional[str], wildcard: bool) -> t.Tuple[FAT12Path, t.Optional[str]]:
        """
        Get the parent directory and filename from fullname
        """
        path, name = fat_split_fullname(
            fullname=fullname,
            default_path=self.pwd,
            wildcard=wildcard,
        )
        if not path:
            raise FileNotFoundError(errno.ENOENT, "No directory specified", fullname)
        # Traverse the path to find the directory entry
        parts = path.split("/")
        parent: FAT12Path = self.get_root()
        for part in parts:
            if not part:
                continue
            parent = parent.get_child(part)
            if not parent.is_dir():
                raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), part)
        return parent, name

    def get_file_entry(self, fullname: str) -> FAT12Path:
        """
        Get a file entry by fullname
        """
        if not fullname or fullname == "/":
            return self.get_root()
        parent, name = self.get_parent_and_name(fullname=fullname, wildcard=False)
        if not name:
            return parent  # Return the directory itself
        else:
            return parent.get_child(name)  # Return the child entry by name

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        """
        List directory contents
        """
        entries = list(self.filter_entries_list(pattern))
        sys.stdout.write(f"\n Volume in drive {volume_id} is {self.volume_label}\n")
        sys.stdout.write(f" Volume Serial Number is {self.volume_serial}\n")
        sys.stdout.write(f" Directory of {volume_id}:{self.pwd}\n\n")
        total_size = 0
        for entry in entries:
            last_mod_date = format_time(entry.last_mod_date)
            size_or_type = "<DIR>   " if entry.is_dir() else f"{entry.get_size():>8}"
            sys.stdout.write(f"{entry.stem:<8} {entry.extension:<3} {size_or_type} {last_mod_date}\n")
            total_size += entry.get_size() if not entry.is_dir() else 0
        free_clusters = sum(1 for cluster in range(2, self.cluster_count + 2) if self.fat_entry(cluster) == 0)
        sys.stdout.write(f"{len(entries):9} file(s) {total_size:>9} bytes\n")
        sys.stdout.write(f"{(free_clusters * self.sectors_per_cluster * self.sector_size):>27} bytes free\n\n")

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        """
        Examine the filesystem
        """
        if arg:
            entry = self.get_file_entry(arg)
            sys.stdout.write(f"{entry.examine()}\n")
            if entry.is_dir():
                sys.stdout.write(f"Directory entries:\n")
                for child in entry.iterdir(include_all=True):
                    sys.stdout.write(f"{child}\n")
        else:
            sys.stdout.write(
                f"Sector size:       {self.sector_size:>6}\n"
                f"Sectors/cluster:   {self.sectors_per_cluster:>6}\n"
                f"Clusters:          {self.cluster_count:>6}\n"
                f"Root entries:      {self.root_entry_count:>6}\n"
                f"FAT copies:        {self.fat_count:>6}\n\n"
            )
            for entry in self.get_root().iterdir(include_all=True):
                sys.stdout.write(f"{entry}\n")

    def chdir(self, entry: t.Optional[XFERXPath]) -> None:
        """
        Change the current User Identification Code
        """
        if entry is None:
            entry = self.get_root()
        assert isinstance(entry, FAT12Path)
        if not entry.is_dir():
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), entry.fullname)
        self.pwd = entry.name

    def get_pwd(self) -> str:
        """
        Get the current working directory
        """
        return self.pwd
