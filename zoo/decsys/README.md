DECSYS-7
========

`GA` - Go Ahead is the prompt for the user to enter a command.

`CONTENTS!` prints the program names and the initial block numbers of
any of the following file types: System, Library, and Working

List System files:

```
GA
CONTENTS!
INDICATE REQUIRED FILES (S,L,W)
S!
CAB DECSYS7 COPY   15 JUNE 1966
CONTEN S 0007
LABEL S 0009
COMPIL S 0010
ASSEMB S 0011
LOAD S 0012
LOADER S 0013
FOROTS S 0023
FASSMB S 0035
FORTRN S 0047
UPDATE S 0067
EDIT S 0076
GA
```

List Library files:

```
GA
CONTENS!
INDICATE REQUIRED FILES (S,L,W)
L!
CAB DECSYS7 COPY   15 JUNE 1966
NARITH, L 0092
EARITH, L 0094
SARITH, L 0096
.IO1, L 0097
.IO2, L 0099
.IO3, L 0101
.IO4, L 0103
.IODEC, L 0105
.IO8, L 0113
.IO5, L 0115
.IO6, L 0117
.IO7, L 0118
.IO57A, L 0120
NINDIG, L 0126
SQRTF., L 0127
XPN, L 0128
EXP, L 0129
EXPF., L 0130
LOGF., L 0133
CLOGF., L 0134
LOGCOM, L 0135
SINF.,COSF., L 0136
ATANF., L 0139
ABSF., L 0141
XABSF., L 0142
GA
```

List Working files:
For working files, CONTENTS! prints the starting block number of each fork
(FORTRAN, Assembler, Binary).

```
GA
CONTENTS!
INDICATE REQUIRED FILES (S,L,W)
W!
CAB DECSYS7 COPY   15 JUNE 1966
HELLO W 0143,0144,0145
TEST W 0146,0000,0000
LONG W 0147,0000,0000
GA
```

```
EDIT!     -- Start the text editor
READY TAPES ON TWO AND THREE
```

The editor is now in the executive-command mode, awaiting one of three possible commands:
- E - Transfers control to the edit mode
- Z - Transfers control to the create mode
- K - Returns control to the Monitor

When called, the edit/create mode waits for the user to indicate the program
and file type to be edited/created.

Edit an existing Assembler program:

```
E -- E for EDIT
A,HELLO!    -- A for Assembler, filename
```

- S - Read the next page (60 lines)
- W - Type the page

Create a new FORTRAN program:

```
Z
F,HELLO!    -- F for FORTRAN, filename
```

enter text

[BACKSPACE] -- 2 times - exit edit mode
[BACKSPACE]
K  -- exit editor


https://vintagesoftwarefun.wordpress.com/2014/07/26/hello-world/
https://github.com/simh/simh/issues/917
