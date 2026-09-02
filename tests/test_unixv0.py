import pytest

from xferx.commons import ASCII
from xferx.pdp7.unix0fs import (
    V0_DD_INODE,
    V0_FIRST_INODE_BLOCK,
    V0_FREE_BLOCKS_LIST_SIZE,
    V0_INODE_BLOCKS,
    UNIX0Directory,
    UNIX0DirectoryEntry,
    UNIX0Filesystem,
    UNIX0FreeStorageMap,
    UNIX0Inode,
)
from xferx.shell import Shell

DSK = "tests/dsk/unixv0.dsk"


def test_unix0_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /unix0 {DSK}", batch=True)
    fs = shell.volumes.get_volume('T')
    assert isinstance(fs, UNIX0Filesystem)
    assert fs.version == 0
    assert fs.get_root() is not None
    assert fs.get_root().is_dir()
    assert fs.get_root().is_mount()

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir t:/", batch=True)
    shell.onecmd("dir t:/system/", batch=True)
    shell.onecmd("type t:/system/password", batch=True)

    x = shell.volumes.read_text("t:dd/data/9k")
    assert x.startswith("|")

    l = list(fs.entries_list)
    filenames = [x.filename for x in l if not x.is_empty()]
    assert "dd" in filenames
    assert "system" in filenames

    entry = shell.volumes.get_file_entry("t:/test/a")
    assert not entry.inode.is_large()

    entry = shell.volumes.get_file_entry("t:/test/b")
    assert not entry.inode.is_large()

    entry = shell.volumes.get_file_entry("t:/test/c")
    assert entry.inode.is_large()


def test_unix0_write():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /unix0 {DSK}.mo", batch=True)
    fs = shell.volumes.get_volume('OU')

    i = fs.get_inode("/")
    assert i.inode_num == V0_DD_INODE
    i2 = fs.read_inode(i.inode_num)
    assert i == i2
    i2.write()
    i3 = fs.read_inode(i.inode_num)
    assert i3 == i2

    shell.onecmd("copy/ascii tests/dsk/data/10.txt ou:/data/10.txt", batch=True)
    shell.onecmd("copy/ascii tests/dsk/data/100.txt ou:/data/100.txt", batch=True)
    shell.onecmd("copy/ascii tests/dsk/data/10.txt ou:/data/11.txt", batch=True)
    x = shell.volumes.read_bytes("ou:/data/100.txt", ASCII)
    x = x.rstrip(b"\0")
    assert len(x) == 4400
    for i in range(0, 100):
        assert f"{i:5d} ABCDEFGHIJKLMNOPQRSTUVWXYZ01234567890".encode("ascii") in x

    # Resize a small file
    i = fs.get_inode("/data/10.txt")
    assert not i.is_large()
    size = i.size
    length = i.get_length()
    with shell.volumes.open_file("ou:/data/10.txt") as f:
        f.truncate(size + 100)
    i = fs.get_inode("/data/10.txt")
    assert not i.is_large()
    assert i.get_length() > length
    assert i.size == size + 100

    # Resize a small file to a large size
    i = fs.get_inode("/data/10.txt")
    assert not i.is_large()
    size = i.size
    with shell.volumes.open_file("ou:/data/10.txt") as f:
        f.truncate(size + 500)
    i = fs.get_inode("/data/10.txt")
    assert i.is_large()
    assert i.size == size + 500

    # Resize a large file
    i = fs.get_inode("/data/100.txt")
    assert i.is_large()
    size = i.size
    length = i.get_length()
    with shell.volumes.open_file("ou:/data/100.txt") as f:
        f.truncate(size + 200)
    i = fs.get_inode("/data/100.txt")
    assert i.is_large()
    assert i.get_length() > length
    assert i.size == size + 200

    # Shrink a small file
    i = fs.get_inode("/data/11.txt")
    assert not i.is_large()
    size = i.size
    length = i.get_length()
    with shell.volumes.open_file("ou:/data/11.txt") as f:
        f.truncate(size - 100)
    i = fs.get_inode("/data/11.txt")
    assert i.get_length() < length
    assert not i.is_large()
    assert i.size == size - 100

    # Shrink a large file
    i = fs.get_inode("/data/100.txt")
    assert i.is_large()
    size = i.size
    length = i.get_length()
    with shell.volumes.open_file("ou:/data/100.txt") as f:
        f.truncate(size - 200)
    i = fs.get_inode("/data/100.txt")
    assert i.is_large()
    assert i.get_length() < length
    assert i.size == size - 200


