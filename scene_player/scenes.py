"""Deterministic golden scenes and the on-screen patch that names them.

A frame is plain packed RGB bytes, so nothing here needs Pillow.  Every
scene the player can request carries a small glyph patch; the player uses
the glyph it reads back out of a grabbed frame to prove that the frame
shows *that* paint job, rather than the previous frame or a half-drawn
one.  The base screen deliberately carries no patch and more than one
colour (the readiness probe treats a monochrome buffer as "never drawn").
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

from .framebuffer import Frame

SIZE: Tuple[int, int] = (320, 240)
WIDTH, HEIGHT = SIZE

# Glyph geometry and where the patch lives on screen.
GLYPH_SIZE: Tuple[int, int] = (5, 7)
GLYPH_COLUMNS, GLYPH_ROWS = GLYPH_SIZE
CELL = 4
PATCH_X, PATCH_Y = 6, 6
PATCH_W, PATCH_H = GLYPH_COLUMNS * CELL, GLYPH_ROWS * CELL

PAPER = (245, 245, 245)
INK = (12, 12, 12)
SCROLL_FILL = (255, 0, 255)
SOLID_COLOUR = (40, 90, 160)

# Five-by-seven bitmap font.  Only the scene keys need glyphs.
GLYPHS: Dict[str, Tuple[str, ...]] = {
    "0": (
        " ### ",
        "#   #",
        "#  ##",
        "# # #",
        "##  #",
        "#   #",
        " ### ",
    ),
    "c": (
        " ####",
        "#    ",
        "#    ",
        "#    ",
        "#    ",
        "#    ",
        " ####",
    ),
    "d": (
        "##   ",
        "# #  ",
        "#  # ",
        "#  # ",
        "#  # ",
        "# #  ",
        "##   ",
    ),
    "g": (
        " ### ",
        "#   #",
        "#    ",
        "#####",
        "    #",
        "#   #",
        " ### ",
    ),
    "s": (
        " ####",
        "#    ",
        "#    ",
        " ### ",
        "    #",
        "    #",
        "#### ",
    ),
}

SCENES: Tuple[str, ...] = tuple(sorted(GLYPHS))
_GLYPH_FOR_PATTERN = {pattern: key for key, pattern in GLYPHS.items()}


def _set_pixel(buf: bytearray, x: int, y: int, colour: Tuple[int, int, int]) -> None:
    offset = (y * WIDTH + x) * 3
    buf[offset:offset + 3] = bytes(colour)


def base_pixels() -> bytearray:
    """The dense, multi-colour, deliberately *unstamped* base screen."""
    buf = bytearray(WIDTH * HEIGHT * 3)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            # Bands plus a per-pixel term: plenty of distinct colours, fully
            # deterministic, no two rows identical.
            r = (x * 7 + y * 13) & 0xFF
            g = (x * 3 + y * 5 + 64) & 0xFF
            b = ((x ^ y) * 11 + 128) & 0xFF
            _set_pixel(buf, x, y, (r, g, b))
    return buf


def stamp_patch(pixels: bytes, width: int, height: int, key: str) -> bytes:
    """Return ``pixels`` with key's glyph patch drawn in the top-left corner."""
    if key not in GLYPHS:
        raise ValueError(f"unknown scene key: {key!r}")
    if width < PATCH_X + PATCH_W or height < PATCH_Y + PATCH_H:
        raise ValueError(
            f"screen {width}x{height} is too small to hold the "
            f"{PATCH_W}x{PATCH_H} scene patch"
        )
    buf = bytearray(pixels)
    pattern = GLYPHS[key]
    for gy, row in enumerate(pattern):
        for gx, mark in enumerate(row):
            colour = INK if mark == "#" else PAPER
            for dy in range(CELL):
                for dx in range(CELL):
                    _set_pixel(buf, PATCH_X + gx * CELL + dx, PATCH_Y + gy * CELL + dy, colour)
    return bytes(buf)


def _solid() -> bytearray:
    buf = bytearray()
    chunk = bytes(SOLID_COLOUR)
    row = chunk * WIDTH
    buf.extend(row * HEIGHT)
    return buf


def _dense() -> bytearray:
    # Distinct from the base screen, still many colours.
    buf = bytearray(WIDTH * HEIGHT * 3)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            _set_pixel(
                buf, x, y,
                ((x * 17 + 32) & 0xFF, (y * 19 + x) & 0xFF, (x * y + 7) & 0xFF),
            )
    return buf


