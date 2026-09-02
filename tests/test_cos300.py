import pytest

from xferx.pdp8.cos300fs import (
    COS300Path,
    COS300Filesystem,
    ascii_to_cos_codes,
    cos_codes_to_ascii,
    from_12bit_words_to_bytes,
    from_bytes_to_12bit_words,
)
from xferx.pdp8.os8fs import OS8Partition, OS8Segment
from xferx.shell import Shell

# fmt: off
WORDS = [4091, 100, 3381, 2227, 3408, 3392, 4091, 110, 3955, 2468,
         3123, 2368, 4090, 120, 3965, 2352, 3503, 3405, 2195, 4088,
         130, 3965, 3062, 2979, 2483, 869, 1229, 3136, 4090, 140,
         3965, 2189, 2387, 849, 1088, 4093, 150, 3187, 3108, 4087,
         160, 3941, 2740, 3181, 2234, 594, 850, 850, 640, 4085,
         170, 2928, 3121, 893, 2410, 3377, 2914, 3721, 2189, 2189,
         1098, 4091, 180, 3940, 3126, 3061, 1954, 4085, 190, 3941,
         2740, 3181, 2234, 610, 866, 868, 3126, 3061, 640, 4091,
         200, 3946, 3044, 3265, 2176, 4082, 210, 3946, 2505, 2191,
         2918, 1007, 3502, 2278, 3274, 2608, 117, 3073, 2928, 3121,
         4093, 220, 2479, 2368, 0, 2368, 0, 2368, 0, 220, 2479,
         2368, 0, 220, 2479, 2368, 0, 3121, 4093, 210, 2479,
         2368, 0, 3265, 3433, 2432, 4056, 130, 1793, 3381, 3493,
         2479, 3393, 2544, 3265, 3433, 2229, 120, 2470, 2831, 65,
         3433, 2740, 106, 3329, 3508, 2469, 117, 3073, 3187, 2735,
         3393, 3433, 2433, 2922, 3381, 112, 2497, 3433, 2433, 3430,
         3009, 3302, 2228, 3119, 3329, 2176, 4058, 140, 1793, 3381,
         3493, 2479, 3393, 3618, 3329, 3381, 3503, 2561, 2223, 2369,
         3433, 2433, 2212, 3446, 2221, 111, 3502, 2278, 3265, 3433,
         2229, 117, 2662, 116, 3446, 2406, 3061, 120, 2228, 111,
         3125, 102, 3684, 3508, 2469, 4056, 150, 1793, 2547, 3118,
         99, 3713, 3433, 2662, 101, 2466, 3021, 2530, 2358, 2933,
         3725, 112, 3265, 3370, 3701, 2638, 2544, 3310, 2483, 961,
         117, 2662, 103, 2739, 3381, 103, 2743, 2433, 2227, 2433,
         2689, 2550, 2925, 105, 3126, 3316, 4069, 160, 1793, 3373,
         2737, 103, 3123, 2945, 3433]
WORDS1 = [4093, 210, 2479, 2368, 0, 3265, 3433, 2432, 4056, 130]
# fmt: on

DSK = "tests/dsk/cos300.tu56"
DSK1 = "tests/dsk/cos300.dsk"


def test_from_12bit_words_to_bytes():
    byte_data = from_12bit_words_to_bytes(WORDS)
    words = from_bytes_to_12bit_words(byte_data)
    assert WORDS == words


def test_cos_codes_to_ascii1():
    text = cos_codes_to_ascii(WORDS1).decode("ascii")
    words = ascii_to_cos_codes(text.encode("ascii"))
    assert words == WORDS1[: len(words)]


def test_cos_codes_to_ascii():
    text = cos_codes_to_ascii(WORDS).decode("ascii")
    assert text.startswith("0100")
    assert "DISPLAY(1,1,1)" in text
    assert text.endswith("END\n")
    words = ascii_to_cos_codes(text.encode("ascii"))
    assert words == WORDS[: len(words)]


class MockFilesytem(COS300Filesystem):
    number_of_blocks = 200

    def __init__(self):
        pass


def test_write_directory_entry():
    words = [0, 0]
    fs = MockFilesytem()
    partition = OS8Partition(fs, 0)
    segment = OS8Segment(partition)
    segment.extra_words = 1

    e = COS300Path.read(segment, words, 0, 0)
    segment.entries_list.append(e)
    assert segment.number_of_entries == 1
    assert e.is_empty
    words1 = e.to_words()
    assert words == words1

    assert len(words1) == 2
    e.empty_entry = False
    e.stem = "TEST"
    e.extension = "TX"
    e.length = 123
    e.extra_words = [342]
    assert not e.is_empty
    words2 = e.to_words()
    assert len(words2) == 6

    e2 = COS300Path.read(segment, words2, 0, 0)
    assert not e2.is_empty
    assert e2.to_words() == words2


def test_cos300():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /cos300 {DSK}", batch=True)
    fs = shell.volumes.get_volume('T')
    assert isinstance(fs, COS300Filesystem)
    assert fs.dev.is_rx
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("type t:a50.a", batch=True)

    x = fs.read_bytes("A50.A")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.filter_entries_list("*.A"))
    assert len(l) == 9

    text = fs.read_bytes("DIBOL.S").decode("ascii")
    assert "CIAO" in text


def test_cos300_init():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /cos300 {DSK}", batch=True)
    shell.onecmd(f"create /allocate:280 {DSK1}.mo", batch=True)
    shell.onecmd(f"init /cos300 {DSK1}.mo", batch=True)
    shell.onecmd(f"mount ou: /cos310 {DSK1}.mo", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("copy t:*.A ou:", batch=True)
    fs = shell.volumes.get_volume('OU')

    x1 = fs.read_bytes("A50.A")
    x1 = x1.rstrip(b"\0")
    assert len(x1) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1
    l = list(fs.filter_entries_list("*.A"))
    assert len(l) == 9
    fs.get_file_entry("A10.A").delete()
    l = list(fs.filter_entries_list("*.A"))
    assert len(l) == 8

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        print(fs.read_bytes("A50.A"))
