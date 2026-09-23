"""Deterministic, dependency-free test doubles for the synchronisation tests."""
from __future__ import annotations

import threading
from typing import List, Optional, Union

from scene_player import scenes
from scene_player.framebuffer import Frame
from scene_player.grabber import GrabError


class FakeClock:
    """Scripted monotonic clock: ``sleep`` advances time without wall waits."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: List[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class StubService:
    """The slice of XService the player talks to, with scripted liveness."""

    def __init__(self, running: bool = True, exit_code: Optional[int] = None) -> None:
        self._running = running
        self.exit_code = exit_code
        self.pressed: List[str] = []

    @property
    def running(self) -> bool:
        return self._running

    def press_key(self, key: str) -> None:
        self.pressed.append(key)

    def exit(self, exit_code: int) -> None:
        self._running = False
        self.exit_code = exit_code


# A grab script entry is either a Frame to return or an Exception to raise.
ScriptEntry = Union[Frame, BaseException]


class ScriptedGrabber:
    """Returns a canned sequence of frames/errors, then repeats the last."""

    def __init__(self, script: List[ScriptEntry]) -> None:
        if not script:
            raise ValueError("a grab script needs at least one entry")
        self._script = script
        self.calls = 0

    def grab(self) -> Frame:
        index = min(self.calls, len(self._script) - 1)
        self.calls += 1
        entry = self._script[index]
        if isinstance(entry, BaseException):
            raise entry
        return entry


def blank_frame() -> Frame:
    return Frame(scenes.WIDTH, scenes.HEIGHT, b"\x00" * (scenes.WIDTH * scenes.HEIGHT * 3), None, 1)


def scene_frame(key: str) -> Frame:
    return Frame(scenes.WIDTH, scenes.HEIGHT, scenes.render(key), key, 2)


class BlankAfterReads:
    """A service that stays up for N liveness checks, then exits early.

    The player reads ``running`` once before queuing the paint and once
    after every grab; the exit therefore lands at a deterministic attempt.
    """

    def __init__(self, reads_before_exit: int, exit_code: int) -> None:
        self._reads_before_exit = reads_before_exit
        self.exit_code = exit_code
        self._checks = 0

    @property
    def running(self) -> bool:
        self._checks += 1
        return self._checks <= self._reads_before_exit

    def press_key(self, key: str) -> None:
        pass


class BlockingDrawService:
    """An X-like service whose draw blocks until the test releases it.

    Lets a test exercise "still unfinished after N polls / T fake seconds"
    deterministically, then cleanly unblock the draw thread in tearDown.
    """

    def __init__(self) -> None:
        self._release = threading.Event()
        self.framebuffer = None
        self._running = True
        self.exit_code: Optional[int] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._running

    def press_key(self, key: str) -> None:
        self._thread = threading.Thread(target=self._draw, daemon=True)
        self._thread.start()

    def _draw(self) -> None:
        self._release.wait()

    def release_and_join(self) -> None:
        self._release.set()
        if self._thread is not None:
            self._thread.join(2.0)
            if self._thread.is_alive():
                raise RuntimeError("blocked draw thread did not finish")

    def stop(self, exit_code: int = 0) -> None:
        self._running = False
        self.exit_code = exit_code
        self.release_and_join()


__all__ = [
    "BlankAfterReads",
    "FakeClock",
    "BlockingDrawService",
    "GrabError",
    "ScriptedGrabber",
    "StubService",
    "blank_frame",
    "scene_frame",
]
