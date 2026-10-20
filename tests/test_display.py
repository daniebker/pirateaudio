import pytest
from PIL import Image

from pirateplayer.display import SIZE, rgb565

numpy = pytest.importorskip("numpy")


def reference(image, rotation):
    """The st7789 library's ST7789.image_to_data, which rgb565 replaces."""
    image = numpy.array(image.convert("RGB"))
    pb = numpy.rot90(image, rotation // 90).astype("uint16")
    red = (pb[..., [0]] & 0xF8) << 8
    green = (pb[..., [1]] & 0xFC) << 3
    blue = (pb[..., [2]] & 0xF8) >> 3
    return (red | green | blue).byteswap().tobytes()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rgb565_matches_the_st7789_library(rotation):
    rng = numpy.random.default_rng(1)
    image = Image.fromarray(rng.integers(0, 256, (SIZE, SIZE, 3), dtype=numpy.uint8))
    assert rgb565(image, rotation) == reference(image, rotation)


def test_rgb565_format():
    image = Image.new("RGB", (2, 1))
    image.putpixel((0, 0), (255, 0, 0))
    image.putpixel((1, 0), (0, 0, 255))
    assert rgb565(image) == bytes([0xF8, 0x00, 0x00, 0x1F])
