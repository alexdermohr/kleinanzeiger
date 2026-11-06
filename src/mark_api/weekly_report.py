"""Offline weekly aggregate from existing owner evidence; never syncs or sends."""
from __future__ import annotations
import argparse, os, sqlite3, sys, tempfile
from datetime import datetime
from html import escape
from pathlib import Path
from .container_draft import container_draft_denied
from .portfolio_reporting import PortfolioReportError, report
from .storage import SnapshotStore
from .windows_runtime import require_private_windows_directory

def _number(value:object)->str:
    if type(value) is not int or value<0: raise ValueError("invalid local metric")
    return str(value)

def render_weekly_html(data:dict)->str:
    if data["window_days"]!=7: raise ValueError("requires seven days")
    old=data["imported_previous_window"]
    sales=data["manual_sales_current_all_time"]
    return ('<!doctype html><html lang="de"><meta charset="utf-8"><title>'
      'Kleinanzeiger Wochenbericht</title><body><h1>Lokaler Wochenbericht</h1>'
      '<p>UTC-Tage: '+escape(str(data["start_day_utc"]))+' bis '+
      escape(str(data["end_day_utc"]))+'</p><p>Nur lokale importierte E-Mails, '
      'kein vollständiger Plattform-Posteingang. Kein Kontoabruf, kein Versand, '
      'keine KI-Kosten. Fehlende E-Mails beweisen keine fehlende Nachfrage.</p>'
      '<table><tr><th>Messwert</th><th>Letzte 7 Tage</th><th>Vorige 7 Tage</th></tr>'
      '<tr><td>Importierte Nachrichten</td><td>'+
      _number(data["imported_inbound_messages_window"])+'</td><td>'+
      _number(old["imported_inbound_messages"])+'</td></tr>'
      '<tr><td>Importierte Gespräche</td><td>'+
      _number(data["imported_conversations_window"])+'</td><td>'+
      _number(old["imported_conversations"])+'</td></tr></table>'
      '<h2>Aktueller Gesamtstand, nicht wochenbezogen</h2>'
      '<p>Eigene Anzeigen mit Bestandsbeleg: '+
      _number(data["own_ads_with_inventory_history"])+
      '; manuell verkauft: '+_number(sales["manually_sold"])+
      '; unbekannte Einstandskosten: '+_number(sales["unknown_cost_count"])+
      '</p><p>KI-Kosten nur lokale Schätzung, keine geprüfte Anbieterrechnung. '
      'Keine bestätigten Käuferzahlen oder Konversionsraten.</p></body></html>\n')

def generate_weekly_report(db:Path, output_dir:Path, *, now:datetime|None=None)->Path:
    db=Path(db)
    output_dir=Path(output_dir)
    if not db.is_file(): raise FileNotFoundError("local database missing")
    data=report(SnapshotStore(db,create_if_missing=False,require_existing_schema=True),
                days=7,now=now)
    html=render_weekly_html(data)
    if os.name=="nt": require_private_windows_directory(output_dir,create=True)
    else: output_dir.mkdir(parents=True,exist_ok=True)
    target=output_dir/("Kleinanzeiger-Wochenbericht-"+str(data["end_day_utc"])+".html")
    fd,tmp=tempfile.mkstemp(prefix=".weekly-",suffix=".tmp",dir=output_dir)
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as stream:
            stream.write(html)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return target

def main(argv:list[str]|None=None)->int:
    if container_draft_denied():
        print("Kleinanzeiger: Nicht freigegebene Laufzeit.",file=sys.stderr)
        return 2
    parser=argparse.ArgumentParser()
    parser.add_argument("--db",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args(argv)
    try: output=generate_weekly_report(args.db,args.output_dir)
    except (OSError,sqlite3.Error,ValueError,KeyError,TypeError,PortfolioReportError):
        print("Kleinanzeiger: Lokaler Wochenbericht fehlgeschlagen.",file=sys.stderr)
        return 2
    print("WEEKLY_REPORT_OK "+output.name)
    return 0

if __name__=="__main__": raise SystemExit(main())
