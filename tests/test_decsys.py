from xferx.commons import IMAGE
from xferx.pdp7.codes import (
    fiodec_to_str,
    read_baudot_string,
    str_to_baudot,
    str_to_fiodec,
)
from xferx.pdp7.decsysfs import (  # LibraryDirectory,
    DECSysDirectoryEntry,
    DECSysFilesystem,
    ProgramDirectory,
    from_bytes_to_18bit_words,
)

# from xferx.shell import Shell


class MockFilesytem(DECSysFilesystem):

    def __init__(self, t):
        self.t = t

    def read_18bit_words_block(self, block_number):
        if block_number in self.t:
            return list(self.t[block_number])
        else:
            raise ValueError(f"Invalid block number {block_number}")

    def write_18bit_words_block(self, block_number, words) -> None:
        self.t[block_number] = list(words)


def test_baudot():
    strings = [
        "A",
        "1.",
        "HELLO",
        "FORTRN",
    ]
    for s in strings:
        words = str_to_baudot(s, length=None)
        s1, _ = read_baudot_string(words)
        assert s1 == s
    for s in strings:
        words = str_to_baudot(s, length=2)
        assert len(words) == 2
        s1, _ = read_baudot_string(words)
        assert s1 == s
    l0 = "CAB DECSYS7 COPY"
    l1 = "15 JUNE 1966"
    words = str_to_baudot(l0) + [0o777777] + str_to_baudot(l1) + [0o777777]
    s0, p = read_baudot_string(words)
    assert s0 == l0
    s1, p = read_baudot_string(words, p + 1)
    assert s1 == l1
    assert p == len(words) - 1


def test_directory_entry():
    fs = MockFilesytem({})
    directory = ProgramDirectory(fs)
    t = [1, 115084, 10252, 7, 64]
    entry = DECSysDirectoryEntry.read(directory, t, 0)
    assert entry.file_type == "SYSTEM"
    assert entry.filename == "CONTEN"
    assert entry.block_number == 7
    assert entry.starting_address == 63
    assert entry.to_words() == t

    t = [1, 76838, 132224, 9, 64]
    entry = DECSysDirectoryEntry.read(directory, t, 0)
    assert entry.file_type == "SYSTEM"
    assert entry.filename == "LABEL"
    assert entry.block_number == 9
    assert entry.starting_address == 63
    assert entry.to_words() == t

    t = [2, 10280, 8192, 146, 147, 148]
    entry = DECSysDirectoryEntry.read(directory, t, 0)
    assert entry.file_type == "WORKING"
    assert entry.filename == "TEST"
    assert entry.fortran_block_number == 146
    assert entry.assembler_block_number == 147
    assert entry.block_number == 148
    assert entry.to_words() == t


def test_program_directory():
    # fmt: off
    program_directory = [73, 1, 115084, 10252, 7, 64, 1, 76838, 132224, 9, 64, 1, 115086, 108050, 10, 64, 1, 199208, 132006, 11, 64, 1, 74160, 147456, 12, 64, 1, 74160, 149524, 13, 4887, 1, 180628, 24744, 23, 64, 1, 183336, 164774, 35, 2859, 1, 180628, 9484, 47, 5958, 1, 231076, 196768, 67, 4994, 1, 133400, 8192, 76, 577, 2, 43026, 74112, 143, 144, 145, 2, 10280, 8192, 146, 0, 0, 2, 74124, 90112, 147, 148, 149, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 157]
    # fmt: on
    fs = MockFilesytem({2: list(program_directory)})
    directory = ProgramDirectory.read(fs)
    assert len(directory.entries) == 14
    assert directory.first_free_block == 157
    fs.write_18bit_words_block(2, [0] * 256)
    directory.write()
    assert fs.read_18bit_words_block(2) == program_directory


