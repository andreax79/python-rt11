# Copyright (C) 2014 Andrea Bonomi <andrea.bonomi@gmail.com>

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
import math
import sys
import typing as t
from datetime import date

from ..abstract import AbstractFile
from ..commons import ASCII, BLOCK_SIZE, IMAGE, READ_FILE_FULL
from ..device.abstract import AbstractDevice
from ..device.block_12bit import BlockDevice12Bit, COSRXBlockDevice12Bit
from .os8fs import OS8Path, OS8Filesystem, OS8Segment, os8_split_fullname

__all__ = [
    "COS300Path",
    "COS300Filesystem",
]

# COS 300/310 System Reference Manual, 1975, Pag 322
# https://bitsavers.org/pdf/dec/pdp8/cos-300/DEC-08-OCOSA-F_D_COS_300_310_System_Reference_Manual_Jul75.pdf

# There are 4 types of files in COS: Source, Binary, Data, and System files
COS_EXTENSIONS = {  # OS/8 extension: COS 1 character file type
    "DA": "A",  # Data
    "DB": "B",  # Binary
    "AS": "S",  # Source
    "SV": "V",  # System files (OS/8 SAVE Format)
}
# fmt: off
# COS 300/310 System Reference Manual, 1975, Pag 263
# https://bitsavers.org/pdf/dec/pdp8/cos-300/DEC-08-OCOSA-F_D_COS_300_310_System_Reference_Manual_Jul75.pdf
# In both source and data files, characters are stored 2 characters per word in 6-bit binary form
COS_CODES = [
    "\0", " ", "!", '"', "#", "$", "%", "&", "'", "(", ")", "*", "+", ",", "-", ".", "/",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", ":", ";", "<", "=", ">", "?",
    "@", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O",
    "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "[", "\t", "]", "^"
]
# fmt: on
COS_INDEX = {ch: i for i, ch in enumerate(COS_CODES) if ch}


def split_ext(name: str) -> t.Tuple[str, str]:
    """
    Split a name into the stem and extension.
    """
    name = name.upper()
    try:
        stem, extension = name.split(".", 1)
        return stem, extension
    except Exception:
        return (name, "")


def cos_file_type_to_os8_extension(file_type: str) -> str:
    """
    Convert a COS file type to an OS/8 extension.
    """
    return {v: k for k, v in COS_EXTENSIONS.items()}.get(file_type, file_type)


def from_bytes_to_12bit_words(byte_data: t.Union[bytes, bytearray]) -> t.List[int]:
    """
    Convert bytes to 12-bit words.
    """
    words = []
    for i in range(0, len(byte_data), 3):
        chr1 = byte_data[i] & 0xFF
        try:
            chr2 = byte_data[i + 1] & 0xFF
        except IndexError:
            chr2 = 0
        try:
            chr3 = byte_data[i + 2] & 0xFF
        except IndexError:
            chr3 = 0
        words.append(chr1 | ((chr3 & 0o360) << 4))
        words.append(chr2 | ((chr3 & 0o17) << 8))
    return words


def from_12bit_words_to_bytes(words: t.List[int]) -> bytes:
    """
    Convert 12-bit words to bytes.
    """
    result = bytearray()
    for i in range(0, len(words), 2):
        chr1 = words[i]
        try:
            chr2 = words[i + 1]
        except IndexError:
            chr2 = 0
        chr3 = ((chr2 >> 8) & 0o17) | ((chr1 >> 4) & 0o360)
        result.append(chr1 & 0xFF)
        result.append(chr2 & 0xFF)
        result.append(chr3 & 0xFF)
    return bytes(result)


def cos_codes_to_ascii(words: t.List[int]) -> bytes:
    """
    Source/data file format:

    +-----------------+
    |   Word count    |  1 word
    +-----------------+
    |   Line number   |  1 word
    +-----------------+
    |   Line data     |  n-1 words
    /                 |
    |                 |
    +-----------------+

    """
    out = bytearray()
    words = list(words)  # Make a copy of the list to avoid modifying the original
    while words:
        # Read the word count
        len_word = 4096 - words.pop(0) - 1
        if len_word >= 4095:
            break
        # Read the line number
        line_number = words.pop(0)
        out += f"{line_number:04} ".encode("ascii")  # Line number
        # Read the line
        for i in range(0, len_word):
            # Characters are stored 2 characters per word in 6-bit binary form
            b3 = words.pop(0)
            b1 = b3 >> 6
            b2 = b3 & 0o77
            out += COS_CODES[b1].encode("ascii")
            if b2 != 0:
                out += COS_CODES[b2].encode("ascii")
        out += b"\n"
    return bytes(out)


def ascii_to_cos_codes(text: t.Union[bytes, bytearray]) -> t.List[int]:
    words: t.List[int] = []
    lines = text.decode("ascii").splitlines()

    for line in lines:
        if len(line) < 5 or not line[:4].isdigit():
            continue  # Skip invalid or malformed lines

        line_number = int(line[:4])
        content = line[5:]  # Skip past line number and space
        word_count = math.ceil(len(content) / 2)
        words.append(4096 - word_count - 1)  # Inverse of encoding logic
        words.append(line_number)

        for i in range(0, len(content), 2):
            c1 = content[i]
            b1 = COS_INDEX.get(c1, 0)
            if i + 1 < len(content):
                c2 = content[i + 1] if i + 1 < len(content) else " "
                b2 = COS_INDEX.get(c2, 0)
            else:
                b2 = 0
            word = (b1 << 6) | b2
            words.append(word)

    return words


