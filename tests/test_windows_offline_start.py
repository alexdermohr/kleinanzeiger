"""Execute the shipped Windows start function with synthetic OS edges, never a real account."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Native Windows PowerShell 5.1 required")
class NativeWindowsOfflineStartTests(unittest.TestCase):
    def test_browser_failure_still_starts_product_without_reset_or_login_claim(self):
        script = r'''
param([string]$Source)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Shipped PowerShell is invalid' }
$definition = $ast.Find({param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Start-Product'
}, $false)
if (!$definition) { throw 'Real Start-Product function missing' }
# Only this exact shipped function is evaluated. No UI, customer directory,
# process launcher, file writer or real browser is run in this fixture.
Invoke-Expression $definition.Extent.Text
$python = 'C:\fixture\python.exe'; $db = 'C:\fixture\customer.sqlite'; $keyFile = 'C:\fixture\missing-key'
$statusLabel = [PSCustomObject]@{}
$statusLabel | Add-Member -MemberType ScriptMethod -Name Refresh -Value {}
$script:active = $false; $script:pythonExists = $true
$script:dbExists = $true; $script:started = @(); $script:messages = @()
$script:failOpen = $false; $script:failReady = $true
function Test-ActiveProduct { return $script:active }
function Test-Path([string]$LiteralPath,[string]$PathType) {
  if ($LiteralPath -eq $python) { return $script:pythonExists }
  if ($LiteralPath -eq $db) { return $script:dbExists }
  return $false
}
function Set-Status([string]$Message,[bool]$ErrorState=$false) { $script:messages += $Message }
function Open-Kleinanzeigen { if ($script:failOpen) { throw 'synthetic chrome absent' }; return 'fixture-chrome' }
function Wait-KleinanzeigerChromeDevTools([int]$Port,[int]$TimeoutSeconds) {
  if ($Port -ne 9222 -or $TimeoutSeconds -ne 12) { throw 'readiness bounds changed' }
  if ($script:failReady) { throw 'synthetic browser transport unavailable' }
}
function Start-PipedProcess([Diagnostics.ProcessStartInfo]$info,[string]$kind) {
  $script:started += [PSCustomObject]@{Arguments=$info.Arguments;Kind=$kind;Input=$info.RedirectStandardInput}
}
function Start-Process { throw 'unexpected real process' }
function Remove-Item { throw 'unexpected deletion' }
function Set-Content { throw 'unexpected file replacement' }
function New-Item { throw 'unexpected state creation' }
foreach ($openFailure in @($false,$true)) {
  $script:failOpen = $openFailure; $script:messages = @()
  Start-Product
  $call = $script:started[-1]
  if ($call.Kind -ne 'product' -or !$call.Input -or $call.Arguments -notmatch '--stop-on-stdin') { throw 'product path not reached' }
  if ($call.Arguments -match '--init-db|--enable-generation') { throw 'existing state or absent AI key bypassed' }
  if (($script:messages -join ' ') -notmatch 'lokalen Arbeitsbereich') { throw 'local mode not explained' }
  if (($script:messages -join ' ') -match 'anmelden|Login') { throw 'browser failure mislabeled as login failure' }
}
$script:failOpen=$false; $script:failReady=$false
Start-Product
if ($script:started.Count -ne 3) { throw 'online or offline path did not reach product' }
$script:dbExists=$false
Start-Product
if ($script:started[-1].Arguments -notmatch '--init-db') { throw 'explicit first installation no longer initializes' }
$script:active=$true; $denied=$false
try { Start-Product } catch { $denied=$true }
if (!$denied -or $script:started.Count -ne 4) { throw 'active product guard bypassed' }
$script:active=$false; $script:pythonExists=$false; $denied=$false
try { Start-Product } catch { $denied=$true }
if (!$denied -or $script:started.Count -ne 4) { throw 'missing installation guard bypassed' }
Write-Output 'WINDOWS_OFFLINE_START_NO_RESET_PASS'
'''
        with tempfile.TemporaryDirectory(prefix="kleinanzeiger-start-test-") as directory:
            harness = Path(directory) / "verify-start.ps1"
            harness.write_text(script, encoding="utf-8-sig")
            result = subprocess.run(
                [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(harness), "-Source", str(ROOT / "scripts/windows-center.ps1")],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WINDOWS_OFFLINE_START_NO_RESET_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
