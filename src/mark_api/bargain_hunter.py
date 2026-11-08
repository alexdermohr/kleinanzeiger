"""Conservative, offline buyer-side price comparisons.

This module never accesses Kleinanzeigen, user sessions or seller accounts. Input
must come from a user-supplied export or an independently authorized data source.
All reference prices are *asking prices*, never confirmed sale prices.
"""
from __future__ import annotations

import csv
import io
import json
import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode, urlsplit

MAX_INPUT_BYTES = 8_000_000
MAX_OFFERS = 10_000
MAX_PRICE = Decimal("10000000")
_MODEL_WORDS = re.compile(r"[a-z0-9]+")
_GENERATION = re.compile(r"\bgen(?:eration)?\s*(\d{1,2})\b", re.I)
_DAMAGED = re.compile(
    r"\b(defekt|kaputt|ersatzteil(?:e|spender)?|reparaturbedürftig|"
    r"ohne\s+(?:funktion|display|akku)|suchanzeige|gesucht)\b", re.I
)
_ACCESSORY = re.compile(
    r"\b(hülle|huelle|case|cover|schutzfolie|panzerglas|tasche|ladegerät|"
    r"ladekabel|halterung|ersatzdisplay|displayglas|verpackung)\b", re.I
)
_ALLOWED_HOSTS = frozenset(("kleinanzeigen.de", "www.kleinanzeigen.de"))


class DealInputError(ValueError):
    """Invalid data, distinct from an inconclusive price estimate."""


def euro(value: object, *, required: bool = False) -> Decimal | None:
    """Parse EUR safely; reject booleans, NaN, free-text and negative prices."""
    if value is None or value == "":
        if required:
            raise DealInputError("Preis fehlt")
        return None
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise DealInputError("Ungültiger Preis")
    raw = str(value).strip().replace("\N{NO-BREAK SPACE}", " ")
    if len(raw) > 50:
        raise DealInputError("Preis zu lang")
    raw = re.sub(r"\s*(?:€|EUR)\s*$", "", raw, flags=re.I).strip()
    if not raw:
        raise DealInputError("Ungültiger Preis")
    if "," in raw:
        if not re.fullmatch(r"\d{1,3}(?:\.\d{3})*,\d{1,2}|\d+,\d{1,2}", raw):
            raise DealInputError("Ungültiger Dezimalpreis")
        raw = raw.replace(".", "").replace(",", ".")
    elif not re.fullmatch(r"\d+(?:\.\d{1,2})?", raw):
        raise DealInputError("Ungültiger Preis")
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise DealInputError("Ungültiger Preis") from exc
    if not amount.is_finite() or amount < 0 or amount > MAX_PRICE:
        raise DealInputError("Preis außerhalb des Bereichs")
    return amount.quantize(Decimal("0.01"))


def safe_listing_url(raw: object) -> str:
    value = str(raw or "").strip()
    if len(value) > 1500:
        raise DealInputError("URL zu lang")
    if any(char in value for char in "\r\n\t"):
        raise DealInputError("Ungültige Steuerzeichen in URL")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https" and parsed.hostname in _ALLOWED_HOSTS
            and not parsed.username and not parsed.password
            and parsed.port is None and parsed.path.startswith("/s-anzeige/")
        )
    except ValueError:
        valid = False
    if not valid:
        raise DealInputError("Nur HTTPS-Kleinanzeigen-Inseratlinks zulässig")
    return value


@dataclass(frozen=True, slots=True)
class Offer:
    uid: str
    title: str
    price_eur: Decimal | None
    url: str
    source: str
    observed_at: str
    shipping_eur: Decimal | None = None
    condition: str = "unbekannt"

    @property
    def comparison_price(self) -> Decimal | None:
        if self.price_eur is None:
            return None
        return self.price_eur + (self.shipping_eur or Decimal(0))


