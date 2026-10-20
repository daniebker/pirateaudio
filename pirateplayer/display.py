"""Output to the Pirate Audio's 240x240 ST7789 screen."""

from PIL import Image

SIZE = 240


class LcdDisplay:
    def __init__(self, rotation: int = 90):
        try:
            from st7789 import ST7789
        except ImportError:  # older releases of the library
            from ST7789 import ST7789

        self._lcd = ST7789(port=0, cs=1, dc=9, backlight=13, width=SIZE, height=SIZE,
                           rotation=rotation, spi_speed_hz=80_000_000)
        if hasattr(self._lcd, "begin"):
            self._lcd.begin()

    def show(self, image: Image.Image):
        self._lcd.display(image)

    def set_backlight(self, on: bool):
        self._lcd.set_backlight(1 if on else 0)

