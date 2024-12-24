.PHONY: __list
__list:
	@grep '^[^#[:space:]].*:' Makefile | grep -v -e 'PHONY' -e '__list:'

.PHONY: prodos.dsk
prodos.dsk:
	../../rt11.py -vc "@prodos.com"

.PHONY: pascal.dsk
pascal.dsk:
	../../rt11.py -vc "@pascal.com"

.PHONY: appledos.dsk
appledos.dsk:
	../../rt11.py -vc "@appledos.com"
