import errno
from pathlib import Path

import pytest

from xferx.fat.fat12 import FAT12Filesystem
from xferx.native import NativeFile
from xferx.volumes import FILESYSTEMS

IMAGE = Path(__file__).parent / "dsk" / "fat12.dsk"


@pytest.fixture
def fat12():
    filesystem = FAT12Filesystem.mount(NativeFile(IMAGE))
    yield filesystem
    filesystem.close()


def test_mount_and_list_disk_image(fat12):
    assert FILESYSTEMS["fat12"] is FAT12Filesystem
    assert fat12.sector_size == 512
    assert fat12.sectors_per_cluster == 2
    assert [entry.name for entry in fat12.entries_list] == [
        "1.TXT",
        "2.TXT",
        "5.TXT",
        "10.TXT",
        "20.TXT",
        "50.TXT",
        "100.TXT",
        "200.TXT",
        "500.TXT",
        "DIR1",
        "DIR2",
    ]


def test_initialize(tmp_path):
    path = tmp_path / "fat12.img"
    path.write_bytes(bytes(720 * 512))

    filesystem = FAT12Filesystem.initialize(NativeFile(path))

    assert filesystem.sector_size == 512
    assert filesystem.total_sectors == 720
    assert filesystem.sectors_per_cluster == 2
    assert list(filesystem.entries_list) == []
    filesystem.close()


@pytest.mark.parametrize(
    "filename,size",
    [
        ("1.TXT", 44),
        ("2.TXT", 88),
        ("5.TXT", 220),
        ("10.TXT", 440),
        ("20.TXT", 880),
        ("50.TXT", 2200),
        ("100.TXT", 4400),
        ("200.TXT", 8800),
        ("500.TXT", 22000),
    ],
)
def test_read_files_and_short_aliases(fat12, filename, size):
    entry = fat12.get_file_entry(filename)
    assert entry.get_size() == size
    assert fat12.read_bytes(filename) == (IMAGE.parent / "data" / filename.lower()).read_bytes()
    with entry.open() as file:
        assert len(file.read_block(0, -1)) == entry.get_length() * fat12.sector_size


def test_create_write_overwrite_delete_and_remount(tmp_path):
    path = tmp_path / "fat12.img"
    path.write_bytes(bytes(720 * 512))
    filesystem = FAT12Filesystem.initialize(NativeFile(path))

    payload = b"first version"
    filesystem.write_bytes("hello.txt", payload)
    assert filesystem.read_bytes("HELLO.TXT") == payload

    replacement = bytes(range(256)) * 12
    filesystem.write_bytes("HELLO.TXT", replacement)
    assert filesystem.read_bytes("hello.txt") == replacement

    entry = filesystem.create_file("DIRECT.BIN", 4)
    with entry.open() as file:
        assert file.write(b"data") == 4
    assert filesystem.read_bytes("DIRECT.BIN") == b"data"
    assert entry.delete()
    assert not filesystem.exists("DIRECT.BIN")
    filesystem.close()

    filesystem = FAT12Filesystem.mount(NativeFile(path))
    assert filesystem.read_bytes("HELLO.TXT") == replacement
    assert filesystem.get_file_entry("HELLO.TXT").delete()
    assert not filesystem.exists("HELLO.TXT")
    filesystem.close()


def test_create_and_write_directory_with_growth(tmp_path):
    path = tmp_path / "fat12.img"
    path.write_bytes(bytes(720 * 512))
    filesystem = FAT12Filesystem.initialize(NativeFile(path))

    directory = filesystem.create_directory("nested", {})
    assert directory.is_dir()
    assert {entry.name for entry in directory.iterdir()} == {".", ".."}
    filesystem.write_bytes("nested/inside.txt", b"nested data")
    assert filesystem.read_bytes("nested/inside.txt") == b"nested data"

    for index in range(33):
        filesystem.create_file(f"nested/F{index:02}.TXT", 0)
    assert len(list(directory.iterdir())) == 36
    with pytest.raises(OSError) as exc:
        directory.delete()
    assert exc.value.errno == errno.ENOTEMPTY

    filesystem.get_file_entry("nested/inside.txt").delete()
    for index in range(33):
        filesystem.get_file_entry(f"nested/F{index:02}.TXT").delete()
    assert [entry.name for entry in directory.iterdir(include_all=True) if not entry.is_deleted()] == [
        ".",
        "..",
    ]
    assert directory.delete()
    assert not filesystem.exists("nested")
    filesystem.close()

    filesystem = FAT12Filesystem.mount(NativeFile(path))
    assert not filesystem.exists("nested")
    filesystem.close()
