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

__all__ = ["UIC"]


class UIC:
    """
    User Identification Code
    The format of UIC if [ggg,uuu] there ggg and uuu are octal digits
    The value on the left of the comma is represents the group number,
    the value on the right represents the user's number within the group.
    """

    group: int
    user: int

    def __init__(self, group: int, user: int):
        self.group = group & 0xFF
        self.user = user & 0xFF

    @classmethod
    def from_str(cls, code_str: str) -> "UIC":
        code_str = code_str.split("[")[1].split("]")[0]
        group_str, user_str = code_str.split(",")
        group = int(group_str, 8) & 0xFF
        user = int(user_str, 8) & 0xFF
        return cls(group, user)

    @classmethod
    def from_word(cls, code_int: int) -> "UIC":
        group = code_int >> 8
        user = code_int & 0xFF
        return cls(group, user)

    def to_word(self) -> int:
        return (self.group << 8) + self.user

    def to_wide_str(self) -> str:
        return f"[{self.group:>3o},{self.user:<3o}]"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, UIC):
            return self.group == other.group and self.user == other.user
        elif isinstance(other, str):
            other_uic = UIC.from_str(other)
            return self.group == other_uic.group and self.user == other_uic.user
        elif isinstance(other, int):
            other_uic = UIC.from_word(other)
            return self.group == other_uic.group and self.user == other_uic.user
        else:
            raise ValueError("Invalid type for comparison")

    def __lt__(self, other: "UIC") -> bool:
        return self.to_word() < other.to_word()

    def __gt__(self, other: "UIC") -> bool:
        return self.to_word() > other.to_word()

    def __hash__(self) -> int:
        return hash(self.to_word())

    def __str__(self) -> str:
        return f"[{self.group:o},{self.user:o}]"

    def __repr__(self) -> str:
        return f"[{self.group:o},{self.user:o}]"
