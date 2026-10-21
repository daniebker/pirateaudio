import json
from dataclasses import replace

import pytest

from pirateplayer import app as app_module
from pirateplayer import buttons as btn
from pirateplayer.app import (BLUETOOTH, BLUETOOTH_ENTRY, BROWSER, FILES_ENTRY,
                              LOW_BATTERY_READINGS, MENU, MESSAGE_TIME, NOW_PLAYING, PLAY_MAX,
                              PLAYLISTS, PLAYLISTS_ENTRY, SCAN_TIME, SETTINGS, SETTINGS_ENTRY,
                              VOLUME_STEP, App)
from pirateplayer.battery import BatteryStatus
from pirateplayer.bluetooth import CONNECTING, audio_device
from pirateplayer.player import PAUSED, PLAYING, STOPPED

from .conftest import (HEADSET, SPEAKER, FakeBluetooth, FakeButtons, FakeDisplay, FakePlayer,
                       make_wav)


class FakeBattery:
    def __init__(self, status):
        self.status = status

    def read(self):
        if isinstance(self.status, Exception):
            raise self.status
        return self.status


@pytest.fixture
def make_app(cfg, monkeypatch):
    monkeypatch.setattr(app_module.time, "sleep", lambda s: None)

    made = []

    def make(battery=None, bluetooth=None, files=True):
        """`files` opens Files from the menu, unless the app resumed into now playing."""
        a = App(cfg, FakeDisplay(), FakeButtons(), battery, FakePlayer(), simulate=True,
                bluetooth=bluetooth)
        a.start()
        a.indexer.join()  # index the test library before going on
        made.append(a)
        if files and a.screen == MENU:
            a.on_button("X", btn.TAP, 0.5)
        return a
    yield make
    for a in made:
        a.close()


@pytest.fixture
def app(make_app):
    return make_app()


def names(paths):
    return [p.name for p in paths]


def tap(app, name, now=1.0):
    app.on_button(name, btn.TAP, now)


def test_starts_at_menu(make_app):
    a = make_app(files=False)
    assert a.screen == MENU
    assert a.menu == [FILES_ENTRY, PLAYLISTS_ENTRY, SETTINGS_ENTRY]  # no Bluetooth row
    a.on_button("X", btn.TAP, 1.0)
    assert a.screen == BROWSER


def test_files_lists_the_music_folder(app, music):
    assert app.screen == BROWSER
    assert names(app.entries) == ["Album 1", "Album 2", "loose.wav"]
    assert app.player.volume == 50


def test_cursor_wraps_on_tap_and_clamps_on_repeat(app):
    tap(app, "A")
    assert app.cursor == 2
    tap(app, "B")
    assert app.cursor == 0
    for _ in range(5):
        app.on_button("B", btn.REPEAT, 1.0)
    assert app.cursor == 2


def test_open_folder_and_go_back(app, music):
    tap(app, "B")
    tap(app, "X")
    assert app.cwd == music / "Album 2"
    assert names(app.entries) == ["Disc 1"]
    tap(app, "Y")
    assert app.cwd == music
    assert app.cursor == 1  # the folder we came from is selected


def test_play_from_selected_track(app, music):
    tap(app, "X")  # into Album 1
    tap(app, "B")
    tap(app, "X")
    assert app.screen == NOW_PLAYING
    assert names(app.playlist) == ["01 First.wav", "02 Second.wav", "10 Tenth.wav"]
    assert app.index == 1
    assert app.player.loaded[-1] == (music / "Album 1" / "02 Second.wav", 0.0, False)
    assert app.info.title == "02 Second"


def test_hold_x_plays_folder_recursively(app):
    app.on_button("X", btn.HOLD, 1.0)
    assert names(app.playlist) == ["01 First.wav", "02 Second.wav", "10 Tenth.wav"]
    assert app.index == 0
    tap(app, "Y")
    tap(app, "Y")
    tap(app, "B")  # Album 2, whose tracks are all in a subfolder
    app.on_button("X", btn.HOLD, 1.0)
    assert names(app.playlist) == ["a.wav"]


