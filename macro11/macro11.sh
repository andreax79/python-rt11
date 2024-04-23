#!/bin/bash
set -e

if [ $# -lt 1 ]; then
  echo 1>&1 "$0: usage $0 SOURCE.MAC"
  exit 2
fi

SOURCE="$(cd "$(dirname -- "$1")" >/dev/null; pwd -P)/$(basename -- "$1")"
BASENAME=$(basename $1)
OUT="$PWD/out"
NAME="${BASENAME%.*}"
WORK_DISK="work.dsk"
WORK_DISK_SIZE=2494464
WORK_VOL="RK0:"
BOOT_DISK="Disks/rtv53_rl.dsk"
BOOT_VOL="DL0:"
CONSOLE_PORT=5000

# Set the current working directory to the directory of this script
cd "$(dirname "$0")"

# Generate the startup file
function generate_startup_file() {
    echo -e ".ENABLE LOWERCASE,QUIET\r" > "STARTA.COM"
    echo -e ".DISABLE PREFIX,SUFFIX,TYPEAHEAD,ABORT\r" >> "STARTA.COM"
    echo -e "ASSIGN ${WORK_VOL} DK:\r" >> "STARTA.COM"
    echo -e "ASSIGN ${WORK_VOL} OUP\r" >> "STARTA.COM"
    echo -e "ASSIGN ${WORK_VOL} INP\r" >> "STARTA.COM"
    if [ "$1" == "compile" ]; then
        echo -e ".DISABLE QUIET\r" >> "STARTA.COM"
        echo -e "MACRO ${NAME}/LIST\r" >> "STARTA.COM"
        echo -e "LINK ${NAME}/MAP\r" >> "STARTA.COM"
        echo -e "DIR ${NAME}.*\r" >> "STARTA.COM"
        echo -e "HALT\r" >> "STARTA.COM"
    fi
}

# Copy the startup file to the boot disk
function copy_startup_file() {
    ../rt11.py -c "mount ${BOOT_VOL} ${BOOT_DISK}" -c "copy STARTA.COM ${BOOT_VOL}"
}

# Prepare the work disk
function prepare_work_disk() {
    echo "Creating work disk ${WORK_DISK}"
    dd bs=${WORK_DISK_SIZE} count=1 if=/dev/zero of=${WORK_DISK}
    ../rt11.py -v \
        -c "mount vol: ${WORK_DISK}" \
        -c "initialize vol:" \
        -c "copy ${SOURCE} vol:${BASENAME}"
}

# Download the boot disk
function prepare_boot_disk() {
    if [ ! -f "${BOOT_DISK}" ]; then
        echo "Downloading disk image"
        curl -LO http://www.bitsavers.org/simh.trailing-edge.com/kits/rtv53swre.tar.Z
        tar xzf rtv53swre.tar.Z
        rm rtv53swre.tar.Z
    fi
    ../rt11.py -c "mount ${BOOT_VOL} ${BOOT_DISK}" -c "copy HALT.SAV ${BOOT_VOL}"
}

# Start pdp11 emulator
function run_macro11() {
    echo "Starting pdp11"
    pdp11 macro.ini
}

# Copy the output files to the out directory
function copy_output() {
    mkdir -p ${OUT}
    ../rt11.py -v \
        -c "mount vol: ${WORK_DISK}" \
        -c "copy vol:*.* ${OUT}"
}

generate_startup_file compile
copy_startup_file
prepare_work_disk
prepare_boot_disk
run_macro11
copy_output
generate_startup_file
copy_startup_file
