"""The scene player.

Responsibility split with the X service:

* The X service paints *asynchronously* after a key event; nothing about
  ``press_key`` returning means the frame is on screen.
* The player owns the synchronisation boundary: it polls the framebuffer
  and only accepts a frame once it is provably the **first finished paint
  of the requested scene** -- non-monochrome *and* carrying that scene's
  glyph stamp.  Blank, half-drawn and stale-previous-scene frames are
  retried, never saved.

Failure outcomes are explicit instead of looking like a capture:

* :class:`ServiceNotRunningError` -- requested before the service started
* :class:`ServiceExitedError`    -- the service died while the player
  waited; no finished frame is ever coming (includes the exit code)
* :class:`RetryExhaustedError`   -- service stayed up but no finished
  frame appeared before attempts/time ran out (includes why the last
  frame was rejected)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from . import scenes
from .framebuffer import Frame
from .grabber import FramebufferGrabber, GrabError
from .xservice import XService


class ScenePlayerError(RuntimeError):
    """Base class for every way a capture can end without a result file."""


class ServiceNotRunningError(ScenePlayerError):
    def __init__(self, key: str) -> None:
        super().__init__(
            f"cannot request scene {key!r}: the X service is not running"
        )
        self.key = key


class ServiceExitedError(ScenePlayerError):
    def __init__(
        self,
        key: str,
        exit_code: Optional[int],
        attempts: int,
        last_frame: Optional[Frame],
    ) -> None:
        state = _frame_state(last_frame)
        super().__init__(
            f"the X service exited (code={exit_code}) before scene {key!r} "
            f"finished painting after {attempts} grab attempt(s); "
            f"last observed frame: {state}"
        )
        self.key = key
        self.exit_code = exit_code
        self.attempts = attempts
        self.last_frame = last_frame


class RetryExhaustedError(ScenePlayerError):
    def __init__(
        self,
        key: str,
        attempts: int,
        elapsed: float,
        limit: str,
        last_frame: Optional[Frame],
        last_error: Optional[BaseException],
    ) -> None:
        state = _frame_state(last_frame)
        grabs = (
            f"last grab error: {last_error!r}" if last_error is not None
            else f"last observed frame: {state}"
        )
        super().__init__(
            f"scene {key!r} was not ready after {attempts} grab attempt(s) "
            f"({limit}, {elapsed:.3f}s elapsed); {grabs}"
        )
        self.key = key
        self.attempts = attempts
        self.elapsed = elapsed
        self.limit = limit
        self.last_frame = last_frame
        self.last_error = last_error


@dataclass(frozen=True)
class CaptureResult:
    path: Path
    key: str
    frame: Frame
    attempts: int
    elapsed: float


def _frame_state(frame: Optional[Frame]) -> str:
    if frame is None:
        return "no frame grabbed"
    if frame.is_blank():
        return "blank (paint unfinished)"
    stamped = scenes.decode_stamp(frame)
    if stamped is None:
        return "multi-colour but its scene patch was unreadable"
    return f"stamped for scene {stamped!r} (a stale frame)"


class ScenePlayer:
    def __init__(
        self,
        service: XService,
        grabber: Optional[FramebufferGrabber] = None,
        *,
        poll_interval: float = 0.05,
        timeout: float = 10.0,
        max_attempts: Optional[int] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.service = service
        self.grabber = grabber or FramebufferGrabber(service.framebuffer)
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._clock = clock
        self._sleep = sleep

    def capture_scene(self, key: str, path: Union[str, Path]) -> CaptureResult:
        """Press ``key`` and save the first *finished* frame to ``path``.

        The destination only ever appears once, atomically (tmp-file plus
        ``os.replace``), with a frame that passed validation: a reader can
        never open a half-written or half-painted result.
        """
        path = Path(path)
        if not self.service.running:
            raise ServiceNotRunningError(key)

        self.service.press_key(key)
        started = self._clock()
        deadline = started + self.timeout
        attempts = 0
        last_frame: Optional[Frame] = None
        last_error: Optional[BaseException] = None

        while True:
            attempts += 1
            try:
                frame = self.grabber.grab()
            except GrabError as exc:
                last_frame = None
                last_error = exc
            else:
                last_error = None
                last_frame = frame
                if not frame.is_blank() and scenes.decode_stamp(frame) == key:
                    elapsed = self._clock() - started
                    self._commit_atomic(path, frame)
                    return CaptureResult(path, key, frame, attempts, elapsed)

            # The frame is not finished.  If the service is gone it never
            # will be: report the early exit rather than burning retries.
            if not self.service.running:
                raise ServiceExitedError(key, self.service.exit_code, attempts, last_frame)

            if self.max_attempts is not None and attempts >= self.max_attempts:
                raise RetryExhaustedError(
                    key, attempts, self._clock() - started,
                    f"max_attempts={self.max_attempts}",
                    last_frame, last_error,
                )

            remaining = deadline - self._clock()
            if remaining <= 0:
                raise RetryExhaustedError(
                    key, attempts, self._clock() - started,
                    f"timeout={self.timeout:g}s",
                    last_frame, last_error,
                )
            self._sleep(min(self.poll_interval, remaining))

    @staticmethod
    def _commit_atomic(path: Path, frame: Frame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        scenes.write_ppm(tmp, frame)
        os.replace(tmp, path)