def test_y_goes_back_up_to_the_menu(app, music):
    tap(app, "X")  # into Album 1
    tap(app, "Y")
    assert app.screen == BROWSER and app.cwd == music
    tap(app, "Y")
    assert app.screen == MENU
    assert app.menu[app.menu_cursor] is FILES_ENTRY
    tap(app, "X")  # Files again starts at the top of the music folder
    assert app.screen == BROWSER and app.cwd == music and app.cursor == 0


def test_now_playing_from_menu_and_browser_only_with_playlist(app):
    app.on_button("Y", btn.HOLD, 1.0)
    assert app.screen == BROWSER
    tap(app, "Y")
    tap(app, "Y")
    assert app.screen == MENU  # nothing to go to
    tap(app, "X")
    app.on_button("X", btn.HOLD, 1.0)  # play Album 1
    tap(app, "Y")
    assert app.screen == BROWSER
    assert app.cwd.name == "Album 1"
    assert app.cursor == 0
    app.on_button("Y", btn.HOLD, 1.0)
    assert app.screen == NOW_PLAYING
    tap(app, "Y")
    tap(app, "Y")
    tap(app, "Y")
    assert app.screen == MENU
    tap(app, "Y")
    assert app.screen == NOW_PLAYING


def test_now_playing_controls(app):
    app.on_button("X", btn.HOLD, 1.0)
    tap(app, "A")
    assert app.volume == app.player.volume == 50 + VOLUME_STEP
    for _ in range(30):
        tap(app, "B")
    assert app.volume == 0
    tap(app, "X")
    assert app.player.state == PAUSED
    tap(app, "X")
    assert app.player.state == PLAYING
    app.on_button("X", btn.HOLD, 1.0)
    assert app.index == 1
    app.on_button("Y", btn.HOLD, 1.0)
    assert app.index == 0
    app.on_button("Y", btn.HOLD, 1.0)
    assert app.index == 2  # wraps to the end
    app.player.position = 10
    app.on_button("Y", btn.HOLD, 1.0)
    assert app.index == 2
    assert app.player.seeks == [0]


def test_x_restarts_when_stopped(app):
    app.on_button("X", btn.HOLD, 1.0)
    app.player.state = STOPPED
    loads = len(app.player.loaded)
    tap(app, "X")
    assert len(app.player.loaded) == loads + 1


def test_track_end_advances_then_stops_at_end(app):
    app.on_button("X", btn.HOLD, 1.0)
    app.on_player_event("ended", None)
    assert app.index == 1
    app.on_player_event("ended", None)
    assert app.index == 2
    assert app.player.state == PLAYING
    app.player.state = STOPPED
    app.on_player_event("ended", None)
    assert app.index == 0
    assert app.player.state == STOPPED
    assert app.info.title == "01 First"


def test_errors_skip_but_do_not_spin_forever(app):
    app.on_button("X", btn.HOLD, 1.0)
    loads = len(app.player.loaded)
    for _ in range(len(app.playlist)):
        app.on_player_event("error", "bad file")
    assert len(app.player.loaded) == loads + len(app.playlist) - 1
    assert app.failures == 0


def test_mpv_dying_stops_the_app(app):
    app.on_player_event("died", None)
    assert not app.running


def test_wake_press_does_nothing_else(app):
    app.awake = False
    tap(app, "B")
    assert app.awake
    assert app.cursor == 0
    assert app.display.backlight is True


def test_tick_handles_buttons_events_and_backlight(app):
    app.buttons.pending = [("B", btn.TAP)]
    app.tick(1.0)
    assert app.cursor == 1
    assert len(app.display.images) == 1
    app.tick(1.1)
    assert len(app.display.images) == 1  # nothing changed, nothing redrawn
    app.tick(1.0 + app.cfg.backlight_timeout + 1)
    assert not app.awake
    assert app.display.backlight is False


