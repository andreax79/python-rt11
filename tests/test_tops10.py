from xferx.pdp10.tops10fs import (
    TOPS10Filesystem,
    MasterFileDirectory,
    decode_byte_pointer,
    left_half,
    right_half,
    sixbit_to_ascii,
)
from xferx.device.block_36bit import BlockDevice36Bit
from xferx.shell import Shell

DSK = "tests/dsk/tops10.dsk.gz"


def test_tops10_sixbit():
    assert sixbit_to_ascii(0o446353422000) == "DSKB0"
    assert left_half(0o123456701234) == 0o123456
    assert right_half(0o123456701234) == 0o701234


def test_tops10_byte_pointer():
    word = (0o123 << 30) | (0o12 << 24)
    assert decode_byte_pointer(word) == (0o123, 0o12)


def test_tops10_mfd_entries():
    entries = list(MasterFileDirectory.parse_mfd_entries([0o1000001, (0o654644 << 18) | 0o12345, 0, 0]))
    assert len(entries) == 1
    assert str(entries[0].ppn) == "[1,1]"
    assert entries[0].extension == "UFD"
    assert entries[0].cfp == 0o12345


def test_tops10_filesystem_mfd_reader():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /tops10 {DSK}", batch=True)
    fs = shell.volumes.get_volume("T")
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()
    entries = list(fs.get_root().iterdir())
    assert entries
    assert str(entries[0].ppn) == "[1,1]"


def test_tops10_home_block():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /tops10 {DSK}", batch=True)
    fs = shell.volumes.get_volume("T")
    assert isinstance(fs, TOPS10Filesystem)
    assert isinstance(fs.dev, BlockDevice36Bit)
    assert fs.home_block == 1
    home = fs.read_home()
    assert home.homnam == "HOM"
    assert home.homsnm == "XFER"
    assert home.hommfd == fs.mfd_block


def test_tops10():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /tops10 {DSK}", batch=True)
    shell.onecmd(f"dir t:[1,2]", batch=True)
    shell.onecmd(f"dir t:[1,4]", batch=True)
    shell.onecmd(f"dir t:[3,3]", batch=True)
    shell.onecmd(f"dir t: /uic", batch=True)
