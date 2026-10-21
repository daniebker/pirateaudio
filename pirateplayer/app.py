"""The main loop: buttons in, mpv commands out, screen redrawn when something changes."""

import json
import logging
import math
import os
import subprocess
import threading
import time
from pathlib import Path

from . import buttons as btn
from .bluetooth import audio_device
from .config import Config
from .database import Indexer, open_database
from .library import (Album, TrackInfo, album_thumbnail, find_albums, is_track, list_dir,
                      prune_thumbnails, read_info, tracks_in)
from .player import PLAYING, STOPPED, Player
from .playlists import SmartPlaylist, load_all
from .ui import COVER, LOW, MenuEntry, Renderer, Row, prepare_cover_flow

log = logging.getLogger(__name__)

TICK = 0.05
BATTERY_INTERVAL = 30.0
SAVE_INTERVAL = 30.0
LOW_BATTERY_READINGS = 3  # consecutive low readings before powering off
VOLUME_STEP = 5
RESTART_THRESHOLD = 3.0   # "previous" restarts the track if we're further in than this
SCAN_TIME = 30.0          # seconds a Bluetooth scan runs for
MESSAGE_TIME = 5.0        # seconds a message stays up
SYNC_REDRAW = 1.0         # seconds between redraws of the sync progress
FLOW_TIME = 0.08          # how quickly cover flow glides to the selected album
FLOW_MAX = 3.0            # a long jump only animates the last few albums
# A track counts as played once it has played for half its length or 4 minutes, whichever is
# sooner (the rule Last.fm uses), or when it plays to the end.
PLAY_FRACTION = 0.5
PLAY_MAX = 240.0

MENU, BROWSER, NOW_PLAYING, BLUETOOTH = "menu", "browser", "now_playing", "bluetooth"
PLAYLISTS, SETTINGS, COVER_FLOW = "playlists", "settings", "cover_flow"
FILES_ENTRY = MenuEntry("Files", "folder")
COVERS_ENTRY = MenuEntry("Cover Flow", "covers")
PLAYLISTS_ENTRY = MenuEntry("Playlists", "playlist")
BLUETOOTH_ENTRY = MenuEntry("Bluetooth", "bluetooth")
SETTINGS_ENTRY = MenuEntry("Settings", "settings")
SYNC_ROW = 0  # rows of the settings screen


def move_cursor(cursor: int, count: int, step: int, kind: str) -> int:
    """Taps wrap around the ends of a list; holding stops at them."""
    if kind == btn.TAP:
        return (cursor + step) % count
    return min(max(0, cursor + step), count - 1)


