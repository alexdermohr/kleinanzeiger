"""Regression checks for the packaged native Windows customer installer."""
from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class WindowsInstallerContractTests(unittest.TestCase):
    def test_installer_verifies_pinned_wheel_before_network_or_setup(self):
        script = (SCRIPTS / "windows-install.ps1").read_text(encoding="utf-8")
        self.assertIn("kleinanzeiger-0.1.6-py3-none-any.whl", script)
        self.assertIn(r"kleinanzeiger-0\.1\.6-py3-none-any\.whl", script)
        self.assertNotIn(r"kleinanzeiger-0\.1\.3-py3-none-any\.whl", script)
        self.assertIn("SHA256SUMS.txt", script)
        self.assertLess(script.index("Get-FileHash"), script.index("Invoke-WebRequest"))
        self.assertIn("python-3.13.17-amd64.exe", script)
        self.assertRegex(script, r"(?i)pythonsha256\s*=\s*'[0-9a-f]{64}'")
        self.assertIn("Get-AuthenticodeSignature", script)
        self.assertIn("Test-Python", script)
        self.assertIn("'[private-web,generation]'", script)
        self.assertNotIn("Remove-Item -LiteralPath $state", script)
        self.assertNotIn("Restore", script)

    def test_no_powershell_home_reserved_variable_write(self):
        for filename in ("windows-install.ps1", "windows-chrome.ps1"):
            code = (SCRIPTS / filename).read_text(encoding="utf-8")
            self.assertNotRegex(code, r"(?i)\$home\b", filename)

    def test_chrome_has_official_signed_download_fallback(self):
        chrome = (SCRIPTS / "windows-chrome.ps1").read_text(encoding="utf-8")
        start = (SCRIPTS / "windows-start.ps1").read_text(encoding="utf-8")
        self.assertIn("googlechromelabs.github.io", chrome)
        self.assertIn("storage", chrome)
        self.assertIn("Get-AuthenticodeSignature", chrome)
        self.assertIn("Find-KleinanzeigerChrome", start)
        self.assertIn("127.0.0.1", start)
        self.assertIn("ChromeProfile", start)
        self.assertIn("Kleinanzeiger.cmd", (ROOT / "docs/windows-installation.md").read_text(encoding="utf-8"))

    def test_user_bound_optional_key_does_not_trigger_unapproved_generation(self):
        configure = (SCRIPTS / "windows-ki-einrichten.ps1").read_text(encoding="utf-8")
        start = (SCRIPTS / "windows-start.ps1").read_text(encoding="utf-8")
        self.assertIn("Read-Host", configure)
        self.assertIn("-AsSecureString", configure)
        self.assertIn("ConvertFrom-SecureString", configure)
        self.assertIn("ConvertTo-SecureString", start)
        self.assertIn("--enable-generation", start)
        self.assertIn("Remove-Item Env:OPENAI_API_KEY", start)
        self.assertIn("windows-center.ps1", (ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8"))

    def test_release_copy_manifest_and_version_are_consistent(self):
        workflow = (ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8")
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = "0.1.6"', metadata)
        self.assertIn("Kleinanzeiger-v0.1.6-Windows.zip", workflow)
        for filename in (
            "Kleinanzeiger.cmd",
            "windows-install.ps1", "windows-center.ps1", "windows-worker.ps1", "windows-start.ps1", "windows-chrome.ps1",
            "windows-ki-einrichten.ps1",
        ):
            self.assertTrue((SCRIPTS / filename).is_file(), filename)
            self.assertIn(filename, workflow)
        self.assertIn("test_dashboard_managed_ad_picker.py", workflow)


if __name__ == "__main__":
    unittest.main()