def test_unix0_free_storage_map():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /unix0 {DSK}.mo", batch=True)
    fs = shell.volumes.get_volume('OU')

    fsm = UNIX0FreeStorageMap.read(fs)
    for i in range(V0_FIRST_INODE_BLOCK, V0_FIRST_INODE_BLOCK + V0_INODE_BLOCKS):  # Blocks 2 to 711 contain the inodes
        assert not fsm.is_free(i)

    fsm2 = UNIX0FreeStorageMap.read(fs)
    assert fsm2 == fsm
    assert fsm2.used() == fsm.used()
    tmp = fsm2.allocate(10)
    assert fsm2 != fsm
    assert fsm2.used() == fsm.used() + 10
    assert fsm2.free() == fsm.free() - 10
    fsm2.write()

    fsm3 = UNIX0FreeStorageMap.read(fs)
    for block_number in tmp:
        assert not fsm3.is_free(block_number)
        fsm3.set_free(block_number)
    fsm3.write()
    assert fsm3.used() == fsm.used()
    assert fsm3.free() == fsm.free()

    # Free some blocks from a file
    fsm4 = UNIX0FreeStorageMap.read(fs)
    used = fsm4.used()
    free = fsm4.free()
    entry = fs.get_file_entry("/data/9k")
    count = 0
    for block_number in entry.inode.blocks(include_indexes=True):
        count += 1
        assert not fsm4.is_free(block_number)
        fsm4.set_free(block_number)
    # Check the map after freeing blocks
    assert fsm4.used() == used - count
    assert fsm4.free() == free + count
    fsm4.write()
    # Recheck after writing
    d = count // V0_FREE_BLOCKS_LIST_SIZE + 1
    assert fsm4.used() > used - count - d
    assert fsm4.free() < free + count + d

    # Read again to ensure the map is still correct
    fsm5 = UNIX0FreeStorageMap.read(fs)
    assert fsm5.used() > used - count - d
    assert fsm5.free() < free + count + d


def test_unix0_delete():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /unix0 {DSK}.mo", batch=True)
    fs = shell.volumes.get_volume('OU')
    fsm = UNIX0FreeStorageMap.read(fs)
    used = fsm.used()
    free = fsm.free()
    entry = fs.get_file_entry("/data/9k")
    count = len(list(entry.inode.blocks(include_indexes=True)))
    shell.onecmd("delete ou:/data/9k", batch=True)
    fsm = UNIX0FreeStorageMap.read(fs)
    d = count // V0_FREE_BLOCKS_LIST_SIZE + 1
    assert fsm.used() > used - count - d
    assert fsm.free() < free + count + d

    # Try to delete a non-empty directory
    with pytest.raises(Exception):
        shell.onecmd("delete ou:/data/", batch=True)

    # Delete a file
    shell.onecmd("delete ou:/data/38k", batch=True)
    # Delete a directory
    shell.onecmd("delete ou:/data/", batch=True)


def test_unix0_inode():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /unix0 {DSK}.mo", batch=True)
    fs = shell.volumes.get_volume('OU')

    inode0 = UNIX0Inode.allocate(fs, number_of_blocks=1, size=0)
    inode1 = UNIX0Inode.allocate(fs, number_of_blocks=1, size=0)
    assert inode0.inode_num != inode1.inode_num
    inode2 = UNIX0Inode.allocate(fs, number_of_blocks=1, size=0)
    assert inode1.inode_num != inode2.inode_num
    inode2.delete()
    inode3 = UNIX0Inode.allocate(fs, number_of_blocks=1, size=0)
    assert inode3.inode_num == inode2.inode_num


def test_unix0_dir():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /unix0 {DSK}.mo", batch=True)
    fs = shell.volumes.get_volume('OU')

    dir0 = UNIX0Directory.read(fs, fs.get_file_entry("/test/").inode)
    assert dir0.inode.is_dir()
    assert len(dir0.entries) == 7
    dir0.write()

    dir1 = UNIX0Directory.read(fs, fs.get_file_entry("/test/").inode)
    assert len(dir1.entries) == 7
    assert dir0.entries == dir1.entries

    dir_entry = fs.create_directory("/test/subdir", {})
    assert dir_entry.inode.is_dir()
    dir_entry.inode.nlinks += 1
    dir_entry.inode.write()
    assert dir_entry.inode.is_dir()
    dir_entry.inode.nlinks -= 1
    dir_entry.inode.write()
    assert dir_entry.inode.is_dir()

    count = 7
    inode0 = UNIX0Inode.allocate(fs, number_of_blocks=1, size=0)
    assert inode0.inode_num != dir_entry.inode.inode_num
    for i in range(0, count):
        UNIX0DirectoryEntry.link(fs, parent=dir_entry, filename=f"{i}", inode=inode0)

    dir_inode = fs.get_file_entry("/test/subdir").inode
    assert dir_inode.inode_num == dir_entry.inode.inode_num
    assert dir_inode.is_dir()
    dir3 = UNIX0Directory.read(fs, fs.get_file_entry("/test/subdir").inode)
    assert len(dir3.entries) == 3 + count
