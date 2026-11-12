"""Version-specific customer notes and official Windows release pipeline contracts."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.release_notes import ReleaseNotesError, current_version, release_notes


ROOT = Path(__file__).resolve().parents[1]


class ReleaseNotesTests(unittest.TestCase):
    def test_current_version_has_specific_customer_notes(self):
        version = current_version(ROOT)
        body = release_notes(version, ROOT)
        self.assertTrue(body.startswith(f"# Kleinanzeiger {version} – Windows 11"))
        self.assertRegex(body, r"(?m)^### (Neu|Verbessert|Behoben|Sicherheit|Erste Kundenversion)$")
        self.assertIn("- ", body)
        self.assertNotIn("## [1.0.2]", body)

    def test_unrelated_versions_are_not_substituted(self):
        for version in ("1.0.0", "1.0.1", "1.0.2"):
            with self.subTest(version=version):
                body = release_notes(version, ROOT)
                self.assertTrue(body.startswith(f"# Kleinanzeiger {version} – Windows 11"))
                self.assertNotIn("## [", body)
        with self.assertRaisesRegex(ReleaseNotesError, "Exactly one entry"):
            release_notes("99.1.0", ROOT)

    def test_missing_duplicate_placeholder_and_empty_entries_fail(self):
        samples = (
            "",
            "## [1.0.3]\n\n- TODO: Bitte noch ergänzen. Das ist nur ein Platzhalter.\n",
            "## [1.0.3]\n\n- Ein langer ausreichender Kundenhinweis mit konkreter Änderung.\n"
            "## [1.0.3]\n\n- Eine zweite Version mit identischer Nummer.\n",
            "## [1.0.3]\n\n### Neu\nEin Fließtext ohne Änderungsaufzählung.\n",
        )
        for source in samples:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temp:
                (Path(temp) / "CHANGELOG.md").write_text(source, encoding="utf-8")
                with self.assertRaises(ReleaseNotesError):
                    release_notes("1.0.3", Path(temp))

    def test_generator_cli_matches_checked_in_current_notes(self):
        output = subprocess.run(
            [sys.executable, "tools/release_notes.py", "--current"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertEqual(output.stdout, release_notes(current_version(ROOT), ROOT))

    def test_native_release_uses_only_changelog_as_notes_source(self):
        workflow = (ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8")
        self.assertIn("Copy-Item CHANGELOG.md $staging", workflow)
        self.assertIn('test -s "$stage/unpacked/CHANGELOG.md"', workflow)
        self.assertIn("python3 tools/release_notes.py --current", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("--notes-file", workflow)
        self.assertNotIn("cat > \"$stage/Kleinanzeiger-v", workflow)

    def test_windows_ui_displays_only_approved_text_without_automatic_install(self):
        ui = (ROOT / "scripts/windows-center.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("function Get-OfficialReleaseNotes", ui)
        self.assertIn("(Get-OfficialReleaseVersion $responseText) -cne $version", ui)
        self.assertIn("$script:latestStableNotes = Get-OfficialReleaseNotes", ui)
        self.assertIn("function Get-LocalReleaseNotes", ui)
        self.assertIn("function Show-ReleaseNotes", ui)
        self.assertIn("$content.ReadOnly = $true", ui)
        self.assertIn("$releaseNotesLink.Visible = $true", ui)
        self.assertIn("Get-FittingReleaseCaption $script:displayNotesVersion", ui)
        self.assertIn("return $prefix + 'Alle Änderungen'", ui)
        self.assertIn("elseif (Get-LocalReleaseNotes $installed)", ui)
        self.assertIn("Show-ReleaseNotes $script:displayNotesVersion", ui)
        self.assertIn("Start-Worker $action", ui)


if __name__ == "__main__":
    unittest.main()