def test_tick_polls_position_on_now_playing(app):
    app.on_button("X", btn.HOLD, 1.0)
    app.tick(5.0)
    assert app.player.polls == 1
    app.player.events.put(("ended", None))
    app.tick(5.1)
    assert app.index == 1


def test_low_battery_powers_off_after_repeated_readings(make_app):
    battery = FakeBattery(BatteryStatus(3, external_power=False))
    a = make_app(battery)
    for _ in range(LOW_BATTERY_READINGS - 1):
        a.check_battery()
    assert a.running
    a.check_battery()
    assert not a.running


def test_low_battery_count_resets_when_charging(make_app):
    battery = FakeBattery(BatteryStatus(3, external_power=False))
    a = make_app(battery)
    a.check_battery()
    a.check_battery()
    battery.status = BatteryStatus(3, external_power=True)
    a.check_battery()
    assert a.low_battery_count == 0
    assert a.running


def test_battery_read_failure_is_tolerated(make_app):
    a = make_app(FakeBattery(OSError("i2c error")))
    a.check_battery()
    assert a.battery_status is None
    assert a.running


def test_combo_powers_off(app):
    app.on_button(btn.COMBO, btn.HOLD, 1.0)
    assert not app.running
    assert app.player.state == STOPPED


def test_state_round_trip(app, cfg, make_app, music):
    app.on_button("X", btn.HOLD, 1.0)
    app.skip(1)
    app.player.position = 33.0
    tap(app, "A")
    app.save_state()
    saved = json.loads(cfg.state_file.read_text())
    assert saved["index"] == 1
    assert saved["position"] == 33.0
    assert saved["volume"] == 55

    restored = make_app()
    assert restored.screen == NOW_PLAYING
    assert restored.playlist == app.playlist
    assert restored.index == 1
    assert restored.volume == restored.player.volume == 55
    assert restored.player.loaded == [(music / "Album 1" / "02 Second.wav", 33.0, True)]
    assert restored.cwd == music / "Album 1"
    assert restored.cursor == 1


def test_restore_skips_deleted_tracks(app, cfg, make_app, music):
    app.on_button("X", btn.HOLD, 1.0)
    app.skip(2)
    app.save_state()
    (music / "Album 1" / "01 First.wav").unlink()
    restored = make_app()
    assert names(restored.playlist) == ["02 Second.wav", "10 Tenth.wav"]
    assert restored.index == 1


def test_restore_ignores_bad_state(cfg, make_app):
    cfg.state_file.parent.mkdir(parents=True)
    cfg.state_file.write_text("{not json")
    a = make_app(files=False)
    assert a.screen == MENU
    assert a.playlist == []


def test_open_dir_outside_root_goes_to_root(app, music, tmp_path):
    app.open_dir(tmp_path)
    assert app.cwd == music


# ---- bluetooth ----------------------------------------------------------------


@pytest.fixture
def bt_app(make_app):
    return make_app(bluetooth=FakeBluetooth([HEADSET, SPEAKER]), files=False)


def open_bluetooth_screen(app):
    app.menu_cursor = app.menu.index(BLUETOOTH_ENTRY)
    tap(app, "X")


def test_menu_has_bluetooth_with_bluetooth(bt_app):
    assert bt_app.menu == [FILES_ENTRY, PLAYLISTS_ENTRY, BLUETOOTH_ENTRY, SETTINGS_ENTRY]
    tap(bt_app, "A")  # wraps
    assert bt_app.menu[bt_app.menu_cursor] is SETTINGS_ENTRY