class App:
    def __init__(self, cfg: Config, display, buttons, battery, player: Player, simulate=False,
                 bluetooth=None):
        self.cfg = cfg
        self.display = display
        self.buttons = buttons
        self.battery = battery
        self.player = player
        self.simulate = simulate
        self.bluetooth = bluetooth
        self.cover_cache = cfg.cache_dir / "covers"
        self.ui = Renderer(self.cover_cache)

        self.db = open_database(cfg.database)
        self.indexer = Indexer(self.db, cfg.music_dir) if self.db else None
        self.syncs_seen = 0  # the indexer's count of finished syncs, when we last looked
        self.was_syncing = False  # whether it was running, when we last looked

        self.screen = MENU
        self.menu = [FILES_ENTRY, COVERS_ENTRY]
        if self.db:
            self.menu.append(PLAYLISTS_ENTRY)
        if bluetooth:
            self.menu.append(BLUETOOTH_ENTRY)
        self.menu.append(SETTINGS_ENTRY)
        self.menu_cursor = 0
        self.root = cfg.music_dir
        self.cwd = self.root
        self.entries = list_dir(self.cwd)
        self.cursor = 0

        self.albums: list[Album] | None = None  # None until the first search finishes
        self.album_cursor = 0
        self.flow_position = 0.0  # the album in the middle of the screen, fractional while moving
        self.album_search: threading.Thread | None = None  # until it has made the thumbnails
        self.found_albums: list[Album] | None = None  # handed over by the search thread

        self.bt_selected: str | None = None  # address of the selected device; None = the scan row
        self.bt_audio: str | None = None     # address of the headset we're playing to
        self.bt_last: str | None = None      # the last headset played to, to reconnect at startup
        self.scan_until = 0.0
        self.message = ""
        self.message_until = 0.0

        self.playlists: list[SmartPlaylist] = []
        self.playlist_cursor = 0
        self.settings_cursor = 0

        self.playlist: list[Path] = []
        self.index = 0
        self.info: TrackInfo | None = None
        self.failures = 0  # consecutive tracks that failed to play
        self.listened = 0.0   # seconds the current track has played for
        self.counted = False  # whether this play of the current track has been counted
        self.speaker_volume = cfg.volume
        self.bluetooth_volume = cfg.bluetooth_volume

        self.battery_status = None
        self.low_battery_count = 0
        self.awake = True
        self.dirty = True
        self.running = True
        self.last_input = self.last_battery = self.last_save = self.last_poll = 0.0
        self.last_tick = 0.0
        self.last_flow = 0.0
        self.now = 0.0  # the time of the tick or button press being handled

    @property
    def volume(self) -> int:
        """The volume of whatever is playing: headphones and speaker each keep their own."""
        return self.bluetooth_volume if self.bt_audio else self.speaker_volume

    @volume.setter
    def volume(self, value: int):
        if self.bt_audio:
            self.bluetooth_volume = value
        else:
            self.speaker_volume = value

    # ---- lifecycle -------------------------------------------------------

    def start(self):
        self.display.set_backlight(True)
        self.restore_state()
        self.now = self.last_input = self.last_tick = time.monotonic()
        if self.indexer:
            # It takes minutes for a big library, and slows everything else down while it runs,
            # so the buttons are locked and the screen shows its progress until it's done.
            # The search for Cover Flow albums waits for it, rather than slowing it further.
            self.indexer.start()
        else:
            # So that Cover Flow has its albums ready by the time it's opened.
            self.start_album_search()
        if self.bluetooth and self.bt_last:
            # Most headsets connect back by themselves when switched on, but not all.
            self.bluetooth.connect(self.bt_last, quiet=True)

    def run(self):
        self.start()
        try:
            # Buttons that can wake the loop early make presses take effect without waiting
            # out the rest of the tick.
            wait = getattr(self.buttons, "wait", time.sleep)
            while self.running:
                self.tick(time.monotonic())
                wait(TICK)
        finally:
            self.save_state()

    def close(self):
        if self.indexer:
            self.indexer.stop()
        if self.db:
            self.db.close()

    def tick(self, now: float):
        self.now = now
        self.tick_listening(now)
        for name, kind in self.buttons.poll():
            self.on_button(name, kind, now)

        while not self.player.events.empty():
            self.on_player_event(*self.player.events.get())

        if self.bluetooth:
            self.tick_bluetooth(now)
        if self.indexer:
            self.tick_indexer(now)
        if self.message and now >= self.message_until:
            self.message = ""
            self.dirty = True

        self.tick_cover_flow(now)

        if now - self.last_battery >= BATTERY_INTERVAL:
            self.last_battery = now
            self.check_battery()

        if self.player.state == PLAYING:
            if now - self.last_save >= SAVE_INTERVAL:
                self.last_save = now
                self.save_state()
            if (self.awake and self.screen == NOW_PLAYING and not self.syncing
                    and now - self.last_poll >= 1.0):
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
        bt = self.bt_audio is not None
        if self.syncing:
            return self.ui.indexing(self.indexer.done, self.indexer.total, self.battery_status)
        if self.screen == MENU:
            status = {BLUETOOTH_ENTRY: self.headset_name(), SETTINGS_ENTRY: self.sync_status()}
            return self.ui.menu(self.menu, self.menu_cursor, status,
                                self.battery_status, self.player.state == PLAYING, bt)
        if self.screen == BROWSER:
            title = "Music" if self.cwd == self.root else self.cwd.name
            return self.ui.browser(title, self.entries, self.cursor, self.battery_status,
                                   self.player.state == PLAYING, bt)
        if self.screen == COVER_FLOW:
            return self.ui.cover_flow(self.albums, self.flow_position, self.battery_status,
                                      self.player.state == PLAYING, bt)
        if self.screen == BLUETOOTH:
            devices = self.bluetooth.devices()
            return self.ui.bluetooth(devices, self.bt_cursor(devices), self.bluetooth.scanning,
                                     max(0.0, self.scan_until - self.now), self.message,
                                     self.battery_status)
        if self.screen == PLAYLISTS:
            rows = [Row(pl.name, "Has errors" if pl.problems else "", status_colour=LOW)
                    for pl in self.playlists]
            return self.ui.list_screen("Playlists", rows, self.playlist_cursor,
                                       self.battery_status, self.player.state == PLAYING, bt,
                                       empty="No playlists.\nAdd them to the\nplaylists folder.",
                                       message=self.message)
        if self.screen == SETTINGS:
            return self.ui.list_screen("Settings", self.settings_rows(), self.settings_cursor,
                                       self.battery_status, self.player.state == PLAYING, bt,
                                       message=self.message)
        return self.ui.now_playing(self.info, self.player.state, self.player.position, self.volume,
                                   self.index, len(self.playlist), self.battery_status, bt)

    # ---- input -------------------------------------------------------------

    def on_button(self, name: str, kind: str, now: float):
        self.now = self.last_input = now
        if not self.awake:
            # The press that wakes the screen doesn't also do something unseen.
            self.awake = True
            self.dirty = True
            self.display.set_backlight(True)
            return
        if name == btn.COMBO:
            self.power_off("Powering off…")
            return
        if self.syncing:
            return  # locked until the library is synced
        if self.screen == MENU:
            self.menu_button(name, kind)
        elif self.screen == BROWSER:
            self.browser_button(name, kind)
        elif self.screen == COVER_FLOW:
            self.cover_flow_button(name, kind)
        elif self.screen == BLUETOOTH:
            self.bluetooth_button(name, kind)
        elif self.screen == PLAYLISTS:
            self.playlists_button(name, kind)
        elif self.screen == SETTINGS:
            self.settings_button(name, kind)
        else:
            self.now_playing_button(name, kind)
        self.dirty = True

    def menu_button(self, name: str, kind: str):
        if name in ("A", "B"):
            self.menu_cursor = move_cursor(self.menu_cursor, len(self.menu),
                                           -1 if name == "A" else 1, kind)
        elif name == "X" and kind != btn.REPEAT:
            entry = self.menu[self.menu_cursor]
            if entry is FILES_ENTRY:
                self.screen = BROWSER
                self.open_dir(self.root)
            elif entry is COVERS_ENTRY:
                self.open_cover_flow()
            elif entry is PLAYLISTS_ENTRY:
                self.open_playlists()
            elif entry is SETTINGS_ENTRY:
                self.screen = SETTINGS
                self.settings_cursor = 0
            else:
                self.open_bluetooth()
        elif name == "Y" and kind != btn.REPEAT and self.playlist:
            self.screen = NOW_PLAYING

    def open_menu(self, select: MenuEntry):
        self.screen = MENU
        self.menu_cursor = self.menu.index(select)

    def browser_button(self, name: str, kind: str):
        if name in ("A", "B") and self.entries:
            self.cursor = move_cursor(self.cursor, len(self.entries), -1 if name == "A" else 1, kind)
        elif name == "X" and kind != btn.REPEAT and self.entries:
            entry = self.entries[self.cursor]
            if entry.is_dir() and kind == btn.TAP:
                self.open_dir(entry)
            elif entry.is_dir():
                self.play(tracks_in(entry, recursive=True), 0)
            else:
                tracks = [p for p in self.entries if not p.is_dir()]
                self.play(tracks, tracks.index(entry))
        elif name == "Y" and kind == btn.TAP:
            if self.cwd != self.root:
                self.open_dir(self.cwd.parent, select=self.cwd)
            else:
                self.open_menu(FILES_ENTRY)
        elif name == "Y" and kind == btn.HOLD and self.playlist:
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
                self.listened, self.counted = 0.0, False  # listening again is another play
            else:
                self.skip(-1)

    def open_dir(self, directory: Path, select: Path | None = None):
        if not directory.is_relative_to(self.root):
            directory = self.root
        self.cwd = directory
        self.entries = list_dir(directory)
        self.cursor = self.entries.index(select) if select in self.entries else 0

    # ---- playlists -----------------------------------------------------------

    def open_playlists(self):
        # Read afresh each time, so edits show up without restarting.
        self.playlists = load_all(self.cfg.playlists_dir)
        self.playlist_cursor = min(self.playlist_cursor, max(0, len(self.playlists) - 1))
        self.screen = PLAYLISTS

    def playlists_button(self, name: str, kind: str):
        if name in ("A", "B") and self.playlists:
            self.playlist_cursor = move_cursor(self.playlist_cursor, len(self.playlists),
                                               -1 if name == "A" else 1, kind)
        elif name == "X" and kind != btn.REPEAT and self.playlists:
            self.play_playlist(self.playlists[self.playlist_cursor])
        elif name == "Y" and kind == btn.TAP:
            self.open_menu(PLAYLISTS_ENTRY)
        elif name == "Y" and kind == btn.HOLD and self.playlist:
            self.screen = NOW_PLAYING

    def play_playlist(self, pl: SmartPlaylist):
        try:
            indexed = self.db.tracks(pl.since())
        except Exception as e:
            log.error("Could not read the library for %s: %s", pl.name, e)
            self.show_message("Couldn't read the library")
            return
        tracks = [p for p in pl.select(indexed, self.root) if is_track(p)]
        if tracks:
            log.info("Playing playlist %s: %d tracks", pl.name, len(tracks))
            self.play(tracks, 0)
        else:
            self.show_message(f"No tracks match\n{pl.name}")

    # ---- settings ------------------------------------------------------------

    @property
    def syncing(self) -> bool:
        """Whether the library is being synced, which locks the buttons."""
        return self.indexer is not None and self.indexer.running

    def sync_status(self) -> str:
        if self.indexer is None:
            return "Unavailable"
        count = self.db.track_count()
        return f"{count} track{'' if count == 1 else 's'}"

    def settings_rows(self) -> list[Row]:
        return [Row("Sync library", self.sync_status())]

    def settings_button(self, name: str, kind: str):
        rows = self.settings_rows()
        if name in ("A", "B"):
            self.settings_cursor = move_cursor(self.settings_cursor, len(rows),
                                               -1 if name == "A" else 1, kind)
        elif name == "X" and kind == btn.TAP and self.settings_cursor == SYNC_ROW:
            if self.indexer is None:
                self.show_message("The library database\ncouldn't be opened")
            elif self.indexer.start():
                self.player.pause()  # the buttons are locked until it's done
                self.save_state()
        elif name == "Y" and kind == btn.TAP:
            self.open_menu(SETTINGS_ENTRY)
        elif name == "Y" and kind == btn.HOLD and self.playlist:
            self.screen = NOW_PLAYING

    def tick_indexer(self, now: float):
        running = self.indexer.running
        if running != self.was_syncing:  # locked or unlocked: swap the progress screen in or out
            self.was_syncing = running
            self.dirty = True
            if not running and self.albums is None:
                self.start_album_search()  # held back at startup until the library was synced
        if self.indexer.finished != self.syncs_seen:
            self.syncs_seen = self.indexer.finished
            if self.screen == SETTINGS:
                self.show_message(f"Synced {self.indexer.last_count} tracks")
        elif running and now - self.last_poll >= SYNC_REDRAW:
            self.last_poll = now  # move the progress bar along
            self.dirty = True

    # ---- cover flow --------------------------------------------------------

    def open_cover_flow(self):
        """Show the albums found last time straight away, and look again in case music has
        been added or removed since."""
        self.screen = COVER_FLOW
        self.flow_position = float(self.album_cursor)
        self.start_album_search()

    def start_album_search(self):
        """Look for albums in the background, unless a search is already under way."""
        if self.album_search is None:
            self.album_search = threading.Thread(target=self.search_albums, daemon=True)
            self.album_search.start()

    def search_albums(self):
        try:
            # Give way to drawing the screen and to mpv: on a single-core Pi Zero a search
            # at startup would otherwise make the first button presses sluggish.
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        except (AttributeError, OSError):  # not Linux, where each thread has its own priority
            pass
        try:
            albums = find_albums(self.root)
        except Exception:
            log.exception("Album search failed")
            self.found_albums = self.albums or []
            return
        self.found_albums = albums
        prepare_cover_flow()
        # Then make sure every cover has a thumbnail, so that flowing through them doesn't wait
        # for big pictures to be decoded. The ones nearest the selected album come first.
        middle = min(self.album_cursor, len(albums) - 1)
        for index in sorted(range(len(albums)), key=lambda i: abs(i - middle)):
            album_thumbnail(albums[index], COVER, self.cover_cache)
        prune_thumbnails(albums, COVER, self.cover_cache)

    def tick_cover_flow(self, now: float):
        if self.found_albums is not None:
            self.receive_albums(self.found_albums)
        if self.album_search is not None and not self.album_search.is_alive():
            self.album_search = None
        if self.screen == COVER_FLOW and self.flow_position != self.album_cursor:
            gap = self.album_cursor - self.flow_position
            if abs(gap) > FLOW_MAX:
                gap = math.copysign(FLOW_MAX, gap)
            # Ease in: cover the same fraction of what's left every moment, so it slows as it
            # arrives however long a frame takes to draw.
            gap *= math.exp(-(now - self.last_flow) / FLOW_TIME)
            self.flow_position = self.album_cursor - gap if abs(gap) > 0.02 else self.album_cursor
            self.dirty = True
        self.last_flow = now

    def receive_albums(self, albums: list[Album]):
        """Take the search's result, keeping the same album selected if it's still there."""
        selected = self.albums[self.album_cursor].folder if self.albums else None
        folders = [a.folder for a in albums]
        self.album_cursor = folders.index(selected) if selected in folders else 0
        self.flow_position = float(self.album_cursor)
        self.albums = albums
        self.found_albums = None
        self.dirty = True

    def cover_flow_button(self, name: str, kind: str):
        if name in ("A", "B") and self.albums:
            self.album_cursor = move_cursor(self.album_cursor, len(self.albums),
                                            -1 if name == "A" else 1, kind)
        elif name == "X" and kind == btn.TAP and self.albums:
            self.play(tracks_in(self.albums[self.album_cursor].folder), 0)
        elif name == "X" and kind == btn.HOLD and self.albums:
            self.screen = BROWSER  # to pick a track from it
            self.open_dir(self.albums[self.album_cursor].folder)
        elif name == "Y" and kind == btn.TAP:
            self.open_menu(COVERS_ENTRY)
        elif name == "Y" and kind == btn.HOLD and self.playlist:
            self.screen = NOW_PLAYING

    # ---- bluetooth ---------------------------------------------------------

    def open_bluetooth(self):
        self.screen = BLUETOOTH
        self.bt_selected = None
        self.start_scan()

    def start_scan(self):
        self.scan_until = self.now + SCAN_TIME
        self.bluetooth.start_scan()

    def bt_cursor(self, devices) -> int:
        """The selected row, counting the scan row as 0, following the device if it moved."""
        for row, device in enumerate(devices, start=1):
            if device.address == self.bt_selected:
                return row
        return 0

    def bluetooth_button(self, name: str, kind: str):
        devices = self.bluetooth.devices()
        cursor = self.bt_cursor(devices)
        device = devices[cursor - 1] if cursor else None
        if name in ("A", "B"):
            cursor = move_cursor(cursor, len(devices) + 1, -1 if name == "A" else 1, kind)
            self.bt_selected = devices[cursor - 1].address if cursor else None
        elif name == "X" and kind == btn.TAP:
            if device is None:
                if self.bluetooth.scanning:
                    self.bluetooth.stop_scan()
                else:
                    self.start_scan()
            elif device.busy:
                pass
            elif device.connected:
                self.bluetooth.disconnect(device.address)
            else:
                self.bluetooth.connect(device.address)
        elif name == "X" and kind == btn.HOLD and device is not None and device.paired:
            self.bluetooth.forget(device.address)
            if device.address == self.bt_last:
                self.bt_last = None
                self.save_state()
            self.show_message(f"Forgot {device.name}")
        elif name == "Y" and kind != btn.REPEAT:
            self.bluetooth.stop_scan()
            self.open_menu(BLUETOOTH_ENTRY)

    def tick_bluetooth(self, now: float):
        self.bluetooth.poll()
        while not self.bluetooth.events.empty():
            self.on_bluetooth_event(*self.bluetooth.events.get())
        if self.bluetooth.scanning and now >= self.scan_until:
            self.bluetooth.stop_scan()
        # Count the scan down on screen.
        if self.screen == BLUETOOTH and self.bluetooth.scanning and now - self.last_poll >= 1.0:
            self.last_poll = now
            self.dirty = True

    def on_bluetooth_event(self, kind: str, detail):
        if kind == "audio":
            self.route_audio(detail)
        elif kind == "error":
            self.show_message(detail)
        self.dirty = True

    def route_audio(self, address: str | None):
        """Play to the headset that just connected, or back to the speaker when it's gone."""
        if address == self.bt_audio:
            return
        if address:
            log.info("Playing to Bluetooth headset %s", address)
            self.player.set_audio_device(audio_device(address))
            self.bt_last = address
        else:
            log.info("Bluetooth headset gone; back to %s", self.cfg.audio_device or "the speaker")
            # Like a phone: losing the headset pauses, rather than carrying on out loud.
            self.player.pause()
            self.player.set_audio_device(self.cfg.audio_device)
        self.bt_audio = address
        self.player.set_volume(self.volume)
        self.save_state()

    def headset_name(self) -> str:
        """The name of the headset we're playing to, for the menu."""
        if self.bluetooth and self.bt_audio:
            for device in self.bluetooth.devices():
                if device.address == self.bt_audio:
                    return device.name
        return ""

    def show_message(self, text: str):
        self.message = text
        self.message_until = self.now + MESSAGE_TIME
        self.dirty = True

    # ---- playback ----------------------------------------------------------

    def play(self, tracks: list[Path], index: int):
        if not tracks:
            return
        self.playlist = tracks
        self.index = index
        self.screen = NOW_PLAYING
        self.start_track()

    def start_track(self, start: float = 0.0, paused: bool = False, listened: float = 0.0,
                    counted: bool = False):
        path = self.playlist[self.index]
        self.info = read_info(path)
        self.listened, self.counted = listened, counted
        self.player.set_volume(self.volume)
        self.player.load(path, start=start, paused=paused)
        self.save_state()
        self.dirty = True

    def skip(self, step: int):
        if not self.playlist:
            return
        self.index = (self.index + step) % len(self.playlist)
        self.start_track()

    # ---- play counts ---------------------------------------------------------

    def tick_listening(self, now: float):
        """Add up how long the current track has played, and count it once it's played enough."""
        elapsed, self.last_tick = now - self.last_tick, now
        if self.player.state != PLAYING or self.counted:
            return
        # A long gap means we weren't running (suspended, or the clock jumped), not playing.
        self.listened += min(max(0.0, elapsed), 1.0)
        length = self.info.length if self.info else 0.0
        if self.listened >= min(PLAY_MAX, length * PLAY_FRACTION if length else PLAY_MAX):
            self.count_play()

    def count_play(self):
        if self.counted or not self.playlist:
            return
        self.counted = True
        if self.db:
            path = self.playlist[self.index]
            try:
                self.db.record_play(path)
            except Exception as e:  # never let bookkeeping stop the music
                log.warning("Could not record a play of %s: %s", path, e)

    def on_player_event(self, kind: str, detail):
        if kind == "ended":
            self.count_play()
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
            "volume": self.speaker_volume,
            "bluetooth_volume": self.bluetooth_volume,
            "bluetooth": self.bt_last,
            "listened": self.listened,
            "counted": self.counted,
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
        self.speaker_volume = int(state.get("volume", self.speaker_volume))
        self.bluetooth_volume = int(state.get("bluetooth_volume", self.bluetooth_volume))
        self.bt_last = state.get("bluetooth")
        self.player.set_volume(self.volume)
        playlist = [Path(p) for p in state.get("playlist", [])]
        index = int(state.get("index", 0))
        if 0 <= index < len(playlist) and is_track(playlist[index]):
            self.playlist = [p for p in playlist if is_track(p)]
            self.index = self.playlist.index(playlist[index])
            # Resume paused: a player shouldn't start making noise just because it booted.
            self.start_track(start=float(state.get("position", 0.0)), paused=True,
                             listened=float(state.get("listened", 0.0)),
                             counted=bool(state.get("counted", False)))
            self.screen = NOW_PLAYING
            self.open_dir(self.playlist[self.index].parent, select=self.playlist[self.index])
