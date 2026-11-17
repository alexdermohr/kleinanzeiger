"""The single supported category: conservative title evidence, never model pricing."""
import re

from .bargain_hunter import _DAMAGED, _ACCESSORY

CATEGORY = 'silver-sacd-players'
LABEL = 'SACD-Player silber'
SEARCH_URL = 'https://www.kleinanzeigen.de/s-suchanfrage.html?keywords=SACD+Player+silber'


def rejection(title: str) -> str | None:
    text = title.casefold()
    if _DAMAGED.search(text) or _ACCESSORY.search(text):
        return 'Defekt, Suchanzeige oder Zubehör statt Player'
    if re.search(r'\bnd[\s-]*8006\b', text):
        return 'ND8006 unterstützt keine SACD-Disc-Wiedergabe'
    if re.search(r'\bsacd\s+(?:nicht|nein)\b|\b(?:disc|disk|tonträger|sacds)\b(?![ -]*(?:player|spieler))', text):
        return 'Tonträger oder widersprüchliche SACD-Disc-Angabe'
    if '?' in text or re.search(r'\b(?:möglicherweise|vielleicht|eventuell|vermutlich)\b|\b(?:disc|disk|scheibe)\s+für\b', text):
        return 'Unklare Angabe oder Tonträger statt Player'
    if re.search(r'\b(defekt\w*|bastler\w*|ersatzteil\w*|fernbedien\w*|remote\w*|lautsprecher\w*|zubehör|kabel\w*|laser\w*|laufwerk\w*|anleitung\w*|sammlung\w*|album\w*|hybrid\w*)\b', text):
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


def assess(offers):
    return [dict(id=o.uid, title=o.title, url=o.url, source=o.source,
                 observed_at=o.observed_at, price_eur=str(o.price_eur),
                 shipping_eur=str(o.shipping_eur) if o.shipping_eur is not None else None,
                 comparison_price_eur=None, peer_median_eur=None, peer_count=0,
                 discount_pct=None, classification='nicht bewertbar',
                 reason='Kategorie ohne Modellpreisvergleich',
                 notes=['SACD-Disc-Wiedergabe und Silber nur anhand des Titels plausibel.',
                        'Foto, Funktion und Originalanzeige selbst prüfen.']) for o in offers]
