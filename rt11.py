#!/usr/bin/python

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
import stat
import math
import copy
import fnmatch
import cmd
import shlex
import glob
from datetime import date, datetime
try:
    import readline
except:
    readline = None

BLOCK_SIZE = 512

HOMEBLK = 1
DEFAULT_DIR_SEGMENT = 6
DIR_ENTRY_SIZE = 14

HISTORY_FILENAME = "~/.rt_history"
HISTORY_LENGTH = 1000

E_TENT = 1
E_MPTY = 2
E_PERM = 4
E_EOS = 8
E_READ = 64
E_PROT = 128

#    READ     =    0
#    WRITE    =    0
#    CLOSE    =    1
#    DELETE   =    2
#    LOOKUP   =    3
#    ENTER    =    4
#    RENAME   =    5

RAD50 = "\0ABCDEFGHIJKLMNOPQRSTUVWXYZ$.%0123456789:"

if sys.version_info[0] == 2:
    def str_to_byte(val, position=0):
        return ord(val[position])

    def str_to_word(val, position=0):
        return ord(val[1 + position]) * 256 + ord(val[0 + position])

else:
    def str_to_byte(val, position=0):
        return val[position]

    def str_to_word(val, position=0):
        return val[1 + position] * 256 + val[0 + position]

def byte_to_str(val):
    return chr(val)

