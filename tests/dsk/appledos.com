CREATE/ALLOCATE:280 appledos.dsk
MOUNT/APPLEDOS dos: appledos.dsk
INITIALIZE dos:
COPY/TYPE:T data/1.txt dos:
COPY/TYPE:T data/2.txt dos:
COPY/TYPE:T data/5.txt dos:
COPY/TYPE:T data/10.txt dos:
COPY/TYPE:T data/20.txt dos:
COPY/TYPE:T data/50.txt dos:
COPY/TYPE:T data/100.txt dos:
COPY/TYPE:T data/200.txt dos:
COPY/TYPE:T data/500.txt dos:
COPY/TYPE:T data/1000.txt dos:
DIR dos:
