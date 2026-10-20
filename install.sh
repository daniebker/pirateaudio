#!/usr/bin/env bash
# Set up the Pirate Audio MP3 player on Raspberry Pi OS (Bookworm or later).
# Run as your normal user from the project directory: ./install.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
RUN_USER=$(id -un)
CONFIG_TXT=/boot/firmware/config.txt
[ -f "$CONFIG_TXT" ] || CONFIG_TXT=/boot/config.txt

echo "==> Installing packages"
sudo apt-get update
sudo apt-get install -y --no-install-recommends mpv umtp-responder \
    bluez bluez-alsa-utils libasound2-plugin-bluez rfkill
sudo apt-get install -y python3-venv python3-pil python3-mutagen python3-numpy \
    python3-gpiozero python3-lgpio python3-libgpiod python3-spidev python3-smbus2 fonts-dejavu-core \
    python3-dbus python3-gi

echo "==> Creating virtualenv"
python3 -m venv --system-site-packages "$HERE/.venv"
"$HERE/.venv/bin/pip" install --upgrade st7789

echo "==> Configuring $CONFIG_TXT"
# Under KMS the HDMI port is a sound card too, and can end up as the default instead of the DAC.
sudo sed -i 's/^dtoverlay=vc4-kms-v3d$/dtoverlay=vc4-kms-v3d,noaudio/' "$CONFIG_TXT"
# The Pirate Audio DAC replaces the onboard audio.
sudo sed -i 's/^dtparam=audio=on/#dtparam=audio=on/' "$CONFIG_TXT"
if ! grep -q "^# pirateplayer" "$CONFIG_TXT"; then
    printf '\n# pirateplayer: Pirate Audio HAT + UPS\n[all]\n' | sudo tee -a "$CONFIG_TXT" >/dev/null
fi
# Each line is added only if missing, so re-running picks up lines added in later versions.
# The display's data/command line is GPIO 9 (SPI MISO), so SPI must leave that pin unclaimed.
for line in dtparam=spi=on dtoverlay=spi0-2cs,no_miso dtparam=i2c_arm=on \
            dtoverlay=hifiberry-dac gpio=25=op,dh dtoverlay=dwc2; do
    grep -qxF "$line" "$CONFIG_TXT" || echo "$line" | sudo tee -a "$CONFIG_TXT" >/dev/null
done

# dtparam=i2c_arm enables the I2C bus, but /dev/i2c-1 (for reading the UPS) needs i2c-dev.
# dwc2 and libcomposite let the Pi act as a USB device, for the MTP music drive.
printf 'i2c-dev\ndwc2\nlibcomposite\n' | sudo tee /etc/modules-load.d/pirateplayer.conf >/dev/null
sudo modprobe i2c-dev

echo "==> Permissions"
sudo usermod -aG gpio,spi,i2c,audio "$RUN_USER"
# Lets the player power the Pi off (button combo or low battery) without a password.
echo "$RUN_USER ALL=(root) NOPASSWD: /usr/sbin/poweroff" | sudo tee /etc/sudoers.d/pirateplayer >/dev/null
sudo chmod 440 /etc/sudoers.d/pirateplayer
sudo visudo -cf /etc/sudoers.d/pirateplayer

mkdir -p "$HOME/Music"

echo "==> Smart playlists"
# Example playlists only go into a new folder, so ones you've deleted don't come back.
if [ ! -d "$HOME/playlists" ]; then
    mkdir -p "$HOME/playlists"
    cp "$HERE"/playlists/*.txt "$HOME/playlists/"
fi
cp "$HERE/playlists/README.txt" "$HOME/playlists/README.txt"

echo "==> Configuring Bluetooth headsets"
# Raspberry Pi OS starts with the radio blocked; systemd-rfkill remembers the unblock.
sudo rfkill unblock bluetooth
# bluez-alsa turns a connected headset into an ALSA device for mpv. The Pi only sends audio
# (a2dp-source); by default it would also offer itself as a speaker for phones.
sudo mkdir -p /etc/systemd/system/bluealsa.service.d
sudo tee /etc/systemd/system/bluealsa.service.d/pirateplayer.conf >/dev/null <<EOF
[Service]
ExecStart=
ExecStart=/usr/bin/bluealsa -S -p a2dp-source
EOF
# bluealsa-aplay plays audio that phones send, which we don't accept.
sudo systemctl disable --now bluealsa-aplay.service 2>/dev/null || true

echo "==> Configuring the USB music drive (MTP)"
# Plugged into a computer through the USB data port, the Pi shows up as a media player
# whose only drive is ~/Music. Files copied onto it belong to $RUN_USER.
UMTPRD_CONF=/etc/umtprd/umtprd.conf
sudo mkdir -p /etc/umtprd
if [ -f "$UMTPRD_CONF" ] && ! grep -q "^# pirateplayer" "$UMTPRD_CONF"; then
    sudo cp "$UMTPRD_CONF" "$UMTPRD_CONF.orig"
fi
SERIAL=$(awk '/^Serial/ {print $3}' /proc/cpuinfo)
sudo tee "$UMTPRD_CONF" >/dev/null <<EOF
# pirateplayer: written by install.sh; re-running it overwrites this file.
loop_on_disconnect 1
umask 022
# With inotify, Debian's umtprd (1.3.10) sends change events while holding a lock; hosts using
# libmtp (Dolphin, Nautilus) never read them, so it hangs mid-copy. Fixed upstream in 1.6.8.
no_inotify 0x1

storage "$HOME/Music" "Music" "rw"
storage "$HOME/playlists" "Playlists" "rw"

manufacturer "Raspberry Pi"
product "Pirate Audio Player"
serial "${SERIAL:-0123456789}"
firmware_version "1.0"
interface "MTP"

usb_vendor_id 0x1D6B
usb_product_id 0x0100
usb_class 0x6
usb_subclass 0x1
usb_protocol 0x1
usb_dev_version 0x3008

usb_functionfs_mode 0x1
usb_dev_path   "/dev/ffs-mtp/ep0"
usb_epin_path  "/dev/ffs-mtp/ep1"
usb_epout_path "/dev/ffs-mtp/ep2"
usb_epint_path "/dev/ffs-mtp/ep3"
usb_max_packet_size 0x200
EOF
sudo install -m 755 "$HERE/scripts/mtp-gadget.sh" /usr/local/sbin/pirateplayer-mtp-gadget
# Earlier versions ran umtprd as root, leaving root-only folders the player can't read.
sudo chown -R "$RUN_USER:" "$HOME/Music" "$HOME/playlists"

echo "==> Installing systemd service"
sudo tee /etc/systemd/system/pirateplayer.service >/dev/null <<EOF
[Unit]
Description=Pirate Audio MP3 player
After=local-fs.target sound.target bluetooth.service bluealsa.service

[Service]
User=$RUN_USER
WorkingDirectory=$HERE
ExecStart=$HERE/.venv/bin/python -m pirateplayer
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/pirateplayer-mtp.service >/dev/null <<EOF
[Unit]
Description=Pirate Audio player USB music drive (MTP)
After=local-fs.target sys-kernel-config.mount

[Service]
Environment=MTP_USER=$RUN_USER
ExecStart=/usr/local/sbin/pirateplayer-mtp-gadget
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable pirateplayer.service pirateplayer-mtp.service bluetooth.service \
    bluealsa.service

echo
echo "Done. Reboot to apply the hardware settings: sudo reboot"
echo "Logs: journalctl -u pirateplayer -f   (USB drive: journalctl -u pirateplayer-mtp)"
