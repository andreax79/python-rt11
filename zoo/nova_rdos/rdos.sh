#!/bin/bash
set -e
# Set the current working directory to the directory of this script
cd "$(dirname "$0")"

BOOT_DISK="rdos_d31.dsk"
URL="https://simh.trailing-edge.com/kits/rdosswre.tar.Z"
LICENSE_README="Licenses/README.txt"
LICENSE_FILE="Licenses/rdos_license.txt"

# Download the boot disk
if [ ! -f "${BOOT_DISK}" ]; then
    mkdir -p tmp
    cd tmp
    URL_REL=${URL:7}
    URL_REL=${URL_REL#*/}
    URL_REL="/${URL_REL%%\?*}"
    FILENAME="${URL_REL##/*/}"
    curl -LO ${URL}
    tar xf ${FILENAME}

    # Show license information
    echo
    cat "$LICENSE_README"
    echo

    # Ask to view license
    read -p "Do you want to view the license file? (y/n): " show_license
    if [[ "$show_license" =~ ^[Yy]$ ]]; then
        if command -v less >/dev/null 2>&1; then
            less "$LICENSE_FILE"
        else
            cat "$LICENSE_FILE"
        fi
    fi

    # Ask to accept license
    read -p "Do you accept the license agreement? (y/n): " accept_license
    if [[ ! "$accept_license" =~ ^[Yy]$ ]]; then
        echo "License not accepted. Exiting."
        cd ..
        rm -rf tmp
        exit 1
    fi

    echo "License accepted. Continuing..."

    mv Disks/${BOOT_DISK} ..
    cd ..
    rm -rf tmp
fi

nova nova.ini
