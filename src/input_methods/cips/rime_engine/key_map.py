"""Translate Windows/PIME KeyEvent objects into X11 keysyms and Rime masks."""

from __future__ import annotations

try:
    from keycodes import (
        VK_BACK,
        VK_CAPITAL,
        VK_CLEAR,
        VK_CONTROL,
        VK_DELETE,
        VK_DOWN,
        VK_END,
        VK_ESCAPE,
        VK_EXECUTE,
        VK_F1,
        VK_F24,
        VK_HELP,
        VK_HOME,
        VK_INSERT,
        VK_KANA,
        VK_KANJI,
        VK_LCONTROL,
        VK_LEFT,
        VK_LMENU,
        VK_LSHIFT,
        VK_LWIN,
        VK_MENU,
        VK_NEXT,
        VK_NUMLOCK,
        VK_NUMPAD0,
        VK_NUMPAD9,
        VK_MULTIPLY,
        VK_ADD,
        VK_SEPARATOR,
        VK_SUBTRACT,
        VK_DECIMAL,
        VK_DIVIDE,
        VK_PAUSE,
        VK_PRINT,
        VK_PRIOR,
        VK_RCONTROL,
        VK_RETURN,
        VK_RIGHT,
        VK_RMENU,
        VK_RSHIFT,
        VK_RWIN,
        VK_SCROLL,
        VK_SELECT,
        VK_SHIFT,
        VK_SPACE,
        VK_TAB,
        VK_UP,
    )
except ImportError:  # Fallback for standalone tests outside PIME
    VK_BACK = 0x08
    VK_TAB = 0x09
    VK_CLEAR = 0x0C
    VK_RETURN = 0x0D
    VK_SHIFT = 0x10
    VK_CONTROL = 0x11
    VK_MENU = 0x12
    VK_PAUSE = 0x13
    VK_CAPITAL = 0x14
    VK_KANA = 0x15
    VK_KANJI = 0x19
    VK_ESCAPE = 0x1B
    VK_SPACE = 0x20
    VK_PRIOR = 0x21
    VK_NEXT = 0x22
    VK_END = 0x23
    VK_HOME = 0x24
    VK_LEFT = 0x25
    VK_UP = 0x26
    VK_RIGHT = 0x27
    VK_DOWN = 0x28
    VK_SELECT = 0x29
    VK_PRINT = 0x2A
    VK_EXECUTE = 0x2B
    VK_INSERT = 0x2D
    VK_DELETE = 0x2E
    VK_HELP = 0x2F
    VK_LWIN = 0x5B
    VK_RWIN = 0x5C
    VK_NUMPAD0 = 0x60
    VK_NUMPAD9 = 0x69
    VK_MULTIPLY = 0x6A
    VK_ADD = 0x6B
    VK_SEPARATOR = 0x6C
    VK_SUBTRACT = 0x6D
    VK_DECIMAL = 0x6E
    VK_DIVIDE = 0x6F
    VK_F1 = 0x70
    VK_F24 = 0x87
    VK_NUMLOCK = 0x90
    VK_SCROLL = 0x91
    VK_LSHIFT = 0xA0
    VK_RSHIFT = 0xA1
    VK_LCONTROL = 0xA2
    VK_RCONTROL = 0xA3
    VK_LMENU = 0xA4
    VK_RMENU = 0xA5


VOID_SYMBOL = 0xFFFFFF

SHIFT_MASK = 1 << 0
LOCK_MASK = 1 << 1
CONTROL_MASK = 1 << 2
ALT_MASK = 1 << 3
RELEASE_MASK = 1 << 30

XK_SPACE = 0x020
XK_BACKSPACE = 0xFF08
XK_TAB = 0xFF09
XK_CLEAR = 0xFF0B
XK_RETURN = 0xFF0D
XK_PAUSE = 0xFF13
XK_SCROLL_LOCK = 0xFF14
XK_ESCAPE = 0xFF1B
XK_DELETE = 0xFFFF
XK_KANJI = 0xFF21
XK_HIRAGANA_KATAKANA = 0xFF27
XK_HOME = 0xFF50
XK_LEFT = 0xFF51
XK_UP = 0xFF52
XK_RIGHT = 0xFF53
XK_DOWN = 0xFF54
XK_PRIOR = 0xFF55
XK_NEXT = 0xFF56
XK_END = 0xFF57
XK_SELECT = 0xFF60
XK_PRINT = 0xFF61
XK_EXECUTE = 0xFF62
XK_INSERT = 0xFF63
XK_HELP = 0xFF6A
XK_NUM_LOCK = 0xFF7F

XK_KP_MULTIPLY = 0xFFAA
XK_KP_ADD = 0xFFAB
XK_KP_SEPARATOR = 0xFFAC
XK_KP_SUBTRACT = 0xFFAD
XK_KP_DECIMAL = 0xFFAE
XK_KP_DIVIDE = 0xFFAF
XK_KP_0 = 0xFFB0
XK_F1 = 0xFFBE