def test_opening_bluetooth_scans_and_leaving_stops(bt_app):
    open_bluetooth_screen(bt_app)
    assert bt_app.screen == BLUETOOTH
    assert bt_app.bluetooth.scanning
    assert bt_app.bt_selected is None
    tap(bt_app, "Y")
    assert bt_app.screen == MENU
    assert not bt_app.bluetooth.scanning
    assert bt_app.menu[bt_app.menu_cursor] is BLUETOOTH_ENTRY


def test_menu_shows_the_connected_headset(bt_app):
    assert bt_app.headset_name() == ""
    bt_app.on_bluetooth_event("audio", HEADSET.address)
    assert bt_app.headset_name() == "Headset"


def test_scan_row_toggles_scanning(bt_app):
    open_bluetooth_screen(bt_app)
    tap(bt_app, "X")
    assert not bt_app.bluetooth.scanning
    tap(bt_app, "X", now=50.0)
    assert bt_app.bluetooth.scanning
    assert bt_app.scan_until == 50.0 + SCAN_TIME


def test_scan_stops_after_scan_time(bt_app):
    open_bluetooth_screen(bt_app)
    bt_app.tick(1.0 + SCAN_TIME - 1)
    assert bt_app.bluetooth.scanning
    bt_app.tick(1.0 + SCAN_TIME)
    assert not bt_app.bluetooth.scanning


def test_select_device_connects_or_disconnects(bt_app):
    open_bluetooth_screen(bt_app)
    tap(bt_app, "B")  # Headset (paired, so listed first)
    assert bt_app.bt_selected == HEADSET.address
    tap(bt_app, "X")
    assert bt_app.bluetooth.calls[-1] == ("connect", HEADSET.address, False)

    tap(bt_app, "B")  # Speaker, not paired: connect pairs first
    tap(bt_app, "X")
    assert bt_app.bluetooth.calls[-1] == ("connect", SPEAKER.address, False)

    bt_app.bluetooth.list = [replace(HEADSET, connected=True), replace(SPEAKER, busy=CONNECTING)]
    calls = len(bt_app.bluetooth.calls)
    tap(bt_app, "X")  # busy: ignored
    assert len(bt_app.bluetooth.calls) == calls
    tap(bt_app, "A")
    tap(bt_app, "X")
    assert bt_app.bluetooth.calls[-1] == ("disconnect", HEADSET.address)


def test_cursor_follows_the_device_when_the_list_reorders(bt_app):
    open_bluetooth_screen(bt_app)
    tap(bt_app, "B")
    tap(bt_app, "B")  # Speaker, row 2
    assert bt_app.bt_cursor(bt_app.bluetooth.devices()) == 2
    bt_app.bluetooth.list = [HEADSET, replace(SPEAKER, paired=True, connected=True)]
    assert bt_app.bt_cursor(bt_app.bluetooth.devices()) == 1
    bt_app.bluetooth.list = [HEADSET]  # it went away: back to the scan row
    assert bt_app.bt_cursor(bt_app.bluetooth.devices()) == 0


def test_hold_x_forgets_paired_device(bt_app):
    bt_app.bt_last = HEADSET.address
    open_bluetooth_screen(bt_app)
    tap(bt_app, "B")
    bt_app.on_button("X", btn.HOLD, 1.0)
    assert bt_app.bluetooth.calls[-1] == ("forget", HEADSET.address)
    assert bt_app.bt_last is None
    assert "Forgot Headset" in bt_app.message
    tap(bt_app, "B")  # Speaker isn't paired: nothing to forget
    bt_app.on_button("X", btn.HOLD, 1.0)
    assert bt_app.bluetooth.calls[-1] == ("forget", HEADSET.address)


def test_headset_connecting_switches_audio_and_is_remembered(bt_app, cfg):
    bt_app.bluetooth.events.put(("audio", HEADSET.address))
    bt_app.tick(1.0)
    assert bt_app.player.audio_device == audio_device(HEADSET.address)
    assert bt_app.bt_audio == HEADSET.address
    assert json.loads(cfg.state_file.read_text())["bluetooth"] == HEADSET.address


