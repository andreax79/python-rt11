from xferx.shell import Shell
from xferx.unix.unix6fs import UNIXFilesystem

DSK = "tests/dsk/unixv6.dsk"


def test_unix6_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /unix6 {DSK}", batch=True)
    fs = shell.volumes.get_volume('T')
    assert isinstance(fs, UNIXFilesystem)
    assert fs.version == 6
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir t:/", batch=True)
    shell.onecmd("dir t:/etc/", batch=True)
    shell.onecmd("type t:/etc/passwd", batch=True)

    x = fs.read_text("1")
    assert x.startswith("1\n")

    l = list(fs.entries_list)
    filenames = [x.filename for x in l if not x.is_empty()]
    assert "1140k" in filenames

    entry = fs.get_file_entry("/1140k")
    assert entry.inode.is_large()
    assert entry.inode.is_huge()
    assert entry.inode.addr[-1] != 0

    entry = fs.get_file_entry("/95k")
    assert entry.inode.is_large()
    assert not entry.inode.is_huge()
    assert entry.inode.addr[-1] == 0

    entry = fs.get_file_entry("/9k")
    assert entry.inode.is_large()
    assert not entry.inode.is_huge()
    assert entry.inode.addr[-1] == 0

    entry = fs.get_file_entry("/etc/passwd")
    assert not entry.inode.is_large()
    assert not entry.inode.is_huge()
    assert entry.inode.addr[-1] == 0

    metadata = entry.metadata
    print(metadata)
    # assert metadata['prodos_storage_type'] == 0x5
    # assert metadata['prodos_file_type'] == 0x34
    # assert metadata['aux_type'] == 0x5678
    # assert isinstance(metadata['creation_date'], datetime)
