import pytest

from xferx.pdp11.files11fs import HOME_BLOCK, INDEXF_SYS, Files11File, Files11Filesystem
from xferx.shell import Shell

DSK = "tests/dsk/files11.dsk"


def test_files11():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /files11 {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, Files11Filesystem)

    indexfs = fs.read_file_header(INDEXF_SYS)
    f = Files11File(indexfs)

    bootstrap_block = fs.read_block(0)
    assert f.read_block(0) == bootstrap_block
    #
    home = fs.read_block(HOME_BLOCK)
    assert b"DECFILE11A" in home
    assert f.read_block(HOME_BLOCK) == home
    f.close()

    with pytest.raises(Exception):
        [x.basename for x in fs.filter_entries_list(pattern="[33,44]*.*")]

    assert fs.chdir("[0,0]")
    files = [x.basename for x in fs.filter_entries_list(pattern="*.*")]
    assert "INDEXF.SYS" in files
    assert "BITMAP.SYS" in files
    assert "000000.DIR" in files
    files = [x.fullname for x in fs.filter_entries_list(pattern="*.*")]
    assert "[0,0]INDEXF.SYS" in files
    assert "[0,0]BITMAP.SYS" in files
    assert "[0,0]000000.DIR" in files
