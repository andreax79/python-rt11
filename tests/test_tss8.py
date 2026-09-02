from datetime import date

import pytest

from xferx.pdp8.tss8fs import (
    TSS8_BLOCK_SIZE_BYTES,
    TSS8Filesystem,
    date_to_tss8,
    tss8_to_date,
)
from xferx.shell import Shell

DSK = "tests/dsk/tss8.dsk"


def test_tss8_to_date_basic():
    val = ((1980 - 1974) * 372) + ((5 - 1) * 31) + (15 - 1)
    assert tss8_to_date(val) == date(1980, 5, 15)


def test_date_round_trip():
    dates = [date(1974, 1, 1), date(1974, 1, 5), date(1974, 5, 10), date(1980, 6, 15), date(1982, 11, 30)]
    for d in dates:
        encoded = date_to_tss8(d)
        decoded = tss8_to_date(encoded)
        assert decoded == d


def test_tss8():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount t: /tss8 {DSK}.mo", batch=True)
    fs = shell.volumes.get_volume('T')
    assert isinstance(fs, TSS8Filesystem)
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir/brief t:[10,20]", batch=True)
    shell.onecmd("cd t:[11,21]", batch=True)
    shell.onecmd("dir t:", batch=True)
    shell.onecmd("ex t:[12,22]", batch=True)
    bitmap1 = fs.read_bitmap()

    shell.onecmd("create /allocate:5 t:[10,20]test.asc", batch=True)
    entry = fs.get_file_entry("[10,20]test.asc")
    assert entry.length == 5
    l = list(fs.filter_entries_list("[10,20]*.asc"))
    assert len(l) == 4
    bitmap2 = fs.read_bitmap()
    assert bitmap2.used() == bitmap1.used() + 5

    shell.onecmd("create /allocate:10 t:[10,20]test.asc", batch=True)
    bitmap3 = fs.read_bitmap()
    assert bitmap3.used() == bitmap1.used() + 10

    shell.onecmd("create /allocate:5 t:[10,20]test.asc", batch=True)
    bitmap4 = fs.read_bitmap()
    assert bitmap4.used() == bitmap2.used()
    assert bitmap4 == bitmap2

    shell.onecmd("delete t:[10,20]test.asc", batch=True)
    bitmap5 = fs.read_bitmap()
    l = list(fs.filter_entries_list("[10,20]*.asc"))
    assert len(l) == 3
    assert bitmap5.used() == bitmap1.used()
    assert bitmap5 == bitmap1

    shell.onecmd("create/directory t:[5,5]", batch=True)
    bitmap6 = fs.read_bitmap()
    assert bitmap6.used() > bitmap1.used()

    shell.onecmd("create /allocate:5 t:[5,5]test.pal", batch=True)

    shell.onecmd("delete t:[5,5]", batch=True)
    bitmap7 = fs.read_bitmap()
    assert bitmap7.used() == bitmap1.used()
    assert bitmap7 == bitmap1

    x = fs.read_bytes("[11,21]M50.asc")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.filter_entries_list("[11,21]*.asc"))
    assert len(l) == 3


def test_tss8_write_file():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}_1.mo", batch=True)
    shell.onecmd(f"mount t: /tss8 {DSK}_1.mo", batch=True)

    blocks = 5
    filename = "t:[10,20]data.asc"
    data = b""
    for i in range(0, blocks):
        data += chr(i + 65).encode("ascii") * TSS8_BLOCK_SIZE_BYTES
    shell.volumes.write_bytes(filename, data)
    shell.onecmd("dir t:[10,20]", batch=True)

    assert len(data) == blocks * TSS8_BLOCK_SIZE_BYTES
    entry = shell.volumes.get_file_entry(filename)
    assert entry.length == blocks
    print(entry)

    data_read = shell.volumes.read_bytes(filename)
    assert len(data_read) == len(data)
    assert data_read == data

    f = shell.volumes.open_file(filename)
    for i in range(0, blocks):
        block_data = f.read_block(i)
        assert block_data == chr(i + 65).encode("ascii") * TSS8_BLOCK_SIZE_BYTES
    f.close()

    f = shell.volumes.open_file(filename)
    for i in range(0, blocks):
        tmp = chr(i + 85).encode("ascii") * TSS8_BLOCK_SIZE_BYTES
        f.write_block(tmp, i)
    f.close()

    f = shell.volumes.open_file(filename)
    for i in range(0, blocks):
        block_data = f.read_block(i)
        assert block_data == chr(i + 85).encode("ascii") * TSS8_BLOCK_SIZE_BYTES
    f.close()

    f = shell.volumes.open_file(filename)
    for i in range(0, blocks // 2):
        tmp = chr(i + 85).encode("ascii") * TSS8_BLOCK_SIZE_BYTES * 2
        f.write_block(tmp, i * 2, number_of_blocks=2)
    f.close()

    f = shell.volumes.open_file(filename)
    for i in range(0, blocks // 2):
        block_data = f.read_block(i * 2, number_of_blocks=2)
        assert block_data == chr(i + 85).encode("ascii") * TSS8_BLOCK_SIZE_BYTES * 2
    f.close()
    shell.onecmd(f"del {DSK}_1.mo", batch=True)


def test_tss8_init():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /tss8 {DSK}", batch=True)
    shell.onecmd(f"create /allocate:1024 {DSK}_2.mo", batch=True)
    shell.onecmd(f"init /tss8 {DSK}_2.mo", batch=True)
    shell.onecmd(f"mount ou: /tss8 {DSK}_2.mo", batch=True)
    shell.onecmd("create/directory ou:[5,5]", batch=True)
    shell.onecmd("cd ou:[5,5]", batch=True)
    shell.onecmd("copy t:[10,20]*.asc ou:[5,5]", batch=True)
    shell.onecmd("dir ou:[5,5]", batch=True)

    x1 = shell.volumes.read_bytes("ou:[5,5]f5.asc")
    x1 = x1.rstrip(b"\0")
    assert len(x1) == 220
    for i in range(0, 5):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        shell.volumes.read_bytes("ou:[5,5]f5.asc")
    shell.onecmd(f"del sy:{DSK}_2.mo", batch=True)
