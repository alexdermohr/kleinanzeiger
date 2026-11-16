"""Repeatable local imports; no seller storage or network access."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .bargain_hunter import (
    DealInputError, MAX_OFFERS, _DAMAGED, _observation_instant, assess_offers, load_offers, matches_model,
)
from .bargain_hunter_cli import render_report

APPLICATION_ID = 0x42524452


def atomic_report(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.radar-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(config_path: Path) -> dict:
    """One transaction per run, including report generation; fail closed on locks."""
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict):
        raise DealInputError('Radar-Konfiguration muss ein Objekt sein')
    model = config.get('model')
    if not isinstance(model, str) or not model.strip():
        raise DealInputError('Exaktes model im Profil erforderlich')
    base = config_path.resolve().parent

    def path(key):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise DealInputError(f'{key}: Pfad erforderlich')
        return (base / value).resolve()

    source, history, report = path('input'), path('history'), path('report')
    json_report = path('json_report') if 'json_report' in config else None
    files = sorted(p for p in source.iterdir() if p.is_file() and
                   p.suffix.lower() in ('.csv', '.json')) if source.is_dir() else [source]
    if not files:
        raise DealInputError('Keine CSV-/JSON-Eingaben gefunden')
    outputs = [history, report] + ([json_report] if json_report else [])
    protected = [config_path.resolve(), *[p.resolve() for p in files]]
    if len(set(outputs)) != len(outputs) or any(p in protected for p in outputs):
        raise DealInputError('Eingaben, Profil, Historie und Berichte müssen getrennt sein')
    if source.is_dir() and any(p.parent == source for p in outputs):
        raise DealInputError('Ausgaben müssen außerhalb des Eingangsordners liegen')
    offers = {}
    for file in files:
        for offer in load_offers(file, reject_conflicting_duplicates=True):
            old = offers.get(offer.uid)
            if old and old != offer:
                before = _observation_instant(old.observed_at)
                after = _observation_instant(offer.observed_at)
                if before is None or after is None or before == after:
                    raise DealInputError(f'Widersprüchliche Eingaben für ID {offer.uid}')
                if after < before:
                    continue
            offers[offer.uid] = offer
            if len(offers) > MAX_OFFERS:
                raise DealInputError('Zu viele Inserate im Eingang')
    min_peers = config.get('min_peers', 4)
    threshold = Decimal(str(config.get('discount_pct', 30))) / 100
    assess_offers([], model, min_peers=min_peers, discount_threshold=threshold)
    # Existing databases must carry our identity before any schema mutation.
    try:
        with history.open('xb'):
            pass
        created = True
    except FileExistsError:
        created = False
    with closing(sqlite3.connect(history, timeout=1)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        app_id = db.execute('PRAGMA application_id').fetchone()[0]
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if app_id != APPLICATION_ID and (not created or app_id or tables):
            raise DealInputError('Keine Radar-Historie: fremde Datenbank wird nicht verändert')
        if created:
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.commit()
            db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS radar_profile (profile TEXT NOT NULL)')
        profile = json.dumps([model, config.get('min_peers', 4), str(config.get('discount_pct', 30))])
        previous_profile = db.execute('SELECT profile FROM radar_profile').fetchone()
        if previous_profile and previous_profile[0] != profile:
            raise DealInputError('Geändertes Modell/Bewertungsprofil benötigt eigene Historie')
        if not previous_profile:
            db.execute('INSERT INTO radar_profile VALUES (?)', (profile,))
        db.execute('CREATE TABLE IF NOT EXISTS radar_current (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS radar_runs (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
        changed = {}
        current_offers = []
        for uid, offer in offers.items():
            # Profile-isolated history: unrelated or defective listings must not consume IDs.
            if not matches_model(offer.title, model) or _DAMAGED.search(offer.condition):
                continue
            current = json.loads(json.dumps(asdict(offer), default=str))
            payload = json.dumps(current, sort_keys=True)
            stored = db.execute('SELECT payload FROM radar_current WHERE id=?', (uid,)).fetchone()
            previous = json.loads(stored[0]) if stored else None
            if previous:
                before = _observation_instant(previous['observed_at'])
                after = _observation_instant(offer.observed_at)
                if before and (after is None or after < before):
                    continue
                price_changed = any(previous[k] != current[k]
                                    for k in ('price_eur', 'shipping_eur'))
                if price_changed and before and after == before:
                    raise DealInputError(f'Preiswiderspruch zum selben Zeitpunkt: {uid}')
                if price_changed:
                    changed[uid] = ('price_changed', previous['price_eur'], previous['shipping_eur'])
            else:
                changed[uid] = ('new', None, None)
            current_offers.append(offer)
            if stored is None or stored[0] != payload:
                db.execute('INSERT OR REPLACE INTO radar_current VALUES (?, ?)', (uid, payload))
        evaluated = assess_offers(current_offers, model, min_peers=min_peers,
                                  discount_threshold=threshold)
        events = []
        for item in evaluated:
            if item['id'] in changed:
                kind, previous_price, previous_shipping = changed[item['id']]
                events.append(dict(item, event=kind, previous_price_eur=previous_price,
                                   previous_shipping_eur=previous_shipping))
        generated = datetime.now(timezone.utc).isoformat()
        result = dict(model=model, generated_at=generated, reference_type='asking_prices',
                      imported=len(offers), new=sum(v[0] == 'new' for v in changed.values()),
                      price_changes=sum(v[0] == 'price_changed' for v in changed.values()),
                      hits=sum(e['classification'] == 'auffällig günstig' for e in events),
                      offers=events)
        db.execute('INSERT INTO radar_runs(payload) VALUES (?)',
                   (json.dumps(result, ensure_ascii=False),))
        # Successful run results remain available in the isolated history.
        display = [dict(e, notes=[f"Radar: {e['event']}; vorher Angebot/Versand: "
                                 f"{e['previous_price_eur']}/{e['previous_shipping_eur']} EUR", *e['notes']])
                   for e in events]
        page = render_report(model, display, generated_at=generated)
        summary = (f"<p>Radar: {result['new']} neue Inserate, {result['price_changes']} Preisänderungen, "
                   f"{result['hits']} neue auffällig günstige Treffer. Nur Änderungen dieses Laufs; "
                   "Vergleich aus aktuellen Eingabedateien. Keine Änderungen: keine neuen Treffer.</p>")
        page = page.replace('<section class="info">', '<section class="info">' + summary)
        if not events:
            page = page.replace('Keine Angebote passen eindeutig auf das gesuchte Modell. Modellbezeichnung oder Eingabedaten prüfen.',
                                'Keine neuen bewertbaren Inserate oder Preisänderungen für dieses Modell.')
        if json_report:
            atomic_report(json_report, json.dumps(result, ensure_ascii=False, indent=2))
        atomic_report(report, page)
    return result
