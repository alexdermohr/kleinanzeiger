"""Windows single-entry customer hub: no new write API or platform actions."""
from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

from mark_api.launcher import _watch_local_stop_stdin


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class WindowsHubContractTests(unittest.TestCase):
    def test_one_visible_entry_and_native_setup_ui(self) -> None:
        command = (SCRIPTS / "Kleinanzeiger.cmd").read_text(encoding="utf-8")
        gui = (SCRIPTS / "windows-center.ps1").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8")
        self.assertIn("windows-center.ps1", command)
        self.assertIn("-STA", command)
        self.assertIn("System.Windows.Forms", gui)
        for label in (
            "Einrichten und starten", "Kleinanzeiger starten",
            "Installieren / aktualisieren", "Sicherung erstellen",
            "KI einrichten", "Kleinanzeigen-Konto öffnen",
            "Artikel importieren", "Entwürfe exportieren",
            "Sicherung wiederherstellen",
        ):
            self.assertIn(label, gui)
        self.assertIn("scripts\\Kleinanzeiger.cmd", workflow)
        self.assertNotIn("Copy-Item scripts\\Installieren.cmd", workflow)
        self.assertIn("windows-center.ps1", workflow)
        self.assertIn("windows-worker.ps1", workflow)
        self.assertIn("test_windows_hub.py", workflow)

    def test_windows_powershell_51_displays_unicode_labels(self) -> None:
        # Windows PowerShell 5.1 otherwise reads BOM-less UTF-8 as ANSI.
        encoded = (SCRIPTS / "windows-center.ps1").read_bytes()
        self.assertTrue(encoded.startswith(b"\xef\xbb\xbf"))
        self.assertIn("Sicherung erstellen", encoded.decode("utf-8-sig"))
        self.assertIn("öffnen", encoded.decode("utf-8-sig"))

    def test_only_one_control_window_can_manage_installation(self) -> None:
        gui = (SCRIPTS / "windows-center.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("Local\\Kleinanzeiger-Control-Center", gui)
        self.assertIn("if (!$newInstance)", gui)
        self.assertIn("$form.Add_FormClosed({", gui)
        self.assertIn("ReleaseMutex()", gui)
        self.assertIn("if (!$SelfTest)", gui)

    def test_reviewed_windows_launcher_paths_remain_in_shared_scope(self) -> None:
        gui = (SCRIPTS / "windows-center.ps1").read_text(encoding="utf-8-sig")
        self.assertLess(gui.index(". (Join-Path $base 'windows-chrome.ps1')"),
                        gui.index("function Open-Kleinanzeigen"))
        self.assertNotIn("function Open-Kleinanzeigen {\\n  . (Join-Path", gui)
        self.assertIn("Get-Command -Name Find-KleinanzeigerChrome", gui)

    def test_powershell_51_version_and_update_regressions(self) -> None:
        gui = (SCRIPTS / "windows-center.ps1").read_text(encoding="utf-8-sig")
        worker = (SCRIPTS / "windows-worker.ps1").read_text(encoding="utf-8")
        for script in (gui, worker):
            self.assertIn('m.version(\'kleinanzeiger\')', script)
            self.assertIn("ExpectedInstalledVersion", script)
            self.assertNotIn('m.version("kleinanzeiger")', script)
        self.assertIn("$names = @(Get-ReleaseAssetNames $version)", worker)
        self.assertIn("if ($null -eq $download)", worker)
        self.assertLess(worker.index("function Get-ReleaseAssetNames"),
                        worker.index("function Download-VerifiedRelease"))
        self.assertLess(worker.index("if ($null -eq $download)"),
                        worker.index("Write-Output 'ALREADY_CURRENT'"))
        workflow = (ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8")
        self.assertIn("-SelfTest -ExpectedInstalledVersion '0.1.11'", workflow)

    def test_update_and_backup_are_hash_bound_and_fail_closed(self) -> None:
        worker = (SCRIPTS / "windows-worker.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-VerifiedPackage", worker)
        self.assertIn("Get-FileHash", worker)
        self.assertIn("Windows.sha256", worker)
        self.assertIn("Release-Manifest.json", worker)
        self.assertIn("github.com/alexdermohr/kleinanzeiger/releases/download", worker)
        self.assertIn("Assert-KleinanzeigerStopped", worker)
        self.assertLess(worker.index("Save-Backup\n"), worker.index("& (Join-Path $folder 'windows-install.ps1')"))
        self.assertIn("mark_api.backup_cli", worker)
        self.assertIn(".manifest.json", worker)
        self.assertIn("Install-Package", worker)
        self.assertIn("Publish-AppFiles", worker)
        self.assertIn("Kleinanzeiger.lnk", worker)
        self.assertIn("App", worker)
        self.assertIn("Wiederherstellung nur bei nicht vorhandener Datenbank", worker)
        self.assertNotIn("Remove-Item -LiteralPath $db", worker)

    def test_compact_workflow_and_explicit_advanced_tools(self) -> None:
        gui = (SCRIPTS / "windows-center.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("function Set-AdvancedLayout([bool]$expanded)", gui)
        self.assertIn("Set-AdvancedLayout $false", gui)
        self.assertIn("$advanced.Add_Click({ Set-AdvancedLayout (!$script:advancedExpanded) })", gui)
        self.assertIn("'Weitere Aktionen'", gui)
        self.assertIn("'Weniger anzeigen'", gui)
        self.assertIn("$form.ClientSize = [System.Drawing.Size]::new(640,510)", gui)
        self.assertIn("$(if($expanded){630}else{510})", gui)
        self.assertIn("$control.Visible = $expanded", gui)
        self.assertIn("$detailsLink.Visible = $failure", gui)
        self.assertIn("$detailsLink.SetBounds(28,453,240,26)", gui)
        self.assertIn("Kompakte Bedienoberflaeche ist nicht korrekt gestartet.", gui)
        self.assertNotIn("$advanced.Add_Click({ $restore.Visible = !$restore.Visible })", gui)
        self.assertLess(gui.index("$update = Add-Button"), gui.index("$account = Add-Button"))
        self.assertLess(gui.index("$account = Add-Button"), gui.index("$mail = Add-Button"))

    def test_private_control_and_key_never_enter_log(self) -> None:
        gui = (SCRIPTS / "windows-center.ps1").read_text(encoding="utf-8")
        self.assertIn("--stop-on-stdin", gui)
        self.assertIn("StandardInput.WriteLine('STOP')", gui)
        self.assertIn("StandardInput.Flush()", gui)
        self.assertIn("write_token|generation_token|OPENAI_API_KEY", gui)
        self.assertIn("ConvertFrom-SecureString", gui)
        self.assertIn("ConvertTo-SecureString", gui)
        self.assertIn("OPENAI_API_KEY", gui)
        self.assertIn("Kleinanzeiger dashboard:", gui)
        self.assertIn("Find-KleinanzeigerChrome", gui)
        self.assertNotIn("Set-Content -LiteralPath $db", gui)

    def test_launcher_accepts_explicit_gui_stop_only(self) -> None:
        from mark_api import launcher
        source = (ROOT / "src/mark_api/launcher.py").read_text(encoding="utf-8")
        self.assertIn('"--stop-on-stdin"', source)
        self.assertIn("args.stop_on_stdin", source)
        self.assertIn("_thread.interrupt_main", source)
        requests: list[int] = []
        _watch_local_stop_stdin(io.StringIO("HELLO\n STOP \n"), lambda: requests.append(1))
        self.assertEqual(requests, [1])
        requests.clear()
        _watch_local_stop_stdin(io.StringIO("NOT_STOP\n"), lambda: requests.append(1))
        # Closing the private input pipe also requests normal shutdown.
        self.assertEqual(requests, [1])
        requests.clear()
        stream = iter(["STOP\n", "STOP\n"])
        _watch_local_stop_stdin(stream, lambda: requests.append(1))
        self.assertEqual(requests, [1])

    def test_private_pipe_requests_existing_sigint_cleanup(self) -> None:
        """Integration: a child with the same stdin watcher exits on a private STOP."""
        program = """
import _thread
import signal
import sys
import time
from threading import Thread
from mark_api.launcher import _watch_local_stop_stdin

def on_int(_signum, _frame):
    raise KeyboardInterrupt()

signal.signal(signal.SIGINT, on_int)
Thread(target=_watch_local_stop_stdin, args=(sys.stdin, _thread.interrupt_main),
       daemon=True).start()
print("READY", flush=True)
try:
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    print("CONTROLLED_STOP", flush=True)
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        with subprocess.Popen(
            [sys.executable, "-u", "-c", program],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        ) as process:
            assert process.stdout is not None
            self.assertEqual(process.stdout.readline().strip(), "READY")
            out, err = process.communicate("STOP\n", timeout=10)
        self.assertEqual(process.returncode, 0, out + err)
        self.assertIn("CONTROLLED_STOP", out)

    def test_versioned_windows_package_is_not_old_release(self) -> None:
        meta = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        script = (SCRIPTS / "windows-install.ps1").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8")
        self.assertIn('version = "0.1.11"', meta)
        self.assertIn("kleinanzeiger-0.1.11-py3-none-any.whl", script)
        self.assertIn("Kleinanzeiger-v0.1.11-Windows.zip", workflow)
        self.assertIn("publish-windows-v0.1.11", workflow)


if __name__ == "__main__":
    unittest.main()