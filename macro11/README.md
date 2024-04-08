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

```bash
./macro11.sh source.mac

