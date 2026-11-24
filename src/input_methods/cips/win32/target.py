"""Identify the foreground window / process (ctypes + stdlib only).

The PIME backend wants to know *where* the composed text would go before it
sends anything: the executable name and window class are what ``profiles.json``
matches on, and the pid lets us guess whether UIPI will reject SendInput.

This module only observes; it never calls SetForegroundWindow and never
injects.  Everything is a snapshot -- a window can change between reading it
and sending keys.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

__all__ = ["ForegroundTarget", "get_foreground_target", "is_elevated_target"]

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20  # TOKEN_INFORMATION_CLASS value
MAX_PATH = 260
ERROR_ACCESS_DENIED = 5


class TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = (("TokenIsElevated", wintypes.DWORD),)


user32.GetForegroundWindow.argtypes = ()
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int

kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

advapi32.OpenProcessToken.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
)
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
advapi32.GetTokenInformation.restype = wintypes.BOOL


@dataclass
class ForegroundTarget:
    """Snapshot of the current foreground window."""

    hwnd: int
    pid: int
    exe_name: str  # bare file name, lower-case; "" when the process is unreadable
    window_class: str
    title: str


def _get_class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    length = user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value if length > 0 else ""


def _get_window_title(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    length = user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value if length > 0 else ""


def _get_exe_name(pid: int) -> str:
    """Bare lower-case image name of ``pid``, or "" when it cannot be read."""
    if pid <= 0:
        return ""
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return ""
    try:
        # 1024 chars covers long installed paths better than MAX_PATH.
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return ""
        return os.path.basename(buffer.value).lower()
    finally:
        kernel32.CloseHandle(process)


def get_foreground_target() -> ForegroundTarget | None:
    """Return the current foreground window, or None if there is none.

    exe_name comes from QueryFullProcessImageNameW and is reduced to a bare
    lower-case file name ("notepad.exe"); window_class from GetClassNameW.  A
    protected process yields exe_name == "" but the rest of the snapshot is
    still returned.
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None

    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    return ForegroundTarget(
        hwnd=int(hwnd),
        pid=int(pid.value),
        exe_name=_get_exe_name(pid.value),
        window_class=_get_class_name(hwnd),
        title=_get_window_title(hwnd),
    )


def get_foreground_hwnd() -> int:
    """Return the foreground window handle, or 0 if there is none.

    Cheap enough to call between injected characters: unlike
    :func:`get_foreground_target` it does not open the owning process.
    """
    return int(user32.GetForegroundWindow() or 0)


def is_elevated_target(target: ForegroundTarget | None) -> bool | None:
    """Heuristic: is ``target`` probably too privileged for our SendInput?

    Returns:
        True  -- definitely elevated (token says so), or OpenProcess was
                 denied (could be higher integrity *or* a protected process).
        False -- the process token exists and is not elevated.
        None  -- no target / unreadable pid / token query failed.

    This is a *heuristic*, not the UIPI verdict.  UIPI compares integrity
    levels of the two processes, so even a non-elevated target can refuse
    injection (e.g. AppContainer/UWP), and a process we cannot open may still
    accept input in some configurations.  The only authoritative answer is
    the result of the SendInput call itself (ERROR_ACCESS_DENIED -> blocked).
    """
    if target is None:
        return None
    pid = getattr(target, "pid", 0) or 0
    if pid <= 0:
        return None

    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return True  # access denied / protected process: assume higher privilege

    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            elevation = TOKEN_ELEVATION()
            returned = wintypes.DWORD(0)
            ok = advapi32.GetTokenInformation(
                token,
                TokenElevation,
                ctypes.byref(elevation),
                ctypes.sizeof(elevation),
                ctypes.byref(returned),
            )
            if not ok:
                return None
            return bool(elevation.TokenIsElevated)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(process)
