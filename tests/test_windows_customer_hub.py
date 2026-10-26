"""Windows customer actions use existing local mail and classification contracts."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from mark_api.classification_cli import update_classification
from mark_api.domain import AdSnapshot, InboundMessageEvent, LifecycleState
from mark_api.email_import_cli import _parser as email_parser
from mark_api.storage import SnapshotStore
from mark_api.windows_customer_choices import list_customer_choices, main

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
NOW = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)


class CustomerHubTests(unittest.TestCase):
    def test_missing_database_does_not_get_initialized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "ads.sqlite"
            errors = io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(main(["--db", str(db)]), 2)
            self.assertFalse(db.parent.exists())
            self.assertIn("nicht gelesen", errors.getvalue())

    def test_known_ads_email_only_ids_and_existing_labels_are_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "anzeigen.sqlite"
            store = SnapshotStore(db)
            store.append_ad_snapshot(
                AdSnapshot(
                    ad_id="11111",
                    observed_at=NOW,
                    source="management",
                    lifecycle_state=LifecycleState.ACTIVE,
                    title="Schreibtisch",
                )
            )
            store.append_inbound_message_events((
                InboundMessageEvent(
                    ad_id="22222",
                    conversation_id="conversation-1",
                    provider_message_id="message-1",
                    observed_at=NOW,
                    source="kleinanzeigen-email",
                ),
            ))
            update_classification(store, ad_id="11111", labels={"city": "Bremen"})
            records = list_customer_choices(db)
            self.assertEqual([x["ad_id"] for x in records], ["11111", "22222"])
            self.assertEqual(records[0]["title"], "Schreibtisch")
            self.assertEqual(records[0]["labels"]["city"], "Bremen")
            self.assertIs(records[0]["email_only"], False)
            self.assertIs(records[1]["email_only"], True)
            self.assertIsNone(records[1]["labels"]["image_type"])
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["--db", str(db)]), 0)
            self.assertEqual(json.loads(out.getvalue())["ads"], records)

    def test_customer_actions_delegate_to_existing_validated_cli(self) -> None:
        worker = (SCRIPTS / "windows-worker.ps1").read_text(encoding="utf-8")
        hub = (SCRIPTS / "windows-center.ps1").read_text(encoding="utf-8-sig")
        workflow = (ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8")
        self.assertIn("Import-CustomerMail", worker)
        self.assertIn("Update-CustomerLabels", worker)
        self.assertIn("mark_api.email_import_cli", worker)
        self.assertIn("mark_api.classification_cli", worker)
        self.assertIn("Assert-KleinanzeigerStopped", worker)
        self.assertIn("Save-Backup", worker)
        self.assertIn("Read-UiPayload", worker)
        self.assertIn("FromBase64String", worker)
        self.assertIn("StandardInput.WriteLine($encoded)", hub)
        self.assertNotIn("-EmailPaths", hub)
        self.assertIn("mark_api.windows_customer_choices", hub)
        self.assertIn("Show-CustomerMailPicker", hub)
        self.assertIn("Show-CustomerLabels", hub)
        self.assertIn("DropDownList", hub)
        self.assertIn("test_windows_customer_hub.py", workflow)
        self.assertIn("Kleinanzeiger-Handbuch.html", workflow)
        self.assertIn("Kleinanzeiger-Funktionen.html", workflow)
        self.assertIn("'0.1.5'", workflow)
        self.assertIn("0.1.5", (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            email_parser().parse_args(["--db", "ads.sqlite", "x.eml"]).emails,
            [Path("x.eml")],
        )


if __name__ == "__main__":
    unittest.main()