class COS300Path(OS8Path):
    @property
    def file_type(self) -> t.Optional[str]:
        """File type"""
        if self.is_tentative:
            return "TEMP"
        elif self.is_empty:
            return "EMPTY"
        elif self.extension == "A":
            return "DATA"
        elif self.extension == "B":
            return "BINARY"
        elif self.extension == "S":
            return "SOURCE"
        elif self.extension == "V":
            return "SYSTEM"
        else:
            return "PERM"

    @classmethod
    def read(cls, segment: "OS8Segment", words: t.List[int], position: int, first_block_number: int) -> "OS8Path":
        self = super().read(segment, words, position, first_block_number)
        # Convert the extension to a COS 1 character extension
        self.extension = COS_EXTENSIONS.get(self.extension, self.extension)
        return self

    def to_words(self) -> t.List[int]:
        """
        Write the directory entry
        """
        try:
            # Convert the 1 character COS file type to an OS/8 extension
            tmp = self.extension
            self.extension = cos_file_type_to_os8_extension(self.extension)
            return super().to_words()
        finally:
            self.extension = tmp

    def read_bytes(self, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> bytes:
        """Get the content of the file"""
        is_source_file = self.extension == "S"  # Source file
        if file_mode is None:
            file_mode = ASCII if is_source_file else IMAGE
        # Always read the file as IMAGE
        with self.open(IMAGE) as f:
            data = f.read_block(0, READ_FILE_FULL)
            if file_mode == ASCII and is_source_file:
                words = from_bytes_to_12bit_words(data)
                return cos_codes_to_ascii(words)
            else:
                return data

    def __str__(self) -> str:
        return f"{self.fullname:<10} {self.file_type:<6} {self.creation_date or '          '} {self.length:>6} {self.first_block_number:6d}"


class COS300Filesystem(OS8Filesystem):
    """
    COS-300/COS-310 Filesystem

    The COS-300/COS-310 filesystem is essentially the same filesystem as the OS/8.
    The main difference is that the COS-300/COS-310 filesystem has four types of files:
    - Source (.S)
    - Binary (.B)
    - Data (.A)
    - System files (.V)
    The file type is stored in the extension field of the directory entry.
    On the directory entry the file type is represented by two characters, e.g. "SV" for a system file,
    but on the extension is repsented by a single character, e.g. "V" for a system file.

    https://bitsavers.org/pdf/dec/pdp8/cos-300/DEC-08-OCOSA-F_D_COS_300_310_System_Reference_Manual_Jul75.pdf
    """

    fs_name = "cos300"
    fs_description = "PDP-8 COS-300/COS-310"
    fs_platforms = ["pdp-8"]
    fs_entry_metadata = [
        "creation_date",
        "file_type",
    ]

    directory_entry_class: t.Type[OS8Path] = COS300Path

    def __init__(self, file_or_device: t.Union["AbstractFile", "AbstractDevice"]):
        if isinstance(file_or_device, AbstractFile):
            self.dev = COSRXBlockDevice12Bit(file_or_device)
        elif isinstance(file_or_device, BlockDevice12Bit):
            self.dev = file_or_device
        else:
            raise OSError(errno.EIO, f"Invalid device type for {self.fs_description} filesystem")

    @classmethod
    def mount(
        cls,
        file_or_dev: t.Union["AbstractFile", "AbstractDevice"],
        strict: t.Union[bool, str] = True,
        device_type: t.Union[bool, str] = "",
        **kwargs: t.Union[bool, str],
    ) -> "COS300Filesystem":
        """
        Mount the COS-300/COS-310 filesystem from a file
        """
        self = cls(file_or_dev)
        self.current_partition = 0
        self.number_of_blocks = self.dev.get_size() // BLOCK_SIZE
        if strict:
            # Check if the filesystem is valid by reading the directory entries
            self.check_os8()
        return self

    def dir(self, volume_id: str, pattern: t.Optional[str], options: t.Dict[str, bool]) -> None:
        partition = self.current_partition
        if pattern:
            partition, pattern = os8_split_fullname(partition, pattern, wildcard=True)
        part = self.get_partition(partition)
        dt = date.today().strftime("%d-%b-%y").upper()
        sys.stdout.write(f"DIRECTORY       {dt}\n\n")
        sys.stdout.write("NAME   TYPE LN    DATE\n\n")
        for segment in part.read_dir_segments():
            for x in segment.filter_entries_list(pattern):
                fullname = f"{x.stem:<6}.{x.extension:<2}"
                file_date = x.creation_date and x.creation_date.strftime("%d-%b-%y").upper() or ""
                sys.stdout.write(f"{x.stem:<6}  {x.extension:>2}  {x.length:>02}  {file_date:<9}\n")
        unused = part.free()
        sys.stdout.write(f" <{unused:>04} FREE BLOCKS>\n")

    def read_bytes(self, fullname: str, file_mode: t.Optional[str] = None, fork: t.Optional[str] = None) -> bytes:
        """
        Get the content of a file
        """
        is_source_file = split_ext(fullname)[1] == "S"  # Source file
        if file_mode is None:
            file_mode = ASCII if is_source_file else IMAGE
        # Always read the file as IMAGE
        with self.open_file(fullname, IMAGE) as f:
            data = f.read_block(0, READ_FILE_FULL)
            if file_mode == ASCII and is_source_file:
                words = from_bytes_to_12bit_words(data)
                return cos_codes_to_ascii(words)
            else:
                return data

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
        is_source_file = split_ext(fullname)[1] == "S"  # Source file
        if file_mode is None:
            file_mode = ASCII if is_source_file else IMAGE
        if file_mode == ASCII and is_source_file:
            # Convert ASCII content to COS codes
            words = ascii_to_cos_codes(content)
            content = from_12bit_words_to_bytes(words)
            # Always write the file as IMAGE
            file_mode = IMAGE
        super().write_bytes(fullname, content, fork, metadata, file_mode)
