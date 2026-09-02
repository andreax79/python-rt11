import pytest

from xferx.apple.appledosfs import AppleDOSFilesystem
from xferx.apple.commons import (
    AppleSingle,
    ProDOSFileInfo,
)
from xferx.shell import Shell

DSK = "tests/dsk/appledos.dsk"


def test_appledos():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /appledos {DSK}", batch=True)
    fs = shell.volumes.get_volume('T')
    assert isinstance(fs, AppleDOSFilesystem)
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir/brief t:", batch=True)
    shell.onecmd("dir/brief t:*.txt", batch=True)
    shell.onecmd("dir/brief t:*.notfound", batch=True)
    shell.onecmd("type t:1.txt", batch=True)

    x = shell.volumes.read_bytes("t:50.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(shell.volumes.filter_entries_list("t:*.TXT"))
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

    x1 = shell.volumes.read_bytes("ou:50.txt")
    x1 = x1.rstrip(b"\0")
    assert len(x1) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1

    with pytest.raises(Exception):
        shell.onecmd("delete ou:aaa", batch=True)
    shell.onecmd("delete ou:50.txt", batch=True)
    shell.volumes.read_bytes("ou:10.txt")
    with pytest.raises(FileNotFoundError):
        shell.volumes.read_bytes("ou:50.txt")

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        shell.volumes.read_bytes("ou:10.txt")


def test_appledos_init_non_standard():
    shell = Shell(verbose=True)
    shell.onecmd(f"create {DSK}.mo /allocate:505", batch=True)
    shell.onecmd(f"init /appledos {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /appledos {DSK}.mo", batch=True)


def test_apple_single():
    info = ProDOSFileInfo(0xFF, 0x34, 0x5678)
    data = b"Hello, world!"
    apple_single_b = AppleSingle(prodos_file_info=info, data=data).write()
    apple_single = AppleSingle.read(apple_single_b)
    assert info.access == apple_single.prodos_file_info.access
    assert info.file_type == apple_single.prodos_file_info.file_type
    assert info.aux_type == apple_single.prodos_file_info.aux_type

    shell = Shell(verbose=True)
    shell.onecmd(f"create {DSK}.mo /allocate:280", batch=True)
    shell.onecmd(f"init /appledos {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /appledos {DSK}.mo", batch=True)

    shell.onecmd("copy tests/dsk/ciao.apple2 ou:", batch=True)
    test1 = shell.volumes.get_file_entry("ou:ciao.apple2")
    assert test1.file_type == "B"
    apple_single3 = shell.volumes.read_bytes("ou:ciao.apple2")
    apple_single2 = AppleSingle.read(apple_single3)
    assert apple_single2.prodos_file_info.file_type == 0x6
    assert apple_single2.prodos_file_info.aux_type == 0x2000
