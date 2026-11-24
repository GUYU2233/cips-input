"""Tests for ``core.policy``."""

from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

CIPS_DIR = Path(__file__).resolve().parents[1] / "src" / "input_methods" / "cips"
if str(CIPS_DIR) not in sys.path:
    sys.path.insert(0, str(CIPS_DIR))

from core.policy import (  # noqa: E402  (import after sys.path setup)
    CHUNK_MODES,
    Chunk,
    plan_chunks,
    plan_otp_chunks,
)


class ChunkTests(unittest.TestCase):
    def test_chunk_is_a_frozen_value(self):
        chunk = Chunk(text="a", wait_ms=5)
        self.assertEqual(chunk.text, "a")
        self.assertEqual(chunk.wait_ms, 5)
        self.assertEqual(chunk, Chunk("a", 5))
        with self.assertRaises(FrozenInstanceError):
            chunk.wait_ms = 6  # type: ignore[misc]


class ModeConstantTests(unittest.TestCase):
    def test_chunk_modes(self):
        self.assertEqual(CHUNK_MODES, ("char", "segmented"))

    def test_every_declared_mode_is_accepted(self):
        for mode in CHUNK_MODES:
            with self.subTest(mode=mode):
                self.assertTrue(plan_chunks("ab", mode=mode))

    def test_bulk_mode_is_gone(self):
        # Whole-string commits were removed with the F24 bridge; the mode name
        # must stay rejected so an old config cannot silently select it.
        self.assertNotIn("bulk", CHUNK_MODES)
        with self.assertRaises(ValueError):
            plan_chunks("ab", mode="bulk")


class CharModeTests(unittest.TestCase):
    def test_char_is_the_default_mode(self):
        chunks = plan_chunks("hello")
        self.assertEqual([chunk.text for chunk in chunks], list("hello"))
        self.assertEqual([chunk.wait_ms for chunk in chunks], [15] * 5)

    def test_one_chunk_per_code_point(self):
        chunks = plan_chunks("abc", mode="char", char_delay_ms=7)
        self.assertEqual([chunk.text for chunk in chunks], ["a", "b", "c"])
        self.assertEqual([chunk.wait_ms for chunk in chunks], [7, 7, 7])

    def test_default_delay_is_15ms(self):
        self.assertEqual(plan_chunks("a", mode="char")[0].wait_ms, 15)

    def test_custom_delays(self):
        for delay in (0, 1, 15, 120):
            with self.subTest(delay=delay):
                self.assertEqual(plan_chunks("a", mode="char", char_delay_ms=delay)[0].wait_ms, delay)

    def test_newline_is_its_own_chunk(self):
        chunks = plan_chunks("a\nb", mode="char", char_delay_ms=9)
        self.assertEqual([chunk.text for chunk in chunks], ["a", "\n", "b"])
        self.assertEqual([chunk.wait_ms for chunk in chunks], [9, 9, 9])

    def test_empty_text_yields_no_chunks(self):
        self.assertEqual(plan_chunks("", mode="char"), [])

    def test_emoji_stays_in_one_chunk(self):
        thumbs_up = "\U0001f44d"
        skin_tone = "\U0001f3fd"
        text = "a" + thumbs_up + skin_tone + "b"
        chunks = plan_chunks(text, mode="char")
        self.assertEqual([chunk.text for chunk in chunks], ["a", thumbs_up, skin_tone, "b"])
        self.assertEqual("".join(chunk.text for chunk in chunks), text)

    def test_astral_characters_never_split(self):
        text = "\U0001f600\U0001d54f\U0001f44d\U0001f3fd\u4e2d"
        chunks = plan_chunks(text, mode="char")
        self.assertEqual("".join(chunk.text for chunk in chunks), text)
        for chunk in chunks:
            # One chunk == one Unicode code point; encoding would explode on a
            # lone surrogate, which is exactly the "cut in half" bug.
            self.assertEqual(len(chunk.text), 1)
            chunk.text.encode("utf-8")

    def test_mixed_text_round_trips(self):
        text = "中文abc\n123\U0001f600"
        chunks = plan_chunks(text, mode="char")
        self.assertEqual("".join(chunk.text for chunk in chunks), text)
        self.assertEqual(len(chunks), len(text))

    def test_tab_is_its_own_chunk(self):
        chunks = plan_chunks("a\tb", mode="char")
        self.assertEqual([chunk.text for chunk in chunks], ["a", "\t", "b"])


class SegmentedModeTests(unittest.TestCase):
    def test_fixed_size_chunks(self):
        chunks = plan_chunks("abcdefghij", mode="segmented", chunk_size=4, chunk_wait_ms=11)
        self.assertEqual([chunk.text for chunk in chunks], ["abcd", "efgh", "ij"])
        self.assertEqual([chunk.wait_ms for chunk in chunks], [11, 11, 11])

    def test_remainder_is_last_chunk(self):
        chunks = plan_chunks("abcdefg", mode="segmented", chunk_size=3)
        self.assertEqual([chunk.text for chunk in chunks], ["abc", "def", "g"])

    def test_exact_multiple(self):
        chunks = plan_chunks("abcdef", mode="segmented", chunk_size=3)
        self.assertEqual([chunk.text for chunk in chunks], ["abc", "def"])

    def test_text_shorter_than_chunk_size(self):
        chunks = plan_chunks("ab", mode="segmented", chunk_size=64)
        self.assertEqual([chunk.text for chunk in chunks], ["ab"])

    def test_defaults(self):
        chunks = plan_chunks("x" * 65, mode="segmented")
        self.assertEqual([len(chunk.text) for chunk in chunks], [64, 1])
        self.assertEqual([chunk.wait_ms for chunk in chunks], [20, 20])

    def test_chunk_size_one_uses_chunk_wait_not_char_delay(self):
        chunks = plan_chunks("abc", mode="segmented", chunk_size=1, chunk_wait_ms=3, char_delay_ms=99)
        self.assertEqual([chunk.text for chunk in chunks], ["a", "b", "c"])
        self.assertEqual([chunk.wait_ms for chunk in chunks], [3, 3, 3])

    def test_astral_characters_not_split(self):
        chunks = plan_chunks("\U0001f600\U0001f600\U0001f600", mode="segmented", chunk_size=2)
        self.assertEqual([chunk.text for chunk in chunks], ["\U0001f600\U0001f600", "\U0001f600"])
        for chunk in chunks:
            chunk.text.encode("utf-8")

    def test_empty_text_yields_no_chunks(self):
        self.assertEqual(plan_chunks("", mode="segmented"), [])

    def test_non_positive_chunk_size_raises(self):
        for chunk_size in (0, -1):
            with self.subTest(chunk_size=chunk_size):
                with self.assertRaises(ValueError):
                    plan_chunks("abc", mode="segmented", chunk_size=chunk_size)

    def test_chunk_size_is_ignored_by_char_mode(self):
        self.assertEqual(len(plan_chunks("ab", mode="char", chunk_size=0)), 2)


class InvalidModeTests(unittest.TestCase):
    def test_unknown_mode_raises(self):
        for mode in ("", "chars", "BULK", "char ", "word", "segmneted", None):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    plan_chunks("abc", mode=mode)  # type: ignore[arg-type]

    def test_invalid_mode_raises_even_for_empty_text(self):
        with self.assertRaises(ValueError):
            plan_chunks("", mode="nope")

    def test_error_message_names_the_mode(self):
        with self.assertRaises(ValueError) as caught:
            plan_chunks("abc", mode="nope")
        self.assertIn("mode", str(caught.exception))


class OtpChunkTests(unittest.TestCase):
    def test_one_chunk_per_character(self):
        chunks = plan_otp_chunks("123456")
        self.assertEqual([chunk.text for chunk in chunks], list("123456"))
        self.assertEqual([chunk.wait_ms for chunk in chunks], [40] * 6)

    def test_custom_wait(self):
        self.assertEqual(plan_otp_chunks("12", per_box_wait_ms=80)[0].wait_ms, 80)

    def test_wait_also_follows_the_last_box(self):
        self.assertEqual(plan_otp_chunks("1")[0].wait_ms, 40)

    def test_trailing_tab_appended_to_every_chunk(self):
        chunks = plan_otp_chunks("123", trailing_tab=True)
        self.assertEqual([chunk.text for chunk in chunks], ["1\t", "2\t", "3\t"])

    def test_no_trailing_tab_by_default(self):
        self.assertNotIn("\t", "".join(chunk.text for chunk in plan_otp_chunks("123")))

    def test_empty_text_yields_no_chunks(self):
        self.assertEqual(plan_otp_chunks(""), [])
        self.assertEqual(plan_otp_chunks("", trailing_tab=True), [])

    def test_astral_character_is_one_chunk(self):
        self.assertEqual([chunk.text for chunk in plan_otp_chunks("\U0001f6001")], ["\U0001f600", "1"])

    def test_text_content_is_not_filtered(self):
        # Sanitizing (stripping spaces/dashes) is the caller's job, so the
        # planner must not silently drop anything.
        self.assertEqual([chunk.text for chunk in plan_otp_chunks("1 2")], ["1", " ", "2"])

    def test_round_trip_without_tabs(self):
        chunks = plan_otp_chunks("123456", trailing_tab=True)
        self.assertEqual("".join(chunk.text for chunk in chunks), "1\t2\t3\t4\t5\t6\t")


if __name__ == "__main__":
    unittest.main()
