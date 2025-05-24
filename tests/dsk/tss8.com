CREATE/ALLOCATE:1024 tss8.dsk
INITIALIZE/TSS8 /NAME:PR tss8.dsk
MOUNT/TSS8 pr: tss8.dsk
CREATE/DIRECTORY pr:[10,20]
COPY/ASCII data/1.txt pr:[10,20]F1.asc
COPY/ASCII data/2.txt pr:[10,20]F2.asc
COPY/ASCII data/5.txt pr:[10,20]F5.asc
CREATE/DIRECTORY pr:[11,21]
COPY/ASCII data/10.txt pr:[11,21]M10.asc
COPY/ASCII data/20.txt pr:[11,21]M20.asc
COPY/ASCII data/50.txt pr:[11,21]M50.asc
CREATE/DIRECTORY pr:[12,22]
COPY/ASCII data/100.txt pr:[12,22]L100.asc
COPY/ASCII data/200.txt pr:[12,22]L200.asc
COPY/ASCII data/500.txt pr:[12,22]L500.asc
DIR pr:
