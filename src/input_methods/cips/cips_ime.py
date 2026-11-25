"""cips-input text service: built-in Rime Frost IME + clipboard keystroke injector.

Two capabilities live together in one TSF text service:

1. **Daily Chinese input (Rime Frost + optional Wanxiang N-gram)**
   Uses an embedded 32-bit ``librime`` 1.17.x engine (``rime_runtime/rime.dll``
   + ``rime_data/``) to compose and commit Chinese text through standard TSF
   edit sessions (``setCompositionString`` / ``setCandidateList`` /
   ``setCommitString``).  Engine startup and deployment run asynchronously on a
   background thread so PIME's 2-second RPC watchdog is never tripped.

2. **Clipboard keystroke injection (``Ctrl+Alt+V/1/2/3/Z``)**
   Synthesises real ``SendInput`` (``KEYEVENTF_UNICODE``) keystrokes for
   paste-hostile inputs.  Hotkeys are claimed via TSF preserved keys and
   ``filterKeyDown`` ahead of Rime so clipboard injection works at any time.
   Because preserved-key callbacks own no TSF edit session, injection runs on a
   background worker thread and diagnostics go to ``cips.log``.
"""

import atexit
import ctypes
import json
import os
import random
import threading
import time

# PIME puts its own ``python`` directory on sys.path, so the framework module
# is imported as a top-level module, not as part of this package.
from textService import TF_MOD_ALT, TF_MOD_CONTROL, TextService

try:
    from textService import COMMAND_LEFT_CLICK
except ImportError:  # pragma: no cover - older test stubs
    COMMAND_LEFT_CLICK = 0

from .core import (
    MAX_TEXT_LENGTH,
    ProfileBook,
    normalize_text,
    plan_chunks,
    plan_human_chunks,
    plan_otp_chunks,
    resolve_dispatch,
    should_refuse,
    strip_edges,
)
from .rime_engine import (
    RimeEngine,
    translate_key_code,
    translate_modifiers,
)
from .rime_engine.key_map import RELEASE_MASK
from .rime_engine.style import (
    ID_ASCII_MODE,
    ID_DEPLOY,
    ID_FULL_SHAPE,
    ID_GRAM_STATUS,
    ID_LOG_DIR,
    ID_MODE_ICON,
    ID_OPTION,
    ID_PROFILES_FILE,
    ID_SCHEMA,
    ID_SHARED_DIR,
    ID_SKIN_FROST_ROUND,
    ID_SKIN_GEMINI_NEKO,
    ID_SKIN_NATIVE,
    ID_SYNC,
    ID_SYNC_DIR,
    ID_TOGGLE_GRAM,
    ID_TOGGLE_LAYOUT,
    ID_URI,
    ID_USER_DIR,
)
from .ui_settings import UiSettingsStore

try:
    from .win32.candidate_ui import CandidateRenderState, CandidateWindowManager
    from .win32.clipboard import read_clipboard_text, sniff_clipboard_has_text
    from .win32.sendinput import InjectError, UipiBlocked, type_unicode
    from .win32.target import get_foreground_hwnd, get_foreground_target

    _WIN32_ERROR = None
except Exception as exc:  # pragma: no cover - only on a broken deployment
    CandidateRenderState = None
    CandidateWindowManager = None
    _WIN32_ERROR = str(exc)


# Virtual key codes (winuser.h). keyStates in a PIME KeyEvent is the 256 entry
# keyboard state array, indexed by virtual key code, bit 7 meaning "down".
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_RETURN = 0x0D
VK_V = 0x56
VK_Z = 0x5A
VK_1 = 0x31
VK_2 = 0x32
VK_3 = 0x33

# Ctrl+Alt+<vk>. "auto" defers to the profile matched for the foreground
# application, the numbered chords force one strategy, and "replay" reuses the
# plan of the previous dispatch.
_HOTKEYS = {
    VK_V: "auto",
    VK_1: "human",
    VK_2: "fast",
    VK_3: "otp",
    VK_Z: "replay",
}

# One preserved-key GUID per action. These are stable and service-specific:
# TSF remembers the GUID<->chord association for the language profile, so they
# must never be reused by another text service.
_PRESERVED_KEY_GUIDS = {
    "auto": "{58f67686-1ab0-4c69-a436-5d4130609f84}",
    "human": "{58f67686-1ab0-4c69-a436-5d4130609f85}",
    "fast": "{58f67686-1ab0-4c69-a436-5d4130609f86}",
    "otp": "{58f67686-1ab0-4c69-a436-5d4130609f87}",
    "replay": "{58f67686-1ab0-4c69-a436-5d4130609f88}",
}

_PRESERVED_KEYS = {
    key_code: (_PRESERVED_KEY_GUIDS[action], action)
    for key_code, action in _HOTKEYS.items()
}
_PRESERVED_ACTIONS = {
    guid: action for guid, action in _PRESERVED_KEYS.values()
}

_HOTKEY_MODIFIERS = TF_MOD_CONTROL | TF_MOD_ALT

# A chord that reaches us through both filterKeyDown and onPreservedKey must
# only fire once; anything faster than this is the same physical press.
_HOTKEY_DEBOUNCE = 0.5

_CONFIG_CHECK_INTERVAL = 2.0
_LOADING_TOAST_INTERVAL = 2.0

#: Payloads that will take longer than this get an extra diagnostic line; the
#: injection is still interruptible with Esc or by switching windows.
_LONG_PAYLOAD_WARNING_MS = 120_000

_LOG_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "PIME", "Logs", "cips.log"
)
_LOG_LOCK = threading.Lock()

_ENGINE_LOCK = threading.RLock()
_SHARED_RIME_ENGINE = None
_SHARED_UI_SETTINGS = None
_SHARED_CANDIDATE_UI = None


def _log(message):
    """Append one diagnostic line to cips.log.

    PIME only reads protocol replies from the backend's stdout and drops
    everything else, and only *uncaught* exceptions reach python_backend.log.
    A deliberate diagnostic line therefore needs a file of its own.

    Called on activation, Rime lifecycle transitions, and hotkey dispatch only
    -- never per keystroke -- and every failure is swallowed.
    """
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with _LOG_LOCK:
            log_dir = os.path.dirname(_LOG_PATH)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            with open(_LOG_PATH, "a", encoding="utf-8") as handle:
                handle.write("%s %s\n" % (stamp, message))
    except Exception:
        pass


def _get_ui_settings():
    """Return the process-wide UiSettingsStore singleton."""
    global _SHARED_UI_SETTINGS
    with _ENGINE_LOCK:
        if _SHARED_UI_SETTINGS is None:
            _SHARED_UI_SETTINGS = UiSettingsStore()
        return _SHARED_UI_SETTINGS


def _get_rime_engine():
    """Return the process-wide RimeEngine singleton."""
    global _SHARED_RIME_ENGINE
    with _ENGINE_LOCK:
        if _SHARED_RIME_ENGINE is None:
            cips_dir = os.path.dirname(os.path.abspath(__file__))
            settings = _get_ui_settings().current
            _SHARED_RIME_ENGINE = RimeEngine(
                cips_dir=cips_dir,
                log_fn=_log,
                use_wanxiang_gram=settings.use_wanxiang_gram,
            )
            atexit.register(_SHARED_RIME_ENGINE.finalize)
        return _SHARED_RIME_ENGINE


def _get_candidate_ui():
    """Return the process-wide skinned candidate window manager."""
    global _SHARED_CANDIDATE_UI
    if _WIN32_ERROR is not None or CandidateWindowManager is None:
        return None
    with _ENGINE_LOCK:
        if _SHARED_CANDIDATE_UI is None:
            cips_dir = os.path.dirname(os.path.abspath(__file__))
            mascot = os.path.join(cips_dir, "skins", "gemini_neko_mascot.png")
            _SHARED_CANDIDATE_UI = CandidateWindowManager(
                mascot_path=mascot if os.path.isfile(mascot) else None,
                log_fn=_log,
            )
            atexit.register(_SHARED_CANDIDATE_UI.close)
        return _SHARED_CANDIDATE_UI


