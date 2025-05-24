import random
import string

import pytest

from xferx.commons import ASCII, IMAGE
from xferx.pdp8.dmsfs import (
    FILE_TYPE_ASCII,
    FILE_TYPE_SYS_USER,
    DirectorNameBlock,
    DMSDirectoryEntry,
    DMSFilesystem,
    StorageAllocationMap,
    StorageAllocationMapBlock,
    asc_to_sixbit_word12,
    from_12bit_words_to_bytes,
    from_bytes_to_12bit_words,
    sixbit_word12_to_asc,
)
from xferx.shell import Shell

DSK = "tests/dsk/dms.df32"


def test_12bit_words_ascii():
    def random_string(l):
        return "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(l)).encode("ascii")

    a = random_string(12)
    b = from_bytes_to_12bit_words(a, "ASCII")
    a2 = from_12bit_words_to_bytes(b, "ASCII")
    assert a == a2.rstrip(b"\x00")

    a = b"*TEST*"
    b = from_bytes_to_12bit_words(a, "ASCII")
    a2 = from_12bit_words_to_bytes(b, "ASCII")
    assert a == a2

    text = b"""\
    0 ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890
    1 ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890
    2 ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890
    3 ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890
    4 ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890
    5 ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890
    6 ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890
    7 ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890
    8 ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890
    9 ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890
    """
    data = text
    words = from_bytes_to_12bit_words(data, file_mode=IMAGE)
    data2 = from_12bit_words_to_bytes(words, file_mode=ASCII)
    words2 = from_bytes_to_12bit_words(data2, file_mode=ASCII)
    data3 = from_12bit_words_to_bytes(words2, file_mode=IMAGE)
    assert words == words2
    assert data == data3.rstrip(b"\x00")

    data = random_string(17)
    words = from_bytes_to_12bit_words(data, file_mode=IMAGE)
    data2 = from_12bit_words_to_bytes(words, file_mode=ASCII)
    words2 = from_bytes_to_12bit_words(data2, file_mode=ASCII)
    data3 = from_12bit_words_to_bytes(words2, file_mode=IMAGE)
    assert words == words2
    assert data == data3.rstrip(b"\x00")

    assert from_12bit_words_to_bytes([], file_mode=ASCII) == b""
    assert from_12bit_words_to_bytes([0], file_mode=ASCII) == b""

    # data = random_string(18)
    data = text
    print(len(data))
    words = from_bytes_to_12bit_words(data, file_mode=ASCII)
    print("len words", len(words))
    data2 = from_12bit_words_to_bytes(words, file_mode=IMAGE)
    print("len bytes (image)", len(data2))
    words2 = from_bytes_to_12bit_words(data2, file_mode=IMAGE)
    data3 = from_12bit_words_to_bytes(words2, file_mode=ASCII)
    print(words)
    print(words2)
    print(data)
    print(data3)
    # assert words == words2
    assert data == data3.rstrip(b"\x00")


def test_12bit_words_image():
    a = random.randbytes(12)
    b = from_bytes_to_12bit_words(a, IMAGE)
    a2 = from_12bit_words_to_bytes(b, IMAGE)
    assert a == a2.rstrip(b"\x00")

    a = b"*TEST*"
    b = from_bytes_to_12bit_words(a, IMAGE)
    a2 = from_12bit_words_to_bytes(b, IMAGE)
    assert a == a2


def test_sixbit_word12_to_asc_and_back():
    test_values = ["AB", "XD", "  ", "@ ", "??"]

    for t in test_values:
        word = asc_to_sixbit_word12(t)
        ascii = sixbit_word12_to_asc(word)
        assert ascii == t


class MockFilesytem(DMSFilesystem):
    first_sam_block_number = 128
    first_scratch_block_number = 251
    version_string = "AF"

    def __init__(self, t):
        self.t = t

    def read_12bit_words_block(self, block_number):
        if block_number in self.t:
            return self.t[block_number]
        else:
            raise ValueError(f"Invalid block number {block_number}")

    def write_12bit_words_block(self, block_number, words) -> None:
        self.t[block_number] = words