def test_headset_going_away_pauses_and_returns_to_speaker(bt_app, cfg):
    cfg.audio_device = "alsa/hw:0"
    tap(bt_app, "X")  # Files
    bt_app.on_button("X", btn.HOLD, 1.0)  # play Album 1
    bt_app.on_bluetooth_event("audio", HEADSET.address)
    assert bt_app.player.state == PLAYING
    bt_app.on_bluetooth_event("audio", None)
    assert bt_app.player.state == PAUSED
    bt_app.on_bluetooth_event("audio", HEADSET.address)
    bt_app.on_bluetooth_event("audio", None)
    assert bt_app.player.state == PAUSED  # already paused: stays paused
    assert bt_app.player.audio_device == "alsa/hw:0"
    assert bt_app.bt_audio is None
    assert bt_app.bt_last == HEADSET.address  # still the one to reconnect to


def test_reconnects_to_last_headset_on_start(bt_app, make_app):
    bt_app.on_bluetooth_event("audio", HEADSET.address)
    restarted = make_app(bluetooth=FakeBluetooth([HEADSET]))
    assert ("connect", HEADSET.address, True) in restarted.bluetooth.calls
    without = make_app()  # Bluetooth turned off in the config...
    without.save_state()
    assert make_app().bt_last == HEADSET.address  # ...doesn't lose the headset


def test_error_message_shows_then_expires(bt_app):
    open_bluetooth_screen(bt_app)
    bt_app.bluetooth.events.put(("error", "Couldn't connect to Speaker:\nnot in range"))
    bt_app.tick(2.0)
    assert bt_app.message.startswith("Couldn't connect")
    bt_app.tick(2.0 + MESSAGE_TIME)
    assert bt_app.message == ""


def test_renders_every_screen_with_bluetooth(bt_app):
    open_bluetooth_screen(bt_app)
    bt_app.dirty = True
    bt_app.tick(2.0)
    bt_app.on_bluetooth_event("audio", HEADSET.address)
    tap(bt_app, "Y")
    bt_app.on_button("X", btn.HOLD, 3.0)
    bt_app.tick(4.0)
    assert len(bt_app.display.images) == 2


def test_headphones_and_speaker_keep_their_own_volume(bt_app, cfg, make_app):
    tap(bt_app, "X")  # Files
    bt_app.on_button("X", btn.HOLD, 1.0)  # now playing: A/B change the volume
    tap(bt_app, "B")
    assert bt_app.volume == bt_app.player.volume == 50 - VOLUME_STEP
    bt_app.on_bluetooth_event("audio", HEADSET.address)
    assert bt_app.volume == bt_app.player.volume == cfg.bluetooth_volume
    tap(bt_app, "A")
    tap(bt_app, "A")
    assert bt_app.player.volume == cfg.bluetooth_volume + 2 * VOLUME_STEP
    bt_app.on_bluetooth_event("audio", None)
    assert bt_app.volume == bt_app.player.volume == 50 - VOLUME_STEP

    saved = json.loads(cfg.state_file.read_text())
    assert saved["volume"] == 50 - VOLUME_STEP
    assert saved["bluetooth_volume"] == cfg.bluetooth_volume + 2 * VOLUME_STEP
    restored = make_app(bluetooth=FakeBluetooth())
    restored.on_bluetooth_event("audio", HEADSET.address)
    assert restored.player.volume == cfg.bluetooth_volume + 2 * VOLUME_STEP


# ---- library index and play counts ----------------------------------------------


def test_indexes_the_library_at_startup(app):
    assert app.db.track_count() == 5


def play_for(app, seconds, start=10.0):
    """Tick through `seconds` of playback, a second at a time."""
    for t in range(int(seconds) + 1):
        app.tick(start + t)


