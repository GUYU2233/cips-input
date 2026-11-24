"""Per-application compatibility profiles.

A profile describes how the cips-input backend should inject a payload into a
particular target application: which strategy ``Ctrl+Alt+V`` uses (the human
rhythms, fixed-delay typing or the one-time-code layout) plus the tuning for
that strategy.

The only I/O in this module is :meth:`ProfileBook.from_json` /
:meth:`ProfileBook.to_json`; everything else is pure data handling.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .strategy import DISPATCH_MODES

__all__ = ["Profile", "ProfileBook", "DEFAULT_PROFILE"]

#: Characters that turn a pattern into a glob instead of a literal name.
_GLOB_CHARS = "*?["


@dataclass(frozen=True)
class Profile:
    """How to inject text into one family of applications.

    Attributes:
        name: Label used in logs/config; not used for matching.
        match_exe: Lower-case executable names. A plain name matches with or
            without the ``.exe`` suffix (``"chrome"`` matches ``chrome.exe``),
            and ``*``/``?`` globs are supported (``"chrome*"``). ``"*"``
            matches any executable.
        match_class: Window class names, compared case-insensitively. Globs are
            accepted as well (``"Chrome_WidgetWin_*"``).
        mode: Strategy for ``Ctrl+Alt+V``, one of
            :data:`core.strategy.DISPATCH_MODES`. Defaults to
            ``"human_normal"``, i.e. the standard human rhythm.
        char_delay_ms: Delay between characters when ``mode`` is ``"char"``
            (and inside a segment for ``"segmented"``).
        chunk_size: Code points per chunk in ``"segmented"`` mode.
        chunk_wait_ms: Wait after each chunk in ``"segmented"`` mode.
        per_box_wait_ms: Wait after each box in ``"otp"`` mode.
        trailing_tab: Append ``"\\t"`` to every OTP box.
    """

    name: str
    match_exe: tuple[str, ...] = ()
    match_class: tuple[str, ...] = ()
    mode: str = "human_normal"
    char_delay_ms: int = 60
    chunk_size: int = 64
    chunk_wait_ms: int = 30
    per_box_wait_ms: int = 60
    trailing_tab: bool = False

    def __post_init__(self) -> None:
        # Accept a single string for the pattern fields: iterating a bare
        # string would otherwise match one pattern per character.
        for field_name in ("match_exe", "match_class"):
            value = getattr(self, field_name)
            if isinstance(value, str):
                value = (value,)
            elif isinstance(value, list):
                value = tuple(value)
            object.__setattr__(self, field_name, value)

        # Clamp the timing fields so a hand-edited config cannot produce
        # negative sleeps or a zero-width segment.
        object.__setattr__(self, "char_delay_ms", max(0, int(self.char_delay_ms)))
        object.__setattr__(self, "chunk_size", max(1, int(self.chunk_size)))
        object.__setattr__(self, "chunk_wait_ms", max(0, int(self.chunk_wait_ms)))
        object.__setattr__(self, "per_box_wait_ms", max(0, int(self.per_box_wait_ms)))


DEFAULT_PROFILE = Profile(name="default")


def _as_int(value: Any, fallback: int) -> int:
    """Coerce a JSON number to ``int``; anything else falls back."""
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return fallback


def _as_bool(value: Any, fallback: bool) -> bool:
    """Accept real booleans only, so a JSON ``"false"`` cannot turn truthy."""
    return value if isinstance(value, bool) else fallback


def _as_patterns(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize a pattern field to a tuple of non-empty, stripped strings."""
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        return fallback
    patterns = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return tuple(patterns)


def _as_mode(value: Any, fallback: str) -> str:
    """Accept a dispatchable strategy name, else fall back to ``base.mode``."""
    return value if isinstance(value, str) and value in DISPATCH_MODES else fallback


def _profile_from_mapping(
    data: Mapping[str, Any],
    *,
    base: Profile = DEFAULT_PROFILE,
    fallback_name: str | None = None,
) -> Profile:
    """Build a :class:`Profile` from a decoded JSON object.

    Missing or malformed fields fall back to ``base``; unknown fields are
    ignored. This never raises, so one broken entry cannot poison the file.
    """
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        name = name.strip()
    elif fallback_name is not None:
        name = fallback_name
    else:
        name = base.name
    return Profile(
        name=name,
        match_exe=_as_patterns(data.get("match_exe"), base.match_exe),
        match_class=_as_patterns(data.get("match_class"), base.match_class),
        mode=_as_mode(data.get("mode"), base.mode),
        char_delay_ms=_as_int(data.get("char_delay_ms"), base.char_delay_ms),
        chunk_size=_as_int(data.get("chunk_size"), base.chunk_size),
        chunk_wait_ms=_as_int(data.get("chunk_wait_ms"), base.chunk_wait_ms),
        per_box_wait_ms=_as_int(data.get("per_box_wait_ms"), base.per_box_wait_ms),
        trailing_tab=_as_bool(data.get("trailing_tab"), base.trailing_tab),
    )


