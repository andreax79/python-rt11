from datetime import date

import pytest

from xferx.pdp11.rstsfs import (  # UserFileDirectoryBlock,; date_to_rsts,; rsts_to_date,
    ANY_GROUP,
    ANY_USER,
    PPN,
    RSTSFilesystem,
)
from xferx.shell import Shell

DSK = "tests/dsk/rsts.dsk"


def test_rsts():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /rsts {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, RSTSFilesystem)

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir /uic t:", batch=True)
    shell.onecmd("type t:[100,100]1.txt", batch=True)

    x = fs.read_bytes("[5,10]50.txt")
    x = x.rstrip(b"\0")
    # assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.filter_entries_list("*.TXT[100,100]"))
    assert len(l) == 4

    x1 = fs.read_bytes("[100,100]1000.txt")
    x1 = x1.rstrip(b"\0")
    # assert len(x1) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1

    l = list(fs.filter_entries_list("*.TXT[200,200]"))
    # assert len(l) == 3

    x2 = fs.read_bytes("[100,100]1000.txt")
    x2 = x2.rstrip(b"\0")
    # assert len(x2) == 44000
    for i in range(0, 1000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x2
    assert x1 == x2

    l = list(fs.filter_entries_list("*.TXT[1,2]"))
    assert len(l) == 0

    x3 = fs.read_bytes("[5,10]500.txt")
    x3 = x3.rstrip(b"\0")
    # assert len(x3) == 22000
    for i in range(0, 500):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x3

    x4 = fs.read_bytes("[5,10]5000.txt")
    x4 = x4.rstrip(b"\0")
    for i in range(0, 5000):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x4


def test_ppn_from_str_normal_case():
    ppn = PPN.from_str("[123,45]")
    assert ppn.group == 123
    assert ppn.user == 45


def test_ppn_from_str_any_group():
    ppn = PPN.from_str("[*,45]")
    assert ppn.group == ANY_GROUP
    assert ppn.user == 45


def test_ppn_from_str_any_user():
    ppn = PPN.from_str("[123,*]")
    assert ppn.group == 123
    assert ppn.user == ANY_USER


def test_ppn_from_str_any_group_and_user():
    ppn = PPN.from_str("[*,*]")
    assert ppn.group == ANY_GROUP
    assert ppn.user == ANY_USER


def test_ppn_from_word_normal_case():
    ppn = PPN.from_word(0x7B2D)  # 0x7B = 123, 0x2D = 45
    assert ppn.group == 123
    assert ppn.user == 45


def test_ppn_to_wide_str_normal_case():
    ppn = PPN(123, 45)
    assert ppn.to_wide_str() == "[123,45 ]"


def test_ppn_to_wide_str_any_group():
    ppn = PPN(ANY_GROUP, 45)
    assert ppn.to_wide_str() == "[  *,45 ]"


def test_ppn_to_wide_str_any_user():
    ppn = PPN(123, ANY_USER)
    assert ppn.to_wide_str() == "[123,*  ]"


def test_ppn_to_wide_str_any_group_and_user():
    ppn = PPN(ANY_GROUP, ANY_USER)
    assert ppn.to_wide_str() == "[  *,*  ]"


def test_ppn_str_normal_case():
    ppn = PPN(123, 45)
    assert str(ppn) == "[123,45]"


def test_ppn_str_any_group():
    ppn = PPN(ANY_GROUP, 45)
    assert str(ppn) == "[*,45]"


def test_ppn_str_any_user():
    ppn = PPN(123, ANY_USER)
    assert str(ppn) == "[123,*]"


def test_ppn_str_any_group_and_user():
    ppn = PPN(ANY_GROUP, ANY_USER)
    assert str(ppn) == "[*,*]"
