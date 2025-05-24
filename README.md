XFERX
=====

XFERX is an utility for transferring files between various file systems.

| Fs / Features     | Read file         | Write file        | Delete file       | Initialize        | Create dir/special|
| ----------------- | ----------------- | ----------------- | ----------------- | ----------------- | ----------------- | 
| RT-11             | Yes               | Yes               | Yes               | Yes               | N/A               |
| DOS-11            | Yes               | Yes               | Yes               | No                | Yes               |
| DOS-11 DecTape    | Yes               | Yes               | Yes               | No                | No                |
| DOS-11 MagTape    | Yes               | Yes               | Yes               | Yes               | No                |
| XXDP              | Yes               | Yes               | Yes               | No                | N/A               |
| CAPS-11           | Yes               | Yes               | Yes               | Yes               | N/A               |
| Files-11          | Yes               | No                | No                | No                | No                |
| SOLO              | Yes               | Yes               | Yes               | Yes               | N/A               |
| PDP-7 UNIX v0     | Yes               | No                | No                | No                | No                |
| PDP-7 DECSys      | Yes               | Yes               | Yes               | No                | No                |
| UNIX v1           | Yes               | No                | No                | No                | No                |
| UNIX v5           | Yes               | No                | No                | No                | No                |
| UNIX v6           | Yes               | No                | No                | No                | No                |
| UNIX v7           | Yes               | No                | No                | No                | No                |
| RSTS/E            | Yes               | No                | No                | No                | No                |
| OS/8              | Yes               | Yes               | Yes               | Yes               | N/A               |
| 4k Disk Monitor   | Yes               | Yes               | Yes               | Yes               | N/A               |
| CAPS-8            | Yes               | Yes               | Yes               | Yes               | N/A               |
| TSS/8             | Yes               | Yes               | Yes               | Yes               | Yes               |
| Apple II ProDOS   | Yes               | Yes               | Yes               | Yes               | Yes               |
| Apple II Pascal   | Yes               | Yes               | Yes               | Yes               | N/A               |
| Apple DOS 3.x     | Yes               | Yes               | Yes               | Yes               | N/A               |
| Data General DOS/RDOS         | Yes               | Yes               | Yes               | No                | No                |
| Data General DOS/RDOS MagTape | Yes               | No                | No                | Yes               | N/A               |
| Data General DOS/RDOS Dump    | Yes               | No                | No                | No                | No                |

Commands
--------

The file system must be logically mounted and assigned a logical device name before use.
This is done with the MOUNT command.

The following commands are availables:

* @               Executes a command file
* ASSIGN          Associates a logical device name with a device
* CD              Changes or displays the current working drive and directory
* COPY            Copies files
* CREATE          Creates files or directories
* DEASSIGN        Removes logical device name assignments
* DELETE          Removes files from a volume
* DIR             Lists file directories
* DISMOUNT        Disassociates a logical disk assignment from a file
* DUMP            Prints formatted data dumps of files or devices
* EXAMINE         Examines disk structure
* EXIT            Exit the shell
* HELP            Displays commands help
* INITIALIZE      Writes an empty device directory on the specified volume
* MOUNT           Assigns a logical disk unit to a file
* PWD             Displays the current working drive and directory
* SHELL           Executes a system shell command
* SHOW		      Displays software status
* TYPE            Outputs files to the terminal

Usage example
-------------

