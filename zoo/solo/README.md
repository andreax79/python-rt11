# SOLO OPERATING SYSTEM

### List files
```
LIST(CATALOG, ALL, CONSOLE)

CONSOLE:
SOLO SYSTEM FILES

AUTOLOAD     SCRATCH      PROTECTED         1 PAGES
BACKUP       SEQCODE      PROTECTED         4 PAGES
BACKUPMAN    ASCII        PROTECTED         3 PAGES
BACKUPTEXT   ASCII        PROTECTED        14 PAGES
BUILDBATTEXT ASCII        UNPROTECTED       1 PAGES
BUILDTEXT    ASCII        UNPROTECTED       4 PAGES
CARDS        SEQCODE      PROTECTED         5 PAGES
...
TOTAPETEXT   ASCII        UNPROTECTED       4 PAGES
WRITE        SEQCODE      PROTECTED         2 PAGES
WRITEMAN     ASCII        PROTECTED         1 PAGES
WRITETEXT    ASCII        PROTECTED         9 PAGES
XMAC         ASCII        UNPROTECTED       1 PAGES
   125 ENTRIES
  3391 PAGES
```

### Display a file
```
COPY (SOLOCOPY, CONSOLE)
CONSOLE:
MOVE(1)
WRITE(AUTOLOAD)
BACKUP(WRITE)
MOVE(2)
BACKUP(CHECK)
MOVE(1)
```

### Copy a file
```
COPY(SOLOCOPY, SOLOCOPY2)
```

### Compile a Sequential Pascal program
```
SPASCAL(LISTTEXT,CONSOLE,LISTNEW)
CONSOLE:

0001 (NUMBER)
0002 "PER BRINCH HANSEN
0003
0004  INFORMATION SCIENCE
0005  CALIFORNIA INSTITUTE OF TECHNOLOGY
0006
0007  UTILITY PROGRAMS FOR
0008  THE SOLO SYSTEM
0009
0010  18 MAY 1975"
0011
0012 "###########
0013 #  PREFIX  #
0014 ###########"
0015
...
0450   END;
0451 END.
```

### Copy source from SOLO disk

To copy a file from the SOLO disk:

```
rt11 --solo solo.dsk -c 'copy dl0:LISTTEXT .'
```

### Copy all ASCII files from SOLO disk

To copy all ASCII (or SCRATCH, SEQCODE, CONCODE, SEGMENT) files from the SOLO disk to the current directory:

```
rt11 --solo solo.dsk -c 'copy dl0:*;ascii .'
```

### Copy source to SOLO disk

To copy a source file in ASCII format to the SOLO disk:

```
rt11 --solo solo.dsk -c 'copy /type:ascii LISTTEXT dl0:'
```

### Copy Kernel/Solo/OtherOS to a segment on SOLO disk

The SOLO filesystem includes three predefined, fixed-size files known as 'segments.'
These segments store the kernel, the SOLO OS, and an additional alternative copy of the OS.
In python-rt11, the segments are named @KERNEL, @SOLO, and @OTHEROS.
To copy the Kernel, Solo, or OtherOS to their respective segments on the SOLO disk:

```
rt11 --solo solo.dsk -c 'copy kernel dl0:@KERNEL'
rt11 --solo solo.dsk -c 'copy solo dl0:@SOLO'
rt11 --solo solo.dsk -c 'copy otheros dl0:@OTHEROS'
```


Links
-----

* [THE SOLO OPERATING SYSTEM: A CONCURRENT PASCAL PROGRAM PER BRINCH HANSEN](http://brinch-hansen.net/papers/1976b.pdf)
* [Solo, A single-user operating system, originally developed by Per Brinch Hansen and his team (1974-1975)](https://github.com/classic-tools/Solo)
* [Solo-Tools, Tools to manipulate Solo tape and disk images and Solo files](https://github.com/ngospina/Solo-Tools)
