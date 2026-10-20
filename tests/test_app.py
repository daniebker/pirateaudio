import json

import pytest

from pirateplayer import app as app_module
from pirateplayer import buttons as btn
from pirateplayer.app import BROWSER, LOW_BATTERY_READINGS, NOW_PLAYING, VOLUME_STEP, App
from pirateplayer.battery import BatteryStatus
from pirateplayer.player import PAUSED, PLAYING, STOPPED

from .conftest import FakeButtons, FakeDisplay, FakePlayer


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

    def make(battery=None):
        a = App(cfg, FakeDisplay(), FakeButtons(), battery, FakePlayer(), simulate=True)
        a.start()
        return a
    return make


@pytest.fixture
def app(make_app):
    return make_app()


def names(paths):
    return [p.name for p in paths]


def tap(app, name, now=1.0):
    app.on_button(name, btn.TAP, now)


def test_starts_in_browser(app, music):
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


def test_y_at_top_goes_to_now_playing_only_with_playlist(app):
    tap(app, "Y")
    assert app.screen == BROWSER
    app.on_button("X", btn.HOLD, 1.0)
    tap(app, "Y")
    assert app.screen == BROWSER
    assert app.cwd.name == "Album 1"
    assert app.cursor == 0
    tap(app, "Y")
    app.on_button("Y", btn.HOLD, 1.0)
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
    a = make_app()
    assert a.screen == BROWSER
    assert a.playlist == []


def test_open_dir_outside_root_goes_to_root(app, music, tmp_path):
    app.open_dir(tmp_path)
    assert app.cwd == music
