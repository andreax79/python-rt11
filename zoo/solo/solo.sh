#!/bin/bash
set -e
# Set the current working directory to the directory of this script
cd "$(dirname "$0")"

BOOT_DISK="solo.dsk"
URL="http://www.bitsavers.org/bits/DEC/pdp11/Brinch_Hansen_SOLO/solo.dsk"

# Download the boot disk
if [ ! -f "${BOOT_DISK}" ]; then
    curl -L -o "${BOOT_DISK}" "${URL}"
fi

pdp11 pdp11.ini
