"""A simulated X service: owns a frame buffer and draws scenes into it
asynchronously, the way a real X server repaints only after the client
asks.  It stands in for the real VNC servers the upstream project runs,
so the capture-before-first-draw race is reproducible in a container.

Line protocol (one connection per player session)::

    status     -> "ok ready=0|1 frames=N size=WxH"
    key KEY    -> "ok"      queue a redraw of the scene KEY selects
    move X Y   -> "ok"      move the pointer
    click B    -> "ok"      queue a redraw of the scene under the pointer
    capture    -> "frame N" followed by N bytes of PNG: the CURRENT frame
                  buffer, blank until the first draw completes -- waiting
                  for ready is the caller's job
    quit       -> close the connection

Latency and failure injection knobs are constructor arguments so tests
can reproduce the race and the failure modes on demand:

* ``startup_delay`` -- seconds before the FIRST draw completes
* ``draw_delay`` -- seconds every redraw takes
* ``blank_captures`` -- the first N captures return the blank undrawn
  frame even once ready (a racey frame-buffer read)
* ``die_after_captures`` -- shut the service down after N captures,
  dropping the connection mid-session
* ``never_ready`` -- draws never complete, ``ready`` stays 0
"""
from __future__ import annotations

import io
import queue
import socket
import threading
from typing import Optional, Tuple

from PIL import Image

from tests.goldens import click_targets, scenes

UNDRAWN = (0, 0, 0)


class XService:
    def __init__(
        self,
        name: str,
        host: str,
        port: int,
        *,
        startup_delay: float = 0.0,
        draw_delay: float = 0.0,
        blank_captures: int = 0,
        die_after_captures: Optional[int] = None,
        never_ready: bool = False,
    ) -> None:
        self.name = name
        self.host = host
        self.port = port
        self.startup_delay = startup_delay
        self.draw_delay = draw_delay
        self.blank_captures = blank_captures
        self.die_after_captures = die_after_captures
        self.never_ready = never_ready

        self._screen = Image.new("RGB", scenes.SIZE, UNDRAWN)
        self._undrawn = self._screen.copy()
        self._pointer: Tuple[int, int] = (0, 0)
        self._frames = 0
        self._captures_served = 0
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._stop_lock = threading.Lock()
        self._screen_lock = threading.Lock()
        self._clients: set[socket.socket] = set()
        self._clients_lock = threading.Lock()
        self._draws: "queue.Queue[Optional[Tuple[str, float]]]" = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._listener: Optional[socket.socket] = None

    @property
    def captures_served(self) -> int:
        return self._captures_served

    @property
    def frames_drawn(self) -> int:
        with self._screen_lock:
            return self._frames

    def serve_in_background(self) -> "XService":
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(16)
        listener.settimeout(0.2)
        self._listener = listener
        self.port = listener.getsockname()[1]
        self._draws.put(("0", self.startup_delay))  # the first draw
        self._spawn("draw", self._draw_loop)
        self._spawn("accept", self._accept_loop)
        return self

    def shutdown(self) -> None:
        with self._stop_lock:
            if self._stopping.is_set():
                return
            self._stopping.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        with self._clients_lock:
            clients = list(self._clients)
        for conn in clients:
            _close(conn)
        self._draws.put(None)
        current = threading.current_thread()
        for thread in list(self._threads):
            if thread is not current:
                thread.join(timeout=2.0)

    # -- threads ---------------------------------------------------------

    def _spawn(self, label: str, target, *args) -> None:
        thread = threading.Thread(
            target=target, args=args, name=f"{self.name}-{label}", daemon=True
        )
        self._threads.append(thread)
        thread.start()

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stopping.is_set():
            try:
                conn, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._clients_lock:
                self._clients.add(conn)
            self._spawn("client", self._handle, conn)

    def _draw_loop(self) -> None:
        while True:
            job = self._draws.get()
            if job is None or self._stopping.is_set():
                return
            key, delay = job
            # Drawing takes `delay`; a capture in this window reads the
            # previous -- or the still undrawn -- frame buffer.
            if self._stopping.wait(delay):
                return
            with self._screen_lock:
                self._screen = scenes.apply(key, self._screen)
                self._frames += 1
            if not self.never_ready:
                self._ready.set()

    def _handle(self, conn: socket.socket) -> None:
        try:
            stream = conn.makefile("rb")
            while not self._stopping.is_set():
                line = stream.readline()
                if not line:
                    break
                try:
                    keep_going = self._dispatch(line.decode("ascii").split(), conn)
                except ValueError:
                    keep_going = self._send(conn, "err bad-arguments\n")
                except OSError:
                    break
                if not keep_going:
                    break
        finally:
            with self._clients_lock:
                self._clients.discard(conn)
            _close(conn)

    def _dispatch(self, parts: list[str], conn: socket.socket) -> bool:
        if not parts:
            return True
        command, args = parts[0], parts[1:]
        if command == "status":
            width, height = scenes.SIZE
            ready = 1 if self._ready.is_set() else 0
            return self._send(
                conn, f"ok ready={ready} frames={self.frames_drawn} size={width}x{height}\n"
            )
        if command == "key" and len(args) == 1:
            key = args[0].lower()
            if key in scenes.SCENES:
                self._draws.put((key, self.draw_delay))
            return self._send(conn, "ok\n")
        if command == "move" and len(args) == 2:
            self._pointer = (int(args[0]), int(args[1]))
            return self._send(conn, "ok\n")
        if command == "click" and len(args) == 1:
            int(args[0])  # validate the button number; any button selects
            key = click_targets.scene_at(*self._pointer)
            if key is not None:
                self._draws.put((key, self.draw_delay))
            return self._send(conn, "ok\n")
        if command == "capture" and not args:
            return self._capture(conn)
        if command == "quit":
            return False
        return self._send(conn, "err unknown-command\n")

    def _capture(self, conn: socket.socket) -> bool:
        if (
            self.die_after_captures is not None
            and self._captures_served >= self.die_after_captures
        ):
            # Die mid-session without replying, the way a crashed X
            # service leaves a client.
            self._spawn("reaper", self.shutdown)
            return False
        self._captures_served += 1
        with self._screen_lock:
            frame = (
                self._undrawn
                if self._captures_served <= self.blank_captures
                else self._screen
            )
            buffer = io.BytesIO()
            frame.save(buffer, "PNG")
        payload = buffer.getvalue()
        if not self._send(conn, f"frame {len(payload)}\n"):
            return False
        try:
            conn.sendall(payload)
        except OSError:
            return False
        return True

    def _send(self, conn: socket.socket, text: str) -> bool:
        try:
            conn.sendall(text.encode("ascii"))
        except OSError:
            return False
        return True


def _close(conn: socket.socket) -> None:
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        conn.close()
    except OSError:
        pass
