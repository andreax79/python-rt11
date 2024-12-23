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
from typing import Dict, Optional

from .abstract import AbstractFilesystem
from .apple2.pascalfs import PascalFilesystem
from .apple2.prodosfs import ProDOSFilesystem
from .caps11fs import CAPS11Filesystem
from .commons import splitdrive
from .dmsfs import DMSFilesystem
from .dos11fs import DOS11Filesystem
from .dos11magtapefs import DOS11MagTapeFilesystem
from .files11fs import Files11Filesystem
from .native import NativeFilesystem
from .os8fs import OS8Filesystem
from .rstsfs import RSTSFilesystem
from .rt11fs import RT11Filesystem
from .solofs import SOLOFilesystem
from .unix0fs import UNIXFilesystem0
from .unixfs import UNIXFilesystem

__all__ = [
    "Volumes",
    "DEFAULT_VOLUME",
    "FILESYSTEMS",
]

DEFAULT_VOLUME = "DK"
SYSTEM_VOLUME = "SY"
FILESYSTEMS = {
    "caps11": CAPS11Filesystem,
    "dos": DOS11Filesystem,
    "dos11": DOS11Filesystem,
    "dos11mt": DOS11MagTapeFilesystem,
    "files11": Files11Filesystem,
    "magtape": DOS11MagTapeFilesystem,
    "rt11": RT11Filesystem,
    "solo": SOLOFilesystem,
    "unix0": lambda f: UNIXFilesystem0(f),
    "unix1": lambda f: UNIXFilesystem(f, version=1),
    "unix5": lambda f: UNIXFilesystem(f, version=5),
    "unix6": lambda f: UNIXFilesystem(f, version=6),
    "unix7": lambda f: UNIXFilesystem(f, version=7),
    "rsts": RSTSFilesystem,
    "os8": OS8Filesystem,
    "dms": DMSFilesystem,
    "prodos": ProDOSFilesystem,
    "pascal": PascalFilesystem,
}


class Volumes(object):
    """
    Logical Device Names

    SY: System volume, the device from which this program was started
    DK: Default storage volume (initially the same as SY:)
    """

    volumes: Dict[str, AbstractFilesystem]  # volume id -> fs
    logical: Dict[str, str]  # local id -> volume id
    defdev: str  # Default device, DK

    def __init__(self) -> None:
        self.volumes: Dict[str, AbstractFilesystem] = {}
        self.logical: Dict[str, str] = {}
        if self._drive_letters():
            # windows
            for letter in self._drive_letters():
                self.volumes[letter] = NativeFilesystem(f"{letter.upper()}:")
            current_drive = os.getcwd().split(":")[0].upper()
            self.defdev = current_drive
            self.logical["SY"] = current_drive
        else:
            # posix
            self.volumes["N"] = NativeFilesystem()
            self.logical[SYSTEM_VOLUME] = "N"
            self.defdev = SYSTEM_VOLUME

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

    def canonical_volume(self, volume_id: str, cmd: str = "KMON") -> str:
        """
        Convert a volume id into canonical form
        """
        if not volume_id:
            volume_id = DEFAULT_VOLUME
        else:
            volume_id = volume_id.upper()
            if volume_id.endswith(":"):
                volume_id = volume_id[:-1]
        return volume_id

    def get(self, volume_id: str, cmd: str = "KMON") -> AbstractFilesystem:
        """
        Get a filesystem by volume id
        """
        volume_id = self.canonical_volume(volume_id, cmd=cmd)
        if volume_id == DEFAULT_VOLUME:
            volume_id = self.defdev
        volume_id = self.logical.get(volume_id, volume_id)
        try:
            return self.volumes[volume_id]
        except KeyError:
            raise Exception(f"?{cmd}-F-Illegal volume {volume_id}:")

    def chdir(self, path: str) -> bool:
        """
        Change current directory
        """
        volume_id, fullname = splitdrive(path)
        volume_id = self.canonical_volume(volume_id)
        try:
            fs = self.get(volume_id)
        except Exception:
            return False
        if fullname and not fs.chdir(fullname):
            return False
        if volume_id != DEFAULT_VOLUME:
            self.set_default_volume(volume_id)
        return True

    def get_pwd(self) -> str:
        """
        Get current volume and directory
        """
        try:
            pwd = self.get(self.defdev).get_pwd()
            return f"{self.defdev}:{pwd}"
        except Exception:
            return f"{self.defdev}:???"

    def set_default_volume(self, volume_id: str, cmd: str = "KMON") -> None:
        """
        Set the default volume
        """
        volume_id = self.canonical_volume(volume_id, cmd=cmd)
        if volume_id != DEFAULT_VOLUME:
            self.get(volume_id, cmd=cmd)
            self.defdev = volume_id

    def assign(self, volume_id: str, logical: str, verbose: bool = False, cmd: str = "KMON") -> None:
        """
        Associate a logical device name with a device
        """
        volume_id = self.canonical_volume(volume_id)
        volume_id = self.logical.get(volume_id, volume_id)
        logical = self.canonical_volume(logical)
        if logical == DEFAULT_VOLUME:
            self.set_default_volume(volume_id, cmd=cmd)
        else:
            self.get(volume_id, cmd=cmd)
            self.logical[logical] = volume_id

    def deassign(self, volume_id: str, verbose: bool = False, cmd: str = "KMON") -> None:
        """
        Removes logical device name assignments
        """
        volume_id = self.canonical_volume(volume_id)
        if volume_id == DEFAULT_VOLUME or not volume_id in self.logical:
            raise Exception(f"?{cmd}-W-Logical name not found {volume_id}:")
        del self.logical[volume_id]

    def mount(
        self,
        path: str,
        logical: str,
        fstype: Optional[str] = None,
        verbose: bool = False,
        cmd: str = "MOUNT",
    ) -> None:
        """
        Mount a file to a logical disk unit
        """
        logical = self.canonical_volume(logical)
        if logical == DEFAULT_VOLUME or not logical:
            raise Exception(f"?{cmd}-F-Illegal volume {logical}:")
        volume_id, fullname = splitdrive(path)
        fs = self.get(volume_id, cmd=cmd)
        try:
            filesystem = FILESYSTEMS.get(fstype or "rt11", RT11Filesystem)
            self.volumes[logical] = filesystem(fs.open_file(fullname))
            sys.stdout.write(f"?{cmd}-I-Disk {path} mounted to {logical}:\n")
        except Exception:
            if verbose:
                traceback.print_exc()
            sys.stdout.write(f"?{cmd}-F-Error mounting {path} to {logical}:\n")

    def dismount(self, volume_id: str, cmd: str = "DISMOUNT") -> None:
        """
        Disassociates a logical disk assignment from a file
        """
        volume_id = self.canonical_volume(volume_id)
        if volume_id == DEFAULT_VOLUME:
            raise Exception(f"?{cmd}-F-Illegal volume {volume_id}:")
        try:
            fs = self.get(volume_id, cmd=cmd)
        except Exception:
            raise Exception(f"?{cmd}-F-Illegal volume {volume_id}:")
        self.volumes = {k: v for k, v in self.volumes.items() if v != fs}
