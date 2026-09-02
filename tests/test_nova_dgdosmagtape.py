import pytest

from xferx.nova.dgdosmagtapefs import DGDOSMagTapeFilesystem
from xferx.nova.dgdosdumpfs import DGDOSDumpFilesystem
from xferx.shell import Shell

DSK = "tests/dsk/nova_magtape.tap"
DSK_DUMP = "tests/dsk/nova_magtape_dump.tap"


def test_dgdos_magtape_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /dgdosmt {DSK}", batch=True)
    fs = shell.volumes.get_volume('T')
    assert isinstance(fs, DGDOSMagTapeFilesystem)
    assert fs.get_root().is_dir()

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("type t:1", batch=True)

    x = fs.read_bytes("5")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.entries_list)
    assert len(l) == 6

    l = list(fs.get_root().iterdir())
    assert len(l) == 6


def test_dgdos_magtape_dump_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount dm0: /dgdosmt {DSK_DUMP}", batch=True)
    fs = shell.volumes.get_volume('DM0')
    assert isinstance(fs, DGDOSMagTapeFilesystem)
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    shell.onecmd("mount dm1: /dgdosdump dm0:5", batch=True)
    fs1 = shell.volumes.get_volume('DM1')
    assert isinstance(fs1, DGDOSDumpFilesystem)
    shell.onecmd("dir dm1:", batch=True)

    x = fs1.read_bytes("/Z20")
    assert x == b'\0' * 10240

    subdir = fs1.get_file_entry("/SUBDIR.DR")
    assert subdir is not None
    assert subdir.is_dir()
    l = list(subdir.iterdir())
    assert len(l) == 5

    subdir = fs1.get_file_entry("/C1.DR")
    assert subdir is not None
    assert subdir.is_dir()
    l = list(subdir.iterdir())
    assert len(l) == 3

    x = fs1.read_bytes("/SUBDIR.DR/SB")
    assert x
    x = fs1.read_bytes("/SUBDIR.DR/COM.CM")
    assert x

    with pytest.raises(FileNotFoundError):
        fs1.get_file_entry("/NOTEXIST")

    with pytest.raises(FileNotFoundError):
        fs1.get_file_entry("/SUBDIR.DR/NOTEXIST")


#
#
# def test_dgdos_magtape_write():
#     shell = Shell(verbose=True)
#     shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
#     shell.onecmd(f"mount in: /dgdosmt {DSK}", batch=True)
#     shell.onecmd(f"mount ou: /dgdosmt {DSK}.mo", batch=True)
#     fs = shell.volumes.get_volume('OU')
#     assert isinstance(fs, DGDOSMagTapeFilesystem)
#
#     d = fs.get_file_entry("5")
#     assert d is not None
#
#     # Delete a file
#     d.delete()
#     with pytest.raises(FileNotFoundError):
#         fs.get_file_entry("5")
#
#     # Create a file
#     shell.onecmd("copy in:5 ou:6", batch=True)
#     x2 = fs.read_bytes("6")
#     x2 = x2.rstrip(b"\0")
#     assert len(x2) == 2200
#     for i in range(0, 50):
#         assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x2
#
#
# def test_dgdos_magtape_init():
#     shell = Shell(verbose=True)
#     shell.onecmd(f"mount in: /dgdosmt {DSK}", batch=True)
#     shell.onecmd(f"create /allocate:280 {DSK}.mo", batch=True)
#     shell.onecmd(f"init /dgdosmt {DSK}.mo", batch=True)
#     shell.onecmd(f"mount ou: /dgdosmt {DSK}.mo", batch=True)
#     shell.onecmd("dir ou:", batch=True)
#     shell.onecmd("copy in:* ou:", batch=True)
#
#     fs = shell.volumes.get_volume('OU')
#     x = fs.read_bytes("5")
#     x = x.rstrip(b"\0")
#     assert len(x) == 2200
#     for i in range(0, 50):
#         assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x
#
#     # Test init mounted volume
#     shell.onecmd("init ou:", batch=True)
#     with pytest.raises(Exception):
#         fs.read_bytes("5")
