#!/bin/bash
set -e
# Set the current working directory to the directory of this script
cd "$(dirname "$0")"

BOOT_DISK="V03B_Apr79/AS-5777C-BC_RT11_V03B_1-9.RX01"
URL="http://www.bitsavers.org/bits/DEC/pdp11/rt-11/V03B_RX01_Apr79.zip"

# Download the boot disk
if [ ! -f "${BOOT_DISK}" ]; then
    URL_REL=${URL:7}
    URL_REL=${URL_REL#*/}
    URL_REL="/${URL_REL%%\?*}"
    FILENAME="${URL_REL##/*/}"
    curl -LO ${URL}
    unzip ${FILENAME}
    rm ${FILENAME}
fi

pdp11 pdp11.ini
