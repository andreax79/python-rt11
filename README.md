python-rt11
===========

Utility for reading/writing RT11 filesystems

The file system must be logically mounted and assigned a logical device name before use.
This is done with the MOUNT command.

The following commands are availables:

* CD              Changes or displays the current working drive and directory
* COPY            Copies files
* CREATE          Creates a file with a specific name and size
* DEL             Removes files from a volume
* DIR             Lists file directories
* DISMOUNT        Disassociates a logical disk assignment from a file
* EXAMINE         Examines disk/block/file structure
* EXIT            Exit the shell
* HELP            Displays commands help
* INITIALIZE      Writes an RT–11 empty device directory on the specified volume
* MOUNT           Assigns a logical disk unit to a file
* PWD             Displays the current working drive and directory
* SHELL           Executes a system shell command
* SHOW            Displays the volume assignment
* TYPE            Outputs files to the terminal

Usage example
-------------

```
[SY:/Users/andreax/Devel/python-rt11] mount test: test.dsk
?MOUNT-I-Disk test.dsk mounted to TEST:
[SY:/Users/andreax/Devel/python-rt11] test:
[TEST:] dir
BOS   .SAV    61  21-Nov-95    VCG   .SAV    40  24-Aug-92
CLI   .SAV    26  24-Aug-92    FRUN  .SAV     4  24-Aug-92
PRINT .SAV    31  24-Aug-92    WHOIS .SAV    24  24-Aug-92
NETSPY.SAV    18  24-Aug-92    LOGIN .SAV    14  24-Aug-92
NETCLK.SAV    17  24-Aug-92    SPQSRV.SAV    22  24-Aug-92
PRTQ  .SAV    92  24-Aug-92    FINGER.SAV     6  24-Aug-92
RSOLV .SAV    20  24-Aug-92    TELSRV.SAV    23  24-Aug-92
TN    .SAV    35  24-Aug-92    LOGOUT.SAV    12  24-Aug-92
HOSTS .SAV    14  24-Aug-92    FTP   .SAV    29  24-Aug-92
FTPSRV.SAV    27  24-Aug-92    SMTP  .SAV    39  24-Aug-92
LOG   .SAV     9  24-Aug-92    SMPSRV.SAV    35  24-Aug-92
CRMAIL.SAV    14  27-Apr-86    XNET  .SAV    25  24-Aug-92
PING  .SAV    28  24-Aug-92    MSG   .SAV    59  27-Apr-86
HELPF .SAV     7  01-Mar-80    UDP   .SAV    69  24-Aug-92
SNDMSG.SAV    45  27-Apr-86    SYSMGR.SAV    37  05-Jul-83
HELP  .TXT   382  24-Aug-92    LOG   .TXT   100  21-Nov-95
UNSENT.MSG   100  21-Nov-95    < UNUSED >    29
CAT   .MAC    13  31-Dec-88    CAT   .SAV     5  31-Dec-88
< UNUSED >  8661
 35 Files, 1482 Blocks
  8690 Free blocks
[TEST:] copy *.txt sy:
DK:HELP.TXT -> SY:/Users/andreax/Devel/python-rt11/HELP.TXT
DK:LOG.TXT -> SY:/Users/andreax/Devel/python-rt11/LOG.TXT
```

Links
-----

[RT-11 Software Support Manual](http://www.bitsavers.org/www.computer.museum.uq.edu.au/RT-11/DEC-11-ORPGA-A-D%20RT-11%20Software%20Support%20Manual.pdf)
[RT–11 Volume and File Formats Manual](http://bitsavers.trailing-edge.com/pdf/dec/pdp11/rt11/v5.6_Aug91/AA-PD6PA-TC_RT-11_Volume_and_File_Formats_Manual_Aug91.pdf)
