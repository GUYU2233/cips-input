"""Dispatch strategy: map a hotkey action to the concrete injection plan.

Pure logic only -- no Win32, no PIME and no file I/O -- so the mapping can be
unit tested on any platform. ``cips_ime`` is responsible for turning the
returned :class:`DispatchPlan` into planner calls.

The five hotkeys do not each own one fixed strategy. ``Ctrl+Alt+V`` ("auto")
asks the profile that matched the foreground application, which is what makes
``profiles.json`` meaningful for the default hotkey; the numbered chords force
one strategy regardless of the profile. ``Ctrl+Alt+Z`` (replay) is resolved by
the text service itself, which reuses the previous plan instead of going
through this module.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ACTIONS",
    "DISPATCH_MODES",
    "FAST_CHAR_DELAY_MS",
    "DispatchPlan",
    "resolve_dispatch",
]

#: Strategies a profile may select for ``Ctrl+Alt+V``.
#:
#: * ``human`` / ``human_normal`` -- one keystroke per character with the
#:   pauses of the matching top-level block in ``profiles.json``.
#: * ``char`` / ``segmented`` -- fixed-delay typing, using the profile's own
#:   ``char_delay_ms`` / ``chunk_size`` / ``chunk_wait_ms``.
#: * ``otp`` -- one keystroke per box, using ``per_box_wait_ms`` and
#:   ``trailing_tab``.
DISPATCH_MODES = ("human_normal", "human", "char", "segmented", "otp")

#: Fixed pace of ``Ctrl+Alt+2``. It is deliberately a constant: the chord
#: exists to type as fast as a target can take, not to follow per-app config.
FAST_CHAR_DELAY_MS = 75

#: Actions :func:`resolve_dispatch` understands. ``replay`` is handled by the
#: text service (it reuses the previous plan), so it is not listed here.
ACTIONS = ("auto", "human", "fast", "otp")


@dataclass(frozen=True)
class DispatchPlan:
    """How one dispatch should be injected.

    Attributes:
        mode: One of :data:`DISPATCH_MODES`.
        char_delay_ms: Delay between characters for ``char`` (and inside a
            segment for ``segmented``). The human modes read their pacing from
            ``profiles.json`` and ignore it, and so does ``otp``.
    """

    mode: str
    char_delay_ms: int = 0


def resolve_dispatch(
    action: str,
    profile_mode: str,
    profile_char_delay_ms: int,
) -> DispatchPlan:
    """Resolve ``action`` plus the matched profile into a :class:`DispatchPlan`.

    Args:
        action: One of :data:`ACTIONS`; ``"auto"`` defers to the profile.
        profile_mode: ``Profile.mode`` of the profile matched for the
            foreground window.
        profile_char_delay_ms: ``Profile.char_delay_ms`` of that profile.

    Raises:
        ValueError: for an unknown action.

    An unknown *profile* mode never raises: the profile parser validates the
    field already, and a hand-edited config typo must not break typing, so it
    degrades to ``human_normal`` (the default rhythm).
    """
    if action == "human":
        return DispatchPlan("human")
    if action == "fast":
        return DispatchPlan("char", FAST_CHAR_DELAY_MS)
    if action == "otp":
        return DispatchPlan("otp")
    if action != "auto":
        raise ValueError(f"unknown action {action!r}")

    mode = profile_mode if profile_mode in DISPATCH_MODES else "human_normal"
    if mode in ("char", "segmented"):
        return DispatchPlan(mode, max(0, int(profile_char_delay_ms)))
    return DispatchPlan(mode)
