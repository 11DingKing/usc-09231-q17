"""The X framebuffer shared between the drawing service and the grabber."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Frame:
    """One immutable snapshot of the framebuffer.

    ``stamp`` is metadata recorded at commit time; callers that need to know
    what is actually visible on screen (the player's readiness gate) must
    decode it from ``pixels`` via :func:`scene_player.scenes.decode_stamp`.
    """

    width: int
    height: int
    pixels: bytes
    stamp: Optional[str]
    generation: int

    def is_blank(self) -> bool:
        # The fleet readiness probe's rule: a framebuffer that holds a single
        # colour has never contained a finished paint.  Both the black
        # pre-start buffer and a white "wiped mid-draw" buffer look like this.
        return len(set(self.pixels)) <= 1


class Framebuffer:
    """A framebuffer safe for one writer and concurrent snapshot readers."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._lock = threading.Lock()
        self._pixels = bytes(width * height * 3)
        self._generation = 0
        self._stamp: Optional[str] = None

    def snapshot(self) -> Frame:
        with self._lock:
            return Frame(
                self.width,
                self.height,
                self._pixels,
                self._stamp,
                self._generation,
            )

    def commit(self, pixels: bytes, stamp: Optional[str] = None) -> Frame:
        if len(pixels) != self.width * self.height * 3:
            raise ValueError("painted pixels do not match the framebuffer geometry")
        with self._lock:
            self._pixels = bytes(pixels)
            self._stamp = stamp
            self._generation += 1
            return Frame(
                self.width,
                self.height,
                self._pixels,
                self._stamp,
                self._generation,
            )
