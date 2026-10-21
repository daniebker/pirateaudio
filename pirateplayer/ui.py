"""Drawing the menu, browser, cover flow, now-playing, playlist, settings, Bluetooth and sync
screens."""

import math
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .battery import BatteryStatus
from .bluetooth import BtDevice
from .display import SIZE
from .library import Album, TrackInfo, album_thumbnail

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
    icon: str  # "folder", "covers", "playlist", "settings" or "bluetooth"


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
def _covers_icon(draw, x: int, y: int, colour):
    """Three covers in a row, the middle one facing out: 18x14 pixels, top-left at x, y."""
    draw.polygon([(x, y + 3), (x + 4, y + 1), (x + 4, y + 13), (x, y + 11)], fill=colour)
    draw.polygon([(x + 18, y + 3), (x + 14, y + 1), (x + 14, y + 13), (x + 18, y + 11)], fill=colour)
    draw.rectangle((x + 5, y, x + 13, y + 9), fill=colour)
    draw.rectangle((x + 5, y + 11, x + 13, y + 13), fill=SELECTED)  # its reflection


# ---- cover flow -----------------------------------------------------------------
#
# Each cover is a square in 3D, seen through a perspective camera. The one in the middle
# faces the screen; the others are turned to face the middle and pushed back, stacked up
# either side. A cover's place comes from its distance from the middle, which is fractional
# while the covers are moving, so they swing round smoothly as they flow past.

COVER = 108           # the middle cover's size on screen
REFLECTION = 34       # how much of its mirror image shows on the floor below it
COVER_TOP = HEADER + 8
FOCAL = 260           # camera distance: smaller exaggerates the perspective
TURN = math.radians(72)
SIDE_DEPTH = 70       # how far behind the middle cover the others stand
SIDE_GAP = 80         # from the middle of the screen to the middle of the first side cover
SIDE_STEP = 24        # between side covers
SIDE_COVERS = 4       # drawn either side of the middle
SIDE_SCALE = FOCAL / (FOCAL + SIDE_DEPTH)  # perspective's shrinking of anything at side depth
STEPS = 8             # covers turning through the middle are drawn at 1/STEPS of the way
CAPTION_TOP = COVER_TOP + COVER + REFLECTION + 4


def _packed(image: Image.Image):
    """An image's pixels as single 32-bit numbers, which numpy moves about many times faster
    on a Pi Zero than separate bytes. Image.frombytes reads them back as raw "RGBX"."""
    import numpy

    return numpy.frombuffer(image.tobytes("raw", "RGBX"), numpy.uint32).reshape(
        image.height, image.width)


def _cover_texture(face: Image.Image | None):
    """The cover, COVER pixels square, with its reflection fading out below it, as a numpy
    array of packed pixels."""
    import numpy

    face = numpy.asarray(face if face is not None else _placeholder())
    fade = numpy.linspace(90, 0, REFLECTION).astype(numpy.uint16)[:, None, None]  # of 256
    mirror = (face[::-1][:REFLECTION] * fade + numpy.array(BG, numpy.uint16) * (256 - fade)) >> 8
    return _packed(Image.fromarray(numpy.concatenate([face, mirror.astype(numpy.uint8)])))


