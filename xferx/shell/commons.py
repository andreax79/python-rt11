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

import re
import string
import sys
import traceback
import typing as t

from ..abstract import XFERXPath, AbstractFilesystem
from ..commons import BLOCK_SIZE, LoggingLevel

if t.TYPE_CHECKING:
    from ..volumes import Volumes
    from .kmon import Shell

__all__ = [
    "CommandError",
    "Commands",
    "PartialMatching",
    "ShellCommand",
    "ShellContext",
    "add_slash",
    "ask",
    "copy_file",
    "extract_options",
    "get_int_option",
    "get_str_option",
    "parse_size",
    "split_arguments",
    "split_command_line",
]

IDENTCHARS = string.ascii_letters + string.digits + "@_"
SPLIT_WIN32_RE = re.compile(r'"((?:""|\\["\\]|[^"])*)"?()|(\\\\(?=\\*")|\\")|([^\s"&|<>]+)|(\s+)|(.)')
SPLIT_POSIX_RE = re.compile(r'''"((?:\\["\\]|[^"])*)"|'([^']*)'|(\\.)|([^\s'"\\&|<>]+)|(\s+)|(.)''')


class CommandError(Exception):
    """
    Exception raised for command errors
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def ask(prompt: str) -> str:
    """
    Prompt the user for input with the given prompt message
    """
    result = ""
    while not result:
        result = input(prompt).strip()
    return result


def extract_options(args: t.List[str], *options: str) -> t.Tuple[t.List[str], t.Dict[str, t.Union[bool, str]]]:
    """
    Extract options from the command line
    """
    result: t.List[str] = []
    options_result: t.Dict[str, t.Union[bool, str]] = {}
    for arg in args:
        if ':' in arg:
            key, value = arg.split(':', 1)
            if key.lower() in options:
                options_result[key.lower()[1:]] = value
            else:
                result.append(arg)
        elif arg.lower() in options:
            options_result[arg.lower()[1:]] = True
        else:
            result.append(arg)
    return result, options_result


def get_int_option(
    options: t.Dict[str, t.Union[bool, str]], key: str, default: t.Optional[int] = None
) -> t.Optional[int]:
    """
    Get an integer option from the options dictionary
    """
    try:
        value = int(options[key])
        if value < 0:
            raise ValueError
        return value
    except KeyError:
        return default
    except ValueError:
        raise CommandError("?KMON-F-Invalid value specified with option")


def get_str_option(
    options: t.Dict[str, t.Union[bool, str]], key: str, default: t.Optional[str] = None
) -> t.Optional[str]:
    """
    Get a string option from the options dictionary
    """
    return options.get(key) if isinstance(options.get(key), str) else default  # type: ignore


def copy_file(
    from_entry: XFERXPath,
    from_fork: t.Optional[str],
    to_fs: AbstractFilesystem,
    to_path: str,
    to_fork: t.Optional[str],
    to_file_type: t.Optional[str],
    file_mode: t.Optional[str],
    level: LoggingLevel,
    cmd: str = "COPY",
) -> None:
    if not to_file_type:
        to_file_type = from_entry.file_type
    try:
        content = from_entry.read_bytes(file_mode, from_fork)
        metadata = from_entry.metadata
        if to_file_type:
            metadata["file_type"] = to_file_type
        to_fs.write_bytes(
            fullname=to_path,
            content=content,
            fork=to_fork,
            metadata=metadata,
            file_mode=file_mode,
        )
    except Exception as ex:
        if level <= LoggingLevel.DEBUG:
            traceback.print_exc()
        message = getattr(ex, "strerror", "") or str(ex)
        raise CommandError(f"?{cmd}-F-Error copying {from_entry.fullname} to {to_path}: {message}")


def add_slash(fs: AbstractFilesystem, filename: str) -> str:
    """
    Add a trailing slash to a filename if it is a directory
    """
    try:
        if fs.is_dir(filename):
            filename = filename + "/"
        return filename.replace(" ", "\\ ")
    except Exception:
        pass
    return filename


def split_command_line(line: str) -> t.Tuple[t.Optional[str], t.Optional[str]]:
    """
    Split a command line into command and argument
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None, None
    elif line[0] == '?':
        line = f"help {line[1:]}"
    elif line[0] == '!':
        line = f"shell {line[1:]}"
    elif line[0] == '@':
        line = f"batch {line[1:]}"
    i, n = 0, len(line)
    while i < n and line[i] in IDENTCHARS:
        i = i + 1
    cmd, arg = line[:i], line[i:].strip()
    return cmd.upper(), arg


