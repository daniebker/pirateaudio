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
sudo apt-get install -y --no-install-recommends mpv
sudo apt-get install -y python3-venv python3-pil python3-mutagen python3-numpy \
    python3-gpiozero python3-lgpio python3-libgpiod python3-spidev python3-smbus2 fonts-dejavu-core

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
            dtoverlay=hifiberry-dac gpio=25=op,dh; do
    grep -qxF "$line" "$CONFIG_TXT" || echo "$line" | sudo tee -a "$CONFIG_TXT" >/dev/null
done

# dtparam=i2c_arm enables the I2C bus, but /dev/i2c-1 (for reading the UPS) needs i2c-dev.
echo i2c-dev | sudo tee /etc/modules-load.d/pirateplayer.conf >/dev/null
sudo modprobe i2c-dev

echo "==> Permissions"
sudo usermod -aG gpio,spi,i2c,audio "$RUN_USER"
# Lets the player power the Pi off (button combo or low battery) without a password.
echo "$RUN_USER ALL=(root) NOPASSWD: /usr/sbin/poweroff" | sudo tee /etc/sudoers.d/pirateplayer >/dev/null
sudo chmod 440 /etc/sudoers.d/pirateplayer
sudo visudo -cf /etc/sudoers.d/pirateplayer

mkdir -p "$HOME/Music"

echo "==> Installing systemd service"
sudo tee /etc/systemd/system/pirateplayer.service >/dev/null <<EOF
[Unit]
Description=Pirate Audio MP3 player
After=local-fs.target sound.target

[Service]
User=$RUN_USER
WorkingDirectory=$HERE
ExecStart=$HERE/.venv/bin/python -m pirateplayer
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable pirateplayer.service

echo
echo "Done. Reboot to apply the hardware settings: sudo reboot"
echo "Logs: journalctl -u pirateplayer -f"
