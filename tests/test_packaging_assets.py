"""Guard tests for the one-file installer assets under ``deploy/bundle``.

These tests do not build the installer (that needs the 48 MB PIME package and
takes a minute); they check that the scripts, manifest and version metadata are
present and still consistent with the module and the README.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DIR = REPO_ROOT / "deploy" / "bundle"
CIPS_DIR = REPO_ROOT / "src" / "input_methods" / "cips"
README = REPO_ROOT / "README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

REQUIRED_FILES = (
    "build_installer.ps1",
    "install.ps1",
    "uninstall.ps1",
    "common.ps1",
    "THIRD_PARTY_NOTICES.md",
    "installer/Installer.cs",
    "installer/app.manifest",
)


class BundleAssetsTests(unittest.TestCase):
    def test_required_files_exist(self):
        for relative in REQUIRED_FILES:
            with self.subTest(file=relative):
                self.assertTrue((BUNDLE_DIR / relative).is_file(), relative)

    def test_manifest_requests_administrator(self):
        text = (BUNDLE_DIR / "installer" / "app.manifest").read_text(encoding="utf-8")
        self.assertIn('level="requireAdministrator"', text)

    def test_bootstrapper_embeds_the_payload_and_runs_install_ps1(self):
        text = (BUNDLE_DIR / "installer" / "Installer.cs").read_text(encoding="utf-8")
        self.assertIn("cips.payload.zip", text)
        self.assertIn("install.ps1", text)
        self.assertIn("batch", text)

    def test_install_script_knows_both_modes(self):
        text = (BUNDLE_DIR / "install.ps1").read_text(encoding="utf-8")
        common = (BUNDLE_DIR / "common.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-Bundle", text)
        self.assertIn("bundle.json", common)
        self.assertIn("module", text)
        self.assertIn("PIMELauncher", text)
        self.assertIn("Get-PimeInstallDir", text)
        self.assertIn("Register-TextService", text)
        self.assertIn("InstallGram", text)
        self.assertIn("GramPath", text)
        self.assertIn("Remove-BundledPimeImes", text)

    def test_bootstrapper_offers_optional_gram_ui(self):
        text = (BUNDLE_DIR / "installer" / "Installer.cs").read_text(encoding="utf-8")
        self.assertIn("InstallGram", text)
        self.assertIn("GramPath", text)
        self.assertIn("wanxiang-lts-zh-hans.gram", text)

    def test_rime_config_and_skin_assets_exist(self):
        for rel in ("cips.yaml", "default.custom.yaml", "gram_patch.yaml"):
            with self.subTest(file=rel):
                self.assertTrue((CIPS_DIR / "rime_config" / rel).is_file(), rel)
        self.assertTrue((CIPS_DIR / "skins" / "gemini_neko_mascot.png").is_file())
        self.assertTrue((CIPS_DIR / "win32" / "candidate_ui.py").is_file())
        self.assertTrue((REPO_ROOT / "deploy" / "prepare_rime.ps1").is_file())

    def test_uninstall_script_reads_the_install_marker(self):
        text = (BUNDLE_DIR / "uninstall.ps1").read_text(encoding="utf-8")
        self.assertIn("install.json", text)
        self.assertIn("Uninstall.exe", text)
        self.assertIn("Remove-UninstallEntry", text)

    def test_pinned_pime_package_matches_the_readme(self):
        script = (BUNDLE_DIR / "build_installer.ps1").read_text(encoding="utf-8")
        match = re.search(r"PimeSetupSha256 = '([0-9a-f]{64})'", script, re.IGNORECASE)
        self.assertIsNotNone(match, "build_installer.ps1 no longer pins a SHA-256")
        sha = match.group(1).lower()
        readme = README.read_text(encoding="utf-8")
        self.assertIn(sha, readme, "README and build script disagree on the PIME package checksum")

    def test_bundle_version_matches_ime_json_and_changelog(self):
        version = json.loads((CIPS_DIR / "ime.json").read_text(encoding="utf-8"))["version"]
        self.assertIn("## " + version, CHANGELOG.read_text(encoding="utf-8"))

    def test_readme_documents_the_installer(self):
        readme = README.read_text(encoding="utf-8")
        self.assertIn("build_installer.ps1", readme)
        self.assertIn("cips-input-setup", readme)
        self.assertIn("Program Files (x86)\\PIME", readme)

    @unittest.skipUnless(os.name == "nt", "the PowerShell parser only exists on Windows")
    def test_powershell_scripts_parse(self):
        powershell = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        )
        if not os.path.isfile(powershell):
            self.skipTest("Windows PowerShell 5.1 is not available")
        for name in ("common.ps1", "install.ps1", "uninstall.ps1", "build_installer.ps1", "../prepare_rime.ps1", "../install_backend.ps1"):
            path = (BUNDLE_DIR / name).as_posix()
            script = (
                "$errors=$null; $tokens=$null;"
                " [void][System.Management.Automation.Language.Parser]::ParseFile("
                "'" + path.replace("'", "''") + "'"
                ", [ref]$tokens, [ref]$errors);"
                " if ($errors -and $errors.Count -gt 0) {"
                " $errors | ForEach-Object { Write-Output $_.Message }; exit 1 }"
            )
            result = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True,
                text=True,
                timeout=180,
            )
            with self.subTest(script=name):
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
