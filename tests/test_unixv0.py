from rt11.shell import Shell
from rt11.unix0fs import UNIXFilesystem0

DSK = "tests/dsk/unixv0.dsk"


def test_unix0_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /unix0 {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, UNIXFilesystem0)
    assert fs.version == 0

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir t:/", batch=True)
    shell.onecmd("dir t:/system/", batch=True)
    shell.onecmd("type t:/system/password", batch=True)

    x = fs.read_text("dd/data/9k")
    assert x.startswith("|")

    l = list(fs.entries_list)
    filenames = [x.filename for x in l if not x.is_empty]
    assert "dd" in filenames
    assert "system" in filenames

    entry = fs.get_file_entry("/test/a")
    assert not entry.inode.is_large

    entry = fs.get_file_entry("/test/b")
    assert not entry.inode.is_large

    entry = fs.get_file_entry("/test/c")
    assert entry.inode.is_large