def word_to_str(val):
    return chr(val % 256) + chr(val // 256)

def rad2asc(val, position=0):
    """
    Convert RAD50 word to 0-3 chars of ASCII
    """
    if isinstance(val, (str, bytes)):
        val = str_to_word(val, position=position)
    # split out RAD50 digits into three ASCII characters a/b/c
    c = RAD50[val % 0x28]
    b = RAD50[(val // 0x28) % 0x28]
    a = RAD50[val // (0x28 * 0x28)]
    result = ""
    if a != "\0":
        result += a
    if b != "\0":
        result += b
    if c != "\0":
        result += c
    return result

def asc2rad(val):
    """
    Convert a string of 3 ASCII to a RAD50 word
    """
    val = [RAD50.find(c.upper()) for c in val] + [0, 0, 0]
    val = [x > 0 and x or 0 for x in val]
    val = (val[0]*0x28+val[1])*0x28+val[2]
    return word_to_str(val)

def rt11_to_date(val, position=0):
    """
    Translate RT-11 time to python date
    """
    if isinstance(val, (str, bytes)):
        val = str_to_word(val, position=position)
    if val == 0:
        return None
    #                   5432109876543210
    year = val & int("0000000000011111", 2)
    day = (val & int("0000001111100000", 2)) >> 5
    month = (val & int("0011110000000000", 2)) >> 10
    age = (val & int("1100000000000000", 2)) >> 14
    year = year + 1972 + age * 32
    if day == 0:
        day = 1
    if month == 0:
        month = 1
    try:
        return date(year, month, day)
    except:
        return None

def date_to_rt11(val):
    """
    Translate python date to RT-11 time
    """
    if val is None:
        return 0
    age = (val.year - 1972) / 32
    if age < 0:
        age = 0
    elif age > 3:
        age = 3
    year = (val.year - 1972) % 32
    return year + \
           (val.day << 5) + \
           (val.month << 10) + \
           (age << 14)
    return val

def splitdrive(path):
    """
    Split a pathname into drive and path.
    """
    result = path.split(":", 1)
    if len(result) < 2:
        return ("DK", path)
    else:
        return (result[0].upper(), result[1])

class NativeFile(object):

    def __init__(self, filename):
        self.filename = os.path.abspath(filename)
        self.f = open(filename, mode="rb+")

    def read_block(self, block_number, number_of_blocks=1):
        if block_number < 0 or number_of_blocks < 0:
            return None
        self.f.seek(block_number * BLOCK_SIZE) # not thread safe...
        return self.f.read(BLOCK_SIZE * number_of_blocks)

    def write_block(self, buffer, block_number, number_of_blocks=1):
        if block_number < 0 or number_of_blocks < 0:
            return None
        self.f.seek(block_number * BLOCK_SIZE) # not thread safe...
        return self.f.write(buffer[0:number_of_blocks*BLOCK_SIZE])

    def close(self):
        self.f.close()

    def __str__(self):
        return self.filename

class RT11File(object):

    def __init__(self, entry):
        self.entry = entry

    def read_block(self, block_number, number_of_blocks=1):
        if self.entry is None or block_number < 0 or number_of_blocks < 0 or block_number + number_of_blocks > self.entry.length:
            return None
        return self.entry.segment.fs.read_block(self.entry.file_position + block_number, number_of_blocks)

    def write_block(self, buffer, block_number, number_of_blocks=1):
        if self.entry is None or block_number < 0 or number_of_blocks < 0 or block_number + number_of_blocks > self.entry.length:
            return None
        return self.entry.segment.fsf.write(buffer, self.entry.file_position + block_number, number_of_blocks)

    def close(self):
        self.entry = None

    def __str__(self):
        return self.entry.fullname


class NativeDirectoryEntry(object):

    def __init__(self, fullname):
        self.fullname = fullname
        self.filename = os.path.basename(fullname)
        self.filename, self.filetype = os.path.splitext(self.filename)
        if self.filetype.startswith("."):
            self.filetype = self.filename[1:]
        self.stat = os.stat(fullname)
        self.length = self.stat.st_size # length in bytes
        self.creation_date = datetime.fromtimestamp(self.stat.st_ctime)

    @property
    def basename(self):
        return os.path.basename(self.fullname)

    def delete(self):
        try:
            os.unlink(self.fullname)
            return True
        except:
            return False

    def __str__(self):
        return "%-11s" % self.fullname + \
               " %s" % (self.creation_date or "") + \
               " length: %6s" % self.length


class RT11DirectoryEntry(object):

    def __init__(self, segment, buffer, position, file_position, extra_bytes):
        self.segment = segment
        self.type = str_to_byte(buffer, position)
        self.clazz = str_to_byte(buffer, position + 1)
        self.filename = rad2asc(buffer, position + 2) + rad2asc(buffer, position + 4) # 6 RAD50 chars
        self.filetype = rad2asc(buffer, position + 6) # 3 RAD50 chars
        self.length = str_to_word(buffer, position + 8) # length in blocks
        self.job = str_to_byte(buffer, position + 10)
        self.channel = str_to_byte(buffer, position + 11)
        self.raw_creation_date = str_to_word(buffer, position + 12)
        self.creation_date = rt11_to_date(self.raw_creation_date)
        self.extra_bytes = buffer[position + 14:position + 14 + extra_bytes]
        self.file_position = file_position

    def to_bytes(self):
        out = []
        out.append(chr(self.type))
        out.append(chr(self.clazz))
        out.append(asc2rad(self.filename[0:3]))
        out.append(asc2rad(self.filename[3:6]))
        out.append(asc2rad(self.filetype))
        out.append(word_to_str(self.length))
        out.append(byte_to_str(self.job))
        out.append(byte_to_str(self.channel))
        out.append(word_to_str(self.raw_creation_date))
        out.append(self.extra_bytes)
        return "".join(out)

    @property
    def is_empty(self):
        return self.clazz & E_MPTY == E_MPTY

    @property
    def is_tentative(self):
        return self.clazz & E_TENT == E_TENT

    @property
    def is_permanent(self):
        return self.clazz & E_PERM == E_PERM

    @property
    def is_end_of_segment(self):
        return self.clazz & E_EOS == E_EOS

    @property
    def is_protected_by_monitor(self):
        return self.clazz & E_READ == E_READ

    @property
    def is_protected_permanent(self):
        return self.clazz & E_PROT == E_PROT

    @property
    def fullname(self):
        #return self.filename + "." + self.filetype
        return self.filename + (self.filetype and ("." + self.filetype) or "")

    @property
    def basename(self):
        return self.fullname

    def delete(self):
        # unset E_PROT,E_TENT,E_READ,E_PROT flasgs, set E_MPTY flag
        self.clazz = self.clazz & ~E_PERM & ~E_TENT & ~E_READ & ~E_PROT | E_MPTY
        self.segment.compact()

    def __str__(self):
        return "%-11s" % self.fullname + \
               " %s" % (self.creation_date or "          ") + \
               " length: %6s" % self.length + \
               " type: %3x" % self.type + \
               " class: %3x" % self.clazz + \
               " job: %3d" % self.job + \
               " chn: %3d" % self.channel + \
               " pos: %3d" % self.file_position


class RT11Segment(object):

    num_of_segments = 0
    next_logical_dir_segment = 0
    block_number = 0
    highest_segment = 0
    extra_bytes = 0
    data_block_number = 0
    entries_list = None
    max_entries = 0

    def __init__(self, fs, block_number):
        self.fs = fs
        if not block_number:
            return
        self.block_number = block_number
        t = fs.read_block(self.block_number, 2)
        self.num_of_segments = str_to_word(t, 0)
        self.next_logical_dir_segment = str_to_word(t, 2)
        self.highest_segment = str_to_word(t, 4)
        self.extra_bytes = str_to_word(t, 6)
        self.data_block_number = str_to_word(t, 8)
        self.entries_list = []

        file_position = self.data_block_number

        self.max_entries = math.floor((1024.0 - 10.0) // (DIR_ENTRY_SIZE + self.extra_bytes))
        for position in range(10, 1024 - (DIR_ENTRY_SIZE + self.extra_bytes), DIR_ENTRY_SIZE + self.extra_bytes):
            dir_entry = RT11DirectoryEntry(self, t, position, file_position, self.extra_bytes)
            file_position = file_position + dir_entry.length
            #if dir_entry.is_end_of_segment:
            #    break
            if dir_entry.is_permanent:
                fs.entries_map[dir_entry.fullname] = dir_entry
            self.entries_list.append(dir_entry)
            if dir_entry.is_end_of_segment:
                break

    def to_bytes(self):
        out = []
        out.append(word_to_str(self.num_of_segments))
        out.append(word_to_str(self.next_logical_dir_segment))
        out.append(word_to_str(self.highest_segment))
        out.append(word_to_str(self.extra_bytes))
        out.append(word_to_str(self.data_block_number))
        out.extend([entry.to_bytes() for entry in self.entries_list])
        out = "".join(out)
        return out + ("\0" * (BLOCK_SIZE*2-len(out)))

    def write(self):
        self.fs.write_block(self.to_bytes(), self.block_number, 2)

    @property
    def next_block_number(self):
        if self.next_logical_dir_segment == 0:
            return 0
        else:
            return (self.next_logical_dir_segment * 2) + 4

    def compact(self):
        # compact multiple unused entrties
        prev_empty_entry = None
        new_entries_list = []
        for entry in self.entries_list:
            if not entry.is_empty:
                prev_empty_entry = None
                new_entries_list.append(entry)
            elif prev_empty_entry is None:
                prev_empty_entry = entry
                new_entries_list.append(entry)
            else:
                prev_empty_entry.length = prev_empty_entry.length + entry.length
                if entry.is_end_of_segment:
                    prev_empty_entry.clazz = prev_empty_entry.clazz | E_EOS
        self.entries_list = new_entries_list
        self.write()

    def insert_entry_after(self, entry, entry_number, length):
        if entry.length == length:
            return
        new_entry = copy.copy(entry) # new empty space entry
        if entry.is_end_of_segment:
            new_entry.clazz = E_EOS
            entry.clazz = entry.clazz - E_EOS
        new_entry.length = entry.length - length
        new_entry.file_position = entry.file_position + length
        entry.length = length
        self.entries_list.insert(entry_number+1, new_entry)
        entry.segment.write()

    def __str__(self):
        result = "segment - block_number: %d num_of_segments: %d highest_segment: %d max_entries: %d\n" % \
                (self.block_number, self.num_of_segments, self.highest_segment, self.max_entries)
        result = result + "\n".join("%02d#  %s" % (i, x) for i,x in enumerate(self.entries_list))
        return result


class NativeFilesystem(object):

    def __init__(self, base=None):
        self.base = base or "/"
        if not base:
            self.pwd = os.getcwd()
        elif os.getcwd().startswith(base):
            self.pwd = os.getcwd()[len(base):]
        else:
            self.pwd = os.path.sep
        pass

    def filter_entries_list(self, pattern, include_all=False):
        if not pattern:
            for filename in os.listdir(os.path.join(self.base, self.pwd)):
                try:
                    v = NativeDirectoryEntry(os.path.join(self.base, self.pwd, filename))
                except:
                    v = None
                if v is not None:
                    yield v
        else:
            if not pattern.startswith("/") and not pattern.startswith("\\"):
                pattern = os.path.join(self.base, self.pwd, pattern)
            if os.path.isdir(pattern):
                pattern = os.path.join(pattern, "*")
            for filename in glob.glob(pattern):
                try:
                    v = NativeDirectoryEntry(filename)
                except:
                    v = None
                if v is not None:
                    yield v

    @property
    def entries_list(self):
        dir = self.pwd
        for filename in os.listdir(dir):
            yield NativeDirectoryEntry(os.path.join(dir, filename))

    def get_file_entry(self, fullname):
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        return NativeDirectoryEntry(fullname)

    def open_file(self, fullname):
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        return NativeFile(fullname)

    def get_file(self, fullname):
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        f = None
        try:
            f = open(fullname, "rb")
            return f.read()
        finally:
            if f is not None:
                f.close()

    def save_file(self, fullname, content):
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        f = None
        try:
            f = open(fullname, "wb")
            f.write(content)
            return True
        finally:
            if f is not None:
                f.close()

    def chdir(self, fullname):
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        fullname = os.path.normpath(fullname)
        if os.path.isdir(os.path.join(self.base, fullname)):
            self.pwd = fullname
            # Change the current working directory
            os.chdir(os.path.join(self.base, fullname))
            return True
        else:
            return False

    def isdir(self, fullname):
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        return os.path.isdir(os.path.join(self.base, fullname))

    def exists(self, fullname):
        if not fullname.startswith("/") and not fullname.startswith("\\"):
            fullname = os.path.join(self.pwd, fullname)
        return os.path.exists(os.path.join(self.base, fullname))

    def dir(self, pattern):
        for x in self.filter_entries_list(pattern):
            mode = x.stat.st_mode
            if stat.S_ISREG(mode):
                type = "%s" % x.length
            elif stat.S_ISDIR(mode):
                type = "DIRECTORY      "
            elif stat.S_ISLNK(mode):
                type = "LINK           "
            elif stat.S_ISFIFO(mode):
                type = "FIFO           "
            elif stat.S_ISSOCK(mode):
                type = "SOCKET         "
            elif stat.S_ISCHR(mode):
                type = "CHAR DEV       "
            elif stat.S_ISBLK(mode):
                type = "BLOCK DEV      "
            else:
                type = "?"
            sys.stdout.write("%15s %19s %s\n" % (type,
                                    x.creation_date and x.creation_date.strftime("%d-%b-%Y %H:%M ") or "",
                                    x.basename))

    def examine(self):
        pass

    def close(self):
        pass

    def __str__(self):
        return self.base


class RT11Filesystem(object):

    dir_segment = None
    ver = None
    id = None
    owner = None
    sys_id = None
    entries_map = None
    segments = None
    num_of_segments = 0 # max num of segments

    def __init__(self, file):
        self.f = file
        self.read_home()
        self.read_dir_segment()
        self.pwd = ""

    def read_block(self, block_number, number_of_blocks=1):
        return self.f.read_block(block_number, number_of_blocks)

    def write_block(self, buffer, block_number, number_of_blocks=1):
        return self.f.write_block(buffer, block_number, number_of_blocks)

    def read_home(self):
        t = self.read_block(HOMEBLK)
        self.dir_segment = str_to_word(t[468:470]) or DEFAULT_DIR_SEGMENT
        self.ver = rad2asc(t[470:472])
        self.id = t[472:484]
        self.owner = t[484:496]
        self.sys_id = t[496:508]

    def read_dir_segment(self):
        self.entries_map = {}
        self.segments = []
        next_block_number = self.dir_segment
        while next_block_number != 0:
            segment = RT11Segment(self, next_block_number)
            next_block_number = segment.next_block_number
            self.segments.append(segment)
            self.num_of_segments = segment.num_of_segments

    def filter_entries_list(self, pattern, include_all=False):
        if pattern:
            pattern=pattern.lower()
        for segment in self.segments:
            for entry in segment.entries_list:
                if (not pattern) or fnmatch.fnmatch(entry.fullname.lower(), pattern):
                    if not include_all and (entry.is_empty or entry.is_tentative):
                        continue
                    yield entry

    @property
    def entries_list(self):
        for segment in self.segments:
            for entry in segment.entries_list:
                yield entry

    def get_file_entry(self, fullname): # fullname=filename+ext
        return self.entries_map.get(fullname.upper())

    def open_file(self, fullname):
        return RT11File(self.get_file_entry(fullname))

    def get_file(self, fullname): # fullname=filename+ext
        entry = self.entries_map.get(fullname.upper())
        if not entry:
            return None
        return self.read_block(entry.file_position, entry.length)

    def save_file(self, fullname, content):
        fullname = os.path.basename(fullname)
        entry = self.get_file_entry(fullname)
        if entry is not None:
            entry.delete()
        length = int(math.ceil(len(content) * 1.0 / BLOCK_SIZE))
        entry = self.allocate_space(fullname, length)
        if not entry:
            return False
        content = content + ("\0" * BLOCK_SIZE)
        self.write_block(content, entry.file_position, entry.length)
        return True

    def split_segment(self, entry):
        # entry is the last entry of the old_segment, new new segment will contain all the entries after that
        old_segment = entry.segment
        # find the new segment number
        sn = [x.block_number for x in self.segments]
        p = 0
        segment_number = None
        for i in range(self.dir_segment, self.dir_segment + (self.num_of_segments * 2), 2):
            p = p + 1
            if i not in sn:
                segment_number = i
                break
        if segment_number is None:
            return False
        # create the new segment
        segment = RT11Segment(self, None)
        segment.block_number = segment_number
        segment.num_of_segments = self.segments[0].num_of_segments
        segment.next_logical_dir_segment = old_segment.next_logical_dir_segment
        segment.highest_segment = 1
        segment.extra_bytes = self.segments[0].extra_bytes
        segment.data_block_number = entry.file_position + entry.length
        old_segment.next_logical_dir_segment = (segment.block_number - 4) // 2 # set the next segment of the last segment
        entry.clazz = entry.clazz | E_EOS # entry is the last entry of the old segment
        self.segments[0].num_of_segments = len(self.segments) # update the total num of segments

        entry_position = None
        for i, e in enumerate(old_segment.entries_list):
            if entry == e:
                entry_position = i
        segment.entries_list = old_segment.entries_list[entry_position+1:]
        old_segment.entries_list = old_segment.entries_list[:entry_position+1]
        old_segment.write()
        segment.data_block_number = entry.file_position + entry.length
        entry.clazz = entry.clazz | E_EOS
        segment.write()
        # Load the new segment
        segment = RT11Segment(self, segment_number)
        self.segments.insert(p, segment) # insert the segment after the old segment
        return True

    def allocate_space(self, fullname, length, creation_date=None): # fullname=filename+ext, length in blocks
        """
        Allocate space for a new file
        """
        entry = None
        entry_number = None
        # Search for an empty entry to be splitted
        for segment in self.segments:
            for i, e in enumerate(segment.entries_list):
                if e.is_empty and e.length >= length:
                    if entry is None or entry.length > e.length:
                        entry = e
                        entry_number = i
                        if entry.length == length:
                            break
        if entry is None:
            return None
        # If the entry length is equal to the requested length, don't create the new empty entity
        if entry.length != length:
            if len(entry.segment.entries_list) >= entry.segment.max_entries:
                if not self.split_segment(entry):
                    return None
            entry.segment.insert_entry_after(entry, entry_number, length)
        # Fill the entry
        t = os.path.splitext(fullname.upper())
        entry.filename = t[0]
        entry.filetype = t[1] and t[1][1:] or ""
        if creation_date is None:
            entry.creation_date = date.today()
        else:
            entry.creation_date = creation_date
        entry.raw_creation_date = date_to_rt11(entry.creation_date)
        entry.job = 0
        entry.channel = 0
        if entry.is_end_of_segment:
            entry.clazz = E_PERM | E_EOS
        else:
            entry.clazz = E_PERM
        entry.length = length
        # Write the segment
        entry.segment.write()
        return entry

    def chdir(self, fullname):
        return False

    def isdir(self, fullname):
        return False

    def dir(self, pattern):
        i = 0
        files = 0
        blocks = 0
        unused = 0
        for x in self.filter_entries_list(pattern, include_all=True):
            if not x.is_empty and not x.is_tentative and not x.is_permanent and not x.is_protected_permanent and not x.is_protected_by_monitor:
                continue
            i = i + 1
            if x.is_empty or x.is_tentative:
                fullname = "< UNUSED >"
                date = ""
                unused = unused + x.length
            else:
                fullname = x.is_empty and x.filename or "%-6s.%-3s" % (x.filename, x.filetype)
                date = x.creation_date and x.creation_date.strftime("%d-%b-%y") or ""
            if x.is_permanent:
                files = files + 1
                blocks = blocks + x.length
            if x.is_protected_permanent:
                attr = "P"
            elif x.is_protected_by_monitor:
                attr ="A"
            else:
                attr = " "
            sys.stdout.write("%10s %5d%1s %9s" % (fullname, x.length, attr, date))
            if i % 2 == 1:
                sys.stdout.write("    ")
            else:
                sys.stdout.write("\n")
        if i % 2 == 1:
            sys.stdout.write("\n")
        sys.stdout.write(" %d Files, %d Blocks\n" % (files, blocks))
        sys.stdout.write(" %d Free blocks\n" % unused)

    def examine(self):
        sys.stdout.write("dir_segment: %s ver: %s id: %d owner: %s sys_id: %s\n" % (self.dir_segment, self.ver, self.id, self.owner, self.sys_id))
        for s in self.segments:
            sys.stdout.write("%s\n" % s)

    def close(self):
        self.f.close()

    def __str__(self):
        return str(self.f)


class Volumes(object):

    def __init__(self):
        self.volumes = {}
        if self._drive_letters():
            # windows
            for letter in self._drive_letters():
                self.volumes[letter] = NativeFilesystem("%s:" % letter)
            self.volumes["DK"] = os.getcwd().split(":")[0]
        else:
            # posix
            self.volumes["SY"] = NativeFilesystem()
            self.volumes["DK"] = "SY"

    def _drive_letters(self):
        try:
            import string
            from ctypes import windll
            drives = []
            bitmask = windll.kernel32.GetLogicalDrives()
            for c in string.ascii_uppercase:
                if bitmask & 1:
                    drives.append(c)
                bitmask >>= 1
            return drives
        except:
            return []

    def get(self, volume_id, required=False, cmd="KMON"):
        if volume_id is None:
            volume_id = "DK"
        elif volume_id.endswith(":"):
            volume_id = volume_id[:-1]
        if volume_id.upper() == "LAST":
            volume_id = self.last()
        v = self.volumes.get(volume_id.upper())
        if isinstance(v, (str, bytes)):
            v = self.volumes.get(v.upper())
        if required and v is None:
            raise Exception("?%s-F-Illegal volume %s:" % (cmd, volume_id))
        return v

    def chdir(self, path):
        volume_id, fullname = splitdrive(path)
        if volume_id.upper() == "LAST":
            volume_id = self.last()
        fs = self.get(volume_id)
        if fs is None:
            return False
        if fullname and not fs.chdir(fullname):
            return False
        if volume_id != "DK":
            self.set_default_volume(volume_id)
        return True

    def pwd(self):
        try:
            return "%s:%s" % (self.volumes.get("DK"), self.get("DK").pwd)
        except:
            return "%s:???" % (self.volumes.get("DK"))

    def set_default_volume(self, volume_id):
        """ Set the default volume """
        if not volume_id:
            return False
        if volume_id.endswith(":"):
            volume_id = volume_id[:-1]
        if volume_id.upper() == "LAST":
            volume_id = self.last()
        volume_id = volume_id.upper()
        if volume_id != "DK" and volume_id in self.volumes:
            self.volumes["DK"] = volume_id
        else:
            raise Exception("?KMON-F-Invalid volume")

    def mount(self, path, logical=None):
        if not logical:
            logical = os.path.basename(path).split(".")[0]
        volume_id, fullname = splitdrive(path)
        fs = self.get(volume_id, required=True, cmd="MOUNT")
        logical = logical.split(":")[0].upper()
        if logical == "DK":
            raise Exception("?MOUNT-F-Illegal volume %s:" % volume_id)
        try:
            self.volumes[logical] = RT11Filesystem(fs.open_file(fullname))
            sys.stdout.write("?MOUNT-I-Disk %s mounted to %s:\n" % (path, logical))
        except:
            sys.stdout.write("?MOUNT-F-Error mounting %s to %s:\n" % (path, logical))

    def dismount(self, logical):
        logical = logical.split(":")[0].upper()
        if logical == "DK" or logical not in self.volumes:
            raise Exception("?DISMOUNT-F-Illegal volume %s:" % logical)
        del self.volumes[logical]

    def last(self):
        return list(self.volumes.keys())[-1]


class Shell(cmd.Cmd):

    def __init__(self,):
        cmd.Cmd.__init__(self)
        self.volumes = Volumes()
        #self.prompt="."
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

    def completenames(self, text, *ignored):
        dotext = "do_"+text.lower()
        return ["%s " % a[3:] for a in self.get_names() if a.startswith(dotext)] + \
               ["%s:" % a for a in self.volumes.volumes.keys() if a.startswith(text.upper()) ]

    def completedefault(self, text, state, *ignored):
        def add_slash(fs, filename):
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
                volume_id = None
                path = None
            pattern = path + "*"
            fs = self.volumes.get(volume_id)
            result = []
            for x in fs.filter_entries_list(pattern):
                if has_volume_id:
                    result.append("%s:%s" % (volume_id, add_slash(fs, x.fullname)))
                else:
                    result.append("%s" % add_slash(fs, x.fullname))
            return result
        except:
            pass # no problem :-)
        return []

    def postloop(self):
        if readline is not None:
            # Cleanup and write history file
            readline.set_completer(None)
            try:
                if self.history_file:
                    readline.set_history_length(HISTORY_LENGTH)
                    readline.write_history_file(self.history_file)
            except:
                pass

    def cmdloop(self, intro=None):
        try:
            return cmd.Cmd.cmdloop(self, intro)
        except KeyboardInterrupt:
            sys.stdout.write("\n")

    def postcmd(self, stop, line):
        self.prompt = "[%s] " % self.volumes.pwd()
        return stop

    def onecmd(self, line):
        try:
            return cmd.Cmd.onecmd(self, line)
        except Exception:
            ex = sys.exc_info()[1]
            sys.stdout.write("%s\n" % str(ex))
            #import traceback
            #traceback.print_exc()
            return False

    def parseline(self, line):
        ccmd, arg, line = cmd.Cmd.parseline(self, line)
        if ccmd is not None:
            ccmd = ccmd.lower()
        return ccmd, arg, line

    def default(self, line):
        if line.endswith(":"):
            self.volumes.set_default_volume(line)
        else:
            raise Exception("?KMON-F-Illegal command")

    def emptyline(self):
        sys.stdout.write("\n")

    def do_dir(self, line):
        """
DIR             Lists file directories

  SYNTAX
        DIR [[volume:][filespec]]

  SEMANTICS
        This command generates a listing of the directory you specify.

  EXAMPLES
        DIR A:*.SAV
        DIR SY:

        """
        if isinstance(line, (str, bytes)):
            line = shlex.split(line)
        if len(line) > 1:
            sys.stdout.write("?DIR-F-Too many arguments\n")
            return
        if line:
            volume_id, pattern = splitdrive(line[0])
        else:
            volume_id = None
            pattern = None
        fs = self.volumes.get(volume_id, required=True, cmd="DIR")
        fs.dir(pattern)

    def do_ls(self, line):
        self.do_dir(line)

    def do_type(self, line):
        """
TYPE            Outputs files to the terminal

  SYNTAX
        TYPE [volume:]filespec

  EXAMPLES
        TYPE A.TXT

        """
        if isinstance(line, (str, bytes)):
            line = shlex.split(line)
        if not line:
            try:
                while not line:
                    line = input("Files? ")
            except KeyboardInterrupt:
                sys.stdout.write("\n")
                sys.stdout.write("\n")
                return
            line = shlex.split(line)
        if len(line) > 1:
            sys.stdout.write("?TYPE-F-Too many arguments\n")
            return
        if line:
            volume_id, pattern = splitdrive(line[0])
        else:
            volume_id = None
            pattern = None
        fs = self.volumes.get(volume_id, required=True, cmd="TYPE")
        match = False
        for x in fs.filter_entries_list(pattern):
            match = True
            content = fs.get_file(x.fullname)
            if content != None:
                os.write(sys.stdout.fileno(), content)
                sys.stdout.write("\n")
        if not match:
            raise Exception("?TYPE-F-No files")

    def do_copy(self, line):
        """
COPY            Copies files

  SYNTAX
        COPY [input-volume:]input-filespec [output-volume:][output-filespec]

  EXAMPLES
        COPY *.TXT DK:

        """
        if isinstance(line, (str, bytes)):
            line = shlex.split(line)
        if len(line) > 2:
            sys.stdout.write("?COPY-F-Too many arguments\n")
            return
        cfrom = len(line) > 0 and line[0]
        to = len(line) > 1 and line[1]
        if not cfrom:
            try:
                cfrom = None
                while not cfrom:
                    cfrom = input("From? ")
            except KeyboardInterrupt:
                sys.stdout.write("\n")
                sys.stdout.write("\n")
                return
        from_volume_id, cfrom = splitdrive(cfrom)
        from_fs = self.volumes.get(from_volume_id, required=True, cmd="COPY")
        if not to:
            try:
                to = None
                while not to:
                    to = input("To? ")
            except KeyboardInterrupt:
                sys.stdout.write("\n")
                sys.stdout.write("\n")
                return
        to_volume_id, to = splitdrive(to)
        to_fs = self.volumes.get(to_volume_id, required=True, cmd="COPY")
        from_len = len(list(from_fs.filter_entries_list(cfrom)))
        from_list = from_fs.filter_entries_list(cfrom)
        if from_len == 0: # No files
            raise Exception("?COPY-F-No files")
        elif from_len == 1: # One file to be copied
            source = list(from_list)[0]
            if not to:
                to = os.path.join(self.volumes.get(to_volume_id).pwd, source.fullname)
            elif to and to_fs.isdir(to):
                to = os.path.join(to, source.basename)
            content = from_fs.get_file(source.fullname)
            sys.stdout.write("%s:%s -> %s:%s\n" % (from_volume_id, source.fullname, to_volume_id, to))
            if not to_fs.save_file(to, content):
                sys.stdout.write("?COPY-F-Error copying %s\n" % source.fullname)
        else:
            if not to:
                to = self.volumes.get(to_volume_id).pwd
            elif not to_fs.isdir(to):
                raise Exception("?COPY-F-Targe must be a volume or a directory")
            for x in from_fs.filter_entries_list(cfrom):
                if to:
                    target = os.path.join(to, x.basename)
                else:
                    target = x.basename
                content = from_fs.get_file(x.fullname)
                sys.stdout.write("%s:%s -> %s:%s\n" % (from_volume_id, x.fullname, to_volume_id, target))
                if not to_fs.save_file(target, content):
                    sys.stdout.write("?COPY-F-Error copying %s\n" % x.fullname)

    def do_del(self, line):
        """
DEL             Removes files from a volume

  SYNTAX
        DEL [volume:]filespec

  SEMANTICS
        This command deletes the files you specify from the volume.

  EXAMPLES
        DEL *.OBJ

        """
        if isinstance(line, (str, bytes)):
            line = shlex.split(line)
        if not line:
            try:
                while not line:
                    line = input("Files? ")
            except KeyboardInterrupt:
                sys.stdout.write("\n")
                sys.stdout.write("\n")
                return
            line = shlex.split(line)
        if line:
            volume_id, pattern = splitdrive(line[0])
        else:
            volume_id = None
            pattern = None
        fs = self.volumes.get(volume_id, required=True, cmd="DEL")
        match = False
        for x in fs.filter_entries_list(pattern):
            match = True
            if not x.delete():
                sys.stdout.write("?DEL-F-Error deleting %s\n" % x.fullname)
        if not match:
            raise Exception("?DEL-F-No files")

    def do_examine(self, line):
        volume_id, pattern = splitdrive(line or "")
        fs = self.volumes.get(volume_id, required=True)
        fs.examine()

    def do_mount(self, line):
        """
MOUNT           Assigns a logical disk unit to a file

  SYNTAX
        MOUNT volume: [volume:]filespec

  SEMANTICS
        Associates a logical disk unit with a file.

  EXAMPLES
        MOUNT AB: SY:AB.DSK

        """
        if isinstance(line, (str, bytes)):
            line = shlex.split(line)
        if len(line) > 2:
            sys.stdout.write("?MOUNT-F-Too many arguments\n")
            return
        logical = len(line) > 0 and line[0]
        path = len(line) > 1 and line[1]
        if not logical:
            try:
                logical = None
                while not logical:
                    logical = input("Volume? ")
            except KeyboardInterrupt:
                sys.stdout.write("\n")
                sys.stdout.write("\n")
                return
        if not path:
            try:
                path = None
                while not path:
                    path = input("File? ")
            except KeyboardInterrupt:
                sys.stdout.write("\n")
                sys.stdout.write("\n")
        self.volumes.mount(path, logical)

    def do_dismount(self, line):
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
        if isinstance(line, (str, bytes)):
            line = shlex.split(line)
        if len(line) > 1:
            sys.stdout.write("?DISMOUNT-F-Too many arguments\n")
            return
        logical = len(line) > 0 and line[0]
        if not logical:
            try:
                logical = None
                while not logical:
                    logical = input("Volume? ")
            except KeyboardInterrupt:
                sys.stdout.write("\n")
                sys.stdout.write("\n")
                return
        self.volumes.dismount(logical)

    def do_cd(self, line):
        """
CD              Changes or displays the current working drive and directory.

  SYNTAX
        CD [[volume:][filespec]]

        """
        if isinstance(line, (str, bytes)):
            line = shlex.split(line)
        if len(line) > 1:
            sys.stdout.write("?CD-F-Too many arguments\n")
            return
        elif len(line) == 0:
            sys.stdout.write("%s\n" % self.volumes.pwd())
            return
        if not self.volumes.chdir(line[0]):
            sys.stdout.write("?CD-F-Directory not found\n")

    def do_pwd(self, line):
        """
PWD             Displays the current working drive and directory

  SYNTAX
        PWD

        """
        sys.stdout.write("%s\n" % self.volumes.pwd())

    def do_show(self, line):
        """
SHOW            Displays the volume assignment

  SYNTAX
        SHOW

        """
        sys.stdout.write("Volumes\n")
        sys.stdout.write("-------\n")
        for k, v in self.volumes.volumes.items():
            if k != "DK":
                sys.stdout.write("%-10s %s\n" % ("%s:" % k, v))

    def do_exit(self, line):
        """
EXIT            Exit the shell

  SYNTAX
        EXIT
        """
        return True

    def do_quit(self, line):
        return True

    def do_help(self, arg):
        """
HELP            Displays commands help

  SYNTAX
        HELP [topic]

        """
        if arg and arg != "*":
            try:
                doc = getattr(self, "do_" + arg).__doc__
                if doc:
                    self.stdout.write("%s\n"%str(doc))
                    return
            except AttributeError:
                pass
            self.stdout.write("%s\n"%str(self.nohelp % (arg,)))
        else:
            names = self.get_names()
            help = {}
            for name in names:
                if name[:5] == "help_":
                    help[name[5:]]=1
            for name in sorted(set(names)):
                if name[:3] == "do_":
                    cmd=name[3:]
                    if cmd in help:
                        del help[cmd]
                    elif getattr(self, name).__doc__:
                        sys.stdout.write("%s\n" % getattr(self, name).__doc__.split("\n")[1])

    def do_shell(self, arg):
        """
SHELL           Executes a system shell command

  SYNTAX
        SHELL command

        """
        os.system(arg)

    def do_EOF(self, line):
        return True

if __name__ == "__main__":
    shell = Shell()
    for dsk in sys.argv[1:]:
        shell.volumes.mount(dsk)
    shell.cmdloop()
    # shell.onecmd("DIR LAST:")

