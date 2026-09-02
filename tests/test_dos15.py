import copy
import errno
from datetime import date
from types import SimpleNamespace

import pytest

from xferx.commons import ASCII, IMAGE
from xferx.device.block_18bit import (
    from_18bit_words_to_bytes,
    from_bytes_to_18bit_words,
)
from xferx.pdp15.dos15fs import (
    WORDS_PER_LINKED_BLOCK,
    DOS15Path,
    DOS15Filesystem,
    RetrievalInformationBlock,
    date_to_dos15,
    dos15_to_date,
)
from xferx.pdp15.adssfs import ascii_to_sixbit
from xferx.pdp15.iops import is_six_bit, encode_block_format, decode_block_format
from xferx.shell import Shell

DSK = "tests/dsk/dos15.dsk"


def test_dos15_to_date():
    assert dos15_to_date(0) is None
    encoded = (9 << 12) | (24 << 6) | (1971 - 1970)
    assert dos15_to_date(encoded) == date(1971, 9, 24)
    assert dos15_to_date((13 << 12) | (1 << 6) | 1) is None


def test_date_to_dos15():
    assert date_to_dos15(None) == 0
    assert date_to_dos15(date(1971, 9, 24)) == (9 << 12) | (24 << 6) | 1
    for value in (date(1970, 1, 1), date(1971, 9, 24), date(2001, 12, 31)):
        assert dos15_to_date(date_to_dos15(value)) == value


def test_dos15_directory_entry_to_words():
    original_words = [
        ascii_to_sixbit("ABC"),
        ascii_to_sixbit("DEF"),
        ascii_to_sixbit("BIN"),
        (1 << 17) | 0o123456,
        (3 << 16) | 0o234,
        0o345,
        (5 << 16) | 0o456,
        date_to_dos15(date(1971, 9, 24)),
    ]
    entry = DOS15Path.read(SimpleNamespace(fs=None), original_words, 7, 0)

    assert entry.to_words() == original_words

    entry.is_truncated = False
    entry.block_number = 0o654321
    entry._u4 = 1
    entry.length = 0o765
    entry.rib = 0o123
    entry.protection_code = 2
    entry.rib_position = 0o777
    entry.raw_creation_date = date_to_dos15(date(2001, 12, 31))
    expected_words = [
        ascii_to_sixbit("ABC"),
        ascii_to_sixbit("DEF"),
        ascii_to_sixbit("BIN"),
        0o654321,
        (1 << 16) | 0o765,
        0o123,
        (2 << 16) | 0o777,
        date_to_dos15(date(2001, 12, 31)),
    ]
    assert entry.to_words() == expected_words
    round_trip = DOS15Path.read(SimpleNamespace(fs=None), entry.to_words(), 7, 0)
    assert round_trip.to_words() == expected_words


def test_dos15_rib_zero_references_is_bounded():
    words = [0] * 256
    words[0] = 123
    words[5:9] = [3, 11, 12, 13]
    words[-2:] = [0o44, 0o55]

    class MockFilesystem:
        def read_words_block(self, block_number):
            assert block_number == 20
            return words

        def write_words_block(self, block_number, new_words):
            assert block_number == 20
            words[:] = new_words

    entry = SimpleNamespace(rib=20, rib_position=5, fs=MockFilesystem())
    entry.length = 2
    rib = RetrievalInformationBlock.read(entry, 20)
    rib.write()

    assert words[0] == 123
    assert words[5:9] == [3, 11, 12, 13]
    assert words[-2:] == [0o44, 0o55]

    rib.zero_references(entry.rib_position, entry.length)
    rib.write()

    assert words[0] == 123
    assert words[5:9] == [3, 0, 0, 13]
    assert words[-2:] == [0o44, 0o55]


