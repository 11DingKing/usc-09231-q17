"""Golden screens: the scenes the X services draw and the tests verify.

Every scene is a delta on the screen before it, except ``0`` which resets
to the base screen.  Each drawn screen carries a small glyph patch in the
corner naming the scene, so a captured frame can be read back even after
a lossy encoding or a low-depth pixel format.  Ink and paper differ only
in luma, and each glyph cell is sampled at its centre.
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw

SIZE = (240, 160)
GLYPH_SIZE = (5, 7)  # columns, rows
CELL = 8  # pixels per glyph cell
PATCH_MARGIN = 1  # cells of paper around the glyph
PATCH_ORIGIN = (8, 8)  # top-left of the patch on screen

INK = (0, 0, 0)
PAPER = (255, 255, 255)

_GLYPH_SOURCES = {
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "c": (".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "d": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "g": (".###.", "#....", "#....", "#.###", "#...#", "#...#", ".###."),
    "s": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
}

GLYPHS = {
    key: tuple(line.replace(".", " ") for line in lines)
    for key, lines in _GLYPH_SOURCES.items()
}
SCENES = tuple(GLYPHS)


def _patch_box() -> tuple[int, int, int, int]:
    columns, rows = GLYPH_SIZE
    width = (columns + 2 * PATCH_MARGIN) * CELL
    height = (rows + 2 * PATCH_MARGIN) * CELL
    x, y = PATCH_ORIGIN
    return x, y, x + width, y + height


def _ink_origin() -> tuple[int, int]:
    return (
        PATCH_ORIGIN[0] + PATCH_MARGIN * CELL,
        PATCH_ORIGIN[1] + PATCH_MARGIN * CELL,
    )


def base() -> Image.Image:
    """The screen every server starts from: a deterministic two-axis ramp."""
    levels = lambda value: (value >> 3) << 3  # 32 levels keep colours <= 4096
    red = Image.linear_gradient("L").resize(SIZE).point(levels)
    green = Image.linear_gradient("L").resize((SIZE[1], SIZE[0]))
    green = green.transpose(Image.Transpose.ROTATE_90).point(levels)
    blue = Image.new("L", SIZE, 128)
    return Image.merge("RGB", (red, green, blue))


def _dense() -> Image.Image:
    image = Image.new("RGB", SIZE)
    draw = ImageDraw.Draw(image)
    for y in range(0, SIZE[1], 4):
        for x in range(0, SIZE[0], 4):
            colour = (16, 32, 48) if (x // 4 + y // 4) % 2 else (208, 216, 224)
            draw.rectangle((x, y, x + 3, y + 3), fill=colour)
    return image


def apply(key: str, image: Image.Image) -> Image.Image:
    """Return a new screen with scene ``key`` applied and the patch stamped."""
    if key not in SCENES:
        raise ValueError(f"unknown scene {key!r}; expected one of {SCENES}")
    if key == "0":
        screen = base()
    elif key == "s":
        screen = Image.new("RGB", SIZE, (70, 130, 180))
    elif key == "d":
        screen = _dense()
    elif key == "g":
        screen = Image.radial_gradient("L").resize(SIZE).convert("RGB")
    elif key == "c":
        screen = ImageChops.offset(image.convert("RGB"), 16, 8)
    stamp_patch(screen, key)
    return screen


def stamp_patch(image: Image.Image, key: str) -> Image.Image:
    """Stamp the glyph naming ``key`` onto ``image``, in place."""
    glyph = GLYPHS[key]
    box = _patch_box()
    if image.size[0] < box[2] or image.size[1] < box[3]:
        raise ValueError(f"screen {image.size} is too small to hold patch {box}")
    draw = ImageDraw.Draw(image)
    draw.rectangle(box, fill=PAPER)
    x0, y0 = _ink_origin()
    for row, line in enumerate(glyph):
        for column, mark in enumerate(line):
            if mark == "#":
                x = x0 + column * CELL
                y = y0 + row * CELL
                draw.rectangle((x, y, x + CELL - 1, y + CELL - 1), fill=INK)
    return image


def read_patch(image: Image.Image) -> "str | None":
    """The scene the patch on ``image`` names, or None if there isn't one."""
    box = _patch_box()
    if image.size[0] < box[2] or image.size[1] < box[3]:
        return None
    gray = image.convert("L")
    x0, y0 = _ink_origin()
    lines = []
    for row in range(GLYPH_SIZE[1]):
        marks = []
        for column in range(GLYPH_SIZE[0]):
            x = x0 + column * CELL + CELL // 2
            y = y0 + row * CELL + CELL // 2
            marks.append("#" if gray.getpixel((x, y)) < 128 else " ")
        lines.append("".join(marks))
    glyph = tuple(lines)
    for key, known in GLYPHS.items():
        if glyph == known:
            return key
    return None
