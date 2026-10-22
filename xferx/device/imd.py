import errno
import struct
import typing as t

from ..imd import IMDDiskImage, IMDSector, TRACK_HEADER_SIZE
from .block import BlockDevice

if t.TYPE_CHECKING:
    from ..abstract import AbstractFile


__all__ = [
    "IMDBlockDevice",
]


class IMDBlockDevice(BlockDevice):
    """Uniform ImageDisk tracks, ordered by cylinder, head, and sector ID.

    Writes replace only the selected sector records. Original track headers,
    sector maps, compression, and error records elsewhere remain byte-identical.
    """

    def __init__(self, file: "AbstractFile"):
        super().__init__(file)
        file.seek(0)
        self._original = file.read()
        file.seek(0)
        try:
            disk = IMDDiskImage(file)
        except (IndexError, ValueError, struct.error) as exc:
            raise OSError(errno.EIO, "Truncated or invalid ImageDisk image") from exc
        if not disk.tracks:
            raise OSError(errno.EIO, "ImageDisk image has no tracks")
        self.sector_size = disk.tracks[0].sector_size
        self.sectors_per_track = disk.tracks[0].sectors_per_track
        self.track_count = len(disk.tracks)
        self._records: t.List[t.Tuple[int, int]] = []
        self._replacements: t.Dict[int, bytes] = {}
        positions: t.Dict[t.Tuple[int, int, int], t.Tuple[IMDSector, int]] = {}
        offset = self._original.index(b"\x1a") + 1
        for track in disk.tracks:
            if track.sector_size != self.sector_size or track.sectors_per_track != self.sectors_per_track:
                raise OSError(errno.EINVAL, "Mixed ImageDisk track geometries are not supported")
            if sorted(track.sector_numbering_map) != list(range(1, self.sectors_per_track + 1)):
                raise OSError(errno.EINVAL, "Nonstandard ImageDisk sector IDs are not supported")
            if track.sector_cylinder_map is not None and any(c != track.cylinder for c in track.sector_cylinder_map):
                raise OSError(errno.EINVAL, "Remapped ImageDisk cylinders are not supported")
            if track.sector_head_map is not None and any(h != track.head for h in track.sector_head_map):
                raise OSError(errno.EINVAL, "Remapped ImageDisk heads are not supported")
            offset += TRACK_HEADER_SIZE + track.sectors_per_track * (
                1 + (track.sector_cylinder_map is not None) + (track.sector_head_map is not None)
            )
            for sector_id, sector in zip(track.sector_numbering_map, track.sector_data_records):
                key = (track.cylinder, track.head, sector_id)
                if key in positions or sector.record_type not in range(9):
                    raise OSError(errno.EIO, "Invalid or duplicate ImageDisk sector")
                if sector.record_type and len(sector.data) != self.sector_size:
                    raise OSError(errno.EIO, "Truncated ImageDisk sector")
                positions[key] = (sector, offset)
                self._records.append((offset, sector.record_size))
                offset += sector.record_size
        heads = max(track.head for track in disk.tracks) + 1
        expected = [
            (cylinder, head, sector)
            for cylinder in range(max(track.cylinder for track in disk.tracks) + 1)
            for head in range(heads)
            for sector in range(1, self.sectors_per_track + 1)
        ]
        if set(expected) != set(positions):
            raise OSError(errno.EIO, "ImageDisk image is missing tracks")
        self._sectors = [positions[key] for key in expected]
        self.size = len(self._sectors) * self.sector_size

    def _check_range(self, block_number: int, number_of_blocks: int) -> None:
        if block_number < 0 or number_of_blocks < 0 or block_number + number_of_blocks > len(self._sectors):
            raise OSError(errno.EIO, "ImageDisk sector out of range")

    def read_block(self, block_number: int, number_of_blocks: int = 1) -> bytes:
        self._check_range(block_number, number_of_blocks)
        result = bytearray()
        for sector, _ in self._sectors[block_number : block_number + number_of_blocks]:
            if sector.record_type not in (1, 2, 3, 4):
                raise OSError(errno.EIO, "Unavailable or damaged ImageDisk sector")
            result.extend(sector.data)
        return bytes(result)

    def write_block(self, buffer: t.Union[bytes, bytearray], block_number: int, number_of_blocks: int = 1) -> None:
        self._check_range(block_number, number_of_blocks)
        if len(buffer) < number_of_blocks * self.sector_size:
            raise OSError(errno.EINVAL, "Short ImageDisk sector write")
        replacements = dict(self._replacements)
        updates = []
        for i, (sector, offset) in enumerate(self._sectors[block_number : block_number + number_of_blocks]):
            data = bytes(buffer[i * self.sector_size : (i + 1) * self.sector_size])
            replacements[offset] = b"\x01" + data
            updates.append((sector, data))
        result = bytearray()
        position = 0
        for offset, length in self._records:
            result.extend(self._original[position:offset])
            result.extend(replacements.get(offset, self._original[offset : offset + length]))
            position = offset + length
        result.extend(self._original[position:])
        self.f.seek(0)
        self.f.write(result)
        self.f.truncate(len(result))
        self._replacements = replacements
        for sector, data in updates:
            sector.data = data
            sector.record_type = 1

    def get_size(self) -> int:
        return self.size
