import pytest
from PIL import Image, ImageDraw

from pirateplayer.battery import BatteryStatus
from pirateplayer.display import SIZE
from pirateplayer.library import TrackInfo
from pirateplayer.ui import Renderer, _clock, _fit


@pytest.mark.parametrize("seconds,text", [(0, "0:00"), (-3, "0:00"), (59.9, "0:59"), (61, "1:01"),
                                          (3600, "1:00:00"), (3725, "1:02:05")])
def test_clock(seconds, text):
    assert _clock(seconds) == text


def test_fit():
    r = Renderer()
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    assert _fit(draw, "short", r.text, 200) == "short"
    long = "A very long title that will not fit on the screen at all"
    fitted = _fit(draw, long, r.text, 100)
    assert fitted.endswith("…")
    assert draw.textlength(fitted, font=r.text) <= 100


def check(image):
    assert image.size == (SIZE, SIZE)
    assert image.mode == "RGB"


def test_browser(music):
    r = Renderer()
    entries = sorted(music.rglob("*"))
    check(r.browser("Music", entries, 0, BatteryStatus(80, False), playing=True))
    check(r.browser("Music", entries * 5, 20, BatteryStatus(5, True), playing=False))
    check(r.browser("Empty", [], 0, None, playing=False))


def test_now_playing():
    r = Renderer()
    art = Image.new("RGB", (300, 200), (10, 200, 10))
    info = TrackInfo("Title", "Artist", "Album", length=200, art=art)
    for state in ("playing", "paused", "stopped"):
        check(r.now_playing(info, state, 50, 40, 0, 3, BatteryStatus(10, False)))
    image = r.now_playing(info, "playing", 500, 40, 2, 3, None)  # position past the end
    assert image.getpixel((SIZE // 2, 60)) == (10, 200, 10)
    check(r.now_playing(TrackInfo("No art"), "playing", 0, 40, 0, 1, None))
    check(r.now_playing(None, "stopped", 0, 50, 0, 0, None))


def test_message():
    check(Renderer().message("Battery low\nPowering off…"))
