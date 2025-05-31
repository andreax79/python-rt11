## Basic RDOS Commands

## File Management Commands

| Command                            | Description                                                                 |
|------------------------------------|-----------------------------------------------------------------------------|
| `APPEND groupfilename filename...` | Combine two or more files.                                                  |
| `BPUNCH filename...`               | Punch a binary file.                                                        |
| `BUILD outputfilename filename...` | Build a file from multiple filenames.                                       |
| `CCONT filename blockct`           | Create a contiguous file.                                                   |
| `CHATR filename [+/- attribs]`     | Change a file’s attributes.                                                 |
| `CHLAT filename /+ /- attribs`     | Change a file’s link access attributes.                                     |
| `CLEAR [filename]`                 | Set file use count to zero.                                                 |
| `CRAND filename`                   | Create a random file.                                                       |
| `CREATE filename`                  | Create a sequential file (or a random file in DOS mode).                    |
| `DUMP dumpfilename [filename...]`  | Dump a file in CLI DUMP format.                                             |
| `FILCOM filename1 filename2`       | Compare the contents of two files.                                          |
| `FPRINT filename`                  | Print a file in octal or another specified format.                          |
| `LINK linkname resfilename`        | Create a link entry to a resource filename.                                 |
| `LIST filename`                    | List the statistics of a file.                                              |
| `LOAD dumpfilename [filename...]`  | Load DUMPed files.                                                          |
| `LOG [password]`                   | Start recording in the log file.                                            |
| `MKABS savefilename binaryname`    | Make an absolute file from a save file.                                     |
| `MKSAVE binaryname savefilename`   | Make a save file from an absolute file.                                     |
| `MOVE directory [filename...]`     | Copy a file to any directory.                                               |
| `PUNCH filename...`                | Punch an ASCII file.                                                        |
| `RENAME oldname newname`           | Rename a file.                                                              |
| `REV filename`                     | Display the revision level of a program file.                               |
| `SAVE filename`                    | Rename a breakfile.                                                         |
| `TYPE filename`                    | Display (type) a file on the console.                                       |
| `XFER sourcefile destinationfile`  | Copy the contents of one file to another file.                              |

## Directory & System Control Commands

| Command                              | Description                                                                 |
|--------------------------------------|-----------------------------------------------------------------------------|
| `COIR directoryname`                 | Create an RDOS subdirectory or DOS directory.                              |
| `COPY diskette1 diskette2`           | Copy contents from one diskette to another (DOS).                          |
| `CPART partname blockct`             | Create a secondary partition (RDOS).                                       |
| `DIR directoryname`                  | Change the current directory.                                              |
| `DISK`                               | Display the number of blocks used and remaining on the current partition or DOS diskette. |
| `DUMP outputfilename`                | Copy the contents of the current directory to an output file.              |
| `GDIR`                               | Display the current directory name.                                        |
| `INIT directory_or_tapedrive`        | Initialize a directory or tape drive.                                      |
| `LDIR`                               | Display the last current directory name.                                   |
| `LOAD dumpfilename`                  | Reload DUMPed files.                                                       |
| `LIST [filename]`                    | List file information.                                                     |
| `MDIR`                               | Display the master directory name.                                         |
| `MOVE directory [filename...]`       | Copy files to any directory.                                               |
| `RELEASE directory_or_tapedrive`     | Release a directory or tape drive.                                         |

## System Control Commands

| Command                  | Description                                                                   |
|--------------------------|-------------------------------------------------------------------------------|
| `filename[.SV]`          | Execute the specified program.                                                |
| `BOOT disk_or_system`    | Bootstrap a system from disk.                                                 |
| `CHAIN filename`         | Overwrite the CLI with an executable program.                                 |
| `CLEAR [filename]`       | Set file or device use count to zero.                                         |
| `DISK`                   | Display the number of disk blocks used and remaining.                         |

## System Utilities & Miscellaneous Commands

| Command                                | Description                                                                 |
|----------------------------------------|-----------------------------------------------------------------------------|
| `EXFG program`                         | Execute a program in the foreground (RDOS).                                |
| `GSYS`                                 | Display the current system name.                                           |
| `GTOD`                                 | Display the current system time.                                           |
| `FGND`                                 | Describe the foreground program status.                                    |
| `INIT directory_or_tapedrive`          | Initialize a disk directory or tape drive.                                 |
| `LOG [password]`                       | Start recording in the log file.                                           |
| `ENDLOG [password]`                    | Close the LOG file.                                                         |
| `MESSAGE text`                         | Display a text message.                                                    |
| `POP`                                  | Return to the program on the next higher level.                            |
| `SDAY mm dd yy`                        | Set the system calendar.                                                   |
| `SMEM background`                      | Set background/foreground memory areas (mapped RDOS).                      |
| `STOD [hh] [mm] [ss]`                  | Set the system clock.                                                      |


Example of using a command:

```
GTOD
06/04/125   17:34:06
```


## Programming Utilities

| Command              | Description                                                  |
|----------------------|--------------------------------------------------------------|
| `ALGOL filename...`  | Compile an ALGOL source file (RDOS).                         |
| `ASM filename...`    | Assemble a source file, producing an `.RB` file.             |
| `BASIC`              | Invoke the BASIC interpreter.                                |


## Variables

The RDOS CLI recognizes the following variable names:

| Variable	| Description                                                             |
|-----------|-------------------------------------------------------------------------|
| %DATE%	| Today's date, in the form mm-dd-vy (e.g., 06-04-85).                    |
| %GCIN%	| The input console name (e.g., STTI).                                    |
| %GCOUT%	| The output console name (e.g., STTO).                                   |
| %GDIR%	| The current directory name (e.g., SUBDIR).                              |
| %LDIR%	| The name of the previous current directory (e.g., DP1).                 |
| %MDIR%	| The master directory name (e.g., DPO).                                  |
| %FGND%	| The character "*F" if CLI is executing in the foreground; nothing if CLI is executing in the background. |
| %TIME%	| The time of day, in the form hh:mm:ss.                                  |


Example of using a variable in a command:

```
MESSAGE %TIME%
17:36:06
```

