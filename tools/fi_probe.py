#!/usr/bin/env python
"""fi_probe -- standalone smoke-test CLI for the cips win32 injection layer.

Runs without PIME: it loads ``src/input_methods/cips/win32/*.py`` directly by
path, so it can be used from a normal Python install (32-bit or 64-bit) to
check SendInput / clipboard / foreground-window detection before wiring the
same code into the PIME backend.

Usage examples
--------------
  # Where would text go right now?
  python tools/fi_probe.py --target

  # Do not touch the input queue, just show the plan:
  python tools/fi_probe.py --dry-run --delay 20 --text "你好，world"

  # Really type into the focused edit box (focus it first!):
  python tools/fi_probe.py --delay 20 --text "你好，世界 🌏"

  # Type whatever is currently on the clipboard:
  python tools/fi_probe.py --delay 20 --clipboard

  # Type the text, then read the target control back over WM_GETTEXT:
  python tools/fi_probe.py --delay 40 --text "123" --read-back

Notes
-----
* SendInput targets whichever window currently has keyboard focus; this tool
  never calls SetForegroundWindow, so focus the target first.
* ``--read-back`` only works for classic Win32 controls (EDIT/RICHEDIT and
  whatever forwards WM_GETTEXT). Browsers, WinUI/UWP apps and Electron
  webviews usually return "" even though the text really arrived; see
  ``win32/verify.py``.
* Exit codes: 0 ok, 1 runtime failure, 2 usage error, 3 UIPI blocked
  (ERROR_ACCESS_DENIED -- target runs at a higher integrity level).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_WIN32_DIR = os.path.join(_ROOT, "src", "input_methods", "cips", "win32")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_UIPI = 3


def _load_win32(name: str):
    """Load one win32 submodule by file path, independent of package imports."""
    path = os.path.join(_WIN32_DIR, name + ".py")
    if not os.path.isfile(path):
        sys.stderr.write("找不到模块 %s\n" % path)
        raise SystemExit(EXIT_USAGE)
    module_name = "cips_win32_probe_" + name
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _setup_stdout() -> None:
    """Use UTF-8 when redirected so Chinese/emoji in the report survive."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def _print_target(target, target_mod) -> None:
    if target is None:
        print("目标: <无前台窗口>")
        return
    print(
        "目标: hwnd=0x%08X pid=%d exe=%s class=%r title=%r"
        % (target.hwnd, target.pid, target.exe_name or "<unknown>", target.window_class, target.title)
    )
    elevated = target_mod.is_elevated_target(target)
    if elevated is None:
        verdict = "unknown"
    else:
        verdict = "True（可能更高权限，SendInput 可能被拒绝）" if elevated else "False（与当前进程同级或更低）"
    print("UIPI 风险（启发式，非最终判定）: elevated=%s" % verdict)


def _plan(text: str) -> tuple[int, int]:
    """Return (visible char count, INPUT group count) for a dry run.

    Mirrors sendinput.type_unicode's rules: CR is dropped, LF/TAB are one
    group, other C0 controls are dropped, other code points cost one group per
    UTF-16 code unit (so an emoji costs 2).
    """
    chars = 0
    groups = 0
    for ch in text:
        if ch == "\r":
            continue
        code_point = ord(ch)
        if code_point < 0x20 or code_point == 0x7F:
            if ch in ("\n", "\t"):
                chars += 1
                groups += 1
            continue
        chars += 1
        groups += len(ch.encode("utf-16-le", "surrogatepass")) // 2
    return chars, groups


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fi_probe.py",
        description="独立测试 Windows 输入注入（SendInput / 剪贴板 / 前台窗口识别），不依赖 PIME。",
        epilog=(
            "示例:\n"
            "  python tools/fi_probe.py --target\n"
            "  python tools/fi_probe.py --dry-run --delay 20 --text \"你好，world\"\n"
            "  python tools/fi_probe.py --delay 20 --text \"你好，世界 🌏\"\n"
            "  python tools/fi_probe.py --delay 20 --clipboard\n"
            "\n"
            "提示: 注入前请先让目标输入框获得键盘焦点；--delay 是每个字符之间的毫秒数。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", metavar="TEXT", help="要注入的文本")
    source.add_argument("--clipboard", action="store_true", help="注入当前剪贴板中的文本")
    parser.add_argument(
        "--target",
        action="store_true",
        help="只打印当前前台窗口信息（exe / class / title / pid）并退出",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要发送的字符数与目标信息，不真正注入",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=15,
        metavar="MS",
        help="字符之间的延迟，毫秒（默认 15）",
    )
    parser.add_argument(
        "--key-delay",
        type=int,
        default=0,
        metavar="MS",
        help="单个按键 down/up 之间的延迟，毫秒（默认 0）",
    )
    parser.add_argument(
        "--read-back",
        action="store_true",
        help="注入后用 WM_GETTEXT 读回目标控件文本（仅经典 Win32 控件有效，浏览器/Electron 通常读不到）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if os.name != "nt":
        print("错误: 该工具只能在 Windows 上运行。", file=sys.stderr)
        return EXIT_ERROR

    target_mod = _load_win32("target")
    target = target_mod.get_foreground_target()

    if args.target:
        _print_target(target, target_mod)
        return EXIT_OK

    if args.text is None and not args.clipboard:
        parser.error("需要 --text / --clipboard / --target 之一")

    if args.clipboard:
        clipboard_mod = _load_win32("clipboard")
        text = clipboard_mod.read_clipboard_text()
        if not text:
            print("错误: 剪贴板中没有可用的 Unicode 文本。", file=sys.stderr)
            return EXIT_ERROR
        source_desc = "剪贴板"
    else:
        text = args.text
        source_desc = "--text"

    if args.delay < 0 or args.key_delay < 0:
        parser.error("--delay / --key-delay 不能为负数")

    chars, groups = _plan(text)
    _print_target(target, target_mod)
    print(
        "输入: 来源=%s 字符=%d 预计按键组=%d delay=%dms key_delay=%dms"
        % (source_desc, chars, groups, args.delay, args.key_delay)
    )

    if args.dry_run:
        print("[dry-run] 未发送任何按键。")
        return EXIT_OK

    # Loaded lazily so --target / --dry-run work even if SendInput setup fails.
    sendinput = _load_win32("sendinput")
    try:
        sent = sendinput.type_unicode(
            text,
            char_delay_ms=args.delay,
            key_delay_ms=args.key_delay,
        )
    except sendinput.UipiBlocked as exc:
        print("失败 (UIPI): %s" % exc, file=sys.stderr)
        return EXIT_UIPI
    except sendinput.InjectError as exc:
        print("失败 (SendInput): %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130

    print("[ok] 已发送 %d 组按键（预计 %d 组）。" % (sent, groups))

    if args.read_back:
        # Read from whatever window is focused now: this is the control that
        # actually received the keystrokes. WM_GETTEXT is a no-op for most
        # modern controls, which the tool states explicitly instead of
        # pretending an empty result is a failure.
        verify = _load_win32("verify")
        hwnd = target_mod.get_foreground_hwnd()
        got = verify.read_window_text(hwnd) if hwnd else ""
        if got:
            print("[read-back] %r" % got)
        else:
            print("[read-back] 空: 目标不响应 WM_GETTEXT（浏览器/Electron/新版记事本等），或文本确实没进去")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