```
[SY:/home/andreax/devel/xferx] mount DL0: test.dsk
?MOUNT-I-Disk test.dsk mounted to DL0:
[SY:/home/andreax/devel/xferx] DL0:
[DL0:] dir
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
[DL0:] copy *.txt sy:
DK:HELP.TXT -> SY:/home/andreax/devel/xferx/HELP.TXT
DK:LOG.TXT -> SY:/home/andreax/devel/xferx/LOG.TXT
[DL0:] mount /dos DL1: SY:BA-F019F-MC_CZZMAF0_DYDP+1_XXDP_UTILITY_1980.DSK
?MOUNT-I-Disk BA-F019F-MC_CZZMAF0_DYDP+1_XXDP_UTILITY_1980.DSK mounted to DL0:
[DL0:] dir DL1:
HSAAA0.SYS    24  22-Mar-80    HUDIA0.SYS     6  22-Mar-80
HELP  .TXT    26  22-Mar-80    HDDYA0.SYS     3  22-Mar-80
HDCTA0.SYS     2  22-Mar-80    HDDBA0.SYS     2  22-Mar-80
HDDDA1.SYS     3  22-Mar-80    HDDKA0.SYS     2  22-Mar-80
HDDLB0.SYS     4  22-Mar-80    HDDMA0.SYS     3  22-Mar-80
HDDPA0.SYS     2  22-Mar-80    HDDRA1.SYS     3  22-Mar-80
HDDSA0.SYS     2  22-Mar-80    HDDTA0.SYS     2  22-Mar-80
HDDXA0.SYS     3  22-Mar-80    HDKBA0.SYS     1  22-Mar-80
HDMMA0.SYS     2  22-Mar-80    HDMSA0.SYS     3  22-Mar-80
HDMTA0.SYS     2  22-Mar-80    HDPDA0.SYS     3  22-Mar-80
HDPPA0.SYS     1  22-Mar-80    HDPRA0.SYS     1  22-Mar-80
HDPTA0.SYS     1  22-Mar-80    HMCTA0.SYS    17  22-Mar-80
HMDBA0.SYS    16  22-Mar-80    HMDDA1.SYS    17  22-Mar-80
HMDKA0.SYS    16  22-Mar-80    HMDLB0.SYS    11  22-Mar-80
HMDMA0.SYS    17  22-Mar-80    HMDPA0.SYS    16  22-Mar-80
HMDRA2.SYS    17  22-Mar-80    HMDSA0.SYS    16  22-Mar-80
HMDTA0.SYS    16  22-Mar-80    HMDXA0.SYS    17  22-Mar-80
HMMSA0.SYS    17  22-Mar-80    HMDYA0.SYS    17  22-Mar-80
HMMMA0.SYS    17  22-Mar-80    HMMTA0.SYS    17  22-Mar-80
HMPDA0.SYS    17  22-Mar-80    UPD1  .BIN    12  22-Mar-80
UPD2  .BIN    16  22-Mar-80    XTECO .BIN    16  22-Mar-80
DXCL  .BIN    32  22-Mar-80    SETUP .BIN    26  22-Mar-80
ZFLAB0.BIN     8  22-Mar-80

TOTL BLKS:   472
TOTL FILES:   45

[DL0:] mount dl2: /dos dos_rk.dsk
?MOUNT-I-Disk dos_rk.dsk mounted to DL0:
[DL0:] dir DL2:
DIRECTORY DL2: [1,1]

24-MAY-11

BADB  .SYS     1  05-NOV-98 <377>
MONLIB.CIL   180C 05-NOV-98 <377>
VERIFY.LDA    65C 05-NOV-98 <233>
FOO   .BAR     3  06-NOV-98 <233>
OVRLAY.LIB     5  05-NOV-98 <233>
LINK  .LDA    67C 05-NOV-98 <233>
CILUS .LDA    33C 05-NOV-98 <233>
PIP   .LDA    36C 05-NOV-98 <233>
MACRO .LDA    39C 05-NOV-98 <233>
EDIT  .LDA    13C 05-NOV-98 <233>
FILDMP.LDA     9C 05-NOV-98 <233>
LIBR  .LDA    10C 05-NOV-98 <233>
FILCOM.LDA    12C 05-NOV-98 <233>
CREF  .LDA     9C 05-NOV-98 <233>

TOTL BLKS:   482
TOTL FILES:   14
```

Links
=====

RT-11
-----