def test_counts_a_play_after_half_the_track(app, music):
    app.on_button("X", btn.HOLD, 1.0)  # Album 1, from its first track
    track = app.playlist[0]
    app.info.length = 100.0
    app.last_tick = 10.0
    play_for(app, 48)
    assert app.db.play_count(track) == 0
    play_for(app, 3, start=59.0)
    assert app.db.play_count(track) == 1
    play_for(app, 60, start=70.0)
    assert app.db.play_count(track) == 1  # once per play


def test_counts_long_tracks_after_four_minutes(app):
    app.on_button("X", btn.HOLD, 1.0)
    app.info.length = 3600.0
    app.last_tick = 10.0
    play_for(app, PLAY_MAX + 1)
    assert app.db.play_count(app.playlist[0]) == 1


def test_paused_time_and_gaps_dont_count(app):
    app.on_button("X", btn.HOLD, 1.0)
    app.info.length = 100.0
    app.player.toggle_pause()
    app.last_tick = 10.0
    play_for(app, 100)
    app.player.toggle_pause()
    app.tick(10_000.0)  # a gap: the clock jumped
    assert app.listened <= 1.0
    assert app.db.play_count(app.playlist[0]) == 0


def test_playing_to_the_end_counts_once(app):
    app.on_button("X", btn.HOLD, 1.0)
    first = app.playlist[0]
    app.on_player_event("ended", None)
    assert app.db.play_count(first) == 1
    assert (app.index, app.listened, app.counted) == (1, 0.0, False)
    app.info.length = 10.0
    app.last_tick = 10.0
    play_for(app, 6)
    app.on_player_event("ended", None)
    assert app.db.play_count(app.playlist[1]) == 1


def test_restarting_a_track_is_another_play(app):
    app.on_button("X", btn.HOLD, 1.0)
    app.on_player_event("ended", None)
    app.skip(-1)
    app.counted = True
    app.player.position = 10
    app.on_button("Y", btn.HOLD, 1.0)
    assert not app.counted


def test_whether_a_play_was_counted_survives_restarts(app, make_app):
    app.on_button("X", btn.HOLD, 1.0)
    app.info.length = 100.0
    app.last_tick = 10.0
    play_for(app, 60)
    app.save_state()
    resumed = make_app()
    assert resumed.counted and resumed.listened >= 50
    resumed.on_player_event("ended", None)
    assert resumed.db.play_count(app.playlist[0]) == 1


# ---- settings ---------------------------------------------------------------------


def open_entry(app, entry):
    app.screen = MENU
    app.menu_cursor = app.menu.index(entry)
    tap(app, "X")


def test_settings_syncs_the_library(app, music):
    make_wav(music / "new.wav")
    open_entry(app, SETTINGS_ENTRY)
    assert app.screen == SETTINGS
    assert app.settings_rows()[0].status == "5 tracks"
    app.render()
    tap(app, "X")
    assert app.indexer.running or app.indexer.last_count == 6
    app.indexer.join()
    assert not app.syncing
    app.tick(5.0)
    assert app.message == "Synced 6 tracks"
    assert app.settings_rows()[0].status == "6 tracks"
    app.render()
    tap(app, "Y")
    assert app.screen == MENU
    assert app.menu[app.menu_cursor] is SETTINGS_ENTRY


def test_syncing_from_settings_pauses_playback(app, monkeypatch):
    tap(app, "B")
    tap(app, "B")
    tap(app, "X")  # loose.wav
    assert app.player.state == PLAYING
    monkeypatch.setattr(app.indexer, "start", lambda: True)
    open_entry(app, SETTINGS_ENTRY)
    tap(app, "X")
    assert app.player.state == PAUSED


def pretend_syncing(app, monkeypatch, done, total):
    app.indexer.done, app.indexer.total = done, total
    monkeypatch.setattr(type(app.indexer), "running", property(lambda self: True))


