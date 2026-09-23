"""Reads frames out of the X framebuffer.

A grab is a separate failure domain from the paint: the framebuffer can be
perfectly fine while the grab transport returns a transient error.  Such
errors are expected to be retried by the player rather than saved.
"""
from __future__ import annotations

from typing import Optional

from .framebuffer import Frame, Framebuffer


class GrabError(RuntimeError):
    """A single grab attempt failed; the next attempt may still succeed."""


class FramebufferGrabber:
    def __init__(
        self,
        framebuffer: Framebuffer,
        *,
        fail_grabs: int = 0,
        error: Optional[Exception] = None,
    ) -> None:
        self.framebuffer = framebuffer
        self._fail_grabs_left = fail_grabs
        self._error = error or GrabError("transient framebuffer read failure")
        self.attempts = 0

    def grab(self) -> Frame:
        self.attempts += 1
        if self._fail_grabs_left > 0:
            self._fail_grabs_left -= 1
            raise self._error
        return self.framebuffer.snapshot()
