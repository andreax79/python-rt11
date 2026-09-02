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

import argparse
import sys
import typing as t

from .commons import LoggingLevel
from .shell import Shell
from .volumes import FILESYSTEMS

__all__ = ["main"]


class CustomAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: t.Any,
        option_string: t.Optional[str] = None,
    ) -> None:
        fstype = option_string.strip("-") if option_string else None
        assert fstype is not None
        arr = getattr(namespace, "mounts", [])
        for v in values:
            arr.append((fstype, v))
        setattr(namespace, "mounts", arr)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c",
        action="append",
        metavar="command",
        help="execute a single command",
    )
    parser.add_argument(
        "-d",
        "--dir",
        metavar="dir",
        help="set working drive and directory",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        default=False,
        help="force opening an interactive shell even if commands are provided",
    )
    parser.add_argument(
        "-e",
        "--explorer",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_const",
        dest="level",
        default=LoggingLevel.INFO,
        const=LoggingLevel.DEBUG,
        help="display verbose output",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_const",
        dest="level",
        default=LoggingLevel.INFO,
        const=LoggingLevel.ERROR,
        help="display only errors",
    )
    for name, fs in FILESYSTEMS.items():
        parser.add_argument(
            f"--{name}",
            nargs=1,
            dest="image",
            action=CustomAction,
            help=f"mount {fs.fs_description}",
        )
    parser.add_argument(
        "disk",
        nargs="*",
        help="disk to be mounted",
    )
    options = parser.parse_args()
    shell = Shell(level=options.level)
    # Mount disks
    i = 0
    try:
        for fstype, dsk in getattr(options, "mounts", []):
            shell.volumes.mount(dsk, f"DL{i}:", fstype=fstype, level=shell.level)
            i = i + 1
        for i, dsk in enumerate(options.disk):
            shell.volumes.mount(dsk, f"DL{i}:", level=shell.level)
            i = i + 1
    except Exception as ex:
        sys.stderr.write(f"{ex}\n")
        sys.exit(1)
    # Change dir
    if options.dir:
        shell.volumes.set_default_volume(options.dir)
    # Execute the commands
    if options.c:
        try:
            for command in options.c:
                shell.onecmd(command, batch=True)
        except Exception:
            sys.exit(1)
    # Start interactive shell
    if options.explorer:
        from .shell.explorer import Explorer

        Explorer(shell).run()
    elif options.interactive or not options.c:
        shell.cmd_loop()


if __name__ == "__main__":
    main()
