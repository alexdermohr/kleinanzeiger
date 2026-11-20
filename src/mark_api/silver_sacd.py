"""Conservative silver SACD title evidence and same-model asking-price comparison."""
import re
from datetime import datetime

from .bargain_hunter import _DAMAGED, _accessory_only

CATEGORY = 'silver-sacd-players'
LABEL = 'SACD-Player silber'
SEARCH_URL = 'https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=SACD+Player+silber'
# Rotating queries broaden coverage without increasing requests per timer run.
SEARCH_URLS = (SEARCH_URL,
               'https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=SACD+silber',
               'https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=SACD+Player')


def _extra_accessory_only(title: str) -> bool:
    # Accessories supplied with (or missing from) a full SACD player do not
    # turn the listing into an accessory-only sale.
    extra = re.compile(
        r'\b(?:fernbedien\w*|remote\w*|lautsprecher\w*|zubehör|kabel\w*|'
        r'laser\w*|laufwerk\w*|anleitung\w*|sammlung\w*|album\w*|hybrid\w*)\b',
        re.I,
    )
    for match in extra.finditer(title):
        prefix = title[:match.start()]
        if re.search(r'\b(?:mit|inkl(?:usive)?\.?|samt|und|ohne)\s*$', prefix, re.I):
            continue
        if re.search(r'\s\+\s*$', prefix):
            continue
        return True
    return False


def rejection(title: str) -> str | None:
    text = title.casefold()
    if _DAMAGED.search(text) or _accessory_only(text) or _extra_accessory_only(text):
        return 'Defekt, Suchanzeige oder Zubehör statt Player'
    if re.search(r'\bnd[\s-]*8006\b', text):
        return 'ND8006 unterstützt keine SACD-Disc-Wiedergabe'
    if re.search(r'\bsacd\s+(?:nicht|nein)\b|\b(?:disc|disk|tonträger|sacds)\b(?![ -]*(?:player|spieler))', text):
        return 'Tonträger oder widersprüchliche SACD-Disc-Angabe'
    if '?' in text or re.search(r'\b(?:möglicherweise|vielleicht|eventuell|vermutlich)\b|\b(?:disc|disk|scheibe)\s+für\b', text):
        return 'Unklare Angabe oder Tonträger statt Player'
    if re.search(r'\b(?:defekt\w*|bastler\w*|ersatzteil\w*)\b', text):
        return 'Defekt, Tonträger oder Zubehör statt eindeutigem Player'
    if re.search(r'\b(schwarz\w*|black\w*|unklar\w*)\b|\b(?:nicht|kein\w*|ohne)\s+(?:silber\w*|silver\w*|sacd)\b', text):
        return 'Widersprüchliche Farbe oder SACD-Angabe'
    if not re.search(r'\b(?:silber|silver)\w*\b', text):
        return 'Explizite Silberangabe im Mailtitel fehlt'
    if re.search(r'\b(?:cd[ -]*only|nur\s+(?:cd|dsd)|stream\w*|dsd[ -]*only)\b', text):
        return 'Keine eindeutige SACD-Disc-Wiedergabe'
    # SACD-Qualität is marketing, not evidence that the optical drive reads SACDs.
    if re.search(r'\bsacd[\s-]*(?:qualität|quality|klang|sound|niveau)\b', text):
        return 'SACD-Qualität belegt keine SACD-Disc-Wiedergabe'
    # Some actual SACD transports are advertised only with their model family,
    # e.g. "Denon DCD 1700 NE SACD Silber", without the English word "Player".
    model_player = re.search(r'\bdcd[\s-]*\d{3,4}(?:[\s-]*(?:ne|ae))?\b', text)
    if not re.search(r'\bsacd\b', text) or not (
            re.search(r'\b(?:player|spieler|abspieler)\b', text) or model_player):
        return 'SACD-Disc-fähiger Player im Titel nicht plausibel benannt'
    return None


# Price evidence is conditional on a recognisable manufacturer/model string,
# four OTHER fresh listings of that same model, and a matching condition.
# Unknown models never inherit category-wide reference prices.
_BRAND = re.compile(r'(?<!\w)(marantz|denon|yamaha|pioneer|sony|philips|onkyo|oppo|teac|accuphase|esoteric|luxman|nad|arcam|technics|t\+a)(?!\w)', re.I)
_MODEL = re.compile(r'\b(?:SCD(?:[\s-]?(?:XA|XB|XE))?|DV[\s-]?SP|DCD|DVD|DVP|CD[\s-]?S|CDS|SADV|BDP|PD|SA|UD|D)[\s-]?\d{2,5}[A-Z0-9]{0,4}\b', re.I)
_SUFFIX = re.compile(r'^[\s-]+(NE|AE|ES|E|R|S[1-9][0-9]?)\b', re.I)
_FRESH_SECONDS = 72 * 3600


