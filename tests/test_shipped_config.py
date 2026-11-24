"""Guard tests for the files that ship with the backend.

``profiles.json`` and ``ime.json`` are the two documents a user is expected to
edit, so they must stay consistent with the code that reads them. These tests
run against the real files in ``src/input_methods/cips`` -- not fixtures -- so a
bad edit fails CI instead of being discovered as silent behaviour.
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CIPS_DIR = REPO_ROOT / "src" / "input_methods" / "cips"
if str(CIPS_DIR) not in sys.path:
    sys.path.insert(0, str(CIPS_DIR))

from core.policy import HUMAN_DEFAULTS, normalize_human_settings  # noqa: E402
from core.profile import ProfileBook  # noqa: E402
from core.strategy import DISPATCH_MODES  # noqa: E402

PROFILES_PATH = CIPS_DIR / "profiles.json"
IME_PATH = CIPS_DIR / "ime.json"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ShippedProfilesTests(unittest.TestCase):
    def setUp(self):
        self.raw = load_json(PROFILES_PATH)
        self.book = ProfileBook.from_json(PROFILES_PATH)

    def test_document_has_only_known_top_level_keys(self):
        self.assertEqual(set(self.raw), {"human", "human_normal", "default", "profiles"})

    def test_default_uses_the_standard_human_rhythm(self):
        self.assertEqual(self.book.default.mode, "human_normal")
        self.assertEqual(self.book.default.name, "default")

    def test_every_profile_mode_is_dispatchable(self):
        self.assertTrue(self.book.profiles, "shipped config should demonstrate at least one profile")
        for profile in self.book.profiles:
            with self.subTest(profile=profile.name):
                self.assertIn(profile.mode, DISPATCH_MODES)

    def test_every_profile_matches_something(self):
        for profile in self.book.profiles:
            with self.subTest(profile=profile.name):
                self.assertTrue(
                    profile.match_exe or profile.match_class,
                    f"{profile.name} has no match rule and can never be selected",
                )

    def test_shipped_profiles_are_not_inert(self):
        # A profile that resolves to exactly the default settings is dead config:
        # it looks tunable but changes nothing. Every shipped entry must differ.
        default = self.book.default
        for profile in self.book.profiles:
            with self.subTest(profile=profile.name):
                self.assertNotEqual(
                    (profile.mode, profile.char_delay_ms, profile.chunk_size,
                     profile.chunk_wait_ms, profile.per_box_wait_ms, profile.trailing_tab),
                    (default.mode, default.char_delay_ms, default.chunk_size,
                     default.chunk_wait_ms, default.per_box_wait_ms, default.trailing_tab),
                )

    def test_human_blocks_are_usable(self):
        for name in ("human", "human_normal"):
            with self.subTest(block=name):
                raw = self.raw[name]
                self.assertIsInstance(raw, dict)
                self.assertEqual(set(raw), set(HUMAN_DEFAULTS))
                merged = normalize_human_settings(raw)
                self.assertGreater(merged["base_delay_ms"], 0)
                self.assertGreaterEqual(merged["jitter_ms"], 0)
                self.assertGreaterEqual(merged["space_pause_ms"], 0)
                self.assertGreaterEqual(merged["newline_pause_ms"], 0)
                self.assertGreaterEqual(merged["punctuation_pause_ms"], 0)
                self.assertGreaterEqual(merged["hesitation_chance"], 0)
                self.assertLessEqual(merged["hesitation_chance"], 1)
                self.assertLessEqual(merged["hesitation_min_ms"], merged["hesitation_max_ms"])

    def test_normal_rhythm_is_faster_than_the_slow_rhythm(self):
        normal = normalize_human_settings(self.raw["human_normal"])
        slow = normalize_human_settings(self.raw["human"])
        self.assertLess(normal["base_delay_ms"], slow["base_delay_ms"])


class ShippedImeJsonTests(unittest.TestCase):
    def setUp(self):
        self.raw = load_json(IME_PATH)

    def test_required_fields(self):
        for key in ("name", "version", "guid", "locale", "icon", "moduleName", "serviceName"):
            with self.subTest(key=key):
                self.assertIsInstance(self.raw[key], str)
                self.assertTrue(self.raw[key])

    def test_name_matches_the_documented_product_name(self):
        self.assertEqual(self.raw["name"], "cips-input")

    def test_guid_is_a_braced_uppercase_guid(self):
        guid = self.raw["guid"]
        self.assertTrue(guid.startswith("{") and guid.endswith("}"))
        self.assertEqual(guid, guid.upper())
        self.assertEqual(len(guid), 38)

    def test_icon_file_exists(self):
        self.assertTrue((CIPS_DIR / self.raw["icon"]).is_file())

    def test_service_class_is_defined_in_the_module(self):
        source = (CIPS_DIR / (self.raw["moduleName"] + ".py")).read_text(encoding="utf-8")
        tree = ast.parse(source)
        classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
        self.assertIn(self.raw["serviceName"], classes)

    def test_version_is_semver_and_documented_in_the_changelog(self):
        version = self.raw["version"]
        parts = version.split(".")
        self.assertEqual(len(parts), 3)
        for part in parts:
            self.assertTrue(part.isdigit(), part)
        self.assertIn(f"## {version}", CHANGELOG_PATH.read_text(encoding="utf-8"))

    def test_readme_does_not_claim_the_removed_bulk_channel(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("bulk", readme.lower())


if __name__ == "__main__":
    unittest.main()
