"""The in-container X service: receives key events and paints asynchronously.

A real X server services the event loop and the framebuffer write on
separate scheduling, which is the source of the race the player must win:
after :meth:`XService.press_key` returns, the finished frame is *not*
guaranteed to be on screen yet.  The draw can also be delayed
(injectable), fail mid-way (leaving a wiped, monochrome buffer), or the
service can exit while the player is still waiting.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from . import scenes
from .framebuffer import Framebuffer


class XService:
    def __init__(
        self,
        framebuffer: Optional[Framebuffer] = None,
        *,
        draw_delay: float = 0.0,
        fail_draws: int = 0,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.framebuffer = framebuffer or Framebuffer(scenes.WIDTH, scenes.HEIGHT)
        self._draw_delay = draw_delay
        self._fail_draws_left = fail_draws
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_pixels = bytes(self.framebuffer.snapshot().pixels)
        self._running = False
        self._exit_code: Optional[int] = None
        self._stopped = threading.Event()
        self._pending: list[threading.Thread] = []

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._exit_code = None
            self._stopped.clear()

    def stop(self, exit_code: int = 0) -> None:
        """Stop the service, as a clean shutdown or a crash (nonzero)."""
        with self._lock:
            self._running = False
            self._exit_code = exit_code
        self._stopped.set()

    def crash(self, exit_code: int = 1) -> None:
        self.stop(exit_code=exit_code)

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def exit_code(self) -> Optional[int]:
        """Set once the service has stopped; ``None`` while it is up."""
        with self._lock:
            return None if self._running else self._exit_code

    # -- input -------------------------------------------------------------

    def press_key(self, key: str) -> None:
        """Queue an asynchronous paint for ``key`` and return immediately.

        The call deliberately does not wait for the paint: that boundary is
        what lets a naive player capture a half-finished frame.
        """
        with self._lock:
            if not self._running:
                raise RuntimeError("X service is not running")
            thread = threading.Thread(target=self._draw, args=(key,), daemon=True)
            self._pending.append(thread)
            thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        """Block until queued paints have settled (test helper)."""
        for thread in list(self._pending):
            thread.join(timeout)

    # -- the asynchronous paint -------------------------------------------

    def _draw(self, key: str) -> None:
        if self._draw_delay:
            if self._sleep is not None:
                # Fully scripted clock: no wall time, but honor a stop.
                self._sleep(self._draw_delay)
            elif self._stopped.wait(self._draw_delay):
                return
        with self._lock:
            if not self._running:
                return
            if self._fail_draws_left > 0:
                self._fail_draws_left -= 1
                # A failed draw dies after wiping the buffer white but before
                # committing the scene: the framebuffer is visibly unfinished.
                self.framebuffer.commit(
                    b"\xff" * (scenes.WIDTH * scenes.HEIGHT * 3), None
                )
                return
            pixels = scenes.render(key, self._last_pixels)
            frame = self.framebuffer.commit(pixels, key)
            self._last_pixels = frame.pixels
