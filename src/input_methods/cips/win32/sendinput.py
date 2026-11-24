"""Low-level text injection through ``user32!SendInput``.

Purpose
-------
The PIME backend decides *what* text to deliver and to which window; this
module only knows how to actually type it, using the Win32 ``SendInput`` queue
with ``KEYEVENTF_UNICODE``. It knows nothing about PIME, composition state or
the target control.

Only the Python standard library is used (``ctypes`` plus ``time``/
``threading``), because PIME's bundled interpreter ships no third-party
packages.  The module works in both 32-bit and 64-bit Python processes.

Key design notes
----------------
* Unicode injection sets ``wScan`` to a single UTF-16 code unit and ``wVk`` to
  0 with ``KEYEVENTF_UNICODE``.  The character therefore does not depend on
  the active keyboard layout -- Chinese text, accented Latin, and emoji all
  arrive in the focused edit control.
* Characters outside the BMP (emoji, rare CJK) are a UTF-16 *surrogate pair*,
  i.e. two code units.  They are emitted as two consecutive down/up rounds,
  in order, with no delay in between; Windows assembles them back into one
  code point inside the target window.  Dropping or reordering one half would
  leave U+FFFD in the target.
* ``INPUT`` is 28 bytes on x86 and 40 bytes on x64, because the union member
  ``MOUSEINPUT.dwExtraInfo`` is a pointer-sized ``ULONG_PTR``.  ``ctypes``
  derives the layout from the declared C types, so the union must contain the
  widest member (``MOUSEINPUT``); declaring ``KEYBDINPUT`` alone would produce
  a 24/32-byte union, a wrong ``cbSize``, and ``SendInput`` would fail with
  ERROR_INVALID_PARAMETER.
* ``SendInput`` is subject to UIPI: an unelevated process cannot inject into a
  window owned by a higher-integrity process.  SendInput then returns 0 and
  ``GetLastError()`` is ERROR_ACCESS_DENIED (5); that is surfaced as
  :class:`UipiBlocked` rather than silently dropping the text.

Public API
----------
``InjectError``, ``UipiBlocked``, ``type_unicode(text, *, char_delay_ms=15,
key_delay_ms=0, stop_event=None) -> int``.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

__all__ = ["InjectError", "UipiBlocked", "type_unicode"]

user32 = ctypes.WinDLL("user32", use_last_error=True)

#: Pointer-sized unsigned integer.  Must NOT be c_int: dwExtraInfo has pointer
#: width, and getting it wrong shifts the whole INPUT struct on x64.
ULONG_PTR = ctypes.c_size_t

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_RETURN = 0x0D
VK_TAB = 0x09

ERROR_ACCESS_DENIED = 5


class InjectError(Exception):
    """A SendInput call failed (or was only partially accepted)."""


class UipiBlocked(InjectError):
    """The foreground window belongs to a higher-integrity process (UIPI)."""


# ---------------------------------------------------------------------------
# Win32 structures
# ---------------------------------------------------------------------------


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class _INPUTUNION(ctypes.Union):
    # The union must keep MOUSEINPUT: it is the widest member and therefore
    # defines sizeof(union) (32 bytes on x64, 24 bytes on x86).
    _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT))


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (
        ("type", wintypes.DWORD),
        ("u", _INPUTUNION),
    )


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def _expected_input_size() -> int:
    return 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28


# Fail loudly at import time instead of letting SendInput reject every call
# with ERROR_INVALID_PARAMETER: a wrong layout here is always a coding bug.
if ctypes.sizeof(INPUT) != _expected_input_size():
    raise RuntimeError(
        "INPUT struct size is %d, expected %d (ctypes layout mismatch)"
        % (ctypes.sizeof(INPUT), _expected_input_size())
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _key_event(w_vk: int = 0, w_scan: int = 0, flags: int = 0) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.ki.wVk = w_vk
    event.ki.wScan = w_scan
    event.ki.dwFlags = flags
    event.ki.time = 0
    event.ki.dwExtraInfo = 0
    return event


def _send(events: tuple[INPUT, ...], what: str) -> None:
    """Push one atomic batch of INPUT events and validate the result."""
    count = len(events)
    array = (INPUT * count)(*events)
    sent = user32.SendInput(count, array, ctypes.sizeof(INPUT))
    if sent == count:
        return

    # SendInput returns 0 when it was blocked; per MSDN the error must be read
    # immediately, before any other failing Win32 call clobbers it.
    err = ctypes.get_last_error()
    if sent == 0 and err == ERROR_ACCESS_DENIED:
        raise UipiBlocked(
            "SendInput 被 UIPI 拒绝（ERROR_ACCESS_DENIED）：前台窗口属于更高完整性"
            "级别的进程（通常是管理员进程或系统进程）。请用相同/更高权限运行输入"
            "法，或先切换到普通权限窗口。"
        )
    raise InjectError(
        "SendInput 未能发送 %s：请求 %d 个事件，实际接受 %d 个，GetLastError=%d"
        % (what, count, sent, err)
    )


def _utf16_units(text: str) -> list[int]:
    """UTF-16 code units of ``text``.

    Python's own UTF-16 encoder does the surrogate arithmetic for us; using it
    avoids hand-rolled bit math and guarantees non-BMP characters are split
    into exactly the high/low pair Windows expects.  ``surrogatepass`` keeps
    even a lone surrogate (possible in a raw IME buffer) transmittable.
    """
    raw = text.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(raw[i : i + 2], "little") for i in range(0, len(raw), 2)]


def _send_char_unit(unit: int, key_delay_ms: int) -> None:
    _send(
        (
            _key_event(w_scan=unit, flags=KEYEVENTF_UNICODE),
            _key_event(w_scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
        ),
        "U+%04X" % unit,
    )
    if key_delay_ms:
        time.sleep(key_delay_ms / 1000.0)


def _send_vk(vk: int, key_delay_ms: int) -> None:
    _send(
        (
            _key_event(w_vk=vk),
            _key_event(w_vk=vk, flags=KEYEVENTF_KEYUP),
        ),
        "VK 0x%02X" % vk,
    )
    if key_delay_ms:
        time.sleep(key_delay_ms / 1000.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def type_unicode(
    text: str,
    *,
    char_delay_ms: int = 15,
    key_delay_ms: int = 0,
    stop_event: threading.Event | None = None,
) -> int:
    """Type ``text`` into the focused window with SendInput + KEYEVENTF_UNICODE.

    Returns the number of down/up *groups* accepted by SendInput.  One BMP
    character is one group, one non-BMP character (emoji) is two groups (one
    per surrogate half), and '\\n'/'\t' are one group each.  Raises
    :class:`UipiBlocked` / :class:`InjectError` on the first rejected batch --
    text is never silently dropped mid-string.

    Control characters: '\\n' -> VK_RETURN, '\\t' -> VK_TAB, '\\r' is skipped
    (a CRLF pair therefore still produces exactly one newline), and every
    other C0 control / DEL is skipped because it cannot be expressed as a
    unicode character event.

    ``char_delay_ms`` is slept between characters; ``key_delay_ms`` between the
    key-down and key-up of one group.  ``stop_event`` (optional) is polled
    before each character so a caller in another thread can cancel mid-string;
    the count returned then reflects what was actually sent.
    """
    if not text:
        return 0

    char_delay = max(0, int(char_delay_ms)) / 1000.0
    key_delay = max(0, int(key_delay_ms))
    groups = 0

    for ch in text:
        if stop_event is not None and stop_event.is_set():
            break

        if ch == "\r":
            continue
        if ch == "\n":
            _send_vk(VK_RETURN, key_delay)
            groups += 1
        elif ch == "\t":
            _send_vk(VK_TAB, key_delay)
            groups += 1
        else:
            code_point = ord(ch)
            if code_point < 0x20 or code_point == 0x7F:
                # C0 controls / DEL have no unicode character representation in
                # the keyboard queue; skip instead of injecting garbage.
                continue
            for unit in _utf16_units(ch):
                _send_char_unit(unit, key_delay)
                groups += 1

        if char_delay:
            time.sleep(char_delay)

    return groups
