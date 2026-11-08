"""Offline buyer-side CLI: python -m mark_api.bargain_hunter_cli."""
from __future__ import annotations

import argparse
import html
import json
import sys
import webbrowser
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .bargain_hunter import DealInputError, assess_offers, load_offers, search_url


def render_report(model: str, offers: list[dict], *, generated_at: str) -> str:
    esc = lambda text: html.escape(str(text), quote=True)
    count = sum(r["classification"] == "auffällig günstig" for r in offers)
    rows = []
    for r in offers:
        status = r["classification"]
        discount = (r["discount_pct"] + " % unter dem Angebotsmedian"
                    if r["discount_pct"] is not None else r["reason"])
        median = (r["peer_median_eur"] + " €"
                  if r["peer_median_eur"] is not None else "Nicht bestimmbar")
        price = (r["price_eur"] + " €" if r["price_eur"] is not None else "Unbekannt")
        label = "Treffer prüfen" if status == "auffällig günstig" else "Angebot öffnen"
        rows.append(
            "<article class='offer'><header>"
            f"<span class='classification'>{esc(status)}</span>"
            f"<h2>{esc(r['title'])}</h2></header>"
            f"<div class='values'><strong>{esc(price)}</strong>"
            f"<span>{esc(discount)}</span></div>"
            f"<p>Vergleichsmedian (Angebotspreise): {esc(median)}"
            f" · {r['peer_count']} passende Vergleichsangebote</p>"
            f"<p class='minor'>Quelle: {esc(r['source'])}"
            f" · Beobachtet: {esc(r['observed_at'])}"
            f" · Inserat-ID: {esc(r['id'])}</p>"
            f"<p class='minor'>{esc('; '.join(r['notes']))}</p>"
            f"<a href='{esc(r['url'])}' target='_blank' rel='noopener noreferrer'>"
            f"{label}</a></article>"
        )
    body = "".join(rows) if rows else (
        "<p class='empty'>Keine Angebote passen eindeutig auf das gesuchte Modell. "
        "Modellbezeichnung oder Eingabedaten prüfen.</p>"
    )
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kleinanzeiger – Schnäppchenjäger</title>
<style>
:root{{font-family:system-ui,"Segoe UI",Arial,sans-serif;line-height:1.5;color:#243442;background:#f1f5f7}}
*{{box-sizing:border-box}}main{{max-width:960px;margin:auto;padding:24px}}h1{{font-size:1.7rem}}
.info,.offer{{background:#fff;border:1px solid #d7e1e6;border-radius:12px;padding:18px 22px;margin:14px 0}}
.offer h2{{font-size:1.12rem;margin:8px 0}}.classification{{font-size:.81rem;font-weight:700;color:#245b68}}
.values{{display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}}.values strong{{font-size:1.35rem}}
.minor{{font-size:.84rem;color:#5b6c76}}a{{color:#036c86}}a:focus-visible{{outline:3px solid #0c7c9c}}
.empty{{padding:30px}}@media(max-width:600px){{main{{padding:12px}}}}
</style></head><body><main><h1>Kleinanzeiger · Schnäppchenjäger</h1>
<section class="info"><strong>{esc(model)}</strong>
<p>{len(offers)} passende Angebote, davon {count} auffällig günstig.</p>
<p><small>Erstellt: {esc(generated_at)}. Dies ist eine Auswertung von beobachteten <b>Angebotspreisen</b>,
keine bestätigten Verkaufspreise und keine Garantie für Echtheit, Zustand oder Ersparnis.
Bei fehlenden Vergleichsdaten gibt es ausdrücklich keine Schnäppchenwertung.</small></p></section>
{body}</main></body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kleinanzeiger-schnäppchenjäger",
        description="Nutzerdatei lokal mit vergleichbaren Angebotspreisen auswerten; kein Scraping.",
    )
    parser.add_argument("--datei", type=Path, help="Export mit Angeboten (.csv/.json)")
    parser.add_argument("--modell", required=True, help="Exakte Modellbezeichnung, z.B. 'Lenovo ThinkPad T14 Gen 3'")
    parser.add_argument("--bericht", type=Path, default=Path("schnäppchenbericht.html"))
    parser.add_argument("--json", action="store_true", help="Strukturiertes Ergebnis auf stdout")
    parser.add_argument("--oeffnen", action="store_true", help="HTML-Bericht im Browser öffnen")
    parser.add_argument("--suchlink", action="store_true", help="Passende Kleinanzeigen-Suchadresse ausgeben")
    parser.add_argument("--hoechstpreis", type=int, default=None, help="Nur Suchlink: Maximalpreis in EUR")
    parser.add_argument("--mindest-vergleiche", type=int, default=4)
    parser.add_argument("--abschlag", default="30", help="Schwelle in Prozent (Standard: 30)")
    args = parser.parse_args(argv)
    try:
        if args.suchlink:
            print(search_url(args.modell, maximum_eur=args.hoechstpreis))
            if not args.datei:
                return 0
        if not args.datei:
            parser.error("--datei wird benötigt (oder --suchlink für manuelle Suche)")
        try:
            threshold = Decimal(args.abschlag) / 100
        except InvalidOperation as exc:
            raise DealInputError("Ungültige Abschlagsschwelle") from exc
        offers = assess_offers(
            load_offers(args.datei), args.modell,
            min_peers=args.mindest_vergleiche, discount_threshold=threshold,
        )
        if args.json:
            print(json.dumps({"model": args.modell,
                              "reference_type": "asking_prices",
                              "offers": offers}, ensure_ascii=False, indent=2))
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        args.bericht.write_text(render_report(args.modell, offers, generated_at=generated),
                                encoding="utf-8")
        print(f"Bericht gespeichert: {args.bericht}", file=sys.stderr)
        if args.oeffnen:
            webbrowser.open(args.bericht.resolve().as_uri())
        return 0
    except (DealInputError, OSError) as exc:
        print(f"Schnäppchenjäger: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
