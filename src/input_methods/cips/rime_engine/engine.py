"""Process-wide Rime engine manager for cips-input.

Design constraints imposed by PIME (see ``docs/pime-api.md``):
* PIME runs all TextService instances inside one 32-bit ``python.exe`` process
  and gives every key/menu RPC at most 2000 ms (15000 ms for ``onActivate``).
* First-time Rime deployment (when ``user_data_dir/build`` does not exist yet
  or after schema edits) takes several seconds. Blocking ``onActivate`` or
  ``onKeyDown`` would trip PIME's timeout and kill the backend.

Therefore ``RimeEngine`` owns a single process-wide librime instance and runs
``initialize`` + ``start_maintenance`` on a background thread. Callers check
``state`` without blocking: while ``is_loading``, the text service keeps
hotkeys working and tells the user on printable keypresses that Rime is still
warming up; once ``is_ready``, normal typing runs synchronously in a few
milliseconds.
"""

from __future__ import annotations

import os
import threading
import time
from ctypes import byref, c_int
from typing import Callable

from .librime_api import (
    RimeCommit,
    RimeConfig,
    RimeContext,
    RimeNotificationHandler,
    RimeTraits,
    load_rime_dll,
)
from .style import RimeStyle

APP_NAME = "cips"
DIST_NAME = "cips-input"
DIST_CODE_NAME = "cips"
DIST_VERSION = "0.5.0"
CONFIG_FILE = "cips.yaml"
GRAM_FILENAME = "wanxiang-lts-zh-hans.gram"

GRAM_SCHEMAS = (
    "rime_frost",
    "rime_frost_double_pinyin",
    "rime_frost_double_pinyin_flypy",
    "rime_frost_double_pinyin_mspy",
    "rime_frost_double_pinyin_sogou",
    "rime_frost_double_pinyin_abc",
    "rime_frost_double_pinyin_ziguang",
)
MANAGED_GRAM_HEADER = "# cips-managed-gram-toggle"
_DISABLE_GRAM_YAML = (
    f"{MANAGED_GRAM_HEADER}\n"
    "patch:\n"
    '  grammar/language: ""\n'
    "  translator/contextual_suggestions: false\n"
)


class RimeState:
    UNINITIALIZED = "uninitialized"
    LOADING = "loading"
    READY = "ready"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


def default_user_data_dir() -> str:
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "cips-input", "rime")


def detect_runtime_paths(cips_dir: str, user_data_dir: str | None = None) -> tuple[str, str, str]:
    """Return ``(dll_path, shared_data_dir, user_data_dir)`` under ``cips_dir``."""
    dll_path = os.path.join(cips_dir, "rime_runtime", "rime.dll")
    shared_dir = os.path.join(cips_dir, "rime_data")
    user_dir = user_data_dir or default_user_data_dir()
    return dll_path, shared_dir, user_dir


def find_gram_file(shared_data_dir: str, user_data_dir: str) -> str | None:
    """Return path to ``wanxiang-lts-zh-hans.gram`` if installed, else ``None``."""
    for directory in (user_data_dir, shared_data_dir):
        if not directory:
            continue
        candidate = os.path.join(directory, GRAM_FILENAME)
        if os.path.isfile(candidate):
            return candidate
    return None


