"""Tests for ``core.profile``."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

CIPS_DIR = Path(__file__).resolve().parents[1] / "src" / "input_methods" / "cips"
if str(CIPS_DIR) not in sys.path:
    sys.path.insert(0, str(CIPS_DIR))

from core.policy import plan_chunks, plan_otp_chunks  # noqa: E402
from core.profile import (  # noqa: E402  (import after sys.path setup)
    DEFAULT_PROFILE,
    Profile,
    ProfileBook,
)


class ProfileDataclassTests(unittest.TestCase):
    def test_defaults(self):
        profile = Profile(name="x")
        self.assertEqual(profile.name, "x")
        self.assertEqual(profile.match_exe, ())
        self.assertEqual(profile.match_class, ())
        self.assertEqual(profile.mode, "human_normal")
        self.assertEqual(profile.char_delay_ms, 60)
        self.assertEqual(profile.chunk_size, 64)
        self.assertEqual(profile.chunk_wait_ms, 30)
        self.assertEqual(profile.per_box_wait_ms, 60)
        self.assertFalse(profile.trailing_tab)

    def test_default_profile(self):
        self.assertEqual(DEFAULT_PROFILE, Profile(name="default"))
        self.assertEqual(DEFAULT_PROFILE.mode, "human_normal")

    def test_every_dispatch_mode_is_accepted(self):
        for mode in ("human_normal", "human", "char", "segmented", "otp"):
            with self.subTest(mode=mode):
                self.assertEqual(Profile(name="x", mode=mode).mode, mode)

    def test_retired_and_unknown_modes_are_rejected_by_the_parser(self):
        # "bulk" was removed in 0.3.0; a stale config must not select it.
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ("bulk", "human-normal", "chars", ""):
                with self.subTest(mode=mode):
                    path = Path(tmp) / "profiles.json"
                    path.write_text(
                        json.dumps(
                            {
                                "default": {"mode": mode},
                                "profiles": [{"name": "p", "match_exe": ["x"], "mode": mode}],
                            }
                        ),
                        encoding="utf-8",
                    )
                    book = ProfileBook.from_json(path)
                    self.assertEqual(book.default.mode, "human_normal")
                    self.assertEqual(book.profiles[0].mode, "human_normal")

    def test_negative_timings_are_clamped(self):
        profile = Profile(
            name="x",
            char_delay_ms=-5,
            chunk_size=0,
            chunk_wait_ms=-1,
            per_box_wait_ms=-9,
        )
        self.assertEqual(profile.char_delay_ms, 0)
        self.assertEqual(profile.chunk_size, 1)
        self.assertEqual(profile.chunk_wait_ms, 0)
        self.assertEqual(profile.per_box_wait_ms, 0)

    def test_profile_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            DEFAULT_PROFILE.mode = "char"  # type: ignore[misc]

    def test_profile_is_hashable(self):
        self.assertIsInstance(hash(Profile(name="x")), int)

    def test_single_string_pattern_is_coerced(self):
        profile = Profile(name="x", match_exe="chrome", match_class="Notepad")  # type: ignore[arg-type]
        self.assertEqual(profile.match_exe, ("chrome",))
        self.assertEqual(profile.match_class, ("Notepad",))

    def test_list_patterns_are_coerced_to_tuples(self):
        profile = Profile(name="x", match_exe=["chrome", "msedge"])  # type: ignore[arg-type]
        self.assertEqual(profile.match_exe, ("chrome", "msedge"))


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.chrome = Profile(name="chrome", match_exe=("chrome",), mode="segmented", chunk_size=20)
        self.notepad = Profile(name="notepad", match_exe=("notepad.exe",), mode="char", char_delay_ms=25)
        self.by_class = Profile(name="by_class", match_class=("SunAwtFrame",), chunk_wait_ms=99)
        self.both = Profile(
            name="both",
            match_exe=("chrome",),
            match_class=("Chrome_WidgetWin_1",),
            mode="char",
        )
        self.book = ProfileBook([self.chrome, self.notepad, self.by_class, self.both])

    def test_exe_exact_match(self):
        self.assertEqual(self.book.resolve("notepad.exe").name, "notepad")

    def test_exe_match_is_case_insensitive(self):
        self.assertEqual(self.book.resolve("NotePad.EXE").name, "notepad")

    def test_exe_pattern_without_extension_matches_exe(self):
        self.assertEqual(self.book.resolve("chrome.exe").name, "chrome")

    def test_exe_with_extension_pattern_does_not_match_bare_name(self):
        book = ProfileBook([Profile(name="p", match_exe=("chrome.exe",))])
        self.assertEqual(book.resolve("chrome").name, "default")

    def test_exe_stem_pattern_does_not_prefix_match(self):
        book = ProfileBook([Profile(name="p", match_exe=("chrome",))])
        self.assertEqual(book.resolve("chrome_helper.exe").name, "default")

    def test_exe_wildcard_prefix(self):
        book = ProfileBook([Profile(name="p", match_exe=("chrome*",))])
        self.assertEqual(book.resolve("chrome.exe").name, "p")
        self.assertEqual(book.resolve("chromedriver.exe").name, "p")
        self.assertEqual(book.resolve("msedge.exe").name, "default")

    def test_exe_star_matches_everything(self):
        book = ProfileBook([Profile(name="p", match_exe=("*",))])
        for exe in ("a.exe", "anything", "C:\\tools\\x.exe"):
            with self.subTest(exe=exe):
                self.assertEqual(book.resolve(exe).name, "p")

    def test_exe_full_path_is_reduced_to_file_name(self):
        path = "C:\\Program Files\\Google\\Chrome\\Application\\Chrome.EXE"
        self.assertEqual(self.book.resolve(path).name, "chrome")

    def test_quoted_path_from_command_line(self):
        path = '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"'
        self.assertEqual(self.book.resolve(path).name, "chrome")

    def test_posix_style_path(self):
        book = ProfileBook([Profile(name="p", match_exe=("chrome",))])
        self.assertEqual(book.resolve("/usr/bin/chrome").name, "p")

    def test_class_match_is_case_insensitive(self):
        self.assertEqual(self.book.resolve(window_class="SUNAWTFRAME").name, "by_class")
        self.assertEqual(self.book.resolve(window_class="sunawtframe").name, "by_class")

    def test_class_wildcard(self):
        book = ProfileBook([Profile(name="p", match_class=("Chrome_WidgetWin_*",))])
        self.assertEqual(book.resolve(window_class="Chrome_WidgetWin_1").name, "p")
        self.assertEqual(book.resolve(window_class="Notepad").name, "default")

    def test_only_exe_known(self):
        self.assertEqual(self.book.resolve("notepad.exe").name, "notepad")

    def test_only_class_known(self):
        self.assertEqual(self.book.resolve(window_class="SunAwtFrame").name, "by_class")

    def test_both_match_wins_over_exe_only(self):
        # `both` is listed last but is the most specific match.
        self.assertEqual(self.book.resolve("chrome.exe", "Chrome_WidgetWin_1").name, "both")

    def test_exe_match_wins_over_class_match(self):
        # `both` does not match here (class differs), `by_class` is listed
        # before `chrome` but exe-only beats class-only.
        self.assertEqual(self.book.resolve("chrome.exe", "SunAwtFrame").name, "chrome")

    def test_unknown_process_falls_back_to_default(self):
        self.assertEqual(self.book.resolve("explorer.exe", "CabinetWClass"), DEFAULT_PROFILE)

    def test_no_arguments_falls_back_to_default(self):
        self.assertEqual(self.book.resolve(), DEFAULT_PROFILE)
        self.assertEqual(self.book.resolve(None, None), DEFAULT_PROFILE)

    def test_empty_strings_fall_back_to_default(self):
        self.assertEqual(self.book.resolve("", ""), DEFAULT_PROFILE)
        self.assertEqual(self.book.resolve("   "), DEFAULT_PROFILE)

    def test_returned_profile_keeps_its_tuning(self):
        resolved = self.book.resolve("chrome.exe")
        self.assertIs(resolved, self.chrome)
        self.assertEqual(resolved.mode, "segmented")
        self.assertEqual(resolved.chunk_size, 20)

    def test_custom_default_is_returned(self):
        custom = Profile(name="fallback", mode="char")
        book = ProfileBook([], custom)
        self.assertEqual(book.resolve("x.exe"), custom)

    def test_unconstrained_profile_never_shadows(self):
        book = ProfileBook([Profile(name="everything"), Profile(name="p", match_exe=("foo",))])
        self.assertEqual(book.resolve("foo.exe").name, "p")
        self.assertEqual(book.resolve("bar.exe").name, "default")


class PriorityOrderTests(unittest.TestCase):
    def test_specificity_beats_list_order(self):
        by_class = Profile(name="class", match_class=("FooClass",))
        by_exe = Profile(name="exe", match_exe=("foo",))
        both = Profile(name="both", match_exe=("foo",), match_class=("FooClass",))
        book = ProfileBook([by_class, by_exe, both])
        self.assertEqual(book.resolve("foo.exe", "FooClass").name, "both")
        self.assertEqual(book.resolve("foo.exe", "OtherClass").name, "exe")
        self.assertEqual(book.resolve("bar.exe", "FooClass").name, "class")

    def test_first_listed_wins_inside_a_bucket(self):
        first = Profile(name="first", match_exe=("foo",))
        second = Profile(name="second", match_exe=("foo*",))
        self.assertEqual(ProfileBook([first, second]).resolve("foo.exe").name, "first")
        self.assertEqual(ProfileBook([second, first]).resolve("foo.exe").name, "second")

    def test_profile_constraining_both_needs_both_to_match(self):
        both = Profile(name="both", match_exe=("foo",), match_class=("FooClass",))
        self.assertEqual(ProfileBook([both]).resolve("foo.exe", "OtherClass").name, "default")
        self.assertEqual(ProfileBook([both]).resolve("bar.exe", "FooClass").name, "default")


class BookBasicsTests(unittest.TestCase):
    def test_empty_book(self):
        book = ProfileBook([])
        self.assertEqual(len(book), 0)
        self.assertEqual(book.profiles, ())
        self.assertEqual(book.resolve("chrome.exe"), DEFAULT_PROFILE)

    def test_profiles_property_is_ordered_tuple(self):
        first = Profile(name="a")
        second = Profile(name="b")
        book = ProfileBook([first, second])
        self.assertEqual(book.profiles, (first, second))
        self.assertEqual(len(book), 2)

    def test_repr_is_short(self):
        self.assertIn("ProfileBook", repr(ProfileBook([Profile(name="a")])))


class FromJsonHappyPathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write_json(self, payload, name="profiles.json"):
        path = self.dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def write_raw(self, text, name="profiles.json"):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_full_document(self):
        path = self.write_json(
            {
                "default": {"name": "default", "mode": "char", "char_delay_ms": 30},
                "profiles": [
                    {
                        "name": "chrome",
                        "match_exe": ["chrome", "msedge"],
                        "mode": "segmented",
                        "chunk_size": 32,
                        "chunk_wait_ms": 12,
                    },
                    {
                        "name": "otp",
                        "match_class": ["OTPBox"],
                        "mode": "char",
                        "per_box_wait_ms": 60,
                        "trailing_tab": True,
                    },
                ],
            }
        )
        book = ProfileBook.from_json(path)
        self.assertEqual(len(book), 2)
        self.assertEqual(book.default.mode, "char")
        self.assertEqual(book.default.char_delay_ms, 30)
        self.assertEqual(book.resolve("chrome.exe").name, "chrome")
        self.assertEqual(book.resolve("chrome.exe").chunk_size, 32)
        otp = book.resolve(window_class="otpbox")
        self.assertEqual(otp.name, "otp")
        self.assertEqual(otp.per_box_wait_ms, 60)
        self.assertTrue(otp.trailing_tab)
        self.assertEqual(book.resolve("explorer.exe"), book.default)

    def test_missing_fields_are_filled_with_defaults(self):
        path = self.write_json({"profiles": [{"name": "minimal", "match_exe": ["foo"]}]})
        profile = ProfileBook.from_json(path).resolve("foo.exe")
        self.assertEqual(profile.name, "minimal")
        self.assertEqual(profile.match_class, ())
        self.assertEqual(profile.mode, "human_normal")
        self.assertEqual(profile.char_delay_ms, 60)
        self.assertEqual(profile.chunk_size, 64)
        self.assertEqual(profile.chunk_wait_ms, 30)
        self.assertEqual(profile.per_box_wait_ms, 60)
        self.assertFalse(profile.trailing_tab)

    def test_unknown_fields_are_ignored(self):
        path = self.write_json(
            {
                "profiles": [
                    {"name": "p", "match_exe": ["foo"], "bogus": 1, "nested": {"a": [1, 2]}}
                ]
            }
        )
        profile = ProfileBook.from_json(path).resolve("foo.exe")
        self.assertEqual(profile.name, "p")

    def test_default_block_is_optional(self):
        path = self.write_json({"profiles": []})
        book = ProfileBook.from_json(path)
        self.assertEqual(book.default, DEFAULT_PROFILE)

    def test_default_name_falls_back_when_absent(self):
        path = self.write_json({"default": {"mode": "segmented", "chunk_size": 8}})
        book = ProfileBook.from_json(path)
        self.assertEqual(book.default.name, "default")
        self.assertEqual(book.default.mode, "segmented")
        self.assertEqual(book.default.chunk_size, 8)

    def test_single_string_pattern_is_accepted(self):
        path = self.write_json({"profiles": [{"name": "p", "match_exe": "chrome"}]})
        self.assertEqual(ProfileBook.from_json(path).resolve("chrome.exe").name, "p")

    def test_blank_patterns_are_dropped(self):
        path = self.write_json({"profiles": [{"name": "p", "match_exe": ["", "  ", " chrome "]}]})
        profile = ProfileBook.from_json(path).profiles[0]
        self.assertEqual(profile.match_exe, ("chrome",))

    def test_missing_name_gets_a_synthetic_one(self):
        path = self.write_json({"profiles": [{"match_exe": ["foo"]}, {"name": "  "}]})
        book = ProfileBook.from_json(path)
        self.assertEqual(book.profiles[0].name, "profile_0")
        self.assertEqual(book.profiles[1].name, "profile_1")

    def test_invalid_values_fall_back(self):
        path = self.write_json(
            {
                "profiles": [
                    {
                        "name": "p",
                        "match_exe": 42,
                        "match_class": {"a": 1},
                        "mode": "segmneted",
                        "char_delay_ms": "fast",
                        "chunk_size": None,
                        "chunk_wait_ms": 12.5,
                        "per_box_wait_ms": True,
                        "trailing_tab": "false",
                    }
                ]
            }
        )
        profile = ProfileBook.from_json(path).profiles[0]
        self.assertEqual(profile.match_exe, ())
        self.assertEqual(profile.match_class, ())
        self.assertEqual(profile.mode, "human_normal")
        self.assertEqual(profile.char_delay_ms, 60)
        self.assertEqual(profile.chunk_size, 64)
        self.assertEqual(profile.chunk_wait_ms, 30)
        self.assertEqual(profile.per_box_wait_ms, 60)
        self.assertFalse(profile.trailing_tab)

    def test_integral_float_is_accepted_for_int_fields(self):
        path = self.write_json({"profiles": [{"name": "p", "match_exe": ["foo"], "char_delay_ms": 20.0}]})
        self.assertEqual(ProfileBook.from_json(path).profiles[0].char_delay_ms, 20)

    def test_non_list_profiles_key_is_ignored(self):
        path = self.write_json({"default": {"mode": "char"}, "profiles": {"name": "p"}})
        book = ProfileBook.from_json(path)
        self.assertEqual(len(book), 0)
        self.assertEqual(book.default.mode, "char")

    def test_non_mapping_entries_are_skipped(self):
        path = self.write_json(
            {"profiles": ["nope", 7, None, {"name": "p", "match_exe": ["foo"]}]}
        )
        book = ProfileBook.from_json(path)
        self.assertEqual(len(book), 1)
        self.assertEqual(book.resolve("foo.exe").name, "p")

    def test_path_accepts_string(self):
        path = self.write_json({"profiles": [{"name": "p", "match_exe": ["foo"]}]})
        self.assertEqual(ProfileBook.from_json(str(path)).resolve("foo.exe").name, "p")


class FromJsonToleranceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def assert_default_only(self, book):
        self.assertIsInstance(book, ProfileBook)
        self.assertEqual(len(book.profiles), 0)
        self.assertEqual(book.default, DEFAULT_PROFILE)
        self.assertEqual(book.resolve("chrome.exe").name, "default")

    def test_missing_file(self):
        self.assert_default_only(ProfileBook.from_json(self.dir / "does-not-exist.json"))

    def test_path_is_a_directory(self):
        directory = self.dir / "sub"
        directory.mkdir()
        self.assert_default_only(ProfileBook.from_json(directory))

    def test_truncated_json(self):
        path = self.dir / "broken.json"
        path.write_text('{"profiles": [{"name": "p"', encoding="utf-8")
        self.assert_default_only(ProfileBook.from_json(path))

    def test_empty_file(self):
        path = self.dir / "empty.json"
        path.write_text("", encoding="utf-8")
        self.assert_default_only(ProfileBook.from_json(path))

    def test_json_scalar_root(self):
        for payload in ("null", "42", '"text"'):
            with self.subTest(payload=payload):
                path = self.dir / "scalar.json"
                path.write_text(payload, encoding="utf-8")
                self.assert_default_only(ProfileBook.from_json(path))

    def test_json_list_root(self):
        path = self.dir / "list.json"
        path.write_text('[{"name": "p", "match_exe": ["foo"]}]', encoding="utf-8")
        self.assert_default_only(ProfileBook.from_json(path))

    def test_non_utf8_bytes(self):
        path = self.dir / "gbk.json"
        path.write_bytes(b'{"profiles": [{"name": "\xd6\xd0"}]}')
        self.assert_default_only(ProfileBook.from_json(path))

    def test_none_path(self):
        self.assert_default_only(ProfileBook.from_json(None))  # type: ignore[arg-type]


class ToJsonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_round_trip(self):
        default = Profile(name="default", mode="segmented", chunk_size=16, trailing_tab=True)
        chrome = Profile(name="chrome", match_exe=("chrome", "msedge"), mode="char", char_delay_ms=5)
        otp = Profile(name="otp", match_class=("OTPBox",), per_box_wait_ms=60, trailing_tab=True)
        original = ProfileBook([chrome, otp], default)

        path = self.dir / "out.json"
        original.to_json(path)
        restored = ProfileBook.from_json(path)

        self.assertEqual(restored.default, default)
        self.assertEqual(restored.profiles, (chrome, otp))
        self.assertEqual(restored.resolve("chrome.exe"), chrome)

    def test_written_file_is_readable_json(self):
        ProfileBook([Profile(name="p", match_exe=("foo",))]).to_json(self.dir / "out.json")
        payload = json.loads((self.dir / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"default", "profiles"})
        self.assertIsInstance(payload["profiles"][0]["match_exe"], list)

    def test_written_file_keeps_non_ascii_names(self):
        ProfileBook([], Profile(name="備忘錄")).to_json(self.dir / "out.json")
        payload = json.loads((self.dir / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["default"]["name"], "備忘錄")


class ProfileToPolicyIntegrationTests(unittest.TestCase):
    """The resolved profile must be consumable by ``core.policy`` as-is."""

    def test_segmented_profile_drives_plan_chunks(self):
        book = ProfileBook(
            [
                Profile(
                    name="chrome",
                    match_exe=("chrome",),
                    mode="segmented",
                    chunk_size=3,
                    chunk_wait_ms=5,
                )
            ]
        )
        profile = book.resolve("chrome.exe")
        chunks = plan_chunks(
            "abcdef",
            mode=profile.mode,
            char_delay_ms=profile.char_delay_ms,
            chunk_size=profile.chunk_size,
            chunk_wait_ms=profile.chunk_wait_ms,
        )
        self.assertEqual([chunk.text for chunk in chunks], ["abc", "def"])
        self.assertEqual([chunk.wait_ms for chunk in chunks], [5, 5])

    def test_otp_profile_drives_plan_otp_chunks(self):
        book = ProfileBook(
            [
                Profile(
                    name="otp",
                    match_class=("OTPBox",),
                    per_box_wait_ms=55,
                    trailing_tab=True,
                )
            ]
        )
        profile = book.resolve(window_class="OTPBox")
        chunks = plan_otp_chunks(
            "12",
            per_box_wait_ms=profile.per_box_wait_ms,
            trailing_tab=profile.trailing_tab,
        )
        self.assertEqual([chunk.text for chunk in chunks], ["1\t", "2\t"])
        self.assertEqual([chunk.wait_ms for chunk in chunks], [55, 55])


if __name__ == "__main__":
    unittest.main()