def test_fiodec():
    # fmt: off
    data = b'\xb3\x80\xb8\xb5\xa3\xa3\xa6\x80\x96\xa6\xa9\xa3\xb4\x80\xa7\xa9\xa6\xb7\xa9\xb1\xa4\x8c\x80\x81\x9e\x96\xa9\xb9\x93\xb5\x80\x82\x9b\x80\x81\x90\x8c\x80\x82\x81\x90\x9e\xb6\xa6\xa9\xa4\xb1\x93\x80\xaf\x81\x82\xb8\x80\xb8\xb5\xa3\xa3\xa6\x9b\x80\x96\xa6\xa9\xa3\xb4\xad\x8f\x8f\x8c\x80\x83\x9e\xb5\xa5\xb4\x8f\x8f\x8c\x80\x84\x8d\x80\x81\x8e\x80\x80\x80\x80\x80'
    # fmt: on
    words = from_bytes_to_18bit_words(data, file_type=IMAGE)
    txt = fiodec_to_str(words)
    words2 = str_to_fiodec(txt)
    txt2 = fiodec_to_str(words2)
    assert words == words2
    assert txt == txt2

    # fmt: off
    block_words = [
        0o000000, 0o777742, 0o630070, 0o654343, 0o460026, 0o465143, 0o640047, 0o514667,
        0o516144, 0o140001, 0o362651, 0o712365, 0o000233, 0o000120, 0o140002, 0o012036,
        0o664651, 0o446123, 0o005701, 0o027000, 0o706543, 0o434633, 0o002646, 0o514364,
        0o551717, 0o140003, 0o366545, 0o641717, 0o140004, 0o150001, 0o160000, 0o000000,
    ] + [0] * (256-32)
    # fmt: on
    assert len(block_words) == 256
    num_words = 0x40000 - block_words[1]
    words3 = block_words[2 : 2 + num_words]
    txt3 = fiodec_to_str(words3)
    words4 = str_to_fiodec(txt3)
    assert words3 == words4
    assert words3 == words

    # fmt: off
    data2 = b'\xb3\x80\xb8\xb5\xa3\xa3\xa6\x80\x96\xa6\xa9\xa3\xb4\x80\xa7\xa9\xa6\xb7\xa9\xb1\xa4\x8c\x80\x81\x9e\x96\xa9\xb9\x93\xb5\x80\x82\x9b\x80\x81\x90\x8c\x80\x82\x81\x90\x9e\xb6\xa6\xa9\xa4\xb1\x93\x80\xaf\x81\x82\xb8\x80\xb8\xb5\xa3\xa3\xa6\x9b\x80\x96\xa6\xa9\xa3\xb4\xad\x8f\x8f\x8c\x80\x83\x9e\xb5\xa5\xb4\x8f\x8f\x8c\x80\x84\x8d\x80\x81\x8e\x80\x80\x80\x80\x80'
    # fmt: on
    words5 = from_bytes_to_18bit_words(data2, file_type=IMAGE)
    txt5 = fiodec_to_str(words5)
    words6 = str_to_fiodec(txt5)
    txt6 = fiodec_to_str(words6)
    assert words5 == words6
    assert txt5 == txt6


# DSK = "tests/dsk/unixv0.dsk"
#
#
# def test_unix0_read():
#     shell = Shell(verbose=True)
#     shell.onecmd(f"mount t: /unix0 {DSK}", batch=True)
#     fs = shell.volumes.get('T')
#     assert isinstance(fs, UNIX0Filesystem)
#     assert fs.version == 0
#
#     shell.onecmd("dir t:", batch=True)
#     shell.onecmd("dir t:/", batch=True)
#     shell.onecmd("dir t:/system/", batch=True)
#     shell.onecmd("type t:/system/password", batch=True)
#
#     x = fs.read_text("dd/data/9k")
#     assert x.startswith("|")
#
#     l = list(fs.entries_list)
#     filenames = [x.filename for x in l if not x.is_empty]
#     assert "dd" in filenames
#     assert "system" in filenames
#
#     entry = fs.get_file_entry("/test/a")
#     assert not entry.inode.is_large
#
#     entry = fs.get_file_entry("/test/b")
#     assert not entry.inode.is_large
#
#     entry = fs.get_file_entry("/test/c")
#     assert entry.inode.is_large
