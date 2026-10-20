# pirateplayer

A music player for a Raspberry Pi Zero with a Pimoroni Pirate Audio HAT and a UPS battery board.
It plays MP3, FLAC and WAV files from `~/Music`, organised in folders however you like.

- Uses `mpv` for playback, with video turned off so it stays light enough for a Pi Zero
- Folder browser and a now-playing screen with cover art (embedded art, or `cover.jpg`/`folder.jpg` in the album folder). Tags are read from ID3 (MP3), Vorbis comments (FLAC), and ID3 or RIFF INFO chunks (WAV)
- Remembers the playlist, track, position and volume across reboots, and resumes paused
- Turns the screen off after 30s idle to save battery
- Shows battery level, and powers off cleanly when the battery is nearly empty
- Plug it into a computer and `~/Music` shows up as a drive (MTP), so you can copy music on without the network

## Buttons

Pirate Audio buttons: **A** top-left, **B** bottom-left, **X** top-right, **Y** bottom-right.

| | Browser | Now playing |
|---|---|---|
| **A** | Up (hold to scroll) | Volume up |
| **B** | Down (hold to scroll) | Volume down |
| **X** | Open folder / play the folder starting from this track | Play / pause |
| **X** hold | Play the whole folder, including subfolders | Next track |
| **Y** | Parent folder (at the top level: go to now playing) | Back to the browser, with the current track selected |
| **Y** hold | Go to now playing | Previous track (or restart the current one if you're more than 3s in) |
| **A + B** hold 3s | Power off | Power off |

When the screen is off, the first press only wakes it up.

## Install

On Raspberry Pi OS (Bookworm or later; Lite works fine), copy this folder to the Pi and run:

```sh
./install.sh
sudo reboot
```

The script:
- installs `mpv` and the Python libraries
- enables SPI, I2C and the Pirate Audio DAC in `config.txt` and turns off onboard audio
- lets your user run `poweroff` without a password, for the low-battery shutdown
- enables USB gadget mode and sets up `umtp-responder` to share `~/Music` over MTP
- installs and enables the `pirateplayer` and `pirateplayer-mtp` systemd services

To update the Pi from your computer, run `scripts/deploy.sh`. It copies the project to `~/pirateplayer` on `pirateaudio.local` (set `PI_HOST` to use another host) and restarts the player if its code changed. `scripts/deploy.sh --install` also re-runs `install.sh` and reboots. Both ask for your sudo password on the Pi.

Put your music in `~/Music`, over USB (below) or over the network with `scp` or `rsync`. Logs: `journalctl -u pirateplayer -f`.

## Copying music over USB

Connect the Pi's micro-USB port labelled **USB** (the one nearer the middle, not **PWR**) to a computer. That port powers the Pi too, so one cable is enough, but it must be a data cable: many micro-USB cables only charge.

The Pi appears as a media player called "Pirate Audio Player" with a single drive, `Music`, whose contents are `~/Music`. Windows and most Linux desktops open it like a phone; macOS has no built-in MTP support, so use an app such as [OpenMTP](https://openmtp.ganeshrvel.com/). Files you copy on belong to your user on the Pi.

- The player sees new files as soon as you browse to their folder.
- Music added on the Pi while it's plugged in won't show on the computer until you reconnect: the Pi doesn't send change notifications, because the umtprd version in Debian hangs when it does.
- Gadget mode means the USB port can't be used for a keyboard or other USB device.
- If nothing appears: `ls /sys/class/udc` should list a controller (if it's empty, reboot after running the installer), and `journalctl -u pirateplayer-mtp` shows errors. To turn the drive off: `sudo systemctl disable --now pirateplayer-mtp`.

## Battery

`battery = "auto"` detects either of these:

- **Waveshare UPS HAT (C)** for Pi Zero: read directly over I2C (INA219 at 0x43)
- **PiSugar 2/3**: read through `pisugar-server`, which you install separately with PiSugar's own script

Battery percentage comes from voltage, so treat it as a rough guide. To use a different board, add a class with a `read()` method returning `BatteryStatus` in `pirateplayer/battery.py`.

## Configuration

Copy `config.example.toml` to `~/.config/pirateplayer/config.toml` to change the music folder, the screen timeout, the battery type, and so on. If button Y doesn't work, you have an early board: set `y_pin = 20`.

## Desktop simulator

You can run the player on a desktop computer. A window shows the board with its screen and four buttons. The screen, buttons, battery and audio are simulated; everything else is the same code that runs on the Pi.

```sh
sudo apt install python3-pygame python3-pil python3-mutagen
python3 -m pirateplayer --sim
```

| Control | Action |
|---|---|
| Click a button, or `A`/`Up`, `B`/`Down`, `X`/`Enter`/`Right`, `Y`/`Esc`/`Left` | Press it. Hold the key or mouse button for a long press. |
| Hold `A` and `B` together for 3s | Power off (closes the window) |
| `C` | Connect or disconnect the charger |
| `-` / `=` | Lower or raise the battery level by 10% |

Options:
- `--music DIR` plays from a different folder than `~/Music`.
- `--scale 3` makes the screen bigger.
- `--mpv` plays through mpv like the Pi does, instead of pygame. Use it to test the Pi's playback code.

Each button's label shows what it does on the current screen. Saved state goes to the same `~/.local/state/pirateplayer/` file as on the Pi, so the simulator resumes where you left off too.

## Tests

The tests cover everything except the hardware drivers, mpv and the simulator window, so they run on any computer:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

GitHub Actions runs them on every push and pull request.
