"""Chunk planning: how a payload is turned into typed keystrokes.

Pure logic only. A :class:`Chunk` pairs a piece of text with the pause that
must follow it before the next piece is sent; the Win32 side turns that into
``SendInput`` calls plus sleeps.

All slicing is done on ``str``, so splits always happen on Unicode code points
(never on UTF-16 code units): a non-BMP character such as an emoji stays in one
piece.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

__all__ = [
    "CHUNK_MODES",
    "Chunk",
    "HUMAN_DEFAULTS",
    "plan_chunks",
    "plan_human_chunks",
    "plan_otp_chunks",
]

#: Fixed-pace strategies: one code point at a time, or fixed-length segments.
#: (The former whole-string ``bulk`` mode is gone: a preserved key never owns
#: an edit session, so a ``setCommitString`` from it is silently dropped. See
#: the module docstring of ``cips_ime``.)
CHUNK_MODES = ("char", "segmented")


@dataclass(frozen=True)
class Chunk:
    """One commit unit.

    ``wait_ms`` is how long to sleep *after this chunk was sent*, before the
    next chunk is dispatched. Milliseconds are advisory: the caller owns the
    actual waiting.
    """

    text: str
    wait_ms: int


def plan_chunks(
    text: str,
    *,
    mode: str = "char",
    char_delay_ms: int = 15,
    chunk_size: int = 64,
    chunk_wait_ms: int = 20,
) -> list[Chunk]:
    """Split ``text`` into typing chunks according to ``mode``.

    * ``"char"`` -- one chunk per Unicode code point, ``wait_ms=char_delay_ms``.
      A newline is a chunk of its own, like any other character.
    * ``"segmented"`` -- chunks of at most ``chunk_size`` code points,
      ``wait_ms=chunk_wait_ms``.

    An empty string yields ``[]`` for every mode.

    Raises:
        ValueError: for an unknown ``mode``, or for a non-positive
            ``chunk_size`` when ``mode="segmented"``.
    """
    if mode not in CHUNK_MODES:
        raise ValueError(f"mode must be one of {CHUNK_MODES}, got {mode!r}")
    if mode == "segmented" and chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size!r}")
    if not text:
        return []

    if mode == "char":
        return [Chunk(text=char, wait_ms=char_delay_ms) for char in text]
    return [
        Chunk(text=text[start : start + chunk_size], wait_ms=chunk_wait_ms)
        for start in range(0, len(text), chunk_size)
    ]


#: Timing knobs for the human-like planner. Every value is a millisecond
#: figure except ``hesitation_chance``, which is a probability per character.
HUMAN_DEFAULTS = {
    "base_delay_ms": 255,
    "jitter_ms": 135,
    "space_pause_ms": 210,
    "newline_pause_ms": 570,
    "punctuation_pause_ms": 180,
    "hesitation_chance": 0.03,
    "hesitation_min_ms": 450,
    "hesitation_max_ms": 1350,
}

#: Characters that a typist usually pauses after.
_PUNCTUATION = ",.;:!?、，。；：！？…—"


def normalize_human_settings(settings: object = None) -> dict:
    """Merge a user supplied mapping over :data:`HUMAN_DEFAULTS`.

    Anything unusable (wrong type, unknown key, nonsense value) is ignored
    rather than raising: a bad ``profiles.json`` must never break typing.
    """
    merged = dict(HUMAN_DEFAULTS)
    if not isinstance(settings, dict):
        return merged
    for key, default in HUMAN_DEFAULTS.items():
        if key not in settings:
            continue
        try:
            value = float(settings[key])
        except (TypeError, ValueError):
            continue
        merged[key] = value if isinstance(default, float) else int(value)
    for key in HUMAN_DEFAULTS:
        if key == "hesitation_chance":
            merged[key] = min(1.0, max(0.0, merged[key]))
        else:
            merged[key] = max(0, merged[key])
    if merged["hesitation_min_ms"] > merged["hesitation_max_ms"]:
        merged["hesitation_min_ms"], merged["hesitation_max_ms"] = (
            merged["hesitation_max_ms"],
            merged["hesitation_min_ms"],
        )
    return merged


def plan_human_chunks(
    text: str,
    *,
    settings: object = None,
    rng: random.Random | None = None,
) -> list[Chunk]:
    """Plan commits that imitate a person typing.

    One chunk per code point, but the pause after each one is drawn from a
    uniform window around ``base_delay_ms`` (so the rhythm never repeats), plus
    extra pauses after a space, a line break and sentence punctuation, plus a
    rare longer hesitation. Pass a seeded ``rng`` to make the result
    reproducible in tests; ``settings`` is a mapping merged over
    :data:`HUMAN_DEFAULTS`.
    """
    if not text:
        return []
    config = normalize_human_settings(settings)
    generator = rng if rng is not None else random.Random()
    chunks = []
    for char in text:
        delay = config["base_delay_ms"] + generator.uniform(
            -config["jitter_ms"], config["jitter_ms"]
        )
        if char == " ":
            delay += config["space_pause_ms"]
        elif char in "\r\n":
            delay += config["newline_pause_ms"]
        elif char in _PUNCTUATION:
            delay += config["punctuation_pause_ms"]
        if config["hesitation_chance"] > 0 and (
            generator.random() < config["hesitation_chance"]
        ):
            delay += generator.uniform(
                config["hesitation_min_ms"], config["hesitation_max_ms"]
            )
        chunks.append(Chunk(text=char, wait_ms=max(0, int(round(delay)))))
    return chunks


def plan_otp_chunks(
    text: str,
    *,
    per_box_wait_ms: int = 40,
    trailing_tab: bool = False,
) -> list[Chunk]:
    """Plan commits for a one-time-password widget with ``maxlength=1`` boxes.

    Each code point is committed on its own and followed by
    ``per_box_wait_ms`` so the web page can move focus to the next box. When
    ``trailing_tab`` is true every chunk gets a trailing ``"\\t"``, which some
    widgets require before they move focus at all.

    The caller is responsible for sanitizing the payload first (whitespace and
    separators are *not* removed here); an empty string yields ``[]``.
    """
    if not text:
        return []
    suffix = "\t" if trailing_tab else ""
    return [Chunk(text=char + suffix, wait_ms=per_box_wait_ms) for char in text]
