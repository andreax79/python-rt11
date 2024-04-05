from datetime import date

import pytest

from rt11 import (
    asc2rad,
    bytes_to_word,
    date_to_rt11,
    rad2asc,
    rt11_canonical_filename,
    rt11_to_date,
    word_to_bytes,
)


def test_bytes_to_word():
    # Test with valid input
    assert bytes_to_word(b'\x01\x00') == 1
    assert bytes_to_word(b'\xff\xff') == 65535
    assert bytes_to_word(b'\x01\xab\xcd', position=1) == 52651
    # Test with out of bounds position
    with pytest.raises(IndexError):
        bytes_to_word(b'\x01\x02', position=2)


def test_word_to_bytes():
    # Test with valid input
    assert word_to_bytes(1) == b'\x01\x00'
    assert word_to_bytes(65535) == b'\xFF\xFF'
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
    assert rad2asc(b'\x01\x00') == "A"
    assert rad2asc(b'\x06\x01') == "FV"
    assert rad2asc(b'\x00\x00') == ""
    # Test with different positions
    assert rad2asc(b'\x10\x37\x31\x43\x74', position=0) == "H2P"
    assert rad2asc(b'\x10\x37\x31\x43\x74', position=2) == "J0A"
    # Test with all zeros
    assert rad2asc(b'\x00\x00\x00') == ""


def test_asc2rad():
    # Test with valid input
    assert asc2rad("ABC") == b'\x93\x06'
    assert asc2rad("Z12") == b'x\xa7'
    assert asc2rad("") == b'\x00\x00'
    # Test with lowercase characters
    assert asc2rad("zia") == b'\xe9\xa3'
    assert asc2rad(":$.") == b'\x54\xfe'


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
