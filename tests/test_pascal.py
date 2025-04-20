import random
from datetime import date, timedelta

import pytest

from xferx.apple2.pascalfs import PascalFilesystem, date_to_pascal, pascal_to_date
from xferx.shell import Shell

DSK = "tests/dsk/pascal.dsk"


def test_date_round_trip():
    start_year = 1980
    end_year = 2020
    for i in range(0, 100):
        start_date = date(start_year, 1, 1)
        end_date = date(end_year, 12, 31)
        random_seconds = random.randint(0, int((end_date - start_date).total_seconds()))
        random_seconds = random_seconds - (random_seconds % 60)
        dt = start_date + timedelta(seconds=random_seconds)
        assert pascal_to_date(date_to_pascal(dt)) == dt


def test_pascal():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /pascal {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, PascalFilesystem)

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir/brief t:", batch=True)
    shell.onecmd("dir/brief t:*.txt", batch=True)
    shell.onecmd("dir/brief t:*.notfound", batch=True)
    shell.onecmd("type t:1.txt", batch=True)

    x = fs.read_bytes("50.txt")
    x = x.rstrip(b"\0")
    assert len(x) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    l = list(fs.filter_entries_list("*.TXT"))
    assert len(l) == 10
    for x in l:
        assert not x.is_empty
        assert str(x)


def test_pascal_init():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /pascal {DSK}", batch=True)
    shell.onecmd(f"create /allocate:280 {DSK}.mo", batch=True)
    shell.onecmd(f"init /pascal {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /pascal {DSK}.mo", batch=True)
    shell.onecmd("dir ou:", batch=True)
    shell.onecmd("copy t:*.txt ou:", batch=True)
    fs = shell.volumes.get('OU')

    x1 = fs.read_bytes("50.txt")
    x1 = x1.rstrip(b"\0")
    assert len(x1) == 2200
    for i in range(0, 50):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x1

    with pytest.raises(Exception):
        shell.onecmd("delete ou:aaa", batch=True)
    shell.onecmd("delete ou:50.txt", batch=True)
    assert fs.read_bytes("10.txt")
    with pytest.raises(FileNotFoundError):
        fs.read_bytes("50.txt")

    # Test init mounted volume
    shell.onecmd("init ou:", batch=True)
    with pytest.raises(Exception):
        print(fs.read_bytes("10.tx"))
