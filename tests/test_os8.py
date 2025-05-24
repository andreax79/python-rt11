import random
import string
from datetime import date

import pytest

from xferx.pdp8.os8fs import (
    OS8_BLOCK_SIZE_BYTES,
    OS8DirectoryEntry,
    OS8Filesystem,
    OS8Segment,
    asc_to_rad50_word12,
    date_to_os8,
    from_12bit_words_to_bytes,
    from_bytes_to_12bit_words,
    os8_to_date,
    rad50_word12_to_asc,
)
from xferx.rx import (
    RX01_SECTOR_SIZE,
    RX01_SIZE,
    RX02_SECTOR_SIZE,
    rx_extract_12bit_words,
    rx_pack_12bit_words,
)
from xferx.shell import Shell

DSK = "tests/dsk/os8.rx01"


def test_rad50_word12():
    for t in ["", "A", "AB", "AC", "AA", "??"]:
        assert rad50_word12_to_asc(asc_to_rad50_word12(t)) == t


def test_write_directory_entry():
    words = [0, 0]
    segment = OS8Segment(None)
    segment.extra_words = 1

    e = OS8DirectoryEntry.read(segment, words, 0, 0)
    segment.entries_list.append(e)
    assert segment.number_of_entries == 1
    assert e.is_empty
    words1 = e.to_words()
    assert words == words1

    assert len(words1) == 2
    e.empty_entry = False
    e.filename = "TEST"
    e.extension = "TX"
    e.length = 123
    e.extra_words = [342]
    assert not e.is_empty
    words2 = e.to_words()
    assert len(words2) == 6

    e2 = OS8DirectoryEntry.read(segment, words2, 0, 0)
    assert not e2.is_empty
    assert e2.to_words() == words2


def test_os8_to_date():
    assert os8_to_date(1039) == date(1977, 4, 1)
    assert os8_to_date(5084) == date(1974, 3, 27)
    assert os8_to_date(0) is None
    assert os8_to_date(0x7FFF) is None


def test_date_to_os8():
    assert date_to_os8(None) == 0
    assert date_to_os8(date(1970, 2, 1)) == 520
    assert date_to_os8(date(1973, 11, 7)) == 2875
    assert date_to_os8(date(1970, 1, 1)) == 264
    assert date_to_os8(date(2001, 1, 1)) == 271


def test_date_round_trip():
    for val in [0, 2017, 1734, 3256]:
        assert date_to_os8(os8_to_date(val)) == val
    for d in [None, date(1970, 1, 1), date(1973, 11, 7), date(1975, 6, 15)]:
        assert os8_to_date(date_to_os8(d)) == d


def test_12bit_words_ascii():
    def random_string(l):
        return "".join(
            random.choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(l)
        ).encode("ascii")

    a = random_string(11)
    b = from_bytes_to_12bit_words(a, "ASCII")
    a2 = from_12bit_words_to_bytes(b, "ASCII")
    assert a == a2.rstrip(b"\x00")

    a = b"*TEST*"
    b = from_bytes_to_12bit_words(a, "ASCII")
    a2 = from_12bit_words_to_bytes(b, "ASCII")
    assert a == a2


def test_rx_extract_and_pack_12bit_words():
    # Test data for RX01 (64 12-bit words)
    words = [i & 0xFFF for i in range(64)]
    sector_size = RX01_SECTOR_SIZE
    byte_data = rx_pack_12bit_words(words, 0, sector_size)
    extracted_words = rx_extract_12bit_words(byte_data, 0, sector_size)
    assert words == extracted_words
    # Test data for RX02 (128 12-bit words)
    words = [i & 0xFFF for i in range(128)]
    sector_size = RX02_SECTOR_SIZE
    byte_data = rx_pack_12bit_words(words, 0, sector_size)
    extracted_words = rx_extract_12bit_words(byte_data, 0, sector_size)
    assert words == extracted_words


def test_rx_pack_invalid_sector_size():
    words = [i & 0xFFF for i in range(64)]
    with pytest.raises(ValueError, match="Invalid sector size"):
        rx_pack_12bit_words(words, 0, 999)


