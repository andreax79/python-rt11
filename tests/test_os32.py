from datetime import date
from pathlib import Path

import pytest

from xferx.interdata.os32fs import (
    OS32Filesystem,
    OS32Path,
    date_to_os32,
    os32_to_date,
    CO_FILE_TYPE,
    records_to_ascii,
    ascii_to_records,
)
from xferx.shell import Shell

DSK = "tests/dsk/os32.dsk"


def test_records_to_ascii():
    # Basic test for records_to_ascii function
    data = b"HELLO\x00\x00\x00WORLD\x00\x00\x00"
    assert records_to_ascii(data, 8, 2) == b"HELLO\nWORLD\n"

    data = b"ABC\rDEF\x00XYZ\x00\x00\x00\x00\x00"
    assert records_to_ascii(data, 8, 2) == b"ABC\nXYZ\n"

    data = b"ABC\x00DEF\x00XYZ12345"
    assert records_to_ascii(data, 8, 2) == b"ABC\nXYZ12345\n"

    data = b"ABC\rDEF\x00"
    assert records_to_ascii(data, 8, 1) == b"ABC\n"

    data = b"\x00" * 16
    assert records_to_ascii(data, 8, 2) == b"\n\n"

    assert records_to_ascii(b"ignored", 8, 0) == b""


def test_ascii_to_records():
    assert ascii_to_records(b"HELLO\nWORLD", 8) == (
        (b"HELLO\x00\x00\x00WORLD\x00\x00\x00"),
        2,
    )

    assert len(ascii_to_records(b"HELLO", 8)[0]) == 8
    assert ascii_to_records(b"HELLO", 8) == (b"HELLO\x00\x00\x00", 1)

    assert ascii_to_records(b"HELLO\r\nWORLD\r", 8) == (
        b"HELLO\x00\x00\x00WORLD\x00\x00\x00",
        2,
    )

    assert ascii_to_records(b"12345678", 8) == (b"12345678", 1)

    assert ascii_to_records(b"", 8) == (b"\x00" * 8, 1)

    with pytest.raises(ValueError, match="Line too long"):
        ascii_to_records(b"123456789", 8)

    data = bytearray(b"ABC")
    assert ascii_to_records(data, 5) == (b"ABC\x00\x00", 1)


def test_round_trip():
    ascii_data = b"HELLO\nWORLD"
    records = ascii_to_records(ascii_data, 8)[0]
    assert records_to_ascii(records, 8, 2) == b"HELLO\nWORLD\n"

    ascii_data = b"ONE\n\nTHREE"
    records = ascii_to_records(ascii_data, 8)[0]
    assert records_to_ascii(records, 8, 3) == b"ONE\n\nTHREE\n"

    records = b"ABC\x00\x00\x00\x00\x00DEF\x00\x00\x00\x00\x00"
    ascii_data = records_to_ascii(records, 8, 2)
    assert ascii_data == b"ABC\nDEF\n"
    assert ascii_to_records(ascii_data.rstrip(b"\n"), 8)[0] == records

    text = Path("tests/dsk/data/1000.txt").read_bytes()
    c0 = len(text)
    l0 = len(text.splitlines())
    records, nr0 = ascii_to_records(text, 80)
    text1 = records_to_ascii(records, 80, nr0)
    c1 = len(text1)
    l1 = len(text1.splitlines())
    assert c0 == c1
    assert l0 == l1


def test_os32_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /os32 {DSK}", batch=True)
    fs = shell.volumes.get_volume('T')
    assert isinstance(fs, OS32Filesystem)

    shell.onecmd("dir t:/0", batch=True)
    shell.onecmd("dir /uic t:", batch=True)
    shell.onecmd("type t:d1.txt/0", batch=True)

    x = fs.read_text("d1000.txt/0")
    assert len(x) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" in x

    shell.onecmd("cd t:/0", batch=True)
    l = list(fs.entries_list)
    assert len(l) == 11


def test_os32_write():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount in: /os32 {DSK}", batch=True)
    shell.onecmd(f"mount ou: /os32 {DSK}.mo", batch=True)
    fs = shell.volumes.get_volume('OU')
    assert isinstance(fs, OS32Filesystem)
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    d = fs.get_file_entry("d500.TXT/0")
    for k in fs.fs_entry_metadata:
        assert d.metadata[k] is not None

    # Delete a file
    d.delete()
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("d500.TXT/0")

    # Create a file
    shell.onecmd("copy /ascii in:D10.TXT/0 ou:D10NEW.TXT/0", batch=True)
    x1 = fs.read_text("D10.txt/0")
    assert len(x1) == 440
    x2 = fs.read_text("D10NEW.txt/0")
    assert len(x2) == 440
    for i in range(0, 10):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" in x2


def test_os32_init():
    shell = Shell(verbose=True)
    # shell.onecmd(f"mount in: /os32 {DSK}", batch=True)
    tmp_dsk = "tests/dsk/tmp_os32.dsk.mo"
    shell.onecmd(f"create /allocate:2400 {tmp_dsk}", batch=True)
    shell.onecmd(f"init /os32 {tmp_dsk}", batch=True)
    shell.onecmd(f"mount ou: /os32 {tmp_dsk}", batch=True)
    shell.onecmd("dir ou:", batch=True)
    # shell.onecmd("copy in:* ou:", batch=True)
    fs = shell.volumes.get_volume("OU")
    assert isinstance(fs, OS32Filesystem)

    # Create some empty files
    free = fs.read_bitmap().free()
    for i in range(0, 12):
        segment, pos = fs.get_free_directory_entry()
        segment.entries_list[pos] = OS32Path.create(
            fs=fs,
            fullname=f"TMP{i:02}.TMP/0",
            size=0,
            record_length=80,
            file_type=CO_FILE_TYPE,
        )
        segment.write()
    free1 = fs.read_bitmap().free()
    assert free1 < free

    shell.onecmd("dir ou:/0", batch=True)
    shell.onecmd("cd ou:/0", batch=True)
    assert len(list(fs.entries_list)) == 12

    for i in range(12, 20):
        entry = fs.create_file(fullname=f"TMP{i:02}.TMP/0", size=1024, metadata={"file_type": "CO"})
    free2 = fs.read_bitmap().free()
    assert free2 < free1

    shell.onecmd("dir ou:/0", batch=True)
    assert len(list(fs.entries_list)) == 20

    for i in range(12, 20):
        entry = fs.get_file_entry(fullname=f"TMP{i:02}.TMP/0")
        assert entry is not None
        entry.delete()

    shell.onecmd("dir ou:/0", batch=True)
    shell.onecmd("cd ou:/0", batch=True)
    assert len(list(fs.entries_list)) == 12
