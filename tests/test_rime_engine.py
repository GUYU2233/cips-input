"""Tests for the built-in Rime Frost engine, UI settings, and skinned candidate UI."""

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
STAGED_RIME_DIR = REPO_ROOT / "build" / "bundle" / "rime_stage"
CACHED_GRAM = REPO_ROOT / "build" / "cache" / "wanxiang-lts-zh-hans.gram"

if str(CIPS_DIR) not in sys.path:
    sys.path.insert(0, str(CIPS_DIR))

from rime_engine import (  # noqa: E402
    GRAM_FILENAME,
    RimeEngine,
    RimeState,
    detect_runtime_paths,
    find_gram_file,
    translate_key_code,
    translate_modifiers,
)
from rime_engine.engine import MANAGED_GRAM_HEADER  # noqa: E402
from rime_engine.key_map import (  # noqa: E402
    CONTROL_MASK,
    LOCK_MASK,
    RELEASE_MASK,
    SHIFT_MASK,
    XK_BACKSPACE,
    XK_RETURN,
)
from rime_engine.style import (  # noqa: E402
    ID_SKIN_GEMINI_NEKO,
    ID_SKIN_NATIVE,
    ID_TOGGLE_GRAM,
    ID_TOGGLE_LAYOUT,
)
from ui_settings import UiSettings, UiSettingsStore  # noqa: E402


class _FakeKeyEvent:
    def __init__(self, key_code, char_code=0, shift=False, ctrl=False, alt=False, caps=False):
        self.keyCode = key_code
        self.charCode = char_code
        self.repeatCount = 1
        self.keyStates = [0] * 256
        if shift:
            self.keyStates[0x10] = 0x80
        if ctrl:
            self.keyStates[0x11] = 0x80
        if alt:
            self.keyStates[0x12] = 0x80
        if caps:
            self.keyStates[0x14] = 0x01

    def isKeyDown(self, code):
        return bool(self.keyStates[code] & 0x80)

    def isKeyToggled(self, code):
        return bool(self.keyStates[code] & 0x01)

    def isPrintableChar(self):
        return self.charCode > 0x1F and self.charCode != 0x7F


class KeyMapTests(unittest.TestCase):
    def test_printable_ascii_translates_to_char_code(self):
        ev = _FakeKeyEvent(0x41, char_code=ord("a"))
        self.assertEqual(translate_key_code(ev), ord("a"))
        self.assertEqual(translate_modifiers(ev), 0)

    def test_special_keys_and_modifiers(self):
        self.assertEqual(translate_key_code(_FakeKeyEvent(0x08)), XK_BACKSPACE)
        self.assertEqual(translate_key_code(_FakeKeyEvent(0x0D)), XK_RETURN)
        ev = _FakeKeyEvent(0x41, char_code=ord("A"), shift=True, ctrl=True)
        mask = translate_modifiers(ev, is_up=True)
        self.assertTrue(mask & SHIFT_MASK)
        self.assertTrue(mask & CONTROL_MASK)
        self.assertTrue(mask & RELEASE_MASK)

    def test_caps_lock_keydown_inverts_toggled_bit(self):
        ev = _FakeKeyEvent(0x14, caps=True)
        self.assertEqual(translate_modifiers(ev, is_up=False) & LOCK_MASK, 0)
        self.assertTrue(translate_modifiers(ev, is_up=True) & LOCK_MASK)


