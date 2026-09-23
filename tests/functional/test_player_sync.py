"""The sync and retry boundaries between the scene player and the X service.

Each test starts its own X service on an ephemeral port with latency or
failure injection, so the race the player guards against is reproduced
deterministically.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase

from PIL import Image

from tests.functional.utils import HOST, ROOT, port_open
from tests.functional.xservice import XService
from tests.goldens import scenes
from vncdotool.command import (
    EXIT_OK,
    EXIT_RETRY_EXHAUSTED,
    EXIT_SERVER_EXITED,
)
from vncdotool.player import (
    CapturePolicy,
    RetryExhaustedError,
    ScenePlayer,
    ServerExitedError,
    is_blank,
)


def closed_port() -> int:
    """A port nothing is listening on."""
    probe = socket.socket()
    probe.bind((HOST, 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class XServiceTestCase(TestCase):
    def start_service(self, **kwargs) -> XService:
        service = XService("test-x", HOST, 0, **kwargs)
        service.serve_in_background()
        self.addCleanup(service.shutdown)
        deadline = time.monotonic() + 5.0
        while not port_open(HOST, service.port):
            if time.monotonic() > deadline:
                self.fail("the injected X service did not start")
            time.sleep(0.02)
        return service

    def player(self, service: XService, **policy) -> ScenePlayer:
        player = ScenePlayer(HOST, service.port, policy=CapturePolicy(**policy))
        return player.connect()

    def capture(self, player: ScenePlayer) -> Image.Image:
        with tempfile.TemporaryDirectory() as tmp:
            image = player.capture(Path(tmp) / "screen.png")
            self.assertTrue((Path(tmp) / "screen.png").exists())
        return image


class TestFirstDrawSync(XServiceTestCase):
    def test_capture_waits_for_a_slow_first_draw(self) -> None:
        service = self.start_service(startup_delay=1.0)
        player = self.player(
            service, ready_timeout=10.0, ready_poll=0.05, capture_retries=1
        )
        started = time.monotonic()
        image = self.capture(player)
        elapsed = time.monotonic() - started

        # capture_retries=1 means only the readiness wait, not the blank
        # retry, could have rescued this capture.
        self.assertGreaterEqual(elapsed, 0.8)
        self.assertGreaterEqual(service.frames_drawn, 1)
        self.assertFalse(is_blank(image))
        self.assertEqual(scenes.read_patch(image), "0")

    def test_first_draw_slower_than_the_budget_exhausts_retries(self) -> None:
        service = self.start_service(startup_delay=30.0)
        player = self.player(service, ready_timeout=0.4, ready_poll=0.05)
        with self.assertRaises(RetryExhaustedError) as caught:
            player.capture("unused.png")
        self.assertIn("first draw", str(caught.exception))

    def test_a_service_that_never_draws_exhausts_retries(self) -> None:
        service = self.start_service(never_ready=True)
        player = self.player(service, ready_timeout=0.4, ready_poll=0.05)
        with self.assertRaises(RetryExhaustedError) as caught:
            player.capture("unused.png")
        self.assertIn("first draw", str(caught.exception))


class TestCaptureRetries(XServiceTestCase):
    def test_recovers_from_transient_blank_frames(self) -> None:
        service = self.start_service(blank_captures=2)
        player = self.player(service, capture_retries=5, capture_retry_delay=0.05)
        image = self.capture(player)
        self.assertFalse(is_blank(image))
        self.assertEqual(service.captures_served, 3)

    def test_persistent_blank_frames_exhaust_retries(self) -> None:
        service = self.start_service(blank_captures=50)
        player = self.player(service, capture_retries=3, capture_retry_delay=0.02)
        with self.assertRaises(RetryExhaustedError) as caught:
            player.capture("unused.png")
        self.assertIn("3 attempts", str(caught.exception))
        self.assertEqual(service.captures_served, 3)


class TestServerExit(XServiceTestCase):
    def test_connecting_to_a_dead_service_is_a_clear_error(self) -> None:
        port = closed_port()
        player = ScenePlayer(HOST, port)
        with self.assertRaises(ServerExitedError) as caught:
            player.connect()
        self.assertIn(str(port), str(caught.exception))
        self.assertIn("exited early", str(caught.exception))

    def test_a_service_dying_mid_session_is_a_clear_error(self) -> None:
        service = self.start_service(die_after_captures=0)
        player = self.player(service, capture_retries=3, capture_retry_delay=0.02)
        with self.assertRaises(ServerExitedError) as caught:
            player.capture("unused.png")
        self.assertIn("exited early", str(caught.exception))


class TestRedrawLatency(XServiceTestCase):
    def test_a_slow_redraw_completes_within_the_pause_boundary(self) -> None:
        service = self.start_service(draw_delay=0.4)
        player = self.player(service)
        player.key("s")
        player.pause(0.7)
        image = self.capture(player)
        self.assertEqual(scenes.read_patch(image), "s")


class TestPlayerCLI(XServiceTestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "vncdotool", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_a_successful_capture(self) -> None:
        service = self.start_service()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screen.png"
            result = self.run_cli(
                "--server",
                f"{HOST}:{service.port}",
                "key",
                "s",
                "pause",
                "0.2",
                "capture",
                str(path),
            )
            self.assertEqual(result.returncode, EXIT_OK, result.stderr)
            self.assertEqual(scenes.read_patch(Image.open(path)), "s")

    def test_a_dead_service_exits_with_its_own_code(self) -> None:
        port = closed_port()
        result = self.run_cli("--server", f"{HOST}:{port}", "capture", "x.png")
        self.assertEqual(result.returncode, EXIT_SERVER_EXITED)
        self.assertIn("exited early", result.stderr)

    def test_retry_exhaustion_exits_with_its_own_code(self) -> None:
        service = self.start_service(never_ready=True)
        result = self.run_cli(
            "--server",
            f"{HOST}:{service.port}",
            "--ready-timeout",
            "0.3",
            "--ready-poll",
            "0.05",
            "capture",
            "x.png",
        )
        self.assertEqual(result.returncode, EXIT_RETRY_EXHAUSTED)
        self.assertIn("first draw", result.stderr)
