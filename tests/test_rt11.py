from datetime import date

import pytest

from xferx.pdp11.rt11fs import (
    RT11Filesystem,
    date_to_rt11,
    rt11_canonical_filename,
    rt11_to_date,
)
from xferx.shell import Shell

DSK = "tests/dsk/rt11.dsk"


def test_rt11_to_date():
    # Test with None
    assert rt11_to_date(0) is None
    # Test with valid input
    assert date(1979, 1, 6) == rt11_to_date(1223)
    assert date(1984, 2, 5) == rt11_to_date(2220)
    assert date(1991, 12, 31) == rt11_to_date(13299)
    assert date(2000, 1, 1) == rt11_to_date(1084)
    assert date(2014, 3, 27) == rt11_to_date(20330)
    assert date(2024, 1, 1) == rt11_to_date(17460)


def test_date_to_rt11():
    # Test with None
    assert date_to_rt11(None) == 0
    # Test with valid input
    assert date_to_rt11(date(1979, 1, 6)) == 1223
    assert date_to_rt11(date(1984, 2, 5)) == 2220
    assert date_to_rt11(date(1991, 12, 31)) == 13299
    assert date_to_rt11(date(2000, 1, 1)) == 1084
    assert date_to_rt11(date(2014, 3, 27)) == 20330
    assert date_to_rt11(date(2024, 1, 1)) == 17460


def test_rt1_canonical_filename():
    assert rt11_canonical_filename(None) == "."
    assert rt11_canonical_filename("") == "."
    assert rt11_canonical_filename("LICENSE") == "LICENS."
    assert rt11_canonical_filename("license.") == "LICENS."
    assert rt11_canonical_filename("read.me") == "READ.ME"
    assert rt11_canonical_filename("read.*", wildcard=True) == "READ.*"
    assert rt11_canonical_filename("r*", wildcard=True) == "R*.*"
    assert rt11_canonical_filename("*.*", wildcard=True) == "*.*"


def test_rt11():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /rt11 {DSK}", batch=True)
    fs = shell.volumes.get_volume('T')
    assert isinstance(fs, RT11Filesystem)
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir/brief t:", batch=True)
    shell.onecmd("dir/brief t:*.txt", batch=True)
    shell.onecmd("dir/brief t:*.notfound", batch=True)
    shell.onecmd("type t:1.txt", batch=True)

    x = fs.read_bytes("50.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.filter_entries_list("*.TXT"))
    assert len(l) == 11
    for x in l:
        assert not x.is_empty()
        assert str(x)


def test_rt11_init():
    disk = f"{DSK}.rt11_init.mo"
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /rt11 {DSK}", batch=True)
    shell.onecmd(f"create /allocate:800 {disk}", batch=True)
    shell.onecmd(f"init /rt11 {disk}", batch=True)
    shell.onecmd(f"mount ou: /rt11 {disk}", batch=True)
    shell.onecmd("dir ou:", batch=True)

    fs = shell.volumes.get_volume('OU')
    assert fs.get_partition(0).free() == 786

    shell.onecmd("copy t:*.txt ou:", batch=True)

    x1 = shell.volumes.read_text("ou:50.txt")
    assert len(x1) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" in x1

    # Delete
    with pytest.raises(Exception):
        shell.onecmd("delete ou:aaa", batch=True)
    shell.onecmd("delete ou:50.txt", batch=True)
    with pytest.raises(FileNotFoundError):
        shell.volumes.read_bytes("ou:50.txt")

    content = b"\0" * 1024
    for i in range(0, 80):
        shell.volumes.write_bytes(f"ou:{i}.dat", content)

    shell.onecmd("delete ou:30.dat", batch=True)
    shell.onecmd("delete ou:31.dat", batch=True)

    content = b"\0" * 512
    for i in range(80, 90):
        shell.volumes.write_bytes(f"ou:{i}.dat", content)

    x2 = shell.volumes.read_bytes("ou:10.txt")
    assert len(x2) == 512
    with shell.volumes.open_file("ou:10.txt") as f:
        print(f.entry.metadata)
        pass

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        shell.volumes.read_bytes("ou:10.txt")

    shell.onecmd(f"delete {disk}", batch=True)
