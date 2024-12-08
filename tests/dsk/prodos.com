CREATE/ALLOCATE:280 prodos.dsk
MOUNT/PRODOS pr: prodos.dsk
INITIALIZE/NAME:PR pr:
CREATE/DIRECTORY pr:small
COPY/TYPE:TXT data/1.txt pr:small
COPY/TYPE:TXT data/2.txt pr:small
COPY/TYPE:TXT data/5.txt pr:small
CREATE/DIRECTORY pr:small/medium
COPY/TYPE:TXT data/10.txt pr:small/medium
COPY/TYPE:TXT data/20.txt pr:small/medium
COPY/TYPE:TXT data/50.txt pr:small/medium
CREATE/DIRECTORY pr:medium
COPY/TYPE:TXT data/100.txt pr:medium
COPY/TYPE:TXT data/200.txt pr:medium
COPY/TYPE:TXT data/500.txt pr:medium
COPY/TYPE:TXT data/1000.txt pr:
DIR pr:small
