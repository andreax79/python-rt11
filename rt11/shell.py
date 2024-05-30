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
import cmd
import os
import shlex
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from .abstract import AbstractDirectoryEntry, AbstractFilesystem
from .commons import PartialMatching, splitdrive
from .volumes import DEFAULT_VOLUME, Volumes

try:
    import readline
except:
    readline = None  # type: ignore

__all__ = [
    "Shell",
]


HISTORY_FILENAME = "~/.rt_history"
HISTORY_LENGTH = 1000

#    READ     =    0
#    WRITE    =    0
#    CLOSE    =    1
#    DELETE   =    2
#    LOOKUP   =    3
#    ENTER    =    4
#    RENAME   =    5


def ask(prompt: str) -> str:
    """
    Prompt the user for input with the given prompt message
    """
    result = ""
    while not result:
        result = input(prompt).strip()
    return result


def extract_options(line: str, *options: str) -> Tuple[List[str], Dict[str, bool]]:
    args = shlex.split(line)
    result: List[str] = []
    options_result: Dict[str, bool] = {}
    for arg in args:
        if arg.lower() in options:
            options_result[arg.lower()[1:]] = True
        else:
            result.append(arg)
    return result, options_result


def flgtxt(arg: str) -> Callable[[Callable], Callable]:
    def decorator(func: Callable) -> Callable:
        setattr(func, "flgtxt", arg)
        return func

    return decorator


def copy_file(
    from_fs: AbstractFilesystem,
    from_entry: AbstractDirectoryEntry,
    to_fs: AbstractFilesystem,
    to_path: str,
    contiguous: Optional[bool],
    verbose: int,
    cmd: str = "COPY",
) -> None:
    try:
        content = from_fs.read_bytes(from_entry.fullname)
        to_fs.write_bytes(to_path, content, from_entry.creation_date, contiguous)
    except Exception:
        raise
        if verbose:
            traceback.print_exc()
        raise Exception(f"?{cmd}-F-Error copying {from_entry.fullname}")


class Shell(cmd.Cmd):
    verbose: bool = False
    volumes: Volumes
    cmd_matching: PartialMatching

    def __init__(self, verbose: bool = False):
        cmd.Cmd.__init__(self)
        self.verbose = verbose
        self.volumes = Volumes()
        # self.prompt="."
        self.postcmd(False, "")
        self.history_file = os.path.expanduser(HISTORY_FILENAME)
        # Init readline and history
        if readline is not None:
            if sys.platform == "darwin":
                readline.parse_and_bind("bind ^I rl_complete")
            else:
                readline.parse_and_bind("tab: complete")
                readline.parse_and_bind("set bell-style none")
            readline.set_completer(self.complete)
            try:
                if self.history_file:
                    readline.set_history_length(HISTORY_LENGTH)
                    readline.read_history_file(self.history_file)
            except IOError:
                pass
        # Process cmd names
        self.cmd_matching = PartialMatching()
        for name in self.get_names():
            if name[:3] == "do_":
                f = getattr(self, name)
                flgtxt = getattr(f, "flgtxt", None)
                if flgtxt:
                    self.cmd_matching.add(flgtxt.lower())

    def completenames(self, text: str, *ignored: Any) -> List[str]:
        dotext = "do_" + text.lower()
        return ["%s " % a[3:] for a in self.get_names() if a.startswith(dotext)] + [
            "%s:" % a for a in self.volumes.volumes.keys() if a.startswith(text.upper())
        ]

    def completedefault(self, text, state, *ignored):
        def add_slash(fs: AbstractFilesystem, filename: str) -> str:
            try:
                if fs.isdir(filename):
                    filename = filename + "/"
                return filename.replace(" ", "\\ ")
            except:
                pass
            return filename

        try:
            has_volume_id = ":" in text
            if text:
                volume_id, path = splitdrive(text)
            else:
                volume_id = DEFAULT_VOLUME
                path = ""
            pattern = path + "*"
            fs = self.volumes.get(volume_id)
            result: List[str] = []
            for x in fs.filter_entries_list(pattern):
                if has_volume_id:
                    result.append("%s:%s" % (volume_id, add_slash(fs, x.fullname)))
                else:
                    result.append("%s" % add_slash(fs, x.fullname))
            return result
        except Exception:
            pass  # no problem :-)
        return []

    def postloop(self) -> None:
        if readline is not None:
            # Cleanup and write history file
            readline.set_completer(None)
            try:
                if self.history_file:
                    readline.set_history_length(HISTORY_LENGTH)
                    readline.write_history_file(self.history_file)
            except:
                pass

    def cmdloop(self, intro: Optional[str] = None) -> None:
        self.update_prompt()
        try:
            return cmd.Cmd.cmdloop(self, intro)
        except KeyboardInterrupt:
            sys.stdout.write("\n")

    def update_prompt(self) -> None:
        self.prompt = "[%s] " % self.volumes.get_pwd()

    def postcmd(self, stop: bool, line: str) -> bool:
        self.update_prompt()
        return stop

    def onecmd(self, line: str, catch_exceptions: bool = True, batch: bool = False) -> bool:
        try:
            cmd, arg, line = self.parseline(line)
            if not line:
                sys.stdout.write("\n")
                return self.emptyline()
            if cmd is None:
                self.default(line)
                return False
            self.lastcmd = line
            if line == "EOF":
                self.lastcmd = ""
            if cmd == "":
                self.default(line)
                return False
            else:
                cmd = self.cmd_matching.get(cmd) or cmd
                try:
                    func = getattr(self, "do_" + cmd)
                except AttributeError:
                    self.default(line)
                    return False
                return bool(func(arg))
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            sys.stdout.write("\n")
            return False
        except SystemExit as ex:
            if not catch_exceptions:
                raise ex
            return True
        except Exception as ex:
            if not catch_exceptions:
                raise ex
            message = str(sys.exc_info()[1])
            sys.stdout.write(f"{message}\n")
            if self.verbose:
                traceback.print_exc()
            if batch:
                raise ex
            return False

    def parseline(self, line: str) -> Tuple[Optional[str], Optional[str], str]:
        """
        Parse the line into a command name and arguments
        """
        line = line.strip()
        if not line:
            return None, None, line
        elif line[0] == '?':
            line = f"help {line[1:]}"
        elif line[0] == '!':
            line = f"shell {line[1:]}"
        elif line[0] == '@':
            line = f"batch {line[1:]}"
        i, n = 0, len(line)
        while i < n and line[i] in self.identchars:
            i = i + 1
        cmd, arg = line[:i], line[i:].strip()
        return cmd.lower(), arg, line

    def default(self, line: str) -> bool:
        if line.endswith(":"):
            self.volumes.set_default_volume(line)
            return False
        else:
            raise Exception("?KMON-F-Illegal command")

    def emptyline(self) -> bool:
        sys.stdout.write("\n")
        return False

    @flgtxt("DIR_ECTORY")
    def do_directory(self, line: str) -> None:
        # fmt: off
        """
DIR             Lists file directories

  SYNTAX
        DIR [/options] [[volume:][filespec]]

  SEMANTICS
        This command generates a listing of the directory you specify.

  OPTIONS
   BRIEF
        Lists only file names and file types
   UIC
        Lists all UIC on a device (only for DOS-11)

  EXAMPLES
        DIR A:*.SAV
        DIR SY:

        """
        # fmt: on
        args, options = extract_options(line, "/brief", "/uic")
        if len(args) > 1:
            sys.stdout.write("?DIR-F-Too many arguments\n")
            return
        if args:
            volume_id, pattern = splitdrive(args[0])
        else:
            volume_id = DEFAULT_VOLUME
            pattern = None
        fs = self.volumes.get(volume_id, cmd="DIR")
        fs.dir(volume_id, pattern, options)

    def do_ls(self, line: str) -> None:
        self.do_directory(line)

    @flgtxt("TY_PE")
    def do_type(self, line: str) -> None:
        # fmt: off
        """
TYPE            Outputs files to the terminal

  SYNTAX
        TYPE [volume:]filespec

  EXAMPLES
        TYPE A.TXT

        """
        # fmt: on
        args = shlex.split(line)
        if not args:
            line = ask("File? ")
            args = shlex.split(line)
        if len(args) > 1:
            sys.stdout.write("?TYPE-F-Too many arguments\n")
            return
        volume_id, pattern = splitdrive(args[0])
        fs = self.volumes.get(volume_id, cmd="TYPE")
        match = False
        for x in fs.filter_entries_list(pattern):
            match = True
            content = fs.read_bytes(x.fullname)
            if content is not None:
                os.write(sys.stdout.fileno(), content)
                sys.stdout.write("\n")
        if not match:
            raise Exception("?TYPE-F-No files")

    @flgtxt("COP_Y")
    def do_copy(self, line: str) -> None:
        # fmt: off
        """
COPY            Copies files

  SYNTAX
        COPY [input-volume:]input-filespec [output-volume:][output-filespec]

  OPTIONS
   CONTIGUOUS
        Specifies that the output file is to be contiguous,
        if supported by the taget filesystem
   NOCONTIGUOUS
        Specifies that the output file is to be noncontiguous,
        if supported by the taget filesystem

  EXAMPLES
        COPY *.TXT DK:

        """
        # fmt: on
        args, options = extract_options(line, "/contiguous", "/nocontiguous")
        if len(args) > 2:
            sys.stdout.write("?COPY-F-Too many arguments\n")
            return
        cfrom = len(args) > 0 and args[0]
        to = len(args) > 1 and args[1]
        if not cfrom:
            cfrom = ask("From? ")
        from_volume_id, cfrom = splitdrive(cfrom)
        from_fs = self.volumes.get(from_volume_id, cmd="COPY")
        if not to:
            to = ask("To? ")
        to_volume_id, to = splitdrive(to)
        to_fs = self.volumes.get(to_volume_id, cmd="COPY")
        from_len = len(list(from_fs.filter_entries_list(cfrom)))
        from_list = from_fs.filter_entries_list(cfrom)
        contiguous = None
        if options.get("contiguous"):
            contiguous = True
        elif options.get("nocontiguous"):
            contiguous = False
        if from_len == 0:  # No files
            raise Exception("?COPY-F-No files")
        elif from_len == 1:  # One file to be copied
            source = list(from_list)[0]
            if not to:
                to_pwd = self.volumes.get(to_volume_id).get_pwd()
                to_path = os.path.join(to_pwd, source.basename)
            elif to and to_fs.isdir(to):
                to_path = os.path.join(to, source.basename)
            else:
                to_path = to
            from_entry = from_fs.get_file_entry(source.fullname)
            if not from_entry:
                raise Exception(f"?COPY-F-Error copying {source.fullname}")
            sys.stdout.write("%s:%s -> %s:%s\n" % (from_volume_id, source.fullname, to_volume_id, to_path))
            copy_file(from_fs, from_entry, to_fs, to_path, contiguous, self.verbose, cmd="COPY")
        else:
            if not to:
                to = self.volumes.get(to_volume_id).get_pwd()
            elif not to_fs.isdir(to):
                raise Exception("?COPY-F-Target must be a volume or a directory")
            for from_entry in from_fs.filter_entries_list(cfrom):
                if to:
                    to_path = os.path.join(to, from_entry.basename)
                else:
                    to_path = from_entry.basename
                sys.stdout.write("%s:%s -> %s:%s\n" % (from_volume_id, from_entry.fullname, to_volume_id, to_path))
                copy_file(from_fs, from_entry, to_fs, to_path, contiguous, self.verbose, cmd="COPY")

    @flgtxt("DEL_ETE")
    def do_delete(self, line: str) -> None:
        # fmt: off
        """
DELETE          Removes files from a volume

  SYNTAX
        DELETE [volume:]filespec

  SEMANTICS
        This command deletes the files you specify from the volume.

  EXAMPLES
        DELETE *.OBJ

        """
        # fmt: on
        args = shlex.split(line)
        if not args:
            line = ask("Files? ")
            args = shlex.split(line)
        volume_id, pattern = splitdrive(args[0])
        fs = self.volumes.get(volume_id, cmd="DEL")
        match = False
        for x in fs.filter_entries_list(pattern):
            match = True
            if not x.delete():
                sys.stdout.write("?DEL-F-Error deleting %s\n" % x.fullname)
        if not match:
            raise Exception("?DEL-F-No files")

    @flgtxt("E_XAMINE")
    def do_examine(self, line: str) -> None:
        # fmt: off
        """
EXAMINE         Examines disk/block/file structure

  SYNTAX
        EXAMINE volume:[filespec/block num]

        """
        # fmt: on
        volume_id, block = splitdrive(line or "")
        fs = self.volumes.get(volume_id)
        fs.examine(block)

    @flgtxt("CR_EATE")
    def do_create(self, line: str) -> None:
        # fmt: off
        """
CREATE          Creates a file with a specific name and size

  SYNTAX
        CREATE [volume:]filespec size

  SEMANTICS
        Filespec is the device name, file name, and file type
        of the file to create.
        The size specifies the number of blocks to allocate.

  EXAMPLES
        CREATE NEW.DSK 200

        """
        # fmt: on
        args = shlex.split(line)
        if len(args) > 2:
            sys.stdout.write("?CREATE-F-Too many arguments\n")
            return
        path = len(args) > 0 and args[0]
        size = len(args) > 1 and args[1]
        if not path:
            path = ask("File? ")
        if not size:
            size = ask("Size? ")
        try:
            length = int(size)
            if length < 0:
                raise ValueError
        except ValueError:
            raise Exception("?KMON-F-Invalid value specified with option")
        volume_id, fullname = splitdrive(path)
        fs = self.volumes.get(volume_id, cmd="CREATE")
        fs.create_file(fullname, length)

    @flgtxt("MO_UNT")
    def do_mount(self, line: str) -> None:
        # fmt: off
        """
MOUNT           Assigns a logical disk unit to a file

  SYNTAX
        MOUNT [/options] volume: [volume:]filespec

  SEMANTICS
        Associates a logical disk unit with a file.

  OPTIONS
   DOS
        Mount DOS-11 filesystem
   MAGTAPE
        Mount DOS-11 MagTape filesystem
   FILES11
        Mount Files-11 filesystem

  EXAMPLES
        MOUNT AB: SY:AB.DSK
        MOUNT /DOS AB: SY:DOS.DSK

        """
        # fmt: on
        args, options = extract_options(line, "/dos", "/dos11", "/dos11mt", "/magtape", "/files11", "/caps11")
        if len(args) > 2:
            sys.stdout.write("?MOUNT-F-Too many arguments\n")
            return
        logical = len(args) > 0 and args[0]
        path = len(args) > 1 and args[1]
        if not logical:
            logical = ask("Volume? ")
        if not path:
            path = ask("File? ")
        if options.get("dos") or options.get("dos11"):
            fstype = "dos11"
        elif options.get("dos11mt") or options.get("magtape"):
            fstype = "dos11mt"
        elif options.get("files11"):
            fstype = "files11"
        elif options.get("caps11"):
            fstype = "caps11"
        else:
            fstype = None
        self.volumes.mount(path, logical, fstype=fstype, verbose=self.verbose)

    @flgtxt("DIS_MOUNT")
    def do_dismount(self, line: str) -> None:
        # fmt: off
        """
DISMOUNT        Disassociates a logical disk assignment from a file

  SYNTAX
        DISMOUNT logical_name

  SEMANTICS
        Removes the association of a logical disk unit with its currently
        assigned file, thereby freeing it to be assigned to another file.

  EXAMPLES
        DISMOUNT AB:

        """
        # fmt: on
        args = shlex.split(line)
        if len(args) > 1:
            sys.stdout.write("?DISMOUNT-F-Too many arguments\n")
            return
        if args:
            logical = args[0]
        else:
            logical = ask("Volume? ")
        self.volumes.dismount(logical)

    @flgtxt("AS_SIGN")
    def do_assign(self, line: str) -> None:
        # fmt: off
        """
ASSIGN          Associates a logical device name with a device

  SYNTAX
        ASSIGN device-name logical-device-name

  SEMANTICS
        Associates a logical device name with a device.
        Logical-device-name is one to three alphanumeric characters long.

  EXAMPLES
        ASSIGN DL0: INP:

        """
        # fmt: on
        args, options = extract_options(line)
        if len(args) > 2:
            sys.stdout.write("?ASSIGN-F-Too many arguments\n")
            return
        volume_id = len(args) > 0 and args[0]
        logical = len(args) > 1 and args[1]
        if not volume_id:
            volume_id = ask("Device name? ")
        if not logical:
            logical = ask("Logical name? ")
        self.volumes.assign(volume_id, logical, verbose=self.verbose)

    @flgtxt("DEA_SSIGN")
    def do_deassign(self, line: str) -> None:
        # fmt: off
        """
DEASSIGN        Removes logical device name assignments

  SYNTAX
        DEASSIGN logical-device-name

  SEMANTICS
        The DEASSIGN command disassociates a logical name.

  EXAMPLES
        DEASSIGN INP:

        """
        # fmt: on
        args = shlex.split(line)
        if len(args) > 1:
            sys.stdout.write("?DEASSIGN-F-Too many arguments\n")
            return
        if args:
            logical = args[0]
        else:
            logical = ask("Volume? ")
        self.volumes.deassign(logical, cmd="DEASSIGN")

    @flgtxt("INI_TIALIZE")
    def do_initialize(self, line: str) -> None:
        # fmt: off
        """
INITIALIZE      Writes an empty device directory on the specified volume

  SYNTAX
        INITIALIZE volume:

        """
        # fmt: on
        if not line:
            line = ask("Volume? ")
        fs = self.volumes.get(line)
        fs.initialize()

    def do_cd(self, line: str) -> None:
        # fmt: off
        """
CD              Changes or displays the current working drive and directory

  SYNTAX
        CD [[volume:][filespec]]

        """
        # fmt: on
        args = shlex.split(line)
        if len(args) > 1:
            sys.stdout.write("?CD-F-Too many arguments\n")
            return
        elif len(args) == 0:
            sys.stdout.write("%s\n" % self.volumes.get_pwd())
            return
        if not self.volumes.chdir(args[0]):
            sys.stdout.write("?CD-F-Directory not found\n")

    def do_batch(self, line: str) -> None:
        # fmt: off
        """
@               Executes a command file

  SYNTAX
        @filespec

  SEMANTICS
        You can group a collection of commands that you want to execute
        sequentially into a command file.
        This command executes the command file.

  EXAMPLES
        @MAKE.COM

        """
        # fmt: on
        line = line.strip()
        if not line:
            return
        try:
            with open(line, "r") as f:
                for line in f:
                    if line.startswith("!"):
                        continue
                    self.onecmd(line.strip(), catch_exceptions=False, batch=True)
        except FileNotFoundError:
            raise Exception("?KMON-F-File not found")

    def do_pwd(self, line: str) -> None:
        # fmt: off
        """
PWD             Displays the current working drive and directory

  SYNTAX
        PWD

        """
        sys.stdout.write("%s\n" % self.volumes.get_pwd())

    @flgtxt("SH_OW")
    def do_show(self, line: str) -> None:
        # fmt: off
        """
SHOW            Displays the volume assignment

  SYNTAX
        SHOW

        """
        # fmt: on
        sys.stdout.write("Volumes\n")
        sys.stdout.write("-------\n")
        for k, v in self.volumes.volumes.items():
            label = f"{k}:"
            sys.stdout.write(f"{label:<6} {v}\n")
        for k, v in self.volumes.logical.items():
            label = f"{k}:"
            sys.stdout.write(f"{label:<4} = {v}:\n")

    def do_exit(self, line: str) -> None:
        # fmt: off
        """
EXIT            Exit the shell

  SYNTAX
        EXIT
        """
        # fmt: on
        raise SystemExit

    def do_quit(self, line: str) -> None:
        raise SystemExit

    @flgtxt("H_ELP")
    def do_help(self, arg: str) -> None:
        # fmt: off
        """
HELP            Displays commands help

  SYNTAX
        HELP [topic]

        """
        # fmt: on
        if arg and arg != "*":
            if arg == "@":
                arg = "batch"
            try:
                arg = self.cmd_matching.get(arg) or arg
                doc = getattr(self, f"do_{arg}").__doc__
                if doc:
                    self.stdout.write(f"{str(doc)}\n")
                    return
            except AttributeError:
                pass
            self.stdout.write("%s\n" % str(self.nohelp % (arg,)))
        else:
            names = ["do_batch"] + sorted([x for x in self.get_names() if x.startswith("do_") and x != "do_batch"])
            for name in names:
                if getattr(self, name).__doc__:
                    sys.stdout.write(getattr(self, name).__doc__.split("\n")[1])
                    sys.stdout.write("\n")

    def do_shell(self, arg: str) -> None:
        # fmt: off
        """
SHELL           Executes a system shell command

  SYNTAX
        SHELL command

        """
        # fmt: on
        os.system(arg)

    def do_EOF(self, line: str) -> bool:
        return True


class CustomAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        fstype = option_string.strip("-")
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
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="display verbose output",
    )
    parser.add_argument(
        "--dos11",
        nargs=1,
        dest="image",
        action=CustomAction,
        help="mount a DOS-11 disk or DecTape",
    )
    parser.add_argument(
        "--files11",
        nargs=1,
        dest="image",
        action=CustomAction,
        help="mount a Files11 disk",
    )
    parser.add_argument(
        "--magtape",
        nargs=1,
        dest="image",
        action=CustomAction,
        help="mount a DOS-11 Magtape",
    )
    parser.add_argument(
        "--caps11",
        nargs=1,
        dest="image",
        action=CustomAction,
        help="mount a CAPS-11 cassette",
    )
    parser.add_argument(
        "--rt11",
        nargs=1,
        dest="image",
        action=CustomAction,
        help="mount a RT-11 disk",
    )
    parser.add_argument(
        "disk",
        nargs="*",
        help="disk to be mounted",
    )
    options = parser.parse_args()
    shell = Shell(verbose=options.verbose)
    # Mount disks
    i = 0
    for fstype, dsk in getattr(options, "mounts", []):
        shell.volumes.mount(dsk, f"DL{i}:", fstype=fstype, verbose=shell.verbose)
        i = i + 1
    for i, dsk in enumerate(options.disk):
        shell.volumes.mount(dsk, f"DL{i}:", verbose=shell.verbose)
        i = i + 1
    # Change dir
    if options.dir:
        shell.volumes.set_default_volume(options.dir)
    # Execute the commands
    if options.c:
        try:
            for command in options.c:
                shell.onecmd(command, batch=True)
        except Exception:
            pass
    # Start interactive shell
    if options.interactive or not options.c:
        shell.cmdloop()
