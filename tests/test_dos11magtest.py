from rt11.pdp11.dos11magtapefs import DOS11MagTapeFilesystem
from rt11.shell import Shell

DSK = "tests/dsk/dos11_magtape.tap"


def test_dos11magtape_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /magtape {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, DOS11MagTapeFilesystem)

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


def test_dos11magtape_write():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount in: /magtape {DSK}", batch=True)
    shell.onecmd(f"mount ou: /magtape {DSK}.mo", batch=True)
    fs = shell.volumes.get('OU')
    assert isinstance(fs, DOS11MagTapeFilesystem)

    d = fs.get_file_entry("500.TXT")
    assert d is not None

    # Delete a file
    d.delete()
    d2 = fs.get_file_entry("500.TXT")
    assert d2 is None

    # Create a file
    shell.onecmd("copy in:10.TXT ou:10NEW.TXT", batch=True)
    x2 = fs.read_bytes("10NEW.txt")
    x2 = x2.rstrip(b"\0")
    assert len(x2) == 440
    for i in range(0, 10):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x2

    # Init
    shell.onecmd("init ou:", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("copy in:*.TXT ou:", batch=True)
    shell.onecmd("copy in:*.TXT ou:", batch=True)

    x = fs.read_bytes("1000.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x
