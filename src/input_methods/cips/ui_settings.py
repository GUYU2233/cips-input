"""Persistent user UI and model-toggle settings for cips-input.

Stored in ``%APPDATA%\\cips-input\\settings.json`` so toggling skins, candidate
layout, or whether to load the Wanxiang N-gram model never requires admin
permissions and survives module upgrades.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

SKINS = ("frost_round", "gemini_neko", "native")
LAYOUTS = ("horizontal", "vertical")

DEFAULT_SKIN = "frost_round"
DEFAULT_LAYOUT = "horizontal"
DEFAULT_FONT_NAME = "Microsoft YaHei UI"
DEFAULT_FONT_POINT = 12


def default_settings_path() -> str:
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "cips-input", "settings.json")


@dataclass(frozen=True)
class UiSettings:
    """Validated UI & runtime model preferences."""

    skin: str = DEFAULT_SKIN
    candidate_layout: str = DEFAULT_LAYOUT
    use_wanxiang_gram: bool = True
    font_name: str = DEFAULT_FONT_NAME
    font_point: int = DEFAULT_FONT_POINT

    @property
    def cand_per_row(self) -> int:
        return 8 if self.candidate_layout == "horizontal" else 1

    @classmethod
    def from_dict(cls, raw: dict | None) -> "UiSettings":
        if not isinstance(raw, dict):
            return cls()
        skin = raw.get("skin", DEFAULT_SKIN)
        if skin not in SKINS:
            skin = DEFAULT_SKIN
        layout = raw.get("candidate_layout", DEFAULT_LAYOUT)
        if layout not in LAYOUTS:
            layout = DEFAULT_LAYOUT
        use_gram = raw.get("use_wanxiang_gram", True)
        if not isinstance(use_gram, bool):
            use_gram = bool(use_gram)
        font_name = raw.get("font_name", DEFAULT_FONT_NAME)
        if not isinstance(font_name, str) or not font_name.strip():
            font_name = DEFAULT_FONT_NAME
        font_point = raw.get("font_point", DEFAULT_FONT_POINT)
        try:
            font_point = max(9, min(28, int(font_point)))
        except (TypeError, ValueError):
            font_point = DEFAULT_FONT_POINT
        return cls(
            skin=skin,
            candidate_layout=layout,
            use_wanxiang_gram=use_gram,
            font_name=font_name.strip(),
            font_point=font_point,
        )

    def to_dict(self) -> dict:
        return {
            "skin": self.skin,
            "candidate_layout": self.candidate_layout,
            "use_wanxiang_gram": self.use_wanxiang_gram,
            "font_name": self.font_name,
            "font_point": self.font_point,
        }


class UiSettingsStore:
    """Thread-safe/fault-tolerant JSON persistence for :class:`UiSettings`."""

    def __init__(self, path: str | None = None):
        self.path = path or default_settings_path()
        self._mtime: float | None = None
        self._settings = UiSettings()
        self.reload(force=True)

    @property
    def current(self) -> UiSettings:
        return self._settings

    def reload(self, force: bool = False) -> UiSettings:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            if force or self._mtime is not None:
                self._mtime = None
                self._settings = UiSettings()
            return self._settings
        if not force and mtime == self._mtime:
            return self._settings
        self._mtime = mtime
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            data = {}
        self._settings = UiSettings.from_dict(data)
        return self._settings

    def update(self, **changes) -> UiSettings:
        merged = self._settings.to_dict()
        merged.update(changes)
        updated = UiSettings.from_dict(merged)
        self._settings = updated
        self._save(updated)
        return updated

    def _save(self, settings: UiSettings) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(settings.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, self.path)
        try:
            self._mtime = os.path.getmtime(self.path)
        except OSError:
            self._mtime = None
