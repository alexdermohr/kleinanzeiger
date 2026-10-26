"""Explicit local portfolio-job commands; no paid AI calls or platform writes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import sys

from .portfolio_jobs import (
    PortfolioJobError, apply_approved_local, change_state, create_job, details,
    list_jobs, recover_interrupted, review_items, settle_unknown,
)
from .storage import SnapshotStore


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(
        description="Lokale P2-KI-Auftragsverwaltung (ohne automatische KI-Kosten/Plattform-Writes).")
    p.add_argument("--db",required=True,type=Path,help="Vorhandene Kleinanzeiger SQLite-Datei")
    cmds=p.add_subparsers(dest="command",required=True)
    create=cmds.add_parser("create",help="Artikel-SKUs einfrieren und Auftrag anlegen")
    sel=create.add_mutually_exclusive_group(required=True)
    sel.add_argument("--skus-file",type=Path,help="UTF-8 Datei, eine Artikelnummer je Zeile")
    sel.add_argument("--sku",action="append",dest="skus",help="Artikelnummer (mehrfach)")
    create.add_argument("--instruction",required=True)
    create.add_argument("--budget-cents",type=int,required=True)
    create.add_argument("--estimate-cents",type=int,required=True)
    create.add_argument("--request-key",help="64 Zeichen SHA-256 (Idempotenz)")
    create.add_argument("--new-request",action="store_true",
                        help="Absichtlich neuen Auftrag mit eigener ID erzeugen")
    show=cmds.add_parser("list",help="Letzte 50 lokale Aufträge anzeigen")
    show.add_argument("--limit",type=int,default=50)
    detail=cmds.add_parser("detail",help="Auftrag mit Original-/Vorschlagsdaten")
    detail.add_argument("job_id")
    detail.add_argument("--after",default="")
    detail.add_argument("--limit",type=int,default=50)
    for name in ("start","pause","resume","cancel","recover","apply-local"):
        sub=cmds.add_parser(name)
        sub.add_argument("job_id")
    for name in ("approve","reject"):
        sub=cmds.add_parser(name)
        sub.add_argument("job_id")
        sub.add_argument("--sku",action="append",required=True,help="Freizugebende SKUs")
    settled=cmds.add_parser("settle-unknown")
    settled.add_argument("job_id")
    settled.add_argument("--sku",required=True)
    settled.add_argument("--actual-cents",type=int,required=True)
    settled.add_argument("--checked",action="store_true",
                         help="Explizite Bestätigung nach Abgleich mit dem Anbieter")
    return p


def _selection(args: argparse.Namespace) -> list[str]:
    if args.skus is not None:
        return args.skus
    path=args.skus_file
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise PortfolioJobError("selection file must be an absolute regular file")
    if path.stat().st_size>512*1024:
        raise PortfolioJobError("selection file exceeds 512 KiB")
    contents=path.read_text(encoding="utf-8-sig")
    return [v.strip() for v in contents.splitlines()]


def main(argv: list[str] | None=None) -> int:
    args=parser().parse_args(argv)
    if args.command=="create" and args.new_request and args.request_key:
        print("portfolio-jobs: choose either --new-request or --request-key",file=sys.stderr)
        return 2
    try:
        # This must never create a replacement database on a customer machine.
        store=SnapshotStore(args.db,create_if_missing=False,require_existing_schema=True)
        if args.command=="create":
            skus=_selection(args)
            fingerprint=json.dumps(
                [sorted(skus),args.instruction.strip(),args.budget_cents,
                 args.estimate_cents],sort_keys=True,separators=(",",":"),
                ensure_ascii=True,
            )
            key=(secrets.token_hex(32) if args.new_request else
                 args.request_key or hashlib.sha256(fingerprint.encode()).hexdigest())
            output=create_job(store,skus,instruction=args.instruction,request_key=key,
                              budget_cents=args.budget_cents,
                              estimate_cents=args.estimate_cents)
        elif args.command=="list":
            output={"jobs":list_jobs(store,limit=args.limit)}
        elif args.command=="detail":
            output=details(store,args.job_id,after=args.after,limit=args.limit)
        elif args.command in ("start","pause","resume","cancel"):
            output=change_state(store,args.job_id,args.command)
        elif args.command=="recover":
            output=recover_interrupted(store,args.job_id)
        elif args.command=="apply-local":
            output=apply_approved_local(store,args.job_id)
        elif args.command in ("approve","reject"):
            output=review_items(store,args.job_id,args.sku,
                                approve=args.command=="approve")
        elif args.command=="settle-unknown":
            output=settle_unknown(store,args.job_id,args.sku,
                                  actual_cents=args.actual_cents,
                                  explicitly_checked=args.checked)
        else:
            raise PortfolioJobError("unsupported command")
    except (PortfolioJobError,ValueError,UnicodeError,OSError,sqlite3.Error) as exc:
        # No tracebacks, credentials, local file paths or original input text.
        if isinstance(exc,PortfolioJobError):
            print("portfolio-jobs: "+str(exc),file=sys.stderr)
        else:
            print("portfolio-jobs: database or input unavailable",file=sys.stderr)
        return 2
    print(json.dumps(output,ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
