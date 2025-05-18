#!/bin/bash
set -e
# Set the current working directory to the directory of this script
cd "$(dirname "$0")"

BOOT_DISK="caps8.tu60"
URL="https://simh.trailing-edge.com/kits/caps8_all.zip"

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
    chmod u+w -R caps8
    mv caps8/${BOOT_DISK} ..
    cd ..
    rm -rf tmp
fi

pdp8 pdp8.ini