class RimeEngine:
    """Thread-safe wrapper around one librime instance shared by all sessions."""

    def __init__(
        self,
        cips_dir: str,
        user_data_dir: str | None = None,
        log_fn: Callable[[str], None] | None = None,
        dll_path: str | None = None,
        shared_data_dir: str | None = None,
        use_wanxiang_gram: bool = True,
    ):
        self._cips_dir = os.path.abspath(cips_dir)
        default_dll, default_shared, default_user = detect_runtime_paths(
            self._cips_dir, user_data_dir=user_data_dir
        )
        self._dll_path = os.path.abspath(dll_path) if dll_path else default_dll
        self._shared_dir = os.path.abspath(shared_data_dir) if shared_data_dir else default_shared
        self._user_dir = default_user
        self._log = log_fn or (lambda _msg: None)
        self._use_gram_pref = bool(use_wanxiang_gram)

        self._lock = threading.RLock()
        self._state = RimeState.UNINITIALIZED
        self._error: str | None = None
        self._lib = None
        self._rime = None
        self._traits = None
        self._notify_cb = None
        self._worker: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._redeploy_requested = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def is_available(self) -> bool:
        return os.path.isfile(self._dll_path) and os.path.isdir(self._shared_dir)

    @property
    def is_loading(self) -> bool:
        with self._lock:
            return self._state == RimeState.LOADING

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._state == RimeState.READY and self._rime is not None

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def shared_data_dir(self) -> str:
        return self._shared_dir

    @property
    def user_data_dir(self) -> str:
        return self._user_dir

    @property
    def gram_path(self) -> str | None:
        return find_gram_file(self._shared_dir, self._user_dir)

    @property
    def gram_enabled(self) -> bool:
        return self.gram_path is not None and self._use_gram_pref

    def sync_gram_patches(self, enabled: bool) -> bool:
        """Synchronize managed ``*.custom.yaml`` gram overrides in ``user_data_dir``.

        Returns ``True`` if any file on disk changed (meaning a redeploy is
        needed), ``False`` otherwise. Hand-edited user ``*.custom.yaml`` files
        that do not carry :data:`MANAGED_GRAM_HEADER` are left untouched.
        """
        self._use_gram_pref = bool(enabled)
        os.makedirs(self._user_dir, exist_ok=True)
        changed = False
        gram_present = self.gram_path is not None
        shared_has_gram_patch = False
        shared_custom = os.path.join(self._shared_dir, "rime_frost.custom.yaml")
        if os.path.isfile(shared_custom):
            try:
                with open(shared_custom, "r", encoding="utf-8") as handle:
                    shared_has_gram_patch = "wanxiang-lts-zh-hans" in handle.read()
            except OSError:
                shared_has_gram_patch = False

        enable_yaml = None
        if enabled and gram_present and not shared_has_gram_patch:
            patch_src = os.path.join(self._cips_dir, "rime_config", "gram_patch.yaml")
            if os.path.isfile(patch_src):
                with open(patch_src, "r", encoding="utf-8") as handle:
                    enable_yaml = f"{MANAGED_GRAM_HEADER}\n{handle.read()}"

        for schema in GRAM_SCHEMAS:
            target = os.path.join(self._user_dir, f"{schema}.custom.yaml")
            existing = None
            if os.path.isfile(target):
                try:
                    with open(target, "r", encoding="utf-8") as handle:
                        existing = handle.read()
                except OSError:
                    continue
                if not existing.startswith(MANAGED_GRAM_HEADER):
                    continue

            if not enabled:
                desired = _DISABLE_GRAM_YAML
            elif enable_yaml is not None:
                desired = enable_yaml
            else:
                desired = None

            if desired is None:
                if existing is not None:
                    os.remove(target)
                    changed = True
            elif existing != desired:
                with open(target, "w", encoding="utf-8") as handle:
                    handle.write(desired)
                changed = True
        return changed

    def set_gram_enabled(self, enabled: bool, redeploy: bool = True) -> bool:
        """Enable or disable loading ``wanxiang-lts-zh-hans.gram`` at runtime."""
        if enabled and self.gram_path is None:
            self._use_gram_pref = False
            return False
        changed = self.sync_gram_patches(enabled)
        self._log("gram preference set to %s (changed=%s)" % (enabled, changed))
        if redeploy and self._rime is not None and changed:
            self.redeploy_async()
        return True

    def ensure_started(self) -> str:
        """Start background initialization if not started yet; return current state."""
        with self._lock:
            if self._state in (RimeState.LOADING, RimeState.READY):
                return self._state
            if not self.is_available:
                self._state = RimeState.UNAVAILABLE
                self._error = "rime_runtime/rime.dll or rime_data not found"
                self._ready_event.set()
                return self._state

            self._state = RimeState.LOADING
            self._error = None
            self._ready_event.clear()
            worker = threading.Thread(
                target=self._init_worker,
                name="cips-rime-init",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return self._state

    def wait_ready(self, timeout: float | None = None) -> bool:
        return self._ready_event.wait(timeout=timeout)

    def redeploy_async(self) -> bool:
        """Trigger a non-blocking Rime maintenance deployment."""
        with self._lock:
            if self._state == RimeState.LOADING:
                return False
            if self._rime is None:
                return self.ensure_started() == RimeState.LOADING
            self._state = RimeState.LOADING
            self._ready_event.clear()
            worker = threading.Thread(
                target=self._redeploy_worker,
                name="cips-rime-deploy",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return True

    def _init_worker(self) -> None:
        t0 = time.perf_counter()
        try:
            os.makedirs(self._user_dir, exist_ok=True)
            self.sync_gram_patches(self._use_gram_pref)
            lib, rime = load_rime_dll(self._dll_path)

            traits = RimeTraits(
                shared_data_dir=self._shared_dir.encode("utf-8"),
                user_data_dir=self._user_dir.encode("utf-8"),
                distribution_name=DIST_NAME.encode("utf-8"),
                distribution_code_name=DIST_CODE_NAME.encode("utf-8"),
                distribution_version=DIST_VERSION.encode("utf-8"),
                app_name=(f"rime.{APP_NAME}").encode("utf-8"),
                min_log_level=2,  # ERROR only; keep stderr quiet during normal typing
                log_dir=b"",
            )

            def _on_notify(_ctx, _sid, msg_type, msg_value):
                if msg_type == b"deploy":
                    self._log(
                        "rime deploy: %s"
                        % (msg_value.decode("utf-8", errors="replace") if msg_value else "")
                    )

            notify_cb = RimeNotificationHandler(_on_notify)
            rime.setup(byref(traits))
            rime.set_notification_handler(notify_cb, None)
            rime.initialize(None)

            # First run on a new machine populates user_data_dir/build (~5s);
            # subsequent runs see no dirty timestamps and finish in ~3 ms.
            if rime.start_maintenance(False):
                rime.join_maintenance_thread()
            rime.deploy_config_file(CONFIG_FILE.encode("utf-8"), b"config_version")

            gram = self.gram_path
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            with self._lock:
                self._lib = lib
                self._rime = rime
                self._traits = traits
                self._notify_cb = notify_cb
                self._state = RimeState.READY
                self._error = None
            self._log(
                "rime ready in %.0fms (version=%s gram=%s enabled=%s)"
                % (
                    elapsed_ms,
                    rime.get_version().decode("utf-8", errors="replace"),
                    os.path.basename(gram) if gram else "none",
                    self.gram_enabled,
                )
            )
        except Exception as exc:
            with self._lock:
                self._state = RimeState.ERROR
                self._error = str(exc)
            self._log("rime init failed: %r" % (exc,))
        finally:
            self._ready_event.set()

    def _redeploy_worker(self) -> None:
        t0 = time.perf_counter()
        try:
            rime = self._rime
            if rime is None:
                return
            rime.cleanup_all_sessions()
            if rime.start_maintenance(True):
                rime.join_maintenance_thread()
            rime.deploy_config_file(CONFIG_FILE.encode("utf-8"), b"config_version")
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            with self._lock:
                self._state = RimeState.READY
                self._error = None
            self._log("rime redeploy finished in %.0fms" % elapsed_ms)
        except Exception as exc:
            with self._lock:
                self._state = RimeState.ERROR
                self._error = str(exc)
            self._log("rime redeploy failed: %r" % (exc,))
        finally:
            self._ready_event.set()

    # ------------------------------------------------------------------
    # session operations (only valid when is_ready)
    # ------------------------------------------------------------------

    def create_session(
        self,
        active_skin: str = "frost_round",
        active_layout: str = "horizontal",
    ) -> tuple[int, RimeStyle | None]:
        with self._lock:
            if not self.is_ready:
                return 0, None
            sid = self._rime.create_session()
            if not sid:
                return 0, None
            style = RimeStyle(
                self._rime,
                APP_NAME,
                sid,
                gram_installed=(self.gram_path is not None),
                gram_enabled=self.gram_enabled,
                active_skin=active_skin,
                active_layout=active_layout,
            )
            self._rime.set_option(sid, b"soft_cursor", 1 if style.soft_cursor else 0)
            self._rime.set_option(
                sid, b"_horizontal", 1 if active_layout == "horizontal" else 0
            )
            return sid, style

    def find_session(self, session_id: int) -> bool:
        with self._lock:
            if not self.is_ready or not session_id:
                return False
            return bool(self._rime.find_session(session_id))

    def destroy_session(self, session_id: int) -> None:
        with self._lock:
            if self._rime is not None and session_id:
                try:
                    self._rime.clear_composition(session_id)
                    self._rime.destroy_session(session_id)
                except Exception:
                    pass

    def process_key(self, session_id: int, keysym: int, mask: int) -> bool:
        with self._lock:
            if not self.is_ready or not session_id:
                return False
            return bool(self._rime.process_key(session_id, keysym, mask))

    def clear_composition(self, session_id: int) -> None:
        with self._lock:
            if self.is_ready and session_id:
                self._rime.clear_composition(session_id)

    def get_option(self, session_id: int, name: bytes) -> bool:
        with self._lock:
            if not self.is_ready or not session_id:
                return False
            return bool(self._rime.get_option(session_id, name))

    def set_option(self, session_id: int, name: bytes, value: bool) -> None:
        with self._lock:
            if self.is_ready and session_id:
                self._rime.set_option(session_id, name, 1 if value else 0)

    def select_schema(self, session_id: int, schema_id: bytes) -> None:
        with self._lock:
            if not self.is_ready or not session_id:
                return
            self._rime.select_schema(session_id, schema_id)
            user_cfg = RimeConfig()
            if self._rime.config_open(b"user", byref(user_cfg)):
                try:
                    self._rime.config_set_string(
                        byref(user_cfg), b"var/previously_selected_schema", schema_id
                    )
                    self._rime.config_set_int(
                        byref(user_cfg),
                        b"var/schema_access_time/" + schema_id,
                        c_int(int(time.time())),
                    )
                finally:
                    self._rime.config_close(byref(user_cfg))

    def sync_user_data(self) -> bool:
        with self._lock:
            if not self.is_ready:
                return False
            return bool(self._rime.sync_user_data())

    def get_sync_dir(self) -> str:
        with self._lock:
            if not self.is_ready:
                return ""
            raw = self._rime.get_sync_dir()
            return raw.decode("utf-8", errors="replace") if raw else ""

    def read_output(self, session_id: int, style: RimeStyle) -> dict:
        """Read commit text, preedit, cursor, selection keys and candidates."""
        result = {
            "commit": None,
            "composing": False,
            "preedit": "",
            "cursor": 0,
            "select_keys": None,
            "candidates": [],
            "candidate_cursor": 0,
            "show_candidates": False,
        }
        with self._lock:
            if not self.is_ready or not session_id:
                return result
            rime = self._rime

            commit = RimeCommit()
            if rime.get_commit(session_id, byref(commit)):
                try:
                    if commit.text:
                        result["commit"] = commit.text.decode("utf-8", errors="replace")
                finally:
                    rime.free_commit(byref(commit))

            ctx = RimeContext()
            if not rime.get_context(session_id, byref(ctx)):
                return result
            try:
                if ctx.composition.length == 0:
                    return result
                result["composing"] = True

                n_page = ctx.menu.page_size
                if n_page:
                    sel = b""
                    if ctx.select_labels:
                        for i in range(n_page):
                            if ctx.select_labels[i]:
                                sel += ctx.select_labels[i]
                    elif ctx.menu.select_keys:
                        sel = ctx.menu.select_keys
                    else:
                        sel = bytes(((i + 1) % 10 + 48 for i in range(n_page)))
                    result["select_keys"] = sel.decode("utf-8", errors="replace")

                inline = style.inline_preedit
                if inline in ("composition", "true"):
                    raw_preedit = ctx.composition.preedit
                    preedit = raw_preedit.decode("utf-8", errors="replace") if raw_preedit else ""
                    cursor = (
                        len(raw_preedit[: ctx.composition.cursor_pos].decode("utf-8", errors="replace"))
                        if raw_preedit
                        else 0
                    )
                elif inline in ("preview", "preedit"):
                    preedit = (
                        ctx.commit_text_preview.decode("utf-8", errors="replace")
                        if ctx.commit_text_preview
                        else ""
                    )
                    cursor = len(preedit)
                elif inline == "input":
                    raw_inp = rime.get_input(session_id)
                    preedit = raw_inp.decode("utf-8", errors="replace") if raw_inp else ""
                    cursor = len(preedit)
                else:
                    preedit = ""
                    cursor = 0
                result["preedit"] = preedit
                result["cursor"] = cursor

                hide_candidate = bool(rime.get_option(session_id, b"_hide_candidate"))
                if ctx.menu.num_candidates and not hide_candidate:
                    cands: list[str] = []
                    for i in range(ctx.menu.num_candidates):
                        cand = ctx.menu.candidates[i]
                        text = cand.text.decode("utf-8", errors="replace") if cand.text else ""
                        if cand.comment:
                            comment = cand.comment.decode("utf-8", errors="replace")
                            try:
                                text = style.candidate_format.format(text, comment)
                            except Exception:
                                text = f"{text} {comment}"
                        cands.append(text)
                    result["candidates"] = cands
                    result["candidate_cursor"] = ctx.menu.highlighted_candidate_index
                    result["show_candidates"] = True
            finally:
                rime.free_context(byref(ctx))

        return result

    def build_style(
        self,
        session_id: int,
        active_skin: str = "frost_round",
        active_layout: str = "horizontal",
    ) -> RimeStyle | None:
        with self._lock:
            if not self.is_ready or not session_id:
                return None
            return RimeStyle(
                self._rime,
                APP_NAME,
                session_id,
                gram_installed=(self.gram_path is not None),
                gram_enabled=self.gram_enabled,
                active_skin=active_skin,
                active_layout=active_layout,
            )

    def finalize(self) -> None:
        with self._lock:
            if self._rime is not None:
                try:
                    self._rime.cleanup_all_sessions()
                    self._rime.finalize()
                except Exception:
                    pass
                self._rime = None
                self._state = RimeState.UNINITIALIZED
