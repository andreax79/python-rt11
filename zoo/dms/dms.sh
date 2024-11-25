#!/bin/bash
set -e
# Set the current working directory to the directory of this script
cd "$(dirname "$0")"

BOOT_DISK="dms_demo.df32"
URL="https://simh.trailing-edge.com/kits/dms8.zip"

# Download the boot disk
if [ ! -f "${BOOT_DISK}" ]; then
    mkdir -p tmp
    cd tmp
    URL_REL=${URL:7}
    URL_REL=${URL_REL#*/}
    URL_REL="/${URL_REL%%\?*}"
    FILENAME="${URL_REL##/*/}"
    curl -LO ${URL}
    unzip ${FILENAME}
    mv ${BOOT_DISK} ..
    cd ..
    rm -rf tmp
fi

pdp8 pdp8.ini
