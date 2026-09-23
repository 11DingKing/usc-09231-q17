"""The scene player: drives an X service and captures its frame buffer.

The X service draws asynchronously, so a capture issued before the first
draw completes reads a half-built frame buffer -- historically saved as
the "result", occasionally an all-blank frame.  The player therefore

* waits for the service to report its first draw complete (``ready``)
  before the first capture of a session, and
* treats a single-colour capture as not-yet-drawn and retries a bounded
  number of times before giving up.

The two failure modes produce distinct results:

* :class:`ServerExitedError` -- the service refused a connection or
  dropped it mid-session;
* :class:`RetryExhaustedError` -- the service stayed up but never became
  ready, or kept serving blank frames, within the retry budget.
"""
from __future__ import annotations

import io
import os
import socket
import time
from dataclasses import dataclass
from typing import BinaryIO, Optional, Union

from PIL import Image

__all__ = [
    "CaptureError",
    "CapturePolicy",
    "RetryExhaustedError",
    "ScenePlayer",
    "ServerExitedError",
    "ServiceStatus",
    "is_blank",
]

PathLike = Union[str, "os.PathLike[str]"]


class CaptureError(RuntimeError):
    """A capture could not be produced."""


class ServerExitedError(CaptureError):
    """The X service exited early: the connection was refused or lost."""


class RetryExhaustedError(CaptureError):
    """The retry budget ran out before the service produced a drawn frame."""


@dataclass(frozen=True)
class CapturePolicy:
    """The sync and retry boundaries between the player and the X service."""

    ready_timeout: float = 10.0  # seconds to wait for the first draw
    ready_poll: float = 0.1  # seconds between readiness polls
    capture_retries: int = 5  # capture attempts once ready
    capture_retry_delay: float = 0.3  # seconds between capture attempts


@dataclass(frozen=True)
class ServiceStatus:
    ready: bool
    frames: int
    size: tuple[int, int]


def is_blank(image: Image.Image) -> bool:
    """A single-colour frame is the undrawn frame buffer, not a result."""
    return image.convert("RGB").getcolors(maxcolors=1) is not None


class ScenePlayer:
    """One session against an X service at ``host:port``."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        policy: Optional[CapturePolicy] = None,
        connect_timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.policy = policy or CapturePolicy()
        self.connect_timeout = connect_timeout
        self._conn: Optional[socket.socket] = None
        self._file: Optional[BinaryIO] = None
        self._ready = False

    def __str__(self) -> str:
        return f"{self.host}:{self.port}"

    def __enter__(self) -> "ScenePlayer":
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> "ScenePlayer":
        if self._conn is not None:
            return self
        try:
            self._conn = socket.create_connection(
                (self.host, self.port), timeout=self.connect_timeout
            )
        except OSError as exc:
            raise ServerExitedError(
                f"X service at {self} is not reachable ({exc}); "
                "it exited early or was never started"
            ) from exc
        self._file = self._conn.makefile("rb")
        return self

    def close(self) -> None:
        conn, self._conn = self._conn, None
        stream, self._file = self._file, None
        if conn is not None:
            try:
                conn.sendall(b"quit\n")
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    # -- protocol ------------------------------------------------------

    def _request(self, line: str) -> str:
        if self._conn is None or self._file is None:
            raise CaptureError("the player is not connected")
        try:
            self._conn.sendall(line.encode("ascii") + b"\n")
            reply = self._file.readline()
        except OSError as exc:
            raise ServerExitedError(
                f"X service at {self} exited early: connection lost during {line!r} ({exc})"
            ) from exc
        if not reply:
            raise ServerExitedError(
                f"X service at {self} exited early: connection closed during {line!r}"
            )
        text = reply.decode("ascii").strip()
        if text.startswith("err"):
            raise CaptureError(f"X service at {self} rejected {line!r}: {text}")
        return text

    def _read_exactly(self, count: int) -> bytes:
        assert self._file is not None
        try:
            data = self._file.read(count)
        except OSError as exc:
            raise ServerExitedError(
                f"X service at {self} exited early: connection lost during a "
                f"frame transfer ({exc})"
            ) from exc
        if not data or len(data) < count:
            raise ServerExitedError(
                f"X service at {self} exited early: connection closed during a "
                "frame transfer"
            )
        return data

    def _expect_ok(self, line: str) -> None:
        reply = self._request(line)
        if reply != "ok":
            raise CaptureError(
                f"X service at {self} answered {reply!r} to {line!r}"
            )

    # -- commands --------------------------------------------------------

    def status(self) -> ServiceStatus:
        reply = self._request("status")
        try:
            marker, *pairs = reply.split()
            assert marker == "ok"
            fields = dict(pair.split("=") for pair in pairs)
            width, height = fields["size"].split("x")
            return ServiceStatus(
                ready=fields["ready"] == "1",
                frames=int(fields["frames"]),
                size=(int(width), int(height)),
            )
        except (AssertionError, KeyError, ValueError) as exc:
            raise CaptureError(
                f"X service at {self} sent a malformed status reply: {reply!r}"
            ) from exc

    def key(self, key: str) -> None:
        self._expect_ok(f"key {key}")

    def move(self, x: int, y: int) -> None:
        self._expect_ok(f"move {int(x)} {int(y)}")

    def click(self, button: int = 1) -> None:
        self._expect_ok(f"click {int(button)}")

    def pause(self, seconds: float) -> None:
        time.sleep(float(seconds))

    # -- the sync and retry boundary ------------------------------------

    def wait_ready(self) -> ServiceStatus:
        """Block until the service reports its first draw complete."""
        if self._ready:
            return self.status()
        deadline = time.monotonic() + self.policy.ready_timeout
        polls = 0
        while True:
            status = self.status()
            polls += 1
            if status.ready:
                self._ready = True
                return status
            if time.monotonic() >= deadline:
                raise RetryExhaustedError(
                    f"X service at {self} did not finish its first draw within "
                    f"{self.policy.ready_timeout:.1f}s "
                    f"({polls} polls, frames={status.frames})"
                )
            time.sleep(self.policy.ready_poll)

    def capture(self, path: PathLike) -> Image.Image:
        """Save the frame buffer to ``path`` once it holds a drawn frame."""
        self.wait_ready()
        for attempt in range(1, self.policy.capture_retries + 1):
            image = self._grab()
            if not is_blank(image):
                image.save(str(path))
                return image
            if attempt < self.policy.capture_retries:
                time.sleep(self.policy.capture_retry_delay)
        raise RetryExhaustedError(
            f"X service at {self} still serves a blank (single-colour) frame "
            f"after {self.policy.capture_retries} attempts; "
            "it has not finished drawing"
        )

    def _grab(self) -> Image.Image:
        reply = self._request("capture")
        parts = reply.split()
        if len(parts) != 2 or parts[0] != "frame":
            raise CaptureError(
                f"X service at {self} sent a malformed capture reply: {reply!r}"
            )
        try:
            count = int(parts[1])
        except ValueError as exc:
            raise CaptureError(
                f"X service at {self} sent a malformed capture reply: {reply!r}"
            ) from exc
        image = Image.open(io.BytesIO(self._read_exactly(count)))
        image.load()
        return image
