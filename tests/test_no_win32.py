"""Guard tests: the core layer must stay importable on every platform.

The backend around it may talk to Win32/PIME, but ``core`` must not, otherwise
the logic cannot be tested (or reused) anywhere else.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CIPS_DIR = REPO_ROOT / "src" / "input_methods" / "cips"
CORE_DIR = CIPS_DIR / "core"

if str(CIPS_DIR) not in sys.path:
    sys.path.insert(0, str(CIPS_DIR))

#: Modules that would make the core layer platform-dependent.
FORBIDDEN_MODULES = (
    "_ctypes",
    "_winapi",
    "ctypes",
    "msvcrt",
    "pime",
    "pywintypes",
    "pythoncom",
    "win32",
    "win32api",
    "win32clipboard",
    "win32con",
    "win32gui",
    "win32process",
    "win32ui",
    "winreg",
    "winsound",
)


def _imported_roots(tree: ast.AST) -> set[str]:
    """Top-level module names imported by a parsed module."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import inside the package: fine
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


class CoreSourceIsPlatformNeutralTests(unittest.TestCase):
    def test_core_modules_avoid_forbidden_imports(self):
        modules = sorted(CORE_DIR.glob("*.py"))
        self.assertTrue(modules, f"no core modules found under {CORE_DIR}")
        for path in modules:
            with self.subTest(module=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                forbidden = _imported_roots(tree) & set(FORBIDDEN_MODULES)
                self.assertEqual(
                    forbidden,
                    set(),
                    f"{path.name} imports platform-specific modules {sorted(forbidden)}",
                )

    def test_core_imports_cleanly_in_a_fresh_interpreter(self):
        # Measure the delta: CPython on Windows already preloads a couple of
        # platform modules (_winapi, winreg) at startup, those are not our
        # doing. Importing ``core`` must not add a single one of them.
        code = (
            "import sys;"
            f"sys.path.insert(0, {str(CIPS_DIR)!r});"
            "before = set(sys.modules);"
            "import core;"
            f"bad = sorted(name for name in {FORBIDDEN_MODULES!r}"
            " if name in sys.modules and name not in before);"
            "print(','.join(bad))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_core_exports_the_public_api(self):
        import core

        for name in (
            "normalize_text",
            "strip_edges",
            "should_refuse",
            "Chunk",
            "CHUNK_MODES",
            "plan_chunks",
            "plan_otp_chunks",
            "plan_human_chunks",
            "Profile",
            "ProfileBook",
            "DEFAULT_PROFILE",
            "DispatchPlan",
            "DISPATCH_MODES",
            "resolve_dispatch",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(core, name), f"core does not export {name}")
                self.assertIn(name, core.__all__)


if __name__ == "__main__":
    unittest.main()
