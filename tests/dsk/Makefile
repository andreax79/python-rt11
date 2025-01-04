.PHONY: __list
__list:
	@grep '^[^#[:space:]].*:' Makefile | grep -v -e 'PHONY' -e '__list:'

.PHONY: prodos.dsk
prodos.dsk: ciao.apple2
	../../rt11.py -vc "@prodos.com"

.PHONY: pascal.dsk
pascal.dsk:
	../../rt11.py -vc "@pascal.com"

.PHONY: appledos.dsk
appledos.dsk:
	../../rt11.py -vc "@appledos.com"

ciao.apple2:
	cl65 -v -t apple2 -u __EXEHDR__ --start-addr 8192 ciao.apple2.c -o ciao.apple2
