"""Bounded, offline search-mail import. Headers never establish authenticity."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from .bargain_hunter import DealInputError, _observation_instant, euro, matches_model

MAX_FILES = 100
MAX_BYTES = 1_000_000


class Layout(HTMLParser):
    """Only a single title anchor followed by one price paragraph is supported."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens = []

    def handle_starttag(self, tag, attrs):
        self.tokens.append(('start', tag, attrs))

    def handle_endtag(self, tag):
        self.tokens.append(('end', tag))

    def handle_data(self, data):
        if data.strip():
            self.tokens.append(('text', data.strip()))


def body_row(text, kind):
    if kind == 'text/html':
        parser = Layout()
        parser.feed(text)
        t = parser.tokens
        if (len(t) != 6 or t[0][:2] != ('start', 'a') or
                len(t[0][2]) != 1 or t[0][2][0][0] != 'href' or
                t[1][0] != 'text' or t[2] != ('end', 'a') or
                t[3] != ('start', 'p', []) or t[4][0] != 'text' or t[5] != ('end', 'p')):
            raise DealInputError('HTML-Layout mehrdeutig oder nicht unterstützt')
        title, price, url = t[1][1], t[4][1], t[0][2][0][1]
    else:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) != 3:
            raise DealInputError('Textlayout benötigt genau Titel, EUR-Preis, Direktlink')
        title, price, url = lines
    if not re.fullmatch(r'\d+(?:[.,]\d{1,2})? (?:EUR|€)', price):
        raise DealInputError('Kein eindeutiger EUR-Festpreis (VB/Free ausgeschlossen)')
    amount = euro(price, required=True)
    if amount <= 0:
        raise DealInputError('Kein positiver Festpreis')
    parsed = urlsplit(url)
    match = re.fullmatch(r'/s-anzeige/[a-zA-Z0-9_-]+/([1-9][0-9]{0,19})(?:-[0-9]+-[0-9]+)?', parsed.path)
    if (parsed.scheme != 'https' or parsed.netloc not in ('www.kleinanzeigen.de', 'kleinanzeigen.de')
            or parsed.query or parsed.fragment or not match):
        raise DealInputError('Kein direkter sicherer Inseratlink; Tracking wird nicht aufgelöst')
    return dict(id=match[1], title=title, price_eur=str(amount), url=url)


def load_search_mail(source: Path, manifest: Path, model: str, *, max_age_hours=48, now=None):
    if type(max_age_hours) is not int or not 1 <= max_age_hours <= 168:
        raise DealInputError('max_age_hours muss 1–168 sein')
    now = now or datetime.now(timezone.utc)
    if manifest.stat().st_size > MAX_BYTES:
        raise DealInputError('Freigabemanifest zu groß')
    approvals = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if not isinstance(approvals, dict) or len(approvals) > MAX_FILES:
        raise DealInputError('Ungültiges Freigabemanifest')
    files = []
    for file in source.iterdir():
        if file.suffix.lower() == '.eml':
            files.append(file)
            if len(files) > MAX_FILES:
                raise DealInputError('Mehr als 100 EML-Dateien')
    rows, diagnostics = [], []
    for file in sorted(files):
        try:
            if file.is_symlink() or not file.is_file():
                raise DealInputError('Nur reguläre EML-Dateien erlaubt')
            with file.open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise DealInputError('EML größer als 1 MB')
            digest = hashlib.sha256(raw).hexdigest()
            approval = approvals.get(file.name)
            if (not isinstance(approval, dict) or approval.get('sha256') != digest or
                    approval.get('authorized_search_mail') is not True):
                raise DealInputError('Keine hashgebundene lokale Suchmail-Freigabe; Header unverifiziert')
            observed = _observation_instant(str(approval.get('observed_at', '')))
            if observed is None or not now - timedelta(hours=max_age_hours) <= observed <= now:
                raise DealInputError('Beobachtung fehlt, ist alt oder liegt in der Zukunft')
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            parts = list(msg.walk())
            if len(parts) > 20 or any(p.defects for p in parts):
                raise DealInputError('Fehlerhafte oder zu komplexe MIME-Mail')
            candidates = []
            for part in parts:
                if part.is_multipart():
                    if part.get_content_subtype() not in ('alternative', 'mixed'):
                        raise DealInputError('MIME-Typ nicht unterstützt')
                    continue
                if part.get_content_disposition() == 'attachment' or part.get_content_type() not in ('text/plain', 'text/html'):
                    raise DealInputError('Anhänge/andere MIME-Inhalte nicht unterstützt')
                candidates.append(body_row(part.get_content(), part.get_content_type()))
            if not candidates or any(row != candidates[0] for row in candidates):
                raise DealInputError('Multipart-Zuordnung widersprüchlich oder leer')
            row = candidates[0]
            if not matches_model(row['title'], model):
                raise DealInputError('Keine eindeutige passende Produktgruppe/Modellzuordnung')
            row.update(observed_at=observed.isoformat(), source='Lokale Suchmail SHA256:' + digest)
            rows.append(row)
        except (ValueError, OSError, UnicodeError, LookupError) as exc:
            diagnostics.append(dict(file=file.name, reason=str(exc)))
    if not files:
        diagnostics.append(dict(file='', reason='Keine EML-Dateien; keine Angebote'))
    return rows, diagnostics
