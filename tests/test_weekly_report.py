"""Local weekly summaries remain aggregate-only and never create missing DBs."""
from datetime import datetime,timezone
from pathlib import Path
import tempfile,unittest
from mark_api.domain import AdSnapshot,InboundMessageEvent,LifecycleState
from mark_api.storage import SnapshotStore
from mark_api.weekly_report import generate_weekly_report,render_weekly_html,main
NOW=datetime(2026,11,9,9,0,tzinfo=timezone.utc)
class WeeklyReportTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory(prefix="kleinanzeiger-weekly-")
  self.addCleanup(tmp.cleanup)
  self.db=Path(tmp.name)/"anzeigen.sqlite"
  self.out=Path(tmp.name)/"Berichte"
 def test_missing_database(self):
  with self.assertRaises(FileNotFoundError): generate_weekly_report(self.db,self.out,now=NOW)
  self.assertFalse(self.db.exists())
  self.assertFalse(self.out.exists())
  self.assertEqual(main(["--db",str(self.db),"--output-dir",str(self.out)]),2)
 def test_aggregate_only_and_idempotent(self):
  s=SnapshotStore(self.db,create_if_missing=True)
  s.append_ad_snapshot(AdSnapshot(ad_id="ad-secret",title="title-secret",lifecycle_state=LifecycleState.ACTIVE,observed_at=NOW,source="private-web-product-launcher"))
  s.append_inbound_message_events([InboundMessageEvent(ad_id="ad-secret",conversation_id="conv-secret",provider_message_id="msg-secret",observed_at=NOW,source="test-synthetic-email")])
  path=generate_weekly_report(self.db,self.out,now=NOW)
  content=path.read_text(encoding="utf-8")
  self.assertEqual(path.name,"Kleinanzeiger-Wochenbericht-2026-11-09.html")
  self.assertIn("Importierte Nachrichten</td><td>1</td><td>0",content)
  self.assertIn("Bestandsbeleg: 1",content)
  for secret in ["ad-secret","title-secret","conv-secret","msg-secret"]: self.assertNotIn(secret,content)
  self.assertEqual(path,generate_weekly_report(self.db,self.out,now=NOW))
  self.assertEqual(content,path.read_text(encoding="utf-8"))
  self.assertEqual(len(list(self.out.iterdir())),1)
 def test_reject_unsupported_window(self):
  with self.assertRaises(ValueError): render_weekly_html({"window_days":30})
