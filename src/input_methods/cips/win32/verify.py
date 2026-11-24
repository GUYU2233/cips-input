"""Read back text from a window with WM_GETTEXT (best effort, diagnostics only).

This is a *verification helper* used by the standalone probe
(``tools/fi_probe.py --read-back``) to compare "what we sent" with "what the
target actually holds".  It is not part of the injection path.

Honest limitations
------------------
* ``WM_GETTEXT`` only returns text for windows that implement it: classic
  Win32 controls such as EDIT / RICHEDIT / ComboBox edit fields, and
  applications that forward the message.  It returns "" for browser
  contenteditable areas, Electron/CEF webviews, UWP/WinUI apps (including the
  Windows 11 Notepad), Windows Terminal, and most modern editors.
* The message only exposes the control's *current* text, not selection or
  composition state.  Cross-process calls can also time out (Windows silently
  turns a hung SendMessage into a failure after a few seconds).
* There is no UIA/COM path here on purpose: doing UI Automation from the
  standard library means hand-writing COM vtables, which is too much
  unverifiable code for v1.  TODO: add an optional UIA reader behind a
  ``comtypes`` import guard once the runtime can ship that dependency (PIME's
  bundled interpreter cannot).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

__all__ = ["read_window_text"]

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_GETTEXT = 0x000D

# The buffer pointer travels in LPARAM, which is pointer-sized; declaring it
# c_void_p and passing an explicit cast keeps this correct on x86 and x64.
user32.SendMessageW.argtypes = (
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    ctypes.c_void_p,
)
user32.SendMessageW.restype = ctypes.c_ssize_t  # LRESULT


def read_window_text(hwnd: int, max_chars: int = 4096) -> str:
    """Return up to ``max_chars`` characters of window text, or "" on failure.

    Works for standard Win32 controls (see the module docstring for the
    limitations); a browser or WinUI target will usually yield "" even though
    the injected text is really there.  Never raises: an invalid hwnd, a
    hung target, or a non-text window all degrade to "".
    """
    if not hwnd or max_chars <= 0:
        return ""

    buffer = ctypes.create_unicode_buffer(max_chars + 1)
    copied = user32.SendMessageW(
        hwnd,
        WM_GETTEXT,
        max_chars,
        ctypes.cast(buffer, ctypes.c_void_p),
    )
    if copied <= 0:
        return ""
    # SendMessageW returns the number of TCHARs copied (no terminating NUL).
    return buffer.value[: int(copied)]
