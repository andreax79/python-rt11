# PDP-11 MACRO-11 Assembler Compilation Tool

## Introduction
This folder provides a convenient script for compiling and linking PDP-11 assembler files using MACRO-11,
a widely used assembler for the PDP-11 architecture.
The script `macro11.sh` simplifies the process of assembling and linking MACRO-11 source files.

## MACRO-11 Assembler
MACRO-11 is a macro assembler for the PDP-11 family of minicomputers.
It allows programmers to write assembly language programs using symbolic instructions and macros, which are then translated into machine code.
MACRO-11 supports a wide range of features including symbolic expressions, macros, conditional assembly, and more.

[MACRO-11 Language Reference Manual](http://bitsavers.trailing-edge.com/pdf/dec/pdp11/rt11/v4.0_Mar80/3a/AA-5075B-TC_PDP-11_MACRO-11_Language_Reference_Manual_Jan80.pdf)

## Usage
To use the `macro11.sh` script, simply execute it in your terminal with the path to the MACRO-11 source file as the argument.
The script will assemble and link the source file, producing an output SAV file.

### Usage example

```bash
$ ./macro11/macro11.sh mac/PIP2.MAC

Creating work disk work.dsk
1+0 records in
1+0 records out
256256 bytes (256 kB, 250 KiB) copied, 0.000458269 s, 559 MB/s
?MOUNT-I-Disk work.dsk mounted to VOL:
DK:/home/andreax/devel/python-rt11/mac/PIP2.MAC -> VOL:PIP2.MAC
Starting pdp11
pdp11 PID: 5243

PDP-11 simulator V3.8-1
Disabling CR
Disabling XQ
RX: buffering file in memory
Listening on port 5000 (socket 6)
Waiting for console Telnet connection
Running
"

Connected to the PDP-11 simulator

@ <EOF>

.MACRO PIP2

.LINK PIP2/MAP

.DIR PIP2.*

PIP2  .MAC   242    -BAD-        PIP2  .OBJ    15
PIP2  .MAP     1                 PIP2  .SAV    13
 4 Files, 271 Blocks
 215 Free blocks

.D 1000=0

.START 1000
HALT instruction, PC: 001002 (RTI)
Goodbye
RX: writing buffer to file


Disconnected from the PDP-11 simulator

?MOUNT-I-Disk work.dsk mounted to VOL:
VOL:PIP2.MAC -> DK:/home/andreax/devel/python-rt11/out/PIP2.MAC
VOL:PIP2.OBJ -> DK:/home/andreax/devel/python-rt11/out/PIP2.OBJ
VOL:PIP2.MAP -> DK:/home/andreax/devel/python-rt11/out/PIP2.MAP
VOL:PIP2.SAV -> DK:/home/andreax/devel/python-rt11/out/PIP2.SAV
```
