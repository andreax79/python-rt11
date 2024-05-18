from rt11.dos11fs import DOS11Filesystem
from rt11.shell import Shell

DSK = "tests/dsk/dos11_dectape.tap"


def test_dos11():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /dos {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, DOS11Filesystem)

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir /uic t:", batch=True)
    shell.onecmd("type t:1.txt", batch=True)

    x = fs.read_bytes("1000.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.entries_list)
    assert len(l) == 9