def test_dos15():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /dos15 {DSK}", batch=True)
    fs = shell.volumes.get_volume("T")
    assert isinstance(fs, DOS15Filesystem)
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    assert fs.uic == None
    shell.onecmd("cd t:[ABC]", batch=True)
    assert fs.uic == "ABC"

    shell.onecmd("dir t:", batch=True)
    entries = list(fs.entries_list)
    assert all(
        DOS15Path.read(entry.ufd, entry.to_words(), entry.file_number, 0).to_words() == entry.to_words()
        for entry in entries
    )
    entry_lengths = {entry.name: entry.length for entry in entries}
    assert entry_lengths["1;BIN"] == 1
    assert entry_lengths["10;BIN"] == 1
    assert entry_lengths["100;BIN"] == 1
    assert entry_lengths["1000;BIN"] == 5
    assert entry_lengths["1000;SRC"] == 84
    assert fs.get_file_entry("1;BIN").creation_date == date(1994, 8, 29)
    tmp = fs.read_text("[ABC]1000;SRC")
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" in tmp
    assert len(list(fs.filter_entries_list("*;BIN"))) >= 4


def test_dos15_sat_write(tmp_path):
    disk = tmp_path / "dos15.dsk"
    shell = Shell(verbose=False)
    shell.onecmd(f"copy {DSK} {disk}", batch=True)
    shell.onecmd(f"mount t: /dos15 {disk}", batch=True)
    fs = shell.volumes.get_volume("T")
    bitmap = fs.read_bitmap()
    free_block = next(block for block in range(bitmap.total_blocks) if bitmap.is_free(block))

    assert bitmap.allocate(1) == [free_block]
    bitmap.write()
    assert not fs.read_bitmap().is_free(free_block)

    bitmap = fs.read_bitmap()
    bitmap.set_free(free_block)
    bitmap.write()

    shell.onecmd(f"mount t: /dos15 {disk}", batch=True)
    fs = shell.volumes.get_volume("T")
    assert fs.read_bitmap().is_free(free_block)


def test_dos15_delete_file(tmp_path):
    disk = tmp_path / "dos15.dsk"
    shell = Shell(verbose=False)
    shell.onecmd(f"copy {DSK} {disk}", batch=True)
    shell.onecmd(f"mount t: /dos15 {disk}", batch=True)
    fs = shell.volumes.get_volume("T")
    shell.volumes.chdir("T:[ABC]")
    entry = fs.get_file_entry("[ABC]1000;BIN")
    initial_entry_count = len(list(fs.entries_list))
    length = entry.length
    data_blocks = entry.get_blocks()
    assert len(data_blocks) == length
    rib = RetrievalInformationBlock.read(entry, entry.rib)
    rib_position = entry.rib_position

    assert entry.delete()
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("[ABC]1000;BIN")
    assert len(list(fs.entries_list)) == initial_entry_count - 1
    bitmap = fs.read_bitmap()
    for block_number in data_blocks:
        if block_number != rib.block_number:
            assert bitmap.is_free(block_number)

    rib_words = fs.read_words_block(rib.block_number)
    assert rib_words[rib_position + 1 : rib_position + 1 + length] == [0] * length

    shell.onecmd("sy:", batch=True)
    shell.onecmd("dismount t:", batch=True)
    shell.onecmd(f"mount t: /dos15 {disk}", batch=True)
    fs = shell.volumes.get_volume("T")
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("[ABC]1000;BIN")
    shell.volumes.chdir("T:[ABC]")
    assert len(list(fs.entries_list)) == initial_entry_count - 1
    assert all(
        fs.read_bitmap().is_free(block_number) for block_number in data_blocks if block_number != rib.block_number
    )


def test_dos15_ufd_write(tmp_path):
    disk = tmp_path / "dos15.dsk"
    shell = Shell(verbose=False)
    shell.onecmd(f"copy {DSK} {disk}", batch=True)
    shell.onecmd(f"mount t: /dos15 {disk}", batch=True)
    fs = shell.volumes.get_volume("T")
    ufd = next(fs.get_root().iterdir())
    original_entries = list(ufd.iterdir(include_all=True))

    original_entries[0].protection_code = 3
    ufd.write(original_entries)
    rewritten_ufd = next(fs.get_root().iterdir())
    rewritten_entries = list(rewritten_ufd.iterdir(include_all=True))
    assert len(list(rewritten_ufd.read_ufd_blocks())) == 1
    assert rewritten_entries[0].protection_code == 3
    assert rewritten_entries[0].to_words() == original_entries[0].to_words()
    rewritten_block = next(rewritten_ufd.read_ufd_blocks())
    assert rewritten_block.previous_block_number == 0o777777
    assert rewritten_block.next_block_number == 0o777777


