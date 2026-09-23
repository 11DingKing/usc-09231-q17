"""Unit tests for scene rendering and the on-screen stamp."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scene_player import scenes
from scene_player.framebuffer import Frame
from scene_player.scenes import SCENES, decode_stamp, read_ppm, render, write_ppm


def _frame(key: str) -> Frame:
    return Frame(scenes.WIDTH, scenes.HEIGHT, render(key), key, 1)


class ScenesTest(unittest.TestCase):
    def test_base_has_more_than_one_colour(self) -> None:
        # A single-colour capture is what the readiness probe reads as
        # "this server has not drawn anything yet".
        base = scenes.base()
        self.assertGreater(len(set(base.pixels)), 1)
        self.assertTrue(not base.is_blank())

    def test_base_is_the_declared_size(self) -> None:
        self.assertEqual((scenes.WIDTH, scenes.HEIGHT), scenes.SIZE)
        self.assertEqual(len(scenes.base().pixels), scenes.WIDTH * scenes.HEIGHT * 3)

    def test_every_scene_is_deterministic(self) -> None:
        for key in SCENES:
            with self.subTest(key=key):
                self.assertEqual(render(key), render(key))

    def test_scroll_scene_depends_on_the_prior_screen(self) -> None:
        # "c" is a delta: scrolling a solid screen vs a dense one differs.
        solid = render("s")
        dense = render("d")
        self.assertNotEqual(render("c", solid), render("c", dense))

    def test_unknown_scene_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            render("z")

    def test_every_scene_stamp_round_trips(self) -> None:
        for key in SCENES:
            with self.subTest(key=key):
                self.assertEqual(decode_stamp(_frame(key)), key)

    def test_every_glyph_is_distinct_and_covers_the_catalogue(self) -> None:
        self.assertEqual(len(set(scenes.GLYPHS.values())), len(SCENES))
        self.assertEqual(sorted(scenes.GLYPHS), sorted(SCENES))

    def test_a_blank_screen_has_no_readable_stamp(self) -> None:
        black = Frame(scenes.WIDTH, scenes.HEIGHT, b"\x00" * (scenes.WIDTH * scenes.HEIGHT * 3), None, 1)
        white = Frame(scenes.WIDTH, scenes.HEIGHT, b"\xff" * (scenes.WIDTH * scenes.HEIGHT * 3), None, 1)
        self.assertIsNone(decode_stamp(black))
        self.assertIsNone(decode_stamp(white))

    def test_a_different_scene_stamp_is_not_this_scene(self) -> None:
        # A finished-but-stale frame must not satisfy a wait for another key.
        self.assertNotEqual(decode_stamp(_frame("0")), "s")

    def test_a_screen_too_small_to_hold_the_patch_has_no_stamp(self) -> None:
        tiny = Frame(8, 8, b"\x00" * 8 * 8 * 3, None, 1)
        self.assertIsNone(decode_stamp(tiny))
        with self.assertRaises(ValueError):
            scenes.stamp_patch(tiny.pixels, 8, 8, "s")

    def test_ppm_round_trip_preserves_the_picture(self) -> None:
        frame = _frame("g")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scene.ppm"
            write_ppm(path, frame)
            loaded = read_ppm(path)
        self.assertEqual(loaded.pixels, frame.pixels)
        self.assertEqual((loaded.width, loaded.height), scenes.SIZE)
        self.assertEqual(decode_stamp(loaded), "g")

    def test_a_truncated_ppm_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.ppm"
            path.write_bytes(b"P6\n10 10\n255\n\x00\x00\x00")
            with self.assertRaises(ValueError):
                read_ppm(path)


if __name__ == "__main__":
    unittest.main()