def split_arguments(line: t.Optional[str], platform: t.Optional[str] = None) -> t.List[str]:
    """
    Split a command line into arguments, respecting quotes and escape sequences.
    """
    if not line:
        return []

    if platform is None:
        platform = sys.platform
    split_re = SPLIT_WIN32_RE if platform == 'win32' else SPLIT_POSIX_RE
    result = []
    current_argument = None  # Accumulates pieces of one argument as we parse

    for (
        double_quoted_string,
        single_quoted_string,
        escaped_char,
        unquoted_word,
        whitespace,
        invalid_char,
    ) in split_re.findall(line):
        processed_word = None

        if unquoted_word:
            # Regular unquoted word
            processed_word = unquoted_word
        elif escaped_char:
            # Escaped characters - remove the backslash
            processed_word = escaped_char[1]
        elif whitespace:
            # Whitespace operators separate arguments
            if current_argument is not None:
                result.append(current_argument)
            current_argument = None
            continue
        elif invalid_char:
            # Found an invalid character that couldn't be parsed
            raise ValueError("Invalid or incomplete shell string")
        elif double_quoted_string:
            # Handle double-quoted strings with escape sequences
            processed_word = double_quoted_string.replace('\\"', '"').replace('\\\\', '\\')
            if platform == 'win32':
                processed_word = processed_word.replace('""', '"')
        else:
            # Handle single-quoted strings (may be empty)
            processed_word = single_quoted_string

        # Accumulate the processed word into the current argument
        current_argument = (current_argument or '') + processed_word

    # Add the final argument if we were building one
    if current_argument is not None:
        result.append(current_argument)

    return result


def parse_size(size_str: str) -> int:
    """
    Convert a size string with optional unit suffix (K, M, G, T) to an integer
    """
    units = {"": BLOCK_SIZE, "B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    size_str = size_str.strip()
    unit = size_str[-1] if size_str[-1].isalpha() else ""
    number = int(size_str[:-1]) if unit else int(size_str)
    return number * units[unit]


class PartialMatching:
    """
    A utility class for managing keys that can be partially matched based on prefixes.
    """

    def __init__(self) -> None:
        self.short: t.Dict[str, str] = {}  # short key => full key
        self.full: t.Dict[str, str] = {}  # full key => short key

    def add(self, key: str) -> None:
        """
        Add a key to the partial matching dictionary
        A key is constituted by a prefix and a tail, separated by an underscore.
        Example: DIR_ECTORY
        """
        key = key.upper()
        try:
            prefix, tail = key.split("_", 1)
        except:
            prefix = key
            tail = ""
        full = prefix + tail
        self.full[full] = prefix
        self.short[prefix] = full

    def add_alias(self, full: str, alias: str) -> None:
        """
        Add an alias
        """
        self.short[alias.upper()] = full.upper()

    def get(self, key: str, default: t.Optional[str] = None) -> t.Optional[str]:
        """
        Retrieves the full key corresponding to a given prefix or alias.
        """
        key = key.upper()
        try:
            return self.short[key]
        except KeyError:
            pass
        matching_keys = [(k, v) for k, v in self.full.items() if k.startswith(key) and len(key) >= len(v)]
        if not matching_keys:
            return default
        return matching_keys[0][0]


class ShellContext:
    shell: "Shell"
    volumes: "Volumes"
    level: LoggingLevel

    def __init__(self, shell: "Shell") -> None:
        self.shell = shell
        self.volumes = shell.volumes
        self.level = shell.level


ShellCommand = t.Callable[["ShellContext", t.List[str]], None]


class Commands:
    commands: t.Dict[str, ShellCommand] = {}
    cmd_matching: PartialMatching

    def __init__(self) -> None:
        self.commands = {}
        self.cmd_matching = PartialMatching()

    def register(
        self, decorator_arg: str, aliases: t.List[str] = [], batch: bool = False
    ) -> t.Callable[[ShellCommand], ShellCommand]:
        """
        Register a command function with the shell
        """

        def decorator(func: ShellCommand) -> ShellCommand:
            if " " not in decorator_arg:
                # Commands without spaces are considered main commands
                if "_" in decorator_arg:
                    self.cmd_matching.add(decorator_arg)
                command = decorator_arg.replace("_", "")
                self.commands[command] = func
                for alias in aliases:
                    self.cmd_matching.add_alias(command, alias)
            else:
                # Subcommands have a space in the decorator argument
                main, sub = decorator_arg.split(" ", 1)
                if "_" in decorator_arg:
                    self.cmd_matching.add(decorator_arg)
                command = decorator_arg.replace("_", "")
                self.commands[command] = func
                for alias in aliases:
                    self.cmd_matching.add_alias(command, alias)
            return func

        return decorator

    def get(self, key: str) -> t.Optional[ShellCommand]:
        """
        Get a command function by its complete/abbreviated name
        """
        key = key.upper()
        return self.commands.get(self.cmd_matching.get(key) or key)

    def get_commands(self, exclude: t.List[str] = []) -> t.List[str]:
        """
        Get a list of all registered commands, excluding those in the exclude list
        """
        return [k for k in self.commands if k not in exclude]
