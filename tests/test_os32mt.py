import pytest

from xferx.interdata.os32tapefs import OS32TapeFilesystem
from xferx.shell import Shell

DSK = "tests/dsk/os32mt.tap"


def test_os32mt_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /os32mt {DSK}", batch=True)
    fs = shell.volumes.get_volume('T')
    assert isinstance(fs, OS32TapeFilesystem)

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir /uic t:", batch=True)
    shell.onecmd("type t:d1.txt", batch=True)

    x = fs.read_text("d1000.txt")
    assert len(x) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" in x

    l = list(fs.get_root().iterdir())
    assert len(l) == 11


def test_os32mt_write():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount in: /os32mt {DSK}", batch=True)
    shell.onecmd(f"mount ou: /os32mt {DSK}.mo", batch=True)
    fs = shell.volumes.get_volume('OU')
    assert isinstance(fs, OS32TapeFilesystem)
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    d = fs.get_file_entry("d500.TXT")
    for k in fs.fs_entry_metadata:
        assert d.metadata[k] is not None

    # Delete a file
    d.delete()
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("d500.TXT")

    # Create a file
    shell.onecmd("copy /ascii in:D10.TXT ou:D10NEW.TXT", batch=True)
    x1 = fs.read_text("D10.txt")
    assert len(x1) == 440
    x2 = fs.read_text("D10NEW.txt")
    assert len(x2) == 440
    for i in range(0, 10):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" in x2


def test_os32mt_init():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount in: /os32mt {DSK}", batch=True)
    shell.onecmd(f"create /allocate:280 {DSK}.mo", batch=True)
    shell.onecmd(f"init /os32mt {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /os32mt {DSK}.mo", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("copy in:*.TXT ou:", batch=True)
    shell.onecmd("copy in:*.TXT ou:", batch=True)
    shell.onecmd("dir ou:", batch=True)

    fs = shell.volumes.get_volume('OU')
    l = list(fs.get_root().iterdir())
    assert len(l) == 11

    x = fs.read_text("d1000.txt")
    assert len(x) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" in x

    l0 = len(list(fs.filter_entries_list("*.txt", account=0, wildcard=True)))
    assert l0 == 3
    l10 = len(list(fs.filter_entries_list("*.txt", account=10, wildcard=True)))
    assert l10 == 3

    shell.onecmd("del ou:*.txt/10", batch=True)
    l10 = len(list(fs.filter_entries_list("*.txt", account=10, wildcard=True)))
    assert l10 == 0

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        fs.read_bytes("d1000.txt")
