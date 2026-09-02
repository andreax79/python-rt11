from datetime import date, datetime

import pytest

from xferx.apple.commons import AppleSingle, FinderInfo
from xferx.apple.mfs import (
    date_to_mfs,
    mfs_to_date,
)
from xferx.commons import DATA_FORK, RESOURCE_FORK
from xferx.shell import Shell

DSK = "tests/dsk/mfs.dsk"


def test_mfs_date_roundtrip():
    dt = datetime(1980, 1, 2, 3, 4, 5)
    assert mfs_to_date(date_to_mfs(dt)) == dt

    dt = datetime(2023, 5, 17, 15, 30, 45)
    assert mfs_to_date(date_to_mfs(dt)) == dt

    d = date(2023, 5, 17)
    expected_dt = datetime(2023, 5, 17, 0, 0, 0)
    assert mfs_to_date(date_to_mfs(d)) == expected_dt

    assert mfs_to_date(date_to_mfs(None)) is None


def test_mfs_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /mfs {DSK}", batch=True)
    fs = shell.volumes.get_volume("T")
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir/brief t:", batch=True)
    shell.onecmd("dir/brief t:notfound", batch=True)
    shell.onecmd("type t:1.txt", batch=True)

    x = shell.volumes.read_text("t:50.txt")
    assert len(x) == 2200
    tmp = "\n".join([f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" for i in range(0, 50)])
    assert x == tmp + "\n"

    l = list(fs.filter_entries_list("*.txt"))
    assert len(l) == 10


def test_mfs_write():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount t: /mfs {DSK}.mo", batch=True)

    x = shell.volumes.read_text("t:10.txt")
    tmp = "\n".join([f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" for i in range(0, 10)])
    assert x == tmp + "\n"

    shell.volumes.get_file_entry("t:5.txt").delete()

    x = shell.volumes.read_text("t:10.txt")
    tmp = "\n".join([f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" for i in range(0, 10)])
    assert x == tmp + "\n"

    shell.onecmd("dismount t:", batch=True)
    shell.onecmd(f"del {DSK}.mo", batch=True)


def test_mfs_init():
    shell = Shell(verbose=True)
    shell.onecmd(f"create /allocate:800 {DSK}.mo", batch=True)
    shell.onecmd(f"init /mfs {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /mfs {DSK}.mo", batch=True)

    fs = shell.volumes.get_volume('OU')

    data = b"Hello, world!" * 100
    resource = b"Resource" * 100
    finder_info = FinderInfo()
    finder_info.raw_file_type = b"abcd"
    finder_info.raw_file_creator = b"ABCD"
    finder_info.finder_flags = 0x80
    finder_info.icon_position = 1
    finder_info.folder_number = 2

    # Create a file from AppleSingle
    apple_single_b = AppleSingle(finder_info=finder_info, data=data, resource=resource).write()
    fs.write_bytes("test1", apple_single_b)

    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("ex ou:", batch=True)
    entry = fs.get_file_entry("test1")
    assert entry is not None
    assert entry.get_size(fork=DATA_FORK) == len(data)
    assert entry.get_size(fork=RESOURCE_FORK) == len(resource)
    assert fs.read_bytes("test1", fork=DATA_FORK) == data
    assert fs.read_bytes("test1", fork=RESOURCE_FORK) == resource

    # Expand data fork
    data1 = b"abcdefghi" * 50
    fs.write_bytes("test1", data1, fork=DATA_FORK)
    entry1 = fs.get_file_entry("test1")
    shell.onecmd("ex ou:", batch=True)
    assert entry1.get_size(fork=DATA_FORK) == len(data1)
    assert fs.read_bytes("test1", fork=DATA_FORK) == data1
    assert entry1.get_size(fork=RESOURCE_FORK) == len(resource)
    assert fs.read_bytes("test1", fork=RESOURCE_FORK) == resource
    assert entry1.creation_date == entry.creation_date

    # Shrink resource fork
    resource2 = b"0123456789" * 150
    fs.write_bytes("test1", resource2, fork=RESOURCE_FORK)
    entry2 = fs.get_file_entry("test1")
    shell.onecmd("ex ou:test1", batch=True)
    assert entry2.get_size(fork=DATA_FORK) == len(data1)
    assert fs.read_bytes("test1", fork=DATA_FORK) == data1
    assert entry2.get_size(fork=RESOURCE_FORK) == len(resource2)
    assert fs.read_bytes("test1", fork=RESOURCE_FORK) == resource2
    assert entry2.creation_date == entry.creation_date

    # Truncate data fork
    data3 = b""
    fs.write_bytes("test1", data3, fork=DATA_FORK)
    entry3 = fs.get_file_entry("test1")
    shell.onecmd("ex ou:test1", batch=True)
    assert entry3.get_size(fork=DATA_FORK) == len(data3)
    assert fs.read_bytes("test1", fork=DATA_FORK) == data3
    assert entry2.get_size(fork=RESOURCE_FORK) == len(resource2)
    assert fs.read_bytes("test1", fork=RESOURCE_FORK) == resource2
    assert entry2.creation_date == entry.creation_date

    shell.onecmd("dismount ou:", batch=True)
    shell.onecmd(f"del {DSK}.mo", batch=True)
