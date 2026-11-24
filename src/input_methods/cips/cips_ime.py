"""cips-input text service: type clipboard text into paste-hostile fields.

Why this exists
---------------
A normal paste arrives as ``beforeinput`` with ``inputType ==
"insertFromPaste"``, and a page is free to reject it (``preventDefault`` on
``paste``, ``onpaste="return false"``, or an ``isTrusted`` check that a
synthetic event cannot pass).

This service synthesises real keyboard input instead: ``SendInput`` with
``KEYEVENTF_UNICODE``, one keydown/keyup pair per character. The target sees a
genuine keystroke, ``isTrusted`` is always true, and no ``paste``/``beforeinput``
event fires at all.

Why there is only one delivery channel
--------------------------------------
TSF's ``setCommitString`` could commit the whole string in one edit session,
but PIME only hands an edit session to ``onKeyDown``. A hotkey is claimed by
``filterKeyDown`` / ``onPreservedKey``, and neither callback owns a session, so
PIME silently drops the commit string (``PIMETextService/PIMEClient.cpp:332``).
The F24 bridge that was supposed to conjure a session never worked either --
TSF does not route injected keys back into the input method -- and was removed
in v0.2. All that is left is typing real keystrokes, with the pace chosen to
fit the target.

The keystroke path runs on a worker thread and returns immediately. This is
deliberate: PIME's client waits at most 2 seconds for an RPC reply
(``kDefaultRpcTimeoutMs`` in ``PIMETextService/PIMEClient.h``) and treats a
late reply as a dead connection, dropping the backend. A blocking sleep inside
``onKeyDown`` would therefore kill the input method rather than type slowly.

The payload is read from the clipboard, so the workflow is: copy the text,
focus the field, press the hotkey.
"""

import json
import os
import random
import threading
import time

# PIME puts its own ``python`` directory on sys.path, so the framework module
# is imported as a top-level module, not as part of this package.
from textService import TF_MOD_ALT, TF_MOD_CONTROL, TextService

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

try:
    from .win32.clipboard import read_clipboard_text, sniff_clipboard_has_text
    from .win32.sendinput import InjectError, UipiBlocked, type_unicode
    from .win32.target import get_foreground_hwnd, get_foreground_target

    _WIN32_ERROR = None
except Exception as exc:  # pragma: no cover - only on a broken deployment
    _WIN32_ERROR = str(exc)


# Virtual key codes (winuser.h). keyStates in a PIME KeyEvent is the 256 entry
# keyboard state array, indexed by virtual key code, bit 7 meaning "down".
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_V = 0x56
VK_Z = 0x5A
VK_1 = 0x31
VK_2 = 0x32
VK_3 = 0x33
VK_ESCAPE = 0x1B

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

# The same chords also have to be declared to TSF as preserved keys, which is
# the framework's own mechanism for an IME hotkey: the keystroke manager then
# claims the chord for us and calls onPreservedKey instead of letting the
# application (or its accelerator table) swallow it. filterKeyDown alone only
# works on keys the framework happens to route here, which is why the hotkeys
# did nothing while plain letters came through fine.
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

#: Payloads that will take longer than this get an extra diagnostic line; the
#: injection is still interruptible with Esc or by switching windows.
_LONG_PAYLOAD_WARNING_MS = 120_000

_LOG_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "PIME", "Logs", "cips.log"
)
_LOG_LOCK = threading.Lock()


def _log(message):
    """Append one diagnostic line to cips.log.

    PIME only reads protocol replies from the backend's stdout and drops
    everything else, and only *uncaught* exceptions reach python_backend.log.
    A deliberate diagnostic line therefore needs a file of its own.

    Called on activation and on hotkey dispatch only -- never per keystroke --
    and every failure is swallowed: diagnostics must not break the IME.
    """
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with _LOG_LOCK:
            with open(_LOG_PATH, "a", encoding="utf-8") as handle:
                handle.write("%s %s\n" % (stamp, message))
    except Exception:
        pass


