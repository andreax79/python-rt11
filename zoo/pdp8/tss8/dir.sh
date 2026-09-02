# #!/usr/bin/env bash
FS=tss8
DISK=tss8_rf.dsk
ARGS=${*:-""}
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
$SCRIPT_DIR/../../../xferx.py --$FS $SCRIPT_DIR/$DISK -d dl0: -c "dir $ARGS"
