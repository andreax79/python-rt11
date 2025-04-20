import pytest

from xferx.apple2.appledosfs import AppleDOSFilesystem
from xferx.apple2.commons import (
    ProDOSFileInfo,
    decode_apple_single,
    encode_apple_single,
)
from xferx.shell import Shell

DSK = "tests/dsk/appledos.dsk"


def test_appledos():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /appledos {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, AppleDOSFilesystem)

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
    assert len(l) == 10
    for x in l:
        assert not x.is_empty
        assert str(x)


def test_appledos_init():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /appledos {DSK}", batch=True)
    shell.onecmd(f"create /allocate:280 {DSK}.mo", batch=True)
    shell.onecmd(f"init /appledos {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /appledos {DSK}.mo", batch=True)
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


def test_appledos_init_non_standard():
    shell = Shell(verbose=True)
    shell.onecmd(f"create {DSK}.mo /allocate:505", batch=True)
    shell.onecmd(f"init /appledos {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /appledos {DSK}.mo", batch=True)


def test_apple_single():
    info = ProDOSFileInfo(0xFF, 0x34, 0x5678)
    data = b"Hello, world!"
    apple_single = encode_apple_single(info, data)
    data2, _, info2 = decode_apple_single(apple_single)
    assert info.access == info2.access
    assert info.file_type == info2.file_type
    assert info.aux_type == info2.aux_type

    shell = Shell(verbose=True)
    shell.onecmd(f"create {DSK}.mo /allocate:280", batch=True)
    shell.onecmd(f"init /appledos {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /appledos {DSK}.mo", batch=True)
    fs = shell.volumes.get('OU')

    shell.onecmd("copy tests/dsk/ciao.apple2 ou:", batch=True)
    test1 = fs.get_file_entry("ciao.apple2")
    assert test1.file_type == "B"
    apple_single3 = fs.read_bytes("ciao.apple2")
    _, _, info3 = decode_apple_single(apple_single3)
    assert info3.file_type == 0x6
    assert info3.aux_type == 0x2000
