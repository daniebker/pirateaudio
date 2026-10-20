#!/bin/sh
# Present the Pi Zero to a USB host as an MTP device (a "media player" drive).
# Run as root by pirateplayer-mtp.service; umtprd serves the folders listed in /etc/umtprd/umtprd.conf.
# umtprd itself runs as $MTP_USER, so the files it writes belong to that user: Debian's umtprd
# is too old to understand default_uid/default_gid, and as root it makes everything root-only.
set -eu

MTP_USER=${MTP_USER:?set MTP_USER to the user who owns the shared folders}
MTP_UID=$(id -u "$MTP_USER")
MTP_GID=$(id -g "$MTP_USER")

G=/sys/kernel/config/usb_gadget/pirateplayer
FFS=/dev/ffs-mtp

modprobe libcomposite
mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config

# Clean up whatever a previous run left behind, so restarting the service works.
if [ -d "$G" ]; then
    echo "" > "$G/UDC" 2>/dev/null || true
fi
mountpoint -q "$FFS" && umount "$FFS"

if [ ! -d "$G" ]; then
    mkdir -p "$G"
    echo 0x1d6b > "$G/idVendor"   # Linux Foundation
    echo 0x0100 > "$G/idProduct"
    echo 0x0100 > "$G/bcdDevice"
    echo 0x0200 > "$G/bcdUSB"

    mkdir -p "$G/strings/0x409"
    serial=$(awk '/^Serial/ {print $3}' /proc/cpuinfo)
    echo "${serial:-0123456789}" > "$G/strings/0x409/serialnumber"
    echo "Raspberry Pi"          > "$G/strings/0x409/manufacturer"
    echo "Pirate Audio Player"   > "$G/strings/0x409/product"

    mkdir -p "$G/configs/c.1/strings/0x409"
    echo "MTP" > "$G/configs/c.1/strings/0x409/configuration"
    echo 250   > "$G/configs/c.1/MaxPower"

    mkdir -p "$G/functions/ffs.mtp"
    ln -s "$G/functions/ffs.mtp" "$G/configs/c.1/"
fi

mkdir -p "$FFS"
mount -t functionfs -o uid="$MTP_UID",gid="$MTP_GID" mtp "$FFS"

# umtprd writes the USB descriptors to ep0; the other endpoints appear once it has.
setpriv --reuid="$MTP_UID" --regid="$MTP_GID" --init-groups umtprd &
UMTPRD=$!
trap 'kill $UMTPRD 2>/dev/null; echo "" > "$G/UDC" 2>/dev/null' TERM INT

i=0
until [ -e "$FFS/ep1" ]; do
    i=$((i + 1))
    if [ $i -gt 300 ] || ! kill -0 $UMTPRD 2>/dev/null; then
        echo "umtprd did not start" >&2
        exit 1
    fi
    sleep 0.1
done

# Binding to the USB controller is what makes the device appear on the host.
UDC_NAME=$(ls /sys/class/udc | head -n1)
if [ -z "$UDC_NAME" ]; then
    echo "No USB device controller: is dtoverlay=dwc2 in config.txt, and has the Pi rebooted?" >&2
    exit 1
fi
echo "$UDC_NAME" > "$G/UDC"

wait $UMTPRD
