from xferx.commons import ASCII, IMAGE, READ_FILE_FULL
from xferx.device.block_18bit import (
    from_18bit_words_to_bytes,
    from_bytes_to_18bit_words,
)
from xferx.pdp15.adssfs import (
    DIRECTORY_BLOCK,
    WORDS_PER_BLOCK,
    ADSSDirectory,
    ADSSPath,
    ADSSFilesystem,
    oct_dump,
)
from xferx.pdp15.iops import (
    ascii_to_five_seven,
    five_seven_to_ascii,
    decode_block_format,
    encode_block_format,
)

from xferx.shell import Shell

DSK = "tests/dsk/adss.dtp"

# fmt: off
DIR = [
    0o777777, 0o777777, 0o777777, 0o777777, 0o777471, 0o630614, 0o306143, 0o061430,
    0o614306, 0o143041, 0o020410, 0o204102, 0o041021, 0o430614, 0o306143, 0o061430,
    0o614306, 0o143061, 0o430614, 0o306143, 0o061430, 0o614306, 0o143061, 0o437777,
    0o777777, 0o777777, 0o777777, 0o777777, 0o777777, 0o777777, 0o777777, 0o777777,
    0o561417, 0o010400, 0o021116, 0o400037, 0o040424, 0o000000, 0o021116, 0o400040,
    0o053005, 0o032524, 0o021116, 0o400041, 0o111624, 0o050105, 0o021116, 0o400043,
    0o111624, 0o161716, 0o021116, 0o400050, 0o220514, 0o050105, 0o021116, 0o400076,
    0o220514, 0o161716, 0o021116, 0o400077, 0o561411, 0o022200, 0o021116, 0o400105,
    0o061703, 0o011400, 0o021116, 0o400120, 0o000000, 0o000000, 0o000000, 0o000000,
    0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000,
    0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000,
    0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000,
    0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000,
    0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000,
    0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000,
    0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000,
    0o777777, 0o777742, 0o104777, 0o777777, 0o000000, 0o000000, 0o000000, 0o000000,
    0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000,
    0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o007777,
    0o777777, 0o777777, 0o777777, 0o777777, 0o777777, 0o777777, 0o777777, 0o777777,
    0o131571, 0o556165, 0o233123, 0o400000, 0o231320, 0o021413, 0o233123, 0o400042,
    0o111702, 0o141300, 0o233123, 0o400046, 0o230716, 0o021413, 0o233123, 0o400052,
    0o233123, 0o100116, 0o233123, 0o400056, 0o233123, 0o021413, 0o233123, 0o400061,
    0o562331, 0o231404, 0o233123, 0o400062, 0o021124, 0o150120, 0o233123, 0o400071,
    0o041122, 0o050324, 0o233123, 0o400100, 0o050411, 0o240000, 0o233123, 0o400644,
    0o201120, 0o000000, 0o233123, 0o400664, 0o150103, 0o221700, 0o233123, 0o400704,
    0o066400, 0o000000, 0o233123, 0o400742, 0o042403, 0o172031, 0o233123, 0o401001,
    0o042515, 0o200000, 0o233123, 0o401004, 0o252004, 0o012405, 0o233123, 0o401010,
    0o230705, 0o160000, 0o233123, 0o401020, 0o031001, 0o111600, 0o233123, 0o401051,
    0o200124, 0o031000, 0o233123, 0o401071, 0o000000, 0o000000, 0o000000, 0o000000,
    0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000,
    0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000, 0o000000,
]
# fmt: on


class MockFilesytem(ADSSFilesystem):
    def __init__(self, t):
        self.t = t

    def read_words_block(self, block):
        block_number = block if isinstance(block, int) else block.block_number
        if block_number in self.t:
            return self.t[block_number]
        else:
            raise ValueError(f"Invalid block number {block_number}")

    def write_words_block(self, block, words) -> None:
        block_number = block if isinstance(block, int) else block.block_number
        self.t[block_number] = words


def test_five_seven():
    """
    Test conversion between ASCII and 5-7 encoding
    """
    data = [f"{i:>5} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" for i in range(10)]
    for line in data:
        lineb = line.encode("ascii")
        words = ascii_to_five_seven(lineb)
        tmp = five_seven_to_ascii(words)
        assert lineb == tmp.rstrip(b"\0")


def test_18bit_words():
    words = list(range(0, 2048))
    tmp = from_18bit_words_to_bytes(words, IMAGE)
    w2 = from_bytes_to_18bit_words(tmp, IMAGE)

    words = list(range(0, 127)) * 26
    tmp = from_18bit_words_to_bytes(words, ASCII)
    w2 = from_bytes_to_18bit_words(tmp, ASCII)
    assert words == w2


def encode_decode(content, mode, l=None):
    blocks_content = list(encode_block_format(content, mode, words_per_block=WORDS_PER_BLOCK - 1))
    if l is not None:
        assert len(blocks_content) == l
    tmp = bytearray()
    for block_content in blocks_content:
        tmp.extend(decode_block_format(block_content))
    assert bytes(tmp) == content


