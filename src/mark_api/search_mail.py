"""Bounded, offline search-mail import. Headers never establish authenticity."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from .bargain_hunter import DealInputError, euro, matches_model, normalize_offer

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


def load_search_mail(source: Path, model: str, *, max_age_hours=48, now=None):
    if type(max_age_hours) is not int or not 1 <= max_age_hours <= 168:
        raise DealInputError('max_age_hours muss 1–168 sein')
    now = now or datetime.now(timezone.utc)
    if not source.is_dir() or source.is_symlink():
        raise DealInputError('Ein ausdrücklich gewählter EML-Eingangsordner ist erforderlich')
    # The opt-in is the selected local inbox; not one approval for each message.
    # From and Date are plausible mail-format signals, not proof of sender authenticity.
    files, diagnostics = [], []
    old_files = 0
    cutoff = (now - timedelta(hours=max_age_hours)).timestamp()
    for file in source.iterdir():
        if file.suffix.lower() != '.eml':
            continue
        # A mail program can move/delete a message between is_file and stat.
        # Diagnose that single file instead of suppressing every valid mail.
        try:
            if file.is_file() and not file.is_symlink() and file.stat().st_mtime < cutoff:
                old_files += 1
                continue
        except OSError as exc:
            diagnostics.append(dict(file=file.name, reason=str(exc)))
            continue
        files.append(file)
        if len(files) > MAX_FILES:
            raise DealInputError('Mehr als 100 aktuelle EML-Dateien')
    rows = []
    if old_files:
        diagnostics.append(dict(file='', reason=f'{old_files} alte EML-Dateien außerhalb des Zeitfensters ignoriert'))
    for file in sorted(files):
        try:
            if file.is_symlink() or not file.is_file():
                raise DealInputError('Nur reguläre EML-Dateien erlaubt')
            with file.open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise DealInputError('EML größer als 1 MB')
            digest = hashlib.sha256(raw).hexdigest()
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            from_headers = msg.get_all('From', [])
            if len(from_headers) != 1:
                raise DealInputError('Eindeutiger Absenderheader fehlt')
            _, sender = parseaddr(str(from_headers[0]))
            domain = sender.casefold().rpartition('@')[2]
            if not sender or not domain or not (
                    domain == 'kleinanzeigen.de' or domain.endswith('.kleinanzeigen.de')):
                raise DealInputError('Absenderheader passt nicht zu Kleinanzeigen (kein Echtheitsbeweis)')
            date_headers = msg.get_all('Date', [])
            if len(date_headers) != 1:
                raise DealInputError('Eindeutiger Datumsheader fehlt')
            try:
                observed = parsedate_to_datetime(str(date_headers[0]))
            except (ValueError, TypeError) as exc:
                raise DealInputError('Ungültiger Datumsheader') from exc
            if observed is None or observed.tzinfo is None or observed.utcoffset() is None:
                raise DealInputError('Zeitangabe braucht Zeitzone')
            observed = observed.astimezone(timezone.utc)
            if not now - timedelta(hours=max_age_hours) <= observed <= now:
                raise DealInputError('Mail ist alt, undatiert oder liegt in der Zukunft')
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
            # Validate field limits and price/url semantics before leaving the
            # per-EML exception handler; a single bad mail must not block peers.
            normalize_offer(row)
            rows.append(row)
        except (ValueError, OSError, UnicodeError, LookupError, OverflowError, RecursionError) as exc:
            diagnostics.append(dict(file=file.name, reason=str(exc)))
    if not files:
        diagnostics.append(dict(file='', reason='Keine EML-Dateien; keine Angebote'))
    return rows, diagnostics
