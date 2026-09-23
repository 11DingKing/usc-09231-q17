"""Tests for the player/X synchronisation and retry boundary.

Covers the acceptance rule (only the first finished paint of the
requested scene is saved), the explicit failure results (service down /
service exited / retries exhausted by attempts or timeout), recovery from
transient grab errors and failed paints, and delay injection against the
real threaded X service.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from scene_player import scenes
from scene_player.player import (
    RetryExhaustedError,
    ScenePlayer,
    ServiceExitedError,
    ServiceNotRunningError,
)
from scene_player.xservice import XService

from .helpers import (
    BlankAfterReads,
    BlockingDrawService,
    FakeClock,
    GrabError,
    ScriptedGrabber,
    StubService,
    blank_frame,
    scene_frame,
)


class _PlayerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _path(self, name: str = "screen.ppm") -> Path:
        return self.tmp / name

    def _player(self, service, grabber, *, clock=None, **kwargs) -> ScenePlayer:
        clock = clock or FakeClock()
        return ScenePlayer(
            service,
            grabber,
            poll_interval=kwargs.pop("poll_interval", 0.1),
            timeout=kwargs.pop("timeout", 1.0),
            clock=clock.time,
            sleep=clock.sleep,
            **kwargs,
        )


class FirstFinishedPaintTest(_PlayerTest):
    def test_blank_race_frames_are_retried_not_saved(self) -> None:
        service = StubService()
        grabber = ScriptedGrabber([blank_frame(), blank_frame(), scene_frame("s")])
        result = self._player(service, grabber).capture_scene("s", self._path())

        self.assertEqual(service.pressed, ["s"])
        self.assertEqual(result.attempts, 3)
        self.assertEqual(scenes.decode_stamp(scenes.read_ppm(result.path)), "s")
        self.assertFalse(self._path().with_name("screen.ppm.part").exists())

    def test_a_stale_finished_frame_does_not_count(self) -> None:
        # The buffer shows the *previous* scene: finished, but not this paint.
        grabber = ScriptedGrabber([scene_frame("0"), scene_frame("s")])
        result = self._player(StubService(), grabber).capture_scene("s", self._path())
        self.assertEqual(result.attempts, 2)
        self.assertEqual(scenes.decode_stamp(scenes.read_ppm(result.path)), "s")

    def test_a_transient_grab_error_is_retried_and_recovers(self) -> None:
        grabber = ScriptedGrabber([GrabError("transport reset"), scene_frame("d")])
        result = self._player(StubService(), grabber).capture_scene("d", self._path())
        self.assertEqual(result.attempts, 2)
        self.assertEqual(scenes.decode_stamp(scenes.read_ppm(result.path)), "d")

    def test_every_rejected_frame_is_polled_for_with_a_delay(self) -> None:
        clock = FakeClock()
        grabber = ScriptedGrabber([blank_frame(), scene_frame("g")])
        player = ScenePlayer(
            StubService(), grabber, poll_interval=0.1, timeout=5.0,
            clock=clock.time, sleep=clock.sleep,
        )
        player.capture_scene("g", self._path())
        self.assertEqual(clock.sleeps, [0.1])

    def test_no_result_file_appears_before_the_frame_is_validated(self) -> None:
        # The draw never finishes: attempts run out and nothing is written,
        # not even the temp staging file.
        service = BlockingDrawService()
        grabber = ScriptedGrabber([blank_frame()])
        try:
            with self.assertRaises(RetryExhaustedError) as caught:
                self._player(service, grabber, max_attempts=2).capture_scene(
                    "s", self._path()
                )
            self.assertEqual(caught.exception.attempts, 2)
            self.assertIn("max_attempts=2", str(caught.exception))
            self.assertFalse(self._path().exists())
            self.assertFalse(self._path().with_name("screen.ppm.part").exists())
        finally:
            service.release_and_join()


class ServiceFailureTest(_PlayerTest):
    def test_capturing_before_start_is_a_clear_error(self) -> None:
        service = StubService(running=False)
        with self.assertRaises(ServiceNotRunningError):
            self._player(service, ScriptedGrabber([scene_frame("s")])).capture_scene(
                "s", self._path()
            )
        self.assertEqual(service.pressed, [])
        self.assertFalse(self._path().exists())

    def test_an_early_exit_while_waiting_is_reported_with_the_code(self) -> None:
        # Service stays up for the pre-flight check and two poll cycles, then
        # exits (code 2) before the finished frame ever arrives.
        service = BlankAfterReads(reads_before_exit=3, exit_code=2)
        grabber = ScriptedGrabber([blank_frame()])
        with self.assertRaises(ServiceExitedError) as caught:
            self._player(service, grabber, max_attempts=10).capture_scene(
                "s", self._path()
            )
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertEqual(caught.exception.attempts, 3)
        self.assertIn("exited (code=2)", str(caught.exception))
        self.assertIn("blank", str(caught.exception))
        self.assertFalse(self._path().exists())

    def test_an_exit_immediately_after_a_transient_grab_error_is_still_an_exit(self) -> None:
        service = BlankAfterReads(reads_before_exit=1, exit_code=1)
        grabber = ScriptedGrabber([GrabError("read failed")])
        with self.assertRaises(ServiceExitedError) as caught:
            self._player(service, grabber, max_attempts=10).capture_scene(
                "s", self._path()
            )
        self.assertEqual(caught.exception.exit_code, 1)
        self.assertEqual(caught.exception.attempts, 1)
        self.assertIsNone(caught.exception.last_frame)

    def test_retry_budget_exhausted_by_time_names_the_timeout(self) -> None:
        service = BlockingDrawService()
        grabber = ScriptedGrabber([blank_frame()])
        clock = FakeClock()
        try:
            with self.assertRaises(RetryExhaustedError) as caught:
                ScenePlayer(
                    service, grabber, poll_interval=0.1, timeout=0.2,
                    clock=clock.time, sleep=clock.sleep,
                ).capture_scene("s", self._path())
            self.assertEqual(caught.exception.attempts, 3)
            self.assertIn("timeout=0.2s", str(caught.exception))
            self.assertFalse(self._path().exists())
        finally:
            service.release_and_join()

    def test_persistent_grab_errors_exhaust_retries_without_a_file(self) -> None:
        grabber = ScriptedGrabber([GrabError("read failed")])
        with self.assertRaises(RetryExhaustedError) as caught:
            self._player(StubService(), grabber, max_attempts=3).capture_scene(
                "s", self._path()
            )
        self.assertEqual(caught.exception.attempts, 3)
        self.assertIsNotNone(caught.exception.last_error)
        self.assertFalse(self._path().exists())


class InjectedDelayRecoveryTest(_PlayerTest):
    """End-to-end against the real threaded X service with injected faults."""

    def test_a_slow_first_draw_is_caught_only_after_it_finishes(self) -> None:
        service = XService(draw_delay=0.05)
        service.start()
        try:
            player = ScenePlayer(service, poll_interval=0.01, timeout=2.0)
            result = player.capture_scene("s", self._path())
            self.assertGreaterEqual(result.attempts, 1)
            self.assertEqual(scenes.decode_stamp(scenes.read_ppm(result.path)), "s")
        finally:
            service.stop()

    def test_a_failed_paint_fails_the_capture_but_the_next_one_recovers(self) -> None:
        service = XService(fail_draws=1)
        service.start()
        player = ScenePlayer(service, poll_interval=0.01, timeout=0.15)
        try:
            with self.assertRaises(RetryExhaustedError):
                player.capture_scene("d", self._path("first.ppm"))
            self.assertFalse(self._path("first.ppm").exists())

            # Service stayed up; re-requesting the scene succeeds.
            result = player.capture_scene("d", self._path("second.ppm"))
            self.assertEqual(scenes.decode_stamp(scenes.read_ppm(result.path)), "d")
        finally:
            service.stop()

    def test_a_crash_amidst_polling_is_an_exit_result_not_a_timeout(self) -> None:
        def crash_on_wait(_seconds: float) -> None:
            service.crash(exit_code=3)

        service = XService(draw_delay=1.0, sleep=crash_on_wait)
        service.start()
        player = ScenePlayer(service, poll_interval=0.01, timeout=5.0)
        with self.assertRaises(ServiceExitedError) as caught:
            player.capture_scene("g", self._path())
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertFalse(self._path().exists())
        service.join(1.0)

    def test_real_wall_clock_rejects_a_capture_that_never_paints(self) -> None:
        # Belt-and-braces: the deadline works with the unpatched clock too.
        service = XService(draw_delay=10.0)
        service.start()
        player = ScenePlayer(service, poll_interval=0.02, timeout=0.1)
        try:
            started = time.monotonic()
            with self.assertRaises(RetryExhaustedError):
                player.capture_scene("0", self._path())
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            service.stop()


if __name__ == "__main__":
    unittest.main()
