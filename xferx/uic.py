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

import typing as t

T_UIC = t.TypeVar("T_UIC", bound="UIC")

__all__ = [
    "UIC",
    "ANY_UIC",
    "DEFAULT_UIC",
]


class UIC:
    """
    User Identification Code
    The format of UIC if [ggg,uuu] there ggg and uuu are octal (or decimal) digits
    The value on the left of the comma is represents the group number,
    the value on the right represents the user's number within the group.
    """

    GROUP_BITS = 8  # Number of bits of the group number
    USER_BITS = 8  # Number of bits of the user number
    IS_OCTAL = True  # Indicates that the UIC is represented in octal format
    ANY_USER: t.Optional[int] = None  # Indicates that the user number is a wildcard (any user)
    ANY_GROUP: t.Optional[int] = None  # Indicates that the group number is a wildcard (any group)

    group: t.Optional[int]  # The group number of the UIC
    user: t.Optional[int]  # The user number of the UIC

    def __init__(self, group: t.Optional[int], user: t.Optional[int]):
        self.group = group & ((1 << self.GROUP_BITS) - 1) if group is not None else None
        self.user = user & ((1 << self.USER_BITS) - 1) if user is not None else None

    @classmethod
    def from_str(cls: t.Type[T_UIC], code_str: str, strict: bool = False) -> T_UIC:
        code_str, tmp = code_str.split("[")[1].split("]", 1)
        if strict and tmp:
            raise ValueError("Invalid UIC")
        group_str, user_str = code_str.split(",")
        if group_str == "*":
            group = cls.ANY_GROUP
        else:
            group = int(group_str, 8 if cls.IS_OCTAL else 10)
        if user_str == "*":
            user = cls.ANY_USER
        else:
            user = int(user_str, 8 if cls.IS_OCTAL else 10)
        return cls(group, user)

    @classmethod
    def from_word(cls: t.Type[T_UIC], code_int: int) -> T_UIC:
        user_mask = (1 << cls.USER_BITS) - 1
        user = code_int & user_mask
        group = code_int >> cls.USER_BITS
        return cls(group, user)

    @property
    def has_wildcard(self) -> bool:
        return self.group == self.ANY_GROUP or self.user == self.ANY_USER

    def to_word(self) -> int:
        if self.group is None or self.user is None:
            raise ValueError("Group and user must be set to convert to word")
        return (self.group << self.USER_BITS) | self.user

    @staticmethod
    def _digit_width(bits: int, base: int) -> int:
        """
        Calculate the number of digits needed to represent a value
        with the given number of bits in the specified base.
        """
        value = (1 << bits) - 1
        width = 1
        while value >= base:
            value //= base
            width += 1
        return width

    def to_wide_str(self) -> str:
        if self.IS_OCTAL:
            group_width = (self.GROUP_BITS + 2) // 3
            user_width = (self.USER_BITS + 2) // 3
            g = f"{self.group:o}" if self.group != self.ANY_GROUP else "*"
            u = f"{self.user:o}" if self.user != self.ANY_USER else "*"
            return f"[{g:>{group_width}},{u:<{user_width}}]"
        else:
            group_width = self._digit_width(self.GROUP_BITS, 10)
            user_width = self._digit_width(self.USER_BITS, 10)
            g = str(self.group) if self.group != self.ANY_GROUP else "*"
            u = str(self.user) if self.user != self.ANY_USER else "*"
            return f"[{g:>{group_width}},{u:<{user_width}}]"

    def match(self, other: "UIC") -> bool:
        if self == other:
            return True
        elif self.group == self.ANY_GROUP and self.user == self.ANY_USER:
            return True
        elif self.group == self.ANY_GROUP and self.user == other.user:
            return True
        elif self.group == other.group and self.user == self.ANY_USER:
            return True
        else:
            return False

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
        if self.IS_OCTAL:
            g = f"{self.group:o}" if self.group != self.ANY_GROUP else "*"
            u = f"{self.user:o}" if self.user != self.ANY_USER else "*"
        else:
            g = str(self.group) if self.group != self.ANY_GROUP else "*"
            u = str(self.user) if self.user != self.ANY_USER else "*"
        return f"[{g},{u}]"

    def __repr__(self) -> str:
        return str(self)


ANY_UIC = UIC.from_str("[*,*]")
DEFAULT_UIC = UIC.from_str("[1,1]")
