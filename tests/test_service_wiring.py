"""Wiring tests for the real text service, imported the way PIME imports it.

Everything else in this directory tests ``core`` in isolation. This file covers
the layer above: it stages a throwaway ``<python>/input_methods/cips`` tree from
the repo, adds the upstream ``textService.py`` (kept in ``_pime/``, not in the
versioned tree) to ``sys.path`` and drives the callbacks that do not touch the
clipboard, the RPC channel or the injection queue.

The test is skipped off Windows -- the backend imports ``ctypes.WinDLL`` at
import time -- and when the read-only upstream copy is absent.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CIPS_DIR = REPO_ROOT / "src" / "input_methods" / "cips"
UPSTREAM_TEXT_SERVICE = REPO_ROOT / "_pime" / "python" / "textService.py"


class KeyEvent:
    """Minimal stand-in for ``textService.KeyEvent``."""

    def __init__(self, key_code, ctrl=True, alt=True, repeat=1):
        self.keyCode = key_code
        self.charCode = 0
        self.repeatCount = repeat
        self.keyStates = [0] * 256
        if ctrl:
            self.keyStates[0x11] = 0x80
        if alt:
            self.keyStates[0x12] = 0x80

    def isKeyDown(self, code):
        return bool(self.keyStates[code] & 0x80)


@unittest.skipUnless(os.name == "nt", "the backend imports ctypes.WinDLL at import time")
class ServiceWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not UPSTREAM_TEXT_SERVICE.is_file():
            raise unittest.SkipTest("_pime/ upstream reference copy is not present")
        cls.tmp = tempfile.mkdtemp(prefix="cips-service-test-")
        shutil.copy(UPSTREAM_TEXT_SERVICE, cls.tmp)
        methods = Path(cls.tmp) / "input_methods"
        methods.mkdir()
        (methods / "__init__.py").write_text("", encoding="utf-8")
        shutil.copytree(CIPS_DIR, methods / "cips")
        sys.path.insert(0, cls.tmp)
        cls.cips_ime = importlib.import_module("input_methods.cips.cips_ime")
        # Never append to the developer's real cips.log.
        cls.cips_ime._LOG_PATH = os.path.join(cls.tmp, "test-cips.log")

    @classmethod
    def tearDownClass(cls):
        try:
            sys.path.remove(cls.tmp)
        except ValueError:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)
        for name in ["textService"] + [
            name for name in sys.modules if name == "input_methods" or name.startswith("input_methods.")
        ]:
            sys.modules.pop(name, None)

    def make_service(self):
        cips_ime = self.cips_ime

        class Client:
            isWindows8Above = True

        class Service(cips_ime.CipsTextService):
            def __init__(self, client):
                super().__init__(client)
                self.preserved = []
                self.toasts = []

            def addPreservedKey(self, key_code, modifiers, guid):
                self.preserved.append((key_code, guid))
                super().addPreservedKey(key_code, modifiers, guid)

            def showMessage(self, message, duration=3):
                self.toasts.append(message)
                super().showMessage(message, duration=duration)

        return Service(Client())

    def test_module_exposes_the_configured_class(self):
        self.assertTrue(hasattr(self.cips_ime, "CipsTextService"))
        self.assertEqual(self.cips_ime._WIN32_ERROR, None)

    def test_activate_loads_profiles_and_requests_five_preserved_keys(self):
        service = self.make_service()
        service.onActivate()
        self.assertIsNotNone(service._profiles)
        self.assertGreaterEqual(len(service._profiles), 1)
        self.assertEqual(len(service.preserved), 5)
        self.assertEqual({action for _, action in self.cips_ime._PRESERVED_KEYS.values()},
                         {"auto", "human", "fast", "otp", "replay"})

    def test_reactivation_does_not_duplicate_preserved_keys(self):
        service = self.make_service()
        service.onActivate()
        service.onActivate()
        self.assertEqual(len(service.preserved), 5)
        self.assertTrue(service._preserved_requested)

    def test_filter_claims_only_the_chords(self):
        service = self.make_service()
        service.onActivate()
        for key_code in (0x56, 0x5A, 0x31, 0x32, 0x33):  # V Z 1 2 3
            with self.subTest(key_code=hex(key_code)):
                self.assertTrue(service.filterKeyDown(KeyEvent(key_code)))
        for key_code in (0x41, 0x1B):  # A, Escape while not typing
            with self.subTest(key_code=hex(key_code)):
                self.assertFalse(service.filterKeyDown(KeyEvent(key_code)))
        # The chord also needs both modifiers.
        self.assertFalse(service.filterKeyDown(KeyEvent(0x56, ctrl=True, alt=False)))
        self.assertFalse(service.filterKeyDown(KeyEvent(0x56, ctrl=False, alt=True)))

    def test_preserved_key_guid_maps_to_its_action(self):
        service = self.make_service()
        service.onActivate()
        for key_code, (guid, action) in self.cips_ime._PRESERVED_KEYS.items():
            with self.subTest(action=action):
                self.assertEqual(self.cips_ime._PRESERVED_ACTIONS[guid], action)
                self.assertIs(self.cips_ime._HOTKEYS[key_code], action)

    def test_replay_without_a_previous_payload_is_log_only(self):
        service = self.make_service()
        service.onActivate()
        replay_guid = self.cips_ime._PRESERVED_KEY_GUIDS["replay"]
        self.assertTrue(service.onPreservedKey(replay_guid))
        # No edit session on this path, so no toast may be queued.
        self.assertEqual(service.toasts, [])

    def test_unknown_preserved_key_is_not_claimed(self):
        service = self.make_service()
        service.onActivate()
        self.assertFalse(service.onPreservedKey("{00000000-0000-0000-0000-000000000000}"))

    def test_keyboard_status_is_tracked(self):
        service = self.make_service()
        service.onActivate()
        service.onKeyboardStatusChanged(False)
        self.assertFalse(service.keyboardOpen)
        service.onKeyboardStatusChanged(True)
        self.assertTrue(service.keyboardOpen)

    def test_log_file_was_written(self):
        service = self.make_service()
        service.onActivate()
        log = Path(self.cips_ime._LOG_PATH)
        self.assertTrue(log.is_file())
        content = log.read_text(encoding="utf-8")
        self.assertIn("service created", content)
        self.assertIn("preserved keys requested: 5", content)


if __name__ == "__main__":
    unittest.main()
