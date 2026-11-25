"""Skinned GDI+ layered candidate window for cips-input.

Renders modern rounded horizontal (or vertical) candidate cards using GDI+
per-pixel alpha (``UpdateLayeredWindow``) on a dedicated Per-Monitor-DPI-aware
GUI thread so PIME's synchronous 2-second RPC loop is never blocked.

Skins:
* ``frost_round`` -- clean translucent snow-frost rounded card with deep-blue
  pill selection highlight.
* ``gemini_neko`` -- starry midnight-indigo & lavender-pink gradient card with
  four-point Gemini sparkles, heterochromia gem accents, and the Gemini catgirl
  mascot badge.
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
from ctypes import wintypes
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Win32 / GDI+ constants
# ---------------------------------------------------------------------------

WS_POPUP = 0x80000000
WS_CLIPCHILDREN = 0x02000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080
SW_HIDE = 0

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

WM_DESTROY = 0x0002
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_APP = 0x8000
WM_CIPS_UPDATE = WM_APP + 10
WM_CIPS_HIDE = WM_APP + 11
WM_CIPS_QUIT = WM_APP + 12

OBJID_CARET = -8
MONITOR_DEFAULTTONEAREST = 2
COINIT_APARTMENTTHREADED = 2
CLSCTX_INPROC_SERVER = 1

UIA_TextPatternId = 10014
UIA_TextPattern2Id = 10024

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3

# GDI+ constants
UnitPixel = 2
FontStyleRegular = 0
FontStyleBold = 1
SmoothingModeAntiAlias = 4
TextRenderingHintAntiAlias = 4
InterpolationModeHighQualityBicubic = 7
PixelOffsetModeHalf = 4
LinearGradientModeHorizontal = 0
LinearGradientModeVertical = 1
LinearGradientModeForwardDiagonal = 2
WrapModeTileFlipXY = 3
StringAlignmentNear = 0
StringAlignmentCenter = 1
StringFormatFlagsNoWrap = 0x00001000
StringFormatFlagsMeasureTrailingSpaces = 0x00000800


# ---------------------------------------------------------------------------
# ctypes structs
# ---------------------------------------------------------------------------

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", ctypes.c_uint32),
        ("DebugEventCallback", ctypes.c_void_p),
        ("SuppressBackgroundThread", ctypes.c_int),
        ("SuppressExternalCodecs", ctypes.c_int),
    ]


class RectF(ctypes.Structure):
    _fields_ = [
        ("X", ctypes.c_float),
        ("Y", ctypes.c_float),
        ("Width", ctypes.c_float),
        ("Height", ctypes.c_float),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class VARIANT_I4(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_uint16),
        ("wReserved1", ctypes.c_uint16),
        ("wReserved2", ctypes.c_uint16),
        ("wReserved3", ctypes.c_uint16),
        ("lVal", ctypes.c_long),
        ("_pad", ctypes.c_ubyte * 4),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


@dataclass
class CandidateRenderState:
    """Snapshot passed from the IME backend to the candidate UI thread."""

    preedit: str = ""
    candidates: list[str] = field(default_factory=list)
    cursor_idx: int = 0
    select_keys: str = "12345678"
    skin: str = "frost_round"
    layout: str = "horizontal"
    font_name: str = "Microsoft YaHei UI"
    font_point: int = 12
    gram_enabled: bool = False
    visible: bool = False


@dataclass(frozen=True)
class SkinTheme:
    name: str
    bg_top: int
    bg_bottom: int
    border_start: int
    border_end: int
    inner_glow: int
    shadow_color: int
    divider: int
    preedit_color: int
    accent_primary: int
    accent_secondary: int
    sel_pill_start: int
    sel_pill_end: int
    sel_border: int
    sel_index_color: int
    sel_text_color: int
    sel_comment_color: int
    hover_pill: int
    index_color: int
    text_color: int
    comment_color: int
    corner_radius: float
    has_sparkles: bool = False
    has_mascot: bool = False


SKIN_THEMES = {
    "frost_round": SkinTheme(
        name="frost_round",
        bg_top=0xF7FCFEFF,
        bg_bottom=0xF3EEF4FB,
        border_start=0xE2C4D5EC,
        border_end=0xE2D6E2F2,
        inner_glow=0x90FFFFFF,
        shadow_color=0x280F172A,
        divider=0xB8DCE6F3,
        preedit_color=0xFF1E293B,
        accent_primary=0xFF205295,
        accent_secondary=0xFF3B82F6,
        sel_pill_start=0xFF205295,
        sel_pill_end=0xFF2D6CC0,
        sel_border=0x40FFFFFF,
        sel_index_color=0xFFDBEAFE,
        sel_text_color=0xFFFFFFFF,
        sel_comment_color=0xFFDBEAFE,
        hover_pill=0x1C205295,
        index_color=0xFF2563EB,
        text_color=0xFF0F172A,
        comment_color=0xFF64748B,
        corner_radius=12.0,
        has_sparkles=False,
        has_mascot=False,
    ),
    "gemini_neko": SkinTheme(
        name="gemini_neko",
        bg_top=0xF5181736,
        bg_bottom=0xF52A1F52,
        border_start=0xEE8E8AFF,
        border_end=0xEEEB9AFF,
        inner_glow=0x58F0CF85,
        shadow_color=0x4A0B0820,
        divider=0x608E8AFF,
        preedit_color=0xFFF1EDFF,
        accent_primary=0xFFEC4C78,   # Heterochromia ruby-rose eye
        accent_secondary=0xFFF5B83D,  # Heterochromia amber-gold eye
        sel_pill_start=0xFF5945C4,
        sel_pill_end=0xFFA15FE6,
        sel_border=0xCCF2CA7E,
        sel_index_color=0xFFFFD670,
        sel_text_color=0xFFFFFFFF,
        sel_comment_color=0xFFF2DEFF,
        hover_pill=0x2E9B7EFF,
        index_color=0xFFA9B2FF,
        text_color=0xFFF7F5FF,
        comment_color=0xFFC4B5FD,
        corner_radius=14.0,
        has_sparkles=True,
        has_mascot=True,
    ),
}


def _com_method(obj_ptr, index, restype, *argtypes):
    vtbl = ctypes.cast(
        obj_ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    fn_type = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return fn_type(vtbl[index])


class CaretLocator:
    """Best-effort screen-pixel caret locator (GUIThreadInfo -> MSAA -> UIA)."""

    def __init__(self):
        self._user32 = ctypes.windll.user32
        self._ole32 = ctypes.windll.ole32
        self._oleaut32 = ctypes.windll.oleaut32
        self._oleacc = ctypes.windll.oleacc
        self._uia = None
        self._last_caret_by_hwnd: dict[int, tuple[int, int, int]] = {}
        self._init_uia()

    def _init_uia(self) -> None:
        try:
            self._ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            clsid = GUID()
            iid = GUID()
            self._ole32.CLSIDFromString(
                ctypes.c_wchar_p("{FF48DBA4-60EF-4201-AA87-54103EEF594E}"),
                ctypes.byref(clsid),
            )
            self._ole32.CLSIDFromString(
                ctypes.c_wchar_p("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}"),
                ctypes.byref(iid),
            )
            uia = ctypes.c_void_p()
            hr = self._ole32.CoCreateInstance(
                ctypes.byref(clsid),
                None,
                CLSCTX_INPROC_SERVER,
                ctypes.byref(iid),
                ctypes.byref(uia),
            )
            if hr == 0 and uia.value:
                self._uia = uia
        except Exception:
            self._uia = None

    def close(self) -> None:
        if self._uia is not None and self._uia.value:
            try:
                _com_method(self._uia, 2, ctypes.c_ulong)(self._uia)
            except Exception:
                pass
            self._uia = None

    def locate(self) -> tuple[int, int, int]:
        """Return ``(x, bottom_y, line_height)`` in physical screen pixels."""
        fg = self._user32.GetForegroundWindow()
        if not fg:
            pt = POINT()
            self._user32.GetCursorPos(ctypes.byref(pt))
            return pt.x, pt.y + 20, 20

        tid = self._user32.GetWindowThreadProcessId(fg, None)
        gti = GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(gti)
        focus_hwnd = fg
        if tid and self._user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
            if gti.hwndFocus:
                focus_hwnd = gti.hwndFocus
            if gti.hwndCaret:
                rc = gti.rcCaret
                if rc.right > rc.left or rc.bottom > rc.top:
                    pt = POINT(rc.left, rc.bottom)
                    self._user32.ClientToScreen(gti.hwndCaret, ctypes.byref(pt))
                    lh = max(16, rc.bottom - rc.top)
                    self._last_caret_by_hwnd[fg] = (pt.x, pt.y, lh)
                    return pt.x, pt.y, lh

        msaa = self._caret_from_msaa(focus_hwnd)
        if msaa is not None:
            self._last_caret_by_hwnd[fg] = msaa
            return msaa

        uia = self._caret_from_uia()
        if uia is not None:
            self._last_caret_by_hwnd[fg] = uia
            return uia

        if fg in self._last_caret_by_hwnd:
            return self._last_caret_by_hwnd[fg]

        rc = wintypes.RECT()
        if self._user32.GetWindowRect(fg, ctypes.byref(rc)):
            x = rc.left + 48
            y = max(rc.top + 64, rc.bottom - 96)
            return x, y, 22
        return 120, 120, 22

    def _caret_from_msaa(self, hwnd: int) -> tuple[int, int, int] | None:
        if not hwnd:
            return None
        try:
            iid = GUID()
            self._ole32.CLSIDFromString(
                ctypes.c_wchar_p("{618736E0-3C3D-11CF-810C-00AA00389B71}"),
                ctypes.byref(iid),
            )
            acc = ctypes.c_void_p()
            hr = self._oleacc.AccessibleObjectFromWindow(
                hwnd, OBJID_CARET, ctypes.byref(iid), ctypes.byref(acc)
            )
            if hr != 0 or not acc.value:
                return None
            try:
                px, py, pw, ph = (
                    ctypes.c_long(),
                    ctypes.c_long(),
                    ctypes.c_long(),
                    ctypes.c_long(),
                )
                child = VARIANT_I4()
                child.vt = 3  # VT_I4
                child.lVal = 0
                # IAccessible::accLocation is vtable index 22
                hr2 = _com_method(
                    acc,
                    22,
                    ctypes.c_long,
                    ctypes.POINTER(ctypes.c_long),
                    ctypes.POINTER(ctypes.c_long),
                    ctypes.POINTER(ctypes.c_long),
                    ctypes.POINTER(ctypes.c_long),
                    VARIANT_I4,
                )(acc, ctypes.byref(px), ctypes.byref(py), ctypes.byref(pw), ctypes.byref(ph), child)
                if hr2 == 0 and (px.value != 0 or py.value != 0) and ph.value > 0:
                    return px.value, py.value + ph.value, max(16, ph.value)
            finally:
                _com_method(acc, 2, ctypes.c_ulong)(acc)
        except Exception:
            return None
        return None

    def _caret_from_uia(self) -> tuple[int, int, int] | None:
        if self._uia is None or not self._uia.value:
            return None
        elem = ctypes.c_void_p()
        try:
            hr = _com_method(
                self._uia, 8, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p)
            )(self._uia, ctypes.byref(elem))
            if hr != 0 or not elem.value:
                return None
            rng = ctypes.c_void_p()
            pat2 = ctypes.c_void_p()
            hr2 = _com_method(
                elem, 16, ctypes.c_long, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
            )(elem, UIA_TextPattern2Id, ctypes.byref(pat2))
            if hr2 == 0 and pat2.value:
                try:
                    is_active = ctypes.c_int()
                    hr3 = _com_method(
                        pat2,
                        10,
                        ctypes.c_long,
                        ctypes.POINTER(ctypes.c_int),
                        ctypes.POINTER(ctypes.c_void_p),
                    )(pat2, ctypes.byref(is_active), ctypes.byref(rng))
                    if hr3 != 0:
                        rng = ctypes.c_void_p()
                finally:
                    _com_method(pat2, 2, ctypes.c_ulong)(pat2)

            if not rng.value:
                pat = ctypes.c_void_p()
                hr4 = _com_method(
                    elem, 16, ctypes.c_long, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
                )(elem, UIA_TextPatternId, ctypes.byref(pat))
                if hr4 == 0 and pat.value:
                    try:
                        arr = ctypes.c_void_p()
                        hr5 = _com_method(
                            pat, 5, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p)
                        )(pat, ctypes.byref(arr))
                        if hr5 == 0 and arr.value:
                            try:
                                cnt = ctypes.c_int()
                                _com_method(
                                    arr, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_int)
                                )(arr, ctypes.byref(cnt))
                                if cnt.value > 0:
                                    _com_method(
                                        arr,
                                        4,
                                        ctypes.c_long,
                                        ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_void_p),
                                    )(arr, 0, ctypes.byref(rng))
                            finally:
                                _com_method(arr, 2, ctypes.c_ulong)(arr)
                    finally:
                        _com_method(pat, 2, ctypes.c_ulong)(pat)

            if not rng.value:
                return None
            try:
                sa = ctypes.c_void_p()
                hr6 = _com_method(
                    rng, 10, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p)
                )(rng, ctypes.byref(sa))
                if hr6 != 0 or not sa.value:
                    return None
                try:
                    lb, ub = ctypes.c_long(), ctypes.c_long()
                    self._oleaut32.SafeArrayGetLBound(sa, 1, ctypes.byref(lb))
                    self._oleaut32.SafeArrayGetUBound(sa, 1, ctypes.byref(ub))
                    if ub.value - lb.value + 1 >= 4:
                        ptr = ctypes.POINTER(ctypes.c_double)()
                        self._oleaut32.SafeArrayAccessData(sa, ctypes.byref(ptr))
                        try:
                            left, top, _w, h = ptr[0], ptr[1], ptr[2], ptr[3]
                        finally:
                            self._oleaut32.SafeArrayUnaccessData(sa)
                        if h > 0:
                            return int(round(left)), int(round(top + h)), max(16, int(round(h)))
                finally:
                    self._oleaut32.SafeArrayDestroy(sa)
            finally:
                _com_method(rng, 2, ctypes.c_ulong)(rng)
        except Exception:
            return None
        finally:
            if elem.value:
                try:
                    _com_method(elem, 2, ctypes.c_ulong)(elem)
                except Exception:
                    pass
        return None


def _split_candidate(raw: str) -> tuple[str, str]:
    """Split ``word comment`` (if any) cleanly while keeping single words intact."""
    if " " not in raw:
        return raw, ""
    word, comment = raw.split(" ", 1)
    return word, comment


class GdiplusCandidateRenderer:
    """Measure and paint rounded candidate cards onto 32bpp premultiplied ARGB bitmaps."""

    def __init__(self, mascot_path: str | None = None):
        self._gdiplus = ctypes.windll.gdiplus
        self._ole32 = ctypes.windll.ole32
        self._token = ctypes.c_size_t()
        inp = GdiplusStartupInput(1, None, 0, 0)
        st = self._gdiplus.GdiplusStartup(ctypes.byref(self._token), ctypes.byref(inp), None)
        if st != 0:
            raise RuntimeError(f"GdiplusStartup failed with status {st}")

        self._mascot = None
        self._mascot_w = 0
        self._mascot_h = 0
        if mascot_path and os.path.isfile(mascot_path):
            bmp = ctypes.c_void_p()
            if self._gdiplus.GdipCreateBitmapFromFile(ctypes.c_wchar_p(mascot_path), ctypes.byref(bmp)) == 0 and bmp.value:
                w, h = ctypes.c_uint32(), ctypes.c_uint32()
                self._gdiplus.GdipGetImageWidth(bmp, ctypes.byref(w))
                self._gdiplus.GdipGetImageHeight(bmp, ctypes.byref(h))
                self._mascot = bmp
                self._mascot_w = w.value
                self._mascot_h = h.value

        self._str_fmt = ctypes.c_void_p()
        self._gdiplus.GdipStringFormatGetGenericTypographic(ctypes.byref(self._str_fmt))
        flags = ctypes.c_int()
        self._gdiplus.GdipGetStringFormatFlags(self._str_fmt, ctypes.byref(flags))
        self._gdiplus.GdipSetStringFormatFlags(
            self._str_fmt, flags.value | StringFormatFlagsNoWrap | StringFormatFlagsMeasureTrailingSpaces
        )

    def close(self) -> None:
        if self._str_fmt and self._str_fmt.value:
            self._gdiplus.GdipDeleteStringFormat(self._str_fmt)
            self._str_fmt = None
        if self._mascot and self._mascot.value:
            self._gdiplus.GdipDisposeImage(self._mascot)
            self._mascot = None
        if self._token and self._token.value:
            self._gdiplus.GdiplusShutdown(self._token)
            self._token = ctypes.c_size_t()

    def _create_font(self, name: str, px_size: float, bold: bool = False):
        family = ctypes.c_void_p()
        if self._gdiplus.GdipCreateFontFamilyFromName(ctypes.c_wchar_p(name), None, ctypes.byref(family)) != 0:
            self._gdiplus.GdipCreateFontFamilyFromName(ctypes.c_wchar_p("Microsoft YaHei"), None, ctypes.byref(family))
        font = ctypes.c_void_p()
        style = FontStyleBold if bold else FontStyleRegular
        self._gdiplus.GdipCreateFont(
            family, ctypes.c_float(px_size), style, UnitPixel, ctypes.byref(font)
        )
        self._gdiplus.GdipDeleteFontFamily(family)
        return font

    def _measure(self, gfx, text: str, font) -> tuple[float, float]:
        if not text:
            return 0.0, 0.0
        layout = RectF(0.0, 0.0, 4000.0, 400.0)
        box = RectF()
        self._gdiplus.GdipMeasureString(
            gfx,
            ctypes.c_wchar_p(text),
            len(text),
            font,
            ctypes.byref(layout),
            self._str_fmt,
            ctypes.byref(box),
            None,
            None,
        )
        return float(box.Width), float(box.Height)

    def _draw_text(self, gfx, text: str, font, color: int, x: float, y: float) -> None:
        if not text:
            return
        brush = ctypes.c_void_p()
        self._gdiplus.GdipCreateSolidFill(ctypes.c_uint32(color), ctypes.byref(brush))
        rc = RectF(x, y, 4000.0, 400.0)
        self._gdiplus.GdipDrawString(
            gfx, ctypes.c_wchar_p(text), len(text), font, ctypes.byref(rc), self._str_fmt, brush
        )
        self._gdiplus.GdipDeleteBrush(brush)

    def _rounded_rect_path(self, x: float, y: float, w: float, h: float, r: float):
        path = ctypes.c_void_p()
        self._gdiplus.GdipCreatePath(0, ctypes.byref(path))
        d = max(2.0, min(r * 2.0, w, h))
        self._gdiplus.GdipAddPathArc(path, ctypes.c_float(x), ctypes.c_float(y), ctypes.c_float(d), ctypes.c_float(d), ctypes.c_float(180), ctypes.c_float(90))
        self._gdiplus.GdipAddPathArc(path, ctypes.c_float(x + w - d), ctypes.c_float(y), ctypes.c_float(d), ctypes.c_float(d), ctypes.c_float(270), ctypes.c_float(90))
        self._gdiplus.GdipAddPathArc(path, ctypes.c_float(x + w - d), ctypes.c_float(y + h - d), ctypes.c_float(d), ctypes.c_float(d), ctypes.c_float(0), ctypes.c_float(90))
        self._gdiplus.GdipAddPathArc(path, ctypes.c_float(x), ctypes.c_float(y + h - d), ctypes.c_float(d), ctypes.c_float(d), ctypes.c_float(90), ctypes.c_float(90))
        self._gdiplus.GdipClosePathFigure(path)
        return path

    def _draw_sparkle(self, gfx, cx: float, cy: float, radius: float, c1: int, c2: int) -> None:
        """Draw a crisp four-pointed Gemini star sparkle centered at ``(cx, cy)``."""
        path = ctypes.c_void_p()
        self._gdiplus.GdipCreatePath(0, ctypes.byref(path))
        inner = radius * 0.22
        pts = [
            (cx, cy - radius),
            (cx + inner, cy - inner),
            (cx + radius, cy),
            (cx + inner, cy + inner),
            (cx, cy + radius),
            (cx - inner, cy + inner),
            (cx - radius, cy),
            (cx - inner, cy - inner),
        ]
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            self._gdiplus.GdipAddPathLine(
                path, ctypes.c_float(x1), ctypes.c_float(y1), ctypes.c_float(x2), ctypes.c_float(y2)
            )
        self._gdiplus.GdipClosePathFigure(path)
        rc = RectF(cx - radius, cy - radius, radius * 2.0, radius * 2.0)
        brush = ctypes.c_void_p()
        self._gdiplus.GdipCreateLineBrushFromRect(
            ctypes.byref(rc),
            ctypes.c_uint32(c1),
            ctypes.c_uint32(c2),
            LinearGradientModeForwardDiagonal,
            WrapModeTileFlipXY,
            ctypes.byref(brush),
        )
        self._gdiplus.GdipFillPath(gfx, brush, path)
        self._gdiplus.GdipDeleteBrush(brush)
        self._gdiplus.GdipDeletePath(path)

    def render_bitmap(
        self,
        state: CandidateRenderState,
        dpi_scale: float = 1.0,
        hover_index: int = -1,
    ) -> tuple[ctypes.c_void_p, int, int, int, list[tuple[int, int, int, int, int]]]:
        """Render ``state`` into a GDI+ ARGB Bitmap.

        Returns ``(gdip_bitmap, width, height, card_top_offset, hit_rects)``
        where ``hit_rects`` is ``[(index, left, top, right, bottom), ...]``.
        """
        theme = SKIN_THEMES.get(state.skin, SKIN_THEMES["frost_round"])
        s = max(1.0, dpi_scale)

        font_px = max(12.0, state.font_point * (96.0 / 72.0) * s)
        preedit_px = max(11.0, font_px * 0.88)
        idx_px = max(10.5, font_px * 0.84)
        cmt_px = max(10.0, font_px * 0.78)

        font_main = self._create_font(state.font_name, font_px, bold=False)
        font_bold = self._create_font(state.font_name, font_px, bold=True)
        font_pre = self._create_font(state.font_name, preedit_px, bold=False)
        font_idx = self._create_font(state.font_name, idx_px, bold=True)
        font_cmt = self._create_font(state.font_name, cmt_px, bold=False)

        scratch = ctypes.c_void_p()
        self._gdiplus.GdipCreateBitmapFromScan0(16, 16, 0, 0x26200A, None, ctypes.byref(scratch))
        m_gfx = ctypes.c_void_p()
        self._gdiplus.GdipGetImageGraphicsContext(scratch, ctypes.byref(m_gfx))
        self._gdiplus.GdipSetTextRenderingHint(m_gfx, TextRenderingHintAntiAlias)

        try:
            pad_x = 12.0 * s
            pad_y = 9.0 * s
            pill_pad_x = 8.0 * s
            pill_pad_y = 4.0 * s
            item_gap_x = 6.0 * s
            item_gap_y = 4.0 * s
            shadow_pad = 10.0 * s
            mascot_size = round(68.0 * s) if (theme.has_mascot and self._mascot is not None) else 0
            mascot_overhang = round(14.0 * s) if mascot_size else 0
            mascot_reserve_w = round(66.0 * s) if mascot_size else 0

            preedit_text = state.preedit.strip()
            pre_w, pre_h = self._measure(m_gfx, preedit_text, font_pre) if preedit_text else (0.0, max(16.0 * s, preedit_px * 1.25))
            if preedit_text:
                pre_w += 22.0 * s  # icon/accent space

            items = []
            for i, raw in enumerate(state.candidates):
                key = state.select_keys[i] if i < len(state.select_keys) else str((i + 1) % 10)
                word, comment = _split_candidate(raw)
                idx_str = f"{key}."
                iw, ih = self._measure(m_gfx, idx_str, font_idx)
                ww, wh = self._measure(m_gfx, word, font_bold if i == state.cursor_idx else font_main)
                cw, ch = self._measure(m_gfx, comment, font_cmt) if comment else (0.0, 0.0)
                text_h = max(ih, wh, ch, font_px * 1.25)
                inner_w = iw + 4.0 * s + ww + ((4.0 * s + cw) if comment else 0.0)
                pill_w = inner_w + pill_pad_x * 2
                pill_h = text_h + pill_pad_y * 2
                items.append((i, idx_str, word, comment, iw, ww, cw, pill_w, pill_h))

            if state.layout == "horizontal":
                row_w = sum(it[7] for it in items) + max(0, len(items) - 1) * item_gap_x
                row_h = max((it[8] for it in items), default=24.0 * s)
                content_w = max(pre_w, row_w)
                cand_area_h = row_h
            else:
                row_w = max((it[7] for it in items), default=120.0 * s)
                content_w = max(pre_w, row_w, 130.0 * s)
                cand_area_h = sum(it[8] for it in items) + max(0, len(items) - 1) * item_gap_y

            header_h = (pre_h + 7.0 * s) if preedit_text else 0.0
            card_w = round(content_w + pad_x * 2 + mascot_reserve_w)
            card_h = round(pad_y * 2 + header_h + cand_area_h)
            if mascot_size:
                card_h = max(card_h, round(mascot_size - mascot_overhang + 10.0 * s))

            card_x = round(shadow_pad)
            card_y = round(max(shadow_pad, mascot_overhang + 4.0 * s))
            total_w = int(card_w + shadow_pad * 2)
            total_h = int(card_y + card_h + shadow_pad)

            # Create target bitmap
            bmp = ctypes.c_void_p()
            self._gdiplus.GdipCreateBitmapFromScan0(total_w, total_h, 0, 0x26200A, None, ctypes.byref(bmp))
            gfx = ctypes.c_void_p()
            self._gdiplus.GdipGetImageGraphicsContext(bmp, ctypes.byref(gfx))
            self._gdiplus.GdipSetSmoothingMode(gfx, SmoothingModeAntiAlias)
            self._gdiplus.GdipSetTextRenderingHint(gfx, TextRenderingHintAntiAlias)
            self._gdiplus.GdipSetInterpolationMode(gfx, InterpolationModeHighQualityBicubic)
            self._gdiplus.GdipSetPixelOffsetMode(gfx, PixelOffsetModeHalf)
            self._gdiplus.GdipGraphicsClear(gfx, 0x00000000)

            r = theme.corner_radius * s
            # Soft layered drop shadow
            for step, alpha_mul in ((3, 0.32), (2, 0.52), (1, 0.8)):
                off = step * 1.5 * s
                sh_color = (int(((theme.shadow_color >> 24) & 0xFF) * alpha_mul) << 24) | (theme.shadow_color & 0x00FFFFFF)
                sh_path = self._rounded_rect_path(card_x - off * 0.25, card_y + off, card_w + off * 0.5, card_h + off * 0.4, r + off * 0.2)
                sh_brush = ctypes.c_void_p()
                self._gdiplus.GdipCreateSolidFill(ctypes.c_uint32(sh_color), ctypes.byref(sh_brush))
                self._gdiplus.GdipFillPath(gfx, sh_brush, sh_path)
                self._gdiplus.GdipDeleteBrush(sh_brush)
                self._gdiplus.GdipDeletePath(sh_path)

            # Card fill
            card_path = self._rounded_rect_path(card_x, card_y, card_w, card_h, r)
            card_rc = RectF(float(card_x), float(card_y), float(card_w), float(card_h))
            bg_brush = ctypes.c_void_p()
            self._gdiplus.GdipCreateLineBrushFromRect(
                ctypes.byref(card_rc),
                ctypes.c_uint32(theme.bg_top),
                ctypes.c_uint32(theme.bg_bottom),
                LinearGradientModeVertical,
                WrapModeTileFlipXY,
                ctypes.byref(bg_brush),
            )
            self._gdiplus.GdipFillPath(gfx, bg_brush, card_path)
            self._gdiplus.GdipDeleteBrush(bg_brush)

            # Gradient border
            border_brush = ctypes.c_void_p()
            self._gdiplus.GdipCreateLineBrushFromRect(
                ctypes.byref(card_rc),
                ctypes.c_uint32(theme.border_start),
                ctypes.c_uint32(theme.border_end),
                LinearGradientModeForwardDiagonal,
                WrapModeTileFlipXY,
                ctypes.byref(border_brush),
            )
            border_pen = ctypes.c_void_p()
            self._gdiplus.GdipCreatePen2(border_brush, ctypes.c_float(1.4 * s), UnitPixel, ctypes.byref(border_pen))
            self._gdiplus.GdipDrawPath(gfx, border_pen, card_path)
            self._gdiplus.GdipDeletePen(border_pen)
            self._gdiplus.GdipDeleteBrush(border_brush)
            self._gdiplus.GdipDeletePath(card_path)

            # Inner highlight rim
            inner_path = self._rounded_rect_path(card_x + 1.5 * s, card_y + 1.5 * s, card_w - 3.0 * s, card_h - 3.0 * s, max(2.0, r - 1.5 * s))
            inner_pen = ctypes.c_void_p()
            self._gdiplus.GdipCreatePen1(ctypes.c_uint32(theme.inner_glow), ctypes.c_float(1.0 * s), UnitPixel, ctypes.byref(inner_pen))
            self._gdiplus.GdipDrawPath(gfx, inner_pen, inner_path)
            self._gdiplus.GdipDeletePen(inner_pen)
            self._gdiplus.GdipDeletePath(inner_path)

            # Preedit header
            cur_y = card_y + pad_y
            if preedit_text:
                if theme.has_sparkles:
                    self._draw_sparkle(
                        gfx,
                        card_x + pad_x + 5.5 * s,
                        cur_y + pre_h * 0.5,
                        5.2 * s,
                        0xFF8BE9FD,
                        0xFFF5C451,
                    )
                    # Heterochromia micro-gems (ruby red + amber gold)
                    for idx_dot, dot_col in ((0, theme.accent_primary), (1, theme.accent_secondary)):
                        db = ctypes.c_void_p()
                        self._gdiplus.GdipCreateSolidFill(ctypes.c_uint32(dot_col), ctypes.byref(db))
                        dx = card_x + pad_x + 14.0 * s + idx_dot * 5.0 * s
                        dy = cur_y + pre_h * 0.5 - 1.8 * s
                        self._gdiplus.GdipFillEllipse(gfx, db, ctypes.c_float(dx), ctypes.c_float(dy), ctypes.c_float(3.6 * s), ctypes.c_float(3.6 * s))
                        self._gdiplus.GdipDeleteBrush(db)
                    self._draw_text(gfx, preedit_text, font_pre, theme.preedit_color, card_x + pad_x + 25.0 * s, cur_y)
                else:
                    bar_path = self._rounded_rect_path(card_x + pad_x, cur_y + pre_h * 0.16, 3.2 * s, pre_h * 0.68, 1.6 * s)
                    bar_brush = ctypes.c_void_p()
                    self._gdiplus.GdipCreateSolidFill(ctypes.c_uint32(theme.accent_primary), ctypes.byref(bar_brush))
                    self._gdiplus.GdipFillPath(gfx, bar_brush, bar_path)
                    self._gdiplus.GdipDeleteBrush(bar_brush)
                    self._gdiplus.GdipDeletePath(bar_path)
                    self._draw_text(gfx, preedit_text, font_pre, theme.preedit_color, card_x + pad_x + 9.0 * s, cur_y)

                div_y = cur_y + pre_h + 3.5 * s
                div_pen = ctypes.c_void_p()
                self._gdiplus.GdipCreatePen1(ctypes.c_uint32(theme.divider), ctypes.c_float(1.0 * s), UnitPixel, ctypes.byref(div_pen))
                self._gdiplus.GdipDrawLine(
                    gfx,
                    div_pen,
                    ctypes.c_float(card_x + pad_x),
                    ctypes.c_float(div_y),
                    ctypes.c_float(card_x + card_w - pad_x - mascot_reserve_w * 0.85),
                    ctypes.c_float(div_y),
                )
                self._gdiplus.GdipDeletePen(div_pen)
                cur_y += header_h

            # Candidates
            hit_rects: list[tuple[int, int, int, int, int]] = []
            cx = card_x + pad_x
            cy = cur_y
            for i, idx_str, word, comment, iw, ww, _cw, pill_w, pill_h in items:
                is_sel = i == state.cursor_idx
                is_hover = (i == hover_index) and not is_sel
                item_w = pill_w if state.layout == "horizontal" else max(pill_w, content_w)
                px = cx
                py = cy

                if is_sel:
                    pill_path = self._rounded_rect_path(px, py, item_w, pill_h, 7.5 * s)
                    pill_rc = RectF(px, py, item_w, pill_h)
                    p_brush = ctypes.c_void_p()
                    self._gdiplus.GdipCreateLineBrushFromRect(
                        ctypes.byref(pill_rc),
                        ctypes.c_uint32(theme.sel_pill_start),
                        ctypes.c_uint32(theme.sel_pill_end),
                        LinearGradientModeHorizontal,
                        WrapModeTileFlipXY,
                        ctypes.byref(p_brush),
                    )
                    self._gdiplus.GdipFillPath(gfx, p_brush, pill_path)
                    self._gdiplus.GdipDeleteBrush(p_brush)
                    if theme.sel_border:
                        sp = ctypes.c_void_p()
                        self._gdiplus.GdipCreatePen1(ctypes.c_uint32(theme.sel_border), ctypes.c_float(1.0 * s), UnitPixel, ctypes.byref(sp))
                        self._gdiplus.GdipDrawPath(gfx, sp, pill_path)
                        self._gdiplus.GdipDeletePen(sp)
                    self._gdiplus.GdipDeletePath(pill_path)
                elif is_hover:
                    pill_path = self._rounded_rect_path(px, py, item_w, pill_h, 7.5 * s)
                    hb = ctypes.c_void_p()
                    self._gdiplus.GdipCreateSolidFill(ctypes.c_uint32(theme.hover_pill), ctypes.byref(hb))
                    self._gdiplus.GdipFillPath(gfx, hb, pill_path)
                    self._gdiplus.GdipDeleteBrush(hb)
                    self._gdiplus.GdipDeletePath(pill_path)

                tx = px + pill_pad_x
                ty_word = py + pill_pad_y
                ty_idx = py + pill_pad_y + (font_px - idx_px) * 0.45
                ty_cmt = py + pill_pad_y + (font_px - cmt_px) * 0.45

                self._draw_text(
                    gfx,
                    idx_str,
                    font_idx,
                    theme.sel_index_color if is_sel else theme.index_color,
                    tx,
                    ty_idx,
                )
                self._draw_text(
                    gfx,
                    word,
                    font_bold if is_sel else font_main,
                    theme.sel_text_color if is_sel else theme.text_color,
                    tx + iw + 4.0 * s,
                    ty_word,
                )
                if comment:
                    self._draw_text(
                        gfx,
                        comment,
                        font_cmt,
                        theme.sel_comment_color if is_sel else theme.comment_color,
                        tx + iw + 4.0 * s + ww + 4.0 * s,
                        ty_cmt,
                    )

                hit_rects.append((i, int(px), int(py), int(px + item_w), int(py + pill_h)))
                if state.layout == "horizontal":
                    cx += item_w + item_gap_x
                else:
                    cy += pill_h + item_gap_y

            # Gemini mascot & corner starlight decorations
            if theme.has_sparkles:
                self._draw_sparkle(
                    gfx,
                    card_x + card_w - (mascot_reserve_w + 8.0 * s if mascot_size else 14.0 * s),
                    card_y + 10.0 * s,
                    4.2 * s,
                    0xFFD8B4FE,
                    0xFFFDE68A,
                )
            if mascot_size and self._mascot is not None:
                draw_size = min(mascot_size, int(card_h + mascot_overhang - 2.0 * s))
                mx = int(card_x + card_w - draw_size - 2.0 * s)
                my = int(card_y + card_h - draw_size - 2.0 * s)
                self._gdiplus.GdipDrawImageRectI(gfx, self._mascot, mx, my, draw_size, draw_size)

            self._gdiplus.GdipDeleteGraphics(gfx)
            return bmp, total_w, total_h, card_y, hit_rects
        finally:
            self._gdiplus.GdipDeleteGraphics(m_gfx)
            self._gdiplus.GdipDisposeImage(scratch)
            for f in (font_main, font_bold, font_pre, font_idx, font_cmt):
                self._gdiplus.GdipDeleteFont(f)

    def render_to_png(self, state: CandidateRenderState, out_path: str, dpi_scale: float = 1.0) -> None:
        """Render ``state`` directly to a PNG file (for previews and tests)."""
        bmp, _w, _h, _cy, _hits = self.render_bitmap(state, dpi_scale=dpi_scale)
        try:
            png_clsid = GUID()
            self._ole32.CLSIDFromString(
                ctypes.c_wchar_p("{557CF406-1A04-11D3-9A73-0000F81EF32E}"),
                ctypes.byref(png_clsid),
            )
            st = self._gdiplus.GdipSaveImageToFile(
                bmp, ctypes.c_wchar_p(out_path), ctypes.byref(png_clsid), None
            )
            if st != 0:
                raise RuntimeError(f"GdipSaveImageToFile failed with status {st}")
        finally:
            self._gdiplus.GdipDisposeImage(bmp)


def _send_selection_digit(key_char: str) -> None:
    """Inject a single Unicode keystroke so TSF/Rime commits the clicked candidate."""
    try:
        from .sendinput import type_unicode

        type_unicode(key_char, char_delay_ms=0)
    except Exception:
        pass


class CandidateWindowManager:
    """Thread-safe controller owning a dedicated GDI+ layered candidate window."""

    def __init__(self, mascot_path: str | None = None, log_fn=None):
        self._mascot_path = mascot_path
        self._log = log_fn or (lambda _msg: None)
        self._lock = threading.Lock()
        self._latest = CandidateRenderState()
        self._hwnd = 0
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._hit_rects: list[tuple[int, int, int, int, int]] = []
        self._hover_idx = -1
        self._last_pos: tuple[int, int] | None = None

    def start(self) -> None:
        with self._lock:
            if self._closed or self._thread is not None:
                return
            t = threading.Thread(target=self._ui_thread, name="cips-cand-ui", daemon=True)
            self._thread = t
            t.start()

    def show(self, state: CandidateRenderState) -> bool:
        if self._closed:
            return False
        self.start()
        if not self._ready.is_set():
            self._ready.wait(0.35)
        with self._lock:
            self._latest = state
            hwnd = self._hwnd
        if not hwnd:
            return False
        return bool(ctypes.windll.user32.PostMessageW(hwnd, WM_CIPS_UPDATE, 0, 0))

    def hide(self) -> None:
        with self._lock:
            self._latest = CandidateRenderState(visible=False)
            self._hover_idx = -1
            hwnd = self._hwnd
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, WM_CIPS_HIDE, 0, 0)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            hwnd = self._hwnd
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, WM_CIPS_QUIT, 0, 0)

    def _ui_thread(self) -> None:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        kernel32 = ctypes.windll.kernel32

        try:
            if hasattr(user32, "SetThreadDpiAwarenessContext"):
                user32.SetThreadDpiAwarenessContext(
                    ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
                )
        except Exception:
            pass

        user32.DefWindowProcW.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        user32.DefWindowProcW.restype = ctypes.c_long

        renderer = None
        locator = None
        try:
            renderer = GdiplusCandidateRenderer(mascot_path=self._mascot_path)
            locator = CaretLocator()
            hinst = kernel32.GetModuleHandleW(None)
            cls_name = f"CipsSkinnedCandWindow_{threading.get_ident()}"

            def _wnd_proc(hwnd, msg, wp, lp):
                try:
                    if msg == WM_CIPS_UPDATE:
                        self._paint_and_position(hwnd, renderer, locator)
                        return 0
                    if msg == WM_CIPS_HIDE:
                        user32.ShowWindow(hwnd, SW_HIDE)
                        self._hit_rects = []
                        self._hover_idx = -1
                        return 0
                    if msg == WM_MOUSEMOVE:
                        mx = ctypes.c_int16(lp & 0xFFFF).value
                        my = ctypes.c_int16((lp >> 16) & 0xFFFF).value
                        hit = self._hit_test(mx, my)
                        if hit != self._hover_idx:
                            self._hover_idx = hit
                            self._paint_and_position(hwnd, renderer, locator, reuse_pos=True)
                        return 0
                    if msg == WM_LBUTTONDOWN:
                        mx = ctypes.c_int16(lp & 0xFFFF).value
                        my = ctypes.c_int16((lp >> 16) & 0xFFFF).value
                        hit = self._hit_test(mx, my)
                        if hit >= 0:
                            with self._lock:
                                keys = self._latest.select_keys
                            if hit < len(keys):
                                threading.Thread(
                                    target=_send_selection_digit,
                                    args=(keys[hit],),
                                    name="cips-cand-click",
                                    daemon=True,
                                ).start()
                        return 0
                    if msg == WM_CIPS_QUIT:
                        user32.DestroyWindow(hwnd)
                        return 0
                    if msg == WM_DESTROY:
                        user32.PostQuitMessage(0)
                        return 0
                except Exception as exc:
                    self._log("candidate UI wndproc error: %r" % (exc,))
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wp, lp)

            proc_cb = WNDPROC(_wnd_proc)
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(wc)
            wc.lpfnWndProc = proc_cb
            wc.hInstance = hinst
            wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
            wc.lpszClassName = cls_name
            if not user32.RegisterClassExW(ctypes.byref(wc)):
                self._log("candidate UI RegisterClassExW failed: err=%d" % kernel32.GetLastError())

            ex_style = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
            hwnd = user32.CreateWindowExW(
                ex_style,
                cls_name,
                "",
                WS_POPUP | WS_CLIPCHILDREN,
                0,
                0,
                1,
                1,
                None,
                None,
                hinst,
                None,
            )
            if not hwnd:
                self._log("candidate UI CreateWindowExW failed: err=%d" % kernel32.GetLastError())
            else:
                desk_name = "?"
                try:
                    hdesk = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
                    dbuf = ctypes.create_unicode_buffer(128)
                    needed = wintypes.DWORD()
                    if user32.GetUserObjectInformationW(hdesk, 2, dbuf, 256, ctypes.byref(needed)):
                        desk_name = dbuf.value
                except Exception:
                    pass
                self._log("candidate UI ready hwnd=0x%X desktop=%s" % (hwnd, desk_name))
            with self._lock:
                self._hwnd = hwnd
                pending = self._latest.visible
            self._ready.set()
            if pending and hwnd:
                user32.PostMessageW(hwnd, WM_CIPS_UPDATE, 0, 0)

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self._log("candidate UI thread error: %r" % (exc,))
        finally:
            self._ready.set()
            if locator is not None:
                locator.close()
            if renderer is not None:
                renderer.close()
            with self._lock:
                self._hwnd = 0

    def _hit_test(self, mx: int, my: int) -> int:
        for idx, left, top, right, bottom in self._hit_rects:
            if left <= mx <= right and top <= my <= bottom:
                return idx
        return -1

    def _paint_and_position(
        self,
        hwnd: int,
        renderer: GdiplusCandidateRenderer,
        locator: CaretLocator,
        reuse_pos: bool = False,
    ) -> None:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        with self._lock:
            state = self._latest
            hover = self._hover_idx

        if not state.visible or not state.candidates:
            user32.ShowWindow(hwnd, SW_HIDE)
            return

        if reuse_pos and self._last_pos is not None:
            cx, cy, line_h = self._last_pos[0], self._last_pos[1], 20
        else:
            cx, cy, line_h = locator.locate()
            self._last_pos = (cx, cy, line_h)

        dpi_scale = 1.0
        try:
            pt = POINT(cx, cy)
            hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
            if hmon and hasattr(ctypes.windll, "shcore"):
                dx, dy = ctypes.c_uint(), ctypes.c_uint()
                if ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0 and dx.value:
                    dpi_scale = dx.value / 96.0
        except Exception:
            dpi_scale = 1.0

        gdip_bmp, w, h, card_y, hit_rects = renderer.render_bitmap(
            state, dpi_scale=dpi_scale, hover_index=hover
        )
        try:
            self._hit_rects = hit_rects
            screen_dc = user32.GetDC(None)
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(bmi)
            bmi.biWidth = w
            bmi.biHeight = -h  # top-down DIB
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bits = ctypes.c_void_p()
            dib = gdi32.CreateDIBSection(mem_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            old_bmp = gdi32.SelectObject(mem_dc, dib)

            gfx = ctypes.c_void_p()
            renderer._gdiplus.GdipCreateFromHDC(mem_dc, ctypes.byref(gfx))
            renderer._gdiplus.GdipDrawImageRectI(gfx, gdip_bmp, 0, 0, w, h)
            renderer._gdiplus.GdipDeleteGraphics(gfx)

            # Clamp position to active monitor work area
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(mi)
            hmon = user32.MonitorFromPoint(POINT(cx, cy), MONITOR_DEFAULTTONEAREST)
            if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                work = mi.rcWork
            else:
                work = wintypes.RECT(0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))

            pos_x = cx - round(10 * dpi_scale)
            pos_y = cy + round(4 * dpi_scale) - card_y
            if pos_x + w > work.right:
                pos_x = max(work.left, work.right - w)
            if pos_x < work.left:
                pos_x = work.left
            if pos_y + h > work.bottom:
                # Flip above the caret line
                pos_y = max(work.top, cy - line_h - h + round(6 * dpi_scale))
            if pos_y < work.top:
                pos_y = work.top

            pt_dst = POINT(pos_x, pos_y)
            pt_src = POINT(0, 0)
            sz = SIZE(w, h)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            user32.UpdateLayeredWindow(
                hwnd,
                screen_dc,
                ctypes.byref(pt_dst),
                ctypes.byref(sz),
                mem_dc,
                ctypes.byref(pt_src),
                0,
                ctypes.byref(blend),
                ULW_ALPHA,
            )
            user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                pos_x,
                pos_y,
                w,
                h,
                SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOSIZE | SWP_NOMOVE,
            )

            gdi32.SelectObject(mem_dc, old_bmp)
            gdi32.DeleteObject(dib)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
        finally:
            renderer._gdiplus.GdipDisposeImage(gdip_bmp)
