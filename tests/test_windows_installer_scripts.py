"""Regression checks for the packaged native Windows customer installer."""
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class WindowsInstallerContractTests(unittest.TestCase):
    def test_installer_verifies_pinned_wheel_before_network_or_setup(self):
        script = (SCRIPTS / "windows-install.ps1").read_text(encoding="utf-8")
        self.assertIn("kleinanzeiger-0.1.24-py3-none-any.whl", script)
        self.assertIn("$wheelName = [IO.Path]::GetFileName($wheel)", script)
        self.assertIn("[regex]::Escape($wheelName)", script)
        self.assertEqual(script.count("function Test-Python("), 1)
        self.assertEqual(script.count("& $stagePython -m pip install"), 1)
        self.assertLess(script.index("$entry ="), script.index("Get-FileHash"))
        self.assertNotIn(r"kleinanzeiger-0\.1\.12-py3-none-any\.whl", script)
        self.assertNotIn(r"kleinanzeiger-0\.1\.8-py3-none-any\.whl", script)
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
        self.assertIn("'.venv-stage-'", script)
        self.assertIn("'.venv-rollback-'", script)
        self.assertIn("'.venv-invalid-'", script)
        self.assertIn("& $stagePython -m pip install", script)
        self.assertNotIn("& $installedPython -m pip install", script)
        self.assertLess(script.index("& $stagePython -m pip install"), script.index("Rename-Item -LiteralPath $venv"))
        self.assertIn("Rename-Item -LiteralPath $rollback", script)
        self.assertIn("if ($mayDisposeStage", script)
        self.assertIn("m.version('kleinanzeiger') == '0.1.24'", script)

    def test_release_notes_match_ai_100_and_checked_unknown_contract(self):
        workflow = (ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8")
        notes = workflow.split("<<\'EOF\'", 1)[1].split("\\n          EOF", 1)[0]
        self.assertIn("Bis zu 10.000 verifizierte eigene veröffentlichte Anzeigen", notes)
        self.assertIn("bis zu 100 einzelne KI-Anfragen", notes)
        self.assertIn("bis zu zehn sequenzielle KI-Anfragen für lokale Entwurfsaufträge", notes)
        self.assertIn("manuell geprüfte UNKNOWN-Beträge", notes)
        self.assertIn("wiederkehrende autonome Autopilot-Regeln", notes)
        self.assertNotIn("Massenpreisänderungen, Autopilot,", notes)

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

    def test_start_refuses_unowned_chrome_cdp_and_keeps_offline_fallback(self):
        chrome = (SCRIPTS / "windows-chrome.ps1").read_text(encoding="utf-8")
        start = (SCRIPTS / "windows-start.ps1").read_text(encoding="utf-8")
        self.assertIn("function Test-KleinanzeigerChromeOwnedDevTools(", chrome)
        self.assertIn("Get-NetTCPConnection", chrome)
        self.assertIn("Get-CimInstance", chrome)
        self.assertIn("Test-KleinanzeigerChromeOwnedDevTools -Port 9222", start)
        center = (SCRIPTS / "windows-center.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Test-KleinanzeigerChromeOwnedDevTools -Port 9222", center)
        self.assertIn("-m mark_api.offline_launcher", center)
        self.assertIn("$cdpOwnerVerified", start)
        self.assertIn("-m mark_api.offline_launcher", start)
        self.assertLess(
            start.index("Test-KleinanzeigerChromeOwnedDevTools -Port 9222"),
            start.index("$cdpOwnerVerified = $true"),
        )
        self.assertIn("ChromeProfile", start)
        self.assertNotIn("Remove-Item -LiteralPath $profile", start)

    @unittest.skipUnless(os.name == "nt", "Requires real Windows PowerShell 5.1")
    def test_windows_chrome_listener_pid_executable_profile_are_bound(self):
        path = str(SCRIPTS / "windows-chrome.ps1").replace("'", "''")
        probe = r"""
. '__CHROME_SCRIPT__'
function Test-KleinanzeigerChromeDevTools { param([int]$Port) return $true }
$script:FakeOwnerPid = 779
$script:FakeProcessPid = 779
$script:ExpectedChrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$script:FakeChrome = $script:ExpectedChrome
$script:FakeProfile = 'C:\Users\Mark\AppData\Local\Kleinanzeiger\ChromeProfile'
$script:FakeCommand = '"' + $script:FakeChrome + '" --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="' + $script:FakeProfile + '" https://www.kleinanzeigen.de/'
function Get-NetTCPConnection {
  [CmdletBinding()]
  param([int]$LocalPort, [string]$State)
  return [pscustomobject]@{LocalAddress='127.0.0.1';OwningProcess=$script:FakeOwnerPid}
}
function Get-CimInstance {
  [CmdletBinding()]
  param([string]$ClassName, [string]$Filter)
  return [pscustomobject]@{ProcessId=$script:FakeProcessPid;Name='chrome.exe';ExecutablePath=$script:FakeChrome;CommandLine=$script:FakeCommand}
}
function IsOwned { return Test-KleinanzeigerChromeOwnedDevTools -Port 9222 -ProfileDirectory $script:FakeProfile -ChromeExecutable $script:ExpectedChrome }
if (!(IsOwned)) { throw 'correct profile listener must be accepted' }
$script:FakeOwnerPid=780
if (IsOwned) { throw 'a different listener PID cannot authorize the old browser' }
$script:FakeOwnerPid=779
$script:FakeCommand = $script:FakeCommand.Replace('ChromeProfile', 'ChromeProfileOther')
if (IsOwned) { throw 'profile-prefix spoofing must be rejected' }
$script:FakeCommand = $script:FakeCommand.Replace('ChromeProfileOther', 'ChromeProfile')
$script:FakeCommand = $script:FakeCommand.Replace('--remote-debugging-port=9222', '--remote-debugging-port=9223')
if (IsOwned) { throw 'wrong debugging port must be rejected' }
$script:FakeCommand = $script:FakeCommand.Replace('--remote-debugging-port=9223', '--remote-debugging-port=9222')
$script:FakeChrome = 'C:\Other\chrome.exe'
if (IsOwned) { throw 'different executable must be rejected' }
Write-Output 'OWNED_CDP_PID_PROFILE_GUARD_PASS'
"""
        probe = probe.replace("__CHROME_SCRIPT__", path)
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", probe],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OWNED_CDP_PID_PROFILE_GUARD_PASS", result.stdout)

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
        self.assertIn('version = "0.1.24"', metadata)
        self.assertIn("Kleinanzeiger-v0.1.24-Windows.zip", workflow)
        for filename in (
            "Kleinanzeiger.cmd",
            "windows-install.ps1", "windows-center.ps1", "windows-worker.ps1", "windows-start.ps1", "windows-chrome.ps1",
            "windows-ki-einrichten.ps1",
        ):
            self.assertTrue((SCRIPTS / filename).is_file(), filename)
            self.assertIn(filename, workflow)
        self.assertIn("test_dashboard_managed_ad_picker.py", workflow)
        self.assertIn("test_dashboard_existing_ai_jobs.py", workflow)
        self.assertIn("test_dashboard_manual_sync.py", workflow)
        self.assertIn("test_product_offline_workspace.py", workflow)
        self.assertIn("assert _chrome() and shutil.which('node')", workflow)


if __name__ == "__main__":
    unittest.main()
