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


import struct
import typing as t

__all__ = [
    "ProDOSFileInfo",
    "decode_apple_single",
    "encode_apple_single",
]

APPLE_SINGLE_MAGIC = 0x00051600
APPLE_SINGLE_HEADER_FORMAT = ">II16sH"
APPLE_SINGLE_HEADER_SIZE = struct.calcsize(APPLE_SINGLE_HEADER_FORMAT)
APPLE_SINGLE_ENTRY_FORMAT = ">III"
APPLE_SINGLE_ENTRY_SIZE = struct.calcsize(APPLE_SINGLE_ENTRY_FORMAT)
APPLE_SINGLE_PRODOS_INFO_FORMAT = ">HHI"
APPLE_SINGLE_PRODOS_INFO_SIZE = struct.calcsize(APPLE_SINGLE_PRODOS_INFO_FORMAT)
APPLE_SINGLE_DATA_FORK = 1
APPLE_SINGLE_RESOURCE_FORK = 2
APPLE_SINGLE_PRODOS_INFO = 11
APPLE_SINGLE_VERSION_2 = 0x20000


class ProDOSFileInfo:
    access: int  # ProDOS access
    file_type: int  # ProDOS file type
    aux_type: int  # ProDOS aux type

    def __init__(self, access: int = 0, file_type: int = 0, aux_type: int = 0):
        self.access = access
        self.file_type = file_type
        self.aux_type = aux_type

    @classmethod
    def read(cls, buffer: bytes, position: int) -> "ProDOSFileInfo":
        self = cls()
        (
            self.access,
            self.file_type,
            self.aux_type,
        ) = struct.unpack_from(APPLE_SINGLE_PRODOS_INFO_FORMAT, buffer, position)
        return self

    def write(self) -> bytes:
        return struct.pack(
            APPLE_SINGLE_PRODOS_INFO_FORMAT,
            self.access,
            self.file_type,
            self.aux_type,
        )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ProDOSFileInfo)
            and self.access == other.access
            and self.file_type == other.file_type
            and self.aux_type == other.aux_type
        )

    def __str__(self) -> str:
        return f"Access: {self.access:04X} File type: {self.file_type:04X} Aux type: {self.aux_type:08X}"


def decode_apple_single(content: bytes) -> t.Tuple[bytes, t.Optional[bytes], t.Optional[ProDOSFileInfo]]:
    """
    Extract data fork and metadata from an AppleSingle file

    assert parse_file_aux_type("$99") == (0x99, 0)

    https://nulib.com/library/AppleSingle_AppleDouble.pdf
    """
    # Read the header
    (
        magic,
        _version,
        _filler,
        number_of_entries,
    ) = struct.unpack_from(APPLE_SINGLE_HEADER_FORMAT, content, 0)
    if magic != APPLE_SINGLE_MAGIC:
        raise ValueError("Invalid AppleSingle format")
    # Read the entries
    resource = None
    data_fork_offset = 0
    data_fork_length = 0
    resource_fork_offset = 0
    resource_fork_length = 0
    prodos_file_info: t.Optional[ProDOSFileInfo] = None  # ProDOS file info
    for i in range(0, number_of_entries):
        position = APPLE_SINGLE_HEADER_SIZE + i * APPLE_SINGLE_ENTRY_SIZE
        (
            entry_id,
            entry_offset,
            entry_length,
        ) = struct.unpack_from(APPLE_SINGLE_ENTRY_FORMAT, content, position)
        if entry_id == APPLE_SINGLE_DATA_FORK:
            data_fork_offset = entry_offset
            data_fork_length = entry_length
        elif entry_id == APPLE_SINGLE_RESOURCE_FORK:
            resource_fork_offset = entry_offset
            resource_fork_length = entry_length
        elif entry_id == APPLE_SINGLE_PRODOS_INFO:
            prodos_file_info = ProDOSFileInfo.read(content, entry_offset)
    if data_fork_offset == 0:
        raise ValueError("Data fork not found")
    data = content[data_fork_offset : data_fork_offset + data_fork_length]
    if resource_fork_offset:
        resource = content[resource_fork_offset : resource_fork_offset + resource_fork_length]
    return data, resource, prodos_file_info


def encode_apple_single(prodos_file_info: ProDOSFileInfo, data: bytes, resource: t.Optional[bytes] = None) -> bytes:
    """
    Encode data fork, resource fork and metadata into an AppleSingle file
    """
    num_of_entries = 3 if resource is not None else 2
    # Write the header
    buffer = bytearray()
    buffer += struct.pack(
        APPLE_SINGLE_HEADER_FORMAT,
        APPLE_SINGLE_MAGIC,
        APPLE_SINGLE_VERSION_2,
        b"\0" * 16,  # filler
        num_of_entries,  # number of entries
    )
    data_offset = APPLE_SINGLE_HEADER_SIZE + num_of_entries * APPLE_SINGLE_ENTRY_SIZE
    buffer += struct.pack(
        APPLE_SINGLE_ENTRY_FORMAT,
        APPLE_SINGLE_DATA_FORK,  # entry_id
        data_offset + APPLE_SINGLE_PRODOS_INFO_SIZE,  # offset
        len(data),  # length
    )
    buffer += struct.pack(
        APPLE_SINGLE_ENTRY_FORMAT,
        APPLE_SINGLE_PRODOS_INFO,  # entry_id
        data_offset,  # offset
        APPLE_SINGLE_PRODOS_INFO_SIZE,  # length
    )
    if resource is not None:
        buffer += struct.pack(
            APPLE_SINGLE_ENTRY_FORMAT,
            APPLE_SINGLE_RESOURCE_FORK,  # entry_id
            data_offset + APPLE_SINGLE_PRODOS_INFO_SIZE + len(data),  # offset
            len(resource),  # length
        )
    # Write the entries
    buffer += prodos_file_info.write()
    buffer += data
    if resource is not None:
        buffer += resource
    return bytes(buffer)
