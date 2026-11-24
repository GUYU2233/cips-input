"""Tests for ``core.strategy``."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

CIPS_DIR = Path(__file__).resolve().parents[1] / "src" / "input_methods" / "cips"
if str(CIPS_DIR) not in sys.path:
    sys.path.insert(0, str(CIPS_DIR))

from core.strategy import (  # noqa: E402  (import after sys.path setup)
    ACTIONS,
    DISPATCH_MODES,
    FAST_CHAR_DELAY_MS,
    DispatchPlan,
    resolve_dispatch,
)


class ConstantTests(unittest.TestCase):
    def test_dispatch_modes_are_exactly_the_supported_strategies(self):
        self.assertEqual(
            DISPATCH_MODES,
            ("human_normal", "human", "char", "segmented", "otp"),
        )

    def test_actions_do_not_include_replay(self):
        # Replay reuses a stored plan, so it is not resolved from an action.
        self.assertEqual(ACTIONS, ("auto", "human", "fast", "otp"))
        self.assertNotIn("replay", ACTIONS)

    def test_fast_delay_is_75(self):
        self.assertEqual(FAST_CHAR_DELAY_MS, 75)


class ForcedActionTests(unittest.TestCase):
    def test_human_forces_the_slow_rhythm(self):
        plan = resolve_dispatch("human", "char", 60)
        self.assertEqual(plan, DispatchPlan("human"))
        self.assertEqual(plan.char_delay_ms, 0)

    def test_fast_forces_fixed_75ms_char_mode(self):
        plan = resolve_dispatch("fast", "human_normal", 60)
        self.assertEqual(plan, DispatchPlan("char", FAST_CHAR_DELAY_MS))

    def test_otp_forces_the_otp_layout(self):
        plan = resolve_dispatch("otp", "human_normal", 60)
        self.assertEqual(plan, DispatchPlan("otp"))

    def test_forced_actions_ignore_the_profile(self):
        for action in ("human", "fast", "otp"):
            with self.subTest(action=action):
                self.assertEqual(
                    resolve_dispatch(action, "char", 60),
                    resolve_dispatch(action, "human_normal", 999),
                )


class AutoUsesTheProfileTests(unittest.TestCase):
    def test_human_normal_profile(self):
        self.assertEqual(resolve_dispatch("auto", "human_normal", 60), DispatchPlan("human_normal"))

    def test_human_profile(self):
        self.assertEqual(resolve_dispatch("auto", "human", 60), DispatchPlan("human"))

    def test_otp_profile(self):
        self.assertEqual(resolve_dispatch("auto", "otp", 60), DispatchPlan("otp"))

    def test_char_profile_uses_its_own_delay(self):
        self.assertEqual(resolve_dispatch("auto", "char", 75), DispatchPlan("char", 75))

    def test_segmented_profile_uses_its_own_delay(self):
        self.assertEqual(resolve_dispatch("auto", "segmented", 40), DispatchPlan("segmented", 40))

    def test_char_delay_is_never_negative(self):
        self.assertEqual(resolve_dispatch("auto", "char", -20).char_delay_ms, 0)

    def test_unknown_profile_mode_degrades_to_human_normal(self):
        # The profile parser rejects unknown modes already; this is the second
        # line of defence so a future/typo'd value cannot break typing.
        for mode in ("", "bulk", "HUMAN_NORMAL", "human-normal", None):
            with self.subTest(mode=mode):
                self.assertEqual(
                    resolve_dispatch("auto", mode, 60),  # type: ignore[arg-type]
                    DispatchPlan("human_normal"),
                )


class UnknownActionTests(unittest.TestCase):
    def test_unknown_action_raises(self):
        for action in ("", "auto ", "replay", None):
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    resolve_dispatch(action, "human_normal", 60)  # type: ignore[arg-type]

    def test_error_message_names_the_action(self):
        with self.assertRaises(ValueError) as caught:
            resolve_dispatch("nope", "human_normal", 60)
        self.assertIn("nope", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