@lru_cache(maxsize=1)
def _placeholder() -> Image.Image:
    face = Image.new("RGB", (COVER, COVER), SELECTED)
    ImageDraw.Draw(face).text((COVER // 2, COVER // 2), "♪", font=_font("DejaVuSans.ttf", 64),
                              fill=DIM, anchor="mm")
    return face


def _placement(offset: float) -> tuple[float, float, float]:
    """Where a cover `offset` places from the middle stands: x and depth of its centre, and
    its turn (positive turns its left edge away from the camera, to face the left)."""
    side = math.copysign(1, offset)
    near = min(1.0, abs(offset))  # 0 in the middle, 1 once it has swung round to the side
    x = side * (near * SIDE_GAP + max(0.0, abs(offset) - 1) * SIDE_STEP)
    return x, -near * SIDE_DEPTH, side * near * TURN


def _project(offset: float) -> list[tuple[float, float]]:
    """The cover's corners on screen: top-left, top-right, bottom-right, bottom-left."""
    x, z, turn = _placement(offset)
    half, mid_y = COVER / 2, COVER_TOP + COVER / 2
    corners = []
    for cx, cy in ((-half, -half), (half, -half), (half, half + REFLECTION), (-half, half + REFLECTION)):
        px, pz = x + cx * math.cos(turn), z + cx * math.sin(turn)
        scale = FOCAL / (FOCAL - pz)
        corners.append((SIZE / 2 + px * scale, mid_y + cy * scale))
    return corners


@lru_cache(maxsize=None)  # there are only 2 * STEPS + 1 offsets it's asked for
def _warp_map(offset: float):
    """Which texture pixel each screen pixel shows, for a cover `offset` places from the
    middle: indexes into the flattened texture, which pixels are the cover's at all, and the
    top-left corner of the box they're in.

    The covers only turn about an upright axis, so each column of the screen shows part of a
    single column of the texture, stretched to that column's height. Working out which, once,
    makes drawing any album there a single numpy lookup: Pillow's general perspective
    transform takes a Pi Zero most of a second."""
    import numpy

    if offset < 0:  # the mirror image of the same place on the right
        rows, inside, (left, top) = _warp_map(-offset)
        right = SIZE - left  # the screen is mirrored about its middle
        rows = rows[:, ::-1] // COVER * COVER + (COVER - 1 - rows[:, ::-1] % COVER)
        return rows, inside[:, ::-1], (right - inside.shape[1], top)

    x, z, turn = _placement(offset)
    corners = _project(offset)
    left, top = math.floor(min(cx for cx, _ in corners)), math.floor(min(cy for _, cy in corners))
    right, bottom = math.ceil(max(cx for cx, _ in corners)), math.ceil(max(cy for _, cy in corners))
    sx = numpy.arange(left, right) + 0.5 - SIZE / 2       # pixel centres, from the middle
    sy = numpy.arange(top, bottom) + 0.5 - (COVER_TOP + COVER / 2)
    # Invert the projection of _project: the point across the cover that lands on each column,
    # and how much perspective shrinks it there.
    across = (sx * (FOCAL - z) - FOCAL * x) / (FOCAL * math.cos(turn) + sx * math.sin(turn))
    scale = FOCAL / (FOCAL - z - across * math.sin(turn))
    u = numpy.broadcast_to(across + COVER / 2, (len(sy), len(sx)))
    v = sy[:, None] / scale + COVER / 2
    inside = (u >= 0) & (u < COVER) & (v >= 0) & (v < COVER + REFLECTION)
    rows = numpy.clip(v, 0, COVER + REFLECTION - 1).astype(numpy.int32)
    cols = numpy.clip(u, 0, COVER - 1).astype(numpy.int32)
    return rows * COVER + cols, inside, (left, top)


def prepare_cover_flow():
    """Work out where the covers' pixels go for every place they're drawn, which takes a Pi
    Zero most of a second in all. Safe to call from another thread, to do it in advance."""
    for step in range(1, STEPS + 1):
        _warp_map(step / STEPS)
        _warp_map(-step / STEPS)


def _blit(frame, picture, inside, left: int, top: int):
    """Copy the pixels of picture that are `inside` onto frame (both numpy arrays), with its
    top-left corner at left, top, cutting off whatever falls outside the frame."""
    import numpy

    h, w = inside.shape
    x0, y0 = max(0, -left), max(0, -top)
    x1, y1 = min(w, SIZE - left), min(h, SIZE - top)
    if x0 < x1 and y0 < y1:
        # copyto(where=) rather than indexing with the mask: several times faster on a Pi Zero.
        numpy.copyto(frame[top + y0:top + y1, left + x0:left + x1], picture[y0:y1, x0:x1],
                     where=inside[y0:y1, x0:x1])


@lru_cache(maxsize=1)
def _blank_frame():
    import numpy

    return _packed(Image.new("RGB", (SIZE, SIZE), BG))


def _clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"


class Renderer:
    def __init__(self, cover_cache: Path | None = None):
        self.cover_cache = cover_cache  # where cover flow keeps its thumbnails
        self.small = _font("DejaVuSans.ttf", 13)
        self.text = _font("DejaVuSans.ttf", 16)
        self.bold = _font("DejaVuSans-Bold.ttf", 18)
        self.note = _font("DejaVuSans.ttf", 64)
        self._art_cache = (None, None)  # (source image, resized image)
        self._textures: dict = {}   # album: texture array, the most recently drawn last
        self._warped: dict = {}     # (album, offset): its pixels placed there
        self._headers: dict = {}    # the header strip for each title, battery level and so on
        self._captions: dict = {}   # the strip under the covers for each album

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

    def _header(self, image, title: str, battery, extra: str = "", bluetooth: bool = False):
        # Drawing text takes a Pi Zero 20ms or so a time, and the header seldom changes.
        key = (title, battery and (battery.percent, battery.external_power), extra, bluetooth)
        if key not in self._headers:
            if len(self._headers) > 32:
                self._headers.clear()
            strip = Image.new("RGB", (SIZE, HEADER), BG)
            draw = ImageDraw.Draw(strip)
            draw.rectangle((0, 0, SIZE, HEADER - 2), fill=(26, 27, 36))
            right = self._battery(draw, battery, SIZE - 4)
            if extra:
                right -= int(draw.textlength(extra, font=self.small)) + 8
                draw.text((right, 8), extra, font=self.small, fill=ACCENT)
            if bluetooth:  # playing through a headset
                right -= 14
                _bluetooth_icon(draw, right + 5, 7, BLUE)
            draw.text((6, 5), _fit(title, self.bold, right - 12), font=self.bold, fill=FG)
            self._headers[key] = strip
        image.paste(self._headers[key], (0, 0))

    def menu(self, entries: list[MenuEntry], cursor: int, status: dict[MenuEntry, str],
             battery: BatteryStatus | None, playing: bool, bluetooth: bool = False) -> Image.Image:
        """The top-level menu. `status` is shown at the right of the rows it has text for."""
        image, draw = self._canvas()
        self._header(image, "Menu", battery, "▶" if playing else "", bluetooth)
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
            elif entry.icon == "covers":
                colour = ACCENT
                _covers_icon(draw, 7, y + 6, colour)
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

    def browser(self, title: str, entries: Sequence[Path], dirs: int, cursor: int,
                battery: BatteryStatus | None, playing: bool, bluetooth: bool = False) -> Image.Image:
        """Files: the first `dirs` entries are folders, the rest tracks."""
        image, draw = self._canvas()
        self._header(image, title, battery, "▶" if playing else "", bluetooth)
        if not entries:
            draw.text((SIZE // 2, SIZE // 2), "No music here", font=self.text, fill=DIM, anchor="mm")
            return image

        top = _top(cursor, len(entries))
        for row, entry in enumerate(entries[top:top + ROWS]):
            y = HEADER + row * ROW
            if top + row == cursor:
                draw.rectangle((0, y, SIZE, y + ROW - 1), fill=SELECTED)
            is_dir = top + row < dirs  # known already: a stat per row makes scrolling lag
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
        self._header(image, title, battery, "▶" if playing else "", bluetooth)
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
    def cover_flow(self, albums: list[Album] | None, position: float,
                   battery: BatteryStatus | None, playing: bool,
                   bluetooth: bool = False) -> Image.Image:
        """Albums flowing past, `position` the (fractional, while moving) one in the middle.
        `albums` is None while they're still being found."""
        if not albums:
            image, draw = self._canvas()
            text = "Finding albums…" if albums is None else "No albums with covers"
            draw.text((SIZE // 2, SIZE // 2), text, font=self.text, fill=DIM, anchor="mm")
        else:
            frame = _blank_frame().copy()
            middle = min(len(albums) - 1, max(0, round(position)))
            first = max(0, middle - SIDE_COVERS)
            last = min(len(albums) - 1, middle + SIDE_COVERS)
            # Furthest first, so the nearer covers overlap them.
            for index in sorted(range(first, last + 1), key=lambda i: -abs(i - position)):
                offset = index - position
                if abs(offset) >= 1:
                    # Once turned to the side, a cover only slides along: the same pixels do.
                    side = math.copysign(1, offset)
                    picture, inside, (left, top) = self._warped_cover(albums[index], side)
                    left += round((_placement(offset)[0] - _placement(side)[0]) * SIDE_SCALE)
                else:
                    picture, inside, (left, top) = self._warped_cover(
                        albums[index], round(offset * STEPS) / STEPS)
                _blit(frame, picture, inside, left, top)
            image = Image.frombytes("RGB", (SIZE, SIZE), frame.tobytes(), "raw", "RGBX")
            image.paste(self._caption(albums[middle]), (0, CAPTION_TOP))
        self._header(image, "Cover Flow", battery, "▶" if playing else "", bluetooth)
        return image

    def _warped_cover(self, album: Album, offset: float):
        """The album's cover placed `offset` from the middle, as for _warp_map."""
        key = (album, offset)
        if key not in self._warped:
            if len(self._warped) > 100:
                self._warped.clear()
            rows, inside, at = _warp_map(offset)
            texture = self._texture(album)
            self._warped[key] = (texture.ravel().take(rows), inside, at)
        return self._warped[key]

    def _texture(self, album: Album):
        texture = self._textures.pop(album, None)
        if texture is None:
            if len(self._textures) >= 4 * SIDE_COVERS:  # forget the least recently drawn
                del self._textures[next(iter(self._textures))]
            texture = _cover_texture(album_thumbnail(album, COVER, self.cover_cache))
        self._textures[album] = texture
        return texture

    def _caption(self, album: Album) -> Image.Image:
        """The album's name and artist, for under the covers."""
        if album not in self._captions:
            if len(self._captions) > 64:
                self._captions.clear()
            strip = Image.new("RGB", (SIZE, SIZE - CAPTION_TOP), BG)
            draw = ImageDraw.Draw(strip)
            draw.text((SIZE // 2, 0), _fit(album.name, self.bold, SIZE - 12), font=self.bold,
                      fill=FG, anchor="mt")
            if album.artist:
                draw.text((SIZE // 2, 24), _fit(album.artist, self.text, SIZE - 12),
                          font=self.text, fill=DIM, anchor="mt")
            self._captions[album] = strip
        return self._captions[album]

    def bluetooth(self, devices: list[BtDevice], cursor: int, scanning: bool, scan_left: float,
                  message: str, battery: BatteryStatus | None) -> Image.Image:
        """Row 0 starts or stops a scan; the rest are devices. `cursor` counts row 0."""
        image, draw = self._canvas()
        self._header(image, "Bluetooth", battery, bluetooth=any(d.connected for d in devices))
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
        self._header(image, f"{index + 1}/{count}" if count else "Nothing playing",
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
        self._header(image, "Syncing library", battery)
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
