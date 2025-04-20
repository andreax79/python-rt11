import random
from datetime import datetime, timedelta

import pytest

from xferx.apple2.commons import (
    ProDOSFileInfo,
    decode_apple_single,
    encode_apple_single,
)
from xferx.apple2.prodosfs import (
    ProDOSFilesystem,
    date_to_prodos,
    parse_file_aux_type,
    prodos_to_date,
)
from xferx.shell import Shell

DSK = "tests/dsk/prodos.dsk"


def test_date_round_trip():
    start_year = 1980
    end_year = 2020
    for i in range(0, 100):
        start_date = datetime(start_year, 1, 1)
        end_date = datetime(end_year, 12, 31, 23, 59, 59)
        random_seconds = random.randint(0, int((end_date - start_date).total_seconds()))
        random_seconds = random_seconds - (random_seconds % 60)
        dt = start_date + timedelta(seconds=random_seconds)
        assert prodos_to_date(date_to_prodos(dt)) == dt


def test_bitmap():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /prodos {DSK}", batch=True)
    fs = shell.volumes.get('T')
    bitmap = fs.read_bitmap()
    print(f"used: {bitmap.used()} free: {bitmap.free()} total: {fs.total_blocks}")
    u = set()
    for b in fs.root.iterdir():
        if hasattr(b, "blocks"):
            u.update(b.blocks())
    for b in fs.root.iterdir():
        if hasattr(b, "blocks"):
            print(b, list(b.blocks()))
            for x in b.blocks():
                assert not bitmap.is_free(x)
    for i in range(0, fs.total_blocks):
        if bitmap.is_free(i):
            assert i not in u


def test_prodos():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /prodos {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, ProDOSFilesystem)

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir/brief t:", batch=True)
    shell.onecmd("dir/brief t:/pr", batch=True)
    with pytest.raises(FileNotFoundError):
        shell.onecmd("dir/brief t:/notfound", batch=True)
    with pytest.raises(FileNotFoundError):
        shell.onecmd("dir/brief t:/pr/notfound/test", batch=True)
    shell.onecmd("type t:/pr/small/1.txt", batch=True)

    x = fs.read_bytes("/pr/small/medium/50.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.filter_entries_list("small/medium/*.TXT"))
    assert len(l) == 3


def test_prodos_init():
    # Init
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /prodos {DSK}", batch=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"init /prodos {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /prodos {DSK}.mo", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("create/directory ou:aaa", batch=True)
    shell.onecmd("create/directory ou:aaa/bbb", batch=True)
    shell.onecmd("copy t:small/medium/*.txt ou:aaa/bbb", batch=True)
    shell.onecmd("copy t:small/1.txt ou:", batch=True)
    fs = shell.volumes.get('OU')

    x1 = fs.read_bytes("aaa/bbb/50.txt")
    x1 = x1.rstrip(b"\0")
    assert len(x1) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1

    shell.onecmd("delete ou:aaa", batch=True)
    assert fs.read_bytes("1.txt")
    with pytest.raises(FileNotFoundError):
        x1 = fs.read_bytes("aaa/bbb/50.txt")
    shell.onecmd("ou:", batch=True)
    shell.onecmd("cd aaa/bbb", batch=True)
    shell.onecmd("dir", batch=True)

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        fs.read_bytes("1.txt")


def test_grow_dir():
    # Test grow directory
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount t: /prodos {DSK}", batch=True)
    shell.onecmd(f"mount ou: /prodos {DSK}.mo", batch=True)
    for i in range(0, 50):
        shell.onecmd(f"create/file ou:{i:05d}.txt /type:txt /allocate:1", batch=True)
    shell.onecmd("dir ou:", batch=True)


def test_types():
    assert parse_file_aux_type("txt,3000") == (0x4, 3000)
    assert parse_file_aux_type("bin,$2000") == (0x6, 0x2000)
    assert parse_file_aux_type("txt") == (0x4, 0)
    assert parse_file_aux_type("$99") == (0x99, 0)
    assert parse_file_aux_type("$99,$ff") == (0x99, 0xFF)

    shell = Shell(verbose=True)
    shell.onecmd(f"create {DSK}.mo /allocate:2000", batch=True)
    shell.onecmd(f"init /prodos {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /prodos {DSK}.mo", batch=True)
    fs = shell.volumes.get('OU')
    shell.onecmd("create ou:test1 /allocate:1 /type:txt,3000", batch=True)
    test1 = fs.get_file_entry("test1")
    assert test1.storage_type == 0x1
    assert test1.prodos_file_type == 0x4
    assert test1.aux_type == 3000
    shell.onecmd("create ou:test2 /allocate:1 /type:bin,$2000", batch=True)
    test2 = fs.get_file_entry("test2")
    assert test2.storage_type == 0x1
    assert test2.prodos_file_type == 0x6
    assert test2.aux_type == 0x2000


def test_pascal_area():
    shell = Shell(verbose=True)
    shell.onecmd(f"create {DSK}.mo /allocate:2000", batch=True)
    shell.onecmd(f"init /prodos {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /prodos {DSK}.mo", batch=True)
    shell.onecmd("create ou:pascal.area /allocate:500", batch=True)
    shell.onecmd("create ou:pascal.area/vol1 /allocate:200", batch=True)
    shell.onecmd("create ou:pascal.area/vol2 /allocate:100", batch=True)
    shell.onecmd("create ou:pascal.area/vol3 /allocate:100", batch=True)
    with pytest.raises(OSError):
        shell.onecmd("create ou:pascal.area/vol4 /allocate:1000", batch=True)
    shell.onecmd("del ou:pascal.area/vol2", batch=True)
    with pytest.raises(OSError):
        shell.onecmd("create ou:pascal.area/vol2 /allocate:101", batch=True)
    shell.onecmd("create ou:pascal.area/vol2a /allocate:50", batch=True)
    shell.onecmd("create ou:pascal.area/vol2b /allocate:50", batch=True)
    shell.onecmd("dir ou:pascal.area", batch=True)
    shell.onecmd("ex ou:pascal.area", batch=True)


def test_apple_single():
    info = ProDOSFileInfo(0xFF, 0x34, 0x5678)
    data = b"Hello, world!"
    resource = b"Resource"
    apple_single = encode_apple_single(info, data, resource)
    data2, resource2, info2 = decode_apple_single(apple_single)
    assert data == data2
    assert resource == resource2
    assert info.access == info2.access
    assert info.file_type == info2.file_type
    assert info.aux_type == info2.aux_type
    apple_single3 = encode_apple_single(info, data)
    data3, resource3, info3 = decode_apple_single(apple_single3)
    assert data == data3
    assert resource3 is None
    assert info == info3

    shell = Shell(verbose=True)
    shell.onecmd(f"create {DSK}.mo /allocate:2000", batch=True)
    shell.onecmd(f"init /prodos {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /prodos {DSK}.mo", batch=True)
    fs = shell.volumes.get('OU')
    shell.onecmd("copy tests/dsk/ciao.apple2 ou:", batch=True)
    test1 = fs.get_file_entry("ciao.apple2")
    assert test1.storage_type == 0x2
    assert test1.prodos_file_type == 0x6
    assert test1.aux_type == 8192

    fs.write_bytes(fullname="test.extended", content=apple_single)
    test2 = fs.get_file_entry("test.extended")
    assert test2.storage_type == 0x5
    assert test2.prodos_file_type == 0x34
    assert test2.aux_type == 0x5678
    assert data == fs.read_bytes("test.extended/data.fork")
    assert resource == fs.read_bytes("test.extended/resource.fork")