def test_dos15_ufd_write_grows_and_links_blocks(tmp_path):
    disk = tmp_path / "dos15.dsk"
    shell = Shell(verbose=False)
    shell.onecmd(f"copy {DSK} {disk}", batch=True)
    shell.onecmd(f"mount t: /dos15 {disk}", batch=True)
    fs = shell.volumes.get_volume("T")
    ufd = next(fs.get_root().iterdir())
    entries = list(ufd.iterdir(include_all=True))
    for file_number in range(len(entries), 32):
        extra_entry = copy.copy(entries[0])
        extra_entry.stem = f"X{file_number:05d}"
        entries.append(extra_entry)

    ufd.write(entries)
    blocks = list(ufd.read_ufd_blocks())
    assert len(blocks) == 2
    assert blocks[0].next_block_number == blocks[1].block_number
    assert blocks[1].previous_block_number == blocks[0].block_number
    assert blocks[1].next_block_number == 0o777777
    assert [entry.name for entry in ufd.iterdir(include_all=True)][-1] == "X00031;BIN"
    assert not fs.read_bitmap().is_free(blocks[1].block_number)

    shell.onecmd(f"mount t: /dos15 {disk}", batch=True)
    fs = shell.volumes.get_volume("T")
    ufd = next(fs.get_root().iterdir())
    assert len(list(ufd.iterdir(include_all=True))) == 32
    blocks = list(ufd.read_ufd_blocks())
    assert len(blocks) == 2
    assert blocks[0].next_block_number == blocks[1].block_number
    assert blocks[1].previous_block_number == blocks[0].block_number


def test_dos15_create_file(tmp_path):
    disk = tmp_path / "dos15.dsk"
    shell = Shell(verbose=False)
    shell.onecmd(f"copy {DSK} {disk}", batch=True)
    shell.onecmd(f"mount t: /dos15 {disk}", batch=True)
    fs = shell.volumes.get_volume("T")
    bitmap_before = fs.read_bitmap()

    for l in (10, 100, 1000):
        words = list(range(0, l))
        data = from_18bit_words_to_bytes(words, IMAGE)
        blocks_content = list(encode_block_format(data, IMAGE, words_per_block=WORDS_PER_LINKED_BLOCK))
        data1 = b"".join([decode_block_format(block) for block in blocks_content])
        assert data1 == data, "data1 != data"

        filename = f"[ABC]T{l};BIN"
        fs.write_bytes(filename, data, file_mode=IMAGE)
        with fs.get_file_entry(filename).open(file_mode=IMAGE) as f:
            t = f.read_words_block(0)
            data2 = decode_block_format(t)
            assert data2 == data[: len(data2)], "data2 != data"
        data_read = fs.read_bytes(filename, file_mode=IMAGE)
        assert data_read == data, "data_read != data"

    content = from_18bit_words_to_bytes([index % 1 << 18 for index in range(1024)], IMAGE)
    fs.write_bytes(
        "[ABC]NEW01;BIN", content, metadata={"creation_date": date(1980, 5, 15), "protection_code": 3}, file_mode=IMAGE
    )
    entry = fs.get_file_entry("[ABC]NEW01;BIN")
    assert entry.length == 5
    assert len(entry.get_blocks()) == 5
    assert entry.creation_date == date(1980, 5, 15)
    assert entry.protection_code == 3
    assert entry.read_bytes()[: len(content)] == content

    shell.onecmd(f"mount t: /dos15 {disk}", batch=True)
    fs = shell.volumes.get_volume("T")
    entry = fs.get_file_entry("[ABC]NEW01;BIN")
    assert entry.read_bytes()[: len(content)] == content
    assert len(entry.get_blocks()) == 5


def test_dos15_create_file_ascii(tmp_path):
    disk = tmp_path / "dos15.dsk"
    shell = Shell(verbose=False)
    shell.onecmd(f"copy {DSK} {disk}", batch=True)
    shell.onecmd(f"mount t: /dos15 {disk}", batch=True)
    fs = shell.volumes.get_volume("T")

    fs.write_bytes("[ABC]TEXT;BIN", b"line one\nline two\n", file_mode="ASCII")
    text = fs.get_file_entry("[ABC]TEXT;BIN").read_bytes(file_mode="ASCII")
    assert b"line one" in text
    assert b"line two" in text