class CipsTextService(TextService):
    """Stateless passthrough IME; all behaviour hangs off the hotkeys.

    Nothing is composed for ordinary typing, so ``filterKeyDown`` returns
    False for everything except our own combinations. That keeps the IME
    transparent: the user can leave cips-input selected and keep typing
    normally.
    """

    def __init__(self, client):
        super().__init__(client)

        self._config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles.json")
        self._profiles = None
        self._config_mtime = None
        self._last_config_check = 0.0

        # The previous successful dispatch, replayed verbatim by Ctrl+Alt+Z:
        # (text, mode, profile, char_delay_ms).
        self._last_payload = None
        self._typing_thread = None
        self._stop_event = None
        self._in_key_down = False
        self._preserved_requested = False

        # Where the injection was aimed (so switching windows aborts it), plus
        # the two human-rhythm blocks read from profiles.json.
        self._target_hwnd = 0
        self._human_settings = {}
        self._human_normal_settings = {}
        self._rng = random.Random()

        # Bounded, per-session diagnostics: the first time each RPC method and
        # each distinct virtual key arrives, plus which foreground app the
        # service was activated in. This is what tells "TSF never routed keys
        # here" apart from "the hotkey matcher did not recognise the chord",
        # and it stays quiet after a handful of lines.
        self._seen_methods = set()
        self._seen_keycodes = set()
        self._modifier_events = 0
        self._last_hotkey = (None, 0.0)

        # Reaching this line means PIME imported the module and instantiated
        # the service, so cips.log existing at all is proof the IME loaded.
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

        # Since Windows 8 every text service starts with the keyboard
        # compartment *closed*, and a closed service is a pass-through that
        # receives no key events at all -- which is exactly why the hotkeys
        # never fired. Declaring the keyboard open is what makes TSF route
        # keys here; typing stays transparent because filterKeyDown only ever
        # claims the hotkey chords and returns False for everything else.
        if getattr(self.client, "isWindows8Above", False):
            self.setKeyboardOpen(True)

        # Claim the chords at the framework level as well. A preserved key is
        # what a real IME uses for its own shortcuts: TSF's keystroke manager
        # claims the combination and calls onPreservedKey, so the focused
        # application never gets a chance to swallow it.
        #
        # This is done once per text-service instance, not on every
        # activation: PIME creates one instance per host application process,
        # and TSF keeps the registration for the lifetime of that process.
        # Re-adding the same GUID on every focus switch only produces duplicate
        # registration errors on the C++ side. Note that addPreservedKey() has
        # no return channel, so this line proves the request was made -- not
        # that TSF accepted it. The observable proof is the
        # "preserved key <guid> ->" line written by onPreservedKey.
        if not self._preserved_requested:
            for key_code, (guid, _action) in _PRESERVED_KEYS.items():
                self.addPreservedKey(key_code, _HOTKEY_MODIFIERS, guid)
            self._preserved_requested = True
            _log(
                "preserved keys requested: %d (once per host process)"
                % len(_PRESERVED_KEYS)
            )

    def onDeactivate(self):
        _log("deactivate")
        self._cancel_typing()

    def onKeyboardStatusChanged(self, opened):
        # The shell can close the keyboard compartment behind our back, and
        # while it is closed the framework stops routing keys here entirely --
        # which from the outside looks exactly like a broken hotkey.
        _log("keyboard status: opened=%s" % opened)
        self.keyboardOpen = bool(opened)

    def onCompartmentChanged(self, guid):
        _log("compartment changed: %s" % guid)

    def checkConfigChange(self):
        """Poll profiles.json, throttled.

        The base class calls this on every single request, so reading the file
        unconditionally would put a stat() (and worst case a full JSON parse)
        in the path of every keystroke.
        """
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
            # Keys pressed while a modifier is held are the interesting ones:
            # if a chord never shows up here, the framework never routed it.
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
        return (
            self._hotkey_action(keyEvent) is not None
            or (keyEvent.keyCode == VK_ESCAPE and self._is_typing())
        )

    def onKeyDown(self, keyEvent):
        # This is the only callback PIME hands an edit session to, so it is the
        # only place where a toast is safe. See _notify.
        self._in_key_down = True
        try:
            return self._handle_key_down(keyEvent)
        finally:
            self._in_key_down = False

    def _handle_key_down(self, keyEvent):
        if keyEvent.keyCode == VK_ESCAPE and self._is_typing():
            # Escape is claimed only while we are injecting, so it is a
            # reliable panic button without stealing Escape from the app.
            self._cancel_typing("escape")
            return True
        action = self._hotkey_action(keyEvent)
        if action is not None:
            # Holding the combination should not fire it repeatedly.
            if keyEvent.repeatCount > 1:
                return True
            self._dispatch(action)
            return True
        return False

    def onPreservedKey(self, guid):
        """Framework-level hotkey notification.

        This is the path that works when the application or its accelerator
        table would otherwise consume the chord. It has no edit session, so it
        can only log -- see ``_notify``.
        """
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

    # ------------------------------------------------------------------
    # dispatch
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
        """Return normalized clipboard text, or None after notifying the user.

        ``read_clipboard_text`` returns "" both for "clipboard is locked/empty"
        and for "clipboard holds an empty string"; sniffing the format lets the
        log say which one it was instead of guessing.
        """
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
            # A code copied from a web page or an SMS commonly carries a
            # trailing newline. In OTP mode every character becomes its own
            # keystroke, so that newline would arrive as a real Enter press
            # and submit the form before the last digit is in. Whitespace is
            # never meaningful in a one-time code, so drop it here rather
            # than letting plan_otp_chunks emit it.
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
                # Typing into the wrong window is the one failure mode that
                # leaks text where it does not belong, so give up as soon as
                # focus moves instead of finishing the payload.
                if target_hwnd and get_foreground_hwnd() != target_hwnd:
                    _log("typing aborted at %d/%d: focus left the target window" % (index, len(chunks)))
                    stop_event.set()
                    return
                # In human/char/otp mode the pause lives in Chunk.wait_ms so that
                # a cancel takes effect between characters, not inside SendInput.
                per_char_delay = char_delay_ms if mode == "segmented" else 0
                type_unicode(chunk.text, char_delay_ms=per_char_delay, stop_event=stop_event)
                # Sleeping after the final chunk would only delay the handover
                # back to the user.
                if index < last_index and chunk.wait_ms > 0:
                    stop_event.wait(chunk.wait_ms / 1000.0)
        except UipiBlocked:
            self._report("注入被拦截：目标窗口权限更高（UIPI）。请在同等权限下运行宿主程序")
        except InjectError as exc:
            self._report("注入失败: %s" % exc)
        except Exception as exc:  # keep the backend alive whatever happens
            self._report("注入异常: %r" % (exc,))

    def _report(self, message):
        """Record a runtime failure. stdout is not a log; cips.log is."""
        _log(message)

    def _notify(self, message, duration=3):
        """Log a user-facing message and, only when it is safe, show a toast.

        PIME calls ``updateMessageWindow()`` *before* its edit-session check
        (``PIMEClient.cpp:332``), and that function dereferences the session
        (:141). A reply carrying ``showMessage`` from a callback without a
        session -- which is every preserved key -- therefore crashes the host
        application with 0xc000041d. ``onKeyDown`` is the only callback that
        owns a session, so it is the only one allowed to pop a toast. When a
        chord arrives through ``onPreservedKey`` the message stays in cips.log;
        that is why the log is the primary diagnostic.
        """
        _log(message)
        if self._in_key_down:
            super().showMessage(message, duration=duration)

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
        # from_json is deliberately forgiving: a missing or malformed file
        # silently yields a book holding only the default profile. Log the
        # outcome of each reload so that a typo is at least visible in
        # cips.log instead of silently changing behaviour.
        _log("profiles loaded: %d, default=%s" % (len(book), book.default.name))

    def _read_human_settings(self):
        """Return the two optional rhythm blocks of profiles.json.

        ``"human"`` drives Ctrl+Alt+1 (the slower rhythm) and
        ``"human_normal"`` drives Ctrl+Alt+V when its profile selects that
        mode. The profile book only knows application profiles, so these are
        read separately. Every failure falls back to {} which means "use the
        defaults in core.policy".
        """
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
        """Profile for the current foreground window (best effort)."""
        try:
            target = get_foreground_target()
        except Exception:
            # Profile resolution is best effort; a failure here must not stop
            # the injection from happening with the default profile.
            target = None
        return self._profile_for(target)

    def _profile_for(self, target):
        if self._profiles is None:
            self._reload_profiles(force=True)
        exe = target.exe_name if target is not None else None
        window_class = target.window_class if target is not None else None
        return self._profiles.resolve(exe=exe, window_class=window_class)
