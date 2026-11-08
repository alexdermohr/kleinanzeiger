"""Ensure the buyer-side Windows entry is truly packaged and only uses the installed offline CLI."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class WindowsDealsUiContractTests(unittest.TestCase):
    def test_existing_advanced_window_has_one_clickable_customer_entry(self):
        hub = (SCRIPTS / "windows-center.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("$deals = Add-Button 'Schnäppchenjäger …' 330 568 282 43",hub)
        self.assertIn("$weekly = Add-Button 'Wochenbericht …' 28 568 282 43",hub)
        self.assertIn("$rollback,$weekly,$deals)",hub)
        self.assertIn("$deals.Enabled = !$busy -and (Test-Path -LiteralPath $python -PathType Leaf)",hub)
        self.assertIn("$deals.Add_Click({",hub)
        self.assertIn("Join-Path $base 'windows-deals.ps1'",hub)
        self.assertIn("Start-Process -FilePath 'powershell.exe'",hub)
        self.assertIn("$deals.Top -ne 568",hub)

    def test_window_has_manual_search_and_local_input_without_automatic_scraping(self):
        source = (SCRIPTS / "windows-deals.ps1").read_text(encoding="utf-8-sig")
        self.assertTrue((SCRIPTS / "windows-deals.ps1").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertIn("Windows.Forms.OpenFileDialog",source)
        self.assertIn("Invoke-DealsCli @('--datei',$source,'--modell',$model,'--bericht',$report)",source)
        self.assertIn("Invoke-DealsCli @('--modell',$model,'--suchlink')",source)
        self.assertIn("function Invoke-DealsCli([string[]]$cliArgs)",source)
        self.assertIn("$ErrorActionPreference = 'Continue'",source)
        self.assertIn("$ErrorActionPreference = $savedPreference",source)
        self.assertEqual(source.count("2>&1"),1)
        self.assertNotIn("$out = @(& $python",source)
        self.assertIn("Start-Process -FilePath $report",source)
        self.assertIn("Start-Process -FilePath $url",source)
        self.assertIn("'https'",source)
        self.assertIn("'www.kleinanzeigen.de'",source)
        self.assertIn("Join-Path $state 'Berichte'",source)
        for forbidden in ("Invoke-WebRequest","Invoke-RestMethod","OPENAI_API_KEY",
                          "mark_api.private_web","mark_api.platform_writes","subprocess.run",
                          "windows-worker.ps1"):
            self.assertNotIn(forbidden,source)
        self.assertIn("DEALS_CUSTOMER_UI_SELFTEST_PASS",source)
        self.assertIn("Eigene Angebotsdatei (CSV oder JSON)",source)
        self.assertIn("[string[]]$lines.ToArray()",source)

    def test_new_customer_script_is_in_package_and_real_windows_selftest(self):
        worker = (SCRIPTS / "windows-worker.ps1").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8")
        self.assertIn("'windows-deals.ps1'",worker)
        self.assertIn("scripts\\windows-deals.ps1",workflow)
        self.assertIn("'windows-deals.ps1'",workflow)
        self.assertIn("test_windows_deals_ui.py",workflow)
        self.assertIn("-SelfTest -ExpectedInstalledVersion '1.0.9'",workflow)
        self.assertIn("Schnäppchenjäger", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertIn("Schnäppchenjäger", (ROOT / "docs/START-HIER.html").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "native Windows PowerShell 5.1 only")
    def test_native_script_can_initialize_without_customer_account(self):
        result = subprocess.run(
            ["powershell.exe","-NoProfile","-NonInteractive","-STA",
             "-ExecutionPolicy","Bypass","-File",str(SCRIPTS / "windows-deals.ps1"),"-SelfTest"],
            capture_output=True,text=True,timeout=35,check=False,
        )
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn("DEALS_CUSTOMER_UI_SELFTEST_PASS",result.stdout)


if __name__ == "__main__":
    unittest.main()
