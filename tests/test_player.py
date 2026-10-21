import json
import queue
import threading

import pytest

from pirateplayer.player import PAUSED, PLAYING, STOPPED, Player, _POSITION_REQUEST


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.extend(json.loads(line)["command"] for line in data.decode().splitlines())


@pytest.fixture
def player():
    """A Player with mpv replaced by a socket that records commands."""
    p = Player.__new__(Player)
    p._sock = FakeSocket()
    p.events = queue.Queue()
    p.state = STOPPED
    p.position = 0.0
    p._loading = False
    p._lock = threading.Lock()
    p._queue_lock = threading.RLock()
    p._loaded = False
    p._next = None
    p._queued = False
    return p


def events(player):
    out = []
    while not player.events.empty():
        out.append(player.events.get())
    return out


def test_load_sends_commands(player, tmp_path):
    player.load(tmp_path / "a.mp3", start=12.34, paused=True)
    assert player._sock.sent == [
        ["set_property", "start", "12.3"],
        ["set_property", "pause", True],
        ["loadfile", str(tmp_path / "a.mp3"), "replace"],
    ]
    assert player.state == PAUSED
    assert player.position == 12.34


def test_end_file_while_loading_is_ignored(player, tmp_path):
    player.load(tmp_path / "a.mp3")
    player._handle({"event": "end-file", "reason": "eof"})  # the previous track ending
    assert events(player) == []
    assert player.state == PLAYING
    player._handle({"event": "start-file"})
    player._handle({"event": "end-file", "reason": "eof"})
    assert events(player) == [("ended", None)]
    assert player.state == STOPPED


def test_end_file_error(player):
    player.state = PLAYING
    player._handle({"event": "end-file", "reason": "error", "file_error": "unrecognized file format"})
    assert events(player) == [("error", "unrecognized file format")]
    assert player.state == STOPPED


def test_end_file_stop_is_not_an_event(player):
    player.state = PLAYING
    player._handle({"event": "end-file", "reason": "stop"})
    assert events(player) == []


def test_pause_property(player):
    player.state = PLAYING
    player._handle({"event": "property-change", "name": "pause", "data": True})
    assert player.state == PAUSED
    player._handle({"event": "property-change", "name": "pause", "data": False})
    assert player.state == PLAYING
    player.state = STOPPED
    player._handle({"event": "property-change", "name": "pause", "data": False})
    assert player.state == STOPPED


def test_position_reply(player):
    player._handle({"request_id": _POSITION_REQUEST, "data": 42.5, "error": "success"})
    assert player.position == 42.5
    player._handle({"request_id": _POSITION_REQUEST, "data": None, "error": "property unavailable"})
    assert player.position == 42.5


def test_poll_position_only_when_active(player):
    player.poll_position()
    assert player._sock.sent == []
    player.state = PLAYING
    player.poll_position()
    assert player._sock.sent == [["get_property", "time-pos"]]


def test_controls(player):
    player.toggle_pause()
    assert player._sock.sent == []  # nothing to pause
    player.state = PLAYING
    player.toggle_pause()
    player.seek(-5)
    player.set_volume(30)
    player.stop()
    assert player._sock.sent == [["cycle", "pause"], ["seek", 0.0, "absolute"],
                                 ["set_property", "volume", 30], ["stop"]]
    assert player.state == STOPPED
    assert player.position == 0.0


def test_pause_and_audio_device(player, tmp_path):
    player.pause()  # nothing loaded: nothing to pause
    assert player._sock.sent == []
    player.load(tmp_path / "a.mp3")
    player._sock.sent.clear()
    player.pause()
    player.pause()  # pausing twice stays paused, unlike toggling
    assert player.state == PAUSED
    assert player._sock.sent == [["set_property", "pause", True]] * 2
    player.set_audio_device("alsa/bluealsa:DEV=00:1A:7D:DA:71:13,PROFILE=a2dp")
    player.set_audio_device("")
    assert player._sock.sent[-2:] == [
        ["set_property", "audio-device", "alsa/bluealsa:DEV=00:1A:7D:DA:71:13,PROFILE=a2dp"],
        ["set_property", "audio-device", "auto"],
    ]


def test_queue_waits_for_the_track_to_load(player, tmp_path):
    player.load(tmp_path / "a.mp3", start=30.0)
    player.queue(tmp_path / "b.mp3")
    assert player._sock.sent[-1][0] == "loadfile"  # not yet: "start" would apply to it too
    player._sock.sent.clear()
    player._handle({"event": "start-file"})
    player._handle({"event": "file-loaded"})
    assert player._sock.sent == [["set_property", "start", "0"],
                                 ["loadfile", str(tmp_path / "b.mp3"), "append-play"]]


def test_queued_track_follows_without_stopping(player, tmp_path):
    player.load(tmp_path / "a.mp3")
    player._handle({"event": "start-file"})
    player._handle({"event": "file-loaded"})
    player.queue(tmp_path / "b.mp3")
    player.position = 200.0
    player._handle({"event": "end-file", "reason": "eof"})
    assert events(player) == [("next", None)]
    assert player.state == PLAYING
    assert player.position == 0.0
    player._handle({"event": "start-file"})
    player._sock.sent.clear()
    player.queue(tmp_path / "c.mp3")  # before mpv has opened b: waits for it
    assert player._sock.sent == []
    player._handle({"event": "file-loaded"})
    assert player._sock.sent[-1] == ["loadfile", str(tmp_path / "c.mp3"), "append-play"]
    player._handle({"event": "end-file", "reason": "eof"})
    player._handle({"event": "start-file"})
    player._handle({"event": "file-loaded"})
    player._handle({"event": "end-file", "reason": "eof"})  # nothing queued after c
    assert events(player) == [("next", None), ("ended", None)]
    assert player.state == STOPPED


def test_unqueue(player, tmp_path):
    player.load(tmp_path / "a.mp3")
    player._handle({"event": "start-file"})
    player._handle({"event": "file-loaded"})
    player.queue(tmp_path / "b.mp3")
    player._sock.sent.clear()
    player.queue(None)
    assert player._sock.sent == [["playlist-clear"]]
    player._handle({"event": "end-file", "reason": "eof"})
    assert events(player) == [("ended", None)]
