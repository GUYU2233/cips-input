"""Read UI style and language-bar menu definitions from cips.yaml via librime."""

from __future__ import annotations

import ctypes
import os
from ctypes import byref, c_int

from .librime_api import RimeConfig, RimeConfigIterator, RimeSchemaList

ID_MODE_ICON = 1
ID_ASCII_MODE = 2
ID_FULL_SHAPE = 3

ID_DEPLOY = 10
ID_SYNC = 11
ID_SYNC_DIR = 12
ID_SHARED_DIR = 13
ID_USER_DIR = 14
ID_SCHEMA_LIST = 15
ID_LOG_DIR = 16
ID_GRAM_STATUS = 17
ID_PROFILES_FILE = 18
ID_TOGGLE_GRAM = 19
ID_TOGGLE_LAYOUT = 20
ID_SKIN_FROST_ROUND = 21
ID_SKIN_GEMINI_NEKO = 22
ID_SKIN_NATIVE = 23

ID_OPTION = 100
ID_SCHEMA = 200
ID_URI = 300

_COMMANDS = {
    "deploy": ID_DEPLOY,
    "sync_user_data": ID_SYNC,
    "get_sync_dir": ID_SYNC_DIR,
    "get_shared_data_dir": ID_SHARED_DIR,
    "get_user_data_dir": ID_USER_DIR,
    "get_schema_list": ID_SCHEMA_LIST,
    "get_log_dir": ID_LOG_DIR,
    "gram_status": ID_GRAM_STATUS,
    "toggle_gram": ID_TOGGLE_GRAM,
    "toggle_layout": ID_TOGGLE_LAYOUT,
    "skin_frost_round": ID_SKIN_FROST_ROUND,
    "skin_gemini_neko": ID_SKIN_GEMINI_NEKO,
    "skin_native": ID_SKIN_NATIVE,
    "open_profiles": ID_PROFILES_FILE,
}

_CHAR_BUF = 128

try:
    from opencc import OpenCC as _OpenCC
except Exception:  # pragma: no cover - optional in stripped test environments
    _OpenCC = None


def _get_cstring(rime, config, key: bytes) -> str:
    raw = rime.config_get_cstring(byref(config), key)
    return raw.decode("utf-8", errors="replace") if raw else ""


