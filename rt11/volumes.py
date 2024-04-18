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

import os
import sys
import traceback
from typing import Dict, Optional, Union

from .abstract import AbstractFilesystem
from .commons import splitdrive
from .dos11fs import DOS11Filesystem
from .native import NativeFilesystem
from .rt11fs import RT11Filesystem

__all__ = [
    "Volumes",
]


class Volumes(object):
    """
    Logical Device Names

    SY: System device, the device from which this program was started
    DK: Default storage device (initially the same as SY:)
    """

    volumes: Dict[str, Union[AbstractFilesystem, str]]

    def __init__(self) -> None:
        self.volumes: Dict[str, Union[AbstractFilesystem, str]] = {}
        if self._drive_letters():
            # windows
            for letter in self._drive_letters():
                self.volumes[letter] = NativeFilesystem("%s:" % letter)
            current_drive = os.getcwd().split(":")[0]
            self.volumes["SY"] = self.volumes[current_drive]
        else:
            # posix
            self.volumes["SY"] = NativeFilesystem()
        self.volumes["DK"] = "SY"

    def _drive_letters(self) -> list[str]:
        try:
            import string
            from ctypes import windll  # type: ignore

            drives = []
            bitmask = windll.kernel32.GetLogicalDrives()
            for c in string.ascii_uppercase:
                if bitmask & 1:
                    drives.append(c)
                bitmask >>= 1
            return drives
        except Exception:
            return []

    def get(self, volume_id: Optional[str], cmd: str = "KMON") -> AbstractFilesystem:
        if volume_id is None:
            volume_id = "DK"
        elif volume_id.endswith(":"):
            volume_id = volume_id[:-1]
        if volume_id.upper() == "LAST":
            volume_id = self.last()
        v = self.volumes.get(volume_id.upper())
        if isinstance(v, str):
            v = self.volumes.get(v.upper())
        if v is None:
            raise Exception("?%s-F-Illegal volume %s:" % (cmd, volume_id))
        return v

    def chdir(self, path: str) -> bool:
        volume_id, fullname = splitdrive(path)
        if volume_id.upper() == "LAST":
            volume_id = self.last()
        try:
            fs = self.get(volume_id)
        except Exception:
            return False
        if fullname and not fs.chdir(fullname):
            return False
        if volume_id != "DK":
            self.set_default_volume(volume_id)
        return True

    def get_pwd(self) -> str:
        try:
            return "%s:%s" % (self.volumes.get("DK"), self.get("DK").get_pwd())
        except:
            return "%s:???" % (self.volumes.get("DK"))

    def set_default_volume(self, volume_id: str) -> None:
        """Set the default volume"""
        if not volume_id:
            return
        if volume_id.endswith(":"):
            volume_id = volume_id[:-1]
        if volume_id.upper() == "LAST":
            volume_id = self.last()
        volume_id = volume_id.upper()
        if volume_id != "DK" and volume_id in self.volumes:
            self.volumes["DK"] = volume_id
        else:
            raise Exception("?KMON-F-Invalid volume")

    def mount(self, path: str, logical: str, fstype: Optional[str] = None, verbose: bool = False) -> None:
        logical = logical.split(":")[0].upper()
        if logical in ("SY", "DK") or not logical:
            raise Exception(f"?MOUNT-F-Illegal volume {logical}:")
        volume_id, fullname = splitdrive(path)
        fs = self.get(volume_id, cmd="MOUNT")
        try:
            if fstype == "dos11":
                self.volumes[logical] = DOS11Filesystem(fs.open_file(fullname))
            else:
                self.volumes[logical] = RT11Filesystem(fs.open_file(fullname))
            sys.stdout.write(f"?MOUNT-I-Disk {path} mounted to {logical}:\n")
        except Exception:
            if verbose:
                traceback.print_exc()
            sys.stdout.write(f"?MOUNT-F-Error mounting {path} to {logical}:\n")

    def dismount(self, logical: str) -> None:
        logical = logical.split(":")[0].upper()
        if logical in ("SY", "DK") or logical not in self.volumes:
            raise Exception(f"?DISMOUNT-F-Illegal volume {logical}:")
        del self.volumes[logical]

    def last(self) -> str:
        return list(self.volumes.keys())[-1]
