"""Text normalization for the cips-input clipboard-to-paste pipeline.

Pure logic only: no I/O and no platform imports, so this module is importable
on any OS.

Deliberately **no** Unicode normalization (NFC/NFD/NFKC/NFKD) is applied here.
Normalizing would rewrite CJK compatibility forms and reorder or drop
combining marks, i.e. the target application would receive something different
from what the user copied.
"""

from __future__ import annotations

import re

__all__ = ["normalize_text", "strip_edges", "should_refuse", "MAX_TEXT_LENGTH"]

#: Upper bound accepted by :func:`should_refuse`; longer payloads are refused.
MAX_TEXT_LENGTH = 20000

# Zero-width and bidirectional formatting characters. They are invisible in the
# clipboard but corrupt caret movement and text runs once re-pasted by the TSF
# client, so they never reach the target application.
_FORMAT_CHARS = frozenset(
    [chr(cp) for cp in range(0x200B, 0x2010)]  # U+200B..U+200F
    + [chr(cp) for cp in range(0x202A, 0x202F)]  # U+202A..U+202E
    + ["\ufeff", "\u00ad"]  # BOM / zero width no-break space, soft hyphen
)

# C0 control characters that are stripped, keeping only TAB (U+0009) and
# LF (U+000A). DEL (U+007F) is removed as well.
_CONTROL_CHARS = frozenset(
    [chr(cp) for cp in range(0x00, 0x09)]  # U+0000..U+0008
    + ["\x0b", "\x0c"]  # VT, FF
    + [chr(cp) for cp in range(0x0E, 0x20)]  # U+000E..U+001F
    + ["\x7f"]
)

# str.translate() deletes every code point mapped to None.
_DROP_TABLE = {ord(char): None for char in _FORMAT_CHARS | _CONTROL_CHARS}

# CRLF and lone CR both become a single LF. Applied before the control-character
# pass; CR itself is never part of _CONTROL_CHARS.
_NEWLINE_RE = re.compile(r"\r\n?")


def normalize_text(raw: str) -> str:
    """Normalize a raw payload for insertion into a target application.

    Rules:

    * ``\\r\\n`` and a lone ``\\r`` are folded into ``\\n``.
    * Zero-width/bidirectional formatting code points are removed
      (U+200B..U+200F, U+202A..U+202E, U+FEFF, U+00AD).
    * C0 controls other than ``\\t`` and ``\\n`` are removed
      (U+0000..U+0008, U+000B, U+000C, U+000E..U+001F) plus U+007F.
    * Everything else is passed through byte-for-byte: no Unicode
      normalization, no case folding, and no trimming of leading/trailing
      whitespace (use :func:`strip_edges` if the caller wants that).

    The function is idempotent: ``normalize_text(normalize_text(s))`` equals
    ``normalize_text(s)``.
    """
    if not raw:
        return ""
    return _NEWLINE_RE.sub("\n", raw).translate(_DROP_TABLE)


def strip_edges(raw: str) -> str:
    """Strip leading/trailing whitespace, explicitly requested by the caller.

    Kept separate from :func:`normalize_text` so that whitespace the user
    really copied (leading indentation, a trailing newline) survives by
    default.
    """
    return raw.strip()


def should_refuse(text: str) -> str | None:
    """Return a refusal reason, or ``None`` when the payload may be committed.

    * ``"empty"`` for ``""``.
    * ``"too_long"`` when ``len(text) > 20000``.

    The length is measured on the string as given, as code points. Callers are
    expected to pass the output of :func:`normalize_text`, so a payload made
    only of zero-width characters collapses to ``""`` and is refused as
    ``"empty"``.
    """
    if not text:
        return "empty"
    if len(text) > MAX_TEXT_LENGTH:
        return "too_long"
    return None