def _grid() -> bytearray:
    buf = bytearray(WIDTH * HEIGHT * 3)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            on_line = x % 32 < 2 or y % 24 < 2
            colour = (230, 230, 230) if on_line else (20, 60, 30)
            _set_pixel(buf, x, y, colour)
    return buf


def _scroll(prior: bytes, shift_x: int = 17, shift_y: int = 11) -> bytearray:
    """Delta update: shift the prior screen, filling the exposed strips."""
    buf = bytearray(len(prior))

    def src_for(x: int, y: int) -> Tuple[int, int]:
        return (x - shift_x) % WIDTH, (y - shift_y) % HEIGHT

    for y in range(HEIGHT):
        for x in range(WIDTH):
            sx, sy = src_for(x, y)
            exposed = x < shift_x or y < shift_y
            colour = SCROLL_FILL if exposed else None
            if colour is None:
                offset = (y * WIDTH + x) * 3
                src = (sy * WIDTH + sx) * 3
                buf[offset:offset + 3] = prior[src:src + 3]
            else:
                _set_pixel(buf, x, y, colour)
    return buf


def render(key: str, prior: Optional[bytes] = None) -> bytes:
    """Render scene ``key``; ``c`` is a delta against ``prior``."""
    if key == "0":
        pixels = base_pixels()
    elif key == "s":
        pixels = _solid()
    elif key == "d":
        pixels = _dense()
    elif key == "g":
        pixels = _grid()
    elif key == "c":
        pixels = _scroll(prior if prior is not None else bytes(base_pixels()))
    else:
        raise ValueError(f"unknown scene key: {key!r}")
    return stamp_patch(bytes(pixels), WIDTH, HEIGHT, key)


def base() -> Frame:
    return Frame(WIDTH, HEIGHT, bytes(base_pixels()), None, 0)


def apply(key: str, prior: Frame) -> Frame:
    if prior.width != WIDTH or prior.height != HEIGHT:
        raise ValueError("scene updates require the declared screen size")
    return Frame(WIDTH, HEIGHT, render(key, prior.pixels), key, prior.generation + 1)


def decode_stamp(frame: Frame) -> Optional[str]:
    """Read the glyph patch out of a grabbed frame, or ``None`` if garbled."""
    if frame.width < PATCH_X + PATCH_W or frame.height < PATCH_Y + PATCH_H:
        return None
    rows = []
    for gy in range(GLYPH_ROWS):
        row = []
        for gx in range(GLYPH_COLUMNS):
            cx = PATCH_X + gx * CELL + CELL // 2
            cy = PATCH_Y + gy * CELL + CELL // 2
            offset = (cy * frame.width + cx) * 3
            r, g, b = frame.pixels[offset], frame.pixels[offset + 1], frame.pixels[offset + 2]
            # Ink is dark, paper is light: decide on luma so slight encoding
            # noise on the channel cannot forge a glyph.
            row.append("#" if (r + g + b) / 3 < 128 else " ")
        rows.append("".join(row))
    return _GLYPH_FOR_PATTERN.get(tuple(rows))


# Backwards-compatible name used by the fleet's test suite.
read_patch = decode_stamp


def write_ppm(path: "str | Path", frame: Frame) -> None:
    """Write a frame as binary PPM (P6), fsync'd so callers see the whole file."""
    p = Path(path)
    header = f"P6\n{frame.width} {frame.height}\n255\n".encode("ascii")
    with p.open("wb") as fh:
        fh.write(header)
        fh.write(frame.pixels)
        fh.flush()
        import os
        os.fsync(fh.fileno())


def read_ppm(path: "str | Path") -> Frame:
    p = Path(path)
    data = p.read_bytes()
    if not data.startswith(b"P6"):
        raise ValueError(f"{p}: not a binary PPM file")
    # Tokens: magic, width, height, maxval, then raw pixels.
    tokens = []
    index = 2
    while len(tokens) < 3:
        while index < len(data) and data[index] in b" \t\r\n":
            index += 1
        start = index
        while index < len(data) and data[index] not in b" \t\r\n":
            index += 1
        tokens.append(int(data[start:index]))
    index += 1  # single whitespace separating maxval from the raster
    width, height, maxval = tokens
    if maxval != 255:
        raise ValueError(f"{p}: only 8-bit PPM is supported, got maxval {maxval}")
    pixels = data[index:index + width * height * 3]
    if len(pixels) != width * height * 3:
        raise ValueError(f"{p}: truncated raster ({len(pixels)} bytes)")
    return Frame(width, height, bytes(pixels), None, 0)
