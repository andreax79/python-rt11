from datetime import date

import pytest

from xferx.pdp11.dos11fs import (
    DOS11Filesystem,
    UserFileDirectoryBlock,
    date_to_dos11,
    dos11_to_date,
)
from xferx.shell import Shell

DSK = "tests/dsk/dos11_rk05.dsk"


def test_dos11():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /dos11 {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, DOS11Filesystem)

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir /uic t:", batch=True)
    shell.onecmd("type t:1.txt", batch=True)

    x = fs.read_bytes("50.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.filter_entries_list("*.TXT[100,100]"))
    assert len(l) == 3
    assert all([x.contiguous for x in l])

    x1 = fs.read_bytes("[100,100]1000.txt")
    x1 = x1.rstrip(b"\0")
    assert len(x1) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1

    l = list(fs.filter_entries_list("*.TXT[200,200]"))
    assert len(l) == 3
    assert all([not x.contiguous for x in l])

    x2 = fs.read_bytes("[100,100]1000.txt")
    x2 = x2.rstrip(b"\0")
    assert len(x2) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x2
    assert x1 == x2

    l = list(fs.filter_entries_list("*.TXT[1,2]"))
    assert len(l) == 0

    x3 = fs.read_bytes("[200,200]500.txt")
    x3 = x3.rstrip(b"\0")
    assert len(x3) == 22000
    for i in range(0, 500):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x3


def test_dos11_bitmap():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount t: /dos11 {DSK}.mo", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, DOS11Filesystem)

    d = fs.get_file_entry("[200,200]500.TXT")
    assert d is not None
    assert not d.contiguous

    e = fs.get_file_entry("[100,100]200.TXT")
    assert e is not None
    assert e.contiguous

    # Test get_bit
    bitmap = fs.read_bitmap()
    for i in range(e.start_block, e.start_block + e.length):
        assert bitmap.get_bit(i)
    assert not bitmap.get_bit(187)
    assert not bitmap.get_bit(4649)

    # Test find_contiguous_blocks
    assert bitmap.find_contiguous_blocks(10) == 4640
    assert bitmap.find_contiguous_blocks(1000) == 3650
    with pytest.raises(OSError):
        bitmap.find_contiguous_blocks(10000)
    assert bitmap.used() == 337
    d_length = d.length
    e_length = e.length

    # Write UFD
    e.ufd_block.write()
    ufd_block2 = UserFileDirectoryBlock.read(e.ufd_block.fs, e.ufd_block.uic, e.ufd_block.block_number)
    assert str(e.ufd_block) == str(ufd_block2)

    # Delete contiguous file
    e.delete()
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("[100,100]200.TXT")
    bitmap = fs.read_bitmap()
    assert bitmap.used() == 337 - e_length

    # Delete linked file
    d.delete()
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("[200,200]500.TXT")
    bitmap = fs.read_bitmap()
    assert bitmap.used() == 337 - e_length - d_length

    # UIC not found
    with pytest.raises(Exception):
        shell.onecmd("copy /TYPE:CONTIGUOUS t:10.TXT t:[123,321]10NEW.TXT", batch=True)

    # Create a contiguous file
    shell.onecmd("copy /TYPE:CONTIGUOUS t:10.TXT t:[100,100]10NEW.TXT", batch=True)
    x2 = fs.read_bytes("[100,100]10NEW.txt")
    x2 = x2.rstrip(b"\0")
    assert len(x2) == 440
    for i in range(0, 10):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x2

    for i in range(0, 100):
        shell.onecmd(f"copy /TYPE:CONTIGUOUS t:1.TXT t:[100,100]A{i}.TXT", batch=True)

    # Create a non-contiguous file
    shell.onecmd("copy /TYPE:NOCONTIGUOUS t:10.TXT t:[200,200]10NEW.TXT", batch=True)
    x2 = fs.read_bytes("[200,200]10NEW.txt")
    x2 = x2.rstrip(b"\0")
    assert len(x2) == 440
    for i in range(0, 10):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x2


def test_dos11_to_date():
    assert dos11_to_date(0) is None
    assert dos11_to_date(21163) == date(1991, 6, 12)
    assert dos11_to_date(16134) == date(1986, 5, 14)


def test_date_to_dos11():
    assert date_to_dos11(None) == 0
    assert date_to_dos11(date(1991, 6, 12)) == 21163
    assert date_to_dos11(date(1986, 5, 14)) == 16134


def test_date_combined():
    assert dos11_to_date(date_to_dos11(None)) is None
    original_date = date(1980, 5, 28)
    dos11_date = date_to_dos11(original_date)
    converted_date = dos11_to_date(dos11_date)
    assert original_date == converted_date


def test_dos11_create_uic():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount t: /dos11 {DSK}.mo", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, DOS11Filesystem)
    # Delete the UIC
    shell.onecmd("create /directory t:[10,20]", batch=True)
    with pytest.raises(Exception):
        shell.onecmd("create /directory t:[10,20]", batch=True)
    shell.onecmd("create t:[10,20]test /allocate:5", batch=True)
    fs.get_file_entry("[10,20]test")
    shell.onecmd("dir t:[10,20]", batch=True)
    # Delete the UIC
    shell.onecmd("delete t:[10,20]", batch=True)
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("[10,20]test")
    with pytest.raises(Exception):
        shell.onecmd("delete t:[10,20]", batch=True)
