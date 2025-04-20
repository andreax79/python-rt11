#!/bin/bash -
xferx=../../xferx.py
source=solo.dsk
target=target.dsk
cmd="$xferx --solo $source --solo $target"
dd bs=2457600 count=1 if=/dev/zero of=$target
$cmd -c "init dl1:"
$cmd -c "copy dl0:@KERNEL dl1:"
$cmd -c "copy dl0:@SOLO dl1:"
$cmd -c "copy dl0: dl1:"
$cmd -c "copy dl0: dl1:"
$cmd -c "dir dl1:"
