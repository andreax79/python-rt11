import pytest

from xferx.pdp11.files11fs import HOME_BLOCK, INDEXF_SYS, Files11File, Files11Filesystem
from xferx.shell import Shell

DSK = "tests/dsk/files11.dsk"


def test_files11():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /files11 {DSK}", batch=True)
    fs = shell.volumes.get_volume('T')
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()
    assert fs.is_dir("[0,0]")
    assert fs.is_dir("[0,0]")
    assert not fs.is_dir("[0,0]INDEXF.SYS")
    assert not fs.is_dir("[0,0]NOTFOUND")
    assert isinstance(fs, Files11Filesystem)

    dir_entry = fs.get_file_entry("[0,0]")
    assert dir_entry is not None
    assert dir_entry.is_dir()

    indexfs = fs.read_file_header(INDEXF_SYS)
    f = Files11File(indexfs)

    bootstrap_block = fs.read_block(0)
    assert f.read_block(0) == bootstrap_block

    home = fs.read_block(HOME_BLOCK)
    assert b"DECFILE11A" in home
    assert f.read_block(HOME_BLOCK) == home
    f.close()

    with pytest.raises(Exception):
        [x.name for x in fs.filter_entries_list(pattern="[33,44]*.*")]

    assert fs.chdir(fs.get_file_entry("[0,0]")) is None
    with pytest.raises(Exception):
        fs.chdir("invalid")
    files = [x.name for x in fs.filter_entries_list(pattern="*.*")]
    assert "INDEXF.SYS" in files
    assert "BITMAP.SYS" in files
    assert "000000.DIR" in files
    files = [x.fullname for x in fs.filter_entries_list(pattern="*.*")]
    assert "[000000]INDEXF.SYS" in files
    assert "[000000]BITMAP.SYS" in files

    dir_entry = fs.get_file_entry("[5,5]")
    files = [x.name for x in dir_entry.iterdir()]
    assert "10.TXT" in files
    assert "20.TXT" in files
    c = dir_entry.get_child("10.TXT")
    assert c is not None
    assert c.is_file()

    files = [x.name for x in fs.filter_entries_list(pattern="*.*")]
    assert "10.TXT" not in files

    fs.chdir(dir_entry)
    files = [x.name for x in fs.filter_entries_list(pattern="*.*")]
    assert "10.TXT" in files

    x = fs.read_bytes("[5,5]10.txt")
    for i in range(0, 10):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x
