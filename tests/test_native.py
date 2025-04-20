from xferx.native import NativeFilesystem
from xferx.shell import Shell


def test_native():
    shell = Shell(verbose=True)
    fs = shell.volumes.get("DK")
    assert isinstance(fs, NativeFilesystem)

    shell.onecmd("dir dk:", batch=True)
    shell.onecmd("type LICENSE", batch=True)

    with open("t.tmp", "w"):
        pass

    e = fs.read_bytes("t.tmp")
    assert len(e) == 0

    f = fs.open_file("t.tmp")

    x = fs.read_bytes("LICENSE")
    assert x
    for line in x.split(b"\n"):
        f.write(line)
        f.write(b"\n")
    f.truncate(len(x))
    f.close()

    x2 = fs.read_bytes("t.tmp")
    assert x == x2

    shell.onecmd("delete t.tmp", batch=True)
