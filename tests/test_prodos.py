import random
from datetime import datetime, timedelta

import pytest

from rt11.prodosfs import ProDOSFilesystem, date_to_prodos, prodos_to_date
from rt11.shell import Shell

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
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount t: /prodos {DSK}", batch=True)
    shell.onecmd(f"mount ou: /prodos {DSK}.mo", batch=True)
    shell.onecmd("init ou:", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("create/directory ou:aaa", batch=True)
    shell.onecmd("create/directory ou:aaa/bbb", batch=True)
    shell.onecmd("copy t:small/medium/*.txt ou:aaa/bbb", batch=True)
    fs = shell.volumes.get('OU')

    x1 = fs.read_bytes("aaa/bbb/50.txt")
    x1 = x1.rstrip(b"\0")
    assert len(x1) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1

    shell.onecmd("delete ou:aaa", batch=True)
    with pytest.raises(FileNotFoundError):
        x1 = fs.read_bytes("aaa/bbb/50.txt")


def test_grow_dir():
    # Init
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount t: /prodos {DSK}", batch=True)
    shell.onecmd(f"mount ou: /prodos {DSK}.mo", batch=True)
    for i in range(0, 50):
        shell.onecmd(f"create/file ou:{i:05d}.txt /type:txt /allocate:1", batch=True)
    shell.onecmd("dir ou:", batch=True)
