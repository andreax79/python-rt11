from xferx.pdp7.unix0fs import (
    V0_FIRST_INODE_BLOCK,
    V0_INODE_BLOCKS,
    V0_ROOT_INODE,
    UNIX0Filesystem,
    UNIX0FreeStorageMap,
)
from xferx.shell import Shell

DSK = "tests/dsk/unixv0.dsk"


def test_unix0_read():
    shell = Shell(verbose=True)
    shell.onecmd(f"mount t: /unix0 {DSK}", batch=True)
    fs = shell.volumes.get('T')
    assert isinstance(fs, UNIX0Filesystem)
    assert fs.version == 0

    shell.onecmd("dir t:", batch=True)
    shell.onecmd("dir t:/", batch=True)
    shell.onecmd("dir t:/system/", batch=True)
    shell.onecmd("type t:/system/password", batch=True)

    x = fs.read_text("dd/data/9k")
    assert x.startswith("|")

    l = list(fs.entries_list)
    filenames = [x.filename for x in l if not x.is_empty]
    assert "dd" in filenames
    assert "system" in filenames

    entry = fs.get_file_entry("/test/a")
    assert not entry.inode.is_large

    entry = fs.get_file_entry("/test/b")
    assert not entry.inode.is_large

    entry = fs.get_file_entry("/test/c")
    assert entry.inode.is_large


def test_unix0_write():
    shell = Shell(verbose=True)
    shell.onecmd(f"copy {DSK} {DSK}.mo", batch=True)
    shell.onecmd(f"mount ou: /unix0 {DSK}.mo", batch=True)
    fs = shell.volumes.get('OU')

    i = fs.get_inode("/")
    assert i.inode_num == V0_ROOT_INODE
    i2 = fs.read_inode(i.inode_num)
    assert i == i2
    i2.write()
    i3 = fs.read_inode(i.inode_num)
    assert i3 == i2

    free_block_list = UNIX0FreeStorageMap.read(fs)
    for i in range(V0_FIRST_INODE_BLOCK, V0_FIRST_INODE_BLOCK + V0_INODE_BLOCKS):  # Blocks 2 to 711 contain the inodes
        assert not free_block_list.is_free(i)
        # free_block_list.set_free(i)
    # print(free_block_list.free_blocks)
    print(f"{free_block_list.used()} blocks used {free_block_list.free()} blocks free")

    free_block_list.write()

    free_block_list2 = UNIX0FreeStorageMap.read(fs)
    assert free_block_list2 == free_block_list
    assert free_block_list2.used() == free_block_list.used()
    tmp = free_block_list2.allocate(10)
    assert free_block_list2 != free_block_list
    assert free_block_list2.used() == free_block_list.used() + 10
    assert free_block_list2.free() == free_block_list.free() - 10
    free_block_list2.write()

    free_block_list3 = UNIX0FreeStorageMap.read(fs)
    for block_number in tmp:
        assert not free_block_list3.is_free(block_number)
        free_block_list3.set_free(block_number)
    free_block_list3.write()
    assert free_block_list3.used() == free_block_list.used()
    assert free_block_list3.free() == free_block_list.free()