class RimeStyle:
    """Snapshot of UI appearance and language-bar menu state for one session."""

    def __init__(
        self,
        rime,
        app_name: str,
        session_id: int,
        gram_installed: bool = False,
        gram_enabled: bool = False,
        active_skin: str = "frost_round",
        active_layout: str = "horizontal",
    ):
        self._rime = rime
        self.session_id = session_id
        self.gram_installed = gram_installed
        self.gram_enabled = gram_enabled
        self.active_skin = active_skin
        self.active_layout = active_layout

        self.font_face = "Microsoft YaHei UI"
        self.font_point = 12
        self.candidate_format = "{0} {1}"
        self.inline_preedit = "composition"
        self.candidate_per_row = 8
        self.display_tray_icon = True
        self.candidate_use_cursor = True
        self.soft_cursor = False
        self._menu_opencc = None

        self.options: list[bytes] = []
        self.schemas: list[bytes] = []
        self.uris: list[str] = []
        self.menu: list[dict] = []

        if not rime or not session_id:
            return

        config = RimeConfig()
        if not rime.config_open(app_name.encode("utf-8"), byref(config)):
            return

        try:
            self.font_face = _get_cstring(rime, config, b"style/font_face") or self.font_face
            self.candidate_format = (
                _get_cstring(rime, config, b"style/candidate_format") or self.candidate_format
            )
            self.inline_preedit = (
                _get_cstring(rime, config, b"style/inline_preedit") or self.inline_preedit
            )
            opencc_cfg = _get_cstring(rime, config, b"style/menu_opencc")
            if opencc_cfg and _OpenCC is not None:
                try:
                    self._menu_opencc = _OpenCC(opencc_cfg)
                except Exception:
                    self._menu_opencc = None

            value = c_int()
            if rime.config_get_int(byref(config), b"style/font_point", byref(value)):
                self.font_point = value.value
            if rime.config_get_bool(byref(config), b"style/horizontal", byref(value)):
                self.candidate_per_row = 8 if bool(value.value) else 1
            if rime.config_get_int(byref(config), b"style/candidate_per_row", byref(value)):
                self.candidate_per_row = value.value
            if rime.config_get_bool(byref(config), b"style/display_tray_icon", byref(value)):
                self.display_tray_icon = bool(value.value)
            if rime.config_get_bool(byref(config), b"style/candidate_use_cursor", byref(value)):
                self.candidate_use_cursor = bool(value.value)
            if rime.config_get_bool(byref(config), b"style/soft_cursor", byref(value)):
                self.soft_cursor = bool(value.value)

            self.menu = self._read_menu(config, b"menu")
        finally:
            rime.config_close(byref(config))

    def get_schema(self, command_id: int) -> bytes | None:
        idx = command_id - ID_SCHEMA
        if 0 <= idx < len(self.schemas):
            return self.schemas[idx]
        return None

    def get_option(self, command_id: int) -> bytes | None:
        idx = command_id - ID_OPTION
        if 0 <= idx < len(self.options):
            return self.options[idx]
        return None

    def get_uri(self, command_id: int) -> str | None:
        idx = command_id - ID_URI
        if 0 <= idx < len(self.uris):
            return self.uris[idx]
        return None

    def _convert_label(self, text: str) -> str:
        if self._menu_opencc is not None and text:
            try:
                return self._menu_opencc.convert(text)
            except Exception:
                return text
        return text

    def _get_schema_list(self) -> list[dict]:
        schema_list = RimeSchemaList()
        self.schemas = []
        submenu: list[dict] = []
        current_buf = ctypes.create_string_buffer(_CHAR_BUF)
        current_id = b""
        if self._rime.get_current_schema(self.session_id, current_buf, _CHAR_BUF):
            current_id = current_buf.value

        if self._rime.get_schema_list(byref(schema_list)):
            try:
                for i in range(schema_list.size):
                    schema_id = schema_list.list[i].schema_id
                    name = schema_list.list[i].name.decode("utf-8", errors="replace")
                    name = self._convert_label(name)
                    self.schemas.append(schema_id)
                    entry = {"text": name, "id": ID_SCHEMA + i}
                    if schema_id == current_id:
                        entry["checked"] = True
                    submenu.append(entry)
            finally:
                self._rime.free_schema_list(byref(schema_list))
        return submenu

    def _decorate_command_item(self, item: dict, cmd_id: int, label: str) -> str:
        if cmd_id == ID_GRAM_STATUS:
            suffix = "已启用" if self.gram_enabled else ("已停用" if self.gram_installed else "未安装")
            return f"{label}: {suffix}"
        if cmd_id == ID_TOGGLE_GRAM:
            if not self.gram_installed:
                item["enabled"] = False
                return f"{label}: 未安装"
            item["checked"] = bool(self.gram_enabled)
            return f"{label}: {'已开启' if self.gram_enabled else '已关闭'}"
        if cmd_id == ID_TOGGLE_LAYOUT:
            layout_label = "横条配列" if self.active_layout == "horizontal" else "竖排配列"
            return f"{label}: {layout_label}"
        if cmd_id == ID_SKIN_FROST_ROUND and self.active_skin == "frost_round":
            item["checked"] = True
        elif cmd_id == ID_SKIN_GEMINI_NEKO and self.active_skin == "gemini_neko":
            item["checked"] = True
        elif cmd_id == ID_SKIN_NATIVE and self.active_skin == "native":
            item["checked"] = True
        return label

    def _read_menu(self, config, path: bytes) -> list[dict]:
        menu: list[dict] = []
        iterator = RimeConfigIterator()
        if not self._rime.config_begin_list(byref(iterator), byref(config), path):
            return menu
        try:
            while self._rime.config_next(byref(iterator)):
                item: dict = {}
                base = iterator.path
                name_raw = self._rime.config_get_cstring(byref(config), base + b"/name")
                cmd_raw = self._rime.config_get_cstring(byref(config), base + b"/command")
                uri_raw = self._rime.config_get_cstring(byref(config), base + b"/uri")
                text_raw = self._rime.config_get_cstring(byref(config), base + b"/text")

                cmd_id = 0
                if cmd_raw:
                    cmd_name = cmd_raw.decode("utf-8", errors="replace")
                    cmd_id = _COMMANDS.get(cmd_name, 0)
                    item["id"] = cmd_id
                    if cmd_id == ID_SCHEMA_LIST:
                        item["submenu"] = self._get_schema_list()
                    elif cmd_id == ID_SYNC_DIR:
                        sync_raw = self._rime.get_sync_dir()
                        sync_dir = sync_raw.decode("utf-8", errors="replace") if sync_raw else ""
                        item["enabled"] = bool(sync_dir and os.path.isdir(sync_dir))
                elif uri_raw:
                    item["id"] = ID_URI + len(self.uris)
                    self.uris.append(uri_raw.decode("utf-8", errors="replace"))
                elif name_raw:
                    s0 = self._rime.config_get_cstring(byref(config), base + b"/states/@0")
                    s1 = self._rime.config_get_cstring(byref(config), base + b"/states/@1")
                    states = [
                        s0.decode("utf-8", errors="replace") if s0 else "关",
                        s1.decode("utf-8", errors="replace") if s1 else "开",
                    ]
                    item["id"] = ID_OPTION + len(self.options)
                    state_idx = 1 if self._rime.get_option(self.session_id, name_raw) else 0
                    item["text"] = "%s → %s" % (states[state_idx], states[1 - state_idx])
                    self.options.append(name_raw)

                if text_raw:
                    label = self._convert_label(text_raw.decode("utf-8", errors="replace"))
                    if cmd_id:
                        label = self._decorate_command_item(item, cmd_id, label)
                    item["text"] = label
                    submenu = self._read_menu(config, base + b"/submenu")
                    if submenu:
                        item["submenu"] = submenu
                elif "text" not in item:
                    item["text"] = ""

                menu.append(item)
        finally:
            self._rime.config_end(byref(iterator))
        return menu
