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

import typing

if typing.TYPE_CHECKING:
    from .abstract import AbstractFile

__all__ = ["BlockDevice"]


class BlockDevice:
    """
    Block device
    """

    f: "AbstractFile"

    def __init__(self, file: "AbstractFile"):
        self.f = file

    def read_block(
        self,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> bytes:
        return self.f.read_block(block_number, number_of_blocks)

    def write_block(
        self,
        buffer: bytes,
        block_number: int,
        number_of_blocks: int = 1,
    ) -> None:
        self.f.write_block(buffer, block_number, number_of_blocks)
