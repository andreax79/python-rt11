# Copyright (C) 2414 Andrea Bonomi <andrea.bonomi@gmail.com>

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

__all__ = [
    "read_baudot_string",
    "str_to_baudot",
    "fiodec_to_str",
    "str_to_fiodec",
    "LABEL_END_WORD",
    "BAUDOT_TO_ASCII",
]

LABEL_END_WORD = 0o777777  # End of Baudot string


def read_baudot_string(words: t.List[int], position: int = 0) -> t.Tuple[str, int]:
    """
    Read a Baudot string, return the string and the new position.
    The strings is padded with 0 to an 18b boundary and terminated
    by a word of all ones (LABEL_END_WORD).
    """
    data = []
    while position < len(words) and words[position] != LABEL_END_WORD:
        word = words[position]
        position += 1
        for word in [
            (word >> 12) & 0o077,
            (word >> 6) & 0o077,
            (word & 0o077),
        ]:
            if word != 0:
                data.append(BAUDOT_TO_ASCII.get(word, '-'))
    return "".join(data), position


def str_to_baudot(val: str, length: t.Optional[int] = None) -> t.List[int]:
    """
    Convert a string to a Baudot word list.
    """
    data = []
    for i in range(0, len(val), 3):
        word = 0
        for j in range(0, 3):
            try:
                ch = val[i + j]
            except IndexError:
                ch = "\0"
            word = (word << 6) | (ASCII_TO_BAUDOT.get(ch, 0) & 0o77)
        data.append(word)
    if length is not None:
        # Truncate or pad the data
        data = data[:length]
        if len(data) < length:
            # Add padding
            data.extend([0] * (length - len(data)))
    return data


# https://bestengineeringprojects.com/alphanumeric-codes-description-and-types/

BAUDOT_TO_ASCII = {
    # Letters
    0b110000: 'A',
    0b011100: 'C',
    0b100110: 'B',
    0b100100: 'D',
    0b100000: 'E',
    0b101100: 'F',
    0b010110: 'G',
    0b001010: 'H',
    0b011000: 'I',
    0b110100: 'J',
    0b111100: 'K',
    0b010010: 'L',
    0b001110: 'M',
    0b001100: 'N',
    0b000110: 'O',
    0b011010: 'P',
    0b111010: 'Q',
    0b010100: 'R',
    0b101000: 'S',
    0b000010: 'T',
    0b111000: 'U',
    0b011110: 'V',
    0b110010: 'W',
    0b101110: 'X',
    0b101010: 'Y',
    0b100010: 'Z',
    0b111110: '-',  #  Figures shift
    0b110110: '-',  #  Letters shift
    0b001000: ' ',  #  Space
    0b010000: '\n',  # New line (line feed)
    0b000000: '\0',  # Null
    # Figures
    0b110001: '-',
    0b011101: '?',
    0b100111: ':',
    0b100101: '$',
    0b100001: '3',
    0b101101: '!',
    0b010111: '&',
    0b001011: '#',
    0b011001: '8',
    0b110101: '`',
    0b111101: '(',
    0b010011: ')',
    0b001111: '.',
    0b001101: '\'',
    0b000111: '9',
    0b011011: '0',
    0b111011: '1',
    0b010101: '4',
    0b101001: '\b',  # Bell
    0b000011: '5',
    0b111001: '7',
    0b011111: ';',
    0b110011: '2',
    0b101111: '/',
    0b101011: '6',
    0b100011: '"',
    0b111111: '-',  #  Figures shift
    0b110111: '-',  #  Letters shift
    0b001001: ' ',  #  Space
    0b010001: '\n',  # New line (line feed)
    0b000001: '\0',  # Null
}

ASCII_TO_BAUDOT = {v: k for k, v in BAUDOT_TO_ASCII.items() if k not in [0b001001, 0b010001, 0b000001]}


def fiodec_to_str(words: t.List[int], position: int = 0) -> str:
    """
    Convert a list of FIODEC words to a string
    """
    data: t.List[str] = []
    shift = 0
    eof = False
    for word in words[position:]:
        position += 1
        chars = [
            (word >> 12) & 0o077,
            (word >> 6) & 0o077,
            (word & 0o077),
        ]
        for i, ch in enumerate(chars):
            if i == 0:
                if ch == FIODEC_END_OF_LINE:  # End of line; next two characters are line number
                    data.append('\n')
                    break
                elif ch == FIODEC_END_OF_PAGE:  # End of page; next two characters are page number
                    data.append('\f')
                    break
                elif ch == FIODEC_END_OF_FILE:  # End of file
                    eof = True
                    break
                elif ch == FIODEC_MASTER_SPACE:
                    break
            if ch == FIODEC_MASTER_SPACE:
                pass
            elif ch == FIODEC_SHIFT_ON:
                shift = 0o100
            elif ch == FIODEC_SHIFT_OFF:
                shift = 0
            else:
                if ch + shift in FIODEC_TO_ASCII:
                    data.append(FIODEC_TO_ASCII[ch + shift])
        if eof:
            break
    return "".join(data)


