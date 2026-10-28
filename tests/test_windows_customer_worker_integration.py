"""Native Windows PowerShell 5.1 acceptance of the actual customer worker.

Runs exclusively on ephemeral GitHub Actions Windows runners AFTER the Wheel
has been installed through the shipped customer installer. Never on a customer
PC, and never against a real email or platform account.
"""
from __future__ import annotations

import base64
import csv
import hashlib
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
from mark_api.portfolio_drafts import drafts_page
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

        # Isolate Python wheel/SQLite code from the Windows PowerShell process.
        # A separate synthetic ad ID prevents this diagnostic probe from
        # modifying the user-action fixture later in this test.
        store.append_ad_snapshot(
            AdSnapshot(
                ad_id="9876543210",
                observed_at=datetime(2026, 10, 26, 8, tzinfo=timezone.utc),
                source="management",
                lifecycle_state=LifecycleState.ACTIVE,
                title="Diagnose",
            )
        )
        direct = base64.b64encode(json.dumps({
            "ad_id": "9876543210", "labels": {"city": 'Łódź "Nord"'},
        }, ensure_ascii=False).encode("utf-8")).decode("ascii")
        direct_env = {**os.environ, "KLEINANZEIGER_LABEL_PAYLOAD": direct}
        direct_run = subprocess.run(
            [str(venv), "-m", "mark_api.windows_customer_choices",
             "--db", str(db), "--apply-labels"],
            env=direct_env, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(direct_run.returncode, 0, direct_run.stderr + direct_run.stdout)
        self.assertEqual(
            store.latest_classification_bounded("9876543210").city, 'Łódź "Nord"',
        )

        labelled = worker("Classify", {
            "ad_id": "1234567890",
            "labels": {"city": 'Łódź "Nord"', "image_type": 'Detail "Makro"'},
        })
        self.assertEqual(labelled.returncode, 0, labelled.stderr + labelled.stdout)
        self.assertIn("LABELS_OK", labelled.stdout)
        current = store.latest_classification_bounded("1234567890")
        self.assertIsNotNone(current)
        self.assertEqual(current.city, 'Łódź "Nord"')
        self.assertEqual(current.image_type, 'Detail "Makro"')

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
        self.assertEqual(store.latest_classification_bounded("1234567890").city, 'Łódź "Nord"')

        # End-to-end: 1,000 customer-supplied articles remain local drafts.
        csv_file = state / "synthetische-artikel.csv"
        with csv_file.open("w", encoding="utf-8-sig", newline="") as output:
            sheet = csv.writer(output)
            sheet.writerow(("sku","title","description","category","price_eur","images"))
            for i in range(1000):
                sheet.writerow((
                    f"SKU-{i:05d}", f'Łódź "Vase" {i}', "Gebrauchter Artikel",
                    "Haus > Dekoration", 29, "ansicht.jpg;detail.png",
                ))
        request = base64.b64encode(json.dumps({"path": str(csv_file)}).encode("utf-8")).decode("ascii")
        preview = subprocess.run(
            [str(venv), "-m", "mark_api.portfolio_import_cli",
             "--db", str(db), "--mode", "preview"],
            env={**os.environ, "KLEINANZEIGER_IMPORT_REQUEST": request},
            capture_output=True, text=True, timeout=50, check=False,
        )
        self.assertEqual(preview.returncode, 0, preview.stderr + preview.stdout)
        dryrun = json.loads(preview.stdout)
        self.assertEqual((dryrun["new"], dryrun["total"]), (1000, 1000))
        imported_products = worker("ImportProducts", {
            "path": str(csv_file), "sha256": dryrun["sha256"],
        })
        self.assertEqual(imported_products.returncode, 0,
                         imported_products.stderr + imported_products.stdout)
        self.assertIn("PRODUCT_IMPORT_OK", imported_products.stdout)
        self.assertEqual(drafts_page(store)["total"], 1000)
        self.assertEqual(drafts_page(store)["drafts"][0]["published"], False)
        exported_csv = state / "synthetische-artikel-export.csv"
        exported_products = worker("ExportProducts", {"path": str(exported_csv)})
        self.assertEqual(exported_products.returncode, 0,
                         exported_products.stderr + exported_products.stdout)
        self.assertIn("PRODUCT_EXPORT_OK", exported_products.stdout)
        self.assertTrue(exported_csv.is_file())
        self.assertEqual(drafts_page(store)["total"], 1000)
        self.assertEqual(store.tracked_ad_ids(), ("1234567890", "9876543210"))

if __name__ == "__main__":
    unittest.main()