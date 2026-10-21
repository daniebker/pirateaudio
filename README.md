# pirateplayer

A music player for a Raspberry Pi Zero with a Pimoroni Pirate Audio HAT and a UPS battery board.
It plays MP3, FLAC and WAV files from `~/Music`, organised in folders however you like.

- Uses `mpv` for playback, with video turned off so it stays light enough for a Pi Zero
- A menu, a folder browser, an iPod-style Cover Flow of your albums, and a now-playing screen with cover art (embedded art, or `cover.jpg`/`folder.jpg` in the album folder). Tags are read from ID3 (MP3), Vorbis comments (FLAC), and ID3 or RIFF INFO chunks (WAV)
- Remembers the playlist, track, position and volume across reboots, and resumes paused
- Turns the screen off after 30s idle to save battery
- Shows battery level, and powers off cleanly when the battery is nearly empty
- Plug it into a computer and `~/Music` shows up as a drive (MTP), so you can copy music on without the network
- Plays through Bluetooth headphones or speakers: scan, pair and connect from the player itself
- Counts how often you play each track, and builds smart playlists from that (most played, not heard for a while, recently added…), described by text files in `~/playlists`

**Contents:** [Hardware](#hardware) · [Installation](#installation) · [Adding music](#adding-music) · [Buttons](#buttons) · [Smart playlists](#smart-playlists) · [Bluetooth headphones](#bluetooth-headphones) · [Battery](#battery) · [Configuration](#configuration) · [Updating](#updating) · [Troubleshooting](#troubleshooting) · [Desktop simulator](#desktop-simulator) · [Tests](#tests) · [License](#license)

## Hardware

### What you need

| Part | Notes |
|---|---|
| **Raspberry Pi Zero W** or **Zero 2 W** | The W models have the Wi-Fi the installer needs and the Bluetooth for headphones. Buy a "WH" (header already fitted) unless you're happy to solder a 40-pin header on yourself. Developed and tested on a Zero W. |
| **Pimoroni Pirate Audio** | Any of the playback models: *Speaker* (built-in speaker), *Headphone Amp* or *Line-out* (3.5 mm jack), or *3W Stereo Amp* (screw terminals for passive speakers). They share the same screen, buttons and DAC. The *Dual Mic* model has no audio output and won't work. |
| **microSD card** | 8 GB is enough for the system; your music goes on the same card, so size it for your library. |
| **Power** | A 5 V micro-USB supply (2.5 A, like the official Pi supply), or a battery board below. |
| **Battery board** (optional) | **Waveshare UPS HAT (C)** or **PiSugar 2 / 3** for Pi Zero. Both clip under the Pi and are detected automatically. Without one, the player runs on mains power and simply doesn't show a battery gauge. |
| **Micro-USB data cable** (optional) | For copying music from a computer over USB. Many micro-USB cables only charge; you need one that carries data. |
| **Bluetooth headphones or speaker** (optional) | Any that support A2DP, which is nearly all of them. |

You also need a computer with an SD card reader to set up the card, on the same Wi-Fi network as the Pi.

### Assembly

1. If your Pi has no header, solder a 40-pin male header on, pins pointing up (the side with the chips).
2. If you have a battery board, fit it **under** the Pi. Its spring pins press against the underside of the GPIO header's solder joints, so they need the header fitted. Follow the board's own instructions for lining it up and connecting the battery.
3. Push the Pirate Audio onto the header **on top**, with its screen over the Pi.
4. The microSD card goes in the slot on the end of the Pi. You may want to do that after [flashing it](#1-flash-the-sd-card).

The Pi Zero has two micro-USB ports. **PWR** (at the corner) is power only. **USB** (nearer the middle) carries data and also powers the Pi; it's the one to plug into a computer for [copying music over USB](#copying-music-over-usb). With a battery board, charge through the board's own charging port.

### Pins used

You only need this if you're adding other hardware.

| What | GPIO |
|---|---|
| Screen (ST7789, SPI0) | SPI0 with chip select 1, data/command on GPIO 9, backlight on GPIO 13 |
| Buttons A, B, X, Y | 5, 6, 16, 24 (Y is 20 on early Pirate Audio boards) |
| DAC (I2S) | 18, 19, 21 |
| Amplifier enable (Speaker and 3W models) | 25, driven high at boot |
| Battery board (I2C) | 2, 3. The Waveshare UPS's INA219 is at address 0x43 |

## Installation

### 1. Flash the SD card

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer and start it.
2. **Device:** pick *Raspberry Pi Zero* for a Zero W, or *Raspberry Pi Zero 2 W*.
3. **Operating system:** *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite**. Use the 32-bit version on a Zero W, which can't run 64-bit. A Zero 2 W can use either. The player is tested on Raspberry Pi OS Lite based on Debian 13 "trixie"; Debian 12 "bookworm" should work too.
4. **Storage:** your microSD card.
5. When asked whether to customise the OS settings, choose to edit them and set:
   - **Hostname:** `pirateaudio`. The update script expects this name; you can use another and [tell it](#updating).
   - **Username and password:** whatever you like. The player runs as this user and plays from its `~/Music`.
   - **Wi-Fi:** your network name and password, and your **Wi-Fi country**. The Wi-Fi stays switched off until the country is set.
   - **Locale:** your time zone and keyboard layout.
   - **Services:** enable **SSH**, with password authentication or your public key.
6. Write the card, put it in the Pi, and plug in power.

The first boot takes a few minutes on a Pi Zero while it resizes the card and applies your settings. The Pirate Audio screen stays dark until the player is installed.

### 2. Connect over SSH

From your computer:

```sh
ssh <username>@pirateaudio.local
```

If `pirateaudio.local` isn't found, look up the Pi's IP address in your router's list of devices and use that instead. Type `exit` to leave.

Optionally, copy your SSH key to the Pi so the update script doesn't ask for your login password at every step:

```sh
ssh-copy-id <username>@pirateaudio.local
```

### 3. Install the player

**Option A: from your computer (recommended).** You'll need `git`, `rsync` and `ssh`. Linux and macOS have them; on Windows, use WSL.

```sh
git clone https://github.com/daniebker/pirateaudio.git
cd pirateaudio
PI_HOST=<username>@pirateaudio.local scripts/deploy.sh --install
```

This copies the project to `~/pirateplayer` on the Pi, runs the installer there (it asks for your password on the Pi, for `sudo`), and reboots. If your username is the same on both machines, you can leave out `PI_HOST=...`.

**Option B: on the Pi.** Over SSH:

```sh
sudo apt install -y git
git clone https://github.com/daniebker/pirateaudio.git ~/pirateplayer
cd ~/pirateplayer
./install.sh
sudo reboot
```

Either way, installing can take a while on a Pi Zero W, tens of minutes, mostly downloading and setting up packages. Re-running the installer is safe: it only adds what's missing and rewrites its own files.

### 4. What the installer changes

`install.sh` runs as your user and uses `sudo` for system changes:

- **Packages:** `mpv`, `umtp-responder`, `bluez`, `bluez-alsa-utils`, `libasound2-plugin-bluez`, `rfkill`, the Python libraries (Pillow, mutagen, NumPy, gpiozero with lgpio, spidev, smbus2, dbus-python, PyGObject) and the DejaVu fonts. It makes a virtualenv in the project folder, `.venv`, for the `st7789` screen driver.
- **`/boot/firmware/config.txt`:**
  - turns on SPI (`dtparam=spi=on`, `dtoverlay=spi0-2cs,no_miso`) for the screen;
  - turns on I2C (`dtparam=i2c_arm=on`) for the battery board;
  - adds `dtoverlay=hifiberry-dac` for the Pirate Audio's DAC, and `gpio=25=op,dh` to switch its amplifier on;
  - adds `dtoverlay=dwc2` for USB gadget mode;
  - turns off the onboard and HDMI audio, so the DAC is the only sound card.
- **Kernel modules** (`/etc/modules-load.d/pirateplayer.conf`): `i2c-dev`, `dwc2`, `libcomposite`.
- **Smart playlists:** creates `~/playlists` with some example playlists, if it doesn't exist yet, and keeps its `README.txt` up to date.
- **Your user** joins the `gpio`, `spi`, `i2c` and `audio` groups, and may run `poweroff` without a password (`/etc/sudoers.d/pirateplayer`) for the low-battery shutdown.
- **USB music drive:** writes `/etc/umtprd/umtprd.conf`, sharing `~/Music` and `~/playlists`, and installs the `pirateplayer-mtp` service that sets up the USB gadget.
- **Bluetooth:** unblocks the radio (Raspberry Pi OS ships it blocked), and configures `bluealsa` to only send audio (`a2dp-source`), so the Pi doesn't offer itself as a speaker to phones.
- **Services:** installs and enables `pirateplayer` (the player), `pirateplayer-mtp`, `bluetooth` and `bluealsa`.

### 5. Check it's working

After the reboot the screen shows *Starting…* for ten seconds or so while mpv starts, then the menu. **Files** says *No music here* until you add some.

Useful commands on the Pi:

```sh
systemctl status pirateplayer       # is it running?
journalctl -u pirateplayer -f       # its log, as it happens
sudo systemctl restart pirateplayer # restart it, e.g. after changing the config
```

## Adding music

Music lives in `~/Music` on the Pi, in whatever folders you like, for example `Artist/Album/01 Track.flac`. The player reads MP3, FLAC and WAV, and shows cover art embedded in the files or from a `cover.jpg` or `folder.jpg` in the album folder.

### Copying music over USB

Connect the Pi's micro-USB port labelled **USB** (the one nearer the middle, not **PWR**) to a computer. That port powers the Pi too, so one cable is enough, but it must be a data cable: many micro-USB cables only charge.

The Pi appears as a media player called "Pirate Audio Player" with two drives: `Music`, whose contents are `~/Music`, and `Playlists`, which is `~/playlists` (see [Smart playlists](#smart-playlists)). Windows and most Linux desktops open it like a phone; macOS has no built-in MTP support, so use an app such as [OpenMTP](https://openmtp.ganeshrvel.com/). Files you copy on belong to your user on the Pi.

- The player sees new files as soon as you browse to their folder. Smart playlists only see them after the library has been synced: at startup, or from **Settings → Sync library**.
- Music added on the Pi while it's plugged in won't show on the computer until you reconnect: the Pi doesn't send change notifications, because the umtprd version in Debian hangs when it does.
- Gadget mode means the USB port can't be used for a keyboard or other USB device.
- Edit smart playlists on the `Playlists` drive like any text file. If your computer won't open a `.txt` file in place, copy it off, edit it, and copy it back. The player picks up changes the next time you open **Playlists**.
- If you delete `~/playlists`, it comes back empty the next time the drive starts. To get the examples back, copy them from `~/pirateplayer/playlists`.
- To turn the drive off: `sudo systemctl disable --now pirateplayer-mtp`.

### Copying music over the network

With `rsync` from your computer (this copies your local `~/Music` into the Pi's, adding to what's there):

```sh
rsync -av --progress ~/Music/ <username>@pirateaudio.local:Music/
```

`scp -r` works too. Copying over Wi-Fi is slow on a Pi Zero; USB is usually faster for a big library.

## Buttons

Pirate Audio buttons: **A** top-left, **B** bottom-left, **X** top-right, **Y** bottom-right.

The player starts at the **menu**, or at the now-playing screen if it's resuming a playlist. The menu has these rows:

- **Files**: the folders and tracks in `~/Music`.
- **Cover Flow**: your albums' covers flowing across the screen like on an iPod classic.
- **Playlists**: your [smart playlists](#smart-playlists).
- **Bluetooth**: for [headphones](#bluetooth-headphones). Only there if the Pi has Bluetooth.
- **Settings**: **Sync library** re-reads your music folder. It shows how many tracks the library has. Syncing pauses the music and locks the buttons until it's done (see [Smart playlists](#smart-playlists)). **Gapless playback** (off at first; **X** turns it on or off) plays each track straight after the last with no pause between them, for live albums and DJ mixes. The player remembers the setting.

| | Menu | Files | Now playing |
|---|---|---|---|
| **A** | Up | Up (hold to scroll) | Volume up |
| **B** | Down | Down (hold to scroll) | Volume down |
| **X** | Open | Open folder / play the folder starting from this track | Play / pause |
| **X** hold | Open | Play the whole folder, including subfolders | Next track |
| **Y** | Go to now playing | Parent folder (at the top: back to the menu) | Back to Files, with the current track selected |
| **Y** hold | Go to now playing | Go to now playing | Previous track (or restart the current one if you're more than 3s in) |
| **A + B** hold 3s | Power off | Power off | Power off |

| | Cover Flow |
|---|---|
| **A** / **B** | Previous / next album (hold to scroll) |
| **X** | Play the album |
| **X** hold | Open the album in Files, to pick a track |
| **Y** | Back to the menu |
| **Y** hold | Go to now playing |

Cover Flow shows every folder of tracks that has a cover (a `cover.jpg` or the like in the folder, or art embedded in its first track), in alphabetical order of album name. The name and artist come from the first track's tags, or the folder's name if it has none. The player looks through `~/Music` for albums in the background as soon as it has [synced the library](#smart-playlists) at startup, so they're usually ready by the time you open Cover Flow; on a big library, it says *Finding albums…* until they are. Each time you open it, it shows the albums it found last time while it checks again for changes. The first time, it also makes a small copy of each cover to flow through quickly; until it has, covers it hasn't reached yet can make the flow stutter.

"Go to now playing" does nothing until you've played something. When the screen is off, the first press only wakes it up.

Powering off with A + B shuts the Pi down cleanly. Wait for the green light on the Pi to stop flashing before removing power. To switch back on, remove and reconnect power (on a battery board, use its power switch).

## Smart playlists

The player keeps an index of your music, with its tags, in a small database (`~/.local/state/pirateplayer/library.db`). It brings the index up to date every time it starts: the first time, on a Pi Zero W, that can take a few minutes for a big library, but later syncs only re-read files that changed. Syncing slows everything else down, so while it runs the screen shows a progress bar and the buttons are locked (holding **A**+**B** still powers off). To sync without restarting, for example after copying music over, open **Settings** and press **X** on **Sync library**.

It also counts how often you play each track. A track counts as played once it has played for half its length or four minutes, whichever comes first (the rule Last.fm uses), or when it plays to the end. Pausing doesn't count towards that; skipping back to the start of a track and listening again is another play. Play counts are kept by file path, so moving or renaming a file starts its count again.

A smart playlist is a text file in `~/playlists` describing which tracks to pick, and it's re-evaluated each time you play it. The installer puts some examples there: *Most played*, *On repeat* (most played this month), *Recently added*, *Forgotten favourites* and *Never played*. Edit them, or add your own, over USB (the `Playlists` drive), over the network, or on the Pi with `nano`. Changes show up the next time you open **Playlists**; there's no need to restart.

```
# ~/playlists/Jazz I haven't heard in a while.txt
genre: jazz
not played in: 6 months
sort: random
limit: 50
```

| Setting | |
|---|---|
| `sort` | `most played`, `least played`, `recently played`, `least recently played`, `recently added`, `random`, `album` (artist, album, track: the default) or `title` |
| `limit` | At most this many tracks |
| `period` | Only count plays this recent towards `min plays`, `max plays` and the most/least played order, e.g. `30 days`. Without it, every play counts |
| `min plays` / `max plays` | Only tracks played at least / at most this many times. `max plays: 0` is tracks you've never played |
| `played in` | Only tracks last played within this long, e.g. `1 week` |
| `not played in` | Only tracks not played for this long, or never |
| `added in` | Only tracks added to the player within this long |
| `artist`, `album`, `genre` | Only tracks whose tag contains this text, ignoring case |
| `folder` | Only tracks in this folder of `~/Music`, e.g. `Jazz/Miles Davis` |
| `name` | The name to show, instead of the file name |

Times are a number and `hours`, `days`, `weeks`, `months` or `years`; a bare number is days. `artist`, `album`, `genre` and `folder` can be given more than once, and a track matches if it matches any of them; different settings must all match. Lines starting with `#` are comments. `~/playlists/README.txt` has the same reference.

| | Playlists |
|---|---|
| **A** / **B** | Up / down |
| **X** | Play the playlist |
| **Y** | Back to the menu |
| **Y** hold | Go to now playing |

If a line of a playlist doesn't make sense, the player skips it and marks the playlist *Has errors*; the log says what's wrong (`journalctl -u pirateplayer | grep Playlist`).

## Bluetooth headphones

Pi Zero W and Zero 2 W boards have Bluetooth. Open **Bluetooth** from the menu: it starts a 30-second scan for headphones and speakers. Put yours in pairing mode and it appears in the list.

| | Bluetooth screen |
|---|---|
| **A** / **B** | Up / down |
| **X** on *Scan for headsets* | Start or stop scanning |
| **X** on a device | Pair and connect, or disconnect if it's connected |
| **X** hold on a paired device | Forget it |
| **Y** | Back to the menu |

- Once a headset connects, music plays through it, a blue Bluetooth sign shows in the header, and the menu shows the headset's name. When it disconnects or goes out of range, playback pauses, and pressing play carries on through the Pirate Audio.
- At startup the player reconnects to the last headset it played to. Most headsets also connect back by themselves when switched on.
- One headset at a time: connecting another disconnects the first.
- Headphones and the Pirate Audio each keep their own volume, so switching between them doesn't blast your ears or leave the speaker too quiet to hear.
- The Pi Zero W shares one antenna between Wi-Fi and Bluetooth, so heavy Wi-Fi use (copying music over the network, say) can make Bluetooth audio stutter.
- To hide Bluetooth, set `bluetooth = false` in the [configuration](#configuration).

## Battery

`battery = "auto"` detects either of these:

- **Waveshare UPS HAT (C)** for Pi Zero: read directly over I2C (INA219 at 0x43). Nothing to install.
- **PiSugar 2/3**: read through `pisugar-server`. Install it with PiSugar's own script from the [PiSugar power manager](https://github.com/PiSugar/pisugar-power-manager-rs) instructions, then restart the player.

The battery gauge sits in the top-right corner, and turns green while charging. When the battery drops to 5% (configurable) and isn't charging, the player saves its place, shows *Battery low*, and powers the Pi off cleanly.

Battery percentage comes from voltage, so treat it as a rough guide. To use a different board, add a class with a `read()` method returning `BatteryStatus` in `pirateplayer/battery.py`.

## Configuration

Settings are optional. To change any, copy the example and uncomment what you want:

```sh
mkdir -p ~/.config/pirateplayer
cp ~/pirateplayer/config.example.toml ~/.config/pirateplayer/config.toml
nano ~/.config/pirateplayer/config.toml
sudo systemctl restart pirateplayer
```

| Setting | Default | |
|---|---|---|
| `music_dir` | `"~/Music"` | Where your music is |
| `volume` | `50` | Starting volume (percent) when there's no saved state |
| `bluetooth_volume` | `50` | The same for Bluetooth headphones, which keep their own volume |
| `cache_dir` | `"~/.cache/pirateplayer"` | Where Cover Flow keeps small copies of album covers, so it doesn't have to decode the full-size ones as you flow through them. Safe to delete; they're made again |
| `backlight_timeout` | `30` | Seconds without a button press before the screen turns off; `0` keeps it on (uses more battery) |
| `rotation` | `90` | Screen rotation in degrees; 90 suits the Pirate Audio |
| `y_pin` | `24` | GPIO for button Y; set `20` for early Pirate Audio boards |
| `battery` | `"auto"` | `auto`, `waveshare`, `pisugar` or `none` |
| `low_battery_percent` | `5` | Power off below this when not charging |
| `audio_device` | `""` | mpv `--audio-device`, e.g. `"alsa/hw:0"`; empty uses the default (the Pirate Audio). See `mpv --audio-device=help` |
| `bluetooth` | `true` | `false` hides the Bluetooth menu |
| `playlists_dir` | `"~/playlists"` | Where your [smart playlists](#smart-playlists) are |
| `database` | `"~/.local/state/pirateplayer/library.db"` | The library index and play counts. Delete it to start the counts again |

## Updating

From your clone of the project on your computer:

```sh
git pull
PI_HOST=<username>@pirateaudio.local scripts/deploy.sh
```

`deploy.sh` copies the project to `~/pirateplayer` on the Pi and restarts the player if its code changed. Run `scripts/deploy.sh --install` instead when `install.sh` has changed (`git pull` lists the files it updated): it also re-runs the installer and reboots. Both may ask for your password on the Pi, for `sudo`. Without `PI_HOST` the script uses `pirateaudio.local` and your local username.

## Troubleshooting

Start with the log: `journalctl -u pirateplayer -b` shows everything the player has logged since boot.

| Problem | What to check |
|---|---|
| Screen stays dark | Did you reboot after installing? `ls /dev/spidev0.*` should list two devices. Check the log for errors from the screen driver. |
| Stuck on *Starting…* | mpv can take 10–20 seconds to start on a Zero W. If it never gets further, the log says why. |
| No sound | `aplay -l` should list a `sndrpihifiberry` card; if not, check the `config.txt` lines in [What the installer changes](#4-what-the-installer-changes) and reboot. Check the volume on the now-playing screen. Speaker and 3W models need `gpio=25=op,dh` in `config.txt` to switch their amplifier on. |
| Button Y does nothing | You have an early Pirate Audio board: set `y_pin = 20`. |
| No battery gauge | Waveshare: `sudo apt install i2c-tools`, then `i2cdetect -y 1` should show `43`. PiSugar: `systemctl status pisugar-server` should be running. Or set `battery` explicitly to see a warning in the log. |
| The USB drive doesn't appear | Use the port labelled **USB** and a data cable. `ls /sys/class/udc` should list a controller; if it's empty, reboot after running the installer. `journalctl -u pirateplayer-mtp` shows errors. |
| A playlist plays nothing | Check the playlist's settings: `min plays` and `period` leave out tracks you haven't played enough. |
| The **Playlists** row is missing | The library database couldn't be opened; the log says why. **Settings** shows *Unavailable*. |
| No **Bluetooth** row in the menu | The log says why at startup (`Bluetooth unavailable: …`). Check `systemctl status bluetooth`. If the radio is blocked: `sudo rfkill unblock bluetooth`. |
| A headset won't pair | Put it in pairing mode (usually holding the power button until a light flashes), keep it close, and scan again. If it was paired before, forget it on the player (hold **X**) and on the headset, then pair again. |
| A headset connects but there's no sound | `systemctl status bluealsa` should be running. Reconnect the headset after restarting it: `sudo systemctl restart bluealsa`. |
| `pirateaudio.local` not found | Use the Pi's IP address from your router. Check that the computer and the Pi are on the same network. |

## Desktop simulator

You can run the player on a desktop computer. A window shows the board with its screen and four buttons. The screen, buttons, battery, Bluetooth and audio are simulated; everything else is the same code that runs on the Pi.

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
| `H` | Switch off the connected Bluetooth headset |

Options:
- `--music DIR` plays from a different folder than `~/Music`.
- `--scale 3` makes the screen bigger.
- `--mpv` plays through mpv like the Pi does, instead of pygame. Use it to test the Pi's playback code.

Each button's label shows what it does on the current screen. Saved state goes to the same `~/.local/state/pirateplayer/` file as on the Pi, so the simulator resumes where you left off too.

## Tests

The tests cover everything except the hardware drivers, mpv, the D-Bus side of Bluetooth and the simulator window, so they run on any computer:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

GitHub Actions runs them on every push and pull request.

## License

[MIT](LICENSE)
