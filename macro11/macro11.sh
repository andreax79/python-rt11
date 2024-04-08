#!/bin/bash
set -e
# Set the current working directory to the directory of this script
cd "$(dirname "$0")"

if [ $# -lt 1 ]; then
  echo 1>&1 "$0: usage $0 SOURCE.MAC"
  exit 2
fi

SOURCE=$1
BASENAME=$(basename $1)
NAME="${BASENAME%.*}"
WORK_DISK="work.dsk"
WORK_DISK_SIZE=256256
BOOT_DISK="Disks/rtv53_rl.dsk"
CONSOLE_PORT=5000

# Download and configure the boot disk
if [ ! -f "${BOOT_DISK}" ]; then
    echo "Downloading disk image"
    curl -LO http://www.bitsavers.org/simh.trailing-edge.com/kits/rtv53swre.tar.Z
    tar xzf rtv53swre.tar.Z
    rm rtv53swre.tar.Z
    ../rt11.py -c "mount DL0: Disks/rtv53_rl.dsk" -c "copy STARTA.COM DL0:"
fi

# Prepare the work disk
echo "Creating work disk ${WORK_DISK}"
dd bs=${WORK_DISK_SIZE} count=1 if=/dev/zero of=${WORK_DISK}
../rt11.py -v \
    -c "mount vol: ${WORK_DISK}" \
    -c "initialize vol:" \
    -c "copy ${SOURCE} vol:${BASENAME}"

# Start pdp11
echo "Starting pdp11"
pdp11 telnet.ini &
PDP11_PID=$!
echo "pdp11 PID: ${PDP11_PID}"
sleep 2

# Send command to pdp11
(
sleep 2
echo "MACRO ${NAME}"
sleep 0.5
echo "LINK ${NAME}/MAP"
sleep 0.5
echo "DIR ${NAME}.*"
sleep 0.5
echo "D 1000=0"
sleep 0.1
echo "START 1000"
sleep 0.1
echo "exit"
sleep 0.1
) | nc localhost ${CONSOLE_PORT}

# Copy the output files
rm -rf out
mkdir -p out
../rt11.py -v \
    -c "mount vol: ${WORK_DISK}" \
    -c "copy vol:*.* out"
