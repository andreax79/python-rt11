# Copyright (C) 2414 Andrea Bonomi <andrea.bonomi@gmail.com>

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import errno
import io
import math
import os
import sys
import typing as t
from functools import reduce

from ..abstract import AbstractFile
from ..commons import ASCII, IMAGE, READ_FILE_FULL
from ..device.abstract import AbstractDevice
from ..device.block_18bit import (
    BYTES_PER_WORD_18BIT,
    BlockDevice18Bit,
    from_18bit_words_to_bytes,
    from_bytes_to_18bit_words,
)
from ..unix.commons import (
    Dirent,
    UNIXDirectory,
    UNIXDirectoryEntry,
    UNIXFile,
    UNIXFilesystem,
    UNIXInode,
    unix_join,
    unix_split,
)

__all__ = [
    "UNIX0File",
    "UNIX0DirectoryEntry",
    "UNIX0Filesystem",
]

V0_SUPER_BLOCK = 0  # Superblock

V0_IO_BYTES_PER_WORD = 3  # When files are exported, each word is encoded in 3 bytes
V0_WORDS_PER_BLOCK = 64  # Number of words per block
V0_BLOCK_SIZE = BYTES_PER_WORD_18BIT * V0_WORDS_PER_BLOCK  # Block size (in bytes)

V0_BLOCKS_PER_SURFACE = 8000  # Number of blocks on a surface
V0_FILESYSTEM_SIZE = 6400  # Number of blocks in the filesystem
V0_INODE_BLOCKS = 710  # Number of i-node blocks
V0_FIRST_INODE_BLOCK = 2  # First i-node block number
V0_INODE_SIZE = 12  # Inode size (in words)
V0_INODES_PER_BLOCK = V0_WORDS_PER_BLOCK // V0_INODE_SIZE  # Number of inodes per block
V0_MAX_INODES = V0_INODE_BLOCKS * V0_INODES_PER_BLOCK  # Total number of inodes
V0_DIRENT_SIZE = 8  # Size of a directory entry (in words)
V0_FILENAME_SIZE = 4  # Number of words in a file name
V0_FILENAME_LEN = 8  # Length of a file name (in characters)
V0_SURFACE_SIZE = V0_BLOCKS_PER_SURFACE * V0_WORDS_PER_BLOCK * BYTES_PER_WORD_18BIT
V0_FREE_BLOCKS_LIST_SIZE = 9  # Number of free blocks in a block (in words)

V0_MAXINT = 0o777777  # Biggest unsigned integer

V0_FLAGS = 0
V0_ADDR = 1
V0_UID = 8
V0_NLINKS = 9
V0_SIZE = 10
V0_UNIQ = 11

V0_NUMBLKS = 7  # Seven block pointers in i-node

V0_USED = 0o400000  # i-node is allocated
V0_LARGE = 0o200000  # large file (> 7 blocks)
V0_SPECIAL = 0o000040  # special file
V0_DIR = 0o000020  # directory

V0_ROWN = 0o000010  # read, owner
V0_WOWN = 0o000004  # write, owner
V0_ROTH = 0o000002  # read, non-owner
V0_WOTH = 0o000001  # write, non-owner

V0_DEFAULT_ACCESS = V0_ROWN | V0_WOWN | V0_ROTH | V0_WOTH

# Reserved inode numbers
V0_CORE_INODE = 1  # core file inode
V0_SYSTEM_INODE = 3  # 'system' directory
V0_DD_INODE = 4  # 'dd' directory
V0_TTYIN_INODE = 6  # 'ttyin' special file
V0_KEYBOARD_INODE = 7  # 'keyboard' GRAPHIC-2 keyboard special file
V0_PPTIN_INODE = 8  # 'pptin' paper tape reader special file
V0_TTYOUT_INODE = 11  # 'ttyout' special file
V0_DISPLAY_INODE = 12  # 'display' GRAPHIC-2 display special file
V0_PPTOUT_INODE = 13  # 'pptout' paper tape punch special file

V0_RESERVED_INODES = [
    V0_CORE_INODE,
    V0_SYSTEM_INODE,
    V0_DD_INODE,
    V0_TTYIN_INODE,
    V0_KEYBOARD_INODE,
    V0_PPTIN_INODE,
    V0_TTYOUT_INODE,
    V0_DISPLAY_INODE,
    V0_PPTOUT_INODE,
]


