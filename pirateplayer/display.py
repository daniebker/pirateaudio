"""Output to the Pirate Audio's 240x240 ST7789 screen."""

from PIL import Image

SIZE = 240

_TRANSPOSE = {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180,
              270: Image.Transpose.ROTATE_270}


def rgb565(image: Image.Image, rotation: int = 0) -> bytes:
    """The screen's pixel format: big-endian 16-bit RGB 5-6-5, rotated anticlockwise.

    Gives the same bytes as the st7789 library's own conversion in under half the time,
    which matters on a Pi Zero: every screen change goes through here.
    """
    import numpy

    if rotation in _TRANSPOSE:
        image = image.transpose(_TRANSPOSE[rotation])
    pixels = numpy.asarray(image.convert("RGB"), dtype=numpy.uint16)
    red, green, blue = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    return (((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)).astype(">u2").tobytes()


class LcdDisplay:
    def __init__(self, rotation: int = 90):
        try:
            from st7789 import ST7789
        except ImportError:  # older releases of the library
            from ST7789 import ST7789

        self._rotation = rotation
        self._lcd = ST7789(port=0, cs=1, dc=9, backlight=13, width=SIZE, height=SIZE,
                           rotation=rotation, spi_speed_hz=80_000_000)
        if hasattr(self._lcd, "begin"):
            self._lcd.begin()

    def show(self, image: Image.Image):
        # What ST7789.display() does, but faster. The panel shows pixels as they arrive, so a
        # slow transfer is visible as the new screen wiping in (sideways, as the panel is
        # mounted rotated).
        self._lcd.set_window()
        data = rgb565(image, self._rotation)
        spi = getattr(self._lcd, "_spi", None)
        if hasattr(spi, "writebytes2"):
            # The library's data() sends 4 KB at a time with spidev's xfer(), which turns each
            # chunk into a list of ints and back, leaving gaps between chunks. writebytes2()
            # sends straight from the buffer.
            self._lcd.send(b"", True)  # sends nothing, but sets the data/command pin to data
            spi.writebytes2(data)
        else:
            self._lcd.data(data)

    def set_backlight(self, on: bool):
        self._lcd.set_backlight(1 if on else 0)
