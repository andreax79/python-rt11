import pytest

from xferx.pdp11.dos11magtapefs import DOS11MagTapeFilesystem
from xferx.shell import Shell

DSK = "tests/dsk/dos11_magtape.tap"


def test_dos11magtape_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /dos11mt {DSK}", batch=True)
    fs = shell.volumes.get_volume('T')
    assert fs.is_dir("[1,1]")
    assert not fs.is_dir("[1,1]1.txt")
    assert not fs.is_dir("[1,1]notfound")
    assert isinstance(fs, DOS11MagTapeFilesystem)

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir /uic t:", batch=True)
    shell.onecmd("type t:[1,1]1.txt", batch=True)

    x = fs.read_bytes("[1,1]1000.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.entries_list)
    assert len(l) == 1
    assert all(x.is_dir() for x in l)

    shell.onecmd("cd t:[1,1]", batch=True)
    l = list(fs.entries_list)
    assert len(l) == 9
    assert all(not x.is_dir() for x in l)


def test_dos11magtape_write():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount in: /dos11mt {DSK}", batch=True)
    shell.onecmd(f"mount ou: /dos11mt {DSK}.mo", batch=True)
    fs = shell.volumes.get_volume('OU')
    assert isinstance(fs, DOS11MagTapeFilesystem)
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    d = fs.get_file_entry("[1,1]500.TXT")
    assert d is not None

    # Delete a file
    d.delete()
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("[1,1]500.TXT")

    # Create a file
    shell.onecmd("copy in:[1,1]10.TXT ou:[1,1]10NEW.TXT", batch=True)
    x2 = fs.read_bytes("[1,1]10NEW.txt")
    x2 = x2.rstrip(b"\0")
    assert len(x2) == 440
    for i in range(0, 10):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x2


def test_dos11magtape_init():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount in: /dos11mt {DSK}", batch=True)
    shell.onecmd(f"create /allocate:280 {DSK}.mo", batch=True)
    shell.onecmd(f"init /dos11mt {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /dos11mt {DSK}.mo", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("copy in:[1,1]*.TXT ou:[1,1]", batch=True)
    shell.onecmd("copy in:[1,1]*.TXT ou:[1,1]", batch=True)

    fs = shell.volumes.get_volume('OU')
    x = fs.read_bytes("[1,1]1000.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        fs.read_bytes("[1,1]1000.txt")