def test_sam_block():
    fs = MockFilesytem(
        {
            128: [0] * 128 + [129],
            129: [0] * 128 + [0],
        }
    )
    s0 = StorageAllocationMapBlock.read(fs, 128, 0)
    assert len(s0.sam) == 256
    assert s0.free() == 256
    assert s0.next_sam_block_number == 129
    s0.write()
    assert fs.t[128] == [0] * 128 + [129]
    s0.sam[0] = 13
    s0.sam[1] = 13
    s0.sam[2] = 13
    assert s0.free() == 256 - 3
    s0.write()
    assert fs.t[128] == [13] * 3 + [0] * 125 + [129]
    s0.sam[10] = 45
    s0.sam[11] = 45
    s0.sam[12] = 45
    s0.sam[13] = 45
    s0.write()
    assert s0.free() == 256 - 7


def test_sam():
    fs = MockFilesytem(
        {
            128: [13, 13, 13, 0, 0, 0, 0, 0, 0, 0, 45, 45, 45, 45] + [0] * 114 + [129],
            129: [10] + [0] * 127 + [0],
        }
    )
    sam = StorageAllocationMap.read(fs)
    assert sam.free() == 512 - 8
    sam.write()
    fn = sam.allocate_space("TEST", 10)
    assert sam.free() == 512 - 8 - 10
    sam.write()
    assert fn in sam.files_blocks
    assert len(sam.files_blocks[fn]) == 10

    sam = StorageAllocationMap.read(fs)
    assert sam.free() == 512 - 8 - 10
    assert fn in sam.files_blocks
    assert len(sam.files_blocks[fn]) == 10
    assert len(sam.files_blocks[45]) == 4
    sam.free_space(45)
    sam.write()
    assert sam.free() == 512 - 4 - 10
    assert 45 not in sam.files_blocks
    sam.free_space(10)
    assert sam.free() == 512 - 3 - 10
    sam.write()


def test_directory_entry():
    t = [3113, 3072, 0, 512, 3138]
    d = DMSDirectoryEntry.read(None, None, t, 0)
    assert d.filename == "PIP "
    assert d.extension == "SYS"
    assert d.file_number == 2
    assert d.low_core_addr == 0
    assert d.entry_point == 0o1000
    assert d.high_core_addr == 0
    assert d.system_program
    assert d.program_type == FILE_TYPE_SYS_USER
    assert d.to_words() == t


def test_directory_name_block():
    # fmt: off
    sam1w = [65, 65, 65, 641, 641, 641, 641, 641,
             641, 641, 708, 708, 708, 709, 709, 709,
             709, 709, 709, 709, 708, 708, 706, 706,
             706, 706, 706, 706, 706, 706, 706, 706,
             706, 706, 706, 706, 706, 706, 706, 770,
             770, 770, 770, 771, 771, 771, 771, 771,
             771, 771, 771, 771, 771, 771, 771, 771,
             771, 774, 774, 774, 838, 838, 838, 838,
             838, 838, 838, 838, 902, 966, 966, 966,
             966, 1030, 1030, 1094, 1094, 1094, 1094, 6,
             6, 1030, 6, 6, 6, 6, 6, 6,
             7, 7, 8, 8, 8, 8, 8, 8,
             8, 8, 8, 8, 8, 8, 8, 8,
             8, 8, 8, 8, 9, 9, 9, 9,
             9, 9, 9, 9, 9, 9, 9, 265,
             265, 265, 265, 73, 73, 73, 74, 65, 0]
    dn1w = [251, 2150, 128,
         2424, 35, 3584, 3584, 3137,
         3113, 3072, 0, 512, 3138,
         2404, 2676, 0, 1408, 3139,
         2863, 2148, 3840, 3840, 3140,
         931, 2318, 0, 0, 3141,
         3105, 2852, 0, 3200, 3142,
         2340, 3328, 3712, 3712, 3143,
         932, 2356, 128, 0, 3080,
         947, 3693, 128, 0, 3081,
         2479, 3252, 0, 128, 3146,
         934, 3342, 128, 0, 3147,
         943, 3278, 0, 0, 3148,
         2479, 3308, 0, 128, 3149,
         3316, 2220, 384, 384, 3150,
         2345, 2151, 128, 128, 3151,
         3316, 2164, 0, 0, 16,
         3125, 2994, 0, 0, 17,
         0, 0, 0, 0, 0,
         0, 0, 0, 0, 0,
         0, 0, 0, 0, 0,
         0, 0, 0, 0, 0,
         0, 0, 0, 0, 0,
         0, 0, 0, 0, 0,
         0, 0, 0, 0, 0,
         0, 0, 0, 0, 0,
         129]
    # fmt: on
    fs = MockFilesytem(
        {
            127: dn1w,
            128: sam1w,
            129: [0] * 129,
            130: [0] * 129,
        }
    )
    assert fs.free() == 39
    sam = StorageAllocationMap.read(fs)
    dn1 = DirectorNameBlock.read(fs, 127, 0, sam)
    assert dn1.first_file_number == 1
    assert dn1.last_file_number == 25
    dn1.write()
    assert fs.t[127] == dn1w
    d = fs.get_file_entry("PIP.SYS")
    assert d is not None
    assert d.filename == "PIP "
    assert d.extension == "SYS"
    assert d.file_number == 2
    assert d.low_core_addr == 0
    assert d.entry_point == 0o1000
    assert d.high_core_addr == 0
    assert d.system_program
    assert d.program_type == FILE_TYPE_SYS_USER
    assert d.get_length() == 21
    assert len(sam.files_blocks[d.file_number]) == d.get_length()
    assert 2 in sam.files_blocks
    with pytest.raises(FileNotFoundError):
        fs.get_file_entry("XXX.SYS")
    d2 = fs.create_file("TEST.ASCII", 7)
    assert d2.program_type == FILE_TYPE_ASCII
    assert fs.free() == 39 - 7
    sam = StorageAllocationMap.read(fs)
    assert sam.free() == 39 - 7
    assert len(sam.files_blocks[d.file_number]) == 21
    # print([(x, len(b)) for x, b in sam.files_blocks.items()])
    assert d.delete()
    sam = StorageAllocationMap.read(fs)
    assert 2 not in sam.files_blocks


