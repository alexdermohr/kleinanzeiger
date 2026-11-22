"""Low-frequency public SACD search. No sessions, mail or circumvention.

Pages are untrusted. Only public listing IDs/titles/prices/direct URLs are retained.
A failed, blocked or structurally changed page never replaces the last good report.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import ssl
import stat
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .bargain_hunter import DealInputError, euro, normalize_offer
from .bargain_radar import atomic_report, run as radar_run
from .silver_sacd import CATEGORY, SEARCH_URL, SEARCH_URLS, rejection, model_key

MAX_PAGE_BYTES = 1_250_000
MIN_FETCH_INTERVAL_SECONDS = 30 * 60
# The first argument remains a fixed, public page; only a same-host HTTPS
# SEO redirect to another /s-... search page is allowed.
BASE = "https://www.kleinanzeigen.de"
AD_PATH = re.compile(r"/s-anzeige/[a-zA-Z0-9._%-]+/([1-9][0-9]{5,19})(?:-[0-9]+-[0-9]+)?\Z")
NUMERIC_PRICE = re.compile(r"([0-9]{1,3}(?:\.[0-9]{3})*|[0-9]+)(?:,([0-9]{1,2}))?\s*€\Z")
USER_AGENT = "Kleinanzeiger/1.7 (private, low-frequency public-offer monitoring)"


class PublicFetchDeferred(DealInputError):
    """Expected no-op when a boot/restart timer fires before the fetch interval."""


class _SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (parsed.scheme != "https" or parsed.hostname != "www.kleinanzeigen.de"
                or parsed.port is not None or parsed.username or parsed.password
                or not parsed.path.startswith("/s-")):
            raise DealInputError("Suchseite leitet auf eine unzulässige Adresse um")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _trusted_https_context() -> ssl.SSLContext:
    """Use system CA roots when an isolated Python has no usable default store.

    NixOS ships a system CA bundle independently of Python's OpenSSL defaults.
    Keep full TLS hostname and certificate verification; never disable trust.
    """
    context = ssl.create_default_context()
    if context.get_ca_certs():
        return context
    system_bundle = Path("/etc/ssl/certs/ca-certificates.crt")
    if system_bundle.is_file():
        context.load_verify_locations(cafile=str(system_bundle))
    if context.get_ca_certs():
        return context
    # OpenSSL CA directories are lazy: get_ca_certs() can remain empty until
    # the first verified handshake. Do not reject an existing directory store.
    # Verification stays enabled; empty/invalid directories fail at handshake.
    ca_dir = ssl.get_default_verify_paths().capath
    if ca_dir and Path(ca_dir).is_dir():
        return context
    raise DealInputError("Kein vertrauenswürdiges TLS-Zertifikatsbündel verfügbar")


def fetch_html(url: str = SEARCH_URL) -> str:
    """Exactly one request per invocation, no login, cookies, JS, retries or proxies."""
    if url not in SEARCH_URLS:
        raise DealInputError("Nur fest vorgesehene öffentliche SACD-Suchen sind zugelassen")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    # ProxyHandler({}) avoids unintended use of a proxy from the environment.
    from urllib.request import ProxyHandler
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=_trusted_https_context()), _SameHostRedirect)
    try:
        with opener.open(request, timeout=20) as response:
            actual = urlsplit(response.url)
            if (actual.scheme != "https" or actual.hostname != "www.kleinanzeigen.de"
                    or not actual.path.startswith("/s-")):
                raise DealInputError("Abruf hat die zugelassene Suchseite verlassen")
            if response.status != 200 or response.headers.get_content_type() != "text/html":
                raise DealInputError("Suchseite antwortet nicht mit HTML 200")
            raw = response.read(MAX_PAGE_BYTES + 1)
    except HTTPError as exc:
        raise DealInputError(f"Öffentlicher Abruf HTTP {exc.code}; keine Umgehung oder Wiederholung") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise DealInputError("Öffentliche Suchseite derzeit nicht erreichbar") from exc
    if not raw or len(raw) > MAX_PAGE_BYTES:
        raise DealInputError("Suchseite leer oder zu groß")
    try:
        return raw.decode("utf-8")
    except UnicodeError as exc:
        raise DealInputError("Suchseite ist kein lesbares UTF-8") from exc


class _Cards(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.card = None
        self.title = False
        self.price = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "article" and attrs.get("data-adid"):
            if self.card is not None:
                raise DealInputError("Verschachtelte Inseratskarte")
            self.card = {"id": attrs["data-adid"], "href": attrs.get("data-href", ""),
                         "title": "", "price": ""}
        if self.card is None:
            return
        if tag == "h3":
            self.title = True
        if tag == "p":
            classes = set((attrs.get("class") or "").split())
            if {"text-title3", "font-strong", "text-secondary"} <= classes:
                if self.card["price"]:
                    raise DealInputError("Mehrdeutige Preisangabe")
                self.price = True

    def handle_data(self, value):
        if self.card is not None:
            if self.title:
                self.card["title"] += value
            if self.price:
                self.card["price"] += value

    def handle_endtag(self, tag):
        if self.card is None:
            return
        if tag == "h3":
            self.title = False
        elif tag == "p":
            self.price = False
        elif tag == "article":
            self.cards.append(self.card)
            self.card = None
            self.title = False
            self.price = False


def parse_search_html(page: str, *, observed_at: str | None = None,
                      review_missing_color: bool = False) -> tuple[list[dict], dict]:
    """Fail on unexpected layouts; ignore VB/free listings, accessories and black players."""
    if not isinstance(page, str) or len(page) > MAX_PAGE_BYTES:
        raise DealInputError("Ungültige Suchseite")
    if not re.search(r'<ul\b[^>]*\bid="srchrslt-adtable"', page):
        raise DealInputError("Ergebnisliste nicht vorhanden; keine Daten verändert")
    parser = _Cards()
    try:
        parser.feed(page)
        parser.close()
    except (ValueError, AssertionError) as exc:
        raise DealInputError("Inserats-HTML ist nicht lesbar") from exc
    if not parser.cards or parser.card is not None or len(parser.cards) > 200:
        raise DealInputError("Keine eindeutigen Inseratskarten; letzte Daten bleiben erhalten")
    stamp = observed_at or datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    color_review: list[dict] = []
    ids: set[str] = set()
    ignored = 0
    for card in parser.cards:
        uid = card["id"]
        path = card["href"]
        match = AD_PATH.fullmatch(path)
        if not match or match[1] != uid:
            raise DealInputError("Unzulässige oder widersprüchliche Inserats-URL")
        if uid in ids:
            raise DealInputError("Doppelte Inserat-ID in Ergebnisliste")
        ids.add(uid)
        title = " ".join(card["title"].split())
        value = " ".join(card["price"].split())
        if not title or len(title) > 350 or not value:
            raise DealInputError("Inseratskarte ohne Titel oder Preis")
        reason = rejection(title)
        # A known SACD model without a colour claim is worth manual photo
        # inspection, but is NEVER an accepted silver candidate or an alert.
        check_color = (review_missing_color
                       and reason == 'Explizite Silberangabe im Mailtitel fehlt'
                       and model_key(title) is not None
                       and rejection(title + ' silber') is None)
        if reason and not check_color:
            ignored += 1
            continue
        match_price = NUMERIC_PRICE.fullmatch(value)
        if not match_price:
            # Explicitly exclude VB/negotiable, free or unsupported prices.
            ignored += 1
            continue
        price = match_price[1].replace(".", "") + ("." + match_price[2] if match_price[2] else "")
        if (not euro(price, required=True) or euro(price, required=True) <= Decimal(0)):
            ignored += 1
            continue
        row = {"id": uid, "title": title, "price_eur": price,
               "url": BASE + path, "source": "Öffentliche Kleinanzeigen-Suche",
               "observed_at": stamp, "shipping_eur": None,
               "condition": "unbekannt"}
        normalize_offer(row)
        if check_color:
            color_review.append(row)
            ignored += 1
        else:
            rows.append(row)
    stats = {"visible_cards": len(parser.cards), "accepted_fixed_price": len(rows),
             "ignored_or_unpriced": ignored}
    if review_missing_color:
        stats.update(color_review_count=len(color_review), color_review=color_review,
                     _seen_ids=tuple(sorted(ids)))
    return rows, stats


@contextmanager
def _single_public_run(profile: Path):
    """No overlap between a systemd timer run and a manual KDE refresh."""
    lock_path = profile.parent / ".public-sacd.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise DealInputError("Unsichere öffentliche Suchlauf-Sperrdatei")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DealInputError("Öffentliche Suche läuft bereits") from exc
        yield fd
    finally:
        os.close(fd)



def _attempt_time() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _enforce_fetch_interval(lock_fd: int) -> int:
    """Persist time and query rotation before the single network request.

    The old one-line timestamp format remains readable during upgrades.
    Failed queries also advance the rotation, so a broken term cannot trap it.
    Caller holds the exclusive systemd/manual-run flock.
    """
    now = _attempt_time()
    os.lseek(lock_fd, 0, os.SEEK_SET)
    prior = os.read(lock_fd, 80)
    previous_slot = -1
    if prior:
        try:
            fields = prior.decode("ascii").split()
            if len(fields) not in (1, 2):
                raise ValueError("invalid lock fields")
            last = int(fields[0])
            if len(fields) == 2:
                previous_slot = int(fields[1])
            if previous_slot < -1 or previous_slot >= len(SEARCH_URLS):
                raise ValueError("invalid query slot")
        except (UnicodeError, ValueError) as exc:
            raise DealInputError("Ungültiger letzter Abrufzeitpunkt") from exc
        if now < last + MIN_FETCH_INTERVAL_SECONDS:
            raise PublicFetchDeferred("Öffentliche Suche höchstens alle 30 Minuten erlaubt")
    slot = (previous_slot + 1) % len(SEARCH_URLS)
    payload = f"{now} {slot}\n".encode("ascii")
    os.lseek(lock_fd, 0, os.SEEK_SET)
    if os.write(lock_fd, payload) != len(payload):
        raise OSError("Abrufzeitpunkt nicht vollständig geschrieben")
    os.ftruncate(lock_fd, len(payload))
    os.fsync(lock_fd)
    return slot


def run_public(profile: Path, *, fetcher=fetch_html) -> dict:
    from .radar_watch import RadarWatch, safe
    profile = profile.absolute()
    manager = RadarWatch(profile.parent.parent)
    config = manager.load(profile)
    if config.get("input_type") != "public" or config.get("category") != CATEGORY:
        raise DealInputError("Nur das separat eingerichtete öffentliche SACD-Profil ist erlaubt")
    # An invalid price-target file must never consume the fetch slot or
    # overwrite a successful previous public snapshot.
    manager.price_targets(profile)
    with _single_public_run(profile) as lock_fd:
        slot = _enforce_fetch_interval(lock_fd)
        page = fetcher(SEARCH_URLS[slot])
        rows, stats = parse_search_html(page, review_missing_color=True)
        color_review = stats.pop('color_review', [])
        # Private per-fetch IDs; do not persist unrelated rejected listings.
        seen_ids = set(stats.pop('_seen_ids', ()))
        stats.update(query_index=slot + 1, query_total=len(SEARCH_URLS),
                     query_url=SEARCH_URLS[slot])
        # Never update the snapshot or history on HTTP errors / invalid HTML.
        target = profile.parent / "angebote.json"
        if target.exists() or target.is_symlink():
            safe(target)
        atomic_report(target, json.dumps(rows, ensure_ascii=False, indent=2))
        result = radar_run(profile, color_review=color_review, public_search=stats,
                           public_seen_ids=seen_ids)
        return {**result, "public_search": stats}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Öffentliche silberne SACD-Angebote einmal prüfen (kein Konto)")
    parser.add_argument("--profile", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_public(args.profile)
    except PublicFetchDeferred:
        # OnBootSec can fire before the 30-minute minimum after a reboot.
        # No HTTP request, fetch-slot advancement or report mutation occurred.
        # This is a skipped timer tick, not a failed public search.
        print(json.dumps({"status": "skipped", "reason": "cooldown"}))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Öffentliche SACD-Suche: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: result[k] for k in ("new", "price_changes", "candidate_events",
                                             "known_candidate_count", "public_search")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())