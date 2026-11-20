"""Repeatable local imports; no seller storage or network access."""
from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .bargain_hunter import (
    DealInputError, MAX_OFFERS, _DAMAGED, _observation_instant, assess_offers, load_offers, matches_model, normalize_offer,
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


def run(config_path: Path, *, color_review: list[dict] | None = None,
        public_search: dict | None = None) -> dict:
    """One transaction per run, including report generation; fail closed on locks."""
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict):
        raise DealInputError('Radar-Konfiguration muss ein Objekt sein')
    model = config.get('model')
    if not isinstance(model, str) or not model.strip():
        raise DealInputError('Exaktes model im Profil erforderlich')
    from .silver_sacd import CATEGORY, assess, rejection
    category = config.get('category')
    if category is not None and (category != CATEGORY or config.get('input_type') not in ('eml', 'public')):
        raise DealInputError('Kategorie benötigt EML oder öffentliche Suche und die vordefinierte Suchkategorie')
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
    if config.get('input_type', 'exports') not in ('exports', 'eml', 'public'):
        raise DealInputError('Unbekannter input_type')
    mail_mode = config.get('input_type') == 'eml'
    public_mode = config.get('input_type') == 'public'
    # A separate private file preserves the immutable identity of existing
    # public profiles; manual price limits never become market-price evidence.
    targets = {}
    if category and public_mode:
        from .radar_watch import RadarWatch
        targets = RadarWatch(config_path.parent.parent).price_targets(config_path)
    color_review = color_review or []
    diagnostics = []
    mail_rows = []
    if mail_mode:
        from .search_mail import load_search_mail
        if type(config.get('notify', False)) is not bool:
            raise DealInputError('notify muss boolesch sein')
        if type(config.get('min_peers', 4)) is not int or not 4 <= config.get('min_peers', 4) <= 100:
            raise DealInputError('Suchmail benötigt mindestens vier Peers')
        if json_report is None:
            raise DealInputError('Suchmail benötigt json_report')
        mail_rows, diagnostics = load_search_mail(source, model,
            max_age_hours=config.get('max_age_hours', 48), category=category)
        files = []
    if not files and not mail_mode:
        raise DealInputError('Keine CSV-/JSON-Eingaben gefunden')
    outputs = [history, report] + ([json_report] if json_report else [])
    protected = [config_path.resolve(), *[p.resolve() for p in files]]
    if len(set(outputs)) != len(outputs) or any(p in protected for p in outputs):
        raise DealInputError('Eingaben, Profil, Historie und Berichte müssen getrennt sein')
    if source.is_dir() and any(p == source or source in p.parents for p in outputs):
        raise DealInputError('Ausgaben müssen außerhalb des Eingangsordners liegen')
    offers = {}
    batches = ([normalize_offer(row) for row in mail_rows],) if mail_mode else (
        load_offers(file, reject_conflicting_duplicates=True) for file in files)
    observations = {}
    for batch in batches:
        for offer in batch:
            key = (offer.uid, _observation_instant(offer.observed_at))
            prior = observations.get(key)
            if prior and any(getattr(prior, field) != getattr(offer, field) for field in
                    ('title', 'price_eur', 'url', 'shipping_eur', 'condition')):
                raise DealInputError(f'Widersprüchliche Eingaben für ID {offer.uid}')
            observations[key] = offer
            old = offers.get(offer.uid)
            if old and old != offer:
                before = _observation_instant(old.observed_at)
                after = _observation_instant(offer.observed_at)
                if before is None or after is None:
                    raise DealInputError(f'Widersprüchliche Eingaben für ID {offer.uid}')
                if before == after:
                    # Equal instants and substantive fields are the same listing,
                    # even if export timezone or source metadata differs.
                    if any(getattr(old, field) != getattr(offer, field) for field in
                           ('title', 'price_eur', 'url', 'shipping_eur', 'condition')):
                        raise DealInputError(f'Widersprüchliche Eingaben für ID {offer.uid}')
                    continue
                if after < before:
                    continue
            offers[offer.uid] = offer
            if len(offers) > MAX_OFFERS:
                raise DealInputError('Zu viele Inserate im Eingang')
    min_peers = config.get('min_peers', 4)
    threshold = Decimal(str(config.get('discount_pct', 30))) / 100
    if not category:
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
        if category:
            profile = json.dumps(['category', category, model])
        previous_profile = db.execute('SELECT profile FROM radar_profile').fetchone()
        if previous_profile and previous_profile[0] != profile:
            raise DealInputError('Geändertes Modell/Bewertungsprofil benötigt eigene Historie')
        if not previous_profile:
            db.execute('INSERT INTO radar_profile VALUES (?)', (profile,))
        db.execute('CREATE TABLE IF NOT EXISTS radar_current (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS radar_runs (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
        if public_mode and category:
            db.execute('CREATE TABLE IF NOT EXISTS radar_target_alerted ('
                       'id TEXT PRIMARY KEY, price_version TEXT NOT NULL)')
        if mail_mode or category:
            # Later exact-model peers can make an existing SACD offer assessable.
            # Persist the last alerted asking-price version,
            # not a speculative estimate or a repeated arrival timestamp.
            db.execute('CREATE TABLE IF NOT EXISTS radar_alerted ('
                       'id TEXT PRIMARY KEY, price_version TEXT NOT NULL)')
        changed = {}
        current_offers = []
        for uid, offer in offers.items():
            # Profile-isolated history: unrelated or defective listings must not consume IDs.
            if (rejection(offer.title) if category else not matches_model(offer.title, model)) or _DAMAGED.search(offer.condition):
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
        # A failed first report can leave a valid empty database file behind.
        # Only *committed* radar_runs prove that a public baseline ever completed.
        # Do not mistake its next retry for new market listings.
        first_public_baseline = bool(category and public_mode and db.execute(
                'SELECT 1 FROM radar_runs LIMIT 1'
        ).fetchone() is None)
        if first_public_baseline:
            changed.clear()
        known_candidates = []
        if category:
            known = []
            for (payload,) in db.execute('SELECT payload FROM radar_current ORDER BY id'):
                row = json.loads(payload)
                row['id'] = row.pop('uid')
                offer = normalize_offer(row)
                if not rejection(offer.title) and not _DAMAGED.search(offer.condition):
                    known.append(offer)
            known_candidates = assess(known, price_targets=targets)
            evaluated = known_candidates
        else:
            evaluated = assess_offers(current_offers, model, min_peers=min_peers,
                                      discount_threshold=threshold)
        events = []
        for item in evaluated:
            uid = item['id']
            change = changed.get(uid)
            cheap = item['classification'] == 'auffällig günstig'
            should_alert = False
            if (mail_mode or category) and cheap:
                if category:
                    price_version = json.dumps([item['price_eur'], item['shipping_eur']], ensure_ascii=False)
                else:
                    offer = offers[uid]
                    price_version = json.dumps(
                        [str(offer.price_eur), str(offer.shipping_eur)], ensure_ascii=False
                    )
                prior_alert = db.execute(
                    'SELECT price_version FROM radar_alerted WHERE id=?', (uid,)
                ).fetchone()
                if prior_alert is None or prior_alert[0] != price_version:
                    should_alert = True
                    db.execute('INSERT OR REPLACE INTO radar_alerted VALUES (?, ?)',
                               (uid, price_version))
            target_hit = bool(public_mode and category and item.get('personal_price_met'))
            target_alert = False
            if target_hit:
                # This is a user budget signal, NOT a claimed market discount.
                target_version = json.dumps([item['price_eur'], item['shipping_eur'],
                                             item['personal_price_target_eur']])
                previous_target = db.execute(
                    'SELECT price_version FROM radar_target_alerted WHERE id=?', (uid,)
                ).fetchone()
                if previous_target is None or previous_target[0] != target_version:
                    target_alert = True
                    db.execute('INSERT OR REPLACE INTO radar_target_alerted VALUES (?, ?)',
                               (uid, target_version))
            if first_public_baseline:
                # A complete first run acknowledges existing bargains without
                # notifying about listings that predate the initial baseline.
                should_alert = False
                target_alert = False
            if change:
                kind, previous_price, previous_shipping = change
                events.append(dict(item, event=kind, previous_price_eur=previous_price,
                                   previous_shipping_eur=previous_shipping))
            elif target_alert and not should_alert:
                events.append(dict(item, event='personal_price_target',
                                   previous_price_eur=None, previous_shipping_eur=None))
            elif should_alert:
                # The source mail is not new, but only *now* has >=min_peers
                # comparable observations arrived. Alert once per price version.
                events.append(dict(item, event='newly_assessable',
                                   previous_price_eur=None, previous_shipping_eur=None))
        generated = datetime.now(timezone.utc).isoformat()
        result = dict(model=model, generated_at=generated, reference_type='asking_prices',
                      imported=len(offers), new=sum(v[0] == 'new' for v in changed.values()),
                      price_changes=sum(v[0] == 'price_changed' for v in changed.values()),
                      hits=sum(e['classification'] == 'auffällig günstig' for e in events),
                      offers=events, diagnostics=diagnostics)
        if category:
            assessment_type = ('category_asking_prices' if any(
                item['peer_count'] >= 4 for item in known_candidates) else 'category_unpriced')
            result.update(reference_type=assessment_type, candidate_events=len(events),
                          known_candidates=known_candidates, known_candidate_count=len(known_candidates),
                          personal_price_targets=len(targets),
                          personal_price_hits=sum(bool(event.get('personal_price_met')) for event in events),
                          color_review=color_review if public_mode else [],
                          color_review_count=len(color_review) if public_mode else 0)
            if public_mode and public_search is not None:
                result['public_search'] = public_search
        db.execute('INSERT INTO radar_runs(payload) VALUES (?)',
                   (json.dumps(result, ensure_ascii=False),))
        # Successful run results remain available in the isolated history.
        display = [dict(e, notes=[f"Radar: {e['event']}; vorher Angebot/Versand: "
                                 f"{e['previous_price_eur']}/{e['previous_shipping_eur']} EUR", *e['notes']])
                   for e in events]
        page = render_report(model, display, generated_at=generated, category=bool(category))
        summary = (f"<p>Radar: {result['new']} neue Inserate, {result['price_changes']} Preisänderungen, "
                   f"{result['hits']} neue auffällig günstige Treffer. Nur Änderungen dieses Laufs; "
                   "Vergleich aus aktuellen Eingabedateien. Keine Änderungen: keine neuen Treffer.</p>")
        if category:
            # Persistent candidate overview must not erase the current run's
            # actual new/price-change explanation from the visible report.
            event_by_id = {event['id']: event for event in events}
            display = []
            for candidate in known_candidates:
                notes = ['Historisch beobachtet; Verfügbarkeit nicht erneut geprüft.']
                change = event_by_id.get(candidate['id'])
                if change and change['event'] == 'new':
                    notes.insert(0, 'Im letzten Lauf als neuer Kandidat erkannt.')
                elif change and change['event'] == 'price_changed':
                    old_price = change['previous_price_eur'] or 'unbekannt'
                    old_shipping = change['previous_shipping_eur'] or 'unbekannt'
                    new_shipping = candidate['shipping_eur'] or 'unbekannt'
                    notes.insert(0, f"Preis-/Versandänderung: zuvor Preis {old_price} EUR, "
                                    f"Versand {old_shipping} EUR; jetzt Preis {candidate['price_eur']} EUR, "
                                    f"Versand {new_shipping} EUR.")
                display.append(dict(candidate, notes=[*notes, *candidate['notes']]))
            page = render_report(model, display, generated_at=generated, category=True,
                                 color_review=color_review)
            summary = (f"<p>Kategorie: {len(events)} neue oder preiszielbezogene SACD-Ereignisse; "
                       f"{result['personal_price_hits']} unter persönlichem Modellpreislimit. "
                       "Historisch beobachtete Kandidaten; Verfügbarkeit nicht erneut geprüft. "
                       + ('Modellpreisvergleich nur bei mindestens vier frischen Angeboten desselben Modells. '
                          if assessment_type == 'category_asking_prices' else 'Keine Modellpreisbewertung. ')
                       + ('Öffentliche Suchseite; erster Abruf ist nur eine Bestandsaufnahme.' if public_mode and created else
                          'Öffentliche Suchseite; kein vollständiger Marktüberblick.' if public_mode else
                          'Nur lokale EML-Eingänge.') + '</p>')
        if diagnostics:
            page = page.replace('</main>', '<section class="info"><h2>Importdiagnostik</h2><pre>' +
                html.escape(json.dumps(diagnostics, ensure_ascii=False, indent=2)) + '</pre></section></main>')
        page = page.replace('<section class="info">', '<section class="info">' + summary, 1)
        if not events and not category:
            page = page.replace('Keine Angebote passen eindeutig auf das gesuchte Modell. Modellbezeichnung oder Eingabedaten prüfen.',
                                'Keine neuen bewertbaren Inserate oder Preisänderungen für dieses Modell.')
        if json_report:
            atomic_report(json_report, json.dumps(result, ensure_ascii=False, indent=2))
        atomic_report(report, page)
    # History commits before delivery: no repeat spam; a crash can lose delivery.
    if (mail_mode or public_mode) and config.get('notify', False) and (result.get('candidate_events', 0) if category else result['hits']):
        executable = shutil.which('notify-send')
        if executable:
            try:
                subprocess.run([executable, '--', 'Kleinanzeiger',
                    (f"{result['candidate_events']} SACD-Ereignisse; "
                     f"{result['hits']} nach Modellvergleich auffällig günstig, "
                     f"{result['personal_price_hits']} innerhalb eigener Modellpreisziele. Bericht: {report}"
                     if category and public_mode else
                     f"{result['candidate_events']} neue passende silberne SACD-Player / Preisänderungen. Bericht: {report}"
                     if category else f"{result['hits']} neue auffällig günstige Inserate. Bericht: {report}")],
                    check=True, timeout=10, capture_output=True)
            except (OSError, subprocess.SubprocessError):
                pass  # Reports/history remain authoritative, never retry delivery.
    return result