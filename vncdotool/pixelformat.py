"""Pixel formats an X service may pack the frame buffer into.

Only the channel maxima matter to the golden tests: a server keeps each
channel's top bits, and the tests rebuild the screen a client would see.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PixelFormat:
    name: str
    depth: int
    redmax: int
    greenmax: int
    bluemax: int
    redshift: int = 0
    greenshift: int = 0
    blueshift: int = 0


_FORMATS = [
    PixelFormat("rgb888", 24, 255, 255, 255, redshift=16, greenshift=8, blueshift=0),
    PixelFormat("bgr888", 24, 255, 255, 255, redshift=0, greenshift=8, blueshift=16),
    PixelFormat("rgb565", 16, 31, 63, 31, redshift=11, greenshift=5, blueshift=0),
    PixelFormat("bgr565", 16, 31, 63, 31, redshift=0, greenshift=5, blueshift=11),
    PixelFormat("rgb555", 15, 31, 31, 31, redshift=10, greenshift=5, blueshift=0),
    PixelFormat("bgr555", 15, 31, 31, 31, redshift=0, greenshift=5, blueshift=10),
    PixelFormat("rgb444", 12, 15, 15, 15, redshift=8, greenshift=4, blueshift=0),
]

PIXEL_FORMATS = {fmt.name: fmt for fmt in _FORMATS}
