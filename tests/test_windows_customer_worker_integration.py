"""Native Windows PowerShell 5.1 acceptance of the actual customer worker.

Runs exclusively on ephemeral GitHub Actions Windows runners AFTER the Wheel
has been installed through the shipped customer installer. Never on a customer
PC, and never against a real email or platform account.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email import policy
import json
import os
from pathlib import Path
import subprocess
import unittest

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.query import MarkQueryService
from mark_api.storage import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "windows-worker.ps1"


@unittest.skipUnless(
    os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true",
    "requires disposable GitHub Actions native Windows runner",
)
class NativeWindowsCustomerWorkerTests(unittest.TestCase):
    def test_native_powershell_mail_import_and_group_labels(self) -> None:
        state = Path(os.environ["LOCALAPPDATA"]) / "Kleinanzeiger"
        db = state / "anzeigen.sqlite"
        venv = state / ".venv" / "Scripts" / "python.exe"
        self.assertTrue(venv.is_file(), "customer-installed wheel required")
        # Only ephemeral CI runner is in scope. Refuse replacing any prior DB.
        self.assertFalse(db.exists(), "unexpected existing customer DB on runner")
        store = SnapshotStore(db)
        store.append_ad_snapshot(
            AdSnapshot(
                ad_id="1234567890",
                observed_at=datetime(2026, 10, 26, 8, tzinfo=timezone.utc),
                source="management",
                lifecycle_state=LifecycleState.ACTIVE,
                title="Synthetischer Test",
            )
        )

        def worker(action: str, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
            data = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii") + "\n"
            return subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", str(WORKER), "-Action", action,
                ],
                input=data, capture_output=True, text=True, timeout=65, check=False,
            )

        labelled = worker("Classify", {
            "ad_id": "1234567890",
            "labels": {"city": "Lübeck", "image_type": "Detail"},
        })
        self.assertEqual(labelled.returncode, 0, labelled.stderr + labelled.stdout)
        self.assertIn("LABELS_OK", labelled.stdout)
        current = store.latest_classification_bounded("1234567890")
        self.assertIsNotNone(current)
        self.assertEqual(current.city, "Lübeck")
        self.assertEqual(current.image_type, "Detail")

        message = EmailMessage(policy=policy.default)
        message["From"] = "Kleinanzeigen <noreply@mail.kleinanzeigen.de>"
        message["To"] = "owner@example.invalid"
        message["Date"] = "Mon, 26 Oct 2026 08:00:00 +0000"
        message["Message-ID"] = "<11111111-2222-3333-4444-555555555555@chat.kleinanzeigen.de>"
        message["X-Conversation-ID"] = "abc12:def34:ghi56"
        message["X-Message-ID"] = "11111111-2222-3333-4444-555555555555"
        message.set_content(
            "Anfrage zu deiner Anzeige (Anzeigennummer: 1234567890) "
            "https://www.kleinanzeigen.de/m-nachrichten.html?"
            "conversationId=abc12:def34:ghi56"
        )
        notification = state / "synthetische-benachrichtigung.eml"
        notification.write_bytes(message.as_bytes(policy=policy.default))
        imported = worker("ImportMail", {"paths": [str(notification)]})
        self.assertEqual(imported.returncode, 0, imported.stderr + imported.stdout)
        self.assertIn("MAIL_IMPORT_OK", imported.stdout)
        reactions = MarkQueryService(store).email_reactions()
        self.assertEqual(len(reactions), 1)
        self.assertEqual(reactions[0].ad_id, "1234567890")
        backups = state / "Backups"
        self.assertTrue(list(backups.glob("*.sqlite.manifest.json")))
        self.assertEqual(store.latest_classification_bounded("1234567890").city, "Lübeck")


if __name__ == "__main__":
    unittest.main()
