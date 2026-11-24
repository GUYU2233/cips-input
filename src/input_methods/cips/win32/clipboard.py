"""Minimal clipboard helpers (CF_UNICODETEXT only, ctypes/stdlib only).

The backend reads the payload from the clipboard when a hotkey fires, and asks
two questions: "is there text at all?" and "what is it?". ``sniff_clipboard_
has_text`` answers the first even when the text is empty, which is what lets the
diagnostics distinguish "clipboard is empty" from "clipboard holds an empty
string". Writing the clipboard is deliberately not implemented: cips-input never
modifies what the user copied.

Known limitations
-----------------
* Only the Windows clipboard (CF_UNICODETEXT). Non-text formats are ignored.
* Opening the clipboard is a machine-wide lock: if another process holds it we
  retry a few times (a few tens of milliseconds) and then give up with an
  empty result / ``False`` instead of blocking a PIME callback thread.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

__all__ = [
    "read_clipboard_text",
    "sniff_clipboard_has_text",
]

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT = 13

# OpenClipboard / GetClipboardData use HANDLE (= void*), so declare
# pointer-sized restypes; the default c_int would truncate the handle in a
# 64-bit process.
user32.OpenClipboard.argtypes = (wintypes.HWND,)
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = ()
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = (wintypes.UINT,)
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL

kernel32.GlobalLock.argtypes = (wintypes.HANDLE,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (wintypes.HANDLE,)
kernel32.GlobalUnlock.restype = wintypes.BOOL


def _open_clipboard(retries: int = 5, delay_s: float = 0.02) -> bool:
    """Open the clipboard, retrying briefly if another process holds it.

    OpenClipboard fails with ERROR_ACCESS_DENIED when a different window has
    the clipboard open. PIME callbacks must never hang, so this is a bounded
    retry rather than an unbounded wait.
    """
    for attempt in range(max(1, retries)):
        if user32.OpenClipboard(None):
            return True
        if attempt + 1 < retries:
            time.sleep(delay_s)
    return False


def read_clipboard_text() -> str:
    """Return the clipboard's CF_UNICODETEXT content, or "" if unavailable.

    Every early-return path still unlocks the global memory block and closes
    the clipboard (see the nested try/finally), so a failure here cannot leave
    the clipboard locked for the rest of the desktop.
    """
    if not _open_clipboard():
        return ""

    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        locked = kernel32.GlobalLock(handle)
        if not locked:
            return ""
        try:
            # Copy the string into Python memory while the block is locked;
            # the clipboard handle stays owned by the system.
            return ctypes.wstring_at(locked)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def sniff_clipboard_has_text() -> bool:
    """True if the clipboard currently offers CF_UNICODETEXT."""
    if not _open_clipboard():
        return False
    try:
        return bool(user32.IsClipboardFormatAvailable(CF_UNICODETEXT))
    finally:
        user32.CloseClipboard()
