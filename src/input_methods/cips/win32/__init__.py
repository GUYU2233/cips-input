"""Windows input-injection primitives for the cips backend.

Submodules
----------
* ``sendinput`` -- type text into the focused window (KEYEVENTF_UNICODE).
* ``clipboard`` -- read the payload and detect whether text is present.
* ``target``    -- snapshot of the foreground window / process.
* ``verify``    -- WM_GETTEXT read-back used by the standalone probe.

Plain ctypes only: this package must run inside PIME's bundled interpreter,
which has no third-party packages, and must work from both 32-bit and 64-bit
Python processes.
"""

from .clipboard import read_clipboard_text, sniff_clipboard_has_text
from .sendinput import InjectError, UipiBlocked, type_unicode
from .target import (  # noqa: F401
    ForegroundTarget,
    get_foreground_hwnd,
    get_foreground_target,
    is_elevated_target,
)
from .verify import read_window_text

__all__ = [
    # sendinput
    "InjectError",
    "UipiBlocked",
    "type_unicode",
    # clipboard
    "read_clipboard_text",
    "sniff_clipboard_has_text",
    # target
    "ForegroundTarget",
    "get_foreground_target",
    "get_foreground_hwnd",
    "is_elevated_target",
    # verify
    "read_window_text",
]