XK_SHIFT_L = 0xFFE1
XK_SHIFT_R = 0xFFE2
XK_CONTROL_L = 0xFFE3
XK_CONTROL_R = 0xFFE4
XK_CAPS_LOCK = 0xFFE5
XK_META_L = 0xFFE7
XK_META_R = 0xFFE8
XK_ALT_L = 0xFFE9
XK_ALT_R = 0xFFEA

VK_MAPS = {
    VK_BACK: XK_BACKSPACE,
    VK_TAB: XK_TAB,
    VK_CLEAR: XK_CLEAR,
    VK_RETURN: XK_RETURN,
    VK_MENU: XK_ALT_L,
    VK_PAUSE: XK_PAUSE,
    VK_CAPITAL: XK_CAPS_LOCK,
    VK_KANA: XK_HIRAGANA_KATAKANA,
    VK_KANJI: XK_KANJI,
    VK_ESCAPE: XK_ESCAPE,
    VK_SPACE: XK_SPACE,
    VK_PRIOR: XK_PRIOR,
    VK_NEXT: XK_NEXT,
    VK_END: XK_END,
    VK_HOME: XK_HOME,
    VK_LEFT: XK_LEFT,
    VK_UP: XK_UP,
    VK_RIGHT: XK_RIGHT,
    VK_DOWN: XK_DOWN,
    VK_SELECT: XK_SELECT,
    VK_PRINT: XK_PRINT,
    VK_EXECUTE: XK_EXECUTE,
    VK_INSERT: XK_INSERT,
    VK_DELETE: XK_DELETE,
    VK_HELP: XK_HELP,
    VK_LWIN: XK_META_L,
    VK_RWIN: XK_META_R,
    VK_MULTIPLY: XK_KP_MULTIPLY,
    VK_ADD: XK_KP_ADD,
    VK_SEPARATOR: XK_KP_SEPARATOR,
    VK_SUBTRACT: XK_KP_SUBTRACT,
    VK_DECIMAL: XK_KP_DECIMAL,
    VK_DIVIDE: XK_KP_DIVIDE,
    VK_NUMLOCK: XK_NUM_LOCK,
    VK_SCROLL: XK_SCROLL_LOCK,
    VK_LSHIFT: XK_SHIFT_L,
    VK_RSHIFT: XK_SHIFT_R,
    VK_LCONTROL: XK_CONTROL_L,
    VK_RCONTROL: XK_CONTROL_R,
    VK_LMENU: XK_ALT_L,
    VK_RMENU: XK_ALT_R,
}
for _i in range(10):
    VK_MAPS[VK_NUMPAD0 + _i] = XK_KP_0 + _i
for _i in range(24):
    VK_MAPS[VK_F1 + _i] = XK_F1 + _i


def _is_key_toggled(key_event, code: int) -> bool:
    if hasattr(key_event, "isKeyToggled"):
        return bool(key_event.isKeyToggled(code))
    states = getattr(key_event, "keyStates", None)
    if states and 0 <= code < len(states):
        return bool(states[code] & 0x01)
    return False


def _is_printable_char(key_event) -> bool:
    if hasattr(key_event, "isPrintableChar"):
        return bool(key_event.isPrintableChar())
    ch = getattr(key_event, "charCode", 0)
    return ch > 0x1F and ch != 0x7F


def translate_key_code(key_event) -> int:
    """Convert a PIME ``KeyEvent`` to a Rime/X11 keysym."""
    key_code = key_event.keyCode
    if key_code == VK_SHIFT:
        key_code = VK_RSHIFT if _is_key_toggled(key_event, VK_RSHIFT) else VK_LSHIFT
    elif key_code == VK_CONTROL:
        key_code = VK_RCONTROL if _is_key_toggled(key_event, VK_RCONTROL) else VK_LCONTROL

    if key_code in VK_MAPS:
        return VK_MAPS[key_code]
    if _is_printable_char(key_event):
        return int(key_event.charCode)
    return VOID_SYMBOL


def translate_modifiers(key_event, is_up: bool = False) -> int:
    """Convert a PIME ``KeyEvent`` modifier state into Rime's bitmask."""
    mask = 0
    key_code = key_event.keyCode
    if key_code != VK_SHIFT and key_event.isKeyDown(VK_SHIFT):
        mask |= SHIFT_MASK
    if _is_key_toggled(key_event, VK_CAPITAL):
        mask |= LOCK_MASK
    if key_code != VK_CONTROL and key_event.isKeyDown(VK_CONTROL):
        mask |= CONTROL_MASK
    if key_code != VK_MENU and key_event.isKeyDown(VK_MENU):
        mask |= ALT_MASK
    if is_up:
        mask |= RELEASE_MASK
    if key_code == VK_CAPITAL and not is_up:
        # Windows toggles the CapsLock bit before the keydown reaches the IME,
        # whereas Rime expects the pre-toggle state on keydown.
        mask ^= LOCK_MASK
    return mask
