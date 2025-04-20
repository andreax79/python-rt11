import pytest

from xferx.native import NativeFilesystem
from xferx.pdp11.rt11fs import RT11Filesystem
from xferx.shell import Shell


def test_help():
    shell = Shell(verbose=True)
    # Help
    shell.onecmd("HELP", batch=True)
    shell.onecmd("HELP HELP", batch=True)
    shell.onecmd("HELP *", batch=True)


def test_assign():
    shell = Shell(verbose=True)
    # Assign/Deassign
    with pytest.raises(Exception):
        shell.onecmd("ASSIGN XX: T1:", batch=True)
    shell.onecmd("ASSIGN SY: T1:", batch=True)
    shell.onecmd("ASSIGN SY: T2:", batch=True)
    shell.onecmd("ASSIGN T2: T3:", batch=True)
    shell.onecmd("DIR /BRIEF T3:", batch=True)
    shell.onecmd("DEASSIGN T3:", batch=True)
    with pytest.raises(Exception):
        shell.onecmd("DIR /BRIEF T3:", batch=True)
    with pytest.raises(Exception):
        shell.onecmd("DEASSIGN T3:", batch=True)


def test_cmds():
    shell = Shell(verbose=True)
    sys_fs = shell.volumes.get('DK')
    license = sys_fs.read_bytes("LICENSE")
    with pytest.raises(FileNotFoundError):
        sys_fs.read_bytes("not found")
    assert license
    assert isinstance(sys_fs, NativeFilesystem)
    assert len(list(sys_fs.entries_list)) > 0
    # Open file
    with pytest.raises(FileNotFoundError):
        sys_fs.open_file("LI")
    f = sys_fs.open_file("LICENSE")
    t = f.read_block(block_number=0, number_of_blocks=1000)
    assert f.size == len(t)
    f.close()
    # Create ad initialize the RT-11 filesystem
    shell.onecmd("CREATE test0.dsk /allocate:500", batch=True)
    shell.onecmd("INITIALIZE /RT11 test0.dsk", batch=True)
    shell.onecmd("MOUNT T: test0.dsk", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, RT11Filesystem)
    with pytest.raises(FileNotFoundError):
        fs.read_bytes("not found")
    l0 = len(list(fs.entries_list))
    # Copy a file
    shell.onecmd("COPY LICENSE T:", batch=True)
    assert len(list(fs.entries_list)) == l0 + 1
    license_rt11 = fs.read_bytes("LICENSE").rstrip(b'\0')
    assert license == license_rt11
    # Open file
    with pytest.raises(FileNotFoundError):
        fs.open_file("LI")
    f = fs.open_file("LICENSE")
    t = f.read_block(block_number=0, number_of_blocks=1000)
    assert f.size == len(t)
    f.close()
    # Create a disk in the RT11 fs
    shell.onecmd("T:", batch=True)
    shell.onecmd("EXAMINE T:", batch=True)
    with pytest.raises(OSError):
        shell.onecmd("CREATE /file test1.dsk /allocate:1000", batch=True)
    shell.onecmd("CREATE /file test1.dsk /allocate:100", batch=True)
    shell.onecmd("MOUNT T1: test1.dsk", batch=True)
    shell.onecmd("EXAMINE T1:", batch=True)
    shell.onecmd("INITIALIZE T1:", batch=True)
    shell.onecmd("DIR T1:*.*", batch=True)
    shell.onecmd("COPY T:L*.* T1:", batch=True)
    shell.onecmd("DISMOUNT T1:", batch=True)
    shell.onecmd("T:", batch=True)
    shell.onecmd("DEL test1.dsk", batch=True)
    # Misc commands
    shell.onecmd("PWD", batch=True)
    shell.onecmd("SHOW", batch=True)
    shell.onecmd("DIR", batch=True)
    shell.onecmd("EXAMINE T:", batch=True)
    shell.onecmd("EXAMINE T:LICENSE", batch=True)
    shell.onecmd("DUMP /start:6 /end:6 T:", batch=True)
    shell.onecmd("TYPE T:LICENSE", batch=True)
    # Delete the file
    shell.onecmd("DEL LICENSE", batch=True)
    assert len(list(fs.entries_list)) == l0
    shell.onecmd("SY:", batch=True)
    # Copy/delete multiple files
    shell.onecmd("COPY py* T:", batch=True)
    shell.onecmd("DELETE T:py*", batch=True)
    # Delete the test disk
    shell.onecmd("DISMOUNT T:", batch=True)
    shell.onecmd("DEL test0.dsk", batch=True)