def test_buttons_are_locked_while_syncing(app, monkeypatch):
    pretend_syncing(app, monkeypatch, 3, 10)
    for name in "ABXY":
        tap(app, name)
    assert app.screen == BROWSER
    assert app.cursor == 0
    assert app.player.loaded == []
    monkeypatch.undo()
    tap(app, "B")
    assert app.cursor == 1  # unlocked once it's done


def test_sync_progress_is_shown_and_redrawn(app, monkeypatch):
    shown = []
    monkeypatch.setattr(app.ui, "indexing", lambda done, total, battery: shown.append((done, total)))
    pretend_syncing(app, monkeypatch, 42, 0)
    app.display.images.clear()
    app.tick(5.0)
    app.indexer.done, app.indexer.total = 7, 50
    app.tick(5.5)  # too soon to redraw
    app.tick(6.0)
    assert shown == [(42, 0), (7, 50)]


def test_screen_is_redrawn_when_a_sync_ends_even_if_it_failed(app, monkeypatch):
    pretend_syncing(app, monkeypatch, 1, 2)
    app.tick(5.0)
    monkeypatch.undo()  # stopped, without finishing
    app.display.images.clear()
    app.tick(5.1)
    assert app.display.images


def test_power_off_works_while_syncing(app, monkeypatch):
    pretend_syncing(app, monkeypatch, 1, 2)
    app.on_button(btn.COMBO, btn.HOLD, 1.0)
    assert not app.running


def test_sync_screen_draws(app):
    for done, total in ((0, 0), (12, 0), (0, 100), (1, 100), (100, 100)):
        assert app.ui.indexing(done, total, None).size == app.ui.message("x").size


# ---- playlists --------------------------------------------------------------------


def test_playlists_play_what_matches(app, cfg):
    cfg.playlists_dir.mkdir()
    (cfg.playlists_dir / "Album one.txt").write_text("folder: Album 1\nsort: title\nlimit: 2")
    (cfg.playlists_dir / "Nothing.txt").write_text("artist: nobody")
    open_entry(app, PLAYLISTS_ENTRY)
    assert app.screen == PLAYLISTS
    assert [p.name for p in app.playlists] == ["Album one", "Nothing"]
    app.render()
    tap(app, "B")
    tap(app, "X")
    assert app.screen == PLAYLISTS
    assert app.message.startswith("No tracks match")
    app.render()
    tap(app, "A")
    tap(app, "X")
    assert app.screen == NOW_PLAYING
    assert names(app.playlist) == ["01 First.wav", "02 Second.wav"]


def test_playlists_by_play_count(app, cfg, music):
    cfg.playlists_dir.mkdir()
    (cfg.playlists_dir / "Top.txt").write_text("sort: most played\nmin plays: 1")
    app.db.record_play(music / "loose.wav")
    app.db.record_play(music / "loose.wav")
    app.db.record_play(music / "Album 1" / "10 Tenth.wav")
    open_entry(app, PLAYLISTS_ENTRY)
    tap(app, "X")
    assert names(app.playlist) == ["loose.wav", "10 Tenth.wav"]


def test_no_playlists(app):
    open_entry(app, PLAYLISTS_ENTRY)
    assert app.playlists == []
    tap(app, "X")
    assert app.screen == PLAYLISTS
    app.render()
    tap(app, "Y")
    assert app.screen == MENU
    assert app.menu[app.menu_cursor] is PLAYLISTS_ENTRY


def test_runs_without_a_database(cfg, tmp_path):
    (tmp_path / "blocker").write_text("")
    a = App(replace(cfg, database=tmp_path / "blocker" / "library.db"), FakeDisplay(),
            FakeButtons(), None, FakePlayer(), simulate=True)
    a.start()
    assert a.menu == [FILES_ENTRY, SETTINGS_ENTRY]
    assert a.sync_status() == "Unavailable"
    open_entry(a, SETTINGS_ENTRY)
    tap(a, "X")
    assert "couldn't be opened" in a.message
    a.render()
    a.close()
