import pytest

from xferx.pdp11.rt11fs import RT11Filesystem
from xferx.shell import Shell

DSK = "tests/dsk/rt11.dsk"


def test_rt11():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /rt11 {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, RT11Filesystem)

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
        assert not x.is_empty
        assert str(x)


def test_rt11_init():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /rt11 {DSK}", batch=True)
    shell.onecmd(f"create /allocate:500 {DSK}.mo", batch=True)
    shell.onecmd(f"init /rt11 {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /rt11 {DSK}.mo", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("copy/type:t t:*.txt ou:", batch=True)
    fs = shell.volumes.get('OU')

    x1 = fs.read_bytes("50.txt")
    x1 = x1.rstrip(b"\0")
    assert len(x1) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1

    with pytest.raises(Exception):
        shell.onecmd("delete ou:aaa", batch=True)
    shell.onecmd("delete ou:50.txt", batch=True)
    fs.read_bytes("10.txt")
    with pytest.raises(FileNotFoundError):
        fs.read_bytes("50.txt")

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        fs.read_bytes("10.txt")
