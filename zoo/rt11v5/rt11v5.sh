#!/bin/bash
set -e
# Set the current working directory to the directory of this script
cd "$(dirname "$0")"

BOOT_DISK="rt11v503.dsk"
URL="https://pdp-11.org.ru/files/rt-11/rt11v503.zip"

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