def test_block_format():
    """
    Test encoding and decoding of block format
    """

    content = b"***Test***\n"
    encode_decode(content, ASCII, 1)

    content = ("\n".join([f"{i:>5} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890" for i in range(50)]) + "\n").encode("ascii")
    encode_decode(content, ASCII, 5)

    content = b'\xb3\x80\xb8\xb5\xa3\xa3\xa6\x80\x96\xa6\xa9\xa3\xb4\x80\xa7\xa9\xa6\xb7\xa9\xb1\xa4\x8c\x80\x81\x9e\x96\xa9\xb9\x93\xb5\x80\x82\x9b\x80\x81\x90\x8c\x80\x82\x81\x90\x9e\xb6\xa6\xa9\xa4\xb1\x93\x80\xaf\x81\x82\xb8\x80\xb8\xb5\xa3\xa3\xa6\x9b\x80\x96\xa6\xa9\xa3\xb4\xad\x8f\x8f\x8c\x80\x83\x9e\xb5\xa5\xb4\x8f\x8f\x8c\x80\x84\x8d\x80\x81\x8e\x80\x80\x80\x80\x80'
    words = list(range(0, 10))
    content = from_18bit_words_to_bytes(words, IMAGE)
    encode_decode(content, IMAGE, 1)

    words = list(range(0, 127))
    content = from_18bit_words_to_bytes(words, IMAGE)
    encode_decode(content, IMAGE, 1)

    words = list(range(0, 1000))
    content = from_18bit_words_to_bytes(words, IMAGE)
    encode_decode(content, IMAGE, 5)


def test_directory():
    fs = MockFilesytem(
        {
            DIRECTORY_BLOCK: list(DIR),
        }
    )
    d = ADSSDirectory.read(fs)

    words = [0o777777, 0o777777, 0o777777, 0o777777]
    e1 = ADSSPath.read(d, words, 0, 0)
    oct_dump(words)
    oct_dump(e1.to_words())
    assert e1.to_words() == words

    d.write()
    print("-----")
    oct_dump(DIR)
    print("-----")
    oct_dump(fs.read_words_block(DIRECTORY_BLOCK))
    assert fs.read_words_block(DIRECTORY_BLOCK) == list(DIR)


def test_adss():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount adss1: /adss {DSK}", batch=True)
    fs = shell.volumes.get_volume('ADSS1')
    assert isinstance(fs, ADSSFilesystem)
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    shell.onecmd("dir adss1:", batch=True)
    shell.onecmd("type adss1:f1;src", batch=True)

    x = shell.volumes.read_bytes("adss1:b100;bin")
    assert len(x) == 300
    assert from_bytes_to_18bit_words(x, IMAGE) == list(range(0, 100))

    x = shell.volumes.read_bytes("adss1:f100;src")
    assert len(x) == 4400
    for i in range(0, 100):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(shell.volumes.filter_entries_list("adss1:*;SRC"))
    assert len(l) == 3


def test_adss_init():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount adss1: /adss {DSK}", batch=True)
    shell.onecmd(f"create /allocate:1152 {DSK}.mo", batch=True)
    shell.onecmd(f"init /adss {DSK}.mo", batch=True)
    shell.onecmd(f"mount adss2: /adss {DSK}.mo", batch=True)
    shell.onecmd("dir adss2:", batch=True)
    shell.onecmd("copy adss1:*;SRC adss2:", batch=True)

    l = list(shell.volumes.filter_entries_list("adss2:*;SRC"))
    assert len(l) == 3

    shell.onecmd("delete adss2:F1;SRC", batch=True)
    l = list(shell.volumes.filter_entries_list("adss2:*;SRC"))
    assert len(l) == 2


def test_adss_write_file():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}_2.mo", batch=True)
    shell.onecmd(f"mount adss2: /adss {DSK}_2.mo", batch=True)
    fs = shell.volumes.get_volume('ADSS2')
    assert isinstance(fs, ADSSFilesystem)

    for l in (1, 10, 100, 1000):
        words = list(range(0, l))
        data = from_18bit_words_to_bytes(words, IMAGE)
        filename = f"adss2:T{l};BIN"
        shell.volumes.write_bytes(filename, data)
        data_read = shell.volumes.read_bytes(filename)
        assert data_read == data

    for l in (1, 10, 100, 1000):
        filename = f"adss2:T{l};BIN"
        data = shell.volumes.read_bytes(filename)
        words = from_bytes_to_18bit_words(data, IMAGE)
        assert words == list(range(0, l))

    for l in (1, 10, 100, 1000):
        tmp = bytearray()
        for i in range(0, l):
            tmp.extend(f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890\n".encode("ascii"))
        data = bytes(tmp)
        filename = f"adss2:T{l};SRC"
        shell.volumes.write_bytes(filename, data)
        data_read = shell.volumes.read_bytes(filename)
        assert data_read == data

    shell.onecmd("create /allocate:400 adss2:big;bin", batch=True)
    big = shell.volumes.get_file_entry("adss2:BIG;BIN")
    assert len(list(big.get_blocks())) == 400

    shell.onecmd("dismount adss2:", batch=True)
    shell.onecmd(f"del {DSK}_2.mo", batch=True)
