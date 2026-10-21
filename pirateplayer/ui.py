"""Drawing the menu, browser, now-playing, playlist, settings, Bluetooth and sync screens."""

from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .battery import BatteryStatus
from .bluetooth import BtDevice
from .display import SIZE
from .library import TrackInfo

BG = (14, 15, 20)
FG = (236, 236, 240)
DIM = (140, 142, 155)
ACCENT = (255, 168, 38)
SELECTED = (52, 54, 68)
LOW = (235, 70, 60)
GOOD = (90, 200, 110)
BLUE = (80, 150, 255)

HEADER = 28
ROW = 26
ROWS = (SIZE - HEADER) // ROW
ART = 104

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


class MenuEntry(NamedTuple):
    """A row of the top-level menu, which opens another screen."""
    label: str
    icon: str  # "folder", "playlist", "settings" or "bluetooth"


class Row(NamedTuple):
    """A row of a list screen: a label, and a status at the right."""
    label: str
    status: str = ""
    colour: tuple = FG
    status_colour: tuple = DIM


def _font(name: str, size: int):
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default(size)


_MEASURE = ImageDraw.Draw(Image.new("RGB", (1, 1)))


@lru_cache(maxsize=2048)
def _fit(text: str, font, width: float) -> str:
    """Shorten text with an ellipsis to fit width. Measuring text is slow on a Pi Zero, so this
    searches for the cut point instead of trimming a character at a time, and remembers results:
    the same folder names are drawn over and over."""
    if _MEASURE.textlength(text, font=font) <= width:
        return text
    fits, too_long = 0, len(text)  # the longest prefix known to fit, the shortest known not to
    while too_long - fits > 1:
        middle = (fits + too_long) // 2
        if _MEASURE.textlength(text[:middle] + "…", font=font) <= width:
            fits = middle
        else:
            too_long = middle
    return text[:fits].rstrip() + "…"


