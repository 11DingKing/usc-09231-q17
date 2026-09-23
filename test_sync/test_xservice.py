"""Delay/failure injection tests for the asynchronous X service.

These pin down the race itself: right after a key event the framebuffer
does not contain the finished frame, a failed paint leaves a visibly
unfinished buffer, and a crash during the draw means no frame is coming.
"""
from __future__ import annotations

import unittest

from scene_player import scenes
from scene_player.xservice import XService


class XServiceRaceTest(unittest.TestCase):
    def test_a_grab_racing_the_draw_sees_an_unfinished_buffer(self) -> None:
        service = XService(draw_delay=0.3)
        service.start()
        try:
            service.press_key("s")
            # The whole bug: the player used to save exactly this frame.
            self.assertTrue(service.framebuffer.snapshot().is_blank())
            service.join(1.0)
            frame = service.framebuffer.snapshot()
            self.assertFalse(frame.is_blank())
            self.assertEqual(scenes.decode_stamp(frame), "s")
        finally:
            service.stop()

    def test_a_failed_draw_leaves_a_blank_buffer_but_the_service_stays_up(self) -> None:
        service = XService(fail_draws=1)
        service.start()
        try:
            service.press_key("d")
            service.join(1.0)
            frame = service.framebuffer.snapshot()
            self.assertTrue(frame.is_blank())
            self.assertIsNone(scenes.decode_stamp(frame))
            self.assertTrue(service.running)
            self.assertIsNone(service.exit_code)

            # The service is still good for the next input: failure recovers.
            service.press_key("d")
            service.join(1.0)
            self.assertEqual(scenes.decode_stamp(service.framebuffer.snapshot()), "d")
        finally:
            service.stop()

    def test_a_crash_during_the_draw_commits_nothing(self) -> None:
        service = XService(draw_delay=5.0)
        service.start()
        service.press_key("g")
        service.crash(exit_code=1)
        service.join(1.0)
        self.assertFalse(service.running)
        self.assertEqual(service.exit_code, 1)
        self.assertTrue(service.framebuffer.snapshot().is_blank())

    def test_pressing_a_key_while_down_is_an_error(self) -> None:
        service = XService()
        with self.assertRaises(RuntimeError):
            service.press_key("s")


if __name__ == "__main__":
    unittest.main()