def test_rx_pack_invalid_word_count():
    words = [i & 0xFFF for i in range(63)]
    with pytest.raises(ValueError, match="Expected 64 words"):
        rx_pack_12bit_words(words, 0, RX01_SECTOR_SIZE)
    words = [i & 0xFFF for i in range(127)]
    with pytest.raises(ValueError, match="Expected 128 words"):
        rx_pack_12bit_words(words, 0, RX02_SECTOR_SIZE)


def test_os8_write_rx01():
    shell = Shell(verbose=True)
    diskname = "tests/dsk/os8.rx01.mo"
    with open(diskname, "wb") as f:
        f.truncate(RX01_SIZE)
    shell.onecmd(f"init /os8 {diskname}", batch=True)
    shell.onecmd(f"mount ou: /os8 {diskname}", batch=True)
    shell.onecmd("dir ou:", batch=True)
    fs = shell.volumes.get('OU')
    assert isinstance(fs, OS8Filesystem)
    assert fs.num_of_partitions == 1
    vol0 = fs.get_partition(0)
    assert vol0.free() == 487
    fs.create_file(fullname="TEST.TX", length=100, file_type="ascii")
    assert vol0.free() == 387
    for l in [100, 378, 512, 888]:
        t = "x" * l
        fs.write_bytes("X.TX", t.encode("ascii"))
        t1 = fs.read_bytes("X.TX", file_mode="ascii").decode("ascii").rstrip("\x00")
        print(t, len(t))
        print(t1, len(t1))
        assert t == t1


def test_os8():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /os8 {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, OS8Filesystem)

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir /uic t:", batch=True)
    shell.onecmd("type t:1.tx", batch=True)

    x = fs.read_bytes("50.tx")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.filter_entries_list("*.TX[0]"))
    assert len(l) == 6


def test_os8_init():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /os8 {DSK}", batch=True)
    shell.onecmd(f"create /allocate:280 {DSK}.mo", batch=True)
    shell.onecmd(f"init /os8 {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /os8 {DSK}.mo", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("copy t:*.TX ou:", batch=True)
    fs = shell.volumes.get('OU')

    x1 = fs.read_bytes("[0]50.tx")
    x1 = x1.rstrip(b"\0")
    assert len(x1) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        print(fs.read_bytes("[0]50.tx"))


def test_os8_write_file():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount t: /os8 {DSK}.mo", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, OS8Filesystem)

    blocks = 5
    filename = "data.tx"
    data = b""
    for i in range(0, blocks):
        data += chr(i + 65).encode("ascii") * OS8_BLOCK_SIZE_BYTES
    fs.write_bytes(filename, data)

    data_read = fs.read_bytes(filename)
    assert data_read == data

    f = fs.open_file(filename)
    for i in range(0, blocks):
        block_data = f.read_block(i)
        assert block_data == chr(i + 65).encode("ascii") * OS8_BLOCK_SIZE_BYTES
    f.close()

    f = fs.open_file(filename)
    for i in range(0, blocks):
        tmp = chr(i + 85).encode("ascii") * OS8_BLOCK_SIZE_BYTES
        f.write_block(tmp, i)
    f.close()

    f = fs.open_file(filename)
    for i in range(0, blocks):
        block_data = f.read_block(i)
        assert block_data == chr(i + 85).encode("ascii") * OS8_BLOCK_SIZE_BYTES
    f.close()

    f = fs.open_file(filename)
    for i in range(0, blocks // 2):
        tmp = chr(i + 85).encode("ascii") * OS8_BLOCK_SIZE_BYTES * 2
        f.write_block(tmp, i * 2, number_of_blocks=2)
    f.close()

    f = fs.open_file(filename)
    for i in range(0, blocks // 2):
        block_data = f.read_block(i * 2, number_of_blocks=2)
        assert block_data == chr(i + 85).encode("ascii") * OS8_BLOCK_SIZE_BYTES * 2
    f.close()
