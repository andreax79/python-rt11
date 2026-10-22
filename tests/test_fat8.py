import errno
import hashlib
import math
import shutil
from pathlib import Path

import pytest

from xferx.commons import READ_FILE_FULL
from xferx.device.imd import IMDBlockDevice
from xferx.fat.fat8fs import FAT8Filesystem
from xferx.imd import IMDDiskImage
from xferx.native import NativeFile
from xferx.shell import Shell


def make_image(path, sector_size=256, sectors_per_track=16, clusters=160, directory_track=37, toshiba=False):
    track_count = 80 if toshiba else clusters // 2
    data = bytearray(b"\xe5" * (track_count * sectors_per_track * sector_size))
    fat_sectors = math.ceil(clusters / sector_size)
    directory_sectors = sectors_per_track - 1 - 3 * fat_sectors
    start = directory_track * sectors_per_track * sector_size
    data[start : start + directory_sectors * sector_size] = b"\xff" * (directory_sectors * sector_size)
    info = start + directory_sectors * sector_size
    data[info : info + sector_size] = bytes(sector_size)
    fat = bytearray(b"\xff" * clusters)
    fat[0:2] = b"\xfe\xfe"

    def first_sector(cluster):
        if toshiba:
            cylinder, part = divmod(cluster, 4)
            side = part & 1
            half = part >> 1
            return (cylinder * 2 + side) * sectors_per_track + half * (sectors_per_track // 2)
        return cluster * (sectors_per_track // 2)

    for cluster in range(clusters):
        if first_sector(cluster) // sectors_per_track == directory_track:
            fat[cluster] = 254
    # A fragmented, two-cluster file with three sectors in its final cluster.
    fat[2], fat[6] = 6, 0xC3
    payload_size = (sectors_per_track // 2 + 3) * sector_size
    payload = (bytes(range(256)) * math.ceil(payload_size / 256))[:payload_size]
    sectors = list(range(first_sector(2), first_sector(2) + sectors_per_track // 2))
    sectors += list(range(first_sector(6), first_sector(6) + 3))
    for i, sector in enumerate(sectors):
        data[sector * sector_size : (sector + 1) * sector_size] = payload[i * sector_size : (i + 1) * sector_size]
    data[start : start + 16] = b"HELLO.BAS" + bytes([0x80, 2]) + b"\x12\x34\x56\x78\x9a"
    # Deleted slot, then end marker; garbage after the marker must not be scanned.
    data[start + 16 : start + 32] = b"\0DELETED  " + bytes([2]) + b"\xff" * 5
    data[start + 48 : start + 64] = bytes(16)
    for copy in range(3):
        offset = info + sector_size + copy * fat_sectors * sector_size
        table = fat + bytes([0xA0 + copy]) * (fat_sectors * sector_size - clusters)
        data[offset : offset + len(table)] = table
    path.write_bytes(data)
    return payload


def as_imd(source, target, sector_size=256, sectors_per_track=16, compressed=True):
    data = source.read_bytes()
    output = bytearray(b"IMD 1.18: 01/01/2000 00:00:00\r\nFAT8 test\r\n\x1a")
    tracks = len(data) // (sector_size * sectors_per_track)
    # Deliberately reverse tracks and permute sector IDs.
    order = list(range(2, sectors_per_track + 1, 2)) + list(range(1, sectors_per_track + 1, 2))
    for track in reversed(range(tracks)):
        cylinder, head = divmod(track, 2)
        # Include optional cylinder/head maps to test preservation.
        output.extend(bytes([3, cylinder, head | 0xC0, sectors_per_track, int(math.log2(sector_size)) - 7]))
        output.extend(bytes(order))
        output.extend(bytes([cylinder]) * sectors_per_track)
        output.extend(bytes([head]) * sectors_per_track)
        for sector in order:
            offset = (track * sectors_per_track + sector - 1) * sector_size
            block = data[offset : offset + sector_size]
            if compressed and len(set(block)) == 1:
                output.extend(b"\x02" + block[:1])
            else:
                output.extend(b"\x01" + block)
    target.write_bytes(output)


def mount(path):
    return FAT8Filesystem.mount(NativeFile(path))


def test_initialize(tmp_path):
    path = tmp_path / "fat8.img"
    path.write_bytes(bytes(256 * 16 * 80))

    fs = FAT8Filesystem.initialize(NativeFile(path))

    assert fs.geometry.sector_size == 256
    assert fs.geometry.sectors_per_track == 16
    assert fs.geometry.cluster_count == 160
    assert list(fs.entries_list) == []
    fs.write_bytes("HELLO", b"initialized FAT8")
    assert fs.read_bytes("HELLO").startswith(b"initialized FAT8")
    fs.close()


@pytest.mark.parametrize(
    "sector_size,spt,clusters,track,toshiba",
    [(128, 16, 70, 18, False), (256, 16, 160, 37, False), (256, 16, 140, 36, True), (128, 26, 154, 35, False)],
)
def test_layouts(tmp_path, sector_size, spt, clusters, track, toshiba):
    path = tmp_path / "fat8.img"
    payload = make_image(path, sector_size, spt, clusters, track, toshiba)
    fs = mount(path)
    assert fs.geometry.sector_size == sector_size
    assert fs.geometry.cluster_count == clusters
    assert fs.geometry.interleaved_heads == toshiba
    assert fs.directory_track == track
    assert fs.read_bytes("hello.bas") == payload
    assert fs.get_root().is_dir() and fs.get_root().is_mount()
    assert [entry.name for entry in fs.get_root().iterdir()] == ["HELLO.BAS"]
    assert [entry.name for entry in fs.filter_entries_list("*.bas")] == ["HELLO.BAS"]
    with fs.open_file("HELLO.BAS") as file:
        assert file.read_block(0, READ_FILE_FULL) == payload
        assert file.read_block(1000) == b""
        assert file.read_block(1, 2) == payload[sector_size : sector_size * 3]
    before = path.read_bytes()
    content = b"FAT8\x00\xff" * 1000
    fs.write_bytes("NEW", content)
    assert fs.read_bytes("NEW") == content.ljust(math.ceil(len(content) / sector_size) * sector_size, b"\0")
    assert fs.read_bytes("HELLO.BAS") == payload
    fs.close()
    fs = mount(path)
    assert fs.read_bytes("NEW").startswith(content)
    assert fs.get_file_entry("NEW").delete()
    assert not fs.exists("NEW")
    # Unchanged directory entry, boot area, disk attributes and FAT padding.
    after = path.read_bytes()
    assert after[: sector_size * 8] == before[: sector_size * 8]
    start = track * spt * sector_size
    assert after[start : start + 16] == before[start : start + 16]
    info = (fs.directory_sector + fs.directory_sectors) * sector_size
    assert after[info : info + sector_size] == before[info : info + sector_size]
    for copy in range(3):
        offset = (fs.fat_sector + copy * fs.fat_sectors) * sector_size + clusters
        end = (fs.fat_sector + (copy + 1) * fs.fat_sectors) * sector_size
        assert after[offset:end] == before[offset:end]
    fs.close()


@pytest.mark.parametrize("length", [0, 1, 255, 256, 257, 2048, 2049, 4096])
def test_sizes_and_overwrite(tmp_path, length):
    path = tmp_path / "fat8.img"
    original = make_image(path)
    fs = mount(path)
    content = bytes(range(256)) * (length // 256) + b"X" * (length % 256)
    fs.write_bytes("HELLO.BAS", content)
    entry = fs.get_file_entry("HELLO.BAS")
    assert entry.raw[11:] == b"\x12\x34\x56\x78\x9a"
    assert entry.get_size() == math.ceil(length / 256) * 256
    assert entry.read_bytes() == content.ljust(entry.get_size(), b"\0")
    fs.write_bytes("HELLO.BAS", original)
    assert fs.read_bytes("HELLO.BAS") == original
    fs.close()


def test_directory_end_and_deleted_slot(tmp_path):
    path = tmp_path / "fat8.img"
    make_image(path)
    fs = mount(path)
    fs.write_bytes("SECOND", b"2")
    assert fs.get_file_entry("SECOND").slot == 1
    fs.write_bytes("THIRD", b"3")
    assert [entry.name for entry in fs.entries_list] == ["HELLO.BAS", "SECOND", "THIRD"]
    # The old garbage beyond the previous end marker must remain hidden.
    fs.close()
    fs = mount(path)
    assert len(list(fs.entries_list)) == 3
    fs.close()


def test_lowercase_directory_name(tmp_path):
    path = tmp_path / "fat8.img"
    payload = make_image(path)
    fs = mount(path)
    entry = fs.get_file_entry("HELLO.BAS")
    fs.write_entry(entry.slot, b"hello.bas" + entry.raw[9:])
    assert fs.read_bytes("hello.bas") == payload
    assert [entry.name for entry in fs.filter_entries_list("*.bas")] == ["HELLO.BAS"]
    fs.write_bytes("HELLO.BAS", b"replacement")
    assert len(list(fs.entries_list)) == 1
    fs.close()


@pytest.mark.parametrize("compressed", [False, True])
def test_imd_writes_preserve_container(tmp_path, compressed):
    raw = tmp_path / "source.img"
    image = tmp_path / "disk.imd"
    payload = make_image(raw)
    as_imd(raw, image, compressed=compressed)
    before_file = NativeFile(image)
    before = IMDDiskImage(before_file)
    before_file.close()
    fs = mount(image)
    assert isinstance(fs.dev, IMDBlockDevice)
    assert fs.read_bytes("HELLO.BAS") == payload
    fs.write_bytes("ADDED.BIN", b"\0" * 5000)
    fs.close()
    fs = mount(image)
    assert fs.read_bytes("ADDED.BIN") == bytes(5120)
    assert fs.read_bytes("HELLO.BAS") == payload
    fs.get_file_entry("ADDED.BIN").delete()
    fs.close()
    fs = mount(image)
    assert not fs.exists("ADDED.BIN")
    assert fs.read_bytes("HELLO.BAS") == payload
    fs.close()
    after_file = NativeFile(image)
    after = IMDDiskImage(after_file)
    after_file.close()
    assert before.ascii_header == after.ascii_header
    for old, new in zip(before.tracks, after.tracks):
        assert (old.cylinder, old.head, old.mode) == (new.cylinder, new.head, new.mode)
        assert old.sector_numbering_map == new.sector_numbering_map
        assert old.sector_cylinder_map == new.sector_cylinder_map
        assert old.sector_head_map == new.sector_head_map


def test_failed_replacement_is_nondestructive(tmp_path):
    path = tmp_path / "fat8.img"
    payload = make_image(path)
    fs = mount(path)
    before = path.read_bytes()
    with pytest.raises(OSError) as exc:
        fs.write_bytes("HELLO.BAS", b"X" * 400000)
    assert exc.value.errno == errno.ENOSPC
    assert path.read_bytes() == before
    assert fs.read_bytes("HELLO.BAS") == payload
    fs.close()


def test_full_disk_replacement_reuses_old_clusters(tmp_path):
    path = tmp_path / "fat8.img"
    payload = make_image(path)
    fs = mount(path)
    fat = fs.read_fat()
    for cluster, value in enumerate(fat):
        if value == 255:
            fat[cluster] = 254
    fs.write_fat(fat)
    before = path.read_bytes()
    with pytest.raises(OSError) as exc:
        fs.write_bytes("NEW", b"test")
    assert exc.value.errno == errno.ENOSPC
    assert path.read_bytes() == before
    fs.write_bytes("HELLO.BAS", b"replacement")
    assert fs.read_bytes("HELLO.BAS") == b"replacement".ljust(256, b"\0")
    fs.write_bytes("HELLO.BAS", payload)
    assert fs.read_bytes("HELLO.BAS") == payload
    fs.close()


def test_stale_entry_cannot_overwrite_reused_storage(tmp_path):
    path = tmp_path / "fat8.img"
    make_image(path)
    fs = mount(path)
    entry = fs.get_file_entry("HELLO.BAS")
    file = entry.open()
    entry.delete()
    fs.write_bytes("OTHER", b"important")
    before = path.read_bytes()
    with pytest.raises(FileNotFoundError):
        file.write_block(bytes(256), 0)
    with pytest.raises(FileNotFoundError):
        entry.delete()
    assert path.read_bytes() == before
    file.close()
    fs.close()


def test_toshiba_imd(tmp_path):
    raw = tmp_path / "source.img"
    image = tmp_path / "disk.imd"
    payload = make_image(raw, clusters=140, directory_track=36, toshiba=True)
    as_imd(raw, image)
    fs = mount(image)
    assert fs.geometry.interleaved_heads
    assert fs.read_bytes("HELLO.BAS") == payload
    fs.write_bytes("NEW", b"Toshiba IMD")
    fs.close()
    fs = mount(image)
    assert fs.read_bytes("NEW").startswith(b"Toshiba IMD")
    assert fs.read_bytes("HELLO.BAS") == payload
    fs.close()


@pytest.mark.parametrize("record_type", [0, 5, 6])
def test_imd_unreadable_sector_preserved(tmp_path, record_type):
    raw = tmp_path / "source.img"
    image = tmp_path / "disk.imd"
    make_image(raw)
    as_imd(raw, image, compressed=False)
    data = image.read_bytes()
    offset = data.index(b"\x1a") + 1 + 5 + 16 * 3
    record = bytes([record_type]) + (b"" if record_type == 0 else b"\xe5" * (1 if record_type == 6 else 256))
    image.write_bytes(data[:offset] + record + data[offset + 257 :])
    before = image.read_bytes()
    fs = mount(image)
    with pytest.raises(OSError, match="Unavailable or damaged"):
        fs.dev.read_block(79 * 16 + 1)
    fs.write_bytes("NEW", b"new content")
    fs.close()
    assert image.read_bytes()[: offset + len(record)] == before[: offset + len(record)]
    fs = mount(image)
    assert fs.read_bytes("NEW").startswith(b"new content")
    with pytest.raises(OSError, match="Unavailable or damaged"):
        fs.dev.read_block(79 * 16 + 1)
    fs.close()


@pytest.mark.parametrize("contents", [b"", b"IMD missing terminator", b"IMD bad\x1a\x03", bytes(327680)])
def test_invalid_image(tmp_path, contents):
    path = tmp_path / "bad.img"
    path.write_bytes(contents)
    file = NativeFile(path)
    with pytest.raises(OSError):
        FAT8Filesystem.mount(file)
    file.close()
    assert path.read_bytes() == contents


def test_protection_and_metadata(tmp_path):
    path = tmp_path / "fat8.img"
    make_image(path)
    fs = mount(path)
    fs.write_bytes("LOCKED", b"Protected content", metadata={"file_type": "ASCII", "attributes": 0x70})
    entry = fs.get_file_entry("LOCKED")
    assert entry.file_type == "ASCII"
    assert entry.attributes == 0x70
    assert entry.read_bytes().startswith(b"Protected content")
    before = path.read_bytes()
    for operation in [
        lambda: entry.delete(),
        lambda: fs.write_bytes("LOCKED", b"replacement"),
        lambda: entry.open().write_block(bytes(256), 0),
    ]:
        with pytest.raises(PermissionError):
            operation()
        assert path.read_bytes() == before
    info = fs.directory_sector + fs.directory_sectors
    fs.write_block(b"\x10" + bytes(255), info)
    before = path.read_bytes()
    with pytest.raises(PermissionError):
        fs.write_bytes("NEW", b"test")
    with pytest.raises(PermissionError):
        fs.get_file_entry("HELLO.BAS").delete()
    assert path.read_bytes() == before
    fs.close()


@pytest.mark.parametrize("name", ["0123456789", "A/B", "A:B", "A?B", "", " A", "A ", "\u00e9"])
def test_invalid_names(tmp_path, name):
    path = tmp_path / "fat8.img"
    make_image(path)
    fs = mount(path)
    before = path.read_bytes()
    with pytest.raises(OSError):
        fs.write_bytes(name, b"data")
    assert path.read_bytes() == before
    fs.close()


@pytest.mark.parametrize("corruption", ["cycle", "free", "reserved", "out_of_range", "crosslink", "copies", "name"])
def test_reject_corruption(tmp_path, corruption):
    path = tmp_path / "fat8.img"
    make_image(path)
    fs = mount(path)
    if corruption == "copies":
        fat = bytearray(fs.read_block(fs.fat_sector + 1))
        fat[2] = 7
        fs.write_block(fat, fs.fat_sector + 1)
    elif corruption == "crosslink":
        fs.write_entry(1, b"CROSSLINK" + bytes([128, 2]) + b"\xff" * 5)
    elif corruption == "name":
        fs.write_entry(0, b"\x01" + fs.get_file_entry("HELLO.BAS").raw[1:])
    else:
        fat = fs.read_fat()
        fat[2] = {"cycle": 2, "free": 255, "reserved": 254, "out_of_range": 170}[corruption]
        fs.write_fat(fat)
    fs.close()
    before = path.read_bytes()
    file = NativeFile(path)
    with pytest.raises(OSError):
        FAT8Filesystem.mount(file)
    file.close()
    assert path.read_bytes() == before


def test_block_io_bounds(tmp_path):
    path = tmp_path / "fat8.img"
    payload = make_image(path)
    fs = mount(path)
    file = fs.open_file("HELLO.BAS")
    for block, count in [(-1, 1), (0, -2), (1000, 1)]:
        with pytest.raises(OSError):
            file.write_block(bytes(256), block, count)
    with pytest.raises(OSError):
        file.write_block(b"short", 0)
    file.seek(253)
    file.write(b"0123456789")
    assert fs.read_bytes("HELLO.BAS") == payload[:253] + b"0123456789" + payload[263:]
    file.close()
    with pytest.raises(OSError):
        file.read_block(0)
    fs.close()


def test_shell(tmp_path, capsys):
    path = tmp_path / "fat8.img"
    make_image(path)
    source = tmp_path / "source"
    source.write_bytes(b"shell copy test")
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /fat8 {path}", batch=True)
    shell.onecmd("dir/brief t:*.bas", batch=True)
    assert "HELLO.BAS" in capsys.readouterr().out
    shell.onecmd(f"copy {source} t:TEST", batch=True)
    fs = shell.volumes.get_volume("T")
    assert fs.read_bytes("TEST").startswith(b"shell copy test")
    shell.onecmd("examine t:", batch=True)
    shell.onecmd("delete t:TEST", batch=True)
    shell.onecmd("dismount t:", batch=True)


@pytest.mark.parametrize(
    "sample,expected_count,known_file,marker",
    [
        ("basic.img", 14, "FORMAT", b"Toshiba Format program"),
        ("alpha4.imd", 4, "FORMAT", b"Copyright by TAAG"),
    ],
)
def test_local_samples(tmp_path, sample, expected_count, known_file, marker):
    original = Path("tmp/fat8") / sample
    if not original.exists():
        pytest.skip("Optional local FAT8 sample is not present")
    digest = hashlib.sha256(original.read_bytes()).digest()
    image = tmp_path / sample
    shutil.copyfile(original, image)
    fs = mount(image)
    contents = {entry.name: entry.read_bytes() for entry in fs.entries_list}
    assert len(contents) == expected_count
    assert marker in contents[known_file]
    fs.write_bytes("ROUNDTRIP", b"sample image round-trip\0\xff" * 300)
    fs.close()
    fs = mount(image)
    assert fs.read_bytes("ROUNDTRIP").startswith(b"sample image round-trip\0\xff" * 300)
    for name, content in contents.items():
        assert fs.read_bytes(name) == content
    fs.get_file_entry("ROUNDTRIP").delete()
    fs.close()
    fs = mount(image)
    assert not fs.exists("ROUNDTRIP")
    assert len(list(fs.entries_list)) == expected_count
    fs.close()
    assert hashlib.sha256(original.read_bytes()).digest() == digest
