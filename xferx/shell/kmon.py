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
import typing as t

from ..commons import LoggingLevel, splitdrive
from ..volumes import Volumes
from .batch import BatchContext
from .cmds import cmds
from .commons import (
    CommandError,
    ShellContext,
    add_slash,
    split_arguments,
    split_command_line,
)

try:
    import readline
except:
    readline = None  # type: ignore

__all__ = [
    "Shell",
]


HISTORY_FILENAME = "~/.rt_history"
HISTORY_LENGTH = 1000


class Shell:
    level: LoggingLevel = LoggingLevel.INFO  # Logging level
    volumes: Volumes  # Volumes manager
    prompt: str  # Command prompt
    history_file: str  # Path to the history file
    completion_matches: t.List[str]  # Matches for shell completion

    def __init__(self, level: LoggingLevel = LoggingLevel.INFO, verbose: t.Optional[bool] = None):
        if verbose is not None:
            self.level = LoggingLevel.DEBUG if verbose else LoggingLevel.INFO
        else:
            self.level = level
        self.volumes = Volumes()
        self.update_prompt()
        self.history_file = os.path.expanduser(HISTORY_FILENAME)
        self.completion_matches = []
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

    def complete(self, text: str, state: int) -> t.Optional[str]:
        """
        Return the next possible completion
        """
        if state == 0:
            rline = readline.get_line_buffer()
            line = rline.lstrip()
            begidx = readline.get_begidx() - len(rline) + len(line)
            if begidx <= 0:
                # If the line is empty, complete commands and volumes
                self.completion_matches = self.complete_commands_volumes(text)
            else:
                cmd, args = split_command_line(line)
                if cmd == "SHOW":
                    # Complete subcommands
                    self.completion_matches = self.complete_subcommands(cmd, args or "")
                else:
                    # Complete filenames
                    self.completion_matches = self.complete_filenames(text)
        try:
            return self.completion_matches[state]
        except IndexError:
            return None

    def complete_commands_volumes(self, text: str) -> t.List[str]:
        """
        Complete commands or volumes
        """
        if text[:1].islower():
            # If the first character is lowercase, complete commands in lowercase
            commands = [x.lower() for x in cmds.get_commands()]
        else:
            commands = cmds.get_commands()
        text = text.upper()
        return [x for x in commands if x.upper().startswith(text)] + [
            f"{x}:" for x in self.volumes.volumes if x.upper().startswith(text)
        ]

    def complete_subcommands(self, cmd: str, text: str) -> t.List[str]:
        """
        Complete subcommands
        """
        if text[:1].islower():
            # If the first character is lowercase, complete commands in lowercase
            commands = [x.lower() for x in cmds.get_commands()]
        else:
            commands = cmds.get_commands()
        text = f"{cmd} {text}".upper()
        return [x.split(" ", 1)[1] for x in commands if x.upper().startswith(text)]

    def complete_filenames(self, text: str) -> t.List[str]:
        """
        Complete filenames based on the current drive and directory
        """
        try:
            has_volume_id = ":" in text
            volume_id, path = splitdrive(text)
            pattern = path + "*"
            fs = self.volumes.get_volume(volume_id)
            result: t.List[str] = []
            for x in fs.filter_entries_list(pattern):
                tmp = add_slash(fs, x.name)
                result.append(f"{volume_id}:{tmp}" if has_volume_id else tmp)
            return result
        except Exception:
            pass  # no problem :-)
        return []

    def setup_completer(self) -> None:
        """
        Set up the readline completer
        """
        if readline is not None:
            try:
                self.old_completer = readline.get_completer()
                readline.set_completer(self.complete)
                readline.parse_and_bind("tab: complete")
            except Exception:
                pass

    def teardown_completer(self) -> None:
        """
        Restore the old completer
        """
        if readline is not None:
            try:
                readline.set_completer(self.old_completer)
            except Exception:
                pass

    def write_history(self) -> None:
        """
        Write the command history to the history file
        """
        if readline is not None and self.history_file:
            try:
                readline.set_history_length(HISTORY_LENGTH)
                readline.write_history_file(self.history_file)
            except Exception:
                pass

    def update_prompt(self) -> None:
        """
        Update the command prompt
        """
        self.prompt = f"[{self.volumes.get_pwd()}] "

    def cmd_loop(self) -> None:
        """
        Main command loop
        """
        # Set up readline completion
        self.setup_completer()
        try:
            while True:
                self.update_prompt()
                try:
                    line = input(self.prompt)
                    self.onecmd(line)
                except SystemExit:
                    break
                except EOFError:
                    break
                except KeyboardInterrupt:
                    sys.stderr.write("\n")
            # Write history file
            self.write_history()
        finally:
            # Restore the old completer
            self.teardown_completer()

    def onecmd(self, line: str, batch: bool = False, context: t.Optional[ShellContext] = None) -> None:
        """
        Execute a single command line
        """
        try:
            cmd, arg = split_command_line(line)
            if not cmd:
                # Empty line
                pass
            elif arg == ":":
                # Set default volume
                self.volumes.set_default_volume(cmd)
            else:
                # Execute the command
                func = cmds.get(cmd)
                if func is None:
                    raise CommandError("?KMON-F-Illegal command")
                if context is None:
                    context = ShellContext(self)
                assert isinstance(context, ShellContext)
                args = split_arguments(arg)
                func(context, args)
        except KeyboardInterrupt:
            sys.stderr.write("\n")
            sys.stderr.write("\n")
        except SystemExit as ex:
            # Allow SystemExit to propagate
            raise ex
        except Exception as ex:
            message = str(sys.exc_info()[1])
            sys.stderr.write(f"{message}\n")
            if self.level <= LoggingLevel.DEBUG and not isinstance(ex, CommandError):
                traceback.print_exc()
            if batch:
                raise ex