def _normalize_exe(exe: str | None) -> str:
    """Reduce an executable name/path to a lower-case bare file name."""
    if not exe:
        return ""
    name = str(exe).strip().strip('"').strip()
    if "\\" in name or "/" in name:
        # Callers may hand over a full image path; the profile only ever
        # declares file names.
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return name.lower()


def _normalize_class(window_class: str | None) -> str:
    return str(window_class).strip().lower() if window_class else ""


def _exe_matches(pattern: str, exe: str) -> bool:
    """Match a lower-case exe name against one (possibly glob) pattern."""
    pat = pattern.strip().lower()
    if not pat:
        return False
    if any(char in pat for char in _GLOB_CHARS):
        return fnmatch.fnmatchcase(exe, pat)
    if pat == exe:
        return True
    if "." not in pat:
        # Extension-less pattern: compare against the stem ("chrome" ~= chrome.exe).
        return exe.rsplit(".", 1)[0] == pat
    return False


def _class_matches(pattern: str, window_class: str) -> bool:
    """Match a lower-case window class against one (possibly glob) pattern."""
    pat = pattern.strip().lower()
    if not pat:
        return False
    if any(char in pat for char in _GLOB_CHARS):
        return fnmatch.fnmatchcase(window_class, pat)
    return pat == window_class


class ProfileBook:
    """Ordered list of profiles plus a fallback default.

    ``resolve`` picks the most specific match:

    1. a profile whose exe *and* window class both match
    2. a profile that only constrains the exe, and it matches
    3. a profile that only constrains the window class, and it matches
    4. :attr:`default`

    Ties inside one bucket go to the profile listed first.
    """

    def __init__(self, profiles: list[Profile], default: Profile = DEFAULT_PROFILE) -> None:
        self._profiles: tuple[Profile, ...] = tuple(profiles)
        self.default: Profile = default

    @property
    def profiles(self) -> tuple[Profile, ...]:
        """The matchable profiles, in priority (file) order."""
        return self._profiles

    def __len__(self) -> int:
        return len(self._profiles)

    def __repr__(self) -> str:
        return f"ProfileBook(profiles={len(self._profiles)}, default={self.default.name!r})"

    def resolve(self, exe: str | None = None, window_class: str | None = None) -> Profile:
        """Return the profile that should handle the focused window."""
        exe_name = _normalize_exe(exe)
        class_name = _normalize_class(window_class)

        both: list[Profile] = []
        exe_only: list[Profile] = []
        class_only: list[Profile] = []
        for profile in self._profiles:
            constrains_exe = bool(profile.match_exe)
            constrains_class = bool(profile.match_class)
            if not (constrains_exe or constrains_class):
                # An unconstrained entry would shadow everything; it can only
                # be reached through `default`.
                continue
            exe_ok = (
                constrains_exe
                and bool(exe_name)
                and any(_exe_matches(pattern, exe_name) for pattern in profile.match_exe)
            )
            class_ok = (
                constrains_class
                and bool(class_name)
                and any(_class_matches(pattern, class_name) for pattern in profile.match_class)
            )
            if constrains_exe and constrains_class:
                if exe_ok and class_ok:
                    both.append(profile)
            elif constrains_exe:
                if exe_ok:
                    exe_only.append(profile)
            elif class_ok:
                class_only.append(profile)

        for bucket in (both, exe_only, class_only):
            if bucket:
                return bucket[0]
        return self.default

    @classmethod
    def from_json(cls, path: str | Path) -> "ProfileBook":
        """Load a profile book from ``path``; never raises.

        The file looks like::

            {"default": {...}, "profiles": [{...}, ...]}

        Missing fields keep the :class:`Profile` defaults, unknown fields are
        ignored, and a missing/unreadable/malformed file yields a book that
        only knows :data:`DEFAULT_PROFILE`.
        """
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return cls([], DEFAULT_PROFILE)
        if not isinstance(data, dict):
            return cls([], DEFAULT_PROFILE)

        raw_default = data.get("default")
        default = (
            _profile_from_mapping(
                raw_default,
                base=DEFAULT_PROFILE,
                fallback_name=DEFAULT_PROFILE.name,
            )
            if isinstance(raw_default, dict)
            else DEFAULT_PROFILE
        )

        profiles: list[Profile] = []
        raw_profiles = data.get("profiles")
        if isinstance(raw_profiles, list):
            for index, item in enumerate(raw_profiles):
                if isinstance(item, dict):
                    profiles.append(_profile_from_mapping(item, fallback_name=f"profile_{index}"))
        return cls(profiles, default)

    def to_json(self, path: str | Path) -> None:
        """Write this book back out as JSON (handy for generating a template)."""
        payload = {
            "default": asdict(self.default),
            "profiles": [asdict(profile) for profile in self._profiles],
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
