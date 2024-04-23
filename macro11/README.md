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
$ ./macro11/macro11.sh mac/hello.mac

?MOUNT-I-Disk Disks/rtv53_rl.dsk mounted to DL0:
DK:/home/andreax/devel/python-rt11/macro11/STARTA.COM -> DL0:/home/andreax/devel/python-rt11/macro11/STARTA.COM
Creating work disk work.dsk
1+0 records in
1+0 records out
2494464 bytes (2.5 MB, 2.4 MiB) copied, 0.00595551 s, 419 MB/s
?MOUNT-I-Disk work.dsk mounted to VOL:
DK:/home/andreax/devel/python-rt11/mac/hello.mac -> VOL:hello.mac
?MOUNT-I-Disk Disks/rtv53_rl.dsk mounted to DL0:
DK:/home/andreax/devel/python-rt11/macro11/HALT.SAV -> DL0:/home/andreax/devel/python-rt11/macro11/HALT.SAV
Starting pdp11
.MACRO hello/LIST
.LINK hello/MAP
.DIR hello.*

HELLO .MAC     2    -BAD-        HELLO .OBJ     1
HELLO .SAV     2                 HELLO .LST     4
HELLO .MAP     1
 5 Files, 10 Blocks
 4824 Free blocks
.HALT

PDP-11 simulator V3.8-1
Disabling CR
Disabling XQ

HALT instruction, PC: 001002 (HALT)
Goodbye
?MOUNT-I-Disk work.dsk mounted to VOL:
VOL:HELLO.MAC -> DK:/home/andreax/devel/python-rt11/out/HELLO.MAC
VOL:HELLO.OBJ -> DK:/home/andreax/devel/python-rt11/out/HELLO.OBJ
VOL:HELLO.SAV -> DK:/home/andreax/devel/python-rt11/out/HELLO.SAV
VOL:HELLO.LST -> DK:/home/andreax/devel/python-rt11/out/HELLO.LST
VOL:HELLO.MAP -> DK:/home/andreax/devel/python-rt11/out/HELLO.MAP
?MOUNT-I-Disk Disks/rtv53_rl.dsk mounted to DL0:
DK:/home/andreax/devel/python-rt11/macro11/STARTA.COM -> DL0:/home/andreax/devel/python-rt11/macro11/STARTA.COM
```
