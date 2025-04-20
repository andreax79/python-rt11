import pytest

from xferx.pdp11.caps11fs import CAPS11Filesystem
from xferx.shell import Shell

DSK = "tests/dsk/caps11.t60"


def test_caps11_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /caps11 {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, CAPS11Filesystem)

    shell.onecmd("dir t:[*,*]", batch=True)
    shell.onecmd("type t:1.txt", batch=True)

    x = fs.read_bytes("1000.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.entries_list)
    assert len(l) == 9


def test_caps11_write():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount in: /caps11 {DSK}", batch=True)
    shell.onecmd(f"mount ou: /caps11 {DSK}.mo", batch=True)
    fs = shell.volumes.get('OU')
    assert isinstance(fs, CAPS11Filesystem)

    d = fs.get_file_entry("500.TXT")

    # Delete a file
    d.delete()
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("500.TXT")

    # Create a file
    shell.onecmd("copy in:10.TXT ou:10NEW.TXT", batch=True)
    x2 = fs.read_bytes("10NEW.txt")
    x2 = x2.rstrip(b"\0")
    assert len(x2) == 440
    for i in range(0, 10):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x2


def test_caps11_init():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount in: /caps11 {DSK}", batch=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"init /caps11 {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /caps11 {DSK}.mo", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("copy in:*.TXT ou:", batch=True)
    fs = shell.volumes.get('OU')

    x = fs.read_bytes("1000.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        fs.read_bytes("1000.txt")