class CipsTextService(TextService):
    """Rime Frost Chinese IME combined with CIPS clipboard keystroke injection."""

    def __init__(self, client, rime_engine=None, ui_settings=None, candidate_ui=None):
        super().__init__(client)

        self._base_dir = os.path.dirname(os.path.abspath(__file__))
        self._icon_dir = os.path.join(self._base_dir, "icons")
        self._fallback_icon = os.path.join(self._base_dir, "icon.ico")
        self._config_path = os.path.join(self._base_dir, "profiles.json")
        self._profiles = None
        self._config_mtime = None
        self._last_config_check = 0.0

        # Clipboard injection state.
        self._last_payload = None
        self._typing_thread = None
        self._stop_event = None
        self._in_key_down = False
        self._preserved_requested = False
        self._target_hwnd = 0
        self._human_settings = {}
        self._human_normal_settings = {}
        self._rng = random.Random()

        # Rime session, UI settings, skinned popup & key-dedup state.
        self._ui_settings = ui_settings if ui_settings is not None else _get_ui_settings()
        self._rime_engine = rime_engine if rime_engine is not None else _get_rime_engine()
        self._candidate_ui = _get_candidate_ui() if candidate_ui is None else candidate_ui
        self._rime_session_id = 0
        self._rime_style = None
        self._rime_select_keys = ""
        self._buttons_added = False
        self._cips_claimed_down = False
        self._rime_loading_claimed = False
        self._last_loading_toast = 0.0
        self._last_key_down_code = None
        self._last_key_skip = 0
        self._last_key_down_ret = False
        self._last_key_up_code = None
        self._last_key_up_ret = False
        self._key_composing = False

        # Bounded, per-session diagnostics.
        self._seen_methods = set()
        self._seen_keycodes = set()
        self._modifier_events = 0
        self._last_hotkey = (None, 0.0)

        _log("service created")

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def onActivate(self):
        target = None
        if _WIN32_ERROR is None:
            try:
                target = get_foreground_target()
            except Exception:
                target = None
        foreground = target.exe_name if target else "?"
        _log("activate keyboardOpen=%s app=%s" % (self.keyboardOpen, foreground))
        self._reload_profiles(force=True)
        if target is not None:
            _log("active profile: %s" % self._profile_for(target).name)

        if getattr(self.client, "isWindows8Above", False):
            self.setKeyboardOpen(True)

        if not self._preserved_requested:
            for key_code, (guid, _action) in _PRESERVED_KEYS.items():
                self.addPreservedKey(key_code, _HOTKEY_MODIFIERS, guid)
            self._preserved_requested = True
            _log(
                "preserved keys requested: %d (once per host process)"
                % len(_PRESERVED_KEYS)
            )

        # Kick off librime startup in the background without blocking onActivate.
        state = self._rime_engine.ensure_started()
        if state == "ready":
            self._ensure_rime_session()
            self._ensure_lang_buttons()

    def onDeactivate(self):
        _log("deactivate")
        self._cancel_typing()
        self._destroy_rime_session()
        self._remove_lang_buttons()

    def onKeyboardStatusChanged(self, opened):
        _log("keyboard status: opened=%s" % opened)
        self.keyboardOpen = bool(opened)
        if self.keyboardOpen:
            if self._rime_engine.is_ready:
                self._ensure_rime_session()
                self._ensure_lang_buttons()
        else:
            self._destroy_rime_session()
        if self._buttons_added and getattr(self.client, "isWindows8Above", False):
            self.changeButton("windows-mode-icon", enable=self.keyboardOpen)

    def onCompartmentChanged(self, guid):
        _log("compartment changed: %s" % guid)

    def onCompositionTerminated(self, forced):
        super().onCompositionTerminated(forced)
        if forced:
            self._destroy_rime_session()

    def checkConfigChange(self):
        """Poll profiles.json, throttled."""
        now = time.monotonic()
        if now - self._last_config_check < _CONFIG_CHECK_INTERVAL:
            return
        self._last_config_check = now
        self._reload_profiles()

    # ------------------------------------------------------------------
    # key handling
    # ------------------------------------------------------------------

    def handleRequest(self, msg):
        method = msg.get("method") if isinstance(msg, dict) else None
        if isinstance(method, str) and method not in self._seen_methods:
            self._seen_methods.add(method)
            _log("request method: %s" % method)
        return super().handleRequest(msg)

    def filterKeyDown(self, keyEvent):
        ctrl = keyEvent.isKeyDown(VK_CONTROL)
        alt = keyEvent.isKeyDown(VK_MENU)
        if ctrl or alt:
            if self._modifier_events < 40:
                self._modifier_events += 1
                _log(
                    "mod key: 0x%02X char=%d ctrl=%s alt=%s"
                    % (keyEvent.keyCode, keyEvent.charCode, ctrl, alt)
                )
        elif len(self._seen_keycodes) < 8 and keyEvent.keyCode not in self._seen_keycodes:
            self._seen_keycodes.add(keyEvent.keyCode)
            _log(
                "key seen: 0x%02X char=%d ctrl=%s alt=%s"
                % (keyEvent.keyCode, keyEvent.charCode, ctrl, alt)
            )

        # 1. CIPS hotkeys and Escape-to-abort always take priority over Rime.
        if self._hotkey_action(keyEvent) is not None or (
            keyEvent.keyCode == VK_ESCAPE and self._is_typing()
        ):
            self._cips_claimed_down = True
            self._rime_loading_claimed = False
            return True

        self._cips_claimed_down = False
        self._rime_loading_claimed = False

        # 2. If keyboard compartment is closed or Rime is unavailable, stay transparent.
        if not self.keyboardOpen or not self._rime_engine.is_available:
            return False

        # 3. While librime is warming up in the background, only intercept
        #    unmodified printable keys so we can notify the user onKeyDown.
        if self._rime_engine.is_loading:
            if not ctrl and not alt and self._is_printable(keyEvent):
                self._rime_loading_claimed = True
                return True
            return False

        if not self._rime_engine.is_ready:
            return False

        # 4. Route to Rime with PIME's duplicate filterKeyDown deduplication.
        if self._last_key_down_code == keyEvent.keyCode:
            self._last_key_skip += 1
            if self._last_key_skip >= 2:
                self._last_key_down_code = None
                self._last_key_skip = 0
        else:
            self._last_key_down_code = keyEvent.keyCode
            self._last_key_skip = 0
            self._last_key_down_ret = self._rime_process_key(keyEvent, is_up=False)
        self._last_key_up_code = None
        return self._last_key_down_ret

    def onKeyDown(self, keyEvent):
        # This is the only callback PIME hands an edit session to, so it is the
        # only place where a toast or Rime UI update is safe.
        self._in_key_down = True
        try:
            if self._cips_claimed_down:
                self._cips_claimed_down = False
                return self._handle_key_down(keyEvent)
            if self._rime_loading_claimed:
                self._rime_loading_claimed = False
                now = time.monotonic()
                if now - self._last_loading_toast >= _LOADING_TOAST_INTERVAL:
                    self._last_loading_toast = now
                    self._notify("白霜拼音正在加载词库，请稍候…", duration=2)
                return True
            if self._rime_engine.is_ready:
                return self._rime_sync_ui()
            return self._handle_key_down(keyEvent)
        finally:
            self._in_key_down = False

    def filterKeyUp(self, keyEvent):
        if not self.keyboardOpen or not self._rime_engine.is_ready:
            return False
        if self._hotkey_action(keyEvent) is not None:
            return False
        if self._last_key_up_code == keyEvent.keyCode:
            self._last_key_up_code = None
        else:
            self._last_key_up_code = keyEvent.keyCode
            self._last_key_up_ret = self._rime_process_key(keyEvent, is_up=True)
        self._last_key_down_code = None
        self._last_key_skip = 0
        return self._last_key_up_ret

    def onKeyUp(self, keyEvent):
        if not self.keyboardOpen or not self._rime_engine.is_ready:
            return False
        return self._rime_sync_ui()

    def _handle_key_down(self, keyEvent):
        if keyEvent.keyCode == VK_ESCAPE and self._is_typing():
            self._cancel_typing("escape")
            return True
        action = self._hotkey_action(keyEvent)
        if action is not None:
            if keyEvent.repeatCount > 1:
                return True
            self._dispatch(action)
            return True
        return False

    def onPreservedKey(self, guid):
        """Framework-level hotkey notification (has no edit session)."""
        action = _PRESERVED_ACTIONS.get(guid.lower()) if isinstance(guid, str) else None
        _log("preserved key %s -> %s" % (guid, action))
        if action is None:
            return False
        self._dispatch(action)
        return True

    def _hotkey_action(self, keyEvent):
        if not keyEvent.isKeyDown(VK_CONTROL) or not keyEvent.isKeyDown(VK_MENU):
            return None
        return _HOTKEYS.get(keyEvent.keyCode)

    @staticmethod
    def _is_printable(keyEvent):
        if hasattr(keyEvent, "isPrintableChar"):
            return bool(keyEvent.isPrintableChar())
        ch = getattr(keyEvent, "charCode", 0)
        return ch > 0x1F and ch != 0x7F

    # ------------------------------------------------------------------
    # Rime integration & language bar
    # ------------------------------------------------------------------

    def _icon_path(self, filename):
        candidate = os.path.join(self._icon_dir, filename)
        if os.path.isfile(candidate):
            return candidate
        return self._fallback_icon

    def _ensure_rime_session(self):
        if not self._rime_engine.is_ready:
            return False
        if self._rime_session_id and self._rime_engine.find_session(self._rime_session_id):
            return True
        ui_cfg = self._ui_settings.reload()
        sid, style = self._rime_engine.create_session(
            active_skin=ui_cfg.skin,
            active_layout=ui_cfg.candidate_layout,
        )
        if not sid or style is None:
            return False
        self._rime_session_id = sid
        self._rime_style = style
        self.customizeUI(
            candFontSize=ui_cfg.font_point,
            candFontName=ui_cfg.font_name,
            candPerRow=ui_cfg.cand_per_row,
            candUseCursor=style.candidate_use_cursor,
        )
        return True

    def _hide_candidate_ui(self):
        if self._candidate_ui:
            self._candidate_ui.hide()

    def _destroy_rime_session(self):
        if self.isComposing():
            self._clear_rime_ui()
        else:
            self._hide_candidate_ui()
        if self._rime_session_id:
            self._rime_engine.destroy_session(self._rime_session_id)
        self._rime_session_id = 0
        self._rime_select_keys = ""

    def _clear_rime_ui(self):
        self.setShowCandidates(False)
        self._hide_candidate_ui()
        self.setCompositionString("")
        self.setCompositionCursor(0)
        if self._rime_session_id:
            self._rime_engine.clear_composition(self._rime_session_id)

    def _rime_process_key(self, keyEvent, is_up=False):
        if not self._ensure_rime_session():
            return False
        if not is_up:
            self._key_composing = self.isComposing()
        keysym = translate_key_code(keyEvent)
        mask = translate_modifiers(keyEvent, is_up=is_up)
        ret = self._rime_engine.process_key(self._rime_session_id, keysym, mask)
        if ret:
            return True
        if self._key_composing and keyEvent.keyCode == VK_RETURN:
            return True
        if keyEvent.keyCode in (VK_SHIFT, VK_CONTROL, VK_CAPITAL) and mask in (0, RELEASE_MASK):
            return True
        return False

    def _rime_sync_ui(self):
        self._ensure_lang_buttons()
        self._update_lang_status()
        if not self._rime_session_id or self._rime_style is None:
            return True

        out = self._rime_engine.read_output(self._rime_session_id, self._rime_style)
        if out["commit"]:
            self.setCommitString(out["commit"])

        if not out["composing"]:
            self._clear_rime_ui()
            return True

        sel_keys = out["select_keys"]
        if sel_keys and sel_keys != self._rime_select_keys:
            self.setSelKeys(sel_keys)
            self._rime_select_keys = sel_keys

        self.setCompositionString(out["preedit"])
        self.setCompositionCursor(out["cursor"])

        if out["show_candidates"] and out["candidates"]:
            ui_cfg = self._ui_settings.reload()
            shown_skinned = False
            if ui_cfg.skin != "native" and self._candidate_ui and CandidateRenderState is not None:
                res = self._candidate_ui.show(
                    CandidateRenderState(
                        preedit=out["preedit"],
                        candidates=out["candidates"],
                        cursor_idx=out["candidate_cursor"],
                        select_keys=sel_keys or self._rime_select_keys or "12345678",
                        skin=ui_cfg.skin,
                        layout=ui_cfg.candidate_layout,
                        font_name=ui_cfg.font_name,
                        font_point=ui_cfg.font_point,
                        gram_enabled=getattr(self._rime_engine, "gram_enabled", False),
                        visible=True,
                    )
                )
                shown_skinned = res is not False
            if shown_skinned:
                self.setShowCandidates(False)
            else:
                self._hide_candidate_ui()
                self.setCandidateList(out["candidates"])
                self.setCandidateCursor(out["candidate_cursor"])
                self.setShowCandidates(True)
        else:
            self.setShowCandidates(False)
            self._hide_candidate_ui()
        return True

    def _ensure_lang_buttons(self):
        if self._buttons_added:
            return
        if self._rime_style is None or not self._rime_style.display_tray_icon:
            return
        if getattr(self.client, "isWindows8Above", False):
            self.addButton(
                "windows-mode-icon",
                icon=self._icon_path("chi_half_capsoff.ico"),
                tooltip="中英文切换",
                commandId=ID_MODE_ICON,
            )
        self.addButton(
            "switch-lang",
            icon=self._icon_path("chi.ico"),
            text="中/英",
            tooltip="中英文切换",
            commandId=ID_ASCII_MODE,
        )
        self.addButton(
            "switch-shape",
            icon=self._icon_path("half.ico"),
            text="全/半角",
            tooltip="全角/半角切换",
            commandId=ID_FULL_SHAPE,
        )
        self.addButton(
            "settings",
            icon=self._icon_path("config.ico"),
            text="设置",
            tooltip="白霜拼音与 cips-input 设置",
            type="menu",
        )
        self._buttons_added = True
        self._update_lang_status()

    def _remove_lang_buttons(self):
        if not self._buttons_added:
            return
        self.removeButton("switch-lang")
        self.removeButton("switch-shape")
        self.removeButton("settings")
        if getattr(self.client, "isWindows8Above", False):
            self.removeButton("windows-mode-icon")
        self._buttons_added = False

    @staticmethod
    def _get_caps_state():
        if _WIN32_ERROR is not None:
            return False
        try:
            return bool(ctypes.WinDLL("User32.dll").GetKeyState(VK_CAPITAL) & 0x0001)
        except Exception:
            return False

    def _update_lang_status(self):
        if not self._buttons_added or not self._rime_session_id:
            return
        if self._rime_style is None or not self._rime_style.display_tray_icon:
            return
        is_ascii = self._rime_engine.get_option(self._rime_session_id, b"ascii_mode")
        is_full = self._rime_engine.get_option(self._rime_session_id, b"full_shape")
        is_caps = self._get_caps_state()

        if getattr(self.client, "isWindows8Above", False):
            mode_icon = "%s_%s_caps%s.ico" % (
                "eng" if is_ascii else "chi",
                "full" if is_full else "half",
                "on" if is_caps else "off",
            )
            self.changeButton("windows-mode-icon", icon=self._icon_path(mode_icon))

        self.changeButton(
            "switch-lang",
            icon=self._icon_path("eng.ico" if is_ascii else "chi.ico"),
        )
        self.changeButton(
            "switch-shape",
            icon=self._icon_path("full.ico" if is_full else "half.ico"),
        )

    def _toggle_rime_option(self, option_name):
        if not self._rime_session_id:
            return
        cur = self._rime_engine.get_option(self._rime_session_id, option_name)
        self._rime_engine.set_option(self._rime_session_id, option_name, not cur)
        self._update_lang_status()

    def onMenu(self, buttonId):
        if buttonId in ("settings", "windows-mode-icon"):
            if self._ensure_rime_session():
                ui_cfg = self._ui_settings.reload()
                style = self._rime_engine.build_style(
                    self._rime_session_id,
                    active_skin=ui_cfg.skin,
                    active_layout=ui_cfg.candidate_layout,
                )
                if style is not None:
                    self._rime_style = style
                    return style.menu
        return None

    def onCommand(self, commandId, commandType):
        if self._rime_style is None and not self._ensure_rime_session():
            return
        style = self._rime_style
        if commandId >= ID_URI and style is not None:
            uri = style.get_uri(commandId)
            if uri:
                self._open_path(uri)
        elif commandId >= ID_SCHEMA and style is not None:
            schema_id = style.get_schema(commandId)
            if schema_id and self._rime_session_id:
                self._rime_engine.select_schema(self._rime_session_id, schema_id)
        elif commandId >= ID_OPTION and style is not None:
            opt = style.get_option(commandId)
            if opt:
                self._toggle_rime_option(opt)
        elif commandId in (ID_ASCII_MODE, ID_MODE_ICON) and commandType == COMMAND_LEFT_CLICK:
            self._toggle_rime_option(b"ascii_mode")
        elif commandId == ID_FULL_SHAPE:
            self._toggle_rime_option(b"full_shape")
        elif commandId == ID_USER_DIR:
            os.makedirs(self._rime_engine.user_data_dir, exist_ok=True)
            self._open_path(self._rime_engine.user_data_dir)
        elif commandId == ID_SHARED_DIR:
            self._open_path(self._rime_engine.shared_data_dir)
        elif commandId == ID_SYNC_DIR:
            sync_dir = self._rime_engine.get_sync_dir()
            if sync_dir and os.path.isdir(sync_dir):
                self._open_path(sync_dir)
        elif commandId == ID_LOG_DIR:
            log_dir = os.path.dirname(_LOG_PATH)
            if log_dir and os.path.isdir(log_dir):
                self._open_path(log_dir)
        elif commandId == ID_PROFILES_FILE:
            self._open_path(self._config_path)
        elif commandId == ID_SYNC:
            threading.Thread(
                target=self._rime_engine.sync_user_data,
                name="cips-rime-sync",
                daemon=True,
            ).start()
        elif commandId == ID_DEPLOY:
            self._destroy_rime_session()
            self._rime_engine.redeploy_async()
        elif commandId == ID_TOGGLE_GRAM:
            new_enabled = not self._ui_settings.current.use_wanxiang_gram
            if self._rime_engine.set_gram_enabled(new_enabled, redeploy=True):
                self._ui_settings.update(use_wanxiang_gram=new_enabled)
                self._destroy_rime_session()
            else:
                self._ui_settings.update(use_wanxiang_gram=False)
                _log("cannot enable Wanxiang gram: wanxiang-lts-zh-hans.gram not installed")
        elif commandId == ID_TOGGLE_LAYOUT:
            cur = self._ui_settings.current.candidate_layout
            new_layout = "vertical" if cur == "horizontal" else "horizontal"
            cfg = self._ui_settings.update(candidate_layout=new_layout)
            self.customizeUI(candPerRow=cfg.cand_per_row)
            if self._rime_session_id:
                self._rime_engine.set_option(
                    self._rime_session_id,
                    b"_horizontal",
                    new_layout == "horizontal",
                )
        elif commandId in (ID_SKIN_FROST_ROUND, ID_SKIN_GEMINI_NEKO, ID_SKIN_NATIVE):
            skin_map = {
                ID_SKIN_FROST_ROUND: "frost_round",
                ID_SKIN_GEMINI_NEKO: "gemini_neko",
                ID_SKIN_NATIVE: "native",
            }
            new_skin = skin_map[commandId]
            self._ui_settings.update(skin=new_skin)
            if new_skin == "native":
                self._hide_candidate_ui()
        elif commandId == ID_GRAM_STATUS:
            gram = self._rime_engine.gram_path
            _log("gram status: %s (enabled=%s)" % (gram or "not installed", self._rime_engine.gram_enabled))

    @staticmethod
    def _open_path(target):
        try:
            os.startfile(target)
        except Exception as exc:
            _log("open path failed (%s): %r" % (target, exc))

    # ------------------------------------------------------------------
    # clipboard injection dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, action):
        # One physical chord can reach us through both filterKeyDown and
        # onPreservedKey; only the first one may fire.
        now = time.monotonic()
        if self._last_hotkey[0] == action and now - self._last_hotkey[1] < _HOTKEY_DEBOUNCE:
            return
        self._last_hotkey = (action, now)
        _log("hotkey %s" % action)

        if _WIN32_ERROR is not None:
            self._notify("cips-input 组件缺失: " + _WIN32_ERROR, duration=5)
            return

        if action == "replay":
            payload = self._last_payload
            if payload is None:
                self._notify("没有可重复的文本", duration=3)
                return
            text, mode, profile, char_delay_ms = payload
        else:
            text = self._read_payload()
            if text is None:
                return
            profile = self._resolve_profile()
            plan = resolve_dispatch(action, profile.mode, profile.char_delay_ms)
            mode, char_delay_ms = plan.mode, plan.char_delay_ms
            self._last_payload = (text, mode, profile, char_delay_ms)

        _log(
            "dispatch %s -> profile=%s mode=%s delay=%dms chars=%d"
            % (action, profile.name, mode, char_delay_ms, len(text))
        )
        try:
            self._start_typing(text, mode, profile, char_delay_ms)
        except ValueError as exc:
            self._notify("无法规划注入: %s" % exc, duration=4)

    def _read_payload(self):
        """Return normalized clipboard text, or None after notifying the user."""
        raw = read_clipboard_text()
        if not raw:
            if sniff_clipboard_has_text():
                self._notify("剪贴板里只有空文本", duration=3)
            else:
                self._notify("剪贴板没有文本", duration=3)
            return None
        text = normalize_text(raw)
        reason = should_refuse(text)
        if reason == "empty":
            self._notify("剪贴板内容为空或全是不可见字符", duration=3)
            return None
        if reason == "too_long":
            self._notify("文本过长（上限 %d 字）" % MAX_TEXT_LENGTH, duration=4)
            return None
        return text

    def _start_typing(self, text, mode, profile, char_delay_ms):
        if mode == "otp":
            text = "".join(ch for ch in strip_edges(text) if not ch.isspace())
            if not text:
                self._notify("验证码内容为空", duration=3)
                return
            chunks = plan_otp_chunks(
                text,
                per_box_wait_ms=profile.per_box_wait_ms,
                trailing_tab=profile.trailing_tab,
            )
        elif mode == "human":
            chunks = plan_human_chunks(text, settings=self._human_settings, rng=self._rng)
        elif mode == "human_normal":
            chunks = plan_human_chunks(
                text, settings=self._human_normal_settings, rng=self._rng
            )
        else:
            chunks = plan_chunks(
                text,
                mode=mode,
                char_delay_ms=char_delay_ms,
                chunk_size=profile.chunk_size,
                chunk_wait_ms=profile.chunk_wait_ms,
            )

        self._cancel_typing()
        stop_event = threading.Event()
        self._stop_event = stop_event
        try:
            self._target_hwnd = get_foreground_hwnd()
        except Exception:
            self._target_hwnd = 0

        estimated_ms = sum(chunk.wait_ms for chunk in chunks)
        _log(
            "typing: mode=%s chunks=%d chars=%d estimated=%.1fs"
            % (mode, len(chunks), len(text), estimated_ms / 1000.0)
        )
        if estimated_ms > _LONG_PAYLOAD_WARNING_MS:
            _log(
                "warning: this payload needs about %.0f s to type; press Esc or "
                "switch windows to abort" % (estimated_ms / 1000.0)
            )

        worker = threading.Thread(
            target=self._typing_worker,
            args=(chunks, mode, char_delay_ms, stop_event, self._target_hwnd),
            name="cips-typing",
            daemon=True,
        )
        self._typing_thread = worker
        worker.start()
        self._notify("逐字注入 %d 字…" % len(text), duration=2)

    # ------------------------------------------------------------------
    # background typing
    # ------------------------------------------------------------------

    def _typing_worker(self, chunks, mode, char_delay_ms, stop_event, target_hwnd):
        last_index = len(chunks) - 1
        try:
            for index, chunk in enumerate(chunks):
                if stop_event.is_set():
                    return
                if target_hwnd and get_foreground_hwnd() != target_hwnd:
                    _log("typing aborted at %d/%d: focus left the target window" % (index, len(chunks)))
                    stop_event.set()
                    return
                per_char_delay = char_delay_ms if mode == "segmented" else 0
                type_unicode(chunk.text, char_delay_ms=per_char_delay, stop_event=stop_event)
                if index < last_index and chunk.wait_ms > 0:
                    stop_event.wait(chunk.wait_ms / 1000.0)
        except UipiBlocked:
            self._report("注入被拦截：目标窗口权限更高（UIPI）。请在同等权限下运行宿主程序")
        except InjectError as exc:
            self._report("注入失败: %s" % exc)
        except Exception as exc:
            self._report("注入异常: %r" % (exc,))

    def _report(self, message):
        """Record a runtime failure. stdout is not a log; cips.log is."""
        _log(message)

    def _notify(self, message, duration=3):
        """Log a user-facing message and, only when it is safe, show a toast.

        Only ``onKeyDown`` owns a TSF edit session, so only calls made inside
        ``onKeyDown`` may invoke ``showMessage``.
        """
        _log(message)
        if self._in_key_down:
            self.showMessage(message, duration=duration)

    def _is_typing(self):
        thread = self._typing_thread
        return thread is not None and thread.is_alive()

    def _cancel_typing(self, reason=None):
        if reason:
            _log("typing cancelled: %s" % reason)
        if self._stop_event is not None:
            self._stop_event.set()
        self._stop_event = None
        self._typing_thread = None
        self._target_hwnd = 0

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------

    def _reload_profiles(self, force=False):
        try:
            mtime = os.path.getmtime(self._config_path)
        except OSError:
            mtime = None
        if not force and mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        book = ProfileBook.from_json(self._config_path)
        self._profiles = book
        self._human_settings, self._human_normal_settings = self._read_human_settings()
        _log("profiles loaded: %d, default=%s" % (len(book), book.default.name))

    def _read_human_settings(self):
        try:
            with open(self._config_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return {}, {}
        if not isinstance(data, dict):
            return {}, {}
        slow = data.get("human")
        normal = data.get("human_normal")
        return (
            slow if isinstance(slow, dict) else {},
            normal if isinstance(normal, dict) else {},
        )

    def _resolve_profile(self):
        try:
            target = get_foreground_target()
        except Exception:
            target = None
        return self._profile_for(target)

    def _profile_for(self, target):
        if self._profiles is None:
            self._reload_profiles(force=True)
        exe = target.exe_name if target is not None else None
        window_class = target.window_class if target is not None else None
        return self._profiles.resolve(exe=exe, window_class=window_class)
