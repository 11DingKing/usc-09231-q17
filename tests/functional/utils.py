"""Fleet wiring for the functional tests.

The upstream project runs its scene servers as real VNC servers in
containers; this standalone snapshot replaces them with simulated X
services (:class:`tests.functional.xservice.XService`) started in-process,
so the capture-before-first-draw race is reproducible without a display.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from unittest import TestCase

from tests.functional.xservice import XService
from tests.goldens import scenes
from vncdotool.player import CapturePolicy, ScenePlayer

HOST = "127.0.0.1"
ROOT = Path(__file__).resolve().parents[2]

HOW_TO_START = "start the simulated fleet with: python3 -m tests.functional.utils"


@dataclass(frozen=True)
class VNCServer:
    name: str
    port: int
    how_to_start: str = HOW_TO_START
    skip_pointer_tests: bool = False
    startup_delay: float = 0.2
    draw_delay: float = 0.05


TIGERVNC = VNCServer("tigervnc", 5931)
X11VNC = VNCServer("x11vnc", 5932)
SCENE_SERVERS = (TIGERVNC, X11VNC)


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


_FLEET_LOCK = threading.Lock()
_RUNNING: dict[str, XService] = {}


def ensure_fleet() -> None:
    """Start any scene server that is not already listening."""
    with _FLEET_LOCK:
        missing = [s for s in SCENE_SERVERS if not port_open(HOST, s.port)]
        for server in missing:
            service = XService(
                server.name,
                HOST,
                server.port,
                startup_delay=server.startup_delay,
                draw_delay=server.draw_delay,
            )
            service.serve_in_background()
            _RUNNING[server.name] = service
        deadline = time.monotonic() + 5.0
        for server in missing:
            while not port_open(HOST, server.port):
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"{server.name} did not start on {HOST}:{server.port}; "
                        f"{server.how_to_start}"
                    )
                time.sleep(0.05)


def assert_fleet_current(server: VNCServer) -> None:
    """The fleet is up and serves the geometry the goldens declare."""
    ensure_fleet()
    if not port_open(HOST, server.port):
        raise AssertionError(
            f"{server.name} is not listening on {HOST}:{server.port}; "
            f"{server.how_to_start}"
        )
    try:
        with ScenePlayer(HOST, server.port, policy=CapturePolicy()) as player:
            status = player.status()
    except Exception as exc:
        raise AssertionError(
            f"{server.name} on {HOST}:{server.port} does not speak the scene "
            f"protocol ({exc}); the running fleet is stale, {server.how_to_start}"
        ) from exc
    if status.size != scenes.SIZE:
        raise AssertionError(
            f"{server.name} serves {status.size[0]}x{status.size[1]} but the "
            f"goldens declare {scenes.SIZE[0]}x{scenes.SIZE[1]}; "
            f"the running fleet is stale, {server.how_to_start}"
        )


class FleetTestCase(TestCase):
    """Base for tests that need one scene server from the fleet."""

    server: VNCServer

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        assert_fleet_current(cls.server)


def run_vncdo(server: VNCServer, *args: str) -> subprocess.CompletedProcess:
    """Run the scene player CLI against ``server`` with ``args``."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "vncdotool", "--server", f"{HOST}:{server.port}", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


if __name__ == "__main__":
    ensure_fleet()
    print("simulated fleet listening:")
    for scene_server in SCENE_SERVERS:
        print(f"  {scene_server.name}: {HOST}:{scene_server.port}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
