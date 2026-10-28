"""Windows customer-start readiness and safe, truthful failure presentation."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class WindowsStartReadinessTests(unittest.TestCase):
    def test_chrome_devtools_probe_stays_local_and_bounded(self):
        script = (SCRIPTS / "windows-chrome.ps1").read_text(encoding="utf-8")
        self.assertIn("function Test-KleinanzeigerChromeDevTools", script)
        self.assertIn("function Wait-KleinanzeigerChromeDevTools", script)
        self.assertIn("http://127.0.0.1:$Port/json/version", script)
        self.assertIn("$request.Proxy = $null", script)
        self.assertIn("$request.Timeout = 800", script)
        self.assertIn("$watch.Elapsed.TotalSeconds -ge $TimeoutSeconds", script)
        self.assertIn("$TimeoutSeconds -gt 30", script)
        self.assertIn("return ($browser -match", script)
        self.assertIn("$socket -match", script)
        self.assertIn("nur die von Kleinanzeiger", script)
        self.assertNotIn("Stop-Process", script)
        self.assertNotIn("Remove-Item", script.split("function Test-KleinanzeigerChromeDevTools", 1)[1])

    def test_gui_waits_for_chrome_readiness_before_launching_python(self):
        script = (SCRIPTS / "windows-center.ps1").read_text(encoding="utf-8-sig")
        body = script.split("function Start-Product {", 1)[1].split("function Stop-Product {", 1)[0]
        self.assertLess(body.index("Open-Kleinanzeigen"),
                        body.index("Wait-KleinanzeigerChromeDevTools -Port 9222 -TimeoutSeconds 12"))
        self.assertLess(body.index("Wait-KleinanzeigerChromeDevTools -Port 9222"),
                        body.index("Start-PipedProcess $info 'product'"))
        self.assertIn("--timeout-seconds 12 --stop-on-stdin", body)
        self.assertIn("Start fehlgeschlagen – Fehlerdetails unten öffnen.", script)
        self.assertIn("Kleinanzeiger-Prozess beendet (Exitcode $exit)", script)
        self.assertIn("readinessProbeCount", script)
        self.assertIn("Fehlerdetails anzeigen", script)
        self.assertIn("$detailsLink.SetBounds(28,490,220,26)", script)
        self.assertNotIn("Konnte nicht starten. Falls nötig in Chrome anmelden", script)

    def test_manual_console_start_also_checks_cdp_endpoint(self):
        script = (SCRIPTS / "windows-start.ps1").read_text(encoding="utf-8")
        self.assertRegex(
            script, r"Read-Host 'Nach erfolgreichem Login ENTER druecken'\s+"
                    r"Wait-KleinanzeigerChromeDevTools -Port 9222 -TimeoutSeconds 12"
        )
        self.assertIn("ChromeProfile", script)

    def test_customers_see_actionable_troubleshooting(self):
        install = (ROOT / "docs/windows-installation.md").read_text(encoding="utf-8")
        manual = (ROOT / "docs/Kleinanzeiger-Handbuch.html").read_text(encoding="utf-8")
        self.assertIn("Browsersteuerung", install)
        self.assertIn("Details", manual)
        self.assertIn("Profile, Datenbank oder Backups", manual)


if __name__ == "__main__":
    unittest.main()