def test_dms():
    with open(f"{DSK}.mo", "wb") as f:
        f.truncate(65534)

    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /dms {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, DMSFilesystem)

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("type t:1.ascii", batch=True)

    x = fs.read_bytes("50.ascii")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.filter_entries_list("*.ascii"))
    assert len(l) == 7

    # Init
    shell.onecmd(f"init /dms {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /dms {DSK}.mo", batch=True)
    shell.onecmd("ex ou:", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("copy t:*.ascii ou:", batch=True)
    shell.onecmd("copy t:aaaa.user ou:aaaa.user:777;5555", batch=True)
    shell.onecmd("copy t:bbbb.sys ou:", batch=True)

    x1 = fs.read_bytes("50.ascii")
    x1 = x1.rstrip(b"\0")
    assert len(x1) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1

    fs = shell.volumes.get('OU')
    l = list(fs.filter_entries_list("*.ascii"))
    assert len(l) == 7
    l = list(fs.filter_entries_list("*.user"))
    assert len(l) == 1

    e1 = fs.get_file_entry("AAAA.USER")
    assert e1 is not None
    assert e1.filename == "AAAA"
    assert e1.extension == "USER"
    assert e1.low_core_addr == 0o777
    assert e1.high_core_addr == 0
    assert e1.entry_point == 0o5555

    x1 = fs.read_bytes("aaaa.user")
    assert b"abcdefghijklmnopqrstuvwxyz" in x1

    shell.onecmd("del ou:*.user", batch=True)
    l = list(fs.filter_entries_list("*.user"))
    assert len(l) == 0

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        fs.read_bytes("50.ascii")


def test_dms_write_file():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount t: /dms {DSK}.mo", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, DMSFilesystem)

    block_size = 256
    blocks = 5
    filename = "data.ascii"
    data = b""
    for i in range(0, blocks):
        data += chr(i + 65).encode("ascii") * block_size
    fs.write_bytes(filename, data)

    data_read = fs.read_bytes(filename)
    assert data_read == data

    f = fs.open_file(filename)
    for i in range(0, blocks):
        block_data = f.read_block(i)
        assert len(block_data) == block_size
        assert block_data == chr(i + 65).encode("ascii") * block_size
    f.close()

    f = fs.open_file(filename)
    for i in range(0, blocks):
        tmp = chr(i + 85).encode("ascii") * block_size
        f.write_block(tmp, i)
    f.close()

    f = fs.open_file(filename)
    for i in range(0, blocks):
        block_data = f.read_block(i)
        assert block_data == chr(i + 85).encode("ascii") * block_size
    f.close()

    f = fs.open_file(filename)
    for i in range(0, blocks // 2):
        tmp = chr(i + 65).encode("ascii") * block_size * 2
        f.write_block(tmp, i * 2, number_of_blocks=2)
    f.close()

    f = fs.open_file(filename)
    for i in range(0, blocks // 2):
        block_data = f.read_block(i * 2, number_of_blocks=2)
        assert block_data == chr(i + 65).encode("ascii") * block_size * 2
    f.close()
