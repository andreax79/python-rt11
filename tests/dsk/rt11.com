CREATE/ALLOCATE:500 rt11.dsk
INITIALIZE/RT11 rt11.dsk
MOUNT/RT11 rt: rt11.dsk
COPY data/1.txt rt:
COPY data/2.txt rt:
COPY data/5.txt rt:
COPY data/10.txt rt:
COPY data/20.txt rt:
COPY data/50.txt rt:
COPY data/100.txt rt:
COPY data/200.txt rt:
COPY data/500.txt rt:
COPY data/1000.txt rt:
COPY data/2000.txt rt:
DIR rt:
