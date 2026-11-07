"""Contract for first customer-facing release documents and package entrypoint."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
CUSTOMER_DOCS = (
    "README.md",
    "docs/START-HIER.html",
    "docs/Kleinanzeiger-Handbuch.html",
    "docs/Kleinanzeiger-Funktionen.html",
    "docs/windows-installation.md",
    "docs/platform-nachrichten.md",
)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.details = 0
        self.strong = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.links.append(dict(attrs).get("href") or "")
        elif tag == "details":
            self.details += 1
        elif tag == "strong":
            self.strong += 1


class FirstCustomerReleaseTests(unittest.TestCase):
    def test_customer_documents_have_no_dev_history_and_correct_version(self) -> None:
        for name in CUSTOMER_DOCS:
            with self.subTest(file=name):
                data = (ROOT / name).read_text(encoding="utf-8")
                self.assertNotRegex(data, r"(?i)\bv0\.1\.\d+\b|\b0\.1\.\d+\b")
                self.assertNotIn("Bereits in v0", data)
                self.assertNotIn("Im aktuellen Entwicklungsbranch", data)
                self.assertNotIn("README-aktualisiert", data)
        self.assertIn("Version 1.0.1", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertLess((ROOT / "README.md").stat().st_size, 5000)
        self.assertLess((ROOT / "docs/windows-installation.md").stat().st_size, 5000)
        self.assertLess((ROOT / "docs/Kleinanzeiger-Handbuch.html").stat().st_size, 16000)
        self.assertLess((ROOT / "docs/Kleinanzeiger-Funktionen.html").stat().st_size, 17000)

    def test_usable_first_start_page_and_local_help(self) -> None:
        start = (ROOT / "docs/START-HIER.html").read_text(encoding="utf-8")
        self.assertIn("Kleinanzeiger.cmd", start)
        self.assertIn("Einrichten und starten", start)
        self.assertIn("Mit Kleinanzeigen abgleichen", start)
        self.assertIn("Benutzerhandbuch", start)
        for name in ("START-HIER.html", "Kleinanzeiger-Handbuch.html", "Kleinanzeiger-Funktionen.html"):
            p = _Links()
            p.feed((ROOT / "docs" / name).read_text(encoding="utf-8"))
            for url in p.links:
                if not url or url.startswith("#") or "://" in url:
                    continue
                if url == "Konto-Nachrichten.md":
                    self.assertTrue((ROOT / "docs/platform-nachrichten.md").is_file())
                else:
                    self.assertTrue((ROOT / "docs" / url).is_file(), (name, url))
        self.assertIn("Browsersteuerung", (ROOT / "docs/windows-installation.md").read_text(encoding="utf-8"))
        manual = (ROOT / "docs/Kleinanzeiger-Handbuch.html").read_text(encoding="utf-8")
        self.assertIn("Profile, Datenbank oder Backups", manual)
        self.assertIn("Details", manual)

    def test_feature_overview_covers_customer_requirements(self) -> None:
        h = (ROOT / "docs/Kleinanzeiger-Funktionen.html").read_text(encoding="utf-8")
        parser = _Links()
        parser.feed(h)
        self.assertEqual(parser.details, 5)
        self.assertGreaterEqual(parser.strong, 35)
        for term in ("Anzeigen", "CSV-/Excel", "Bild", "KI", "Titel", "Beschreibung",
                     "Aufrufe", "Merker", "Antworten", "Bildtyp", "Stadt", "Preis",
                     "Wochenbericht", "Sicherung", "Wiederherstellung", "Nachrichten"):
            with self.subTest(feature=term):
                self.assertIn(term.casefold(), h.casefold())
        self.assertIn("unbeaufsichtigter nachrichtenversand", h.casefold())

    def test_offline_ui_guides_to_real_windows_launcher(self) -> None:
        html_source = (ROOT / "src/mark_api/dashboard.py").read_text(encoding="utf-8")
        self.assertIn("Kleinanzeiger.cmd", html_source)
        self.assertNotIn("Starten.cmd", html_source)

    def test_release_pipeline_provides_single_customer_zip_not_historical_docs(self) -> None:
        workflow = (ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8")
        meta = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = "1.0.1"', meta)
        self.assertIn("release_tag='v1.0.1'", workflow)
        self.assertIn("Copy-Item docs\\START-HIER.html $staging", workflow)
        self.assertIn('test -s "$stage/unpacked/START-HIER.html"', workflow)
        self.assertIn("Kleinanzeiger-v1.0.1-Windows.zip", workflow)
        self.assertNotIn("README-aktualisiert", workflow)
        release = workflow.split("          gh release create ", 1)[1]
        self.assertIn('            "$zip" \\', release)
        self.assertIn('            "$stage/Kleinanzeiger-v1.0.1-Windows.sha256" \\', release)
        self.assertIn('            "$stage/Kleinanzeiger-v1.0.1-Release-Manifest.json"', release)
        self.assertNotIn('"$stage/unpacked/Kleinanzeiger-Funktionen.html"', release)


if __name__ == "__main__":
    unittest.main()
