#!/bin/bash
set -e
# Set the current working directory to the directory of this script
cd "$(dirname "$0")"

BOOT_DISK="decsys.dtp"
URL="https://simh.trailing-edge.com/kits/decsys.zip"

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
    mv *.dtp ..
    mv decsys.rim ..
    cd ..
    rm -rf tmp
fi

pdp7 pdp7.ini