def model_key(title: str) -> str | None:
    """Conservative manufacturer/model identity extracted from a listing title."""
    brands = {m.group(1).casefold() for m in _BRAND.finditer(title)}
    if len(brands) != 1:
        return None
    models = list(_MODEL.finditer(title))
    if len(models) != 1:
        return None
    match = models[0]
    token = re.sub(r'[\s-]+', '', match.group().upper())
    suffix = _SUFFIX.match(title[match.end():])
    if suffix:
        token += suffix.group(1).upper()
    return next(iter(brands)) + ':' + token


def _fresh(observed_at: str, now: datetime) -> bool:
    try:
        instant = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
        age = (now - instant).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return False
    return instant.tzinfo is not None and 0 <= age <= _FRESH_SECONDS


def assess(offers, *, price_targets=None):
    """Fresh same-model asking-price peers and separate user-defined price limits."""
    from bisect import bisect_left
    from datetime import datetime, timezone
    from decimal import Decimal
    now = datetime.now(timezone.utc)
    price_targets = price_targets or {}
    groups: dict[tuple[str, str], list[Decimal]] = {}
    keys: dict[str, tuple[str, str]] = {}
    for offer in offers:
        key = model_key(offer.title)
        if key is None or offer.price_eur is None or not _fresh(offer.observed_at, now):
            continue
        group = (key, offer.condition.casefold())
        keys[offer.uid] = group
        groups.setdefault(group, []).append(offer.price_eur)
    for values in groups.values():
        values.sort()
    output = []
    for o in sorted(offers, key=lambda item: (item.price_eur, item.title.casefold(), item.uid)):
        model = model_key(o.title)
        row = dict(id=o.uid, title=o.title, url=o.url, source=o.source,
                   observed_at=o.observed_at, price_eur=str(o.price_eur),
                   shipping_eur=str(o.shipping_eur) if o.shipping_eur is not None else None,
                   comparison_price_eur=None, peer_median_eur=None, peer_count=0,
                   discount_pct=None, classification='nicht bewertbar',
                   reason='Weniger als vier frische Vergleichsangebote desselben Modells',
                   model_key=model, personal_price_target_eur=None,
                   personal_price_met=False,
                   notes=['SACD-Disc-Wiedergabe und Silber nur anhand des Titels plausibel.',
                          'Foto, Funktion und Originalanzeige selbst prüfen.'])
        group = keys.get(o.uid)
        if group is not None:
            values = groups[group]
            peer_count = len(values) - 1
            if peer_count >= 4:
                remove = bisect_left(values, o.price_eur)
                at = lambda index: values[index if index < remove else index + 1]
                middle = peer_count // 2
                median = at(middle) if peer_count % 2 else (at(middle - 1) + at(middle)) / 2
                if median > 0:
                    raw_discount = Decimal(100) * (median - o.price_eur) / median
                    discount = raw_discount.quantize(Decimal('0.1'))
                    row.update(peer_count=peer_count,
                               peer_median_eur=f'{median:.2f}',
                               discount_pct=f'{discount:.1f}',
                               classification='auffällig günstig' if raw_discount >= 30 else 'preislich nicht auffällig',
                               reason='Median frischer Angebotspreise desselben Herstellers und Modells')
                    row['notes'].append('Referenz sind andere aktuelle Inserate, keine erzielten Verkaufspreise; Versand unbekannt.')
        target = price_targets.get(model) if model else None
        if target is not None:
            # Only a fresh, known listing can meet a personal price limit.
            amount = Decimal(target)
            row['personal_price_target_eur'] = f'{amount:.2f}'
            if o.price_eur is not None and _fresh(o.observed_at, now) and o.price_eur <= amount:
                row['personal_price_met'] = True
                if row['classification'] != 'auffällig günstig':
                    row['classification'] = 'persönliches Preisziel erreicht'
                    row['reason'] = 'Eigenes Preislimit erfüllt, kein statistischer Marktpreisbeleg'
                row['notes'].append(
                    'Persönliches Preislimit, keine Marktpreisreferenz oder bestätigte Ersparnis.')
            elif not _fresh(o.observed_at, now):
                row['notes'].append(
                    'Der letzte beobachtete Preis ist für einen aktuellen Preiszielalarm zu alt.')
        output.append(row)
    return output