def str_to_fiodec(val: str) -> t.List[int]:
    """
    Convert a string to a list of FIODEC words
    """
    data: t.List[int] = []
    shift: bool = False
    current_word: t.List[int] = []
    line_number = 1  # Line number
    page_number = 1  # Page number

    def add_current_word_to_data() -> None:
        # Add the current word to the data
        # and clear the current word
        data.append(current_word[0] << 12 | current_word[1] << 6 | current_word[2])
        current_word.clear()

    def add_ch(*args: int, flush: bool = False) -> None:
        # Add one or more characters to the current word
        if flush:
            flush_current_word()
        for v in args:
            # Add a character to the current word
            current_word.append(v & 0o77)
            # If the current word is complete, add it to the data
            if len(current_word) == 3:
                add_current_word_to_data()

    def add_end_of_line() -> None:
        # Add end of line
        add_ch(FIODEC_END_OF_LINE, line_number >> 6, line_number & 0o77, flush=True)

    def add_end_of_page() -> None:
        # Add end of page
        add_ch(FIODEC_END_OF_PAGE, page_number >> 6, page_number & 0o77, flush=True)

    def add_end_of_file() -> None:
        # Add end of file
        add_ch(FIODEC_END_OF_FILE, 0, 0, flush=True)

    def flush_current_word() -> None:
        # Flush the current word
        if current_word:
            # Pad the current word with spaces
            if len(current_word) < 3:
                current_word.extend([FIODEC_MASTER_SPACE] * (3 - len(current_word)))
            # Add the current word to the data
            add_current_word_to_data()

    for ch in val:
        if ch == '\n':  # End of line, line_number
            add_end_of_line()
            line_number += 1
            if line_number > FIODEC_LINES_PER_PAGE:
                # Add a new page
                add_end_of_page()
                line_number = 1
                page_number += 1
        elif ch == '\f':  # End of page, page_number
            add_end_of_page()
            line_number = 1
            page_number += 1
        elif ch == '\x1A':  # End of file
            break
        else:
            ch = ch.upper()
            v: int = ASCII_TO_FIODEC.get(ch, None)  # type: ignore
            if v is not None:
                if v & 0o100:
                    if not shift:
                        shift = True
                        add_ch(FIODEC_SHIFT_ON)
                    add_ch(v & 0o77)
                else:
                    if shift:
                        shift = False
                        add_ch(FIODEC_SHIFT_OFF)
                    add_ch(v)

    # Add end of page
    if line_number > 1:
        add_end_of_page()
    # Add end of file
    add_end_of_file()
    # Add one 0 word
    add_ch(0, 0, 0)
    flush_current_word()
    return data


# https://simh.trailing-edge.com/docs/decsys.pdf

FIODEC_END_OF_LINE = 0o14
FIODEC_END_OF_PAGE = 0o15
FIODEC_END_OF_FILE = 0o16
FIODEC_MASTER_SPACE = 0o17
FIODEC_SHIFT_ON = 0o74
FIODEC_SHIFT_OFF = 0o72
FIODEC_LINES_PER_PAGE = 60

FIODEC_TO_ASCII = {
    0o00: ' ',  # Space
    0o01: '1',
    0o02: '2',
    0o03: '3',
    0o04: '4',
    0o05: '5',
    0o06: '6',
    0o07: '7',
    0o10: '8',
    0o11: '9',
    0o13: '\f',  # Form feed
    0o20: '0',
    0o21: '/',
    0o22: 'S',
    0o23: 'T',
    0o24: 'U',
    0o25: 'V',
    0o26: 'W',
    0o27: 'X',
    0o30: 'Y',
    0o31: 'Z',
    0o33: ',',
    0o34: ':',
    0o36: '\t',  # Tab
    0o40: '@',
    0o41: 'J',
    0o42: 'K',
    0o43: 'L',
    0o44: 'M',
    0o45: 'N',
    0o46: 'O',
    0o47: 'P',
    0o50: 'Q',
    0o51: 'R',
    0o54: '-',
    0o55: ')',
    0o56: '\\',
    0o57: '(',
    0o61: 'A',
    0o62: 'B',
    0o63: 'C',
    0o64: 'D',
    0o65: 'E',
    0o66: 'F',
    0o67: 'G',
    0o70: 'H',
    0o71: 'I',
    0o73: '.',
    # Shift
    0o100: ' ',
    0o101: '"',
    0o102: '\'',
    0o103: '~',
    0o104: '#',
    0o105: '!',
    0o106: '&',
    0o107: '<',
    0o110: '>',
    0o111: '^',
    0o120: '`',
    0o121: '?',
    0o133: '=',
    0o134: ';',
    0o140: '_',
    0o154: '+',
    0o155: ']',
    0o156: '|',
    0o157: '[',
    0o173: '*',
}

ASCII_TO_FIODEC = {v: k for k, v in FIODEC_TO_ASCII.items() if k not in [0o100]}
