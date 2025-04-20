import random
from datetime import datetime, timedelta

import pytest

from xferx.nova.dgdosfs import (
    START_DATE,
    DGDOSFilesystem,
    date_to_rdos,
    filename_hash,
    rdos_to_date,
    swap_bytes,
)
from xferx.shell import Shell

DSK = "tests/dsk/rdos.dsk"


def test_rdos_to_date_basic():
    dt = rdos_to_date(1, 0)
    expected = START_DATE + timedelta(days=1)
    assert dt == expected

    dt = rdos_to_date(2, (3 << 8) + 15)  # 3 hours, 15 minutes
    expected = START_DATE + timedelta(days=2, hours=3, minutes=15)
    assert dt == expected

    dt = rdos_to_date(0, 0)
    assert dt is None


def test_date_to_rdos():
    dt = START_DATE + timedelta(days=5)
    days, time = date_to_rdos(dt)
    assert days == 5
    assert time == 0

    dt = START_DATE + timedelta(days=10, hours=4, minutes=20)
    days, time = date_to_rdos(dt)
    assert days == 10
    assert time == (4 << 8) + 20

    days, time = date_to_rdos(None)
    assert days == 0
    assert time == 0


def test_swap_bytes():
    original = b'\x01\x02\x03\x04'
    expected = b'\x02\x01\x04\x03'
    assert swap_bytes(original) == expected

    original = b'\x01\x02\x03'
    expected = b'\x02\x01\x03'
    assert swap_bytes(original) == expected

    original = b''
    expected = b''
    assert swap_bytes(original) == expected

    original = b'\x01'
    expected = b'\x01'
    assert swap_bytes(original) == expected

    original = b'\x00\x01\x00\x02\x00\x03\x00\x04'
    expected = b'\x01\x00\x02\x00\x03\x00\x04\x00'
    assert swap_bytes(original) == expected


def test_filename_hash():
    assert filename_hash("SYS", "DR", 0xFFFF) == 60075
    assert filename_hash("bootsys", "sv", 0xFFFF) == 35667
    assert filename_hash("MAP", "DR", 0xFFFF) == 57747


def test_date_round_trip():
    start_year = 1968
    end_year = 1999
    for i in range(0, 100):
        start_date = datetime(start_year, 1, 1)
        end_date = datetime(end_year, 12, 31, 23, 59, 59)
        random_seconds = random.randint(0, int((end_date - start_date).total_seconds()))
        random_seconds = random_seconds - (random_seconds % 60)
        dt = start_date + timedelta(seconds=random_seconds)
        (d1, t1) = date_to_rdos(dt)
        assert rdos_to_date(d1, t1) == dt


def test_bitmap():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /dgdos {DSK}", batch=True)
    fs = shell.volumes.get('T')
    bitmap = fs.read_bitmap()
    for b in fs.read_dir_entries():
        if not b.is_directory:
            for x in b.blocks():
                assert not bitmap.is_free(x)


def test_rdos():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /dgdos {DSK}.mo", batch=True)
    fs = shell.volumes.get('OU')
    assert isinstance(fs, DGDOSFilesystem)

    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("dir/brief ou:", batch=True)
    # with pytest.raises(FileNotFoundError):
    #     shell.onecmd("dir/brief ou:/notfound", batch=True)
    shell.onecmd("type ou:com.cm", batch=True)

    d0 = fs.read_bytes("com.cm")
    shell.onecmd("copy ou:com.cm ou:com1.cm", batch=True)
    d1 = fs.read_bytes("com.cm")
    assert d0 == d1

    free = fs.read_bitmap().free()

    name = "SECONDPART.DR"
    entry = fs.get_file_entry(name)
    length = entry.get_length()
    assert entry.is_contiguous
    assert len(list(entry.blocks())) == length
    assert len(list(entry.blocks(include_indexes=True))) == length
    entry.delete()

    with pytest.raises(FileNotFoundError):
        fs.get_file_entry(name)

    assert free + length == fs.read_bitmap().free()
