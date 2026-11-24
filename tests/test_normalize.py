"""Tests for ``core.normalize``."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

CIPS_DIR = Path(__file__).resolve().parents[1] / "src" / "input_methods" / "cips"
if str(CIPS_DIR) not in sys.path:
    sys.path.insert(0, str(CIPS_DIR))

from core.normalize import (  # noqa: E402  (import after sys.path setup)
    MAX_TEXT_LENGTH,
    normalize_text,
    should_refuse,
    strip_edges,
)


class NewlineTests(unittest.TestCase):
    def test_crlf_becomes_lf(self):
        self.assertEqual(normalize_text("a\r\nb"), "a\nb")

    def test_lone_cr_becomes_lf(self):
        self.assertEqual(normalize_text("a\rb"), "a\nb")

    def test_mixed_newlines(self):
        self.assertEqual(normalize_text("a\r\nb\rc\nd"), "a\nb\nc\nd")

    def test_consecutive_crlf(self):
        self.assertEqual(normalize_text("a\r\n\r\nb"), "a\n\nb")

    def test_crlf_before_lf(self):
        self.assertEqual(normalize_text("a\r\n\nb"), "a\n\nb")

    def test_empty_string(self):
        self.assertEqual(normalize_text(""), "")

    def test_newline_and_tab_are_preserved(self):
        self.assertEqual(normalize_text("a\tb\nc"), "a\tb\nc")

    def test_text_without_newlines_is_untouched(self):
        self.assertEqual(normalize_text("hello"), "hello")


class InvisibleCharacterTests(unittest.TestCase):
    def test_zero_width_range_removed(self):
        for code_point in range(0x200B, 0x2010):  # U+200B..U+200F
            with self.subTest(code_point=hex(code_point)):
                self.assertEqual(normalize_text(f"a{chr(code_point)}b"), "ab")

    def test_bidi_formatting_range_removed(self):
        for code_point in range(0x202A, 0x202F):  # U+202A..U+202E
            with self.subTest(code_point=hex(code_point)):
                self.assertEqual(normalize_text(f"a{chr(code_point)}b"), "ab")

    def test_bom_removed(self):
        self.assertEqual(normalize_text("\ufeffabc\ufeff"), "abc")

    def test_soft_hyphen_removed(self):
        self.assertEqual(normalize_text("ab\u00adc"), "abc")

    def test_zero_width_joiner_flattens_emoji_sequences(self):
        # U+200D is inside the stripped range, so a ZWJ sequence loses its
        # joiners and renders as the individual glyphs. Documented on purpose:
        # the TSF side cannot represent joiner sequences through WM_CHAR.
        family = "\U0001f468\u200d\U0001f469"
        self.assertEqual(normalize_text(family), "\U0001f468\U0001f469")


class ControlCharacterTests(unittest.TestCase):
    def test_c0_controls_removed(self):
        code_points = (
            list(range(0x00, 0x09))  # NUL..BS
            + [0x0B, 0x0C]  # VT, FF
            + list(range(0x0E, 0x20))  # SO..US
            + [0x7F]  # DEL
        )
        for code_point in code_points:
            with self.subTest(code_point=hex(code_point)):
                self.assertEqual(normalize_text(f"a{chr(code_point)}b"), "ab")

    def test_all_strippable_controls_leave_nothing(self):
        text = "".join(chr(code_point) for code_point in list(range(0x00, 0x09)) + [0x0B, 0x0C, 0x0E, 0x1F, 0x7F])
        self.assertEqual(normalize_text(text), "")

    def test_c1_controls_are_not_touched(self):
        # The rules only cover C0 + DEL, so C1 stays: deleting more would be a
        # silent behaviour change for the caller.
        self.assertEqual(normalize_text("\x85"), "\x85")

    def test_non_ascii_whitespace_survives(self):
        self.assertEqual(normalize_text("a\u00a0b\u3000c"), "a\u00a0b\u3000c")


class NoUnicodeNormalizationTests(unittest.TestCase):
    def test_combining_marks_are_not_composed(self):
        decomposed = "e\u0301"  # e + COMBINING ACUTE
        self.assertEqual(normalize_text(decomposed), decomposed)
        self.assertEqual(len(normalize_text(decomposed)), 2)

    def test_precomposed_form_is_preserved(self):
        self.assertEqual(normalize_text("\u00e9"), "\u00e9")

    def test_hangul_jamo_is_not_composed(self):
        jamo = "\u1112\u1161\u11ab"
        self.assertEqual(normalize_text(jamo), jamo)
        self.assertEqual(len(normalize_text(jamo)), 3)

    def test_chinese_text_is_unchanged(self):
        text = "中文測試，繁體與简体混合 𠀀"
        self.assertEqual(normalize_text(text), text)

    def test_fullwidth_forms_are_unchanged(self):
        self.assertEqual(normalize_text("ＡＢＣ１２３"), "ＡＢＣ１２３")


class WhitespaceTests(unittest.TestCase):
    def test_leading_and_trailing_whitespace_kept(self):
        raw = "  hello \n\t"
        self.assertEqual(normalize_text(raw), raw)

    def test_strip_edges_opts_in(self):
        self.assertEqual(strip_edges("  hello \n\t"), "hello")

    def test_strip_edges_keeps_inner_whitespace(self):
        self.assertEqual(strip_edges(" a\tb "), "a\tb")

    def test_strip_edges_on_empty_string(self):
        self.assertEqual(strip_edges(""), "")

    def test_strip_edges_on_whitespace_only(self):
        self.assertEqual(strip_edges(" \t\n"), "")

    def test_strip_edges_does_not_normalize(self):
        self.assertEqual(strip_edges(" \u200b "), "\u200b")


class IdempotenceTests(unittest.TestCase):
    def test_normalize_is_idempotent(self):
        raw = "a\r\nb\u200b\x00c\t\ufeff"
        once = normalize_text(raw)
        self.assertEqual(normalize_text(once), once)


class ShouldRefuseTests(unittest.TestCase):
    def test_limit_constant(self):
        self.assertEqual(MAX_TEXT_LENGTH, 20000)

    def test_empty_string_is_refused(self):
        self.assertEqual(should_refuse(""), "empty")

    def test_whitespace_only_is_not_empty(self):
        # Whitespace may be intentional (indentation, blank line); trimming is
        # an explicit decision by the caller.
        self.assertIsNone(should_refuse(" "))
        self.assertIsNone(should_refuse("\n"))

    def test_zero_width_only_collapses_to_empty(self):
        collapsed = normalize_text("\u200b\ufeff\u200d")
        self.assertEqual(collapsed, "")
        self.assertEqual(should_refuse(collapsed), "empty")

    def test_exact_limit_is_allowed(self):
        self.assertIsNone(should_refuse("x" * MAX_TEXT_LENGTH))

    def test_one_over_limit_is_refused(self):
        self.assertEqual(should_refuse("x" * (MAX_TEXT_LENGTH + 1)), "too_long")

    def test_length_counts_code_points_not_utf16_units(self):
        self.assertIsNone(should_refuse("\U0001f600" * MAX_TEXT_LENGTH))
        self.assertEqual(
            should_refuse("\U0001f600" * (MAX_TEXT_LENGTH + 1)),
            "too_long",
        )

    def test_normal_payload_passes(self):
        self.assertIsNone(should_refuse("hello 世界"))


if __name__ == "__main__":
    unittest.main()
