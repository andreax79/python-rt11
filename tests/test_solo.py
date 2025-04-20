import pytest

from xferx.pdp11.solofs import SOLOBitmap, SOLOFilesystem
from xferx.shell import Shell

DSK = "tests/dsk/solo.dsk"


def test_solo_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /solo {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, SOLOFilesystem)

    shell.onecmd("dir t:[*,*]", batch=True)
    shell.onecmd("type t:1.txt", batch=True)

    x = fs.read_bytes("50.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.entries_list)
    filenames = [x.filename for x in l if not x.is_empty]
    assert "NEXT" in filenames

    entry = fs.get_file_entry("NEXT")
    assert entry.protected


def test_solo_write():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount in: /solo {DSK}", batch=True)
    shell.onecmd(f"mount ou: /solo {DSK}.mo", batch=True)
    fs = shell.volumes.get('OU')
    assert isinstance(fs, SOLOFilesystem)

    d = fs.get_file_entry("50.TXT")
    assert d is not None

    # Delete a file
    d.delete()
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("50.TXT")

    # Create a file
    shell.onecmd("copy in:10.TXT ou:10NEW.TXT", batch=True)
    x2 = fs.read_bytes("10NEW.txt")
    x2 = x2.rstrip(b"\0")
    assert len(x2) == 440
    for i in range(0, 10):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x2


def test_solo_init_write():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount in: /solo {DSK}", batch=True)
    shell.onecmd(f"create /allocate:4800 {DSK}.mo", batch=True)
    shell.onecmd(f"init /solo {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /solo {DSK}.mo", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("copy in:*.TXT ou:", batch=True)
    fs = shell.volumes.get('OU')

    x = fs.read_bytes("50.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    entry = fs.get_file_entry("50.txt")
    hash_key = entry.hash_key
    assert fs.get_searchlength(hash_key) == 1

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        fs.read_bytes("50.txt")


def test_dos11_bitmap():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount in: /solo {DSK}", batch=True)
    shell.onecmd(f"mount ou: /solo {DSK}.mo", batch=True)
    fs = shell.volumes.get('OU')
    assert isinstance(fs, SOLOFilesystem)

    # Test is_free
    entry = fs.get_file_entry("50.txt")
    bitmap = SOLOBitmap.read(fs)
    free = bitmap.free()
    page_map = list(entry.page_map)
    for i in page_map:
        assert not bitmap.is_free(i)
    length = entry.length
    entry.delete()
    bitmap = SOLOBitmap.read(fs)
    for i in page_map:
        assert bitmap.is_free(i)
    assert bitmap.free() == free + length + 1

    bitmap.allocate(10)
    f0 = bitmap.find_first_free()
    f1 = bitmap.find_first_free()
    assert f0 == f1
    bitmap.write()
    f2 = bitmap.find_first_free()
    print(f2)
    bitmap.write()


def test_solo_segments():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount in: /solo {DSK}", batch=True)
    shell.onecmd(f"mount ou: /solo {DSK}.mo", batch=True)
    fs = shell.volumes.get('OU')
    assert isinstance(fs, SOLOFilesystem)

    # Get segment entries
    entry = fs.get_file_entry("@KERNEL")
    assert entry.length == 24
    assert entry.protected
    assert entry.file_type == "SEGMENT"
    with pytest.raises(Exception):
        assert entry.delete()
    entry = fs.get_file_entry("@SOLO")
    assert entry.length == 64
    entry = fs.get_file_entry("@OTHEROS")
    assert entry.length == 64
    with pytest.raises(Exception):
        fs.get_file_entry("@NOTFOUND")

    # Create a segment
    shell.onecmd("copy /type:concode in:50.TXT ou:@SOLO", batch=True)
    x2 = fs.read_bytes("@SOLO")
    x2 = x2.rstrip(b"\0")
    assert len(x2) > 2000
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x2