def get_v0_inode_block_offset(inode_num: int) -> t.Tuple[int, int]:
    """
    Return block number and offset for an inode number
    """
    block_num = V0_FIRST_INODE_BLOCK + (inode_num // V0_INODES_PER_BLOCK)
    offset = V0_INODE_SIZE * (inode_num % V0_INODES_PER_BLOCK)
    return block_num, offset


def get_v0_inode_num_from_block_offset(block_number: int, offset: int) -> int:
    """
    Return inode number from block number and offset
    """
    if block_number < V0_FIRST_INODE_BLOCK or block_number >= V0_FIRST_INODE_BLOCK + V0_INODE_BLOCKS:
        raise OSError(errno.EINVAL, os.strerror(errno.EINVAL), "Invalid inode block number")
    if offset < 0 or offset >= V0_WORDS_PER_BLOCK:
        raise OSError(errno.EINVAL, os.strerror(errno.EINVAL), "Invalid inode offset")
    inode_num = (block_number - V0_FIRST_INODE_BLOCK) * V0_INODES_PER_BLOCK + (offset // V0_INODE_SIZE)
    return inode_num


def logical_to_physical_block_number(block_number: int) -> int:
    """
    Convert a logical block number to a physical block number.

    The Unix filesystem is stored in the first 80 tracks of the second surface
    (physical blocks 8000 to 14399) and numbers them from logical block 0 to 6399
    """
    return (block_number + V0_BLOCKS_PER_SURFACE) % (2 * V0_BLOCKS_PER_SURFACE)


class UNIX0File(UNIXFile):
    inode: "UNIX0Inode"

    def __init__(self, inode: "UNIX0Inode", file_mode: t.Optional[str] = None):
        super().__init__(inode)
        self.file_mode = file_mode or IMAGE

    def read_words_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> t.List[int]:
        """
        Read block(s) of words from the file
        """
        if number_of_blocks == READ_FILE_FULL:
            number_of_blocks = self.inode.get_length()
        if (
            self.closed
            or block_number < 0
            or number_of_blocks < 0
            or block_number + number_of_blocks > self.inode.get_length()
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        words: t.List[int] = []
        # Get the blocks to be read
        blocks = list(self.inode.blocks())[block_number : block_number + number_of_blocks]
        # Read the blocks
        for disk_block_number in blocks:
            words.extend(self.inode.fs.read_words_block(disk_block_number))
        return words

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        """
        Read block(s) of data from the file
        """
        words = self.read_words_block(block_number, number_of_blocks)
        return from_18bit_words_to_bytes(words, self.file_mode)

    def write_words_block(
        self,
        words: t.List[int],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        """
        if (
            self.closed or block_number < 0 or number_of_blocks < 0
            # or block_number + number_of_blocks > self.inode.get_length()
        ):
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        if block_number + number_of_blocks > self.inode.get_length():
            self.truncate((block_number + number_of_blocks) * V0_WORDS_PER_BLOCK)
        # Get the blocks to be written
        blocks = list(self.inode.blocks())[block_number : block_number + number_of_blocks]
        # Write the blocks
        for i, disk_block_number in enumerate(blocks):
            tmp = words[i * V0_WORDS_PER_BLOCK : (i + 1) * V0_WORDS_PER_BLOCK]
            tmp = tmp + [0] * (V0_WORDS_PER_BLOCK - len(tmp))  # Pad to block size
            self.inode.fs.write_words_block(disk_block_number, tmp)

    def write_block(
        self,
        buffer: t.Union[bytes, bytearray],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        """
        Write block(s) of data to the file
        """
        words = from_bytes_to_18bit_words(buffer, self.file_mode)
        self.write_words_block(words, block_number, number_of_blocks)

    def get_size(self) -> int:
        """
        Get file size in bytes
        """
        if self.file_mode == ASCII:
            return self.inode.size * 2  # 2 ASCII bytes per 18 bit word
        else:
            return self.inode.size * V0_IO_BYTES_PER_WORD

    def truncate(self, size: t.Optional[int] = None) -> None:
        """
        Resize the file to the given number of words.
        If the size is not specified, the current position will be used.
        """
        if size is None:
            size = self.current_position
        self.inode.truncate(size)


class UNIX0Inode(UNIXInode):
    """
    Inode numbers begin at 1, and the storage for i-nodes begins at block 2.
    Blocks 2 to 711 contain the i-nodes, with five 12-word i-nodes per block.

    Reserved i-node numbers:

     1  core file
     3  "system" directory
     4  "dd" directory
     6  "ttyin" special file
     7  "keyboard" GRAPHIC-2 keyboard special file
     8  "pptin" paper tape reader special file
    11  "ttyout" special file
    12  "display" GRAPHIC-2 display special file
    13  "pptout" paper tape punch special file

    s3.s lines 227-229
    https://github.com/DoctorWkt/pdp7-unix/blob/master/man/fs.5 (i-node numbers are different)
    """

    fs: "UNIX0Filesystem"
    uniq: int  # Unique value assigned at creation
    inode_num: int  # Inode number
    flags: int  # Flags
    uid: int  # Owner user id
    nlinks: int  # Link count
    size: int  # Size (in words)
    addr: t.List[int]  # Indirect blocks or data blocks

    @classmethod
    def read(cls, fs: "UNIX0Filesystem", inode_num: int, words: t.List[int], position: int = 0) -> "UNIX0Inode":  # type: ignore
        self = UNIX0Inode(fs)
        self.inode_num = inode_num
        self.flags = words[position + V0_FLAGS]
        self.uid = words[position + V0_UID]  # Owner user id
        if self.uid == V0_MAXINT:
            self.uid = -1  # 'system' (root) uid
        self.nlinks = V0_MAXINT - words[position + V0_NLINKS] + 1  # Link count
        self.size = words[position + V0_SIZE]  # Size (in words)
        self.uniq = words[position + V0_UNIQ]  # Unique value assigned at creation
        self.addr = words[position + V0_ADDR : position + V0_ADDR + V0_NUMBLKS]  # Indirect blocks or data blocks
        return self

    @classmethod
    def allocate(
        cls, fs: "UNIX0Filesystem", number_of_blocks: int, size: int, flags: int = V0_DEFAULT_ACCESS
    ) -> "UNIX0Inode":
        """
        Allocate a new inode and the data blocks for it
        """
        # Create a new inode with default values
        self = UNIX0Inode(fs)
        self.inode_num = fs.find_free_inode_num()  # Find a free inode
        self.flags = V0_USED | flags  # Mark the inode as allocated
        self.uid = -1  # Owner user id
        self.nlinks = 0  # Link count
        self.size = 0  # Size (in words)
        self.uniq = self.inode_num  # Unique value assigned at creation - TODO
        self.addr = [0] * V0_NUMBLKS  # Indirect blocks or data blocks
        # Resize the file to the requested size
        self.truncate(size)
        # free_storage_map = UNIX0FreeStorageMap.read(fs)
        # if number_of_blocks <= V0_NUMBLKS:
        #     # Small file
        #     self.addr[:number_of_blocks] = free_storage_map.allocate(number_of_blocks)
        # else:
        #     # Large file
        #     self.flags |= V0_LARGE
        #     indirect_blocks = math.ceil(number_of_blocks / V0_NUMBLKS)
        #     if indirect_blocks > V0_NUMBLKS:
        #         # If the file is too large, raise an error
        #         raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), "File too large")
        #     self.addr[:indirect_blocks] = free_storage_map.allocate(indirect_blocks)
        #     # Allocate indirect blocks
        #     remaining_blocks = free_storage_map.allocate(number_of_blocks)
        #     for indirect_block_num in self.addr[:indirect_blocks]:
        #         blocks = remaining_blocks[:V0_WORDS_PER_BLOCK]
        #         remaining_blocks = remaining_blocks[V0_WORDS_PER_BLOCK:]
        #         words = blocks + [0] * (V0_WORDS_PER_BLOCK - len(blocks))  # Pad to block size
        #         fs.write_words_block(indirect_block_num, words)
        # free_storage_map.write()
        # self.write()
        # Write the inode to the filesystem
        return self

    def write(self) -> None:
        """
        Write inode
        """
        block_number, offset = get_v0_inode_block_offset(self.inode_num)
        words = self.fs.read_words_block(block_number)
        words[offset : offset + V0_INODE_SIZE] = self.to_words()
        self.fs.write_words_block(block_number, words)

    def to_words(self) -> t.List[int]:
        """
        Convert inode data to 18bit words
        """
        words = [0] * V0_INODE_SIZE
        words[V0_FLAGS] = self.flags
        if self.uid == -1:
            words[V0_UID] = V0_MAXINT
        else:
            words[V0_UID] = self.uid
        words[V0_NLINKS] = V0_MAXINT - self.nlinks + 1
        words[V0_SIZE] = self.size
        words[V0_UNIQ] = self.uniq
        words[V0_ADDR : V0_ADDR + V0_NUMBLKS] = self.addr
        return words

    def blocks(self, include_indexes: bool = False) -> t.Iterator[int]:
        """
        Iterate over the blocks of the inode
        If include_indexes is True, yield the block numbers as well
        """
        if self.is_large():
            # Large file
            for block_number in self.addr:
                if block_number == 0:
                    break
                if include_indexes:
                    yield block_number
                for n in self.fs.read_words_block(block_number):
                    if n == 0:
                        break
                    yield n
        else:
            # Small file
            for block_number in self.addr:
                if block_number == 0:
                    break
                yield block_number

    def truncate(self, number_of_words: int) -> None:
        """
        Resize the file to the given number of words.
        """
        if number_of_words < 0:
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))
        number_of_blocks = math.ceil(number_of_words / V0_WORDS_PER_BLOCK)  # Convert to block number
        current_blocks = self.get_length()  # Get the current number of blocks
        if number_of_blocks > current_blocks:
            # If the file is small but the requested size is larger than the small file size,
            # we need to convert it to a large file
            if number_of_blocks > V0_NUMBLKS and not self.is_large():
                self._convert_to_large()
            if self.is_large():
                self._grow_large_file(current_blocks, number_of_blocks)
            else:
                self._grow_small_file(current_blocks, number_of_blocks)
        elif current_blocks > number_of_blocks:
            if self.is_large():
                self._shrink_large_file(current_blocks, number_of_blocks)
            else:
                self._shrink_small_file(current_blocks, number_of_blocks)
        self.size = number_of_words  # Update the size of the inode
        self.write()  # Write the inode to the filesystem

    def _convert_to_large(self) -> None:
        """
        Convert a small file into a large one
        """
        if self.is_large():
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL), "File is already large")
        free_storage_map = UNIX0FreeStorageMap.read(self.fs)  # Read the free storage map
        self.flags |= V0_LARGE  # Mark as large file
        # Allocate 1 indirect blocks
        iblock = free_storage_map.allocate(1)[0]
        words = self.addr + [0] * (V0_NUMBLKS - len(self.addr))  # Pad to V0_NUMBLKS
        assert len(words) == V0_NUMBLKS
        self.addr[0] = iblock  # Set the first indirect block
        self.addr[1:] = [0] * (V0_NUMBLKS - 1)  # Clear the rest of the blocks
        self.write()  # Write the inode to the filesystem
        free_storage_map.write()  # Write the free storage map

    def _grow_small_file(self, current_blocks: int, desired_blocks: int) -> None:
        """
        Grow a small file to the requested size
        """
        if self.is_large():
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL), "Not a small file")
        if desired_blocks > V0_NUMBLKS:
            # If the file is too large for a small file, raise an error
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), "File too large")
        required_blocks = desired_blocks - current_blocks
        free_storage_map = UNIX0FreeStorageMap.read(self.fs)  # Read the free storage map
        blocks = free_storage_map.allocate(required_blocks)
        self.addr[current_blocks : current_blocks + required_blocks] = blocks
        free_storage_map.write()  # Write the free storage map

    def _grow_large_file(self, current_blocks: int, desired_blocks: int) -> None:
        """
        Grow a large file to the requested size
        """
        if not self.is_large():
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL), "Not a large file")
        required_blocks = desired_blocks - current_blocks
        free_storage_map = UNIX0FreeStorageMap.read(self.fs)  # Read the free storage map
        # Grow a large file to the requested size
        indirect_blocks = math.ceil(desired_blocks / V0_NUMBLKS)
        if indirect_blocks > V0_NUMBLKS:
            # If the file is too large, raise an error
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), "File too large")
        remaining_blocks = free_storage_map.allocate(required_blocks)
        for i in range(0, V0_NUMBLKS):
            if not remaining_blocks:
                break
            if self.addr[i] != 0:
                # Read the existing indirect block
                blocks = self.fs.read_words_block(self.addr[i])
            else:
                # Allocate a new indirect block
                self.addr[i] = free_storage_map.allocate(1)[0]
                blocks = [0] * V0_WORDS_PER_BLOCK  # Create a new block with zeroes
            for p in range(len(blocks)):
                if i * V0_NUMBLKS + p >= current_blocks:
                    # If we have reached the end of the inode, allocate new blocks
                    blocks[p] = remaining_blocks.pop(0)
                    # Fill the block with zeroes
                    self.fs.write_words_block(blocks[0], [0] * V0_WORDS_PER_BLOCK)
                    if not remaining_blocks:
                        break
            # Write the indirect block
            self.fs.write_words_block(self.addr[i], blocks)
        assert not remaining_blocks, "Not all blocks were allocated, something went wrong"
        free_storage_map.write()  # Write the free storage map

    def _shrink_small_file(self, current_blocks: int, desired_blocks: int) -> None:
        """
        Shrink a small file to the requested size
        """
        if self.is_large():
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL), "Not a small file")
        if desired_blocks < 0 or desired_blocks > current_blocks:
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL), "Invalid file size")
        if desired_blocks > V0_NUMBLKS:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), "File too large")
        free_storage_map = UNIX0FreeStorageMap.read(self.fs)  # Read the free storage map
        for i in range(0, V0_NUMBLKS):
            if i > desired_blocks - 1:
                if self.addr[i] != 0:
                    free_storage_map.set_free(self.addr[i])
                    self.addr[i] = 0
        free_storage_map.write()  # Write the free storage map

    def _shrink_large_file(self, current_blocks: int, desired_blocks: int) -> None:
        """
        Shrink a large file to the requested size
        """
        if not self.is_large():
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL), "Not a large file")
        if desired_blocks < 0 or desired_blocks > current_blocks:
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL), "Invalid file size")
        free_storage_map = UNIX0FreeStorageMap.read(self.fs)  # Read the free storage map

        for i in range(0, V0_NUMBLKS):
            if self.addr[i] == 0:
                continue  # Skip empty indirect blocks
            blocks = self.fs.read_words_block(self.addr[i])
            for p in range(len(blocks)):
                if i * V0_NUMBLKS + p > desired_blocks - 1 and blocks[p] != 0:
                    free_storage_map.set_free(self.addr[i])
                    blocks[p] = 0
            # Write the indirect block
            self.fs.write_words_block(self.addr[i], blocks)
        free_storage_map.write()  # Write the free storage map

    def delete(self) -> None:
        """
        Delete the inode and free the blocks
        """
        # Update the link count
        if self.nlinks > 0:
            self.nlinks = self.nlinks - 1
        # Delete the inode if link count is zero
        if self.nlinks == 0:
            if not self.is_special_file():
                # Free the blocks
                free_storage_map = UNIX0FreeStorageMap.read(self.fs)  # type: ignore
                for block_number in self.blocks(include_indexes=True):
                    free_storage_map.set_free(block_number)
                free_storage_map.write()
            # Mark the inode as not allocated
            self.flags = 0  # not allocated
        self.write()

    def is_dir(self) -> bool:
        return (self.flags & V0_DIR) == V0_DIR

    def is_special_file(self) -> bool:
        return (self.flags & V0_SPECIAL) == V0_SPECIAL

    def is_file(self) -> bool:
        return not self.is_dir()

    def is_large(self) -> bool:
        return bool(self.flags & V0_LARGE)

    def is_allocated(self) -> bool:
        return (self.flags & V0_USED) != 0

    def get_block_size(self) -> int:
        """
        Get block size in bytes
        """
        return V0_BLOCK_SIZE

    def get_size(self, fork: t.Optional[str] = None) -> int:
        """
        Get file size in bytes
        """
        return self.size * V0_IO_BYTES_PER_WORD

    def get_length(self, fork: t.Optional[str] = None) -> int:
        """
        Get the length in blocks
        """
        return len(list(self.blocks()))

    def examine(self) -> str:
        buf = io.StringIO()
        buf.write("\n*Inode\n")
        buf.write(f"Inode number:          {self.inode_num:>6}\n")
        buf.write(f"Uniq:                  {self.uniq:>6}\n")
        buf.write(f"Flags:                 {self.flags:>06o}\n")
        if self.is_dir():
            buf.write("Type:               directory\n")
        elif self.is_special_file():
            buf.write("Type:            special file\n")
        elif self.is_large():
            buf.write("Type:              large file\n")
        else:
            buf.write("Type:                    file\n")
        buf.write(f"Owner user id:         {self.uid:>6}\n")
        buf.write(f"Link count:            {self.nlinks:>6}\n")
        buf.write(f"Size (words):          {self.size:>6}\n")
        if self.is_large():
            buf.write(f"Indirect blocks:       {self.addr}\n")
        buf.write(f"Blocks:                {list(self.blocks())}\n")
        return buf.getvalue()

    def __str__(self) -> str:
        if not self.is_allocated():
            return f"{self.inode_num:>4}# ---"
        else:
            return f"{self.inode_num:>4}# uid: {self.uid:>3}  nlinks: {self.nlinks:>3}  size: {self.size:>5} words  flags: {self.flags:o}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UNIX0Inode):
            return False
        return (
            self.inode_num == other.inode_num
            and self.uniq == other.uniq
            and self.flags == other.flags
            and self.uid == other.uid
            and self.nlinks == other.nlinks
            and self.size == other.size
            and self.addr == other.addr
        )


class UNIX0DirectoryEntry(UNIXDirectoryEntry):
    inode: "UNIX0Inode"

    def dirent(self) -> "Dirent0":
        """
        Return a Dirent object for this entry
        """
        return Dirent0(self.inode.inode_num, self.name, self.inode.uniq)

    def delete(self) -> bool:
        """
        Delete the file
        """
        if self.inode.is_dir():
            # Check if the directory is empty
            inodes = [V0_DD_INODE, V0_SYSTEM_INODE, self.inode_num]
            not_empty = any(x for x in self.fs.list_dir(self.inode) if x.inode_num not in inodes)
            if not_empty:
                raise OSError(errno.ENOTEMPTY, os.strerror(errno.ENOTEMPTY), self.dirname)
        # Delete the directory entry
        if self.unlink():
            # Delete the inode if link count is zero
            self.inode.delete()
            return True
        else:
            return False

    def open(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> UNIX0File:
        """
        Open the file
        """
        return UNIX0File(self.inode, file_mode)


class Dirent0(Dirent):
    """
    Directory entry for UNIX version 0 filesystem
    """

    def __init__(self, inode_num: int, filename: str, unique: int):
        super().__init__(inode_num, filename)
        self.unique = unique

    @classmethod
    def read(cls, words: t.List[int], position: int) -> "Dirent0":
        inum = words[position]
        name = from_18bit_words_to_bytes(words[position + 1 : position + 1 + V0_FILENAME_SIZE])
        uniq = words[position + 1 + V0_FILENAME_SIZE]
        name_ascii = name.decode("ascii", errors="ignore").rstrip(" \x00")
        return Dirent0(inum, name_ascii, uniq)

    def to_words(self) -> t.List[int]:
        pname = self.filename.ljust(V0_FILENAME_LEN, " ").encode("ascii", errors="ignore")
        words = [0] * V0_DIRENT_SIZE
        words[0] = self.inode_num  # Inode number
        words[1 : V0_FILENAME_SIZE + 1] = from_bytes_to_18bit_words(pname[:V0_FILENAME_LEN])  # Filename
        words[V0_FILENAME_SIZE + 1] = self.unique  # Unique value
        return words

    def __str__(self) -> str:
        return f"{self.inode_num:>5} {self.filename} {self.unique:>5}"


class UNIX0Directory(UNIXDirectory):
    """
    Directory entries are 8 words long.

    Word

        +-----------------------------------+
      0 | i-node, 0 if the entry is empty   |
        +-----------------------------------+
      1 | Filename (8 characters)           |
        / (8 characters)                    /  4 words
      4 | space paddred on the right        |
        +-----------------------------------+
      5 | File unique value                 |
        +-----------------------------------+
      6 | Unused                            |
      7 |                                   |
        +-----------------------------------+

    https://github.com/DoctorWkt/pdp7-unix/blob/master/man/dir.5
    """

    @classmethod
    def read(cls, fs: "UNIXFilesystem", inode: "UNIXInode") -> "UNIXDirectory":
        """
        Read the directory
        """
        if not inode.is_dir():
            raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR))
        with UNIX0File(inode) as f:  # type: ignore
            words = f.read_words_block(0, READ_FILE_FULL)
        self = UNIX0Directory(fs, inode)
        self.entries = []
        # Read the directory entries
        for position in range(0, len(words), V0_DIRENT_SIZE):
            entry = Dirent0.read(words, position)
            if entry.inode_num > 0:
                self.entries.append(entry)
        return self

    @classmethod
    def create(cls, fs: "UNIX0Filesystem", access: int = V0_DEFAULT_ACCESS) -> "UNIX0Directory":
        """
        Create a new directory
        """
        inode = UNIX0Inode.allocate(fs, 1, V0_WORDS_PER_BLOCK, flags=V0_DIR | access)
        self = UNIX0Directory(fs, inode)
        self.entries = [
            Dirent0(V0_DD_INODE, "dd", V0_DD_INODE),  # "dd" directory
            Dirent0(inode.inode_num, "..", inode.uniq),  # Current directory
            Dirent0(V0_SYSTEM_INODE, "system", V0_SYSTEM_INODE),  # "system" directory
        ]
        # TODO increment link count for "dd" and "system"
        # TODO add a link to this directory in "dd"
        # Write the empty directory to the filesystem
        self.write()
        return self

    def write(self) -> None:
        """
        Write the directory
        """
        if not self.inode.is_dir():
            raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR))
        # Prepare the directory entries
        words = []
        dirent: Dirent0
        for dirent in self.entries:  # type: ignore
            if dirent.inode_num > 0:
                words.extend(dirent.to_words())
        # Pad to the block size
        num_of_blocks = math.ceil(len(words) / V0_WORDS_PER_BLOCK)
        words = words + [0] * (V0_WORDS_PER_BLOCK * num_of_blocks - len(words))
        # Write the directory entries
        with UNIX0File(self.inode) as f:  # type: ignore
            f.write_words_block(words, 0, num_of_blocks)


class UNIX0FreeStorageMap:
    """
    Free-Storage Map

    Each block in the free-storage map is structured as follows:

    - the first word is the block number of the next block
      in the free-storage map, or zero if this is the end of
      the free-storage map;
    - the next 9 words hold free block numbers,
      or 0 (no block number).
    """

    fs: "UNIX0Filesystem"
    map_blocks: t.List[int]  # List of Free Storage Map blocks
    free_blocks: t.List[int]  # List of free blocks

    def __init__(self, fs: "UNIX0Filesystem"):
        self.fs = fs
        self.map_blocks = []
        self.free_blocks = []

    @classmethod
    def read(cls, fs: "UNIX0Filesystem") -> "UNIX0FreeStorageMap":
        self = UNIX0FreeStorageMap(fs)
        block_number = fs.storage_map_block
        while block_number != 0:
            self.map_blocks.append(block_number)
            words = fs.read_words_block(block_number)
            block_number = words[0]
            self.free_blocks.extend(words[1 : V0_FREE_BLOCKS_LIST_SIZE + 1])
        return self

    def write(self) -> None:
        # Check if the map blocks are enough
        required_map_blocks = math.ceil(len(self.free_blocks) / V0_FREE_BLOCKS_LIST_SIZE)
        for _ in range(len(self.map_blocks), required_map_blocks):
            # Allocate a new block for the map
            self.map_blocks.append(self.allocate(1)[0])
        tmp = list(self.free_blocks)
        for i, map_block_number in enumerate(self.map_blocks):
            # Write the map block
            words = [0] * V0_WORDS_PER_BLOCK
            words[0] = self.map_blocks[i + 1] if i + 1 < len(self.map_blocks) else 0
            for j in range(1, V0_FREE_BLOCKS_LIST_SIZE + 1):
                if tmp:
                    words[j] = tmp.pop(0)
                else:
                    words[j] = 0
            self.fs.write_words_block(map_block_number, words)

    def is_free(self, block_number: int) -> bool:
        """
        Check if the block is free
        """
        return block_number in self.free_blocks

    def set_free(self, block_number: int) -> None:
        """
        Mark the block as free
        """
        # Check if the block is already free
        if block_number not in self.free_blocks:
            # Update the free block list
            for i in range(0, len(self.free_blocks)):
                if self.free_blocks[i] == 0:
                    self.free_blocks[i] = block_number
                    return
            # If there is no free block, add it to the end of the list
            self.free_blocks.append(block_number)

    def set_used(self, block_number: int) -> None:
        """
        Mark the block as used
        """
        # Check if the block is already used
        if block_number in self.free_blocks:
            # Update the free block list
            for i in range(0, len(self.free_blocks)):
                if self.free_blocks[i] == block_number:
                    self.free_blocks[i] = 0
                    break

    def allocate(self, size: int) -> t.List[int]:
        """
        Allocate blocks
        """
        blocks = []
        for block_number in self.free_blocks:
            if block_number != 0:
                blocks.append(block_number)
                self.set_used(block_number)
            if len(blocks) == size:
                break
        if len(blocks) < size:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        return blocks

    def used(self) -> int:
        """
        Count the number of used blocks
        """
        return V0_FILESYSTEM_SIZE - self.free()

    def free(self) -> int:
        """
        Count the number of free blocks
        """
        return len([x for x in self.free_blocks if x != 0])

    def examine(self) -> str:
        buf = io.StringIO()
        buf.write("\n*Free-Storage Map\n")
        buf.write(f"Free:                  {self.free():>6}\n")
        buf.write(f"Used:                  {self.used():>6}\n")
        buf.write(f"Map Blocks:            {self.map_blocks}\n")
        return buf.getvalue()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UNIX0FreeStorageMap):
            return False
        return self.map_blocks == other.map_blocks and self.free_blocks == other.free_blocks


class UNIX0Filesystem(UNIXFilesystem):
    """
    UNIX version 0 Filesystem
    """

    fs_name = "unix0"
    fs_description = "UNIX version 0"
    fs_platforms = ["pdp-7"]
    fs_path_separator = "/"
    fs_entry_metadata = [
        "unix_flags",
        "unix_uid",
    ]

    version: int = 0
    directory_class = UNIX0Directory
    storage_map_block: int = 0  # First block of the free-storage map
    dev: BlockDevice18Bit

    def __init__(self, file_or_device: t.Union["AbstractFile", "AbstractDevice"]):
        if isinstance(file_or_device, AbstractFile):
            self.dev = BlockDevice18Bit(file_or_device, words_per_block=V0_WORDS_PER_BLOCK)
        elif isinstance(file_or_device, BlockDevice18Bit):
            self.dev = file_or_device
        else:
            raise OSError(errno.EIO, f"Invalid device type for {self.fs_description} filesystem")

    @classmethod
    def mount(
        cls, file_or_dev: t.Union["AbstractFile", "AbstractDevice"], **kwargs: t.Union[bool, str]
    ) -> "UNIX0Filesystem":
        self = cls(file_or_dev)
        self.version = 0
        self.pwd = "/"
        self.inode_size = V0_INODE_SIZE
        self.root_inode = V0_DD_INODE
        self.read_superblock()
        return self

    def read_words_block(
        self,
        block_number: int,
    ) -> t.List[int]:
        """
        Read a 256 bytes block as 18bit words
        """
        return self.dev.read_words_block(logical_to_physical_block_number(block_number))

    def write_words_block(
        self,
        block_number: int,
        words: t.List[int],
    ) -> None:
        """
        Write 256 18bit words as a block
        """
        self.dev.write_words_block(logical_to_physical_block_number(block_number), words)

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        return self.dev.read_block(logical_to_physical_block_number(block_number), number_of_blocks)

    def write_block(
        self,
        buffer: t.Union[bytes, bytearray],
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        self.dev.write_block(buffer, logical_to_physical_block_number(block_number), number_of_blocks)

    def read_superblock(self) -> None:
        """Read superblock"""
        # The first word of block 0 points to the first block of the free-storage map.
        words = self.read_words_block(V0_SUPER_BLOCK)
        self.storage_map_block = words[0]

    def read_inode(self, inode_num: int) -> UNIXInode:
        """
        Read inode by number
        """
        block_number, offset = get_v0_inode_block_offset(inode_num)
        words = self.read_words_block(block_number)[offset : offset + V0_INODE_SIZE]
        return UNIX0Inode.read(self, inode_num, words)

    def read_dir_entries(self, dirname: str) -> t.Iterator["UNIXDirectoryEntry"]:
        inode: UNIX0Inode = self.get_inode(dirname)  # type: ignore
        if inode:
            if not inode.is_dir():
                raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), dirname)
            for dirent in self.list_dir(inode):
                fullname = unix_join(dirname, dirent.filename)
                yield UNIX0DirectoryEntry(self, fullname, dirent.inode_num)

    def get_file_entry(self, fullname: str) -> UNIX0DirectoryEntry:
        inode: UNIX0Inode = self.get_inode(fullname)  # type: ignore
        if not inode:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), fullname)
        return UNIX0DirectoryEntry(self, fullname, inode.inode_num, inode)

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        entries = sorted(self.filter_entries_list(pattern, include_all=True, wildcard=True))
        if not entries:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), pattern)
        if not self.version == 0:
            blocks = reduce(lambda x, y: x + y, [x.inode.get_length() for x in entries])
            if self.version < 3:
                sys.stdout.write(f"total {blocks:>4}\n")
            else:
                sys.stdout.write(f"blocks = {blocks}\n")
        for x in entries:
            if not options.get("full") and x.name.startswith("."):
                pass
            uid = x.inode.uid if x.inode.uid != -1 else 0o77
            sys.stdout.write(
                f"{x.inode_num:>03o} {x.inode.flags & 0o77:02o} {uid:02o} {x.inode.nlinks:>02o} {x.inode.size:>05o} {x.name}\n"
            )

    def find_free_inode_num(self) -> int:
        """
        Find a free inode number
        """
        inode_num = 0
        for block_num in range(V0_FIRST_INODE_BLOCK, V0_FIRST_INODE_BLOCK + V0_INODE_BLOCKS):
            words = self.read_words_block(block_num)
            for i in range(0, V0_INODES_PER_BLOCK):
                offset = i * V0_INODE_SIZE
                if words[offset + V0_FLAGS] == 0:
                    # Found a free inode
                    inode_num = get_v0_inode_num_from_block_offset(block_num, offset)
                    if inode_num > max(V0_RESERVED_INODES):  # Skip reserved inodes
                        return inode_num
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), "No free inodes available")

    def write_bytes(
        self,
        fullname: str,
        content: t.Union[bytes, bytearray],
        fork: t.Optional[str] = None,
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
        file_mode: t.Optional[str] = None,
    ) -> None:
        """
        Write content to a file
        """
        metadata = metadata or {}
        words = from_bytes_to_18bit_words(content, file_mode or IMAGE)
        metadata["number_of_words"] = len(words)
        metadata["number_of_blocks"] = (len(words) + V0_WORDS_PER_BLOCK - 1) // V0_WORDS_PER_BLOCK
        # Create the file entry
        entry = self.create_file(fullname=fullname, size=len(content), metadata=metadata)
        with entry.open(file_mode) as f:
            f.write_words_block(words, block_number=0, number_of_blocks=metadata["number_of_blocks"])  # type: ignore

    def create_file(
        self,
        fullname: str,
        size: int,  # Size in bytes
        metadata: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> UNIXDirectoryEntry:
        """
        Create a new file with a given length in number of blocks
        """
        metadata = metadata or {}
        number_of_words: int = metadata.get("number_of_words")  # type: ignore
        if number_of_words is None:
            number_of_words = (size + V0_IO_BYTES_PER_WORD - 1) // V0_IO_BYTES_PER_WORD
        fullname = unix_join(self.pwd, fullname)
        dirname, filename = unix_split(fullname)
        # Delete the file if it already exists
        try:
            self.get_file_entry(fullname).delete()
        except FileNotFoundError:
            pass
        # Get parent directory
        try:
            parent = self.get_file_entry(dirname)
        except FileNotFoundError:
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), dirname)
        # Allocate a new inode and the data blocks for it
        number_of_blocks = (number_of_words + V0_WORDS_PER_BLOCK - 1) // V0_WORDS_PER_BLOCK
        flags: int = metadata.get("unix_flags", V0_DEFAULT_ACCESS)  # type: ignore
        inode = UNIX0Inode.allocate(self, number_of_blocks=number_of_blocks, size=number_of_words, flags=flags)
        # Creates a new hard link
        return UNIX0DirectoryEntry.link(fs=self, parent=parent, filename=filename, inode=inode)

    def create_directory(
        self,
        fullname: str,
        options: t.Dict[str, t.Union[bool, str]],
    ) -> "UNIX0DirectoryEntry":
        """
        Create a Directory
        """
        fullname = unix_join(self.pwd, fullname)
        dirname, filename = unix_split(fullname)
        # Check if the directory already exists
        try:
            self.get_file_entry(fullname)
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST))
        except FileNotFoundError:
            pass
        # Get parent directory
        parent = self.get_file_entry(dirname)
        if not parent.inode.is_dir():
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), dirname)
        # Allocate a new inode and the data blocks for it
        directory = UNIX0Directory.create(self)
        # Creates a new hard link
        return UNIX0DirectoryEntry.link(fs=self, parent=parent, filename=filename, inode=directory.inode)  # type: ignore

    def examine(self, arg: t.Optional[str], options: t.Dict[str, t.Union[bool, str]]) -> None:
        if options.get("bitmap"):
            free_storage_map = UNIX0FreeStorageMap.read(self)
            sys.stdout.write(free_storage_map.examine())
        else:
            super().examine(arg, options)
