CREATE/ALLOCATE:4800 solo.dsk
INITIALIZE/SOLO solo.dsk
MOUNT/SOLO solo: solo.dsk
COPY/TYPE:ASCII data/1.txt solo:
COPY/TYPE:ASCII data/10.txt solo:
COPY/TYPE:ASCII data/2.txt solo:
COPY/TYPE:ASCII data/20.txt solo:
COPY/TYPE:ASCII data/5.txt solo:
COPY/TYPE:ASCII data/50.txt solo:
COPY/TYPE:ASCII ../../LICENSE solo:
DIR solo:
