"""Tests for the human rhythm planner in ``core.policy``."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

CIPS_DIR = Path(__file__).resolve().parents[1] / "src" / "input_methods" / "cips"
if str(CIPS_DIR) not in sys.path:
    sys.path.insert(0, str(CIPS_DIR))

from core.policy import (  # noqa: E402  (import after sys.path setup)
    HUMAN_DEFAULTS,
    normalize_human_settings,
    plan_human_chunks,
)

FLAT = {
    "base_delay_ms": 200,
    "jitter_ms": 0,
    "space_pause_ms": 100,
    "newline_pause_ms": 300,
    "punctuation_pause_ms": 50,
    "hesitation_chance": 0.0,
}


class PlanHumanChunksTest(unittest.TestCase):
    def test_empty_text_yields_nothing(self):
        self.assertEqual(plan_human_chunks(""), [])

    def test_one_chunk_per_code_point(self):
        chunks = plan_human_chunks("ab\nc", settings=FLAT)
        self.assertEqual([c.text for c in chunks], ["a", "b", "\n", "c"])

    def test_emoji_is_never_split(self):
        chunks = plan_human_chunks("\U0001f30f!", settings=FLAT)
        self.assertEqual([c.text for c in chunks], ["\U0001f30f", "!"])

    def test_plain_character_gets_the_base_delay(self):
        chunks = plan_human_chunks("abc", settings=FLAT)
        self.assertEqual([c.wait_ms for c in chunks], [200, 200, 200])

    def test_space_line_break_and_punctuation_add_their_pause(self):
        chunks = plan_human_chunks("a b\nc,d", settings=FLAT)
        waits = {c.text: c.wait_ms for c in chunks if c.text != "d"}
        self.assertEqual(waits[" "], 200 + 100)
        self.assertEqual(waits["\n"], 200 + 300)
        self.assertEqual(waits[","], 200 + 50)
        self.assertEqual(waits["a"], 200)

    def test_waits_are_never_negative(self):
        settings = dict(FLAT, base_delay_ms=10, jitter_ms=500)
        for chunk in plan_human_chunks("abcdefghij", settings=settings, rng=random.Random(7)):
            self.assertGreaterEqual(chunk.wait_ms, 0)

    def test_jitter_stays_inside_its_window(self):
        settings = dict(FLAT, base_delay_ms=100, jitter_ms=40)
        rng = random.Random(3)
        for chunk in plan_human_chunks("abcdefghijklmnop", settings=settings, rng=rng):
            self.assertGreaterEqual(chunk.wait_ms, 60)
            self.assertLessEqual(chunk.wait_ms, 140)

    def test_jitter_actually_varies(self):
        settings = dict(FLAT, jitter_ms=60)
        waits = {
            chunk.wait_ms
            for chunk in plan_human_chunks("abcdefghijkl", settings=settings, rng=random.Random(1))
        }
        self.assertGreater(len(waits), 1)

    def test_same_seed_gives_the_same_plan(self):
        first = plan_human_chunks("hello world", rng=random.Random(42))
        second = plan_human_chunks("hello world", rng=random.Random(42))
        self.assertEqual(first, second)

    def test_hesitation_can_stretch_a_pause(self):
        settings = dict(FLAT, base_delay_ms=100, jitter_ms=0, hesitation_chance=1.0,
                        hesitation_min_ms=500, hesitation_max_ms=500)
        for chunk in plan_human_chunks("abc", settings=settings):
            self.assertEqual(chunk.wait_ms, 600)


class NormalizeHumanSettingsTest(unittest.TestCase):
    def test_defaults_when_nothing_is_given(self):
        self.assertEqual(normalize_human_settings(None), dict(HUMAN_DEFAULTS))
        self.assertEqual(normalize_human_settings("nonsense"), dict(HUMAN_DEFAULTS))

    def test_overrides_win_and_types_are_coerced(self):
        config = normalize_human_settings({"base_delay_ms": "250", "hesitation_chance": 0.5})
        self.assertEqual(config["base_delay_ms"], 250)
        self.assertEqual(config["hesitation_chance"], 0.5)

    def test_unknown_keys_are_ignored(self):
        config = normalize_human_settings({"nope": 1})
        self.assertEqual(config, dict(HUMAN_DEFAULTS))

    def test_unusable_values_fall_back(self):
        config = normalize_human_settings({"base_delay_ms": "fast", "jitter_ms": None})
        self.assertEqual(config["base_delay_ms"], HUMAN_DEFAULTS["base_delay_ms"])
        self.assertEqual(config["jitter_ms"], HUMAN_DEFAULTS["jitter_ms"])

    def test_negatives_are_clamped_and_probability_is_capped(self):
        config = normalize_human_settings({"jitter_ms": -50, "hesitation_chance": 9})
        self.assertEqual(config["jitter_ms"], 0)
        self.assertEqual(config["hesitation_chance"], 1.0)

    def test_swapped_hesitation_range_is_repaired(self):
        config = normalize_human_settings({"hesitation_min_ms": 900, "hesitation_max_ms": 300})
        self.assertEqual(config["hesitation_min_ms"], 300)
        self.assertEqual(config["hesitation_max_ms"], 900)


if __name__ == "__main__":
    unittest.main()
