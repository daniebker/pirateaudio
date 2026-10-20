import io
import queue
import struct
import wave
from pathlib import Path

import pytest
from PIL import Image

from pirateplayer.config import Config
from pirateplayer.player import PAUSED, PLAYING, STOPPED


def make_wav(path: Path, seconds: float = 0.1, info: dict[bytes, str] | None = None) -> Path:
    """Write a short silent WAV, optionally with a RIFF LIST/INFO chunk appended."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 8000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))
    if info:
        body = b"INFO"
        for key, value in info.items():
            data = value.encode() + b"\0"
            body += key + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")
        raw = path.read_bytes()
        raw += b"LIST" + struct.pack("<I", len(body)) + body
        raw = raw[:4] + struct.pack("<I", len(raw) - 8) + raw[8:]
        path.write_bytes(raw)
    return path


def png_bytes(colour=(255, 0, 0), size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


class FakePlayer:
    """Stands in for the mpv Player, recording what the app asked it to do."""

    def __init__(self):
        self.events: queue.Queue = queue.Queue()
        self.state = STOPPED
        self.position = 0.0
        self.volume = None
        self.loaded: list[tuple[Path, float, bool]] = []
        self.seeks: list[float] = []
        self.polls = 0

    def load(self, path, start=0.0, paused=False):
        self.loaded.append((path, start, paused))
        self.position = start
        self.state = PAUSED if paused else PLAYING

    def toggle_pause(self):
        if self.state == PLAYING:
            self.state = PAUSED
        elif self.state == PAUSED:
            self.state = PLAYING

    def stop(self):
        self.state = STOPPED

    def seek(self, seconds):
        self.seeks.append(seconds)
        self.position = seconds

    def set_volume(self, percent):
        self.volume = percent

    def poll_position(self):
        self.polls += 1


class FakeDisplay:
    def __init__(self):
        self.images = []
        self.backlight = None

    def show(self, image):
        self.images.append(image)

    def set_backlight(self, on):
        self.backlight = on


class FakeButtons:
    def __init__(self):
        self.pending = []

    def poll(self):
        events, self.pending = self.pending, []
        return events


@pytest.fixture
def music(tmp_path) -> Path:
    """A small library:  Music/{Album 1/{01,02,10}.wav, Album 2/Disc 1/a.wav, loose.wav}"""
    root = tmp_path / "Music"
    for name in ("01 First.wav", "02 Second.wav", "10 Tenth.wav"):
        make_wav(root / "Album 1" / name)
    make_wav(root / "Album 2" / "Disc 1" / "a.wav")
    make_wav(root / "loose.wav")
    return root


@pytest.fixture
def cfg(tmp_path, music) -> Config:
    return Config(music_dir=music, state_file=tmp_path / "state" / "state.json")
