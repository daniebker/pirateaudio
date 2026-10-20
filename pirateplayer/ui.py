"""Drawing the browser and now-playing screens."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .battery import BatteryStatus
from .display import SIZE
from .library import TrackInfo

BG = (14, 15, 20)
FG = (236, 236, 240)
DIM = (140, 142, 155)
ACCENT = (255, 168, 38)
SELECTED = (52, 54, 68)
LOW = (235, 70, 60)
GOOD = (90, 200, 110)

HEADER = 28
ROW = 26
ROWS = (SIZE - HEADER) // ROW
ART = 104

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def _font(name: str, size: int):
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default(size)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: float) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


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

    def _header(self, draw, title: str, battery, extra: str = ""):
        draw.rectangle((0, 0, SIZE, HEADER - 2), fill=(26, 27, 36))
        right = self._battery(draw, battery, SIZE - 4)
        if extra:
            right -= int(draw.textlength(extra, font=self.small)) + 8
            draw.text((right, 8), extra, font=self.small, fill=ACCENT)
        draw.text((6, 5), _fit(draw, title, self.bold, right - 12), font=self.bold, fill=FG)

    def browser(self, title: str, entries: list[Path], cursor: int,
                battery: BatteryStatus | None, playing: bool) -> Image.Image:
        image, draw = self._canvas()
        self._header(draw, title, battery, "▶" if playing else "")
        if not entries:
            draw.text((SIZE // 2, SIZE // 2), "No music here", font=self.text, fill=DIM, anchor="mm")
            return image

        top = min(max(0, cursor - ROWS // 2), max(0, len(entries) - ROWS))
        for row, entry in enumerate(entries[top:top + ROWS]):
            y = HEADER + row * ROW
            if top + row == cursor:
                draw.rectangle((0, y, SIZE, y + ROW - 1), fill=SELECTED)
            is_dir = entry.is_dir()
            label = entry.name + "/" if is_dir else entry.stem
            draw.text((8, y + 4), _fit(draw, label, self.text, SIZE - 22), font=self.text,
                      fill=ACCENT if is_dir else FG)

        if len(entries) > ROWS:
            track = SIZE - HEADER
            bar = max(12, track * ROWS // len(entries))
            y = HEADER + (track - bar) * top // (len(entries) - ROWS)
            draw.rectangle((SIZE - 4, y, SIZE - 1, y + bar), fill=DIM)
        return image

    def now_playing(self, info: TrackInfo | None, state: str, position: float, volume: int,
                    index: int, count: int, battery: BatteryStatus | None) -> Image.Image:
        image, draw = self._canvas()
        self._header(draw, f"{index + 1}/{count}" if count else "Nothing playing",
                     battery, f"vol {volume}%")
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
        draw.text((SIZE // 2, y), _fit(draw, info.title, self.bold, SIZE - 12),
                  font=self.bold, fill=FG, anchor="mt")
        for line in (info.artist, info.album):
            y += 22
            if line:
                draw.text((SIZE // 2, y), _fit(draw, line, self.text, SIZE - 12),
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

    def message(self, text: str) -> Image.Image:
        image, draw = self._canvas()
        draw.multiline_text((SIZE // 2, SIZE // 2), text, font=self.bold, fill=FG,
                            anchor="mm", align="center")
        return image
