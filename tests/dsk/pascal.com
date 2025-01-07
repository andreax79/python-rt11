CREATE/ALLOCATE:280 pascal.dsk
INITIALIZE/PASCAL /NAME:PAS pascal.dsk
MOUNT/PASCAL pas: pascal.dsk
COPY/TYPE:TEXT data/1.txt pas:
COPY/TYPE:TEXT data/2.txt pas:
COPY/TYPE:TEXT data/5.txt pas:
COPY/TYPE:TEXT data/10.txt pas:
COPY/TYPE:TEXT data/20.txt pas:
CREATE/ALLOCATE:1 /TYPE:BAD pas:bad0
COPY/TYPE:TEXT data/50.txt pas:
COPY/TYPE:TEXT data/100.txt pas:
COPY/TYPE:TEXT data/200.txt pas:
COPY/TYPE:TEXT data/500.txt pas:
COPY/TYPE:DATA data/1000.txt pas:
CREATE/ALLOCATE:1 /TYPE:BAD pas:bad
DELETE pas:bad0
DIR pas:
