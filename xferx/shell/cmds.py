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

import importlib.resources
import os
import shlex
import sys
import typing as t

from ..commons import ASCII, BLOCK_SIZE, LoggingLevel, dump_struct, splitdrive
from ..volumes import DEFAULT_VOLUME, FILESYSTEMS
from .commons import (
    CommandError,
    Commands,
    ShellContext,
    ask,
    copy_file,
    extract_options,
    get_int_option,
    get_str_option,
    parse_size,
    split_arguments,
)

__all__ = ["cmds"]


cmds = Commands()


@cmds.register("DIR_ECTORY", aliases=["LS"])
def directory(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
DIR             Lists file directories

  SYNTAX
        DIR [/options] [[volume:][filespec]]

  SEMANTICS
        This command generates a listing of the directory you specify.

  OPTIONS
   BRIEF
        Lists only file names
   FULL
        Lists the entire directory, including unused areas
   UIC
        Lists all UIC/PPN/account on a device (DOS-11, RSTS/E)

  EXAMPLES
        DIR A:*.SAV
        DIR SY:
        DIR/UIC

    """
    # fmt: on
    args, options = extract_options(args, "/brief", "/uic", "/full")
    if not args:
        args = [f"{DEFAULT_VOLUME}:"]  # default to current volume
    if options.get("uic") or options.get("account"):
        # Listing of all the accounts on a volume
        for arg in args:
            try:
                context.volumes.show_accounts(arg, options, cmd="DIR")  # type: ignore
            except OSError as ex:
                raise CommandError(f"?DIR-F-{ex.strerror} {arg}")
        return
    if options.get("brief"):
        # Listing of files in brief format
        for arg in args:
            try:
                for x in context.volumes.filter_entries_list(arg):
                    sys.stdout.write(f"{x.name}\n")
            except OSError as ex:
                raise CommandError(f"?DIR-F-{ex.strerror} {arg}")
        return
    for arg in args:
        try:
            context.volumes.dir(arg, options, cmd="DIR")  # type: ignore
        except OSError as ex:
            raise CommandError(f"?DIR-F-{ex.strerror} {arg}")


@cmds.register("TY_PE")
def type(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
TYPE            Outputs files to the terminal

  SYNTAX
        TYPE [volume:]filespec

  EXAMPLES
        TYPE A.TXT

    """
    # fmt: on
    if not args:
        line = ask("File? ")
        args = split_arguments(line)
    match = False
    for arg in args:
        for entry in context.volumes.filter_entries_list(pattern=arg, cmd="TYPE"):
            match = True
            content = entry.read_bytes(file_mode=ASCII)
            if content is not None:
                os.write(sys.stdout.fileno(), content)
                sys.stdout.write("\n")
    if not match:
        raise CommandError("?TYPE-F-No files")


@cmds.register("COP_Y")
def copy(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
COPY            Copies files

  SYNTAX
        COPY [/options] [input-volume:]input-filespec [output-volume:][output-filespec]

  OPTIONS
   ASCII
        Copy as ASCII text
   TYPE:type
        Specifies that the output file type, if supported by the target filesystem
        See the SHOW TYPES command for a list of filesystems.
   FORK:name
        Specifies the file fork to be copied, if supported by the filesystem
   TO-FORK:name
        Specifies the file fork to be created on the target filesystem

  EXAMPLES
        COPY *.TXT DK:
        COPY /ASCII /TYPE:CONTIGUOUS LICENSE DK:

    """
    # fmt: on
    args, options = extract_options(args, "/ascii", "/type", "/fork", "/to-fork")
    if len(args) > 2:
        raise CommandError("?COPY-F-Too many arguments")
    file_mode = ASCII if options.get("ascii") else None
    cfrom = len(args) > 0 and args[0]
    to = len(args) > 1 and args[1]
    if not cfrom:
        cfrom = ask("From? ")
    from_volume_id, cfrom = splitdrive(cfrom)
    from_fs = context.volumes.get_volume(from_volume_id, cmd="COPY")
    if not to:
        to = ask("To? ")
    to_volume_id, to = splitdrive(to)
    to_fs = context.volumes.get_volume(to_volume_id, cmd="COPY")
    to_file_type = get_str_option(options, "type")
    from_len = len(list(from_fs.filter_entries_list(cfrom)))
    from_list = from_fs.filter_entries_list(cfrom)
    from_fork = get_str_option(options, "fork")
    to_fork = get_str_option(options, "to-fork")
    if from_len == 0:  # No files
        raise CommandError("?COPY-F-No files")
    elif from_len == 1:  # One file to be copied
        source = list(from_list)[0]
        if not to:
            to_path = source.name
        elif to and to_fs.is_dir(to):
            to_path = to_fs.path_join(to, source.name)
        else:
            to_path = to
        from_entry = from_fs.get_file_entry(source.fullname)
        if not from_entry:
            raise CommandError(f"?COPY-F-Error copying {source.fullname}")
        sys.stderr.write("%s:%s -> %s:%s\n" % (from_volume_id, source.fullname, to_volume_id, to_path))
        copy_file(from_entry, from_fork, to_fs, to_path, to_fork, to_file_type, file_mode, context.level, cmd="COPY")
    else:
        if not to:
            to = context.volumes.get_volume(to_volume_id).get_pwd()
        elif not to_fs.is_dir(to):
            raise CommandError("?COPY-F-Target must be a volume or a directory")
        for from_entry in from_fs.filter_entries_list(cfrom):
            if to:
                to_path = to_fs.path_join(to, from_entry.name)  # TODO
            else:
                to_path = from_entry.name
            sys.stderr.write("%s:%s -> %s:%s\n" % (from_volume_id, from_entry.fullname, to_volume_id, to_path))
            copy_file(
                from_entry, from_fork, to_fs, to_path, to_fork, to_file_type, file_mode, context.level, cmd="COPY"
            )


@cmds.register("DEL_ETE")
def delete(context: "ShellContext", args: t.List[str]) -> None:
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
    if not args:
        line = ask("Files? ")
        args = split_arguments(line)
    for arg in args:
        match = False
        for x in list(
            context.volumes.filter_entries_list(pattern=arg, expand=False, cmd="DELETE")
        ):  # don't expand directories
            match = True
            if x.delete() and context.level <= LoggingLevel.INFO:
                sys.stderr.write(f"?DELETE-I-{x.fullname} deleted\n")
            else:
                sys.stderr.write(f"?DELETE-F-Error deleting {x.fullname}\n")
    if not match:
        raise CommandError("?DELETE-F-No files")


@cmds.register("E_XAMINE")
def examine(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
EXAMINE         Examines disk structure

  SYNTAX
        EXAMINE [/options] volume:

  OPTIONS
   FULL
        Lists the entire directory, including unused areas

    """
    # fmt: on
    args, options = extract_options(args, "/free", "/bitmap", "/diskid", "/full")
    if not args:
        args = ask("From? ").split()
    for arg in args:
        context.volumes.examine(arg=arg, options=options, cmd="EXAMINE")


@cmds.register("DU_MP")
def dump(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
DUMP            Prints formatted data dumps of files or devices

  SYNTAX
        DUMP [/options] filespec

  SEMANTICS
        Filespec represents the device or file to be dumped.

  OPTIONS
   START:block
        Specifies the first block to be dumped
   END:block
        Specifies the last block to be dumped
   FORK:name
        Specifies the file fork to be dumped, if supported by the filesystem

  EXAMPLES
        DUMP A.OBJ
        DUMP /START:6 /END:6 DL0:
        DUMP /FORK:RESOURCE "Read Me"

    """
    # fmt: on
    args, options = extract_options(args, "/start", "/end", "/fork")
    start = get_int_option(options, "start")
    end = get_int_option(options, "end")
    fork = get_str_option(options, "fork")
    if not args:
        args = ask("From? ").split()
    for arg in args:
        try:
            context.volumes.dump(fullname=arg, start=start, end=end, cmd="DUMP", fork=fork)
        except FileNotFoundError:
            raise CommandError("?DUMP-F-File not found")


@cmds.register("CR_EATE")
def create(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
CREATE          Creates files or directories

  SYNTAX
        CREATE [/options] [volume:]filespec

  SEMANTICS
        This command creates a file or directory on the volume you specify.
        The default option is to create a file.

  OPTIONS
   FILE
        Creates a file with a specific name and size
   DIRECTORY
        Creates a directory
   ALLOCATE:size
        Specifies the size of the file to be allocated
        Size is specified in bytes (B), blocks (default), kilobytes (K), or megabytes (M).
   TYPE:type
        Specifies the file type
        See the SHOW TYPES command for a list of filesystems.

  EXAMPLES
        CREATE /ALLOCATE:200 new.dsk
        CREATE /ALLOCATE:10M disk_10m.dsk

    """
    # fmt: on
    args, options = extract_options(args, "/file", "/dir", "/directory", "/uic", "/allocate", "/type")
    if len(args) > 1:
        raise CommandError("?CREATE-F-Too many arguments")
    if "file" in options:
        kind = "file"
    if "directory" in options or "dir" in options:
        kind = "directory"
    else:
        kind = "file"
    path = len(args) > 0 and args[0]
    if not path:
        path = ask("File? ")
    if kind == "directory":
        # Create a directory
        context.volumes.create_directory(fullname=path, options=options, cmd="CREATE")
    else:
        # Create a file
        metadata = {}
        try:
            size = parse_size(options.get("allocate") or ask("Size? "))  # type: ignore
            if size < 0:
                raise ValueError
            metadata["number_of_blocks"] = size // BLOCK_SIZE
        except:
            raise CommandError("?CREATE-F-Invalid value specified with option")
        if options.get("type"):
            metadata["file_type"] = options.get("type")  # type: ignore
        context.volumes.create_file(
            fullname=path,
            size=size,
            metadata=metadata,
            cmd="CREATE",
        )


@cmds.register("MO_UNT")
def mount(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
MOUNT           Assigns a logical disk unit to a file

  SYNTAX
        MOUNT [/options] volume: [volume:]filespec

  SEMANTICS
        Associates a logical disk unit with a file.
        See the SHOW FILESYSTEMS command for a list of filesystems
        that can be mounted.

  EXAMPLES
        MOUNT AB: SY:rt11v503.dsk
        MOUNT /DOS11 AB: SY:dos.dsk
        MOUNT /UNIX7 AB: SY:unix7.dsk
        MOUNT /DMS /DEV:DECTAPE AB: SY:dms.tu56

  OPTIONS
   DEV:device type
        Specifies the device type, if supported by the target filesystem.

    """
    # fmt: on
    fs_args = [f"/{x}" for x in FILESYSTEMS.keys()]
    args, options = extract_options(args, "/dev", *fs_args)
    try:
        device_type: t.Optional[str] = options["dev"].lower()  # type: ignore
    except Exception:
        device_type = None
    if len(args) > 2:
        raise CommandError("?MOUNT-F-Too many arguments")
    logical = len(args) > 0 and args[0]
    path = len(args) > 1 and args[1]
    if not logical:
        logical = ask("Volume? ")
    if not path:
        path = ask("File? ")
    fstype = None
    for filesystem in FILESYSTEMS.keys():
        if options.get(filesystem):
            fstype = filesystem
            break
    context.volumes.mount(path, logical, fstype=fstype, level=context.level, device_type=device_type)


@cmds.register("DIS_MOUNT")
def dismount(context: "ShellContext", args: t.List[str]) -> None:
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
    if len(args) > 1:
        raise CommandError("?DISMOUNT-F-Too many arguments")
    if args:
        logical = args[0]
    else:
        logical = ask("Volume? ")
    context.volumes.dismount(logical)


@cmds.register("AS_SIGN")
def assign(context: "ShellContext", args: t.List[str]) -> None:
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
    args, options = extract_options(args)
    if len(args) > 2:
        raise CommandError("?ASSIGN-F-Too many arguments")
    volume_id = len(args) > 0 and args[0]
    logical = len(args) > 1 and args[1]
    if not volume_id:
        volume_id = ask("Device name? ")
    if not logical:
        logical = ask("Logical name? ")
    context.volumes.assign(volume_id, logical, level=context.level)


@cmds.register("DEA_SSIGN")
def deassign(context: "ShellContext", args: t.List[str]) -> None:
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
    if len(args) > 1:
        raise CommandError("?DEASSIGN-F-Too many arguments")
    if args:
        logical = args[0]
    else:
        logical = ask("Volume? ")
    context.volumes.deassign(logical, cmd="DEASSIGN")


@cmds.register("INI_TIALIZE")
def initialize(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
INITIALIZE      Writes an empty device directory on the specified volume

  SYNTAX
        INITIALIZE [/options] [volume:][filespec]]

  SEMANTICS
        Initializes the specified filesystem on the volume.
        Any data on the volume is lost.
        See the SHOW FILESYSTEMS command for a list of filesystems.

  EXAMPLES
        INIT /DOS11 /DEV:dectape test.tap

  OPTIONS
   NAME:name
        Specifies the volume name
   DEV:device type
        Specifies the device type, if supported by the target filesystem.

    """
    # fmt: on
    fs_args = [f"/{x}" for x in FILESYSTEMS.keys()]
    args, options = extract_options(args, "/name", "/dev", *fs_args)
    if len(args) > 1:
        raise CommandError("?INITIALIZE-F-Too many arguments")
    target = len(args) > 0 and args[0]
    if not target:
        target = ask("Volume? ")
    if target.endswith(":"):
        try:
            options["device_type"] = options["dev"].lower()  # type: ignore
        except Exception:
            pass
        context.volumes.initialize(target=target, options=options, cmd="INITIALIZE")
    else:
        filesystem_cls = None
        for k, v in FILESYSTEMS.items():
            if options.get(k):
                filesystem_cls = v
                break
        if filesystem_cls is None:
            raise CommandError("?INITIALIZE-F-Filesystem not specified")
        parent_volume_id, target_path = splitdrive(target)
        parent_fs = context.volumes.get_volume(parent_volume_id)
        target_file = parent_fs.open_file(target_path)
        try:
            device_type = options["dev"].lower()  # type: ignore
        except Exception:
            device_type = context.volumes.guess_device_type(target_path)
        if device_type:
            options["device_type"] = device_type
        fs = filesystem_cls.initialize(target_file, **options)
        fs.close()


@cmds.register("CD")
def cd(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
CD              Changes or displays the current working drive and directory

  SYNTAX
        CD [[volume:][filespec]]

    """
    # fmt: on
    if len(args) > 1:
        raise CommandError("?CD-F-Too many arguments")
    elif len(args) == 0:
        sys.stdout.write("%s\n" % context.volumes.get_pwd())
    else:
        try:
            context.volumes.chdir(args[0])
        except Exception as exc:
            raise CommandError("?CD-F-Directory not found") from exc


@cmds.register("@", aliases=["BATCH"])
def batch(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
@               Executes a command file

  SYNTAX
        @[volume:]filespec

  SEMANTICS
        You can group a collection of commands that you want to execute
        sequentially into a command file.
        This command executes the command file.

  EXAMPLES
        @MAKE.COM

    """
    # fmt: on
    from .batch import BatchContext

    if not args:
        return
    try:
        context = BatchContext(context, args[0])
        context.execute()
    except SystemExit:
        pass
    except FileNotFoundError:
        raise CommandError("?KMON-F-File not found")


@cmds.register("PWD")
def pwd(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
PWD             Displays the current working drive and directory

  SYNTAX
        PWD

    """
    sys.stdout.write(f"{context.volumes.get_pwd()}\n")


@cmds.register("SH_OW")
def show(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
SHOW            Displays software status

  SYNTAX
        SHOW [options] [volume:]

  SEMANTICS
	    SHOW displays the device assignments; other information
	    is displayed by specifying one or more option names.

  OPTIONS
   ACCOUNTS
        Show the accounts of a volume
   FILESYSTEMS
        Show the supported filesystems
   TYPES
        Show the file types of a volume
   VERSION
        Show the version of XFERX
   VOLUMES
        Show the device assignments

  EXAMPLES
        SHOW
        SHOW FILESYSTEMS
        SHOW TYPES DL0:

    """
    # fmt: on
    func = cmds.get(f"SHOW {args[0]}" if args else "SHOW VOLUMES")
    if func is None:
        raise CommandError("?SHOW-F-Illegal command")
    func(context, args)


@cmds.register("SHOW A_CCOUNTS")
def show_accounts(context: "ShellContext", args: t.List[str]) -> None:
    """
    SHOW ACCOUNTS     Show the accounts of a volume
    """
    # fmt: on
    if len(args) == 1:
        volume_id = context.volumes.get_default_volume()
    else:
        volume_id = args[1]
    volume = context.volumes.get_volume(volume_id, cmd="SHOW ACCOUNTS")
    volume.show_accounts(volume_id=volume_id, options={})


@cmds.register("SHOW T_YPES")
def show_type(context: "ShellContext", args: t.List[str]) -> None:
    """
    SHOW TYPES        Show the file types of a volume
    """
    # fmt: on
    if len(args) == 1:
        volume_id = context.volumes.get_default_volume()
    else:
        volume_id = args[1]
    sys.stdout.write("File Types\n")
    sys.stdout.write("----------\n")
    for item in context.volumes.get_types(volume_id=volume_id, cmd="SHOW TYPES"):
        sys.stdout.write(f"{item}\n")


@cmds.register("SHOW F_ILESYSTEMS", aliases=["SHOW FS"])
def show_filesystems(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
SHOW FILESYSTEMS  Show the supported filesystems
    """
    # fmt: on
    if args[1:]:
        for arg in args[1:]:
            fs = FILESYSTEMS.get(arg.lower())
            if fs:
                data = {
                    "Name": arg.upper(),
                    "Description": fs.fs_description,
                    "Platform": ",".join(fs.fs_platforms) if fs.fs_platforms else "Any",
                    "Metadata": ",".join(fs.fs_entry_metadata),
                    "Forks": ",".join(fs.fs_forks) if fs.fs_forks else "N/A",
                }
                sys.stdout.write(dump_struct(data, newline=True, format_label=False))
            else:
                raise CommandError(f"?SHOW-FS-F-Unknown filesystem {arg}")
    else:
        sys.stdout.write("Filesystems\n")
        sys.stdout.write("-----------\n")
        for k, v in sorted(FILESYSTEMS.items()):  # type: ignore
            sys.stdout.write(f"{k.upper():<10} {v.fs_description}\n")


@cmds.register("SHOW VE_RSION")
def show_version(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
SHOW VERSION      Show the version of XFERX
    """
    # fmt: on
    with importlib.resources.files("xferx").joinpath("VERSION").open("r", encoding="utf-8") as f:
        version = f.read().strip()
    sys.stdout.write(f"XFERX {version}\n")


@cmds.register("SHOW VO_LUMES")
def show_volumes(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
SHOW VOLUMES      Show the device assignments
    """
    # fmt: on
    sys.stdout.write("Volumes\n")
    sys.stdout.write("-------\n")
    for k, v in context.volumes.volumes.items():
        label = f"{k}:"
        sys.stdout.write(f"{label:<6} {v.fs_name.upper():<10} {v.source}\n")
    for k, v in context.volumes.logical.items():  # type: ignore
        label = f"{k}:"
        sys.stdout.write(f"{label:<4} = {v}:\n")


@cmds.register("EXIT", aliases=["QUIT"])
def exit(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
EXIT            Exit the shell

  SYNTAX
        EXIT
    """
    # fmt: on
    raise SystemExit


@cmds.register("H_ELP")
def help(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
HELP            Displays commands help

  SYNTAX
        HELP [topic]

    """
    # fmt: on
    if args and args[0] != "*":
        arg = args[0]
        func = cmds.get(arg)
        if func is None:
            raise CommandError(f"?HELP-F-Help not available for {arg}")
        sys.stdout.write(f"{func.__doc__}\n")
    else:
        for name, command in sorted(cmds.commands.items()):
            if not " " in name and command.__doc__ is not None:
                sys.stdout.write(command.__doc__.split("\n")[1])
                sys.stdout.write("\n")


@cmds.register("SHELL")
def shell(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
SHELL           Executes a system shell command

  SYNTAX
        SHELL command

    """
    # fmt: on
    os.system(shlex.join(args))


@cmds.register("ECHO")
def echo(context: "ShellContext", args: t.List[str]) -> None:
    # fmt: off
    """
ECHO            Write arguments to the terminal

  SYNTAX
        ECHO [arg ...]

  EXAMPLES
        ECHO Hello World

    """
    # fmt: on
    line = " ".join(args).strip()
    sys.stdout.write(f"{line}\n")