* [RT-11 Software Support Manual](http://www.bitsavers.org/www.computer.museum.uq.edu.au/RT-11/DEC-11-ORPGA-A-D%20RT-11%20Software%20Support%20Manual.pdf)
* [RT–11 Volume and File Formats Manual](http://bitsavers.trailing-edge.com/pdf/dec/pdp11/rt11/v5.6_Aug91/AA-PD6PA-TC_RT-11_Volume_and_File_Formats_Manual_Aug91.pdf)

DOS-11
------

* [Disk Operating System Monitor - System Programmers Manual](http://www.bitsavers.org/pdf/dec/pdp11/dos-batch/DEC-11-OSPMA-A-D_PDP-11_DOS_Monitor_V004A_System_Programmers_Manual_May72.pdf)
* [DOS/BATCH File Utility Package](http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp11/dos-batch/V9/DEC-11-UPPA-A-D_PIP_Aug73.pdf)

XXDP
----

* [XXDP File Structure Guide](https://raw.githubusercontent.com/rust11/xxdp/main/XXDP%2B%20File%20Structure.pdf)

CAPS-8
------

* [CAPS-8 Users Manual](https://bitsavers.org/pdf/dec/pdp8/caps8/DEC-8E-OCASA-B-D_CAPS8_UG.pdf)

CAPS-11
-------

* [CAPS-11 User Guide](http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp11/caps-11/DEC-11-OTUGA-A-D_CAPS-11_Users_Guide_Oct73.pdf)

SOLO
----

* [THE SOLO OPERATING SYSTEM: A CONCURRENT PASCAL PROGRAM PER BRINCH HANSEN](http://brinch-hansen.net/papers/1976b.pdf)

UNIX
----

* [PDP-7 UNIX version 0 fs man page](https://github.com/DoctorWkt/pdp7-unix/blob/master/man/fs.5)
* [Unix on the PDP-7 from a scan of the original assembly code](https://github.com/DoctorWkt/pdp7-unix)
* [UNIX version 1 fs man page](http://squoze.net/UNIX/v1man/man5/fs)
* [UNIX version 2 fs man page](http://squoze.net/UNIX/v2man/man5/fs)
* [UNIX version 3 fs man page](http://squoze.net/UNIX/v3man/man5/fs)
* [UNIX version 4 fs man page](http://squoze.net/UNIX/v4man/man5/fs)
* [UNIX version 5 fs man page](http://squoze.net/UNIX/v5man/man5/fs)
* [UNIX version 6 fs man page](http://squoze.net/UNIX/v6man/man5/fs)

RSTS/E
------

* [RSTS/E Monitor Internals, Michael Mayfield](http://elvira.stacken.kth.se/rstsdoc/rsts-doc-v80/extra/mayfieldRSTS8internals.pdf)
* [RSTS/E V8.0 Internals Manual](https://bitsavers.org/pdf/dec/pdp11/rsts_e/V08/AA-CL35A-TE_8.0intern_Sep84.pdf)

PDP-7 DECSys
------------

* [DECSys-7 Operating Manual](http://bitsavers.informatik.uni-stuttgart.de/pdf/dec/pdp7/DEC-07-SDDA-D_DECSYS7_Nov66.pdf)
* [Technical Notes on DECsys](https://simh.trailing-edge.com/docs/decsys.pdf)

PDP-8 OS/8
----------

* [OS/8 Software Support Manual](https://www.bitsavers.org/pdf/dec/pdp8/os8/DEC-S8-OSSMB-A-D_OS8_v3ssup.pdf)

PDP-8 4k Disk Monitor
---------------------

* [PDP-8 4K Disk Monitor System](https://svn.so-much-stuff.com/svn/trunk/pdp8/src/dec/dec-08-odsma/dec-08-odsma-a-d.pdf)
* [PDP-8 Disc System Builder](https://svn.so-much-stuff.com/svn/trunk/pdp8/src/dec/dec-d8-sba/dec-d8-sbab-d.pdf)

PDP-8 TSS/8
-------------
 

* [TSS/8 TIME-SHARING SYSTEM USER'S GUIDE](https://bitsavers.org/pdf/dec/pdp8/tss8/DEC-T8-MRFB-D_UserGde_Feb70.pdf)
* [System Manager's Guide for PDP-8E TSS 8.24 Monitor](https://bitsavers.org/pdf/dec/pdp8/tss8/TSS8_8.24_ManagersGuide.pdf)


Apple II ProDOS / Apple III SOS (Sophisticated Operating System)
----------------------------------------------------------------

* [ProDOS 8 Technical Reference Manual](http://www.easy68k.com/paulrsm/6502/PDOS8TRM.HTM)
* [Apple III SOS Reference Manual Volume 1 - How SOS Works.PDF](https://apple3.org/Documents/Manuals/Apple%20III%20SOS%20Reference%20Manual%20Volume%201%20-%20How%20SOS%20Works.PDF)

Apple II Pascal
---------------

* [Apple II Pascal 1.3](https://archive.org/details/apple-ii-pascal-1.3/page/n803/mode/2up)


Apple II AppleDOS
-----------------

* [Beneath Apple DOS](https://archive.org/details/Beneath_Apple_DOS_alt/page/n17/mode/2up)
* [Beneath Apple DOS ProDOS 2020](https://archive.org/details/beneath-apple-dos-prodos-2020/page/30/mode/2up)

Data General DOS / RDOS
-----------------------

* [Real Time Disk Operating System (RDOS) Reference Manual](https://bitsavers.org/pdf/dg/software/rdos/093-000075-08_RDOS_Reference_Manual_Mar79.pdf)
* [Diskette Operating System Reference Manual](https://bitsavers.org/pdf/dg/software/093-000201-00_Diskette_Operating_System_Ref_Feb77.pdf)
