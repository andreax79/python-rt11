import shlex
from datetime import date

import pytest

from xferx.commons import PartialMatching, bytes_to_word, word_to_bytes
from xferx.pdp11.rad50 import asc2rad, rad2asc
from xferx.pdp11.rt11fs import date_to_rt11, rt11_canonical_filename, rt11_to_date
from xferx.shell import extract_options


def test_bytes_to_word():
    # Test with valid input
    assert bytes_to_word(b"\x01\x00") == 1
    assert bytes_to_word(b"\xff\xff") == 65535
    assert bytes_to_word(b"\x01\xab\xcd", position=1) == 52651
    # Test with out of bounds position
    with pytest.raises(IndexError):
        bytes_to_word(b"\x01\x02", position=2)


def test_word_to_bytes():
    # Test with valid input
    assert word_to_bytes(1) == b"\x01\x00"
    assert word_to_bytes(65535) == b"\xFF\xFF"
    assert len(word_to_bytes(1234)) == 2
    for i in range(0, 1 << 16):
        assert bytes_to_word(word_to_bytes(i)) == i
    # Test with negative value
    with pytest.raises(ValueError):
        word_to_bytes(-1)
    # Test with value exceeding 16-bit range
    with pytest.raises(ValueError):
        word_to_bytes(2**16)


def test_rad2asc():
    # Test with valid input
    assert rad2asc(b"\x01\x00") == "A"
    assert rad2asc(b"\x06\x01") == "FV"
    assert rad2asc(b"\x00\x00") == ""
    # Test with different positions
    assert rad2asc(b"\x10\x37\x31\x43\x74", position=0) == "H2P"
    assert rad2asc(b"\x10\x37\x31\x43\x74", position=2) == "J0A"
    # Test with all zeros
    assert rad2asc(b"\x00\x00\x00") == ""


def test_asc2rad():
    # Test with valid input
    assert asc2rad("ABC") == b"\x93\x06"
    assert asc2rad("Z12") == b"x\xa7"
    assert asc2rad("") == b"\x00\x00"
    # Test with lowercase characters
    assert asc2rad("zia") == b"\xe9\xa3"
    assert asc2rad(":$%") == b"\x54\xfe"


def test_rt11_to_date():
    # Test with None
    assert rt11_to_date(0) is None
    # Test with valid input
    assert date(1979, 1, 6) == rt11_to_date(1223)
    assert date(1984, 2, 5) == rt11_to_date(2220)
    assert date(1991, 12, 31) == rt11_to_date(13299)
    assert date(2000, 1, 1) == rt11_to_date(1084)
    assert date(2014, 3, 27) == rt11_to_date(20330)
    assert date(2024, 1, 1) == rt11_to_date(17460)


def test_date_to_rt11():
    # Test with None
    assert date_to_rt11(None) == 0
    # Test with valid input
    assert date_to_rt11(date(1979, 1, 6)) == 1223
    assert date_to_rt11(date(1984, 2, 5)) == 2220
    assert date_to_rt11(date(1991, 12, 31)) == 13299
    assert date_to_rt11(date(2000, 1, 1)) == 1084
    assert date_to_rt11(date(2014, 3, 27)) == 20330
    assert date_to_rt11(date(2024, 1, 1)) == 17460


def test_rt1_canonical_filename():
    assert rt11_canonical_filename(None) == "."
    assert rt11_canonical_filename("") == "."
    assert rt11_canonical_filename("LICENSE") == "LICENS."
    assert rt11_canonical_filename("license.") == "LICENS."
    assert rt11_canonical_filename("read.me") == "READ.ME"
    assert rt11_canonical_filename("read.*", wildcard=True) == "READ.*"
    assert rt11_canonical_filename("r*", wildcard=True) == "R*.*"
    assert rt11_canonical_filename("*.*", wildcard=True) == "*.*"


def test_partial_matching():
    x = PartialMatching()
    x.add("APP_LE")
    x.add("PE_AR")
    x.add("O_RANGE")
    x.add("D")
    x.add("DA_TE")

    # Test partial matching keys
    assert x.get("APP") == "APPLE"
    assert x.get("PE") == "PEAR"
    assert x.get("PEA") == "PEAR"
    assert x.get("O") == "ORANGE"
    assert x.get("OR") == "ORANGE"
    assert x.get("ORA") == "ORANGE"
    assert x.get("ORAN") == "ORANGE"
    assert x.get("D") == "D"
    assert x.get("DA") == "DATE"
    assert x.get("DAT") == "DATE"
    assert x.get("DATE") == "DATE"

    # Test non-partial matching keys
    assert x.get("XXX") is None
    assert x.get("XX") is None
    assert x.get("A") is None
    assert x.get("P") is None
    assert x.get("PE_X") is None
    assert x.get("O_") is None
    assert x.get("ORANGO") is None
    assert x.get("TD") is None
    assert x.get("") is None


def test_extract_options():
    line = "command /a /b /c:1 /d:abc /flag value1 value2"
    options = ("/a", "/b", "/c", "/d", "/flag")

    args, opts = extract_options(shlex.split(line), *options)
    assert args == ["command", "value1", "value2"]
    assert opts == {"a": True, "b": True, "c": "1", "d": "abc", "flag": True}


def test_extract_options_with_no_options():
    line = "command value1 value2"
    options = ("/a", "/b", "/flag")

    args, opts = extract_options(shlex.split(line), *options)
    assert args == ["command", "value1", "value2"]
    assert opts == {}


def test_extract_options_with_some_options():
    line = "command /a value1 /flag value2"
    options = ("/a", "/b", "/flag")

    args, opts = extract_options(shlex.split(line), *options)
    assert args == ["command", "value1", "value2"]
    assert opts == {"a": True, "flag": True}


def test_extract_options_case_insensitive():
    line = "command /A /B /FLAG value1 value2"
    options = ("/a", "/b", "/flag")

    args, opts = extract_options(shlex.split(line), *options)
    assert args == ["command", "value1", "value2"]
    assert opts == {"a": True, "b": True, "flag": True}


def test_extract_options_with_unexpected_options():
    line = "command /x /y value1 value2"
    options = ("/a", "/b", "/flag")

    args, opts = extract_options(shlex.split(line), *options)
    assert args == ["command", "/x", "/y", "value1", "value2"]
    assert opts == {}