def _string(value: object, *, name: str, size: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > size:
        raise DealInputError(f"{name} fehlt oder ist zu lang")
    return value.strip()


def normalize_offer(row: dict, *, source: str = "Nutzerimport",
                    observed_at: str = "nicht angegeben") -> Offer:
    if not isinstance(row, dict):
        raise DealInputError("Inserat muss ein Objekt sein")
    identifier = row.get("id", row.get("ad_id", ""))
    uid = str(identifier).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", uid):
        raise DealInputError("Ungültige Inserat-ID")
    title = _string(row.get("title"), name="Titel", size=350)
    url = safe_listing_url(row.get("url", row.get("ad_url")))
    amount = row.get("price_eur", row.get("priceEuros", row.get("price")))
    if isinstance(amount, dict):
        if amount.get("currency_code", "EUR") != "EUR":
            raise DealInputError("Nur EUR-Preise zulässig")
        amount = amount.get("amount")
    raw_shipping = row.get("shipping_eur", None)
    cond = str(row.get("condition") or "unbekannt").strip().casefold()
    if len(cond) > 50:
        raise DealInputError("Zustand zu lang")
    source_value = _string(str(row.get("source") or source), name="Quelle", size=120)
    time_value = _string(str(row.get("observed_at") or observed_at),
                         name="Beobachtungszeit", size=80)
    return Offer(uid, title, euro(amount), url, source_value, time_value,
                 euro(raw_shipping), cond)


def _observation_instant(value: str) -> datetime | None:
    """Timezone-aware ISO instants only. Unknown/naive timestamps are not dated."""
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if date.utcoffset() is None:
        return None
    return date.astimezone(timezone.utc)


def load_offers(path: str | Path) -> list[Offer]:
    """Import user-provided CSV, plain JSON list or REST data.ads JSON.

    No URL is fetched. The file limit prevents unbounded memory/HTML reports.
    """
    file = Path(path)
    if file.suffix.casefold() not in (".csv", ".json"):
        raise DealInputError("Nur .csv oder .json erlaubt")
    if file.stat().st_size > MAX_INPUT_BYTES:
        raise DealInputError("Datei ist zu groß")
    try:
        data = file.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise DealInputError("UTF-8-Datei erforderlich") from exc
    if file.suffix.casefold() == ".csv":
        try:
            reader = csv.DictReader(io.StringIO(data), strict=True)
            if (not reader.fieldnames or len(reader.fieldnames) > 30
                    or len(set(reader.fieldnames)) != len(reader.fieldnames)):
                raise DealInputError("Ungültiger oder doppelter CSV-Header")
            rows = list(reader)
        except csv.Error as exc:
            raise DealInputError("Beschädigte CSV-Datei") from exc
    else:
        try:
            payload = json.loads(data)
        except ValueError as exc:
            raise DealInputError("Ungültiges JSON") from exc
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            container = payload.get("data", payload)
            if not isinstance(container, dict):
                raise DealInputError("Ungültige JSON-Antwort")
            rows = container.get("ads", container.get("listings"))
        else:
            rows = None
    if not isinstance(rows, list) or len(rows) > MAX_OFFERS:
        raise DealInputError("Inseratliste fehlt oder ist zu groß")
    deduplicated: dict[str, Offer] = {}
    for number, row in enumerate(rows, start=1):
        try:
            offer = normalize_offer(row)
        except DealInputError as exc:
            raise DealInputError(f"Zeile {number}: {exc}") from exc
        # Compare actual instants, not lexical offset strings.
        # A dated record outranks an unknown/naive timestamp; when neither
        # is dated, the last input row wins deterministically.
        earlier = deduplicated.get(offer.uid)
        current_time = _observation_instant(offer.observed_at)
        previous_time = _observation_instant(earlier.observed_at) if earlier else None
        if (earlier is None or
                (current_time is not None and
                 (previous_time is None or current_time >= previous_time)) or
                (current_time is None and previous_time is None)):
            deduplicated[offer.uid] = offer
    return list(deduplicated.values())


def _tokens(text: str) -> set[str]:
    # S23+ is the Plus edition; don't confuse it with plain S23.
    normalized = re.sub(r"(?<=\d)\+(?!\w)", " plus", text.casefold())
    return set(_MODEL_WORDS.findall(normalized))


_CPU = re.compile(r"\b(?:i[3579](?:[-\s]\d{3,5}[a-z0-9]{0,4})?|ryzen\s*[3579](?:[-\s]*\d{3,5}[a-z0-9]{0,4})?|core\s*ultra\s*[579](?:\s*\d{3}[a-z0-9]{0,4})?|m[1234](?:\s*(?:pro|max|ultra))?)\b", re.I)
_RAM = re.compile(r"\b(\d{1,3})\s*(?:gb|gbyte)\s*(?:ram|arbeitsspeicher|ddr[345]|memory)\b", re.I)
_STORAGE = re.compile(
    r"\b(\d{2,4})\s*gb\s*(?:ssd|hdd)\b|\b(\d{1,2})\s*tb\s*(?:ssd|hdd)\b", re.I
)
_STORAGE_MEDIUM_FIRST = re.compile(
    r"\b(ssd|hdd)\s*(?:(\d{2,4})\s*gb|(\d{1,2})\s*tb)\b", re.I
)
_PHONE_CAPACITY = re.compile(r"\b(64|128|256|512|1024)\s*gb\b", re.I)


def _variant_key(title: str, model: str) -> tuple[str, str, str]:
    """Only title-evidenced specs. Unknown is distinct from a known variant."""
    cpu = _CPU.search(title)
    ram = _RAM.search(title)
    storage = _STORAGE.search(title)
    reversed_storage = _STORAGE_MEDIUM_FIRST.search(title)
    phone = _PHONE_CAPACITY.search(title)
    size = ""
    if storage:
        capacity = (str(int(storage.group(1))) if storage.group(1)
                    else str(int(storage.group(2)) * 1024))
        medium = "ssd" if storage.group().casefold().endswith("ssd") else "hdd"
        size = capacity + "gb:" + medium
    elif reversed_storage:
        capacity = (str(int(reversed_storage.group(2))) if reversed_storage.group(2)
                    else str(int(reversed_storage.group(3)) * 1024))
        size = capacity + "gb:" + reversed_storage.group(1).casefold()
    elif phone:
        size = phone.group(1) + "gb"
    return (
        re.sub(r"[\s-]+", "", cpu.group().casefold()) if cpu else "",
        (ram.group(1) if ram else ""),
        size,
    )


def _accessory_only(title: str) -> bool:
    """Reject accessory offers, not devices supplied with accessories.

    A connector immediately before an accessory noun proves a bundle/absence.
    A bare accessory noun, including one at the start, stays excluded.
    """
    for accessory in _ACCESSORY.finditer(title):
        prefix = title[:accessory.start()]
        if re.search(r"\b(?:mit|inkl(?:usive)?\.?|samt|und|ohne)\s*$", prefix, re.I):
            continue
        if re.search(r"\s\+\s*$", prefix):
            continue
        return True
    return False


def matches_model(title: str, exact_model: str) -> bool:
    """Fail conservatively on mismatched generations, editions and conditions."""
    expected = _tokens(exact_model)
    found = _tokens(title)
    if not expected or len(expected) > 16 or not expected.issubset(found):
        return False
    requested_generation = _GENERATION.search(exact_model)
    if requested_generation:
        listing_generation = _GENERATION.search(title)
        if not listing_generation or listing_generation.group(1) != requested_generation.group(1):
            return False
    if _DAMAGED.search(title) or _accessory_only(title):
        return False
    # Phone editions are distinct models; an unspecified edition is not a match.
    if expected & {"iphone", "samsung", "galaxy", "pixel", "redmi"}:
        for suffix in ("pro", "max", "plus", "mini", "ultra", "fe", "xl"):
            if suffix in found and suffix not in expected:
                return False
    return True


def _peer_median(prices: list[Decimal], excluded: list[int]) -> Decimal:
    """Median of a sorted group without this ID, without copying the group."""
    count = len(prices) - len(excluded)

    def at(rank: int) -> Decimal:
        low, high = rank, rank + len(excluded)
        while low < high:
            middle = (low + high) // 2
            if middle + 1 - bisect_right(excluded, middle) <= rank:
                low = middle + 1
            else:
                high = middle
        return prices[low]

    return ((at((count - 1) // 2) + at(count // 2)) / 2).quantize(Decimal("0.01"))


def assess_offers(offers: list[Offer], exact_model: str, *,
                  min_peers: int = 4,
                  discount_threshold: Decimal = Decimal("0.30")) -> list[dict]:
    """Rank observed offer prices, without pretending these are sale prices.

    Excludes the candidate from its own median and never uses a model mismatch.
    Inconclusive rows are present but cannot be called bargains.
    """
    model = _string(exact_model, name="Exaktes Modell", size=160)
    if (type(min_peers) is not int or not 3 <= min_peers <= 100
            or not isinstance(discount_threshold, Decimal)
            or not discount_threshold.is_finite()
            or not Decimal("0") < discount_threshold < Decimal("1")):
        raise DealInputError("Ungültige Bewertungsgrenzen")
    matching = [o for o in offers if matches_model(o.title, model)
                and not _DAMAGED.search(o.condition)]
    groups = defaultdict(list)
    prepared = []
    for offer in matching:
        key = (_variant_key(offer.title, model), offer.condition)
        own = offer.comparison_price
        prepared.append((offer, key, own))
        if own is not None and own > 0:
            groups[key].append((own, offer.uid))
    comparisons = {}
    for key, entries in groups.items():
        entries.sort()
        positions = defaultdict(list)
        for index, (_, uid) in enumerate(entries):
            positions[uid].append(index)
        comparisons[key] = ([price for price, _ in entries], positions)
    ranked: list[dict] = []
    for offer, key, own in prepared:
        prices, positions = comparisons.get(key, ([], {}))
        excluded = positions.get(offer.uid, [])
        peer_count = len(prices) - len(excluded)
        reason = ""
        median = None
        discount = None
        if own is None or own <= 0:
            reason = "Kein belastbarer positiver Angebotspreis"
        elif peer_count < min_peers:
            reason = f"Nur {peer_count} vergleichbare Angebote; mindestens {min_peers} erforderlich"
        else:
            median = _peer_median(prices, excluded)
            if median > 0:
                discount = ((median - own) / median * 100).quantize(Decimal("0.1"))
        flags = []
        if offer.shipping_eur is None:
            flags.append("Versandkosten nicht belegt")
        if offer.condition == "unbekannt":
            flags.append("Zustand nicht belegt")
        flags.append("Vergleich nur mit Angebotspreisen, nicht mit Verkaufspreisen")
        flags.append("Ausstattung nur anhand belegter Titelmerkmale verglichen")
        ranked.append({
            "id": offer.uid, "title": offer.title, "url": offer.url,
            "source": offer.source, "observed_at": offer.observed_at,
            "price_eur": str(offer.price_eur) if offer.price_eur is not None else None,
            "shipping_eur": str(offer.shipping_eur) if offer.shipping_eur is not None else None,
            "comparison_price_eur": str(own) if own is not None else None,
            "peer_median_eur": str(median) if median is not None else None,
            "peer_count": peer_count, "discount_pct": str(discount) if discount is not None else None,
            "classification": (
                "auffällig günstig" if discount is not None
                and discount >= discount_threshold * 100 else
                "kein Preisschnäppchen" if discount is not None else "nicht bewertbar"
            ), "reason": reason, "notes": flags,
        })
    ranked.sort(key=lambda r: (
        r["classification"] != "auffällig günstig",
        r["discount_pct"] is None,
        -(Decimal(r["discount_pct"]) if r["discount_pct"] is not None else Decimal("-1000")),
        r["id"],
    ))
    return ranked


def search_url(query: str, *, maximum_eur: int | None = None) -> str:
    """Create a regular search link; opening it is a human browser action."""
    phrase = _string(query, name="Suchbegriff", size=160)
    params = {"keywords": phrase}
    if maximum_eur is not None:
        if type(maximum_eur) is not int or not 1 <= maximum_eur <= 10_000_000:
            raise DealInputError("Ungültige Preisobergrenze")
        params["maxPrice"] = str(maximum_eur)
    return "https://www.kleinanzeigen.de/s-suchanfrage.html?" + urlencode(params)