def _top(cursor: int, count: int) -> int:
    """The first row to show so the cursor stays roughly in the middle of a scrolling list."""
    return min(max(0, cursor - ROWS // 2), max(0, count - ROWS))


def _scrollbar(draw, top: int, count: int):
    if count > ROWS:
        track = SIZE - HEADER
        bar = max(12, track * ROWS // count)
        y = HEADER + (track - bar) * top // (count - ROWS)
        draw.rectangle((SIZE - 4, y, SIZE - 1, y + bar), fill=DIM)


def _bluetooth_icon(draw, x: int, y: int, colour, h: int = 12):
    """The Bluetooth rune, h pixels tall with its spine at x."""
    w = h // 4
    draw.line([(x - w, y + h * 3 // 4), (x + w, y + h // 4), (x, y), (x, y + h),
               (x + w, y + h * 3 // 4), (x - w, y + h // 4)], fill=colour, width=1)


def _folder_icon(draw, x: int, y: int, colour):
    """A folder, 16x12 pixels with its top-left corner at x, y."""
    draw.rectangle((x, y, x + 6, y + 2), fill=colour)
    draw.rectangle((x, y + 2, x + 16, y + 12), fill=colour)


def _playlist_icon(draw, x: int, y: int, colour):
    """Three lines of a list, 16x12 pixels with its top-left corner at x, y."""
    for i in range(3):
        draw.rectangle((x, y + i * 5, x + 16, y + i * 5 + 2), fill=colour)


def _settings_icon(draw, x: int, y: int, colour):
    """A cog, 14 pixels across with its top-left corner at x, y."""
    cx, cy = x + 7, y + 7
    draw.line((cx, y, cx, y + 14), fill=colour, width=3)
    draw.line((x, cy, x + 14, cy), fill=colour, width=3)
    draw.line((x + 2, y + 2, x + 12, y + 12), fill=colour, width=3)
    draw.line((x + 2, y + 12, x + 12, y + 2), fill=colour, width=3)
    draw.ellipse((x + 2, y + 2, x + 12, y + 12), fill=colour)
    draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=BG)


def _clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"


class Renderer:
    def __init__(self):
        self.small = _font("DejaVuSans.ttf", 13)
        self.text = _font("DejaVuSans.ttf", 16)
        self.bold = _font("DejaVuSans-Bold.ttf", 18)
        self.note = _font("DejaVuSans.ttf", 64)
        self._art_cache = (None, None)  # (source image, resized image)

    def _canvas(self):
        image = Image.new("RGB", (SIZE, SIZE), BG)
        return image, ImageDraw.Draw(image)

    def _battery(self, draw, battery: BatteryStatus | None, right: int) -> int:
        """Draw the battery gauge ending at x=right; return the x where it starts."""
        if battery is None:
            return right
        x, y, w, h = right - 24, 9, 21, 11
        colour = LOW if battery.percent < 15 and not battery.external_power else FG
        draw.rectangle((x, y, x + w, y + h), outline=colour)
        draw.rectangle((x + w + 1, y + 3, x + w + 2, y + h - 3), fill=colour)
        fill = int((w - 3) * battery.percent / 100)
        if fill > 0:
            draw.rectangle((x + 2, y + 2, x + 2 + fill, y + h - 2),
                           fill=GOOD if battery.external_power else colour)
        label = f"{battery.percent:.0f}%"
        lx = x - 4 - draw.textlength(label, font=self.small)
        draw.text((lx, 8), label, font=self.small, fill=DIM)
        return int(lx)

    def _header(self, draw, title: str, battery, extra: str = "", bluetooth: bool = False):
        draw.rectangle((0, 0, SIZE, HEADER - 2), fill=(26, 27, 36))
        right = self._battery(draw, battery, SIZE - 4)
        if extra:
            right -= int(draw.textlength(extra, font=self.small)) + 8
            draw.text((right, 8), extra, font=self.small, fill=ACCENT)
        if bluetooth:  # playing through a headset
            right -= 14
            _bluetooth_icon(draw, right + 5, 7, BLUE)
        draw.text((6, 5), _fit(title, self.bold, right - 12), font=self.bold, fill=FG)

    def menu(self, entries: list[MenuEntry], cursor: int, status: dict[MenuEntry, str],
             battery: BatteryStatus | None, playing: bool, bluetooth: bool = False) -> Image.Image:
        """The top-level menu. `status` is shown at the right of the rows it has text for."""
        image, draw = self._canvas()
        self._header(draw, "Menu", battery, "▶" if playing else "", bluetooth)
        for row, entry in enumerate(entries):
            y = HEADER + row * ROW
            if row == cursor:
                draw.rectangle((0, y, SIZE, y + ROW - 1), fill=SELECTED)
            if entry.icon == "bluetooth":
                colour = BLUE
                _bluetooth_icon(draw, 16, y + 6, colour, h=14)
            elif entry.icon == "playlist":
                colour = GOOD
                _playlist_icon(draw, 8, y + 7, colour)
            elif entry.icon == "settings":
                colour = DIM
                _settings_icon(draw, 9, y + 6, colour)
            else:
                colour = ACCENT
                _folder_icon(draw, 8, y + 7, colour)
            draw.text((32, y + 4), entry.label, font=self.text, fill=colour)
            text = status.get(entry, "")
            if text:
                left = 40 + int(draw.textlength(entry.label, font=self.text))
                text = _fit(text, self.small, SIZE - 10 - left)
                draw.text((SIZE - 10, y + 6), text, font=self.small, fill=GOOD, anchor="ra")
        return image

    def browser(self, title: str, entries: list[Path], cursor: int,
                battery: BatteryStatus | None, playing: bool, bluetooth: bool = False) -> Image.Image:
        image, draw = self._canvas()
        self._header(draw, title, battery, "▶" if playing else "", bluetooth)
        if not entries:
            draw.text((SIZE // 2, SIZE // 2), "No music here", font=self.text, fill=DIM, anchor="mm")
            return image

        top = _top(cursor, len(entries))
        for row, entry in enumerate(entries[top:top + ROWS]):
            y = HEADER + row * ROW
            if top + row == cursor:
                draw.rectangle((0, y, SIZE, y + ROW - 1), fill=SELECTED)
            is_dir = entry.is_dir()
            label = entry.name + "/" if is_dir else entry.stem
            draw.text((8, y + 4), _fit(label, self.text, SIZE - 22), font=self.text,
                      fill=ACCENT if is_dir else FG)
        _scrollbar(draw, top, len(entries))
        return image

    def list_screen(self, title: str, rows: list[Row], cursor: int, battery: BatteryStatus | None,
                    playing: bool, bluetooth: bool = False, empty: str = "",
                    message: str = "") -> Image.Image:
        """A scrolling list of rows, with `empty` shown if there are none."""
        image, draw = self._canvas()
        self._header(draw, title, battery, "▶" if playing else "", bluetooth)
        if not rows and empty:
            draw.multiline_text((SIZE // 2, SIZE // 2), empty, font=self.text, fill=DIM,
                                anchor="mm", align="center")
        top = _top(cursor, len(rows))
        for i, row in enumerate(rows[top:top + ROWS]):
            y = HEADER + i * ROW
            if top + i == cursor:
                draw.rectangle((0, y, SIZE, y + ROW - 1), fill=SELECTED)
            right = SIZE - 10
            if row.status:
                status = _fit(row.status, self.small, SIZE // 2)
                right -= int(draw.textlength(status, font=self.small))
                draw.text((right, y + 6), status, font=self.small, fill=row.status_colour)
            draw.text((8, y + 4), _fit(row.label, self.text, right - 16), font=self.text,
                      fill=row.colour)
        _scrollbar(draw, top, len(rows))
        if message:
            self._message_box(draw, message)
        return image

    def _message_box(self, draw, message: str):
        lines = message.count("\n") + 1
        box = 10 + 18 * lines
        draw.rectangle((0, SIZE - box, SIZE, SIZE), fill=(26, 27, 36))
        draw.multiline_text((SIZE // 2, SIZE - box // 2), message, font=self.small, fill=FG,
                            anchor="mm", align="center")

    def bluetooth(self, devices: list[BtDevice], cursor: int, scanning: bool, scan_left: float,
                  message: str, battery: BatteryStatus | None) -> Image.Image:
        """Row 0 starts or stops a scan; the rest are devices. `cursor` counts row 0."""
        image, draw = self._canvas()
        self._header(draw, "Bluetooth", battery, bluetooth=any(d.connected for d in devices))
        count = len(devices) + 1
        top = _top(cursor, count)
        for row in range(top, min(count, top + ROWS)):
            y = HEADER + (row - top) * ROW
            if row == cursor:
                draw.rectangle((0, y, SIZE, y + ROW - 1), fill=SELECTED)
            if row == 0:
                label = f"Scanning… {scan_left:.0f}s" if scanning else "Scan for headsets"
                draw.text((8, y + 4), label, font=self.text, fill=BLUE)
                continue
            device = devices[row - 1]
            if device.busy:
                status, colour = device.busy, ACCENT
            elif device.connected:
                status, colour = "Connected", GOOD
            elif device.paired:
                status, colour = "Paired", DIM
            else:
                status, colour = "", DIM
            right = SIZE - 10
            if status:
                right -= int(draw.textlength(status, font=self.small))
                draw.text((right, y + 6), status, font=self.small, fill=colour)
            draw.text((8, y + 4), _fit(device.name, self.text, right - 16), font=self.text,
                      fill=FG)
        _scrollbar(draw, top, count)

        if scanning and not any(not d.paired for d in devices) and count < ROWS:
            draw.multiline_text((SIZE // 2, HEADER + count * ROW + 30),
                                "Put your headset\nin pairing mode", font=self.text, fill=DIM,
                                anchor="ma", align="center")
        if message:
            self._message_box(draw, message)
        return image

    def now_playing(self, info: TrackInfo | None, state: str, position: float, volume: int,
                    index: int, count: int, battery: BatteryStatus | None,
                    bluetooth: bool = False) -> Image.Image:
        image, draw = self._canvas()
        self._header(draw, f"{index + 1}/{count}" if count else "Nothing playing",
                     battery, f"vol {volume}%", bluetooth)
        if info is None:
            draw.text((SIZE // 2, 120), "Press Y to browse", font=self.text, fill=DIM, anchor="mm")
            return image

        art_x, art_y = (SIZE - ART) // 2, HEADER + 6
        if info.art is not None:
            if self._art_cache[0] is not info.art:
                self._art_cache = (info.art, ImageOps.fit(info.art, (ART, ART)))
            image.paste(self._art_cache[1], (art_x, art_y))
        else:
            draw.rounded_rectangle((art_x, art_y, art_x + ART, art_y + ART), radius=8, fill=SELECTED)
            draw.text((SIZE // 2, art_y + ART // 2), "♪", font=self.note, fill=DIM, anchor="mm")

        y = art_y + ART + 6
        draw.text((SIZE // 2, y), _fit(info.title, self.bold, SIZE - 12),
                  font=self.bold, fill=FG, anchor="mt")
        for line in (info.artist, info.album):
            y += 22
            if line:
                draw.text((SIZE // 2, y), _fit(line, self.text, SIZE - 12),
                          font=self.text, fill=DIM, anchor="mt")

        bar_y = 212
        draw.rectangle((10, bar_y, SIZE - 10, bar_y + 3), fill=SELECTED)
        if info.length:
            done = int((SIZE - 20) * min(1.0, position / info.length))
            draw.rectangle((10, bar_y, 10 + done, bar_y + 3), fill=ACCENT)
        draw.text((10, bar_y + 8), _clock(position), font=self.small, fill=DIM)
        draw.text((SIZE - 10, bar_y + 8), _clock(info.length), font=self.small, fill=DIM, anchor="ra")

        cx, cy = SIZE // 2, bar_y + 15
        if state == "playing":
            draw.polygon([(cx - 4, cy - 6), (cx - 4, cy + 6), (cx + 6, cy)], fill=FG)
        elif state == "paused":
            draw.rectangle((cx - 5, cy - 6, cx - 2, cy + 6), fill=FG)
            draw.rectangle((cx + 2, cy - 6, cx + 5, cy + 6), fill=FG)
        else:
            draw.rectangle((cx - 5, cy - 5, cx + 5, cy + 5), fill=FG)
        return image

    def indexing(self, done: int, total: int, battery: BatteryStatus | None) -> Image.Image:
        """The library being synced. `total` is 0 while tracks are still being found."""
        image, draw = self._canvas()
        self._header(draw, "Syncing library", battery)
        cy = HEADER + (SIZE - HEADER) // 2
        if total:
            label, detail = "Reading tags", f"{done} of {total} tracks"
            fraction = min(1.0, done / total)
        else:
            label, detail = "Finding music", f"{done} tracks so far"
            fraction = 0.0
        draw.text((SIZE // 2, cy - 34), label, font=self.bold, fill=FG, anchor="mm")
        draw.rounded_rectangle((20, cy - 8, SIZE - 20, cy + 8), radius=8, fill=SELECTED)
        filled = int((SIZE - 40) * fraction)
        if filled >= 16:  # narrower than that, the rounded ends would overlap
            draw.rounded_rectangle((20, cy - 8, 20 + filled, cy + 8), radius=8, fill=ACCENT)
        draw.text((SIZE // 2, cy + 26), detail, font=self.text, fill=DIM, anchor="mm")
        draw.text((SIZE // 2, SIZE - 20), "Buttons work again when it's done", font=self.small,
                  fill=DIM, anchor="mm")
        return image

    def message(self, text: str) -> Image.Image:
        image, draw = self._canvas()
        draw.multiline_text((SIZE // 2, SIZE // 2), text, font=self.bold, fill=FG,
                            anchor="mm", align="center")
        return image
