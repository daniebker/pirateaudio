"""The main loop: buttons in, mpv commands out, screen redrawn when something changes."""

import json
import logging
import os
import subprocess
import time
from pathlib import Path

from . import buttons as btn
from .config import Config
from .library import TrackInfo, is_track, list_dir, read_info, tracks_in
from .player import PLAYING, STOPPED, Player
from .ui import Renderer

log = logging.getLogger(__name__)

TICK = 0.05
BATTERY_INTERVAL = 30.0
SAVE_INTERVAL = 30.0
LOW_BATTERY_READINGS = 3  # consecutive low readings before powering off
VOLUME_STEP = 5
RESTART_THRESHOLD = 3.0   # "previous" restarts the track if we're further in than this

BROWSER, NOW_PLAYING = "browser", "now_playing"


class App:
    def __init__(self, cfg: Config, display, buttons, battery, player: Player, simulate=False):
        self.cfg = cfg
        self.display = display
        self.buttons = buttons
        self.battery = battery
        self.player = player
        self.simulate = simulate
        self.ui = Renderer()

        self.screen = BROWSER
        self.root = cfg.music_dir
        self.cwd = self.root
        self.entries = list_dir(self.cwd)
        self.cursor = 0

        self.playlist: list[Path] = []
        self.index = 0
        self.info: TrackInfo | None = None
        self.failures = 0  # consecutive tracks that failed to play
        self.volume = cfg.volume

        self.battery_status = None
        self.low_battery_count = 0
        self.awake = True
        self.dirty = True
        self.running = True
        self.last_input = self.last_battery = self.last_save = self.last_poll = 0.0

    # ---- lifecycle -------------------------------------------------------

    def start(self):
        self.display.set_backlight(True)
        self.restore_state()
        self.last_input = time.monotonic()

    def run(self):
        self.start()
        try:
            while self.running:
                self.tick(time.monotonic())
                time.sleep(TICK)
        finally:
            self.save_state()

    def tick(self, now: float):
        for name, kind in self.buttons.poll():
            self.on_button(name, kind, now)

        while not self.player.events.empty():
            self.on_player_event(*self.player.events.get())

        if now - self.last_battery >= BATTERY_INTERVAL:
            self.last_battery = now
            self.check_battery()

        if self.player.state == PLAYING:
            if now - self.last_save >= SAVE_INTERVAL:
                self.last_save = now
                self.save_state()
            if self.awake and self.screen == NOW_PLAYING and now - self.last_poll >= 1.0:
                self.last_poll = now
                self.player.poll_position()
                self.dirty = True

        timeout = self.cfg.backlight_timeout
        if self.awake and timeout > 0 and now - self.last_input > timeout:
            self.awake = False
            self.display.set_backlight(False)

        if self.dirty and self.awake:
            self.dirty = False
            self.display.show(self.render())

    def render(self):
        if self.screen == BROWSER:
            title = "Music" if self.cwd == self.root else self.cwd.name
            return self.ui.browser(title, self.entries, self.cursor, self.battery_status,
                                   self.player.state == PLAYING)
        return self.ui.now_playing(self.info, self.player.state, self.player.position, self.volume,
                                   self.index, len(self.playlist), self.battery_status)

    # ---- input -------------------------------------------------------------

    def on_button(self, name: str, kind: str, now: float):
        self.last_input = now
        if not self.awake:
            # The press that wakes the screen doesn't also do something unseen.
            self.awake = True
            self.dirty = True
            self.display.set_backlight(True)
            return
        if name == btn.COMBO:
            self.power_off("Powering off…")
            return
        if self.screen == BROWSER:
            self.browser_button(name, kind)
        else:
            self.now_playing_button(name, kind)
        self.dirty = True

    def browser_button(self, name: str, kind: str):
        if name in ("A", "B") and self.entries:
            step = -1 if name == "A" else 1
            if kind == btn.TAP:
                self.cursor = (self.cursor + step) % len(self.entries)
            else:
                self.cursor = min(max(0, self.cursor + step), len(self.entries) - 1)
        elif name == "X" and kind != btn.REPEAT and self.entries:
            entry = self.entries[self.cursor]
            if entry.is_dir() and kind == btn.TAP:
                self.open_dir(entry)
            elif entry.is_dir():
                self.play(tracks_in(entry, recursive=True), 0)
            else:
                tracks = [p for p in self.entries if not p.is_dir()]
                self.play(tracks, tracks.index(entry))
        elif name == "Y" and kind == btn.TAP and self.cwd != self.root:
            self.open_dir(self.cwd.parent, select=self.cwd)
        elif name == "Y" and kind != btn.REPEAT and self.playlist:
            self.screen = NOW_PLAYING

    def now_playing_button(self, name: str, kind: str):
        if name in ("A", "B"):
            step = VOLUME_STEP if name == "A" else -VOLUME_STEP
            self.volume = min(100, max(0, self.volume + step))
            self.player.set_volume(self.volume)
        elif name == "X" and kind == btn.TAP:
            if self.player.state == STOPPED and self.playlist:
                self.start_track()
            else:
                self.player.toggle_pause()
                self.save_state()
        elif name == "X" and kind == btn.HOLD:
            self.skip(1)
        elif name == "Y" and kind == btn.TAP:
            self.screen = BROWSER
            if self.playlist:
                current = self.playlist[self.index]
                self.open_dir(current.parent, select=current)
        elif name == "Y" and kind == btn.HOLD:
            if self.player.position > RESTART_THRESHOLD:
                self.player.seek(0)
            else:
                self.skip(-1)

    def open_dir(self, directory: Path, select: Path | None = None):
        if not directory.is_relative_to(self.root):
            directory = self.root
        self.cwd = directory
        self.entries = list_dir(directory)
        self.cursor = self.entries.index(select) if select in self.entries else 0

    # ---- playback ----------------------------------------------------------

    def play(self, tracks: list[Path], index: int):
        if not tracks:
            return
        self.playlist = tracks
        self.index = index
        self.screen = NOW_PLAYING
        self.start_track()

    def start_track(self, start: float = 0.0, paused: bool = False):
        path = self.playlist[self.index]
        self.info = read_info(path)
        self.player.set_volume(self.volume)
        self.player.load(path, start=start, paused=paused)
        self.save_state()
        self.dirty = True

    def skip(self, step: int):
        if not self.playlist:
            return
        self.index = (self.index + step) % len(self.playlist)
        self.start_track()

    def on_player_event(self, kind: str, detail):
        if kind == "ended":
            self.failures = 0
            if self.index + 1 < len(self.playlist):
                self.skip(1)
            else:
                self.index = 0  # end of the playlist: rewind, but stay stopped
                self.info = read_info(self.playlist[0]) if self.playlist else None
                self.player.position = 0
                self.save_state()
        elif kind == "error" and self.playlist:
            log.warning("Can't play %s: %s", self.playlist[self.index], detail)
            self.failures += 1
            if self.failures < len(self.playlist):  # don't spin forever if nothing plays
                self.skip(1)
            else:
                self.failures = 0
        elif kind == "died":
            log.error("mpv exited; quitting so systemd can restart us")
            self.running = False
        self.dirty = True

    # ---- battery & power --------------------------------------------------

    def check_battery(self):
        if self.battery is None:
            return
        try:
            self.battery_status = self.battery.read()
        except Exception as e:
            log.warning("Battery read failed: %s", e)
            return
        self.dirty = True
        s = self.battery_status
        if not s.external_power and s.percent <= self.cfg.low_battery_percent:
            self.low_battery_count += 1
            if self.low_battery_count >= LOW_BATTERY_READINGS:
                self.power_off("Battery low\nPowering off…")
        else:
            self.low_battery_count = 0

    def power_off(self, reason: str):
        log.info(reason.replace("\n", " "))
        self.save_state()
        self.player.stop()
        self.awake = True
        self.display.set_backlight(True)
        self.display.show(self.ui.message(reason))
        time.sleep(2)
        self.display.set_backlight(False)
        self.running = False
        if not self.simulate:
            subprocess.run(["sudo", "-n", "/usr/sbin/poweroff"], check=False)

    # ---- persistence -------------------------------------------------------

    def save_state(self):
        state = {
            "playlist": [str(p) for p in self.playlist],
            "index": self.index,
            "position": self.player.position,
            "volume": self.volume,
        }
        path = self.cfg.state_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state))
            os.replace(tmp, path)
        except OSError as e:
            log.warning("Could not save state: %s", e)

    def restore_state(self):
        try:
            state = json.loads(self.cfg.state_file.read_text())
        except (OSError, ValueError):
            self.player.set_volume(self.volume)
            return
        self.volume = int(state.get("volume", self.volume))
        self.player.set_volume(self.volume)
        playlist = [Path(p) for p in state.get("playlist", [])]
        index = int(state.get("index", 0))
        if 0 <= index < len(playlist) and is_track(playlist[index]):
            self.playlist = [p for p in playlist if is_track(p)]
            self.index = self.playlist.index(playlist[index])
            # Resume paused: a player shouldn't start making noise just because it booted.
            self.start_track(start=float(state.get("position", 0.0)), paused=True)
            self.screen = NOW_PLAYING
            self.open_dir(self.playlist[self.index].parent, select=self.playlist[self.index])