class UiSettingsTests(unittest.TestCase):
    def test_defaults_use_horizontal_layout_and_frost_round_skin(self):
        cfg = UiSettings()
        self.assertEqual(cfg.skin, "frost_round")
        self.assertEqual(cfg.candidate_layout, "horizontal")
        self.assertEqual(cfg.cand_per_row, 8)
        self.assertTrue(cfg.use_wanxiang_gram)

    def test_store_roundtrips_and_recovers_from_bad_json(self):
        with tempfile.TemporaryDirectory(prefix="cips-ui-settings-") as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{not-json", encoding="utf-8")
            store = UiSettingsStore(str(path))
            self.assertEqual(store.current.skin, "frost_round")

            updated = store.update(skin="gemini_neko", candidate_layout="vertical", use_wanxiang_gram=False)
            self.assertEqual(updated.skin, "gemini_neko")
            self.assertEqual(updated.cand_per_row, 1)
            self.assertFalse(updated.use_wanxiang_gram)

            reloaded = UiSettingsStore(str(path))
            self.assertEqual(reloaded.current, updated)


class RuntimeDiscoveryTests(unittest.TestCase):
    def test_missing_runtime_reports_unavailable_without_blocking(self):
        with tempfile.TemporaryDirectory(prefix="cips-rime-unavail-") as tmp:
            engine = RimeEngine(cips_dir=tmp, user_data_dir=os.path.join(tmp, "user"))
            self.assertFalse(engine.is_available)
            state = engine.ensure_started()
            self.assertEqual(state, RimeState.UNAVAILABLE)
            self.assertTrue(engine.wait_ready(timeout=0.5))

    def test_find_gram_file_prefers_user_dir_then_shared_dir(self):
        with tempfile.TemporaryDirectory(prefix="cips-gram-find-") as tmp:
            shared = os.path.join(tmp, "shared")
            user = os.path.join(tmp, "user")
            os.makedirs(shared)
            os.makedirs(user)
            self.assertIsNone(find_gram_file(shared, user))

            shared_gram = os.path.join(shared, GRAM_FILENAME)
            Path(shared_gram).write_bytes(b"shared")
            self.assertEqual(find_gram_file(shared, user), shared_gram)

            user_gram = os.path.join(user, GRAM_FILENAME)
            Path(user_gram).write_bytes(b"user")
            self.assertEqual(find_gram_file(shared, user), user_gram)

    def test_detect_runtime_paths(self):
        dll, shared, user = detect_runtime_paths(r"C:\cips", user_data_dir=r"D:\user")
        self.assertTrue(dll.endswith(os.path.join("rime_runtime", "rime.dll")))
        self.assertTrue(shared.endswith("rime_data"))
        self.assertEqual(user, r"D:\user")

    def test_sync_gram_patches_toggles_managed_files_without_clobbering_user_custom(self):
        with tempfile.TemporaryDirectory(prefix="cips-gram-toggle-") as tmp:
            cips_dir = Path(tmp) / "cips"
            shared = cips_dir / "rime_data"
            user = Path(tmp) / "user"
            (cips_dir / "rime_config").mkdir(parents=True)
            shared.mkdir(parents=True)
            user.mkdir(parents=True)
            (cips_dir / "rime_config" / "gram_patch.yaml").write_text("patch:\n  grammar/language: wanxiang-lts-zh-hans\n", encoding="utf-8")
            (shared / GRAM_FILENAME).write_bytes(b"stub")
            (shared / "rime_frost.custom.yaml").write_text("patch:\n  grammar:\n    language: wanxiang-lts-zh-hans\n", encoding="utf-8")

            # Hand-edited user custom file on flypy must not be overwritten.
            hand_edited = user / "rime_frost_double_pinyin_flypy.custom.yaml"
            hand_edited.write_text("patch:\n  menu/page_size: 5\n", encoding="utf-8")

            engine = RimeEngine(cips_dir=str(cips_dir), user_data_dir=str(user), shared_data_dir=str(shared))
            self.assertTrue(engine.set_gram_enabled(False, redeploy=False))
            self.assertFalse(engine.gram_enabled)
            frost_custom = user / "rime_frost.custom.yaml"
            self.assertTrue(frost_custom.is_file())
            self.assertIn(MANAGED_GRAM_HEADER, frost_custom.read_text(encoding="utf-8"))
            self.assertEqual(hand_edited.read_text(encoding="utf-8"), "patch:\n  menu/page_size: 5\n")

            self.assertTrue(engine.set_gram_enabled(True, redeploy=False))
            self.assertTrue(engine.gram_enabled)
            self.assertFalse(frost_custom.exists())


@unittest.skipUnless(os.name == "nt", "GDI+ skin renderer runs on Windows")
class SkinnedCandidateRendererTests(unittest.TestCase):
    def test_renders_frost_round_and_gemini_neko_skins_to_png(self):
        sys.path.insert(0, str(CIPS_DIR))
        from win32.candidate_ui import CandidateRenderState, GdiplusCandidateRenderer

        mascot = CIPS_DIR / "skins" / "gemini_neko_mascot.png"
        renderer = GdiplusCandidateRenderer(mascot_path=str(mascot))
        try:
            with tempfile.TemporaryDirectory(prefix="cips-skin-png-") as tmp:
                for skin in ("frost_round", "gemini_neko"):
                    for layout in ("horizontal", "vertical"):
                        out = Path(tmp) / f"{skin}_{layout}.png"
                        state = CandidateRenderState(
                            preedit="ni hao",
                            candidates=["你好", "拟好", "妳好"],
                            cursor_idx=0,
                            select_keys="12345678",
                            skin=skin,
                            layout=layout,
                            visible=True,
                        )
                        renderer.render_to_png(state, str(out), dpi_scale=1.0)
                        self.assertTrue(out.is_file())
                        self.assertGreater(out.stat().st_size, 500)
        finally:
            renderer.close()


@unittest.skipUnless(os.name == "nt", "Service wiring uses ctypes.WinDLL on Windows")
class ServiceRimeWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not UPSTREAM_TEXT_SERVICE.is_file():
            raise unittest.SkipTest("_pime/ upstream reference copy is not present")
        cls.tmp = tempfile.mkdtemp(prefix="cips-rime-wiring-")
        shutil.copy(UPSTREAM_TEXT_SERVICE, cls.tmp)
        methods = Path(cls.tmp) / "input_methods"
        methods.mkdir()
        (methods / "__init__.py").write_text("", encoding="utf-8")
        shutil.copytree(CIPS_DIR, methods / "cips")
        sys.path.insert(0, cls.tmp)
        cls.cips_ime = importlib.import_module("input_methods.cips.cips_ime")
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

    def test_loading_state_shows_toast_on_printable_key_and_keeps_hotkeys_working(self):
        cips_ime = self.cips_ime

        class FakeLoadingEngine:
            is_available = True
            is_loading = True
            is_ready = False
            shared_data_dir = "shared"
            user_data_dir = "user"
            gram_path = None
            gram_enabled = False

            def ensure_started(self):
                return "loading"

        class Client:
            isWindows8Above = True

        settings_path = os.path.join(self.tmp, "settings_loading.json")
        store = UiSettingsStore(settings_path)

        class Service(cips_ime.CipsTextService):
            def __init__(self, client):
                super().__init__(client, rime_engine=FakeLoadingEngine(), ui_settings=store, candidate_ui=False)
                self.toasts = []

            def addPreservedKey(self, key_code, modifiers, guid):
                pass

            def showMessage(self, message, duration=3):
                self.toasts.append(message)

        svc = Service(Client())
        svc.onActivate()
        svc.keyboardOpen = True

        self.assertTrue(svc.filterKeyDown(_FakeKeyEvent(0x41, char_code=ord("a"))))
        self.assertTrue(svc.onKeyDown(_FakeKeyEvent(0x41, char_code=ord("a"))))
        self.assertEqual(len(svc.toasts), 1)
        self.assertIn("白霜拼音", svc.toasts[0])

        self.assertTrue(svc.filterKeyDown(_FakeKeyEvent(0x56, ctrl=True, alt=True)))
        self.assertTrue(svc._cips_claimed_down)

    def test_skinned_and_native_candidate_ui_and_menu_commands(self):
        cips_ime = self.cips_ime

        class FakeStyle:
            font_face = "Microsoft YaHei UI"
            font_point = 12
            candidate_per_row = 8
            candidate_use_cursor = True
            display_tray_icon = True
            menu = [{"text": "设置", "id": 10}]

        class FakeReadyEngine:
            is_available = True
            is_loading = False
            is_ready = True
            shared_data_dir = "shared"
            user_data_dir = "user"
            gram_path = "wanxiang-lts-zh-hans.gram"
            gram_enabled = True

            def __init__(self):
                self.processed = []
                self.gram_calls = []
                self.horizontal_opts = []

            def ensure_started(self):
                return "ready"

            def create_session(self, active_skin="frost_round", active_layout="horizontal"):
                return 42, FakeStyle()

            def find_session(self, sid):
                return sid == 42

            def destroy_session(self, sid):
                pass

            def clear_composition(self, sid):
                pass

            def get_option(self, sid, name):
                return False

            def set_option(self, sid, name, val):
                if name == b"_horizontal":
                    self.horizontal_opts.append(val)

            def set_gram_enabled(self, enabled, redeploy=True):
                self.gram_calls.append((enabled, redeploy))
                self.gram_enabled = enabled
                return True

            def process_key(self, sid, keysym, mask):
                self.processed.append((sid, keysym, mask))
                return True

            def read_output(self, sid, style):
                return {
                    "commit": "你好",
                    "composing": True,
                    "preedit": "ni hao",
                    "cursor": 6,
                    "select_keys": "12345678",
                    "candidates": ["你好", "拟好"],
                    "candidate_cursor": 0,
                    "show_candidates": True,
                }

        class FakePopup:
            def __init__(self):
                self.shown = []
                self.hidden = 0

            def show(self, state):
                self.shown.append(state)

            def hide(self):
                self.hidden += 1

        class Client:
            isWindows8Above = True

        settings_path = os.path.join(self.tmp, "settings_ready.json")
        store = UiSettingsStore(settings_path)
        popup = FakePopup()
        eng = FakeReadyEngine()

        class Service(cips_ime.CipsTextService):
            def __init__(self, client):
                super().__init__(client, rime_engine=eng, ui_settings=store, candidate_ui=popup)
                self.commits = []
                self.candidates = []
                self.preedits = []

            def addPreservedKey(self, key_code, modifiers, guid):
                pass

            def setCommitString(self, s):
                self.commits.append(s)
                super().setCommitString(s)

            def setCandidateList(self, cand):
                self.candidates.append(list(cand))
                super().setCandidateList(cand)

            def setCompositionString(self, s):
                self.preedits.append(s)
                super().setCompositionString(s)

        svc = Service(Client())
        svc.onActivate()
        svc.keyboardOpen = True

        # Default frost_round skin uses FakePopup (hides native TSF candidate list).
        ev = _FakeKeyEvent(0x4E, char_code=ord("n"))
        self.assertTrue(svc.filterKeyDown(ev))
        self.assertTrue(svc.onKeyDown(ev))
        self.assertEqual(svc.commits, ["你好"])
        self.assertEqual(svc.preedits[-1], "ni hao")
        self.assertEqual(len(popup.shown), 1)
        self.assertEqual(popup.shown[-1].skin, "frost_round")
        self.assertEqual(popup.shown[-1].layout, "horizontal")
        self.assertEqual(svc.candidates, [])

        # Switch to Gemini Neko skin & vertical layout via commands.
        svc.onCommand(ID_SKIN_GEMINI_NEKO, 0)
        svc.onCommand(ID_TOGGLE_LAYOUT, 0)
        self.assertTrue(svc.onKeyDown(ev))
        self.assertEqual(popup.shown[-1].skin, "gemini_neko")
        self.assertEqual(popup.shown[-1].layout, "vertical")

        # Switch to native GDI mode: falls back to PIME setCandidateList.
        svc.onCommand(ID_SKIN_NATIVE, 0)
        self.assertTrue(svc.onKeyDown(ev))
        self.assertEqual(svc.candidates[-1], ["你好", "拟好"])

        # Toggle Wanxiang gram off.
        svc.onCommand(ID_TOGGLE_GRAM, 0)
        self.assertEqual(eng.gram_calls, [(False, True)])
        self.assertFalse(store.current.use_wanxiang_gram)


@unittest.skipUnless(
    os.name == "nt"
    and (STAGED_RIME_DIR / "rime_runtime" / "rime.dll").is_file()
    and (STAGED_RIME_DIR / "rime_data" / "build" / "rime_frost.table.bin").is_file(),
    "Staged librime + precompiled rime-frost not present (run deploy/prepare_rime.ps1 first)",
)
class RealLibrimeFrostIntegrationTests(unittest.TestCase):
    def _run_typing_session(self, with_gram: bool):
        with tempfile.TemporaryDirectory(prefix="cips-real-rime-") as tmp:
            shared_dir = Path(tmp) / "rime_data"
            user_dir = Path(tmp) / "user"
            shutil.copytree(STAGED_RIME_DIR / "rime_data", shared_dir)
            user_dir.mkdir()

            if with_gram:
                gram_patch = CIPS_DIR / "rime_config" / "gram_patch.yaml"
                shutil.copy2(gram_patch, shared_dir / "rime_frost.custom.yaml")
                dest_gram = shared_dir / GRAM_FILENAME
                try:
                    os.link(CACHED_GRAM, dest_gram)
                except OSError:
                    shutil.copy2(CACHED_GRAM, dest_gram)

            dll_path = STAGED_RIME_DIR / "rime_runtime" / "rime.dll"
            engine = RimeEngine(
                cips_dir=str(Path(tmp)),
                user_data_dir=str(user_dir),
                dll_path=str(dll_path),
                shared_data_dir=str(shared_dir),
            )
            try:
                self.assertEqual(engine.ensure_started(), RimeState.LOADING)
                self.assertTrue(engine.wait_ready(timeout=30.0))
                self.assertTrue(engine.is_ready, msg=engine.last_error)
                self.assertEqual(engine.gram_enabled, with_gram)

                sid, style = engine.create_session()
                self.assertTrue(sid)
                self.assertIsNotNone(style)
                self.assertEqual(style.gram_enabled, with_gram)

                for ch in b"nihaoshijie":
                    self.assertTrue(engine.process_key(sid, ch, 0))
                out = engine.read_output(sid, style)
                self.assertTrue(out["composing"])
                self.assertIn("你好世界", out["candidates"])

                self.assertTrue(engine.process_key(sid, ord(" "), 0))
                committed = engine.read_output(sid, style)
                self.assertEqual(committed["commit"], "你好世界")
                self.assertFalse(committed["composing"])
                engine.destroy_session(sid)
            finally:
                engine.finalize()

    def test_base_rime_frost_composes_and_commits(self):
        self._run_typing_session(with_gram=False)

    @unittest.skipUnless(CACHED_GRAM.is_file(), "wanxiang-lts-zh-hans.gram not cached in build/cache")
    def test_rime_frost_with_wanxiang_gram_composes_and_commits(self):
        self._run_typing_session(with_gram=True)


if __name__ == "__main__":
    unittest.main()
