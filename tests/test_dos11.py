from rt11.dos11fs import DOS11Filesystem
from rt11.shell import Shell

DSK = "tests/dsk/dos11_rk05.dsk"


def test_dos11():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /dos {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, DOS11Filesystem)

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir /uic t:", batch=True)
    shell.onecmd("type t:1.txt", batch=True)

    x = fs.read_bytes("50.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.filter_entries_list("*.TXT[100,100]"))
    assert len(l) == 3
    assert all([x.contiguous for x in l])

    x1 = fs.read_bytes("[100,100]1000.txt")
    x1 = x1.rstrip(b"\0")
    assert len(x1) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1

    l = list(fs.filter_entries_list("*.TXT[200,200]"))
    assert len(l) == 3
    assert all([not x.contiguous for x in l])

    x2 = fs.read_bytes("[100,100]1000.txt")
    x2 = x2.rstrip(b"\0")
    assert len(x2) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x2
    assert x1 == x2

    l = list(fs.filter_entries_list("*.TXT[1,2]"))
    assert len(l) == 0
