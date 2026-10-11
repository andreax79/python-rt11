import pytest

from xferx.pdp11.files11fs import HOME_BLOCK, INDEXF_SYS, Files11File, Files11Filesystem, files11_split_fullname
from xferx.shell import Shell

DSK = "tests/dsk/files11.dsk"


@pytest.mark.parametrize(
    ("fullname", "default_path", "wildcard", "ods_version", "expected"),
    [
        # None input
        (None, "DEFAULT", False, 2, ("DEFAULT", None, None)),
        # Filename without a directory
        ("FILE.TXT", "DEFAULT", False, 2, ("DEFAULT", "FILE.TXT", None)),
        ("FILE.TXT;1", "DEFAULT", False, 2, ("DEFAULT", "FILE.TXT", 1)),
        ("FILE.TXT;42", "DEFAULT", False, 2, ("DEFAULT", "FILE.TXT", 42)),
        # Two-level directory hierarchy
        ("[1,2]FILE.TXT", "DEFAULT", False, 2, ("001002", "FILE.TXT", None)),
        ("[10,20]FILE.TXT;3", "DEFAULT", False, 2, ("010020", "FILE.TXT", 3)),
        ("[777,777]FILE.TXT;1", "DEFAULT", False, 2, ("777777", "FILE.TXT", 1)),
        ("[ 1 , 2 ]FILE.TXT", "DEFAULT", False, 2, ("001002", "FILE.TXT", None)),
        ("[0,0]FILE.TXT", "DEFAULT", False, 2, ("000000", "FILE.TXT", None)),
        # Multi-level directory hierarchy
        ("[DISK.DIR]FILE.TXT", "DEFAULT", False, 2, ("DISK.DIR", "FILE.TXT", None)),
        (
            "[ROOT.SUBDIR.LEAF]FILE.TXT;5",
            "DEFAULT",
            False,
            2,
            ("ROOT.SUBDIR.LEAF", "FILE.TXT", 5),
        ),
        ("[disk.subdir]FILE.TXT", "DEFAULT", False, 2, ("DISK.SUBDIR", "FILE.TXT", None)),
        # Wildcard handling
        ("", "DEFAULT", True, 2, ("DEFAULT", "*.*", None)),
        ("", "DEFAULT", True, 5, ("DEFAULT", "*.*", None)),
    ],
)
def test_files11_split_fullname(
    fullname,
    default_path,
    wildcard,
    ods_version,
    expected,
):
    assert (
        files11_split_fullname(
            fullname=fullname,
            default_path=default_path,
            wildcard=wildcard,
            ods_version=ods_version,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("fullname", "expected_message"),
    [
        ("[1,2,3]FILE.TXT", "Invalid two-level directory path"),
        ("[8,2]FILE.TXT", "Invalid octal directory path"),
        ("[-1,2]FILE.TXT", "Directory components must fit in three octal digits"),
        ("[1000,2]FILE.TXT", "Directory components must fit in three octal digits"),
        ("[1,1000]FILE.TXT", "Directory components must fit in three octal digits"),
    ],
)
def test_files11_split_fullname_invalid_directory(
    fullname,
    expected_message,
):
    with pytest.raises(ValueError, match=expected_message):
        files11_split_fullname(
            fullname=fullname,
            default_path="DEFAULT",
            wildcard=False,
            ods_version=2,
        )


def test_files11_split_fullname():
    # The regular expression does not recognize a non-numeric version suffix.
    # The suffix remains part of the filename.
    path, filename, version = files11_split_fullname(
        fullname="FILE.TXT;ABC",
        default_path="DEFAULT",
        wildcard=False,
        ods_version=2,
    )
    assert path == "DEFAULT"
    assert version is None
    assert filename is not None

    path, filename, version = files11_split_fullname(
        fullname="README",
        default_path="DEFAULT",
        wildcard=False,
        ods_version=2,
    )
    assert path == "DEFAULT"
    assert filename is not None
    assert version is None

    path, filename, version = files11_split_fullname(
        fullname="[1,2]",
        default_path="DEFAULT",
        wildcard=True,
        ods_version=2,
    )

    assert path == "001002"
    assert filename == "*.*"
    assert version is None


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
    # print(x)

    e = fs.get_file_entry("[1,1]hello.txt;1")
    assert e is not None
    assert e.version == 1
    e = fs.get_file_entry("[1,1]hello.txt;2")
    assert e is not None
    assert e.version == 2
    e = fs.get_file_entry("[1,1]hello.txt;3")
    assert e is not None
    assert e.version == 3
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("[1,1]hello.txt;4")
    e = fs.get_file_entry("[1,1]hello.txt")
    assert e is not None
    assert e.version == 3


def test_files11_ods2():
    image = "tests/dsk/files11_ods2.rx01"
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /files11 {image}", batch=True)
    fs = shell.volumes.get_volume("T")

    assert fs.ods_version == 2
    assert fs.vlev == 0x0201
    root = fs.get_root()
    entries = {entry.name: entry for entry in root.iterdir()}
    assert "INDEXF.SYS" in entries
    assert entries["INDEXF.SYS"].is_file()
    assert entries["INDEXF.SYS"].creation_date is not None
    # indirect_entry = next(entry for entry in entries.values() if entry.fnum > 16)
    # assert indirect_entry.header.fnum == indirect_entry.fnum
    directory = next(entry for entry in entries.values() if entry.is_dir() and not entry.is_mount())
    children = list(directory.iterdir())
    assert children
    data_file = next(entry for entry in children if not entry.is_dir())
    assert fs.get_file_entry(data_file.fullname).fnum == data_file.fnum
    with data_file.open() as file:
        assert file.read_block(0)
    with entries["INDEXF.SYS"].open() as file:
        assert file.read_block(0) == fs.read_block(0)
