.PHONY: __list
__list:
	@grep '^[^#[:space:]].*:' Makefile | grep -v -e 'PHONY' -e '__list:'

.PHONY: rt11.dsk
rt11.dsk:
	../../xferx.py -vc "@rt11.com"

.PHONY: prodos.dsk
prodos.dsk: ciao.apple2
	../../xferx.py -vc "@prodos.com"

.PHONY: pascal.dsk
pascal.dsk:
	../../xferx.py -vc "@pascal.com"

.PHONY: appledos.dsk
appledos.dsk:
	../../xferx.py -vc "@appledos.com"

.PHONY: solo.dsk
solo.dsk:
	../../xferx.py -vc "@solo.com"

ciao.apple2:
	cl65 -v -t apple2 -u __EXEHDR__ --start-addr 8192 ciao.apple2.c -o ciao.apple2
