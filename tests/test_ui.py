import pytest
from PIL import Image, ImageDraw

from pirateplayer.battery import BatteryStatus
from pirateplayer.bluetooth import PAIRING, BtDevice
from pirateplayer.display import SIZE
from pirateplayer.library import TrackInfo
from pirateplayer.ui import BLUE, MenuEntry, Renderer, _clock, _fit


@pytest.mark.parametrize("seconds,text", [(0, "0:00"), (-3, "0:00"), (59.9, "0:59"), (61, "1:01"),
                                          (3600, "1:00:00"), (3725, "1:02:05")])
def test_clock(seconds, text):
    assert _clock(seconds) == text


def test_fit():
    r = Renderer()
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    assert _fit("short", r.text, 200) == "short"
    long = "A very long title that will not fit on the screen at all"
    fitted = _fit(long, r.text, 100)
    assert fitted.endswith("…")
    assert draw.textlength(fitted, font=r.text) <= 100
    # As long as fits: one more character would be too wide.
    assert draw.textlength(long[:len(fitted)] + "…", font=r.text) > 100
    assert _fit(long, r.text, 5) == "…"


def check(image):
    assert image.size == (SIZE, SIZE)
    assert image.mode == "RGB"


def test_browser(music):
    r = Renderer()
    entries = sorted(music.rglob("*"))
    check(r.browser("Music", entries, 0, BatteryStatus(80, False), playing=True))
    check(r.browser("Music", entries * 5, 20, BatteryStatus(5, True), playing=False))
    check(r.browser("Empty", [], 0, None, playing=False))
    check(r.browser("Music", entries, 1, None, playing=False, bluetooth=True))


def test_menu():
    r = Renderer()
    files, bluetooth = MenuEntry("Files", "folder"), MenuEntry("Bluetooth", "bluetooth")
    check(r.menu([files, bluetooth], 0, {}, BatteryStatus(80, False), playing=False))
    image = r.menu([files, bluetooth], 1, {bluetooth: "A headset with a very long name indeed"},
                   None, playing=True, bluetooth=True)
    check(image)
    assert BLUE in image.crop((0, 54, SIZE, 80)).getdata()  # the Bluetooth row's icon


def test_bluetooth_icon_in_header(music):
    r = Renderer()
    header = (120, 0, SIZE, 26)
    plain = r.browser("Music", [], 0, None, playing=False).crop(header)
    with_bt = r.browser("Music", [], 0, None, playing=False, bluetooth=True).crop(header)
    assert BLUE not in plain.getdata()
    assert BLUE in with_bt.getdata()


def test_bluetooth_screen():
    r = Renderer()
    devices = [BtDevice("00:00:00:00:00:01", "Headset", paired=True, connected=True),
               BtDevice("00:00:00:00:00:02", "A speaker with a very long name indeed", paired=True),
               BtDevice("00:00:00:00:00:03", "Earbuds", busy=PAIRING)]
    check(r.bluetooth([], 0, scanning=True, scan_left=29.6, message="", battery=None))
    check(r.bluetooth(devices, 2, scanning=False, scan_left=0, message="Couldn't pair:\nrefused",
                      battery=BatteryStatus(50, False)))
    check(r.bluetooth(devices * 5, 14, scanning=True, scan_left=3, message="", battery=None))


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
