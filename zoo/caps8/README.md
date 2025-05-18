[https://bitsavers.org/pdf/dec/pdp8/caps8/DEC-8E-OCASA-B-D_CAPS8_UG.pdf]

Files are referenced symbolically by a name of as many as 6 alphanumeric characters,
followed optionally by an extension of from 1 to 3 alphanumeric characters.
The first character in a filename must be alphabetic.

Run
---

The RUN command is of the form:

```
.R [Drive #:]Filename[/Options]
```

The RUN command instructs the Monitor to load and execute the file specified in the command line.

Date
----

The DATE command set the date for the system. The command is of the form:

```
.DA mm/dd/yy
```

where mm, dd, and yy represent the current month, day and year.

Example:

```
.DA 01/06/79
```

Directory
---------

The DIR command causes a directory listing of the cassette on the drive specified
to be output on the console terminal. The command is of the form:

```
.DI [Drive #][/Options]
```

Example: 
```
.DI

01/06/79
C2BOOT.BIN            V1
MONTOR.BIN            V2
SYSCOP.BIN            V2
EDIT  .BIN            V1
BASIC .BIN            V1
PALC  .BIN            V2
UTIL  .BIN            V1
BOOT  .BIN            V3
CODT  .PA
```

Delete
------
The DEL command deletes a file from the directory. The command is of the form:

```
.DE [Drive #]:Filename.ext
```

Zero
----

The ZERO command is of the form:

```
.Z Drive #:Filename
```

and specifies that the sentinel file of the indicated cassette is to
be moved so that it immediately follows the file indicated in the
command line. 

Version
-------

The Version command is used to find out the version number of the CAPS-8 currently in use.

```
.V                                                                                                                             │
V1.3
```
