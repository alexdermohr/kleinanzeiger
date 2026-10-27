from __future__ import annotations

import argparse
import csv
import io
import sys
import hmac
import http.client
import json
import math
import os
import re
import secrets
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime
from threading import Lock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .container_draft import container_draft_denied
from .analytics import (
    ANALYTICS_DIMENSIONS,
    ANALYTICS_METRICS,
    REACTION_METRICS,
    AnalyticsContract,
    AnalyticsService,
    ad_metric_ranking_to_dict,
    analytics_contract_to_dict,
    group_metric_ranking_to_dict,
)
from .portfolio_drafts import drafts_page
from .portfolio_jobs import (
    PortfolioJobError, apply_approved_local, change_state, create_job, details as job_details,
    list_jobs, matching_draft_skus, process_one, recover_interrupted, review_items,
    settle_unknown,
)
from .portfolio_provider import (
    PROVIDER_REQUIRED_RESERVE_CENTS, generate_metered_proposal,
)
from .query import (
    MarkQueryService,
    ad_snapshot_to_dict,
    ad_view_to_dict,
    email_reaction_view_to_dict,
    reaction_snapshot_to_dict,
    summary_to_dict,
)
from .storage import DashboardPendingWrite, SnapshotStore
from .storage_owner import StorageOwnerClient, StorageOwnerError
from .strict_json import strict_json_object
from .write_api import _normalize_create_payload, _request_fingerprint

from .generation import (
    AdvertisementDraft, AdvertisementGenerator, GenerationError,
    _validate_png_payload,
)


_ANALYTICS_CSV_RANKING_COLUMNS = (
    "ad_id", "metric", "value", "title", "present", "lifecycle_state",
    "metric_observed_at", "metric_source", "metric_last_known",
)
_ANALYTICS_CSV_GROUP_COLUMNS = (
    "dimension", "metric", "label", "sample_size", "metric_sum", "metric_mean",
)


def _csv_safe_cell(value: object) -> object:
    """Prevent spreadsheet formulas from executing in untrusted strings."""
    if value is None:
        return ""
    if isinstance(value, str):
        unspaced = value.lstrip()
        if unspaced.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
            return "'" + value
    return value


def _analytics_csv_bytes(
    columns: tuple[str, ...],
    rows: Iterable[tuple[object, ...]],
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_safe_cell(value) for value in row])
    # UTF-8 BOM also makes German labels readable in spreadsheet imports.
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


_MAX_PROXY_JSON_BODY_BYTES = 16 * 1024
_MAX_PROXY_MEDIA_BODY_BYTES = 25 * 1024 * 1024
_MAX_PROXY_RESPONSE_BYTES = 256 * 1024
_PROXY_BODY_READ_TIMEOUT_SECONDS = 10.0
_DASHBOARD_IDEMPOTENCY_KEY_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)
_DASHBOARD_WRITE_MARKER_HEADER = "X-Mark-Dashboard-Write"
_DASHBOARD_WRITE_MARKER_VALUE = "1"
_DASHBOARD_WRITE_TOKEN_HEADER = "X-Mark-Dashboard-Token"


def _dashboard_http_origin(host: str, port: int) -> str:
    return f"http://{host}" if port == 80 else f"http://{host}:{port}"


@dataclass(frozen=True, slots=True)
class DashboardWriteProxy:
    """Narrow same-origin gateway to the existing loopback Write API."""

    host: str
    port: int
    bearer_token: str = field(repr=False)
    ui_token: str = field(repr=False)
    timeout_seconds: float = 90.0
    backend_principal: str | None = None
    # Explicit trusted composition opt-in: never enable with an independent
    # owner or witness. Their completion authority cannot be inferred locally.
    local_reconciliation_enabled: bool = False
    # Distinct explicit composition: only an authenticated protected owner
    # with independently witnessed completion may authorize either UI ACK.
    owner_reconciliation_enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.local_reconciliation_enabled) is not bool:
            raise ValueError("local_reconciliation_enabled must be bool")
        if type(self.owner_reconciliation_enabled) is not bool:
            raise ValueError("owner_reconciliation_enabled must be bool")
        if self.local_reconciliation_enabled and self.owner_reconciliation_enabled:
            raise ValueError("local and owner reconciliation are mutually exclusive")
        if self.backend_principal is not None and (
            not isinstance(self.backend_principal, str)
            or not self.backend_principal.strip()
            or self.backend_principal != self.backend_principal.strip()
            or len(self.backend_principal) > 128
            or "\r" in self.backend_principal or "\n" in self.backend_principal
        ):
            raise ValueError("backend_principal is invalid")
        if self.host != "127.0.0.1":
            raise ValueError("dashboard write proxy must target 127.0.0.1")
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("dashboard write proxy port must be 1..65535")
        for name, value in (
            ("bearer_token", self.bearer_token),
            ("ui_token", self.ui_token),
        ):
            if (
                not isinstance(value, str)
                or len(value) < 16
                or len(value) > 512
                or any(character.isspace() for character in value)
                or any(
                    not 0x21 <= ord(character) <= 0x7E
                    for character in value
                )
            ):
                raise ValueError(f"{name} is invalid")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("dashboard write proxy timeout must be positive")



class DashboardGenerationBusy(RuntimeError):
    """Only one potentially billable provider call may run at a time."""


class DashboardGenerationLimit(RuntimeError):
    """The explicit per-process generation spending limit was reached."""


class DashboardGenerationAccess:
    """Independent session token and hard limit for on-demand provider calls.

    No provider is constructed at startup. Failed/unknown provider calls count
    against the limit and are never retried automatically. This surface has
    no platform write authority, no cookies, and no URL forwarding.
    """

    def __init__(
        self,
        *,
        ui_token: str,
        generator_factory: Callable[[], AdvertisementGenerator] = AdvertisementGenerator,
        max_requests: int = 10,
    ) -> None:
        if (
            not isinstance(ui_token, str) or not 16 <= len(ui_token) <= 512
            or any(not 0x21 <= ord(char) <= 0x7E for char in ui_token)
        ):
            raise ValueError("invalid generation UI token")
        if not callable(generator_factory):
            raise TypeError("generator_factory must be callable")
        if (
            isinstance(max_requests, bool)
            or not isinstance(max_requests, int)
            or not 1 <= max_requests <= 100
        ):
            raise ValueError("invalid generation request limit")
        self._token = ui_token
        self._factory = generator_factory
        self._max_requests = max_requests
        self._used = 0
        self._lock = Lock()

    @property
    def ui_token(self) -> str:
        return self._token

    def generate(self, mode: str, prompt: str) -> object:
        if not self._lock.acquire(blocking=False):
            raise DashboardGenerationBusy("generation already in progress")
        try:
            if self._used >= self._max_requests:
                raise DashboardGenerationLimit("generation session budget reached")
            # Consume a slot BEFORE any billable or ambiguous provider call.
            self._used += 1
            generator = self._factory()
            if mode == "text":
                return generator.generate_text(prompt)
            if mode == "image":
                return generator.generate_image(prompt)
            raise GenerationError("invalid generation kind")
        finally:
            self._lock.release()

    def generate_one_portfolio_proposal(
        self, store: SnapshotStore, job_id: str,
    ) -> dict[str, object]:
        """Bounded per-click paid request with SQLite claim and no blind replay."""
        if not self._lock.acquire(blocking=False):
            raise DashboardGenerationBusy("generation already in progress")
        try:
            if self._used >= self._max_requests:
                raise DashboardGenerationLimit("generation session call limit reached")
            progress=job_details(store,job_id,limit=1)
            if progress["state"]!="running":
                raise PortfolioJobError("job must be explicitly started")
            if progress["estimate_cents"]<PROVIDER_REQUIRED_RESERVE_CENTS:
                raise PortfolioJobError("minimum 20 USD cents per provider claim required")
            if not progress["counts"].get("queued",0):
                raise PortfolioJobError("no remaining queued articles")
            if (progress["actual_cents"]+progress["reserved_cents"]
                    +progress["estimate_cents"]>progress["budget_cents"]):
                raise PortfolioJobError("job budget too small for next request")
            # Consume the session allowance BEFORE potentially billable work.
            self._used+=1
            return process_one(
                store,job_id,
                lambda original,instruction: generate_metered_proposal(
                    self._factory(),original,instruction),
            )
        finally:
            self._lock.release()


_DASHBOARD_HTML = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kleinanzeiger – Meine Anzeigen</title>
  <link rel="stylesheet" href="/dashboard.css">
</head>
<body>
  <main id="top">
    <header class="app-header">
      <div class="brand">
        <span class="eyebrow">DEIN ANZEIGEN-COCKPIT</span>
        <h1>Kleinanzeiger</h1>
        <p>Alle Anzeigen auf einen Blick. Einfach bearbeiten und auswerten.</p>
      </div>
      <div class="header-actions">
        <button id="sync-now" class="reload-button" type="button" hidden>Mit Kleinanzeigen abgleichen</button>
        <button id="reload" class="reload-button" type="button">Ansicht neu laden</button>
      </div>
    </header>

    <nav class="quick-nav" aria-label="Bereiche">
      <a href="#workspace">Meine Anzeigen</a>
      <a id="nav-create" href="#create-disclosure">Neue Anzeige</a>
      <a id="nav-drafts" href="#drafts-panel">Artikelentwürfe</a>
      <a id="nav-jobs" href="#jobs-panel" hidden>KI-Stapelaufträge</a>
      <a id="nav-analytics" href="#analytics-panel">Auswertungen</a>
      <a id="nav-generation" href="#generation-panel" hidden>KI-Entwürfe</a>
    </nav>
    <section id="status" class="status" role="status" aria-live="polite"></section>

    <section class="cards" aria-label="Übersicht">
      <article><span>Aktuelle Anzeigen</span><strong id="current">—</strong></article>
      <article><span>Aufrufe</span><strong id="views">—</strong></article>
      <article><span>Merker</span><strong id="watches">—</strong></article>
      <article><span>Antworten</span><strong id="replies">—</strong></article>
      <article><span>Insgesamt erfasst</span><strong id="tracked">—</strong></article>
      <article><span>Nicht mehr vorhanden</span><strong id="absent">—</strong></article>
    </section>

    <div id="workspace" class="workspace">
      <section id="ads-panel" class="panel inventory-panel">
        <div class="section-heading">
          <div>
            <h2>Meine Anzeigen</h2>
            <p class="section-subtitle">Zum Bearbeiten eine Anzeige auswählen.</p>
          </div>
          <span class="section-label" id="sync-overview">Lokaler Bestand</span>
        </div>
        <div class="catalog-toolbar" aria-label="Anzeigen durchsuchen">
          <label>Suche nach Titel oder Anzeigen-ID
            <input id="catalog-search" type="search" maxlength="120" placeholder="Titel oder Anzeigen-ID" autocomplete="off">
          </label>
          <label>Status
            <select id="catalog-status">
              <option value="all">Alle Anzeigen</option>
              <option value="active">Aktiv</option>
              <option value="paused">Pausiert</option>
              <option value="pending">Offen</option>
              <option value="absent">Entfernt</option>
              <option value="unknown">Unbekannt</option>
            </select>
          </label>
          <button type="button" id="catalog-find">Suchen</button>
        </div>
        <div class="catalog-summary">
          <span id="catalog-feedback" role="status" aria-live="polite">Lade Anzeigen …</span>
          <span id="catalog-selected-count">0 ausgewählt</span>
        </div>
        <div class="catalog-selection-actions">
          <button type="button" id="catalog-clear">Auswahl leeren</button>
          <button type="button" id="catalog-export">Auswahl als CSV</button>
        </div>
        <div id="ad-cards" class="ad-cards" role="list"></div>
        <div class="catalog-pages">
          <button id="catalog-prev" type="button">Zurück</button>
          <span id="catalog-page-label">Seite 1</span>
          <button id="catalog-next" type="button">Weiter</button>
        </div>
        <p id="ad-cards-empty" class="empty" hidden>Hier erscheinen deine Anzeigen nach der Synchronisierung.</p>
        <details class="compact-details inventory-details">
          <summary>Alle Kennzahlen und Details anzeigen</summary>
          <p id="sync-status" class="note" aria-live="polite">Aktualisierung wird geladen …</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Auswahl</th>
              <th>ID</th>
              <th>Titel</th>
              <th>Status</th>
              <th>Views</th>
              <th>Merker</th>
              <th>Replies</th>
              <th>Letzte Beobachtung</th>
              <th>Aktion</th>
            </tr>
          </thead>
          <tbody id="ads-body"></tbody>
        </table>
      </div>
      <p id="empty" class="empty" hidden>Keine Anzeigenhistorie vorhanden.</p>

          <details class="compact-details">
            <summary>Woher kommen die Zahlen?</summary>
            <p class="note">Bestandsaktualisierung und Zeitpunkt einzelner Zähler sind getrennt. Für jeden Wert zeigt die Detailtabelle Beobachtungszeit und Quelle.</p>
          </details>
        </details>
      </section>

      <section id="write-panel" class="panel management-panel" hidden>
        <div class="section-heading">
          <div>
            <h2>Anzeige bearbeiten</h2>
            <p class="section-subtitle">Titel oder Beschreibung ändern und den Status verwalten.</p>
          </div>
        </div>
        <p id="write-session-note" class="note" hidden>Zum Bearbeiten Kleinanzeiger über Starten.cmd öffnen.</p>
        <p id="write-status" class="status write-status" role="status" aria-live="polite"></p>
        <form id="manage-form" class="write-form">
          <h3 class="sr-only">Bestehende Anzeige</h3>
          <label>
            Vorhandene Anzeige auswählen
            <select id="manage-ad-select" data-write-control>
              <option value="">Anzeige auswählen …</option>
            </select>
          </label>
          <div class="selection-status">Status: <strong id="manage-state">—</strong></div>
          <details class="compact-details technical-detail">
            <summary>Anzeigen-ID anzeigen</summary>
            <label>
              Anzeigen-ID (automatisch übernommen)
              <input id="manage-ad-id" type="text" readonly>
            </label>
          </details>
          <label>
            Titel
            <input id="manage-title" data-write-control type="text" autocomplete="off">
          </label>
          <label>
            Beschreibung
            <textarea id="manage-description" data-write-control></textarea>
          </label>
          <div class="write-actions">
            <button id="manage-save" class="primary-button" data-write-control type="button">Änderungen speichern</button>
            <button id="manage-pause" data-write-control type="button">Pausieren</button>
            <button id="manage-activate" data-write-control type="button">Aktivieren</button>
            <button id="manage-delete" data-write-control class="danger" type="button">Löschen</button>
          </div>
        </form>
        <details id="create-disclosure" class="feature-disclosure create-disclosure">
          <summary><span>Neue Anzeige erstellen</span><small>Mit Titel, Preis und Bildern</small></summary>
        <form id="create-form" class="write-form">
          <h3 class="sr-only">Neue Anzeige</h3>
          <label>
            Kategoriepfad
            <input id="create-category" data-write-control type="text" placeholder="Haus &amp; Garten &gt; Dekoration" autocomplete="off">
          </label>
          <label>
            Titel
            <input id="create-title" data-write-control type="text" autocomplete="off">
          </label>
          <label>
            Beschreibung
            <textarea id="create-description" data-write-control></textarea>
          </label>
          <label>
            Festpreis in Euro
            <input id="create-price" data-write-control type="number" min="1" step="1" inputmode="numeric">
          </label>
          <label>
            Bilder (optional)
            <input id="create-media" data-write-control type="file" accept="image/jpeg,image/png,image/webp" multiple>
          </label>
          <div class="write-actions">
            <button id="create-submit" class="primary-button" data-write-control type="submit">Anzeige erstellen</button>
          </div>
        </form>
        </details>
        <details class="compact-details safety-details">
          <summary>Hinweise zu Änderungen</summary>
          <p class="note">Änderungen erfolgen nur nach deiner Aktion. Unklare Ergebnisse werden nicht automatisch wiederholt. Offene Vorgänge müssen zuerst geklärt werden.</p>
        </details>
      </section>
      <section id="write-unavailable" class="panel read-only-panel" hidden>
        <h2>Nur Ansicht</h2>
        <p class="muted">Zum Bearbeiten und Erstellen Kleinanzeiger normal über Starten.cmd starten.</p>
      </section>
    </div>

    <section id="drafts-panel" class="panel secondary-panel">
      <details id="drafts-disclosure" class="feature-disclosure">
        <summary><span>Artikelentwürfe</span><small>CSV-/Excel-Artikel prüfen, durchsuchen und ins Anzeigenformular übernehmen</small></summary>
        <p class="muted">Diese Artikel sind nur lokal gespeichert und nicht auf Kleinanzeigen veröffentlicht. Import und Export erfolgen im Kleinanzeiger-Startfenster bei beendeter Anwendung.</p>
        <div class="controls">
          <label>Artikelnummer oder Titel suchen
            <input id="draft-search" type="search" maxlength="120" autocomplete="off" placeholder="Artikelnummer oder Titel">
          </label>
          <button id="draft-find" type="button">Suchen</button>
        </div>
        <p id="draft-feedback" role="status" aria-live="polite">Entwürfe anzeigen …</p>
        <div class="table-wrap"><table>
          <thead><tr><th>Wählen</th><th>Artikelnummer</th><th>Bezeichnung</th><th>Preis</th><th>Kategorie</th><th>Aktion</th></tr></thead>
          <tbody id="drafts-body"></tbody>
        </table></div>
        <div class="catalog-pages">
          <button id="draft-prev" type="button">Zurück</button>
          <span id="draft-page-label">Seite 1</span>
          <button id="draft-next" type="button">Weiter</button>
        </div>
      </details>
    </section>

    <section id="jobs-panel" class="panel secondary-panel" hidden>
      <details id="jobs-disclosure" class="feature-disclosure">
        <summary><span>KI-Stapelaufträge</span><small>Lokale Artikel auswählen, Bearbeitung vorbereiten und Vorschläge freigeben</small></summary>
        <p class="muted">Bis zu 5.000 eigene Artikelentwürfe pro Auftrag. Die lokale Auftragsverwaltung
        ruft ohne ausdrücklichen Einzelauftrag keine KI auf und führt keine Änderungen
        auf Kleinanzeigen aus. Vorgeschlagene Änderungen bleiben bis zur Freigabe getrennt.</p>
        <div class="controls">
          <label><input id="job-all-matching" type="checkbox">
            Alle lokalen Entwürfe aus dem zuletzt angewandten Suchfilter wählen (max. 5.000)
          </label>
          <span id="job-selected-count">0 Entwürfe einzeln ausgewählt</span>
          <button id="job-clear-selection" type="button">Auswahl löschen</button>
        </div>
        <label>Gemeinsamer Verbesserungsauftrag
          <textarea id="job-instruction" maxlength="1500" rows="3" placeholder="Nur nachgewiesene Artikelmerkmale verwenden, Titel präzisieren, nichts hinzuerfinden."></textarea>
        </label>
        <div class="controls">
          <label>Reserviertes Gesamtbudget (USD)
            <input id="job-budget-eur" type="number" min="0" max="1000000" step="0.01" value="2.00">
          </label>
          <label>Budgetreserve je möglicher KI-Anfrage (USD)
            <input id="job-estimate-eur" type="number" min="0.20" max="1000000" step="0.01" value="0.20">
          </label>
          <button id="job-create" type="button">Auftrag lokal anlegen</button>
        </div>
        <p class="muted">„Auftrag freigeben“ startet keine Anfrage. Das Budget reserviert
        USD-Cent; die aus Provider-Tokens berechneten Kosten sind konservativ
        geschätzt, keine verifizierte Anbieterrechnung und keine garantierte
        Kostenobergrenze. Offizielle Preisliste: developers.openai.com/api/docs/pricing.
        Eine kostenpflichtige Anfrage erfordert jeweils gesonderte Bestätigung.</p>
        <div id="job-provider-control" hidden>
          <label><input id="job-provider-consent" type="checkbox">
            Für die nachfolgend ausgewählte Einzelanfrage oder die begrenzte Anzahl
            im Stapel bestätige ich die externe Übertragung von Artikelangaben,
            Modell gpt-5.6-terra, die veröffentlichte API-Preisliste und
            möglicherweise anfallende, nicht exakt garantierbare USD-Kosten.</label>
          <button id="job-generate-next" type="button">
            Nächsten Vorschlag mit KI erzeugen (kostenpflichtig)
          </button>
          <div class="controls">
            <label>Vorschläge nacheinander erzeugen (maximal 10 je Auftragsschritt)
              <input id="job-batch-count" type="number" min="1" max="10" step="1" value="10">
            </label>
            <button id="job-generate-batch" type="button">KI-Stapelverarbeitung starten</button>
            <button id="job-batch-stop" type="button" disabled>Nach der laufenden Anfrage stoppen</button>
          </div>
          <p class="muted">Für den bestätigten Stapel wird jeder Artikel einzeln und nacheinander
          verarbeitet. Bei Abbruch, unklarem Ergebnis, Kostenlimit oder Browser-Schließung
          startet kein weiterer Aufruf. Nicht abgeschlossene Anfragen werden nicht wiederholt.</p>
          <p id="job-provider-status" role="status" aria-live="polite"></p>
        </div>
        <p id="jobs-feedback" role="status" aria-live="polite"></p>
        <div class="controls">
          <label>Vorhandener Auftrag
            <select id="job-choice"><option value="">Aufträge laden …</option></select>
          </label>
          <button id="job-refresh" type="button">Aufträge aktualisieren</button>
        </div>
        <p id="job-progress" class="note" role="status" aria-live="polite"></p>
        <div class="write-actions">
          <button id="job-start" type="button">Auftrag freigeben</button>
          <button id="job-pause" type="button">Pausieren</button>
          <button id="job-resume" type="button">Fortsetzen</button>
          <button id="job-cancel" type="button">Abbrechen</button>
          <button id="job-recover" type="button">Unterbrochene Anfrage sichern</button>
        </div>
        <div class="table-wrap"><table>
          <thead><tr><th>Wählen</th><th>Artikel</th><th>Bisheriger Titel</th><th>Vorschlag</th><th>Status</th></tr></thead>
          <tbody id="job-items-body"></tbody>
        </table></div>
        <div class="catalog-pages">
          <button id="job-prev" type="button">Zurück</button>
          <span id="job-page-label">Seite 1</span>
          <button id="job-next" type="button">Weiter</button>
        </div>
        <div class="write-actions">
          <button id="job-approve" type="button">Ausgewählte Vorschläge freigeben</button>
          <button id="job-reject" type="button">Ausgewählte Vorschläge ablehnen</button>
          <button id="job-apply-local" type="button">Freigegebene Änderungen nur lokal übernehmen</button>
        </div>
        <p class="muted">Es werden weder Anzeigen veröffentlicht noch bestehende Kleinanzeigen
        geändert. Die Übernahme prüft alle Originaldaten erneut und bricht bei Konflikten
        ohne Teiländerung ab. Vor umfangreichen lokalen Änderungen eine Sicherung erstellen.</p>
      </details>
    </section>

    <section id="generation-panel" class="panel secondary-panel" hidden>
      <details id="generation-disclosure" class="feature-disclosure">
        <summary><span>KI-Assistent für Anzeigen</span><small>Texte und Bilder als Entwurf</small></summary>
      <p class="muted">Erstelle einen Text- oder Bildvorschlag, den du vor der Veröffentlichung selbst prüfst.</p>
      <p id="generation-pricing-note" class="note">Kostenbasis für das jeweils ausgewählte Verfahren (OpenAI-Standardtarife, Stand 20.10.2026):
        <strong>Textentwurf:</strong> Modell <code>gpt-5.6-terra</code>, 2.00 USD je 1 Mio Text-Eingabetokens und 12.00 USD je 1 Mio Text-Ausgabetokens.
        <strong>Bildentwurf:</strong> Modell <code>gpt-image-2.5-flare</code>, 5.00 USD je 1 Mio Text-Eingabetokens und 30.00 USD je 1 Mio Bild-Ausgabetokens.
        Die tatsächlichen Kosten hängen vom Tokenverbrauch, Modell und ggf. weiteren Bild-/Cache-Eingaben ab; kein fixer Bildpreis und keine garantierte Kostenobergrenze.
        Tarife können sich ändern. Offizielle OpenAI-Preisliste zum Nachprüfen (Adresse kopieren): <code>developers.openai.com/api/docs/pricing</code>, bevor die einzelne Anfrage bestätigt wird.</p>
      <p id="generation-session-note" class="note" hidden>Die Generierung muss für diese Sitzung mit <code>--enable-generation</code> aktiviert und das Dashboard über die vom Launcher ausgegebene URL geöffnet werden.</p>
      <label>Artikelangaben oder Bildidee
        <textarea id="generation-prompt" maxlength="4000" placeholder="Nur die tatsächlich bekannten Angaben zum Artikel eingeben. Keine privaten Daten."></textarea>
      </label>
      <label>
        <input id="generation-consent" type="checkbox">
        Ich habe die Modell- und Preisinformation oben für diese Anfrage geprüft und bestätige für genau die nächste Anfrage die externe Promptübertragung sowie mögliche variable, nicht garantierte Kosten.
      </label>
      <div class="write-actions">
        <button id="generation-text" type="button">Textentwurf erzeugen</button>
        <button id="generation-image" type="button">Bildentwurf erzeugen</button>
      </div>
      <p id="generation-status" class="note" role="status" aria-live="polite"></p>
      <div id="generation-text-preview" hidden>
        <h3>Textvorschlag (nicht veröffentlicht)</h3>
        <p id="generation-text-prompt" class="note"></p>
        <label>Vorgeschlagener Titel <input id="generation-draft-title" type="text" readonly></label>
        <label>Vorgeschlagene Beschreibung <textarea id="generation-draft-description" readonly></textarea></label>
        <button id="generation-apply-text" type="button" disabled>In neue Anzeige übernehmen (ohne Veröffentlichung)</button>
      </div>
      <div id="generation-image-preview" hidden>
        <h3>Bildvorschlag (nicht veröffentlicht)</h3>
        <p class="note">KI-Bilder sind keine nachgewiesenen Produktfotos. Übereinstimmung mit dem tatsächlichen Artikel vor jeder Verwendung prüfen.</p>
        <p id="generation-image-prompt" class="note"></p>
        <img id="generation-draft-image" alt="KI-generierter Bildentwurf, noch nicht veröffentlicht" style="max-width: 100%; max-height: 360px;" hidden>
        <p><button id="generation-apply-image" type="button" disabled>Als neues Anzeigenbild übernehmen (ohne Veröffentlichung)</button></p>
      </div>

      </details>
    </section>

    <section id="analytics-panel" class="panel secondary-panel">
      <details id="analytics-disclosure" class="feature-disclosure">
        <summary><span>Auswertungen und Rankings</span><small>Erfolge vergleichen und Trends erkennen</small></summary>

      <p id="analytics-contract" class="note"></p>
      <div class="controls">
        <label>
          Metrik
          <select id="metric-select"></select>
        </label>
        <label>
          Dimension
          <select id="dimension-select"></select>
        </label>
      </div>
      <p id="analytics-status" class="note" role="status" aria-live="polite"></p>

      <h3>Gruppen</h3>
      <p><a id="groups-csv" href="#" hidden>Gruppen als CSV exportieren</a></p>
      <div id="groups-chart" class="chart" role="list" aria-label="Gruppenvergleich nach Mittelwert" hidden></div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Label</th>
              <th>Stichprobe</th>
              <th>Summe</th>
              <th>Mittelwert</th>
            </tr>
          </thead>
          <tbody id="groups-body"></tbody>
        </table>
      </div>
      <p id="groups-empty" class="empty" hidden>Keine ausreichenden klassifizierten Daten für diese Auswahl.</p>
      <details class="compact-details"><summary>Was bedeuten die Werte?</summary><p class="note">Stichprobengröße 1 ist nur ein Datenwert, keine Qualitätsaussage.</p></details>

      <h3>Anzeigenranking</h3>
      <p><a id="ranking-csv" href="#" hidden>Ranking als CSV exportieren</a></p>
      <details class="compact-details"><summary>Quellen und Aktualität</summary><p class="note">Jeder Ranking-Wert besitzt seine eigene Beobachtungszeit und Quelle. Reaktions- und E-Mail-Werte stammen aus getrennten Datengrundlagen; Summen sind keine aktuelle Plattformbestätigung.</p></details>
      <div id="ranking-chart" class="chart" role="list" aria-label="Anzeigenvergleich nach ausgewählter Metrik" hidden></div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Titel</th>
              <th>Wert</th>
              <th>Vorhanden</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="ranking-body"></tbody>
        </table>
      </div>
      <p id="ranking-empty" class="empty" hidden>Keine Anzeigen mit bekanntem Wert für diese Metrik.</p>

      </details>
    </section>
  </main>
  <script src="/dashboard.js" defer></script>
</body>
</html>
"""
_DASHBOARD_CSS = """
:root {
  color-scheme: light;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  line-height: 1.5;
  --page: #f3f6fa;
  --surface: #fff;
  --surface-soft: #f6f9fb;
  --ink: #172e3b;
  --subtle: #5e7282;
  --line: #dbe5eb;
  --accent: #11677a;
  --accent-dark: #0d5263;
  --positive: #196948;
  --danger: #ac3446;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; background: var(--page); color: var(--ink); }
main { max-width: 1380px; margin: 0 auto; padding: 28px 24px 72px; }
.app-header { display: flex; justify-content: space-between; align-items: center; gap: 24px; margin-bottom: 20px; }
h1, h2, h3, p { margin-top: 0; }
h1 { font-size: clamp(1.9rem, 3vw, 2.5rem); letter-spacing: -0.05em; margin-bottom: 2px; line-height: 1.15; }
h2 { font-size: 1.28rem; letter-spacing: -0.025em; margin-bottom: 3px; }
h3 { font-size: 1.02rem; }
.brand p { margin: 6px 0 0; color: var(--subtle); font-size: .94rem; }
.eyebrow { display: inline-block; margin-bottom: 8px; font-size: .66rem; font-weight: 800; letter-spacing: .15em; color: var(--accent); }
button, select, input, textarea { font: inherit; color: inherit; }
button, select, input, textarea {
  border: 1px solid #cad8e0;
  background: var(--surface);
  border-radius: 10px;
  padding: 10px 12px;
  max-width: 100%;
}
input:not([type="checkbox"]):not([type="file"]), select, textarea { width: 100%; }
textarea { min-height: 116px; resize: vertical; }
input[readonly] { color: var(--subtle); background: var(--surface-soft); }
input[type="checkbox"] { width: auto; accent-color: var(--accent); }
button { cursor: pointer; font-weight: 650; }
button:hover { border-color: var(--accent); background: #f4f9fb; }
button:disabled { opacity: .52; cursor: not-allowed; }
:focus-visible { outline: 3px solid #6bb6c5; outline-offset: 2px; }
[hidden] { display: none !important; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; }
.reload-button { white-space: nowrap; background: var(--surface); color: var(--accent-dark); padding: 10px 16px; }
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.quick-nav { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 23px; }
.quick-nav a { text-decoration: none; color: #486879; font-weight: 700; font-size: .84rem; padding: 9px 14px; border: 1px solid transparent; border-radius: 999px; }
.quick-nav a:first-child { color: var(--accent-dark); background: #e0f0f3; }
.quick-nav a:hover, .quick-nav a:focus-visible { color: var(--accent-dark); background: #e0f0f3; }
.status { color: #95551e; font-size: .87rem; margin: 12px 0; }
.status:empty { display: none; }
.cards { display: grid; grid-template-columns: repeat(6, minmax(0,1fr)); gap: 12px; margin-bottom: 22px; }
.cards article, .panel { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; box-shadow: 0 6px 26px rgba(27,55,77,.045); }
.cards article { padding: 16px; }
.cards span { display: block; font-size: .75rem; font-weight: 650; color: var(--subtle); }
.cards strong { display: block; font-size: clamp(1.18rem, 2vw, 1.65rem); letter-spacing: -.025em; line-height: 1.3; margin-top: 7px; font-variant-numeric: tabular-nums; }
.panel { padding: 22px; margin-bottom: 18px; min-width: 0; }
.workspace { display: grid; grid-template-columns: minmax(0,1.12fr) minmax(340px,.88fr); gap: 18px; align-items: start; }
.workspace > .panel { margin-bottom: 18px; }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 18px; }
.section-subtitle { margin: 3px 0 0; color: var(--subtle); font-size: .85rem; }
.section-label { display: inline-flex; align-items: center; background: #e9f4f6; color: #21667a; border-radius: 999px; padding: 5px 9px; font-size: .72rem; font-weight: 700; text-align: right; }
.section-label.warning { background: #fff1df; color: #8b5417; }
.catalog-toolbar { display: grid; grid-template-columns: minmax(150px,1fr) minmax(140px,auto) auto; gap: 9px; align-items: end; margin-bottom: 11px; }
.catalog-toolbar label { display: grid; gap: 5px; font-size: .78rem; font-weight: 650; color: var(--subtle); min-width: 0; }
.catalog-toolbar input, .catalog-toolbar select { width: 100%; min-width: 0; }
.catalog-toolbar button { white-space: nowrap; }
.catalog-summary, .catalog-selection-actions, .catalog-pages { display: flex; gap: 9px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.catalog-summary { justify-content: space-between; font-size: .78rem; color: var(--subtle); }
.catalog-pages { justify-content: space-between; margin-top: 12px; }
.catalog-pages button, .catalog-selection-actions button { font-size: .8rem; }
.catalog-select { display: inline-flex; align-items: center; gap: 5px; font-size: .78rem; color: var(--subtle); }
.catalog-select input { width: 16px; height: 16px; }
.ad-cards { display: grid; gap: 10px; }
.ad-card { padding: 15px; border: 1px solid var(--line); border-radius: 13px; background: #fbfcfd; display: grid; gap: 10px; }
.ad-card.selected { border-color: #65a8b9; background: #f1fafb; box-shadow: 0 0 0 2px #dbf0f2; }
.ad-card-head { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
.ad-card h3 { margin: 0; font-size: .98rem; line-height: 1.35; overflow-wrap: anywhere; }
.ad-id { margin: 4px 0 0; color: var(--subtle); font-size: .76rem; }
.ad-card-footer { display: flex; gap: 12px; justify-content: space-between; align-items: center; }
.ad-views { font-size: .82rem; color: var(--subtle); }
.ad-card-action { background: #e8f3f5; border-color: #cce3e9; color: var(--accent-dark); padding: 7px 12px; font-size: .83rem; white-space: nowrap; }
.ad-card.selected .ad-card-action, .primary-button { background: var(--accent); border-color: var(--accent); color: #fff; }
.primary-button:hover, .ad-card.selected .ad-card-action:hover { background: var(--accent-dark); border-color: var(--accent-dark); color: #fff; }
.write-form { display: grid; gap: 14px; align-content: start; }
.write-form label { display: grid; gap: 6px; font-size: .82rem; font-weight: 650; color: #41596a; }
.write-form textarea { min-height: 126px; }
#manage-ad-select { width: 100%; min-width: 0; font-weight: 600; }
.selection-status { font-size: .78rem; color: var(--subtle); margin: -7px 0 0; }
.selection-status strong { color: var(--ink); }
.write-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.write-actions .primary-button { flex: 1 1 100%; }
button.danger { border-color: #f0d2d4; color: var(--danger); background: #fff7f8; margin-left: auto; }
button.danger:hover { border-color: var(--danger); }
.write-status { background: #fff2e2; border-left: 3px solid #e6ad54; border-radius: 6px; padding: 9px 12px; }
.write-status.success { color: var(--positive); background: #e9f5ed; border-color: #69af80; }
.write-status.warning { color: #885116; }
.write-status.error { color: var(--danger); background: #fff0f2; border-color: #d97786; }
.feature-disclosure { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.create-disclosure { margin-top: 20px; border-color: #cbdfe5; }
.feature-disclosure > summary { cursor: pointer; display: flex; flex-direction: column; gap: 2px; padding: 15px 17px; font-weight: 750; background: #f3f8fa; list-style-position: inside; }
.feature-disclosure > summary small { color: var(--subtle); font-weight: 450; font-size: .78rem; padding-left: 19px; }
.feature-disclosure[open] > summary { border-bottom: 1px solid var(--line); }
.feature-disclosure > .write-form { padding: 18px; }
.secondary-panel { padding: 0; }
.secondary-panel > .feature-disclosure { border: 0; border-radius: inherit; }
.secondary-panel > .feature-disclosure > summary { padding: 21px 23px; background: transparent; font-size: 1.03rem; }
.secondary-panel > .feature-disclosure > summary small { font-size: .83rem; }
.secondary-panel > .feature-disclosure[open] > :not(summary) { margin-left: 23px; margin-right: 23px; }
.secondary-panel > .feature-disclosure[open] > :last-child { margin-bottom: 23px; }
.compact-details { margin-top: 12px; font-size: .8rem; color: var(--subtle); }
.compact-details > summary { cursor: pointer; font-size: .8rem; color: var(--accent-dark); font-weight: 700; padding: 4px 0; }
.technical-detail { margin: -2px 0 0; }
.technical-detail label { margin-top: 10px; }
.inventory-details { border-top: 1px solid #e5edf1; padding-top: 12px; margin-top: 18px; }
.inventory-details .table-wrap { margin-top: 12px; }
.note, .muted { color: var(--subtle); }
.note { font-size: .79rem; margin: 8px 0 15px; }
.controls { display: flex; flex-wrap: wrap; gap: 12px; margin: 14px 0 18px; }
.controls label { display: grid; gap: 6px; font-size: .82rem; color: #476071; }
.metric-evidence { display: block; font-size: .71rem; color: var(--subtle); overflow-wrap: anywhere; }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 9px; }
table { width: 100%; border-collapse: collapse; font-size: .8rem; }
th, td { padding: 10px 9px; border-bottom: 1px solid #e6edf2; text-align: left; white-space: nowrap; }
tr:last-child td { border-bottom: 0; }
th { color: #4f6573; background: #f4f8fa; font-size: .75rem; }
td.title { min-width: 180px; white-space: normal; }
.state { display: inline-block; flex: 0 0 auto; font-size: .75rem; font-weight: 750; padding: 4px 8px; border-radius: 999px; background: #eff2f5; color: #6b7785; }
.state.active { color: #166448; background: #e6f4eb; }
.state.paused, .state.pending { color: #925c17; background: #fff2dd; }
.state.absent { color: #697a89; }
.row-action { padding: 6px 10px; font-size: .77rem; }
.empty { margin: 8px 0 0; color: var(--subtle); }
.chart { display: grid; gap: 8px; margin: 10px 0 16px; }
.chart[hidden] { display: none; }
.chart-row { display: grid; grid-template-columns: minmax(90px,180px) minmax(120px,1fr) auto; align-items: center; gap: 10px; }
.chart-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #455b6a; }
.chart-progress { width: 100%; height: 12px; border: 0; border-radius: 999px; overflow: hidden; appearance: none; background: #e5edf2; }
.chart-progress::-webkit-progress-bar { background: #e5edf2; border-radius: 999px; }
.chart-progress::-webkit-progress-value { background: var(--accent); border-radius: 999px; }
.chart-progress::-moz-progress-bar { background: var(--accent); border-radius: 999px; }
.chart-value { min-width: 7.5em; text-align: right; font-variant-numeric: tabular-nums; color: var(--subtle); }
@media (min-width: 1060px) { .management-panel { position: sticky; top: 18px; } }
@media (max-width: 1100px) { .cards { grid-template-columns: repeat(3,minmax(0,1fr)); } }
@media (max-width: 920px) { .workspace { grid-template-columns: minmax(0,1fr); } }
@media (max-width: 640px) {
  main { padding: 18px 12px 42px; }
  .app-header { align-items: flex-start; flex-direction: column; gap: 14px; }
  .cards { grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; }
  .cards article { padding: 12px; }
  .panel { padding: 16px; border-radius: 12px; }
  .section-heading { flex-wrap: wrap; }
  .section-label { text-align: left; }
  .write-actions button { flex: 1 1 auto; }
  .catalog-toolbar { grid-template-columns: minmax(0,1fr); }
  .catalog-toolbar button { width: 100%; }
  .write-actions button.danger { margin-left: 0; }
  .chart-row { grid-template-columns: minmax(70px, 110px) minmax(0, 1fr); }
  .chart-value { grid-column: 2; min-width: 0; white-space: normal; overflow-wrap: anywhere; }
}
"""
_DASHBOARD_JS = """
const byId = (id) => document.getElementById(id);

// Optional presentational controls must not affect headless core write/recovery tests.
const optionalById = (id) => {
  if (typeof document === "undefined" || typeof document.getElementById !== "function") return null;
  try { return document.getElementById(id); } catch (_error) { return null; }
};

const display = (value) => value === null || value === undefined ? "—" : String(value);
const WRITE_TOKEN_STORAGE_KEY = "mark-dashboard-write-token";
const GENERATION_TOKEN_STORAGE_KEY = "mark-dashboard-generation-token";
const pendingWrites = new Map();
// Only this page session may remember an observed terminal result.
const pendingAckOnly = new Map();
let writeUiAvailable = false;
let manualSyncAvailable = false;
let syncInFlight = false;
let writeToken = null;
let generationToken = null;
let generationUiAvailable = false;
let generationBusy = false;
let generationImageBlob = null;
let generationImageSourcePrompt = null;
let generationImagePreviewUrl = null;
let generationTextDraft = null;
let pendingRecoveryBlocked = false;
let createInFlight = false;
let latestAdsById = new Map();
const adCardElements = new Map();
// Local selection only stages IDs for export; never grants or performs Writes.
const catalogSelectedIds = new Set();
let catalogRows = [];
let catalogCursors = [""];
let catalogPageIndex = 0;
let catalogNextAfter = null;
let catalogLoading = false;
let catalogGeneration = 0;
let catalogFilters = {search: "", status: "all"};
let draftCursors = [""];
let draftPageIndex = 0;
let draftNextAfter = null;
let draftSearch = "";
let draftBusy = false;
let draftLoaded = false;
const draftSelectedSkus = new Set();
let jobsAvailable = false;
let jobsLoaded = false;
let jobActiveId = "";
let jobPageIndex = 0;
let jobCursors = [""];
let jobNextAfter = null;
let jobBusy = false;
let jobDetailGeneration = 0;
let jobDisplayedId = "";
let jobReviewSelected = new Set();
let jobPendingRequest = null;
let jobBatchBusy = false;
let jobBatchStop = false;
let managedAdBaseline = null;

function consumeWriteToken() {
  const hashParams = new URLSearchParams(window.location.hash.slice(1));
  const candidate = hashParams.get("write_token");
  if (
    candidate
    && candidate.length >= 16
    && candidate.length <= 512
    && !/\\s/.test(candidate)
  ) {
    writeToken = candidate;
    try {
      window.sessionStorage.setItem(WRITE_TOKEN_STORAGE_KEY, candidate);
    } catch (_error) {
      // In-memory token remains usable for this page.
    }
  } else {
    try {
      writeToken = window.sessionStorage.getItem(WRITE_TOKEN_STORAGE_KEY);
    } catch (_error) {
      writeToken = null;
    }
  }
  const genCandidate = hashParams.get("generation_token");
  if (
    genCandidate
    && genCandidate.length >= 16
    && genCandidate.length <= 512
    && Array.from(genCandidate).every((char) => {
      const code = char.charCodeAt(0);
      return code >= 33 && code <= 126;
    })
  ) {
    generationToken = genCandidate;
    try {
      window.sessionStorage.setItem(GENERATION_TOKEN_STORAGE_KEY, genCandidate);
    } catch (_error) {
      // Per-process token remains only in this tab.
    }
  } else {
    try {
      generationToken = window.sessionStorage.getItem(GENERATION_TOKEN_STORAGE_KEY);
    } catch (_error) {
      generationToken = null;
    }
  }
  if (window.location.hash) {
    window.history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search,
    );
  }
}

function writeUiReady() {
  return (
    writeUiAvailable
    && typeof writeToken === "string"
    && writeToken.length >= 16
    && !pendingRecoveryBlocked
  );
}

function validPendingEntry(entry) {
  if (!entry || typeof entry !== "object") return false;
  if (
    typeof entry.scope !== "string"
    || typeof entry.key !== "string"
    || typeof entry.method !== "string"
    || typeof entry.path !== "string"
  ) {
    return false;
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(entry.key)) {
    return false;
  }
  if (!["POST", "PATCH", "DELETE"].includes(entry.method)) return false;
  if (!entry.path.startsWith("/api/write/")) return false;
  if (
    entry.payload !== null
    && (
      typeof entry.payload !== "object"
      || Array.isArray(entry.payload)
    )
  ) {
    return false;
  }
  if (
    entry.adId !== null
    && entry.adId !== undefined
    && (typeof entry.adId !== "string" || !/^[0-9]{1,32}$/.test(entry.adId))
  ) {
    return false;
  }
  if (
    entry.acknowledged !== undefined
    && typeof entry.acknowledged !== "boolean"
  ) {
    return false;
  }
  return true;
}

async function acknowledgePendingWrite(entry) {
  const response = await fetch("/api/dashboard/pending-writes/ack", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Mark-Dashboard-Write": "1",
      "X-Mark-Dashboard-Token": writeToken,
    },
    body: JSON.stringify({
      scope: entry.scope,
      idempotency_key: entry.key,
    }),
    cache: "no-store",
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch (_error) {
    throw new Error("pending recovery acknowledgement invalid");
  }
  if (!response.ok || payload.acknowledged !== true) {
    throw new Error(payload.error ?? "pending recovery acknowledgement failed");
  }
}

async function refreshPendingWrites() {
  pendingWrites.clear();
  pendingRecoveryBlocked = false;
  if (
    !writeUiAvailable
    || typeof writeToken !== "string"
    || writeToken.length < 16
  ) {
    return;
  }
  try {
    const response = await fetch("/api/dashboard/pending-writes", {
      headers: {
        "X-Mark-Dashboard-Write": "1",
        "X-Mark-Dashboard-Token": writeToken,
      },
      cache: "no-store",
    });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : {};
    if (
      !response.ok
      || !payload
      || !Array.isArray(payload.pending_writes)
      || payload.pending_writes.some((entry) => !validPendingEntry(entry))
    ) {
      throw new Error("pending recovery unavailable");
    }
    // A legacy acknowledged tombstone is not independent evidence of an
    // external outcome. Keep it visible as a blocking recovery intent.
    for (const entry of payload.pending_writes) {
      if (pendingWrites.has(entry.scope)) {
        throw new Error("duplicate pending scope");
      }
      const prior = pendingAckOnly.get(entry.scope);
      if (
        prior !== undefined
        && prior.key === entry.key
        && prior.method === entry.method
        && prior.path === entry.path
      ) {
        entry.acknowledgeOnly = true;
      }
      pendingWrites.set(entry.scope, entry);
    }
    // A replaced or absent durable key must not inherit old browser
    // ACK-only evidence. Failed GET leaves this cache intact and fail-closed.
    for (const [scope, prior] of pendingAckOnly) {
      const current = pendingWrites.get(scope);
      if (
        current === undefined
        || current.acknowledgeOnly !== true
        || current.key !== prior.key
      ) {
        pendingAckOnly.delete(scope);
      }
    }
  } catch (_error) {
    pendingWrites.clear();
    pendingRecoveryBlocked = true;
  }
}

function setWriteStatus(message, kind = "warning") {
  const status = byId("write-status");
  status.textContent = message;
  status.className = `status write-status ${kind}`;
}

function renderWriteAvailability() {
  byId("write-panel").hidden = !writeUiAvailable;
  byId("write-unavailable").hidden = writeUiAvailable;
  // Hide navigation that points to a form absent from a standalone read-only view.
  const createNavigation = typeof optionalById === "function"
    ? optionalById("nav-create") : null;
  if (createNavigation) createNavigation.hidden = !writeUiAvailable;
  if (!writeUiAvailable) return;
  if (pendingRecoveryBlocked) {
    byId("write-session-note").hidden = true;
    for (const element of document.querySelectorAll("[data-write-control]")) {
      element.disabled = true;
    }
    setWriteStatus(
      "Der persistente Recovery-Status früherer Write-Anfragen ist nicht sicher lesbar. Neue Writes bleiben fail-closed gesperrt.",
      "error",
    );
    return;
  }
  const ready = writeUiReady();
  byId("write-session-note").hidden = ready;
  for (const element of document.querySelectorAll("[data-write-control]")) {
    element.disabled = !ready;
  }
  if (ready && pendingWrites.size > 0) {
    setWriteStatus(
      "Eine frühere Write-Anfrage bleibt mit ihrem ursprünglichen Idempotency-Key gesperrt. Keine erneute Plattformweiterleitung ohne unabhängige Ergebnisprüfung.",
      "warning",
    );
  }
}

async function getJson(path) {
  const response = await fetch(path, {cache: "no-store"});
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function td(value, className = "") {
  const element = document.createElement("td");
  element.textContent = display(value);
  if (className) element.className = className;
  return element;
}

function ageDescription(observedAt) {
  const observedMs = Date.parse(observedAt);
  if (!Number.isFinite(observedMs)) return "Alter unbekannt";
  const elapsedMs = Date.now() - observedMs;
  if (elapsedMs < 0) return "Beobachtungszeit in der Zukunft";
  const minutes = Math.floor(elapsedMs / 60000);
  if (minutes < 1) return "vor weniger als 1 Minute";
  if (minutes < 60) return "vor " + minutes + (minutes === 1 ? " Minute" : " Minuten");
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return "vor " + hours + (hours === 1 ? " Stunde" : " Stunden");
  const days = Math.floor(hours / 24);
  return "vor " + days + (days === 1 ? " Tag" : " Tagen");
}

function renderSyncStatus(sync, recovery) {
  const view = byId("sync-status");
  if (!sync || typeof sync !== "object") {
    view.textContent = "Sync-Status nicht verfügbar; Datenfrische unbekannt.";
    return;
  }
  const latest = sync.latest_attempt;
  const successful = sync.last_successful_attempt;
  const state = sync.state;
  let headline = "Noch kein Eigentümer-Sync protokolliert.";
  if (latest && typeof latest === "object") {
    const ago = ageDescription(latest.started_at);
    const source = typeof latest.source === "string" ? latest.source : "unbekannt";
    if (state === "in_progress") {
      headline = "Letzter Sync gestartet " + ago
        + " (" + source + "); kein bestätigter Abschluss. Läuft oder wurde unterbrochen.";
    } else if (state === "failed") {
      const error = typeof latest.error_kind === "string"
        ? latest.error_kind : "unbekannt";
      headline = "Letzter Sync fehlgeschlagen (" + error + ") · "
        + ageDescription(latest.completed_at) + " · Quelle: " + source + ".";
    } else if (state === "success_empty" || state === "success_nonempty") {
      const count = state === "success_empty" ? 0 : latest.snapshot_count;
      headline = "Letzter Sync erfolgreich · "
        + (state === "success_empty"
          ? "bestätigter leerer Besitzerbestand"
          : String(count) + " Anzeigen")
        + " · " + ageDescription(latest.completed_at)
        + " · Quelle: " + source + ".";
    }
  }
  const baseline = successful && typeof successful === "object"
    ? " Letzte bestätigte Bestandsgrundlage: "
      + successful.completed_at + " ("
      + ageDescription(successful.completed_at) + "), Quelle: "
      + successful.source + "."
    : " Keine bestätigte Bestandsgrundlage aus diesem Journal.";
  let recoveryNote = " Offene ältere Sync-Versuche konnten nicht geprüft werden.";
  if (recovery && typeof recovery === "object") {
    const open = recovery.unresolved_count;
    if (Number.isSafeInteger(open) && open === 0) {
      recoveryNote = " Zum Abfragezeitpunkt keine offenen Sync-Versuche.";
    } else if (Number.isSafeInteger(open) && open > 0) {
      const oldest = recovery.oldest_unresolved_attempt;
      const oldestId = oldest && Number.isSafeInteger(oldest.id)
        ? " (ältester Versuch #" + oldest.id + ")" : "";
      recoveryNote = " Achtung: " + open + " noch nicht abgeschlossene Sync-Versuche"
        + oldestId + "; möglicherweise noch aktiv oder unterbrochen."
        + " Unbekannte Ergebnisse dürfen nicht automatisch wiederholt werden.";
    }
  }
  view.textContent = headline + baseline + recoveryNote
    + " Das Alter einzelner Zähler ist separat ausgewiesen.";

  const short = optionalById("sync-overview");
  if (short) {
    const open = recovery && typeof recovery === "object" ? recovery.unresolved_count : null;
    if (Number.isSafeInteger(open) && open > 0) {
      short.textContent = open + " offene Synchronisierung(en) prüfen";
      short.className = "section-label warning";
    } else if (state === "failed") {
      short.textContent = "Aktualisierung prüfen";
      short.className = "section-label warning";
    } else if (latest && (state === "success_empty" || state === "success_nonempty")) {
      short.textContent = "Stand: " + ageDescription(latest.completed_at);
      short.className = "section-label";
    } else {
      short.textContent = "Stand unbekannt";
      short.className = "section-label";
    }
  }
}

function metricCellWithEvidence(value, evidence) {
  const cell = td(value);
  if (
    value === null || value === undefined
    || evidence === null || evidence === undefined || typeof evidence !== "object"
  ) return cell;
  const note = document.createElement("small");
  note.className = "metric-evidence";
  const evidenceKind = evidence.last_known === true
    ? "letzter bekannter Wert"
    : evidence.last_known === false
      ? "direkt beobachtet"
      : "eigenständige Evidenz";
  const observedAt = typeof evidence.observed_at === "string"
    ? evidence.observed_at : "Zeitpunkt unbekannt";
  const source = typeof evidence.source === "string"
    ? evidence.source : "Quelle unbekannt";
  note.textContent = evidenceKind + " · " + ageDescription(observedAt)
    + " · " + observedAt + " · Quelle: " + source;
  cell.append(note);
  return cell;
}

function managedBaselineFor(ad) {
  const titleKnown = typeof ad.title === "string";
  const descriptionKnown = typeof ad.description === "string";
  return {
    adId: ad.ad_id,
    titleKnown,
    title: titleKnown ? ad.title : null,
    descriptionKnown,
    description: descriptionKnown ? ad.description : null,
  };
}

function highlightManagedCard(adId) {
  for (const [id, card] of adCardElements) {
    card.className = id === adId ? "ad-card selected" : "ad-card";
  }
}

function revealCreateForm() {
  const disclosure = optionalById("create-disclosure");
  if (disclosure) disclosure.open = true;
}

function clearManagedSelection() {
  managedAdBaseline = null;
  byId("manage-ad-select").value = "";
  byId("manage-ad-id").value = "";
  byId("manage-title").value = "";
  byId("manage-description").value = "";
  byId("manage-state").textContent = "—";
  highlightManagedCard("");
}

function populateManageForm(ad) {
  managedAdBaseline = managedBaselineFor(ad);
  byId("manage-ad-select").value = ad.ad_id;
  byId("manage-ad-id").value = ad.ad_id;
  byId("manage-title").value = managedAdBaseline.titleKnown ? managedAdBaseline.title : "";
  byId("manage-description").value = managedAdBaseline.descriptionKnown
    ? managedAdBaseline.description
    : "";
  byId("manage-state").textContent = ad.lifecycle_state ?? "—";
  highlightManagedCard(ad.ad_id);
  byId("manage-form").scrollIntoView({behavior: "smooth", block: "nearest"});
}

function rebaseManagedBaseline(adId, payload) {
  if (byId("manage-ad-id").value !== adId) return;
  if (managedAdBaseline === null || managedAdBaseline.adId !== adId) return;
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) return;

  const next = {...managedAdBaseline};
  if (
    Object.prototype.hasOwnProperty.call(payload, "title")
    && typeof payload.title === "string"
  ) {
    next.titleKnown = true;
    next.title = payload.title;
  }
  if (
    Object.prototype.hasOwnProperty.call(payload, "description")
    && typeof payload.description === "string"
  ) {
    next.descriptionKnown = true;
    next.description = payload.description;
  }
  managedAdBaseline = next;
}

function retireManagedBaseline(adId) {
  if (byId("manage-ad-id").value !== adId) return;
  if (managedAdBaseline === null || managedAdBaseline.adId !== adId) return;
  managedAdBaseline = null;
  setWriteStatus(
    "Ausgang des Content-Updates ist unklar. Derselbe Inhalt wird nicht mit einem neuen Idempotency-Key erneut gesendet. Anzeige über „Verwalten“ neu auswählen, bevor ein neuer Content-Write gestartet wird.",
    "warning",
  );
}

function managedContentChanges(adId) {
  if (managedAdBaseline === null || managedAdBaseline.adId !== adId) {
    throw new Error("Management-Ausgangswerte sind nicht mehr eindeutig gebunden.");
  }
  const payload = {};
  const title = byId("manage-title").value;
  const description = byId("manage-description").value;
  if (
    managedAdBaseline.titleKnown
      ? title !== managedAdBaseline.title
      : title !== ""
  ) {
    payload.title = title;
  }
  if (
    managedAdBaseline.descriptionKnown
      ? description !== managedAdBaseline.description
      : description !== ""
  ) {
    payload.description = description;
  }
  return payload;
}


function renderCatalogSelection() {
  // Existing standalone/legacy Dashboard render harnesses have no portfolio
  // toolbar. Selection remains inert rather than changing their Write flows.
  const count = catalogSelectedIds.size;
  const label = optionalById("catalog-selected-count");
  const clear = optionalById("catalog-clear");
  const exportControl = optionalById("catalog-export");
  if (label) label.textContent = count + " ausgewählt";
  if (clear) clear.disabled = count === 0;
  if (exportControl) exportControl.disabled = count === 0;
}

function catalogCheckbox(ad) {
  const wrapper = document.createElement("label");
  wrapper.className = "catalog-select";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = catalogSelectedIds.has(ad.ad_id);
  checkbox.title = "Anzeige " + ad.ad_id + " in Arbeitsauswahl übernehmen";
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) catalogSelectedIds.add(ad.ad_id);
    else catalogSelectedIds.delete(ad.ad_id);
    for (const peer of Array.from(document.querySelectorAll(".catalog-select input"))) {
      if (peer.title === checkbox.title) peer.checked = checkbox.checked;
    }
    renderCatalogSelection();
  });
  wrapper.append(checkbox);
  return wrapper;
}

function renderCatalogNavigation() {
  byId("catalog-prev").disabled = catalogLoading || catalogPageIndex === 0;
  byId("catalog-next").disabled = catalogLoading || catalogNextAfter === null;
  byId("catalog-find").disabled = catalogLoading;
  byId("catalog-page-label").textContent = "Seite " + (catalogPageIndex + 1);
  renderCatalogSelection();
}

async function loadCatalogPage({reset = false} = {}) {
  if (reset) {
    catalogCursors = [""];
    catalogPageIndex = 0;
    catalogNextAfter = null;
  }
  const generation = ++catalogGeneration;
  catalogLoading = true;
  renderCatalogNavigation();
  byId("catalog-feedback").textContent = "Lade Anzeigen …";
  const params = new URLSearchParams({
    limit: "50",
    after: catalogCursors[catalogPageIndex],
    q: catalogFilters.search,
    status: catalogFilters.status,
  });
  try {
    const payload = await getJson("/api/catalog/ads?" + params.toString());
    if (generation !== catalogGeneration) return false;
    if (!payload || !Array.isArray(payload.ads) || payload.ads.length > 50
        || typeof payload.has_more !== "boolean"
        || (payload.has_more && (typeof payload.next_after !== "string" || !payload.next_after))) {
      throw new Error("Ungültige Katalogantwort");
    }
    catalogNextAfter = payload.has_more ? payload.next_after : null;
    catalogRows = payload.ads;
    renderAds(payload.ads);
    byId("catalog-feedback").textContent = payload.ads.length +
      " Anzeigen auf dieser Seite" +
      (catalogFilters.search || catalogFilters.status !== "all" ? " · gefiltert" : "");
    return true;
  } catch (_error) {
    if (generation === catalogGeneration) {
      byId("catalog-feedback").textContent =
        "Anzeigen konnten nicht geladen werden. Vorhandene Daten bleiben erhalten.";
      catalogNextAfter = null;
    }
    return false;
  } finally {
    if (generation === catalogGeneration) {
      catalogLoading = false;
      renderCatalogNavigation();
    }
  }
}

// Navigation is transactional: on a failed GET, old rows and their page
// label/cursor remain bound. A newer search must never be rolled back by an
// older HTTP completion.
async function changeCatalogPage({index, cursors, filters, reset = false}) {
  const previous = {
    index: catalogPageIndex, cursors: catalogCursors.slice(),
    nextAfter: catalogNextAfter, filters: catalogFilters,
  };
  catalogPageIndex = index;
  catalogCursors = cursors;
  catalogFilters = filters;
  const request = loadCatalogPage({reset});
  const generation = catalogGeneration;
  const success = await request;
  if (!success && generation === catalogGeneration) {
    catalogPageIndex = previous.index;
    catalogCursors = previous.cursors;
    catalogNextAfter = previous.nextAfter;
    catalogFilters = previous.filters;
    renderCatalogNavigation();
  }
  return success;
}

function searchCatalog() {
  return changeCatalogPage({
    index: 0, cursors: [""], reset: true,
    filters: {
      search: byId("catalog-search").value.trim(),
      status: byId("catalog-status").value,
    },
  });
}

function nextCatalogPage() {
  if (catalogLoading || catalogNextAfter === null) return;
  const cursors = catalogCursors.slice(0, catalogPageIndex + 1);
  cursors.push(catalogNextAfter);
  return changeCatalogPage({
    index: catalogPageIndex + 1, cursors, filters: catalogFilters,
  });
}

function previousCatalogPage() {
  if (catalogLoading || catalogPageIndex <= 0) return;
  return changeCatalogPage({
    index: catalogPageIndex - 1,
    cursors: catalogCursors.slice(), filters: catalogFilters,
  });
}

function clearCatalogSelection() {
  catalogSelectedIds.clear();
  renderAds(catalogRows);
}

function exportCatalogSelection() {
  if (!catalogSelectedIds.size) return;
  const safe = (value) => {
    // trimStart also recognizes leading whitespace before spreadsheet formulas.
    const first = value.trimStart().charAt(0);
    const normalized = first && "=+-@".includes(first) ? "'" + value : value;
    return '"' + normalized.replaceAll('"','""') + '"';
  };
  const rows = ["anzeigen_id", ...Array.from(catalogSelectedIds).sort().map(safe)];
  const separator = String.fromCharCode(13, 10);
  const blob = new Blob([String.fromCharCode(65279) + rows.join(separator) + separator],
                        {type: "text/csv;charset=utf-8"});
  const link = document.createElement("a");
  const url = URL.createObjectURL(blob);
  try {
    link.href = url;
    link.download = "kleinanzeiger-auswahl.csv";
    link.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

function renderAds(ads) {
  latestAdsById = new Map(ads.map((ad) => [ad.ad_id, ad]));
  catalogRows = ads;
  const picker = byId("manage-ad-select");
  const previouslySelected = picker.value;
  const eligibleIds = new Set();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Anzeige auswählen …";
  picker.replaceChildren(placeholder);
  for (const ad of ads) {
    if (!writeUiReady() || !(ad.present || pendingForAd(ad.ad_id) !== null)) continue;
    eligibleIds.add(ad.ad_id);
    const option = document.createElement("option");
    option.value = ad.ad_id;
    option.textContent = `${ad.title || "Ohne Titel"} · ${ad.ad_id} · ${ad.lifecycle_state ?? "unbekannt"}`;
    picker.append(option);
  }
  if (eligibleIds.has(previouslySelected)) {
    picker.value = previouslySelected;
  } else {
    clearManagedSelection();
  }
  const cards = optionalById("ad-cards");
  adCardElements.clear();
  if (cards && typeof cards.replaceChildren === "function") {
    cards.replaceChildren();
    const empty = optionalById("ad-cards-empty");
    if (empty) empty.hidden = ads.length !== 0;
    const ordered = [
      ...ads.filter((ad) => ad.present !== false),
      ...ads.filter((ad) => ad.present === false),
    ];
    for (const ad of ordered) {
      const card = document.createElement("article");
      card.className = "ad-card";
      const heading = document.createElement("div");
      heading.className = "ad-card-head";
      const name = document.createElement("div");
      const title = document.createElement("h3");
      title.textContent = ad.title || "Ohne Titel";
      const id = document.createElement("p");
      id.className = "ad-id";
      id.textContent = "ID " + ad.ad_id;
      name.append(title, id);
      const state = document.createElement("span");
      state.className = "state " + (ad.lifecycle_state ?? "");
      const stateLabels = {active: "Aktiv", paused: "Pausiert", absent: "Entfernt", pending: "Offen"};
      state.textContent = stateLabels[ad.lifecycle_state] ?? (ad.lifecycle_state || "Unbekannt");
      heading.append(name, state);
      const footer = document.createElement("div");
      footer.className = "ad-card-footer";
      const views = document.createElement("span");
      views.className = "ad-views";
      views.textContent = "Aufrufe: " + display(ad.views);
      footer.append(views);
      footer.append(catalogCheckbox(ad));
      if (writeUiReady() && (ad.present || pendingForAd(ad.ad_id) !== null)) {
        const action = document.createElement("button");
        action.type = "button";
        action.className = "ad-card-action";
        action.textContent = ad.present ? "Bearbeiten" : "Recovery prüfen";
        action.title = (ad.title || "Ohne Titel") + " verwalten";
        action.addEventListener("click", () => populateManageForm(ad));
        footer.append(action);
      }
      card.append(heading, footer);
      cards.append(card);
      adCardElements.set(ad.ad_id, card);
    }
    highlightManagedCard(picker.value);
  }
  const body = byId("ads-body");
  body.replaceChildren();
  byId("empty").hidden = ads.length !== 0;

  for (const ad of ads) {
    const row = document.createElement("tr");
    const selectionCell = document.createElement("td");
    selectionCell.append(catalogCheckbox(ad));
    row.append(selectionCell);
    row.append(td(ad.ad_id));
    row.append(td(ad.title, "title"));

    const stateCell = document.createElement("td");
    const state = document.createElement("span");
    state.className = `state ${ad.lifecycle_state}`;
    state.textContent = ad.lifecycle_state;
    stateCell.append(state);
    row.append(stateCell);

    row.append(metricCellWithEvidence(ad.views, ad.metric_evidence?.views));
    row.append(metricCellWithEvidence(ad.watch_count, ad.metric_evidence?.watch_count));
    row.append(metricCellWithEvidence(ad.reply_count, ad.metric_evidence?.reply_count));
    row.append(td(ad.observed_at));

    const actionCell = document.createElement("td");
    const pending = pendingForAd(ad.ad_id);
    if (writeUiReady() && (ad.present || pending !== null)) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "row-action";
      button.textContent = ad.present ? "Verwalten" : "Recovery";
      button.addEventListener("click", () => populateManageForm(ad));
      actionCell.append(button);
    }
    row.append(actionCell);
    body.append(row);
  }
  renderCatalogSelection();
}

function setOptions(select, values, placeholder = null) {
  select.replaceChildren();
  if (placeholder !== null) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = placeholder;
    select.append(option);
  }
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  }
}

function renderBarChart(containerId, items, valueOf, labelOf, formatValue) {
  const container = byId(containerId);
  container.replaceChildren();
  const rows = [];
  for (const item of items) {
    const rawValue = valueOf(item);
    if (rawValue === null || rawValue === undefined) continue;
    const value = Number(rawValue);
    if (!Number.isFinite(value) || value < 0) continue;
    rows.push({item, value});
  }
  container.hidden = rows.length === 0;
  if (rows.length === 0) return;

  const max = Math.max(...rows.map((entry) => entry.value), 0);
  for (const entry of rows) {
    const row = document.createElement("div");
    row.className = "chart-row";
    row.setAttribute("role", "listitem");

    const label = document.createElement("span");
    label.className = "chart-label";
    label.textContent = String(labelOf(entry.item));

    const progress = document.createElement("progress");
    progress.className = "chart-progress";
    progress.max = max === 0 ? 1 : max;
    progress.value = entry.value;
    progress.setAttribute("aria-hidden", "true");

    const value = document.createElement("span");
    value.className = "chart-value";
    value.textContent = formatValue(entry.value, entry.item);

    row.append(label, progress, value);
    container.append(row);
  }
}

function renderGroups(groups) {
  const body = byId("groups-body");
  body.replaceChildren();
  byId("groups-empty").hidden = groups.length !== 0;
  for (const group of groups) {
    const row = document.createElement("tr");
    row.append(td(group.label));
    row.append(td(group.sample_size));
    row.append(td(group.metric_sum));
    row.append(td(Number(group.metric_mean).toFixed(2)));
    body.append(row);
  }
  renderBarChart(
    "groups-chart",
    groups,
    (group) => group.metric_mean,
    (group) => group.label,
    (value, group) => `${value.toFixed(2)} (n=${group.sample_size})`,
  );
}

let analyticsRequestGeneration = 0;

function renderAnalyticsContract(contract) {
  const reaction = contract.reaction_metric ?? "nicht festgelegt";
  const objective = contract.objective_metric ?? "nicht festgelegt";
  byId("analytics-contract").textContent =
    `Reaktionsmetrik: ${reaction}; Optimierungsziel: ${objective}. Rankings werden nur nach explizit ausgewählter Metrik geladen.`;
}

function renderRanking(items) {
  const body = byId("ranking-body");
  body.replaceChildren();
  byId("ranking-empty").hidden = items.length !== 0;
  for (const item of items) {
    const row = document.createElement("tr");
    row.append(td(item.ad_id));
    row.append(td(item.title, "title"));
    row.append(metricCellWithEvidence(item.value, item.metric_evidence));
    row.append(td(item.present === null ? "—" : (item.present ? "ja" : "nein")));
    row.append(td(item.lifecycle_state));
    body.append(row);
  }
  renderBarChart(
    "ranking-chart",
    items,
    (item) => item.value,
    (item) => item.ad_id,
    (value) => String(value),
  );
}

function newIdempotencyKey() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return `ui:${globalThis.crypto.randomUUID()}`;
  }
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return "ui:" + Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function pendingForAd(adId) {
  for (const entry of pendingWrites.values()) {
    if (entry.adId === adId) return entry;
  }
  return null;
}

async function proxyRequest(method, path, {body = null, contentType = null, filename = null, idempotencyKey = null} = {}) {
  if (!writeUiReady()) {
    throw new Error("Write-Sitzung ist nicht aktiv.");
  }
  const headers = {
    "X-Mark-Dashboard-Write": "1",
    "X-Mark-Dashboard-Token": writeToken,
  };
  if (contentType !== null) headers["Content-Type"] = contentType;
  if (filename !== null) headers["X-Mark-Media-Filename"] = filename;
  if (idempotencyKey !== null) headers["Idempotency-Key"] = idempotencyKey;

  const response = await fetch(path, {
    method,
    headers,
    body,
    cache: "no-store",
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch (_error) {
    throw new Error("Ungültige Antwort der lokalen Write API.");
  }
  return {response, payload};
}

function completedWriteResponse(entry, payload) {
  return payload && payload.idempotency_key === entry.key;
}

const DETERMINISTIC_PRE_EXECUTION_ERRORS = new Set([
  "invalid_create_request",
  "invalid_media_create_request",
  "invalid_media_refs",
  "invalid_content_fields",
  "title_must_be_string",
  "description_must_be_string",
  "invalid_unicode_text",
]);

function canClearPendingAfterError(response, payload, retryingPending = false) {
  if (!payload || typeof payload.error !== "string") return false;
  if (
    payload.error === "idempotency_in_progress"
    || payload.error === "idempotency_conflict"
    || payload.error === "write_proxy_transport_unknown"
  ) {
    return false;
  }
  if (response.status < 400 || response.status >= 500) return false;
  if (!retryingPending) return true;
  return (
    response.status === 400
    && DETERMINISTIC_PRE_EXECUTION_ERRORS.has(payload.error)
  );
}

function canClearCompletedWrite(_response, _payload) {
  // completedWriteResponse already established the exact persisted
  // Idempotency-Key returned by the Write API.
  return true;
}

function confirmedWriteResult(result) {
  return (
    result !== null
    && result.response.status === 200
    && result.payload?.operation_receipt?.outcome === "confirmed"
  );
}

function unknownEffectTerminalWriteResult(result) {
  return (
    result !== null
    && (
      (
        result.response.status === 202
        && result.payload?.operation_receipt?.outcome === "ambiguous"
      )
      || (
        result.response.status === 500
        && result.payload?.error === "write_execution_error"
      )
    )
  );
}

function terminalCreateRetirementResult(result) {
  const outcome = result?.payload?.operation_receipt?.outcome;
  return (
    result !== null
    && (
      (result.response.status === 200 && outcome === "confirmed")
      || (
        result.response.status === 202
        && (outcome === "ambiguous" || outcome === "confirmed")
      )
      || (
        result.response.status === 500
        && result.payload?.error === "write_execution_error"
      )
    )
  );
}

function captureCreateDraft(files = null) {
  return {
    category: byId("create-category").value,
    title: byId("create-title").value,
    description: byId("create-description").value,
    price: byId("create-price").value,
    files: Array.from(files ?? byId("create-media").files ?? []),
  };
}

function createDraftMatchesSnapshot(snapshot) {
  if (snapshot === null || typeof snapshot !== "object") return false;
  if (
    byId("create-category").value !== snapshot.category
    || byId("create-title").value !== snapshot.title
    || byId("create-description").value !== snapshot.description
    || byId("create-price").value !== snapshot.price
    || !Array.isArray(snapshot.files)
  ) {
    return false;
  }
  const currentFiles = Array.from(byId("create-media").files ?? []);
  return (
    currentFiles.length === snapshot.files.length
    && currentFiles.every((file, index) => file === snapshot.files[index])
  );
}

function createDraftMatchesPendingEntry(entry) {
  if (
    entry === null
    || typeof entry !== "object"
    || !["create", "create-media"].includes(entry.scope)
    || entry.payload === null
    || typeof entry.payload !== "object"
    || Array.isArray(entry.payload)
  ) {
    return false;
  }
  let currentPayload;
  try {
    currentPayload = createPayload();
  } catch (_error) {
    return false;
  }
  const expected = entry.payload;
  if (
    !Array.isArray(expected.category_path)
    || currentPayload.category_path.length !== expected.category_path.length
    || currentPayload.category_path.some(
      (label, index) => label !== expected.category_path[index],
    )
    || currentPayload.title !== expected.title
    || currentPayload.description !== expected.description
    || currentPayload.price_eur !== expected.price_eur
  ) {
    return false;
  }
  return Array.from(byId("create-media").files ?? []).length === 0;
}

function retireCreateDraft() {
  byId("create-category").value = "";
  byId("create-title").value = "";
  byId("create-description").value = "";
  byId("create-price").value = "";
  byId("create-media").value = "";
}

function retireCreateBeforeAcknowledge(result, entry) {
  if (!terminalCreateRetirementResult(result)) return;
  const draftStillSubmitted = (
    entry?.createDraftSnapshot !== undefined
      ? createDraftMatchesSnapshot(entry.createDraftSnapshot)
      : createDraftMatchesPendingEntry(entry)
  );
  if (draftStillSubmitted) retireCreateDraft();
}

function settleManagedUpdateBeforeAcknowledge(adId, payload, result) {
  if (confirmedWriteResult(result)) {
    rebaseManagedBaseline(adId, payload);
  } else if (unknownEffectTerminalWriteResult(result)) {
    retireManagedBaseline(adId);
  }
}

async function refreshAfterSettledWrite() {
  try {
    await load();
  } catch (error) {
    setWriteStatus(
      `Write-Ergebnis ist geklärt, aber Dashboard-Aktualisierung fehlgeschlagen: ${error.message}`,
      "warning",
    );
  }
}

function describeWriteResult(response, payload) {
  if (payload.operation_receipt) {
    const outcome = payload.operation_receipt.outcome ?? "unknown";
    if (response.status === 200) {
      return {kind: "success", text: `Bestätigt: ${outcome}.`};
    }
    if (response.status === 202) {
      return {
        kind: "warning",
        text: `Ausgang unklar (${outcome}). Kein automatischer Plattform-Retry.`,
      };
    }
    return {kind: "error", text: `Write-Ergebnis: ${outcome} (HTTP ${response.status}).`};
  }
  if (payload.error) {
    const retry = payload.platform_retry_authorized === true
      ? " Retry autorisiert."
      : " Kein automatischer Plattform-Retry.";
    return {kind: response.ok ? "warning" : "error", text: `${payload.error} (HTTP ${response.status}).${retry}`};
  }
  return {kind: response.ok ? "success" : "error", text: `HTTP ${response.status}.`};
}

async function runPlatformWrite(
  scope,
  method,
  path,
  payload,
  {adId = null, beforeAcknowledge = null, createDraftSnapshot = null} = {},
) {
  let entry = pendingWrites.get(scope);
  const retryingPending = entry !== undefined;
  if (retryingPending && entry.acknowledgeOnly === true) {
    // A terminal result was already observed *in this browser runtime*.
    // Retry only the local UI ACK, never the prior platform request.
    try {
      await acknowledgePendingWrite(entry);
      pendingWrites.delete(scope);
      pendingAckOnly.delete(scope);
      await refreshAfterSettledWrite();
      setWriteStatus(
        "Bereits beobachtetes Write-Ergebnis lokal bestätigt. Keine erneute Plattformweiterleitung.",
        "warning",
      );
    } catch (error) {
      setWriteStatus(
        `Der lokale Recovery-ACK bleibt offen: ${error.message}. Kein neuer Plattformrequest.`,
        "warning",
      );
    }
    return null;
  }
  if (entry === undefined) {
    entry = {
      scope,
      key: newIdempotencyKey(),
      method,
      path,
      payload,
      adId,
    };
    if (createDraftSnapshot !== null) {
      entry.createDraftSnapshot = createDraftSnapshot;
    }
    pendingWrites.set(scope, entry);
  }

  try {
    const body = entry.payload === null ? null : JSON.stringify(entry.payload);
    const {response, payload: responsePayload} = await proxyRequest(
      entry.method,
      entry.path,
      {
        body,
        contentType: entry.payload === null ? null : "application/json",
        idempotencyKey: entry.key,
      },
    );
    const result = describeWriteResult(response, responsePayload);
    setWriteStatus(result.text, result.kind);

    if (completedWriteResponse(entry, responsePayload)) {
      if (canClearCompletedWrite(response, responsePayload)) {
        const completedResult = {response, payload: responsePayload};
        if (beforeAcknowledge !== null) {
          try {
            beforeAcknowledge(completedResult, entry);
          } catch (error) {
            setWriteStatus(
              "Write-Ergebnis ist terminal, aber lokales UI-Fencing ist fehlgeschlagen: "
                + error.message + ". Derselbe Request/Key bleibt gebunden.",
              "warning",
            );
            return completedResult;
          }
        }
        entry.acknowledgeOnly = true;
        pendingAckOnly.set(scope, {
          key: entry.key, method: entry.method, path: entry.path,
        });
        try {
          await acknowledgePendingWrite(entry);
        } catch (error) {
          // A denied local ACK must keep the original operation fence, but
          // must not prevent read-only dashboard refresh after the settled
          // backend response. Refresh neither retries nor clears the write.
          await refreshAfterSettledWrite();
          setWriteStatus(
            `Write-Ergebnis ist terminal, aber der lokale Recovery-ACK ist fehlgeschlagen: ${error.message}. Derselbe Request/Key bleibt gebunden.`,
            "warning",
          );
          return {response, payload: responsePayload};
        }
        pendingWrites.delete(scope);
        pendingAckOnly.delete(scope);
      }
      await refreshAfterSettledWrite();
    } else if (responsePayload.error === "dashboard_pending_write_conflict") {
      await refreshAfterSettledWrite();
    } else if (
      canClearPendingAfterError(
        response,
        responsePayload,
        retryingPending,
      )
    ) {
      entry.acknowledgeOnly = true;
      pendingAckOnly.set(scope, {
        key: entry.key, method: entry.method, path: entry.path,
      });
      try {
        await acknowledgePendingWrite(entry);
      } catch (error) {
        setWriteStatus(
          `Lokaler Recovery-ACK fehlgeschlagen: ${error.message}. Der Request bleibt vorsorglich gebunden.`,
          "warning",
        );
        return {response, payload: responsePayload};
      }
      pendingWrites.delete(scope);
      pendingAckOnly.delete(scope);
    }
    return {response, payload: responsePayload};
  } catch (error) {
    setWriteStatus(
      `Transportstatus unbekannt: ${error.message} Derselbe Idempotency-Key bleibt zur unabhängigen Recovery-Prüfung gebunden; eine erneute Plattformweiterleitung ist nicht autorisiert. Kein automatischer Retry.`,
      "warning",
    );
    return null;
  }
}

function isPythonWhitespace(character) {
  const codePoint = character.codePointAt(0);
  return (
    (codePoint >= 0x0009 && codePoint <= 0x000d)
    || (codePoint >= 0x001c && codePoint <= 0x0020)
    || codePoint === 0x0085
    || codePoint === 0x00a0
    || codePoint === 0x1680
    || (codePoint >= 0x2000 && codePoint <= 0x200a)
    || codePoint === 0x2028
    || codePoint === 0x2029
    || codePoint === 0x202f
    || codePoint === 0x205f
    || codePoint === 0x3000
  );
}

function pythonStrip(value) {
  const characters = Array.from(value);
  let start = 0;
  let end = characters.length;
  while (start < end && isPythonWhitespace(characters[start])) start += 1;
  while (end > start && isPythonWhitespace(characters[end - 1])) end -= 1;
  return characters.slice(start, end).join("");
}

function requireUnicodeScalarText(value, fieldName) {
  for (const character of value) {
    const codePoint = character.codePointAt(0);
    if (codePoint >= 0xd800 && codePoint <= 0xdfff) {
      throw new Error(fieldName + " darf keine ungültigen Unicode-Surrogates enthalten.");
    }
  }
}

function createPayload() {
  const rawCategoryPath = byId("create-category").value.split(">");
  if (rawCategoryPath.length < 2 || rawCategoryPath.length > 6) {
    throw new Error("Kategoriepfad muss zwischen 2 und 6 Labels enthalten.");
  }
  const categoryPath = rawCategoryPath.map((label) => {
    const normalized = pythonStrip(label);
    requireUnicodeScalarText(normalized, "Kategorie");
    if (!normalized) {
      throw new Error("Kategorie-Labels dürfen nicht leer sein.");
    }
    if (Array.from(normalized).length > 120) {
      throw new Error("Kategorie-Labels dürfen höchstens 120 Zeichen enthalten.");
    }
    return normalized;
  });
  if (new Set(categoryPath).size !== categoryPath.length) {
    throw new Error("Kategoriepfad darf keine doppelten Labels enthalten.");
  }

  const title = byId("create-title").value;
  requireUnicodeScalarText(title, "Titel");
  if (!pythonStrip(title)) {
    throw new Error("Titel darf nicht leer sein.");
  }
  if (title !== pythonStrip(title)) {
    throw new Error("Titel darf keine umgebenden Whitespaces enthalten.");
  }
  if (title.includes("\\r") || title.includes("\\n")) {
    throw new Error("Titel darf keine Zeilenumbrüche enthalten.");
  }
  if (title.length > 65) {
    throw new Error("Titel darf höchstens 65 UTF-16-Code-Units enthalten.");
  }

  const description = byId("create-description").value
    .replace(/\\r\\n/g, "\\n")
    .replace(/\\r/g, "\\n");
  requireUnicodeScalarText(description, "Beschreibung");
  if (!pythonStrip(description)) {
    throw new Error("Beschreibung darf nicht leer sein.");
  }
  if (description.length > 4000) {
    throw new Error("Beschreibung darf höchstens 4000 UTF-16-Code-Units enthalten.");
  }

  const price = Number(byId("create-price").value);
  if (!Number.isSafeInteger(price) || price < 1 || price > 99999999) {
    throw new Error("Festpreis muss eine ganze Euro-Zahl zwischen 1 und 99999999 sein.");
  }
  return {
    category_path: categoryPath,
    title,
    description,
    price_eur: price,
  };
}

function mediaContentType(file) {
  if (["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    return file.type;
  }
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".webp")) return "image/webp";
  return null;
}

function mediaStageFilename(file) {
  const contentType = mediaContentType(file);
  if (contentType === "image/jpeg") return "upload.jpg";
  if (contentType === "image/png") return "upload.png";
  if (contentType === "image/webp") return "upload.webp";
  throw new Error(`Nicht unterstützter Bildtyp: ${file.name}`);
}

async function discardStagedMedia(refs) {
  if (refs.length === 0) return;
  const {response, payload} = await proxyRequest(
    "POST",
    "/api/write/media/discard",
    {
      body: JSON.stringify({media_refs: refs}),
      contentType: "application/json",
    },
  );
  if (!response.ok || payload.discarded !== refs.length) {
    throw new Error(payload.error ?? `Media-Cleanup HTTP ${response.status}`);
  }
}

async function stageSelectedMedia(files) {
  if (files.length > 32) {
    throw new Error("Höchstens 32 Bilder können pro Create gestaged werden.");
  }
  let totalBytes = 0;
  for (const file of files) {
    const contentType = mediaContentType(file);
    if (contentType === null) {
      throw new Error(`Nicht unterstützter Bildtyp: ${file.name}`);
    }
    if (!Number.isSafeInteger(file.size) || file.size <= 0 || file.size > 25 * 1024 * 1024) {
      throw new Error(`Ungültige Bildgröße: ${file.name}`);
    }
    totalBytes += file.size;
    if (totalBytes > 100 * 1024 * 1024) {
      throw new Error("Die ausgewählten Bilder überschreiten zusammen 100 MiB.");
    }
  }

  const refs = [];
  try {
    for (const file of files) {
      const {response, payload} = await proxyRequest(
        "POST",
        "/api/write/media/stage",
        {
          body: file,
          contentType: mediaContentType(file),
          filename: mediaStageFilename(file),
        },
      );
      if (!response.ok || typeof payload.media_ref !== "string") {
        throw new Error(payload.error ?? `Media-Staging HTTP ${response.status}`);
      }
      refs.push(payload.media_ref);
    }
    return refs;
  } catch (error) {
    if (refs.length > 0) {
      try {
        await discardStagedMedia(refs);
      } catch (cleanupError) {
        throw new Error(
          `${error.message}; lokale Media-Cleanup fehlgeschlagen: ${cleanupError.message}. Launcher neu starten, bevor erneut Bilder gestaged werden.`,
        );
      }
    }
    throw error;
  }
}

async function submitCreate(event) {
  event.preventDefault();
  if (!writeUiReady()) return;

  if (createInFlight) {
    setWriteStatus("Create läuft bereits. Es wird kein zweiter Auftrag gestartet.", "warning");
    return;
  }
  createInFlight = true;
  try {
    if (pendingWrites.has("create-media")) {
      await runPlatformWrite(
        "create-media",
        "",
        "",
        null,
        {beforeAcknowledge: retireCreateBeforeAcknowledge},
      );
      return;
    }
    if (pendingWrites.has("create")) {
      await runPlatformWrite(
        "create",
        "",
        "",
        null,
        {beforeAcknowledge: retireCreateBeforeAcknowledge},
      );
      return;
    }

    let payload;
    try {
      payload = createPayload();
    } catch (error) {
      setWriteStatus(error.message, "error");
      return;
    }

    const files = Array.from(byId("create-media").files ?? []);
    const createDraftSnapshot = captureCreateDraft(files);
    if (files.length === 0) {
      await runPlatformWrite(
        "create",
        "POST",
        "/api/write/ads",
        payload,
        {
          beforeAcknowledge: retireCreateBeforeAcknowledge,
          createDraftSnapshot,
        },
      );
      return;
    }

    setWriteStatus("Bilder werden ausschließlich lokal gestaged …", "warning");
    try {
      const mediaRefs = await stageSelectedMedia(files);
      const result = await runPlatformWrite(
        "create-media",
        "POST",
        "/api/write/media/ads",
        {...payload, media_refs: mediaRefs},
        {
          beforeAcknowledge: retireCreateBeforeAcknowledge,
          createDraftSnapshot,
        },
      );
      if (
        result !== null
        && result.payload?.error === "dashboard_pending_write_conflict"
      ) {
        try {
          await discardStagedMedia(mediaRefs);
        } catch (cleanupError) {
          setWriteStatus(
            "Konkurrierender Create wurde nicht weitergeleitet, aber lokale Media-Cleanup fehlgeschlagen: "
              + cleanupError.message + ".",
            "error",
          );
          return;
        }
        setWriteStatus(
          "Konkurrierender Create wurde nicht weitergeleitet; dessen lokal gestagte Bilder wurden verworfen. Die bereits gebundene Anfrage bleibt maßgeblich.",
          "warning",
        );
      }
    } catch (error) {
      setWriteStatus(
        "Media-Staging fehlgeschlagen: " + error.message
          + ". Es wurde kein neuer Plattform-Create gestartet.",
        "error",
      );
    }
  } finally {
    createInFlight = false;
  }
}

function selectedAdId() {
  const adId = byId("manage-ad-id").value;
  if (!adId || !latestAdsById.has(adId)) {
    setWriteStatus("Zuerst eine aktuelle Anzeige über „Verwalten“ auswählen.", "error");
    return null;
  }
  const ad = latestAdsById.get(adId);
  if (ad.present === false && pendingForAd(adId) === null) {
    setWriteStatus(
      "Diese Anzeige ist nicht mehr vorhanden. Ohne gebundene Recovery-Anfrage sind keine neuen Writes zulässig.",
      "warning",
    );
    return null;
  }
  return adId;
}

async function runAdAction(
  action,
  method,
  suffix,
  payload = null,
  {beforeAcknowledge = null} = {},
) {
  const adId = selectedAdId();
  if (adId === null) return;
  const existing = pendingForAd(adId);
  const scope = `ad:${adId}:${action}`;
  if (existing !== null && existing.scope !== scope) {
    setWriteStatus(
      `Für diese Anzeige ist bereits „${existing.scope}“ mit unbekanntem/ausstehendem Ergebnis gebunden. Zuerst den ursprünglichen Ausgang unabhängig klären.`,
      "warning",
    );
    return;
  }
  return await runPlatformWrite(
    scope,
    method,
    `/api/write/ads/${encodeURIComponent(adId)}${suffix}`,
    payload,
    {adId, beforeAcknowledge},
  );
}

async function saveManagedAd() {
  const adId = selectedAdId();
  if (adId === null) return;

  const existing = pendingForAd(adId);
  if (existing !== null) {
    await runAdAction(
      "update",
      "PATCH",
      "",
      {},
      {
        beforeAcknowledge: (result) => {
          settleManagedUpdateBeforeAcknowledge(adId, existing.payload, result);
        },
      },
    );
    return;
  }

  let payload;
  try {
    payload = managedContentChanges(adId);
  } catch (error) {
    setWriteStatus(error.message, "error");
    return;
  }
  if (Object.keys(payload).length === 0) {
    setWriteStatus("Keine Content-Änderungen zum Speichern.", "warning");
    return;
  }
  await runAdAction(
    "update",
    "PATCH",
    "",
    payload,
    {
      beforeAcknowledge: (result) => {
        settleManagedUpdateBeforeAcknowledge(adId, payload, result);
      },
    },
  );
}

async function deleteManagedAd() {
  const adId = selectedAdId();
  if (adId === null) return;
  if (!window.confirm(`Anzeige ${adId} wirklich löschen?`)) return;
  await runAdAction("delete", "DELETE", "");
}

async function loadAnalytics() {
  const generation = ++analyticsRequestGeneration;
  const metric = byId("metric-select").value;
  const dimension = byId("dimension-select").value;
  const status = byId("analytics-status");
  const groupsCsv = byId("groups-csv");
  const rankingCsv = byId("ranking-csv");
  groupsCsv.hidden = true;
  rankingCsv.hidden = true;

  // A new selection must never display the previous selection's values.
  renderGroups([]);
  renderRanking([]);
  byId("groups-empty").hidden = true;
  byId("ranking-empty").hidden = true;
  if (!metric || !dimension) {
    status.textContent = !metric ? "Metrik auswählen …" : "Dimension auswählen …";
    return;
  }

  status.textContent = "Lade Analytics …";
  try {
    const [groups, ranking] = await Promise.all([
      getJson(`/api/analytics/groups?dimension=${encodeURIComponent(dimension)}&metric=${encodeURIComponent(metric)}`),
      getJson(`/api/analytics/ads?metric=${encodeURIComponent(metric)}`),
    ]);
    if (generation !== analyticsRequestGeneration) return;
    renderGroups(groups);
    renderRanking(ranking);
    const params = `dimension=${encodeURIComponent(dimension)}&metric=${encodeURIComponent(metric)}`;
    groupsCsv.href = `/api/analytics/groups.csv?${params}`;
    rankingCsv.href = `/api/analytics/ads.csv?metric=${encodeURIComponent(metric)}`;
    groupsCsv.hidden = false;
    rankingCsv.hidden = false;
    status.textContent = "";
  } catch (error) {
    // An older rejection must not overwrite a newer selection's status.
    if (generation !== analyticsRequestGeneration) return;
    status.textContent = `Analytics konnten nicht geladen werden: ${error.message}`;
  }
}


function generationUiReady() {
  return (
    generationUiAvailable
    && typeof generationToken === "string"
    && generationToken.length >= 16
  );
}

function renderGenerationAvailability() {
  byId("generation-panel").hidden = !generationUiAvailable;
  byId("generation-session-note").hidden = generationUiReady();
  const canGenerate = generationUiReady() && !generationBusy;
  byId("generation-text").disabled = !canGenerate;
  byId("generation-image").disabled = !canGenerate;
  byId("generation-consent").disabled = generationBusy;
  // Never display a draft next to a prompt edited during a pending request.
  byId("generation-prompt").disabled = generationBusy;
  const canApply = writeUiReady() && !createInFlight && !generationBusy;
  const prompt = byId("generation-prompt").value.trim();
  byId("generation-apply-text").disabled = !canApply || generationTextDraft?.sourcePrompt !== prompt;
  byId("generation-apply-image").disabled = !canApply || !generationImageBlob || generationImageSourcePrompt !== prompt;
}

function clearSelectedGenerationDraft(mode) {
  // A new request (including failed validation or provider failure) makes
  // the prior result for that mode inapplicable to the current prompt.
  if (mode === "text") {
    generationTextDraft = null;
    byId("generation-text-prompt").textContent = "";
    byId("generation-draft-title").value = "";
    byId("generation-draft-description").value = "";
    byId("generation-text-preview").hidden = true;
  } else if (mode === "image") {
    generationImageBlob = null;
    generationImageSourcePrompt = null;
    byId("generation-image-prompt").textContent = "";
    if (generationImagePreviewUrl) {
      URL.revokeObjectURL(generationImagePreviewUrl);
      generationImagePreviewUrl = null;
    }
    const preview = byId("generation-draft-image");
    preview.removeAttribute("src");
    preview.hidden = true;
    byId("generation-image-preview").hidden = true;
  }
}

function onGenerationPromptChange() {
  // The shared prompt determines the provenance of both cached drafts.
  // Neither a title nor an image may survive changing its source text.
  clearSelectedGenerationDraft("text");
  clearSelectedGenerationDraft("image");
  renderGenerationAvailability();
}

async function requestGeneration(mode) {
  if (!generationUiReady() || generationBusy) return;
  if (mode !== "text" && mode !== "image") return;
  const prompt = byId("generation-prompt").value.trim();
  if (generationTextDraft && generationTextDraft.sourcePrompt !== prompt) clearSelectedGenerationDraft("text");
  if (generationImageBlob && generationImageSourcePrompt !== prompt) clearSelectedGenerationDraft("image");
  clearSelectedGenerationDraft(mode);
  renderGenerationAvailability();
  const status = byId("generation-status");
  if (!prompt || prompt.length > 4000) {
    status.textContent = "Artikelangaben/Bildidee (1–4000 Zeichen) eingeben.";
    return;
  }
  if (!byId("generation-consent").checked) {
    status.textContent = "Für jede kostenpflichtige Anfrage die Übertragung und mögliche Kosten gesondert bestätigen.";
    return;
  }
  // Consent is single-use, never reused after an unknown provider outcome.
  byId("generation-consent").checked = false;
  generationBusy = true;
  renderGenerationAvailability();
  status.textContent = "Erzeuge unveröffentlichten Entwurf …";
  try {
    const response = await fetch("/api/dashboard/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Mark-Generation": "1",
        "X-Mark-Generation-Token": generationToken,
      },
      body: JSON.stringify({
        mode,
        prompt,
        acknowledge_provider_cost: true,
      }),
      credentials: "omit",
      mode: "same-origin",
      redirect: "error",
      cache: "no-store",
    });
    if (!response.ok) {
      let errorCode = "generation_request_failed";
      try {
        const errorResult = await response.json();
        if (typeof errorResult.error === "string") errorCode = errorResult.error;
      } catch (_error) {
        // Never read or display a provider error/secret body.
      }
      throw new Error(errorCode + " (HTTP " + response.status + ")");
    }
    if (mode === "text") {
      const draft = await response.json();
      if (
        draft.status !== "draft" || draft.published !== false
        || typeof draft.title !== "string"
        || typeof draft.description !== "string"
        || draft.title.length > 65 || draft.description.length > 4000
      ) {
        throw new Error("Ungültige lokale Textentwurf-Antwort.");
      }
      if (byId("generation-prompt").value.trim() !== prompt) {
        throw new Error("Prompt während der Anfrage geändert; Ergebnis verworfen.");
      }
      generationTextDraft = {title: draft.title, description: draft.description, sourcePrompt: prompt};
      byId("generation-text-prompt").textContent = "Erstellt aus Prompt: " + prompt;
      byId("generation-draft-title").value = draft.title;
      byId("generation-draft-description").value = draft.description;
      byId("generation-text-preview").hidden = false;
      status.textContent = "Textentwurf bereit. Vor Übernahme Fakten überprüfen.";
    } else if (mode === "image") {
      if (response.headers.get("Content-Type")?.split(";")[0] !== "image/png") {
        throw new Error("Lokaler Bildentwurf ist kein PNG.");
      }
      const payload = await response.blob();
      if (!payload.size || payload.size > 18000000) {
        throw new Error("Lokaler Bildentwurf überschreitet die Größenbegrenzung.");
      }
      if (byId("generation-prompt").value.trim() !== prompt) {
        throw new Error("Prompt während der Anfrage geändert; Ergebnis verworfen.");
      }
      const newUrl = URL.createObjectURL(payload);
      const oldUrl = generationImagePreviewUrl;
      generationImageBlob = payload;
      generationImageSourcePrompt = prompt;
      byId("generation-image-prompt").textContent = "Erstellt aus Prompt: " + prompt;
      generationImagePreviewUrl = newUrl;
      byId("generation-draft-image").src = newUrl;
      byId("generation-draft-image").hidden = false;
      byId("generation-image-preview").hidden = false;
      if (oldUrl) URL.revokeObjectURL(oldUrl);
      status.textContent = "Bildentwurf bereit. Er ist kein geprüfter Produktfoto-Nachweis.";
    }
  } catch (error) {
    // An uncertain provider outcome is NEVER retried automatically.
    status.textContent = "Generierung fehlgeschlagen oder unklar: " + error.message
      + ". Kein automatischer Neuversuch.";
  } finally {
    generationBusy = false;
    renderGenerationAvailability();
  }
}

function applyGenerationTextToCreate() {
  if (!writeUiReady() || !generationTextDraft || createInFlight) return;
  if (generationTextDraft.sourcePrompt !== byId("generation-prompt").value.trim()) {
    byId("generation-status").textContent = "Textentwurf gehört zu anderem Prompt; bitte neu generieren.";
    return;
  }
  const title = byId("create-title");
  const description = byId("create-description");
  if ((title.value || description.value)
      && !window.confirm("Bestehenden Titel und Beschreibung im lokalen Formular durch den geprüften Entwurf ersetzen?")) {
    return;
  }
  title.value = generationTextDraft.title;
  description.value = generationTextDraft.description;
  if (typeof document !== "undefined" && typeof document.getElementById === "function") {
    const create = document.getElementById("create-disclosure");
    if (create) create.open = true;
  }
  byId("generation-status").textContent =
    "Entwurf lokal übernommen. Prüfen, Kategorie und Preis ergänzen und Veröffentlichung separat auslösen.";
}

function applyGenerationImageToCreate() {
  if (!writeUiReady() || !generationImageBlob || createInFlight) return;
  if (generationImageSourcePrompt !== byId("generation-prompt").value.trim()) {
    byId("generation-status").textContent = "Bildentwurf gehört zu anderem Prompt; bitte neu generieren.";
    return;
  }
  const files = byId("create-media");
  const previous = Array.from(files.files ?? []);
  if (previous.length >= 10 || generationImageBlob.size > 25000000) {
    byId("generation-status").textContent = "Maximal 10 Bilder und geltende Größenlimits beachten.";
    return;
  }
  try {
    if (typeof DataTransfer !== "function" || typeof File !== "function") {
      throw new Error("Dieser Browser unterstützt die lokale Bildübernahme nicht.");
    }
    const selected = new DataTransfer();
    for (const file of previous) selected.items.add(file);
    selected.items.add(
      new File([generationImageBlob], "mark-ki-entwurf.png", {type: "image/png"}),
    );
    files.files = selected.files;
    if (typeof document !== "undefined" && typeof document.getElementById === "function") {
    const create = document.getElementById("create-disclosure");
    if (create) create.open = true;
  }
    byId("generation-status").textContent =
      "Bild lokal in die Dateiauswahl übernommen, nicht hochgeladen/veröffentlicht.";
  } catch (error) {
    byId("generation-status").textContent =
      "Bildübernahme fehlgeschlagen: " + error.message;
  }
}

if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("pagehide", () => {
    if (generationImagePreviewUrl) URL.revokeObjectURL(generationImagePreviewUrl);
  });
}

async function syncNow() {
  if (!manualSyncAvailable || !writeUiReady() || syncInFlight) return;
  syncInFlight = true;
  const button = byId("sync-now");
  button.disabled = true;
  const status = byId("status");
  status.textContent = "Gleiche Anzeigen mit Kleinanzeigen ab …";
  try {
    const response = await fetch("/api/dashboard/sync", {
      method: "POST",
      headers: {
        "Origin": window.location.origin,
        "X-Mark-Dashboard-Write": "1",
        "X-Mark-Dashboard-Token": writeToken,
      },
      cache: "no-store",
    });
    const result = await response.json();
    if (!response.ok || result.status !== "success"
      || !Number.isSafeInteger(result.current_ads) || result.current_ads < 0) {
      throw new Error("Abgleich nicht bestätigt");
    }
    const reloaded = await load();
    if (!reloaded) {
      status.textContent = "Bestand abgeglichen, aber lokale Ansicht konnte nicht neu geladen werden. Bitte Ansicht neu laden.";
      return;
    }
    status.textContent = result.current_ads + " Anzeigen aus dem Kundenkonto abgeglichen.";
  } catch (_error) {
    status.textContent = "Abgleich fehlgeschlagen. Vorhandene Anzeigen bleiben gespeichert; bitte Anmeldung und Verbindungsstatus prüfen.";
  } finally {
    syncInFlight = false;
    button.disabled = !writeUiReady();
  }
}

function renderJobsAvailability() {
  byId("jobs-panel").hidden = !jobsAvailable;
  byId("nav-jobs").hidden = !jobsAvailable;
  byId("job-provider-control").hidden =
    !(jobsAvailable && generationUiAvailable);
}

async function load() {
  const status = byId("status");
  status.textContent = "Lade lokale Daten …";
  byId("groups-csv").hidden = true;
  byId("ranking-csv").hidden = true;
  try {
    const [summary, metricsPayload, dimensionsPayload, contract, dashboardConfig] = await Promise.all([
      getJson("/api/summary"),
      getJson("/api/analytics/metrics"),
      getJson("/api/analytics/dimensions"),
      getJson("/api/analytics/contract"),
      getJson("/api/dashboard/config"),
    ]);
    byId("tracked").textContent = summary.tracked_ads;
    byId("current").textContent = summary.current_ads;
    byId("absent").textContent = summary.absent_ads;
    byId("views").textContent =
      `${summary.views_total_known} / ${summary.views_observed_ads} Ads`;
    byId("watches").textContent =
      `${summary.watch_total_known} / ${summary.watch_observed_ads} Ads`;
    byId("replies").textContent =
      `${summary.replies_total_known} / ${summary.replies_observed_ads} Ads`;
    writeUiAvailable = dashboardConfig.write_ui_available === true;
    manualSyncAvailable = dashboardConfig.manual_sync_available === true;
    generationUiAvailable = dashboardConfig.generation_ui_available === true;
    jobsAvailable = false;
    renderJobsAvailability();
    byId("sync-now").hidden = !manualSyncAvailable;
    byId("sync-now").disabled = !writeUiReady() || syncInFlight;
    const genNavigation = optionalById("nav-generation");
    if (genNavigation) genNavigation.hidden = !generationUiAvailable;
    await refreshPendingWrites();
    // Probe the authenticated local-only job read route, without changing
    // the established /api/dashboard/config wire contract or guessing that
    // independently protected owner frontends expose the local SQLite job API.
    if (writeUiReady()) {
      try {
        const available = await _jobGet("/api/portfolio/jobs?limit=1");
        jobsAvailable = available && Array.isArray(available.jobs);
      } catch (_error) {
        jobsAvailable = false;
      }
    }
    renderJobsAvailability();
    renderWriteAvailability();
    byId("sync-now").disabled = !writeUiReady() || syncInFlight;
    renderGenerationAvailability();
    await loadCatalogPage();
    renderSyncStatus(summary.sync_status, summary.sync_recovery);
    renderAnalyticsContract(contract);

    const metricSelect = byId("metric-select");
    const dimensionSelect = byId("dimension-select");
    const previousMetric = metricSelect.value;
    const previousDimension = dimensionSelect.value;
    setOptions(metricSelect, metricsPayload.metrics, "Metrik auswählen …");
    setOptions(dimensionSelect, dimensionsPayload.dimensions);
    if (metricsPayload.metrics.includes(previousMetric)) {
      metricSelect.value = previousMetric;
    } else if (
      contract.objective_metric !== null
      && metricsPayload.metrics.includes(contract.objective_metric)
    ) {
      metricSelect.value = contract.objective_metric;
    }
    if (dimensionsPayload.dimensions.includes(previousDimension)) dimensionSelect.value = previousDimension;

    await loadAnalytics();
    status.textContent = "";
    return true;
  } catch (error) {
    status.textContent = `Fehler beim Laden: ${error.message}`;
    return false;
  }
}


function renderDraftNavigation() {
  byId("draft-prev").disabled = draftBusy || draftPageIndex === 0;
  byId("draft-next").disabled = draftBusy || draftNextAfter === null;
  byId("draft-find").disabled = draftBusy;
  byId("draft-page-label").textContent = "Seite " + (draftPageIndex + 1);
}

function useLocalDraft(item) {
  if (!writeUiReady()) {
    byId("draft-feedback").textContent = "Zum Übernehmen Kleinanzeiger normal starten.";
    return;
  }
  if (!window.confirm("Die bisherigen Eingaben im Formular 'Neue Anzeige' durch den lokalen Entwurf ersetzen? Es wird nichts veröffentlicht.")) {
    return;
  }
  byId("create-category").value = item.category;
  byId("create-title").value = item.title;
  byId("create-description").value = item.description;
  byId("create-price").value = String(item.price_eur);
  byId("create-media").value = "";
  revealCreateForm();
  byId("draft-feedback").textContent = "Artikel " + item.sku +
    " lokal ins Formular übernommen. Bilder bitte selbst auswählen; Veröffentlichung separat bestätigen.";
}

function renderDraftRows(items) {
  const body = byId("drafts-body");
  body.replaceChildren();
  for (const draft of items) {
    const row = document.createElement("tr");
    const choose = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.title = "Entwurf " + draft.sku + " für KI-Auftrag wählen";
    checkbox.checked = draftSelectedSkus.has(draft.sku);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        if (draftSelectedSkus.size >= 5000) {
          checkbox.checked = false;
          byId("draft-feedback").textContent = "Maximal 5.000 Entwürfe wählen.";
          return;
        }
        draftSelectedSkus.add(draft.sku);
      } else {
        draftSelectedSkus.delete(draft.sku);
      }
      renderJobSelection();
    });
    choose.append(checkbox);
    row.append(choose);
    for (const field of ["sku", "title", "price_eur", "category"]) {
      const cell = document.createElement("td");
      cell.textContent = String(draft[field] ?? "");
      row.append(cell);
    }
    const actions = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "In neue Anzeige";
    button.disabled = !writeUiReady();
    button.addEventListener("click", () => useLocalDraft(draft));
    actions.append(button);
    row.append(actions);
    body.append(row);
  }
  renderJobSelection();
}

async function loadDraftsPage() {
  if (draftBusy) return false;
  draftBusy = true;
  renderDraftNavigation();
  byId("draft-feedback").textContent = "Lade lokale Entwürfe …";
  const params = new URLSearchParams({
    limit: "50", after: draftCursors[draftPageIndex], q: draftSearch,
  });
  try {
    const payload = await getJson("/api/portfolio/drafts?" + params.toString());
    if (!payload || !Array.isArray(payload.drafts) || payload.drafts.length > 50
        || typeof payload.has_more !== "boolean"
        || (payload.has_more && (typeof payload.next_after !== "string" || !payload.next_after))) {
      throw new Error("ungültige lokale Entwurfsantwort");
    }
    draftNextAfter = payload.has_more ? payload.next_after : null;
    draftLoaded = true;
    renderDraftRows(payload.drafts);
    byId("draft-feedback").textContent = payload.total +
      " lokale Entwürfe · " + payload.drafts.length + " auf dieser Seite";
    return true;
  } catch (_error) {
    byId("draft-feedback").textContent = "Entwürfe konnten nicht geladen werden. Daten bleiben unverändert.";
    return false;
  } finally {
    draftBusy = false;
    renderDraftNavigation();
  }
}

async function changeDraftPage(index, cursors, search) {
  if (draftBusy) return false;
  const prev = {
    index: draftPageIndex, cursors: draftCursors.slice(),
    after: draftNextAfter, search: draftSearch,
  };
  draftPageIndex = index;
  draftCursors = cursors;
  draftSearch = search;
  const success = await loadDraftsPage();
  if (!success) {
    draftPageIndex = prev.index;
    draftCursors = prev.cursors;
    draftNextAfter = prev.after;
    draftSearch = prev.search;
    renderDraftNavigation();
  }
  return success;
}

function searchDrafts() {
  return changeDraftPage(0, [""], byId("draft-search").value.trim());
}
function nextDraftsPage() {
  if (draftBusy || draftNextAfter === null) return false;
  return changeDraftPage(draftPageIndex + 1,
    [...draftCursors.slice(0, draftPageIndex + 1), draftNextAfter], draftSearch);
}
function previousDraftsPage() {
  if (draftBusy || draftPageIndex === 0) return false;
  return changeDraftPage(draftPageIndex - 1, draftCursors.slice(), draftSearch);
}

const newAdNavigation = optionalById("nav-create");
if (newAdNavigation) newAdNavigation.addEventListener("click", revealCreateForm);
const analyticsNavigation = optionalById("nav-analytics");
if (analyticsNavigation) analyticsNavigation.addEventListener("click", () => {
  const disclosure = optionalById("analytics-disclosure");
  if (disclosure) disclosure.open = true;
});
const generationNavigation = optionalById("nav-generation");
if (generationNavigation) generationNavigation.addEventListener("click", () => {
  const disclosure = optionalById("generation-disclosure");
  if (disclosure) disclosure.open = true;
});
byId("reload").addEventListener("click", load);

function renderJobSelection() {
  byId("job-selected-count").textContent =
    draftSelectedSkus.size + " Entwürfe einzeln ausgewählt";
}
function _jobEuros(id, allowZero) {
  const raw = byId(id).value.trim();
  if (!/^[0-9]{1,7}(?:[.,][0-9]{1,2})?$/.test(raw)) {
    throw new Error("Budget in Euro mit höchstens zwei Nachkommastellen eingeben.");
  }
  const cents = Math.round(Number(raw.replace(",", ".")) * 100);
  if (!Number.isSafeInteger(cents) || cents > 100000000
      || (!allowZero && cents < 1)) {
    throw new Error("Ungültiger Kostenbetrag.");
  }
  return cents;
}
function _newJobRequestKey() {
  const bytes = new Uint8Array(32);
  window.crypto.getRandomValues(bytes);
  return Array.from(bytes, x => x.toString(16).padStart(2, "0")).join("");
}
function _jobHeaders() {
  return {"X-Mark-Dashboard-Write": "1",
          "X-Mark-Dashboard-Token": writeToken};
}
async function _jobGet(path) {
  const response = await fetch(path, {cache: "no-store",headers: _jobHeaders()});
  if (!response.ok) throw new Error("KI-Auftragsdaten konnten nicht gelesen werden.");
  return response.json();
}
async function _jobPost(name, payload) {
  if (!jobsAvailable || !writeUiReady()) throw new Error("Kleinanzeiger bitte regulär starten.");
  const response = await fetch("/api/portfolio/jobs/" + name, {
    method: "POST", cache: "no-store",
    headers: {..._jobHeaders(),"Content-Type":"application/json"},
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error("Aktion abgelehnt: Vorgang oder Originaldaten prüfen.");
  return response.json();
}
async function loadJobs() {
  if (!jobsAvailable || !writeUiReady()) return false;
  try {
    const data = await _jobGet("/api/portfolio/jobs?limit=50");
    if (!data || !Array.isArray(data.jobs)) throw new Error("Ungültige Antwort.");
    const selector = byId("job-choice");
    const prior = jobActiveId;
    selector.replaceChildren();
    for (const job of data.jobs) {
      const option = document.createElement("option");
      option.value = job.job_id;
      option.textContent = job.created_at.slice(0,16).replace("T"," ") +
        " · " + job.total + " Artikel · " + job.state;
      selector.append(option);
    }
    const choice = data.jobs.some(j => j.job_id === prior) ? prior :
      (data.jobs[0]?.job_id || "");
    jobActiveId = choice;
    selector.value = choice;
    jobsLoaded = true;
    byId("jobs-feedback").textContent = data.jobs.length +
      " letzte lokale KI-Aufträge · keine automatische KI-Ausführung";
    if (choice) await loadJobDetails(true);
    else {
      jobDetailGeneration++;
      jobDisplayedId = "";
      jobReviewSelected.clear();
      byId("job-items-body").replaceChildren();
      byId("job-progress").textContent = "Noch keine KI-Aufträge gespeichert.";
    }
    return true;
  } catch (_error) {
    byId("jobs-feedback").textContent =
      "Auftragsübersicht nicht verfügbar; gespeicherte Daten bleiben unverändert.";
    return false;
  }
}
async function loadJobDetails(reset = false) {
  if (!jobActiveId || !writeUiReady()) return false;
  const requestedId = jobActiveId;
  const generation = ++jobDetailGeneration;
  if (reset) {
    jobPageIndex = 0;
    jobCursors = [""];
    jobNextAfter = null;
    jobReviewSelected.clear();
  }
  jobBusy = true;
  jobDisplayedId = "";
  byId("job-items-body").replaceChildren();
  byId("job-progress").textContent = "Prüfe ausgewählten KI-Auftrag …";
  try {
    const params = new URLSearchParams({
      job_id: requestedId, limit: "50", after: jobCursors[jobPageIndex],
    });
    const job = await _jobGet("/api/portfolio/jobs/detail?" + params.toString());
    if (generation !== jobDetailGeneration || requestedId !== jobActiveId) return false;
    if (!job || job.job_id !== requestedId || !Array.isArray(job.items)) {
      throw new Error("Auftrag nicht gefunden");
    }
    jobNextAfter = job.has_more ? job.next_after : null;
    const c = job.counts || {};
    byId("job-progress").textContent = "Status: " + job.state +
      " · " + job.total + " Artikel · " + (c.queued || 0) + " offen · " +
      (c.proposed || 0) + " Vorschläge · " + (c.approved || 0) +
      " freigegeben · " + (c.unknown || 0) + " unklar · Budget " +
      (job.actual_cents/100).toFixed(2) + " USD geschätzt, " +
      (job.reserved_cents/100).toFixed(2) + " USD reserviert von " +
      (job.budget_cents/100).toFixed(2) + " USD";
    const body=byId("job-items-body");
    body.replaceChildren();
    for (const item of job.items) {
      const row=document.createElement("tr");
      const review=document.createElement("td");
      const check=document.createElement("input");
      check.type="checkbox";
      check.title="Vorschlag für " + item.sku + " wählen";
      check.disabled= !["proposed","approved","rejected"].includes(item.phase);
      check.checked=jobReviewSelected.has(item.sku);
      check.addEventListener("change",() => {
        if (check.checked) jobReviewSelected.add(item.sku);
        else jobReviewSelected.delete(item.sku);
      });
      review.append(check);row.append(review);
      for (const value of [item.sku,item.original?.title || "",
                            item.proposal?.title || "Noch kein Vorschlag",item.phase]) {
        const cell=document.createElement("td");
        cell.textContent=String(value);
        row.append(cell);
      }
      if (item.proposal) {
        const more=document.createElement("details");
        const title=document.createElement("summary");
        title.textContent="Beschreibungen vergleichen";
        const old=document.createElement("p");
        old.textContent="Bisher: " + (item.original?.description || "");
        const proposed=document.createElement("p");
        proposed.textContent="Vorschlag: " + item.proposal.description;
        more.append(title,old,proposed);
        row.lastChild.append(more);
      }
      body.append(row);
    }
    byId("job-page-label").textContent="Seite " + (jobPageIndex+1);
    byId("job-prev").disabled=jobPageIndex===0;
    byId("job-next").disabled=!jobNextAfter;
    jobDisplayedId = requestedId;
    return true;
  } catch (_error) {
    if (generation === jobDetailGeneration) {
      byId("jobs-feedback").textContent="Auftrag konnte nicht geladen werden.";
    }
    return false;
  } finally {
    if (generation === jobDetailGeneration) jobBusy=false;
  }
}
async function createJob() {
  if (jobBusy) return;
  try {
    const mode=byId("job-all-matching").checked ? "all" : "selected";
    const selected=mode==="selected" ? Array.from(draftSelectedSkus).sort() : [];
    if (mode==="selected" && (selected.length<1 || selected.length>5000)) {
      throw new Error("Mindestens einen Entwurf auswählen.");
    }
    const instruction=byId("job-instruction").value.trim();
    if (!instruction || instruction.length>1500) {
      throw new Error("Bitte den Verbesserungsauftrag eingeben.");
    }
    const body = {
      mode, skus:selected,search:mode==="all"?draftSearch:"",
      instruction,budget_cents:_jobEuros("job-budget-eur",true),
      estimate_cents:_jobEuros("job-estimate-eur",false)
    };
    const fingerprint=JSON.stringify(body);
    if (jobPendingRequest?.fingerprint !== fingerprint) {
      jobPendingRequest={fingerprint,key:_newJobRequestKey()};
    }
    const answer=await _jobPost("create",{
      ...body,request_key:jobPendingRequest.key
    });
    jobPendingRequest=null;
    jobActiveId=answer.job_id;
    byId("jobs-feedback").textContent=answer.total +
      " Entwürfe im lokalen Auftrag gespeichert. Noch keine KI-Anfragen gestellt.";
    await loadJobs();
  } catch (error) {
    byId("jobs-feedback").textContent=error.message;
  }
}
async function jobAction(name, payload) {
  if (jobBusy || !jobActiveId || jobDisplayedId !== jobActiveId) return;
  const requestedId = jobActiveId;
  try {
    const result = await _jobPost(name,{job_id:requestedId,...payload});
    if (jobActiveId !== requestedId) return;
    if (name === "apply-local" && result.applied > 0) {
      // Never reuse stale draft objects for "In neue Anzeige" after an
      // approved local AI edit. Hide old rows before refreshing, so a
      // failed read cannot leave actionable stale article details.
      draftLoaded = false;
      byId("drafts-body").replaceChildren();
      if (byId("drafts-disclosure").open) await loadDraftsPage();
    }
    byId("jobs-feedback").textContent="Lokaler Vorgang bestätigt. Keine Plattformänderung.";
    await loadJobs();
  } catch (error) {
    byId("jobs-feedback").textContent=error.message;
  }
}
function jobReview(approve) {
  if (jobBusy || !jobActiveId || jobDisplayedId !== jobActiveId) {
    byId("jobs-feedback").textContent="Den ausgewählten Auftrag zuerst erfolgreich laden und prüfen.";
    return;
  }
  if (!jobReviewSelected.size) {
    byId("jobs-feedback").textContent="Zuerst Vorschläge in der Tabelle auswählen.";
    return;
  }
  jobAction("review",{skus:Array.from(jobReviewSelected),approve});
}
async function applyJobLocally() {
  if (!jobActiveId) return;
  if (!window.confirm("Freigegebene Titel und Beschreibungen ausschließlich in deinen lokalen Entwürfen ändern? Bitte vorher im Startfenster eine Sicherung erstellen. Es wird nichts veröffentlicht.")) return;
  await jobAction("apply-local",{});
}
async function generateJobNext() {
  const consent=byId("job-provider-consent");
  const status=byId("job-provider-status");
  if (!writeUiReady() || !jobsAvailable || !generationUiAvailable ||
      !generationToken || !jobActiveId || jobBusy || jobDisplayedId !== jobActiveId) {
    status.textContent="Die KI-Sitzung muss im Kleinanzeiger-Startfenster eingerichtet sein.";
    return;
  }
  if (!consent.checked) {
    status.textContent="Zuerst die Modellkosten für genau diese einzelne Anfrage bestätigen.";
    return;
  }
  if (!window.confirm("Eine einzelne möglicherweise kostenpflichtige KI-Anfrage jetzt ausführen? Keine automatische Wiederholung, keine Veröffentlichung.")) {
    consent.checked=false;
    return;
  }
  const button=byId("job-generate-next");
  button.disabled=true;
  consent.checked=false;
  status.textContent="Erzeuge genau einen Vorschlag. Externe API-Kosten sind möglich …";
  try {
    const response=await fetch("/api/portfolio/jobs/generate-next",{
      method:"POST",cache:"no-store",
      headers:{
        ..._jobHeaders(),
        "X-Mark-Generation":"1",
        "X-Mark-Generation-Token":generationToken,
        "Content-Type":"application/json",
      },
      body:JSON.stringify({
        job_id:jobActiveId,
        acknowledge_provider_cost:true,
      }),
    });
    const data=await response.json();
    if (!response.ok || data.claimed!==true) {
      throw new Error("Kein eindeutiges Ergebnis. Auftrag prüfen und keinesfalls blind erneut starten.");
    }
    status.textContent="Ein KI-Vorschlag gespeichert; vor Übernahme prüfen und freigeben.";
    await loadJobs();
  } catch (_error) {
    status.textContent="KI-Ergebnis oder Kosten unklar. Auftrag prüfen, unklare Anfrage klären und nicht blind wiederholen.";
    await loadJobs();
  } finally {
    button.disabled=false;
  }
}
async function generateJobBatch() {
  const status=byId("job-provider-status");
  const consent=byId("job-provider-consent");
  if (jobBatchBusy) return;
  if (!writeUiReady() || !jobsAvailable || !generationUiAvailable ||
      !generationToken || !jobActiveId || jobBusy || jobDisplayedId !== jobActiveId) {
    status.textContent="Bitte Kleinanzeiger mit aktivierter KI-Sitzung starten.";
    return;
  }
  if (!consent.checked) {
    status.textContent="Zuerst Modell und mögliche Kosten des gesamten Stapels bestätigen.";
    return;
  }
  const countValue=byId("job-batch-count").value.trim();
  if (!/^(?:[1-9]|10)$/.test(countValue)) {
    status.textContent="Bitte 1 bis 10 Anfragen wählen.";
    return;
  }
  const count=Number(countValue);
  const job=jobActiveId;
  const reserve=_jobEuros("job-estimate-eur",false)*count;
  if (!window.confirm("Bis zu " + count + " einzelne kostenpflichtige KI-Anfragen " +
      "für diesen Auftrag ausführen? Voraussichtliche Budgetreservierung bis " +
      (reserve/100).toFixed(2) + " USD. Echte Anbieterabrechnung kann abweichen. " +
      "Keine Plattformänderung und keine Wiederholung unklarer Aufrufe.")) {
    consent.checked=false;
    return;
  }
  consent.checked=false;
  jobBatchBusy=true;
  jobBatchStop=false;
  byId("job-generate-batch").disabled=true;
  byId("job-generate-next").disabled=true;
  byId("job-batch-stop").disabled=false;
  let done=0;
  try {
    for (let index=0;index<count&&!jobBatchStop;index++) {
      if (!writeUiReady() || !generationUiAvailable || !generationToken) break;
      status.textContent=(index+1)+" von "+count+" KI-Vorschlägen wird erzeugt …";
      let response;
      try {
        response=await fetch("/api/portfolio/jobs/generate-next",{
          method:"POST",cache:"no-store",
          headers:{
            ..._jobHeaders(),
            "X-Mark-Generation":"1",
            "X-Mark-Generation-Token":generationToken,
            "Content-Type":"application/json",
          },
          body:JSON.stringify({job_id:job,acknowledge_provider_cost:true}),
        });
      } catch (_error) {
        status.textContent="Verbindung unterbrochen. Ergebnis kann unbekannt sein; kein automatischer Folgeaufruf.";
        break;
      }
      if (!response.ok) {
        status.textContent=(response.status===429?
          "KI-Sitzungslimit erreicht; kein weiterer Aufruf." :
          "KI-Auftrag angehalten. Ergebnis/Kosten prüfen; keine automatische Wiederholung.");
        break;
      }
      try {
        const result=await response.json();
        if (result.claimed!==true) throw new Error("unconfirmed result");
        done++;
      } catch (_error) {
        status.textContent="Antwort nicht bestätigt. Vorgang prüfen; keine Wiederholung.";
        break;
      }
      if (jobBatchStop) break;
    }
    // Job state is durable even if the browser loses a response; this read
    // is never authority to retry a potentially billed provider request.
    await loadJobs();
    if (done===count) status.textContent=done+" Vorschläge lokal gespeichert. Jetzt prüfen und freigeben.";
    else if (jobBatchStop) status.textContent=done+" Vorschläge gespeichert; Stapel nach laufender Anfrage gestoppt.";
  } finally {
    jobBatchBusy=false;
    byId("job-generate-batch").disabled=false;
    byId("job-generate-next").disabled=false;
    byId("job-batch-stop").disabled=true;
  }
}
function nextJobPage() {
  if (!jobNextAfter || jobBusy) return;
  jobCursors=[...jobCursors.slice(0,jobPageIndex+1),jobNextAfter];
  jobPageIndex++;
  jobReviewSelected.clear();
  loadJobDetails();
}
function prevJobPage() {
  if (jobPageIndex===0 || jobBusy) return;
  jobPageIndex--;
  jobCursors=jobCursors.slice(0,jobPageIndex+1);
  jobReviewSelected.clear();
  loadJobDetails();
}
byId("job-clear-selection").addEventListener("click", () => {
  draftSelectedSkus.clear();
  for (const checkbox of byId("drafts-body").querySelectorAll("input[type=checkbox]")) {
    checkbox.checked = false;
  }
  renderJobSelection();
});
byId("job-create").addEventListener("click",createJob);
byId("job-generate-next").addEventListener("click",generateJobNext);
byId("job-generate-batch").addEventListener("click",generateJobBatch);
byId("job-batch-stop").addEventListener("click",()=>{ jobBatchStop=true; });
byId("job-refresh").addEventListener("click",loadJobs);
byId("job-choice").addEventListener("change",() => {
  jobActiveId=byId("job-choice").value;
  loadJobDetails(true);
});
for (const action of ["start","pause","resume","cancel"]) {
  byId("job-"+action).addEventListener("click",() => {
    if (action==="cancel" && !window.confirm("Diesen lokalen KI-Auftrag abbrechen?")) return;
    jobAction("state",{action});
  });
}
byId("job-recover").addEventListener("click",() => jobAction("recover",{}));
byId("job-approve").addEventListener("click",() => jobReview(true));
byId("job-reject").addEventListener("click",() => jobReview(false));
byId("job-apply-local").addEventListener("click",applyJobLocally);
byId("job-next").addEventListener("click",nextJobPage);
byId("job-prev").addEventListener("click",prevJobPage);
const jobsNavigation=optionalById("nav-jobs");
if (jobsNavigation) jobsNavigation.addEventListener("click",() => {
  byId("jobs-disclosure").open=true;
  if (!jobsLoaded) loadJobs();
});
byId("jobs-disclosure").addEventListener("toggle",() => {
  if (byId("jobs-disclosure").open && !jobsLoaded) loadJobs();
});
const draftNavigation = optionalById("nav-drafts");
if (draftNavigation) draftNavigation.addEventListener("click", () => {
  byId("drafts-disclosure").open = true;
  if (!draftLoaded) loadDraftsPage();
});
byId("drafts-disclosure").addEventListener("toggle", () => {
  if (byId("drafts-disclosure").open && !draftLoaded) loadDraftsPage();
});
byId("draft-find").addEventListener("click", searchDrafts);
byId("draft-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); searchDrafts(); }
});
byId("draft-prev").addEventListener("click", previousDraftsPage);
byId("draft-next").addEventListener("click", nextDraftsPage);
byId("catalog-find").addEventListener("click", searchCatalog);
byId("catalog-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    searchCatalog();
  }
});
byId("catalog-status").addEventListener("change", searchCatalog);
byId("catalog-prev").addEventListener("click", previousCatalogPage);
byId("catalog-next").addEventListener("click", nextCatalogPage);
byId("catalog-clear").addEventListener("click", clearCatalogSelection);
byId("catalog-export").addEventListener("click", exportCatalogSelection);
byId("sync-now").addEventListener("click", syncNow);
byId("metric-select").addEventListener("change", loadAnalytics);
byId("dimension-select").addEventListener("change", loadAnalytics);
byId("create-form").addEventListener("submit", submitCreate);
byId("manage-ad-select").addEventListener("change", () => {
  const adId = byId("manage-ad-select").value;
  const ad = latestAdsById.get(adId);
  if (writeUiReady() && ad && (ad.present || pendingForAd(adId) !== null)) {
    populateManageForm(ad);
  } else {
    clearManagedSelection();
  }
});
byId("manage-save").addEventListener("click", saveManagedAd);
byId("manage-pause").addEventListener(
  "click",
  () => runAdAction("pause", "POST", "/pause"),
);
byId("manage-activate").addEventListener(
  "click",
  () => runAdAction("activate", "POST", "/activate"),
);
byId("manage-delete").addEventListener("click", deleteManagedAd);
byId("generation-prompt").addEventListener("input", onGenerationPromptChange);
byId("generation-text").addEventListener("click", () => requestGeneration("text"));
byId("generation-image").addEventListener("click", () => requestGeneration("image"));
byId("generation-apply-text").addEventListener("click", applyGenerationTextToCreate);
byId("generation-apply-image").addEventListener("click", applyGenerationImageToCreate);
consumeWriteToken();
load();
"""


def _valid_ad_id(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 32
        and value.isascii()
        and value.isdigit()
    )


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _single_query_value(query_string: str, name: str) -> str | None:
    values = parse_qs(query_string, keep_blank_values=True).get(name)
    if values is None or len(values) != 1 or not values[0]:
        return None
    return values[0]


def _dashboard_write_route(path: str, method: str) -> str | None:
    target = urlsplit(path)
    if target.path != path:
        return None
    # Reject noncanonical write paths before binding any durable intent.
    # Otherwise a percent-encoded media route can lose its staging references.
    raw_parts = target.path.split("/")
    if (
        raw_parts[0] != ""
        or any(not part or unquote(part) != part for part in raw_parts[1:])
    ):
        return None
    parts = raw_parts[1:]
    if parts == ["api", "write", "ads"] and method == "POST":
        return "json"
    if parts == ["api", "write", "media", "stage"] and method == "POST":
        return "media"
    if parts == ["api", "write", "media", "discard"] and method == "POST":
        return "local_json"
    if parts == ["api", "write", "media", "ads"] and method == "POST":
        return "json"
    if len(parts) == 4 and parts[:3] == ["api", "write", "ads"]:
        if not _valid_ad_id(parts[3]):
            return None
        if method == "PATCH":
            return "json"
        if method == "DELETE":
            return "empty"
    if (
        len(parts) == 5
        and parts[:3] == ["api", "write", "ads"]
        and _valid_ad_id(parts[3])
        and parts[4] in {"pause", "activate"}
        and method == "POST"
    ):
        return "empty"
    return None


def _dashboard_pending_identity(
    path: str,
    method: str,
) -> tuple[str, str, str | None] | None:
    target = urlsplit(path)
    if target.path != path:
        return None
    parts = [unquote(item) for item in target.path.split("/") if item]
    if parts == ["api", "write", "ads"] and method == "POST":
        return ("create", "create", None)
    if parts == ["api", "write", "media", "ads"] and method == "POST":
        return ("create-media", "create", None)
    if len(parts) == 4 and parts[:3] == ["api", "write", "ads"]:
        ad_id = parts[3]
        if not _valid_ad_id(ad_id):
            return None
        if method == "PATCH":
            return (f"ad:{ad_id}:update", f"ad:{ad_id}", ad_id)
        if method == "DELETE":
            return (f"ad:{ad_id}:delete", f"ad:{ad_id}", ad_id)
    if (
        len(parts) == 5
        and parts[:3] == ["api", "write", "ads"]
        and _valid_ad_id(parts[3])
        and parts[4] in {"pause", "activate"}
        and method == "POST"
    ):
        ad_id = parts[3]
        return (
            f"ad:{ad_id}:{parts[4]}",
            f"ad:{ad_id}",
            ad_id,
        )
    return None


def _local_reconciliation_allowed(
    store: SnapshotStore | StorageOwnerClient, proxy: DashboardWriteProxy,
) -> bool:
    return (
        type(store) is SnapshotStore
        and proxy.local_reconciliation_enabled
        and proxy.backend_principal is not None
        and not container_draft_denied()
    )


def _local_pending_matches(
    store: SnapshotStore, *, scope: str, key: str, resource: str | None = None,
) -> tuple[DashboardPendingWrite, ...]:
    # Deliberately local SQLite reads, never an owner RPC. Limit collision
    # reads to two rows; more than one match is always a conflict.
    with closing(store._connect()) as connection:
        store._validate_store_schema(connection)
        rows = connection.execute(
            "SELECT scope, resource_key, idempotency_key, method, path, "
            "payload_json, ad_id, acknowledged FROM dashboard_pending_writes "
            "WHERE scope = ? OR idempotency_key = ? OR resource_key = ? LIMIT 2",
            (scope, key, resource),
        ).fetchall()
    return tuple(store._dashboard_pending_write_record(row) for row in rows)


def _local_completed_write_response(
    store: SnapshotStore, proxy: DashboardWriteProxy, pending: DashboardPendingWrite,
) -> bytes:
    """Read only the original confirmed result of an exactly bound local intent.

    Browser JSON is never a completion proof. Both the trusted backend's
    fingerprint and its persisted operation receipt must support the HTTP row.
    This function cannot complete requests, clear recovery or contact a backend.
    """
    if not _local_reconciliation_allowed(store, proxy):
        raise ValueError("local reconciliation is not configured")
    return _stored_completed_write_response(store, proxy.backend_principal, pending)


def _stored_completed_write_response(
    store: SnapshotStore, principal: str, pending: DashboardPendingWrite,
) -> bytes:
    """Exact persisted completion check shared by local and protected owners.

    This helper does not establish an independent witness proof. The protected
    owner adds that proof itself before any remote ACK or replay is permitted.
    """
    if type(store) is not SnapshotStore or type(principal) is not str or not principal:
        raise ValueError("trusted local completion context required")
    kind = _dashboard_write_route(pending.path, pending.method)
    if (
        kind not in {"json", "empty"}
        or urlsplit(pending.path).path != pending.path
        or _dashboard_pending_identity(pending.path, pending.method)
        != (pending.scope, pending.resource_key, pending.ad_id)
        or _DASHBOARD_IDEMPOTENCY_KEY_RE.fullmatch(pending.idempotency_key) is None
    ):
        raise ValueError("pending identity mismatch")
    payload: dict[str, object] = {}
    if kind == "json":
        if pending.payload_json is None or len(pending.payload_json) > _MAX_PROXY_JSON_BODY_BYTES:
            raise ValueError("missing or oversized pending payload")
        pending_bytes = pending.payload_json.encode("utf-8")
        if len(pending_bytes) > _MAX_PROXY_JSON_BODY_BYTES:
            raise ValueError("oversized pending payload")
        payload = strict_json_object(pending_bytes)
        if _json_bytes(payload).decode("utf-8") != pending.payload_json:
            raise ValueError("noncanonical pending payload")
        if pending.ad_id is None:
            _, _, payload = _normalize_create_payload(
                payload, with_media=pending.scope == "create-media",
            )
        elif (
            not payload or set(payload) - {"title", "description"}
            or any(not isinstance(value, str) for value in payload.values())
        ):
            raise ValueError("invalid content payload")
    elif pending.payload_json is not None:
        raise ValueError("unexpected pending payload")

    record = store.write_api_request(pending.idempotency_key)
    if (
        record is None or record.idempotency_key != pending.idempotency_key
        or record.request_sha256 != _request_fingerprint(
            principal=principal, method=pending.method,
            path=pending.path, payload=payload,
        )
        or record.state != "completed" or record.execution_started_at is None
        or record.completed_at is None or record.response_status != 200
        or not record.claim_owner
        or any(value.utcoffset() is None for value in (
            record.requested_at, record.execution_started_at, record.completed_at,
        ))
        or not record.requested_at <= record.execution_started_at <= record.completed_at
        or record.response_json is None
        or len(record.response_json) > _MAX_PROXY_RESPONSE_BYTES
    ):
        raise ValueError("no exact completed result")
    response_bytes = record.response_json.encode("utf-8")
    if len(response_bytes) > _MAX_PROXY_RESPONSE_BYTES:
        raise ValueError("oversized completed response")
    response = strict_json_object(response_bytes)
    media = pending.scope == "create-media"
    response_fields = {"idempotency_key", "operation_receipt", "platform_retry_authorized"}
    if media:
        response_fields |= {"media_persistence_confirmed", "media_post_read_status"}
    receipt = response.get("operation_receipt")
    operation = (
        "create" if pending.ad_id is None else
        "update_content" if pending.method == "PATCH" else
        "delete" if pending.method == "DELETE" else
        {"pause": "set_state:paused", "activate": "set_state:active"}[
            pending.path.rsplit("/", 1)[1]
        ]
    )
    if (
        _json_bytes(response) != response_bytes or set(response) != response_fields
        or response["idempotency_key"] != pending.idempotency_key
        or response["platform_retry_authorized"] is not False
        or not isinstance(receipt, dict)
        or receipt.get("operation") != operation
        or receipt.get("outcome") != "confirmed"
        or receipt.get("writer_invoked") is not True
        or receipt.get("authorization_by") != principal
        or receipt.get("authorization_reference") != "write-api:" + pending.idempotency_key
    ):
        raise ValueError("invalid completed receipt")
    if pending.ad_id is None:
        if not isinstance(receipt.get("created_ad_id"), str) or not _valid_ad_id(receipt["created_ad_id"]):
            raise ValueError("invalid created target")
        if any(receipt.get(name) != "success_nonempty" for name in (
            "post_read_status", "confirmation_post_read_status", "content_post_read_status",
        )):
            raise ValueError("incomplete create confirmation")
    elif receipt.get("ad_id") != pending.ad_id:
        raise ValueError("receipt target mismatch")
    if media and (
        receipt.get("media_persistence_confirmed") is not True
        or response["media_persistence_confirmed"] is not True
        or response["media_post_read_status"] != receipt.get("media_post_read_status")
    ):
        raise ValueError("unconfirmed media")
    for name in ("started_at", "completed_at"):
        raw = receipt.get(name)
        if not isinstance(raw, str) or len(raw) > 80:
            raise ValueError("invalid receipt time")
        timestamp = datetime.fromisoformat(raw)
        if timestamp.utcoffset() is None or timestamp.isoformat() != raw:
            raise ValueError("noncanonical receipt time")
    if datetime.fromisoformat(receipt["started_at"]) > datetime.fromisoformat(receipt["completed_at"]):
        raise ValueError("invalid receipt chronology")

    # Compare every wire field against the sole persisted operation receipt,
    # across both tables. A response-only edit or authorization-key collision
    # must never become an ACK authority. Table names are fixed local constants.
    with closing(store._connect()) as connection:
        found = []
        common = (
            "operation", "started_at", "completed_at", "outcome", "pre_read_status",
            "post_read_status", "writer_invoked", "authorization_by",
            "authorization_reference", "writer_error",
        )
        for table in ("operation_receipts", "create_operation_receipts"):
            columns = common + (
                ("ad_id",) if table == "operation_receipts" else (
                    "created_ad_id", "confirmation_pre_read_status",
                    "confirmation_post_read_status", "content_post_read_status",
                    "media_post_read_status", "media_persistence_confirmed",
                )
            )
            rows = connection.execute(
                f"SELECT {','.join(columns)} FROM {table} WHERE authorization_reference = ? LIMIT 2",
                ("write-api:" + pending.idempotency_key,),
            ).fetchall()
            found.extend((table, dict(row)) for row in rows)
    expected_table = "create_operation_receipts" if pending.ad_id is None else "operation_receipts"
    if len(found) != 1 or found[0][0] != expected_table:
        raise ValueError("missing or colliding operation receipt")
    row = found[0][1]
    wire = dict(row)
    wire["writer_invoked"] = wire["writer_invoked"] == 1
    if pending.ad_id is None:
        if row["media_post_read_status"] is None:
            del wire["media_post_read_status"]
            del wire["media_persistence_confirmed"]
        else:
            wire["media_persistence_confirmed"] = row["media_persistence_confirmed"] == 1
    if _json_bytes(wire) != _json_bytes(receipt):
        raise ValueError("stored response differs from operation receipt")
    return response_bytes



def _owner_reconciliation_allowed(
    store: SnapshotStore | StorageOwnerClient, proxy: DashboardWriteProxy,
) -> bool:
    from .completion_dashboard import CompletionDashboardClient
    return (
        type(store) is CompletionDashboardClient
        and proxy.owner_reconciliation_enabled
        and not proxy.local_reconciliation_enabled
        and proxy.backend_principal == store.principal
        and not container_draft_denied()
    )


def _pending_reconciliation_allowed(
    store: SnapshotStore | StorageOwnerClient, proxy: DashboardWriteProxy,
) -> bool:
    return _local_reconciliation_allowed(store, proxy) or _owner_reconciliation_allowed(store, proxy)


def _pending_matches(
    store: SnapshotStore | StorageOwnerClient, *, scope: str, key: str,
    resource: str | None = None,
) -> tuple[DashboardPendingWrite, ...]:
    if type(store) is SnapshotStore:
        return _local_pending_matches(store, scope=scope, key=key, resource=resource)
    from .completion_dashboard import CompletionDashboardClient
    if type(store) is not CompletionDashboardClient:
        raise ValueError("independent reconciliation is not configured")
    # This is a bounded typed read of pending intents, not completion evidence.
    # The owner rechecks the full identity plus its witness before either ACK.
    return tuple(row for row in store.dashboard_pending_writes()
                 if row.scope == scope or row.idempotency_key == key
                 or row.resource_key == resource)[:2]


def _completed_write_response(
    store: SnapshotStore | StorageOwnerClient, proxy: DashboardWriteProxy,
    pending: DashboardPendingWrite,
) -> bytes:
    if _owner_reconciliation_allowed(store, proxy):
        return store.confirmed_dashboard_write_response(pending)
    return _local_completed_write_response(store, proxy, pending)


def _handler_factory(
    store: SnapshotStore | StorageOwnerClient,
    query: MarkQueryService,
    analytics: AnalyticsService,
    write_proxy: DashboardWriteProxy | None,
    generation_access: DashboardGenerationAccess | None,
    *,
    public_origin_port: int | None = None,
    sync_once: Callable[[], int] | None = None,
):
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "mark-api-readonly/0.1"
        sys_version = ""

        def parse_request(self) -> bool:
            # Reset per-request state before parsing even malformed HTTP/0.9.
            self.headers = None
            if not super().parse_request():
                return False
            # Both native loopback and independently-relayed public origin
            # must be exact numeric 127.0.0.1 hosts. No DNS rebound or
            # duplicate Host headers can authorize read or Write proxy routes.
            listener_host, listener_port = self.server.server_address
            allowed_ports = {int(listener_port)}
            if public_origin_port is not None:
                allowed_ports.add(public_origin_port)
            # Both explicit :80 and canonical portless Host spellings
            # designate the same numeric 127.0.0.1 listener, not DNS aliases.
            allowed_hosts = {
                spelling
                for port in allowed_ports
                for spelling in (
                    _dashboard_http_origin("127.0.0.1", port).removeprefix("http://"),
                    f"127.0.0.1:{port}",
                )
            }
            parsed_headers = getattr(self, "headers", None)
            get_all = getattr(parsed_headers, "get_all", None)
            hosts = (get_all("Host") if callable(get_all) else None) or []
            if (
                str(listener_host) != "127.0.0.1"
                or len(hosts) != 1
                or hosts[0] not in allowed_hosts
            ):
                self.close_connection = True
                self.send_error(421, "Misdirected Request")
                return False
            return True

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str,
            *,
            allow: str | None = None,
            idempotency_replayed: bool = False,
            download_filename: str | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; "
                "script-src 'self'; style-src 'self'; img-src 'self' blob:; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            if allow is not None:
                self.send_header("Allow", allow)
            if idempotency_replayed:
                self.send_header("Idempotency-Replayed", "true")
            if download_filename is not None:
                # Names are built from enumerated, validated metric/dimension keys.
                self.send_header("Content-Disposition", f'attachment; filename="{download_filename}"')
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, value: object) -> None:
            try:
                encoded = _json_bytes(value)
            except (TypeError, ValueError, OverflowError, RecursionError):
                # Never emit JavaScript-invalid NaN/Infinity from metrics
                # or other service data; invalid data cannot be success.
                status = 500
                encoded = b'{"error":"invalid_json_response"}'
            self._send(
                status,
                encoded,
                "application/json; charset=utf-8",
            )

        def _method_not_allowed(self) -> None:
            body = _json_bytes({"error": "method_not_allowed"})
            self._send(
                405,
                body,
                "application/json; charset=utf-8",
                allow="GET",
            )

        def _invalid_metric(self) -> None:
            self._send_json(
                400,
                {
                    "error": "invalid_metric",
                    "allowed_metrics": list(ANALYTICS_METRICS),
                },
            )

        def _invalid_dimension(self) -> None:
            self._send_json(
                400,
                {
                    "error": "invalid_dimension",
                    "allowed_dimensions": list(ANALYTICS_DIMENSIONS),
                },
            )

        def _write_token_authorized(self, *, require_origin: bool) -> bool:
            if write_proxy is None:
                return False
            marker = self.headers.get_all(_DASHBOARD_WRITE_MARKER_HEADER) or []
            tokens = self.headers.get_all(_DASHBOARD_WRITE_TOKEN_HEADER) or []
            if (
                len(marker) != 1
                or marker[0] != _DASHBOARD_WRITE_MARKER_VALUE
                or len(tokens) != 1
                or not hmac.compare_digest(tokens[0], write_proxy.ui_token)
            ):
                return False
            if self.headers.get("Authorization") is not None:
                return False
            if require_origin:
                origins = self.headers.get_all("Origin") or []
                server_host, server_port = self.server.server_address
                expected_origin = _dashboard_http_origin(
                    str(server_host),
                    int(server_port) if public_origin_port is None else public_origin_port,
                )
                if (
                    len(origins) != 1
                    or not hmac.compare_digest(origins[0], expected_origin)                ):
                    return False
            fetch_sites = self.headers.get_all("Sec-Fetch-Site") or []
            if fetch_sites and (
                len(fetch_sites) != 1 or fetch_sites[0] != "same-origin"
            ):
                return False
            return True

        def _write_session_authorized(self) -> bool:
            return self._write_token_authorized(require_origin=True)

        def _content_length(self, *, max_bytes: int) -> int:
            if self.headers.get("Transfer-Encoding") is not None:
                raise ValueError("transfer_encoding_not_allowed")
            values = self.headers.get_all("Content-Length") or []
            if not values:
                return 0
            if len(values) != 1:
                raise ValueError("invalid_content_length")
            try:
                value = int(values[0], 10)
            except ValueError as exc:
                raise ValueError("invalid_content_length") from exc
            if value < 0 or value > max_bytes:
                raise ValueError("invalid_content_length")
            return value

        def _read_proxy_body(self, body_kind: str) -> tuple[bytes, dict[str, str]]:
            headers: dict[str, str] = {}
            if body_kind == "empty":
                length = self._content_length(max_bytes=0)
                if length != 0:
                    raise ValueError("request_body_not_allowed")
                return b"", headers

            if body_kind in {"json", "local_json"}:
                content_type = self.headers.get("Content-Type", "")
                if content_type.split(";", 1)[0].strip().lower() != "application/json":
                    raise ValueError("content_type_must_be_json")
                length = self._content_length(max_bytes=_MAX_PROXY_JSON_BODY_BYTES)
                if length == 0:
                    raise ValueError("json_body_required")
                headers["Content-Type"] = "application/json"
            elif body_kind == "media":
                content_type = (
                    self.headers.get("Content-Type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                    raise ValueError("unsupported_media_type")
                filenames = self.headers.get_all("X-Mark-Media-Filename") or []
                if len(filenames) != 1:
                    raise ValueError("invalid_media_filename")
                length = self._content_length(max_bytes=_MAX_PROXY_MEDIA_BODY_BYTES)
                if length == 0:
                    raise ValueError("media_body_required")
                headers["Content-Type"] = content_type
                headers["X-Mark-Media-Filename"] = filenames[0]
            else:
                raise ValueError("unsupported_proxy_body")

            previous_timeout = self.connection.gettimeout()
            self.connection.settimeout(_PROXY_BODY_READ_TIMEOUT_SECONDS)
            try:
                try:
                    body = self.rfile.read(length)
                except TimeoutError as exc:
                    raise ValueError("request_body_timeout") from exc
            finally:
                try:
                    self.connection.settimeout(previous_timeout)
                except OSError:
                    pass
            if len(body) != length:
                raise ValueError("incomplete_request_body")
            return body, headers

        def _proxy_write(self, method: str) -> None:
            if write_proxy is None:
                self._method_not_allowed()
                return
            body_kind = _dashboard_write_route(self.path, method)
            if body_kind is None:
                self._send_json(404, {"error": "not_found"})
                return
            if not self._write_session_authorized():
                self._send_json(
                    403,
                    {
                        "error": "write_session_required",
                        "platform_retry_authorized": False,
                    },
                )
                return

            if urlsplit(self.path).path == "/api/write/media/stage":
                try:
                    create_recovery_pending = any(
                        record.resource_key == "create"
                        for record in store.dashboard_pending_writes()
                    )
                except Exception:
                    self._send_json(
                        500,
                        {
                            "error": "dashboard_pending_store_error",
                            "platform_retry_authorized": False,
                        },
                    )
                    return
                if create_recovery_pending:
                    self._send_json(
                        409,
                        {
                            "error": "dashboard_pending_write_conflict",
                            "platform_retry_authorized": False,
                        },
                    )
                    return

            headers: dict[str, str] = {
                "Authorization": f"Bearer {write_proxy.bearer_token}",
            }
            platform_write = body_kind in {"json", "empty"}
            idempotency_key: str | None = None
            if platform_write:
                values = self.headers.get_all("Idempotency-Key") or []
                if (
                    len(values) != 1
                    or _DASHBOARD_IDEMPOTENCY_KEY_RE.fullmatch(values[0]) is None
                ):
                    self._send_json(
                        400,
                        {
                            "error": "invalid_or_missing_idempotency_key",
                            "platform_retry_authorized": False,
                        },
                    )
                    return
                idempotency_key = values[0]
                headers["Idempotency-Key"] = idempotency_key
            elif self.headers.get("Idempotency-Key") is not None:
                self._send_json(
                    400,
                    {
                        "error": "idempotency_key_not_allowed_for_local_media_operation",
                        "platform_retry_authorized": False,
                    },
                )
                return

            try:
                body, body_headers = self._read_proxy_body(body_kind)
            except ValueError as exc:
                self._send_json(
                    400,
                    {
                        "error": str(exc),
                        "platform_retry_authorized": False,
                    },
                )
                return
            headers.update(body_headers)
            headers["Content-Length"] = str(len(body))

            parsed_payload: dict[str, object] | None = None
            if body_kind in {"json", "local_json"}:
                try:
                    parsed_payload = strict_json_object(body)
                except ValueError as exc:
                    self._send_json(
                        400,
                        {
                            "error": str(exc),
                            "platform_retry_authorized": False,
                        },
                    )
                    return

            pending_scope: str | None = None
            if platform_write:
                assert idempotency_key is not None
                identity = _dashboard_pending_identity(self.path, method)
                if identity is None:
                    self._send_json(
                        500,
                        {
                            "error": "dashboard_pending_identity_error",
                            "platform_retry_authorized": False,
                        },
                    )
                    return
                pending_scope, resource_key, pending_ad_id = identity
                pending_payload_json: str | None = None
                if body_kind == "json":
                    assert parsed_payload is not None
                    try:
                        pending_payload_json = _json_bytes(
                            parsed_payload
                        ).decode("utf-8")
                    except (UnicodeError, ValueError):
                        # Lone JSON surrogates are not UTF-8. Reject before
                        # claiming or forwarding instead of dropping the socket.
                        self._send_json(
                            400,
                            {
                                "error": "invalid_unicode_text",
                                "platform_retry_authorized": False,
                            },
                        )
                        return
                replay_body: bytes | None = None
                try:
                    if _pending_reconciliation_allowed(store, write_proxy):
                        expected = DashboardPendingWrite(
                            scope=pending_scope, resource_key=resource_key,
                            idempotency_key=idempotency_key, method=method,
                            path=urlsplit(self.path).path,
                            payload_json=pending_payload_json, ad_id=pending_ad_id,
                            acknowledged=False,
                        )
                        with store.dashboard_pending_write_guard():
                            matches = _pending_matches(
                                store, scope=pending_scope, key=idempotency_key,
                                resource=resource_key,
                            )
                            if matches and (
                                len(matches) != 1
                                or replace(matches[0], acknowledged=False) != expected
                            ):
                                raise ValueError("pending write identity conflict")
                            if matches or store.write_api_request(idempotency_key) is not None:
                                replay_body = _completed_write_response(
                                    store, write_proxy, matches[0] if matches else expected,
                                )
                            else:
                                # Keep local read/claim and ACK mutually exclusive.
                                created = store.claim_dashboard_pending_write(
                                    scope=pending_scope, resource_key=resource_key,
                                    idempotency_key=idempotency_key, method=method,
                                    path=expected.path, payload_json=pending_payload_json,
                                    ad_id=pending_ad_id,
                                )
                    else:
                        created = store.claim_dashboard_pending_write(
                            scope=pending_scope,
                            resource_key=resource_key,
                            idempotency_key=idempotency_key,
                            method=method,
                            path=urlsplit(self.path).path,
                            payload_json=pending_payload_json,
                            ad_id=pending_ad_id,
                        )
                    if replay_body is None and created is not True:
                        # An exact duplicate is still an unresolved UI fence:
                        # after a lost backend ACK it may already have written.
                        # Never forward that intent again without separate,
                        # read-only independent reconciliation of the outcome.
                        raise ValueError("existing pending write is unresolved")
                except ValueError:
                    self._send_json(
                        409,
                        {
                            "error": "dashboard_pending_write_conflict",
                            "platform_retry_authorized": False,
                        },
                    )
                    return
                except Exception:
                    self._send_json(
                        500,
                        {
                            "error": "dashboard_pending_store_error",
                            "platform_retry_authorized": False,
                        },
                    )
                    return
                if replay_body is not None:
                    self._send(
                        200, replay_body, "application/json; charset=utf-8",
                        idempotency_replayed=True,
                    )
                    return

            connection = http.client.HTTPConnection(
                write_proxy.host,
                write_proxy.port,
                timeout=float(write_proxy.timeout_seconds),
            )
            try:
                try:
                    connection.request(
                        method,
                        urlsplit(self.path).path,
                        body=body,
                        headers=headers,
                    )
                    response = connection.getresponse()
                    response_status = response.status
                    response_body = response.read(
                        _MAX_PROXY_RESPONSE_BYTES + 1
                    )
                    if len(response_body) > _MAX_PROXY_RESPONSE_BYTES:
                        raise ValueError("write_proxy_response_too_large")
                    content_type = response.getheader("Content-Type", "")
                    if not content_type.lower().startswith("application/json"):
                        raise ValueError("write_proxy_response_not_json")
                    try:
                        response_payload = strict_json_object(response_body)
                    except ValueError as exc:
                        raise ValueError(
                            "write_proxy_response_not_json"
                        ) from exc
                    replayed = (
                        response.getheader("Idempotency-Replayed") == "true"
                    )
                except Exception:
                    self._send_json(
                        502,
                        {
                            "error": "write_proxy_transport_unknown",
                            "platform_retry_authorized": False,
                        },
                    )
                    return
            finally:
                connection.close()

            # The backend operation and its response are settled at this point.
            # A later dashboard-client disconnect must not be misreported as an
            # unknown backend transport outcome or trigger another response.
            self._send(
                response_status,
                response_body,
                "application/json; charset=utf-8",
                idempotency_replayed=replayed,
            )

        def do_GET(self) -> None:
            try:
                self._handle_GET()
            except (sqlite3.Error, StorageOwnerError):
                # No database error messages, SQL or filesystem paths on HTTP.
                self._send_json(503, {"error": "database_unavailable"})

        def _handle_GET(self) -> None:
            target = urlsplit(self.path)
            path = target.path

            if path == "/":
                self._send(
                    200,
                    _DASHBOARD_HTML.encode("utf-8"),
                    "text/html; charset=utf-8",
                )
                return
            if path == "/dashboard.css":
                self._send(
                    200,
                    _DASHBOARD_CSS.encode("utf-8"),
                    "text/css; charset=utf-8",
                )
                return
            if path == "/dashboard.js":
                self._send(
                    200,
                    _DASHBOARD_JS.encode("utf-8"),
                    "text/javascript; charset=utf-8",
                )
                return
            if path == "/healthz":
                self._send_json(200, {"status": "ok"})
                return
            if path == "/readyz":
                if store.is_ready():
                    self._send_json(200, {"status": "ready"})
                else:
                    self._send_json(
                        503,
                        {"status": "unavailable", "error": "database_unavailable"},
                    )
                return
            if path == "/api/dashboard/config":
                self._send_json(
                    200,
                    {
                        "write_ui_available": write_proxy is not None,
                        **(
                            {"generation_ui_available": True}
                            if generation_access is not None else {}
                        ),
                        **({"manual_sync_available": True} if sync_once is not None else {}),
                    },
                )
                return
            if path == "/api/dashboard/pending-writes":
                if write_proxy is None:
                    self._send_json(404, {"error": "not_found"})
                    return
                if not self._write_token_authorized(require_origin=False):
                    self._send_json(
                        403,
                        {
                            "error": "write_session_required",
                            "platform_retry_authorized": False,
                        },
                    )
                    return
                try:
                    records = store.dashboard_pending_writes()
                    pending_rows = [
                        {
                            "scope": record.scope,
                            "key": record.idempotency_key,
                            "method": record.method,
                            "path": record.path,
                            "payload": (
                                json.loads(record.payload_json)
                                if record.payload_json is not None
                                else None
                            ),
                            "adId": record.ad_id,
                            "acknowledged": record.acknowledged,
                        }
                        for record in records
                    ]
                except Exception:
                    self._send_json(
                        500,
                        {
                            "error": "dashboard_pending_store_error",
                            "platform_retry_authorized": False,
                        },
                    )
                    return
                self._send_json(
                    200,
                    {"pending_writes": pending_rows},
                )
                return
            if path == "/api/sync/status":
                self._send_json(200, store.sync_status())
                return
            if path == "/api/sync/recovery":
                self._send_json(200, store.sync_recovery_status())
                return
            if path == "/api/summary":
                payload = summary_to_dict(query.summary())
                payload["sync_status"] = store.sync_status()
                payload["sync_recovery"] = store.sync_recovery_status()
                self._send_json(200, payload)
                return
            if path == "/api/catalog/ads":
                # Observation-only, strict URL schema; never authorize a Write.
                # 120 Unicode scalar characters may occupy up to 1,440
                # percent-encoded bytes; include the independently bounded cursor.
                if len(target.query) > 4096:
                    self._send_json(400, {"error": "invalid_catalog_query"})
                    return
                try:
                    filters = parse_qs(
                        target.query, keep_blank_values=True, max_num_fields=8,
                        encoding="utf-8", errors="strict",
                    )
                    if (set(filters) - {"limit", "after", "q", "status"}
                            or any(len(values) != 1 for values in filters.values())):
                        raise ValueError("invalid fields")
                    count = filters.get("limit", ["50"])[0]
                    if len(count) > 3 or not count.isdecimal():
                        raise ValueError("invalid limit")
                    result = query.catalog_page(
                        limit=int(count),
                        after=filters.get("after", [""])[0],
                        search=filters.get("q", [""])[0],
                        status=filters.get("status", ["all"])[0],
                    )
                except (UnicodeError, ValueError):
                    self._send_json(400, {"error": "invalid_catalog_query"})
                    return
                self._send_json(200, result)
                return
            if path == "/api/portfolio/drafts":
                # Imported SKUs are local draft identities, never owner ad IDs.
                if len(target.query) > 4096:
                    self._send_json(400, {"error": "invalid_draft_query"})
                    return
                try:
                    options = parse_qs(
                        target.query, keep_blank_values=True, max_num_fields=6,
                        encoding="utf-8", errors="strict",
                    )
                    if (set(options) - {"limit", "after", "q"}
                            or any(len(v) != 1 for v in options.values())):
                        raise ValueError("invalid fields")
                    count = options.get("limit", ["50"])[0]
                    if len(count) > 2 or not count.isdecimal():
                        raise ValueError("invalid limit")
                    if isinstance(store, SnapshotStore):
                        drafts = drafts_page(
                            store, limit=int(count),
                            after=options.get("after", [""])[0],
                            search=options.get("q", [""])[0],
                        )
                    else:
                        drafts = {"drafts": [], "has_more": False,
                                  "next_after": None, "total": 0}
                except (UnicodeError, ValueError):
                    self._send_json(400, {"error": "invalid_draft_query"})
                    return
                except (sqlite3.Error, OSError, TypeError):
                    self._send_json(503, {"error": "database_unavailable"})
                    return
                self._send_json(200, drafts)
                return
            if path in ("/api/portfolio/jobs", "/api/portfolio/jobs/detail"):
                if type(store) is not SnapshotStore or write_proxy is None:
                    self._send_json(404, {"error": "not_found"})
                    return
                if not self._write_token_authorized(require_origin=False):
                    self._send_json(403, {"error": "write_session_required"})
                    return
                if len(target.query)>256:
                    self._send_json(400, {"error": "invalid_job_query"})
                    return
                try:
                    options=parse_qs(target.query,keep_blank_values=True,
                                    max_num_fields=4,encoding="utf-8",errors="strict")
                    allowed={"limit"} if path.endswith("/jobs") else {"job_id","limit","after"}
                    if (set(options)-allowed or
                            any(len(v)!=1 for v in options.values())):
                        raise ValueError("invalid fields")
                    page_size=options.get("limit",["50"])[0]
                    if len(page_size)>2 or not page_size.isdecimal():
                        raise ValueError("invalid limit")
                    limit=int(page_size)
                    if path.endswith("/jobs"):
                        answer={"jobs":list_jobs(store,limit=limit)}
                    else:
                        answer=job_details(
                            store,options["job_id"][0],
                            after=options.get("after",[""])[0],limit=limit)
                except (ValueError,UnicodeError,KeyError,PortfolioJobError):
                    self._send_json(400, {"error": "invalid_job_query"})
                    return
                except (sqlite3.Error,OSError,TypeError):
                    self._send_json(503, {"error": "database_unavailable"})
                    return
                self._send_json(200,answer)
                return
            if path == "/api/ads":
                self._send_json(
                    200,
                    [ad_view_to_dict(item) for item in query.latest_ads()],
                )
                return
            if path == "/api/email-reactions":
                self._send_json(
                    200,
                    [
                        email_reaction_view_to_dict(item)
                        for item in query.email_reactions()
                    ],
                )
                return
            if path == "/api/analytics/metrics":
                self._send_json(200, {"metrics": list(ANALYTICS_METRICS)})
                return
            if path == "/api/analytics/contract":
                self._send_json(
                    200,
                    analytics_contract_to_dict(analytics.contract),
                )
                return
            if path == "/api/analytics/dimensions":
                self._send_json(
                    200,
                    {"dimensions": list(ANALYTICS_DIMENSIONS)},
                )
                return
            if path == "/api/analytics/ads.csv":
                metric = _single_query_value(target.query, "metric")
                if metric not in ANALYTICS_METRICS:
                    self._invalid_metric()
                    return
                rows = []
                for item in analytics.rank_ads(metric):
                    evidence = item.metric_evidence
                    rows.append((
                        item.ad_id, metric, item.value, item.title,
                        "" if item.present is None else str(item.present).lower(),
                        item.lifecycle_state.value if item.lifecycle_state is not None else "",
                        evidence.observed_at.isoformat() if evidence is not None else "",
                        evidence.source if evidence is not None else "",
                        "" if evidence is None or evidence.last_known is None
                        else str(evidence.last_known).lower(),
                    ))
                self._send(
                    200, _analytics_csv_bytes(_ANALYTICS_CSV_RANKING_COLUMNS, rows),
                    "text/csv; charset=utf-8",
                    download_filename=f"mark-analytics-{metric}.csv",
                )
                return
            if path == "/api/analytics/ads":
                metric = _single_query_value(target.query, "metric")
                if metric not in ANALYTICS_METRICS:
                    self._invalid_metric()
                    return
                self._send_json(
                    200,
                    [
                        ad_metric_ranking_to_dict(item)
                        for item in analytics.rank_ads(metric)
                    ],
                )
                return
            if path == "/api/analytics/groups.csv":
                dimension = _single_query_value(target.query, "dimension")
                if dimension not in ANALYTICS_DIMENSIONS:
                    self._invalid_dimension()
                    return
                metric = _single_query_value(target.query, "metric")
                if metric not in ANALYTICS_METRICS:
                    self._invalid_metric()
                    return
                rows = [
                    (dimension, metric, item.label, item.sample_size,
                     item.metric_sum, item.metric_mean)
                    for item in analytics.group_rankings(dimension, metric)
                ]
                self._send(
                    200, _analytics_csv_bytes(_ANALYTICS_CSV_GROUP_COLUMNS, rows),
                    "text/csv; charset=utf-8",
                    download_filename=f"mark-groups-{dimension}-{metric}.csv",
                )
                return
            if path == "/api/analytics/groups":
                dimension = _single_query_value(target.query, "dimension")
                if dimension not in ANALYTICS_DIMENSIONS:
                    self._invalid_dimension()
                    return
                metric = _single_query_value(target.query, "metric")
                if metric not in ANALYTICS_METRICS:
                    self._invalid_metric()
                    return
                self._send_json(
                    200,
                    [
                        group_metric_ranking_to_dict(item)
                        for item in analytics.group_rankings(dimension, metric)
                    ],
                )
                return

            parts = [unquote(item) for item in path.split("/") if item]
            if (
                len(parts) == 4
                and parts[0] == "api"
                and parts[1] == "ads"
                and parts[3] in {"history", "reactions", "email-reactions"}
            ):
                ad_id = parts[2]
                if not _valid_ad_id(ad_id):
                    self._send_json(400, {"error": "invalid_ad_id"})
                    return

                if parts[3] == "history":
                    history = query.ad_history(ad_id)
                    if not history:
                        self._send_json(404, {"error": "ad_not_found"})
                        return
                    self._send_json(
                        200,
                        [ad_snapshot_to_dict(item) for item in history],
                    )
                    return

                if parts[3] == "email-reactions":
                    email_reaction = query.email_reaction(ad_id)
                    if email_reaction is None:
                        self._send_json(404, {"error": "ad_not_found"})
                        return
                    self._send_json(
                        200,
                        email_reaction_view_to_dict(email_reaction),
                    )
                    return

                reactions = query.reaction_history(ad_id)
                if not reactions:
                    self._send_json(404, {"error": "ad_not_found"})
                    return
                self._send_json(
                    200,
                    [
                        reaction_snapshot_to_dict(item)
                        for item in reactions
                    ],
                )
                return

            self._send_json(404, {"error": "not_found"})

        def do_HEAD(self) -> None:
            self._method_not_allowed()

        def _ack_dashboard_pending_write(self) -> None:
            target = urlsplit(self.path)
            if target.path != "/api/dashboard/pending-writes/ack" or target.query:
                self._send_json(404, {"error": "not_found"})
                return
            if write_proxy is None:
                self._send_json(404, {"error": "not_found"})
                return
            if not self._write_session_authorized():
                self._send_json(
                    403,
                    {
                        "error": "write_session_required",
                        "platform_retry_authorized": False,
                    },
                )
                return
            if self.headers.get("Idempotency-Key") is not None:
                self._send_json(
                    400,
                    {
                        "error": "idempotency_key_not_allowed_for_pending_ack",
                        "platform_retry_authorized": False,
                    },
                )
                return
            try:
                body, _ = self._read_proxy_body("local_json")
                payload = strict_json_object(body)
                if (
                    set(payload) != {"scope", "idempotency_key"}
                    or not isinstance(payload["scope"], str)
                    or not isinstance(payload["idempotency_key"], str)
                ):
                    raise ValueError("invalid_pending_ack")
                if not _pending_reconciliation_allowed(store, write_proxy):
                    self._send_json(409, {
                        "error": "dashboard_pending_write_reconciliation_required",
                        "platform_retry_authorized": False,
                    })
                    return
                with store.dashboard_pending_write_guard():
                    matches = _pending_matches(
                        store, scope=payload["scope"], key=payload["idempotency_key"],
                    )
                    if (
                        len(matches) != 1 or matches[0].scope != payload["scope"]
                        or matches[0].idempotency_key != payload["idempotency_key"]
                    ):
                        raise ValueError("pending acknowledgement identity conflict")
                    _completed_write_response(store, write_proxy, matches[0])
                    phase = store.acknowledge_dashboard_pending_write(
                        scope=matches[0].scope, idempotency_key=matches[0].idempotency_key,
                    )
                    if phase not in {"acknowledged", "finalized"}:
                        raise ValueError("pending acknowledgement lost record")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                self._send_json(
                    409,
                    {
                        "error": "dashboard_pending_write_conflict",
                        "platform_retry_authorized": False,
                    },
                )
                return
            except Exception:
                self._send_json(
                    500,
                    {
                        "error": "dashboard_pending_store_error",
                        "platform_retry_authorized": False,
                    },
                )
                return
            self._send_json(200, {"acknowledged": True, "finalized": phase == "finalized"})

        def _generation_session_authorized(self) -> bool:
            if generation_access is None:
                return False
            marker = self.headers.get_all("X-Mark-Generation") or []
            tokens = self.headers.get_all("X-Mark-Generation-Token") or []
            origins = self.headers.get_all("Origin") or []
            fetch_sites = self.headers.get_all("Sec-Fetch-Site") or []
            # parse_request already restricts Host to exact loopback
            # listener/public-ingress ports. Bind generation consent to the
            # *browser-facing* Host, not the private frontend netns port.
            hosts = self.headers.get_all("Host") or []
            expected_origin = "http://" + hosts[0] if len(hosts) == 1 else ""
            return (
                len(marker) == 1
                and marker[0] == "1"
                and len(tokens) == 1
                and tokens[0].isascii()
                and hmac.compare_digest(tokens[0], generation_access.ui_token)
                and len(origins) == 1
                and origins[0].isascii()
                and hmac.compare_digest(origins[0], expected_origin)
                and self.headers.get("Authorization") is None
                and (
                    not fetch_sites
                    or len(fetch_sites) == 1 and fetch_sites[0] == "same-origin"
                )
            )

        def _generate_draft(self) -> None:
            # This is NOT a Write API route. Never forward a provider or
            # generated-media request to the platform or media staging API.
            target = urlsplit(self.path)
            if (
                target.path != "/api/dashboard/generate"
                or target.query or target.fragment
                or generation_access is None
            ):
                self._send_json(404, {"error": "not_found"})
                return
            if not self._generation_session_authorized():
                self._send_json(403, {"error": "generation_session_required"})
                return
            if self.headers.get("Idempotency-Key") is not None:
                self._send_json(400, {"error": "generation_idempotency_not_supported"})
                return
            try:
                raw, _ = self._read_proxy_body("local_json")
                request = json.loads(raw.decode("utf-8"))
                if (
                    not isinstance(request, dict)
                    or set(request) != {"mode", "prompt", "acknowledge_provider_cost"}
                    or request["mode"] not in ("text", "image")
                    or request["acknowledge_provider_cost"] is not True
                ):
                    raise ValueError("invalid_request")
                prompt = request["prompt"]
                if (
                    not isinstance(prompt, str)
                    or not 1 <= len(prompt.strip()) <= 4000
                    or any(ord(ch) < 32 and ch not in "\n\t" for ch in prompt)
                    or any(0xD800 <= ord(ch) <= 0xDFFF for ch in prompt)
                ):
                    raise ValueError("invalid_prompt")
            except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError, TimeoutError, OSError):
                self._send_json(400, {"error": "invalid_generation_request"})
                return
            try:
                result = generation_access.generate(request["mode"], prompt)
            except DashboardGenerationBusy:
                self._send_json(409, {"error": "generation_in_progress"})
                return
            except DashboardGenerationLimit:
                self._send_json(429, {"error": "generation_session_budget_exhausted"})
                return
            except Exception:
                # No prompts, credentials, upstream errors or stack details.
                self._send_json(502, {"error": "generation_failed_no_automatic_retry"})
                return
            if request["mode"] == "text":
                if not isinstance(result, AdvertisementDraft):
                    self._send_json(502, {"error": "generation_invalid_output"})
                    return
                self._send_json(200, result.as_dict())
            else:
                try:
                    _validate_png_payload(result)
                except GenerationError:
                    self._send_json(502, {"error": "generation_invalid_output"})
                    return
                self._send(200, result, "image/png")

        def _manual_sync(self) -> None:
            target = urlsplit(self.path)
            if (
                sync_once is None or target.path != "/api/dashboard/sync"
                or target.query or target.fragment
            ):
                self._send_json(404, {"error": "not_found"})
                return
            if not self._write_session_authorized():
                self._send_json(403, {"error": "write_session_required"})
                return
            if self.headers.get("Idempotency-Key") is not None:
                self._send_json(400, {"error": "sync_idempotency_not_supported"})
                return
            try:
                self._read_proxy_body("empty")
            except ValueError:
                self._send_json(400, {"error": "sync_request_body_not_allowed"})
                return
            try:
                count = sync_once()
            except Exception:
                # Never leak CDP credentials, browser state or local paths.
                self._send_json(503, {"error": "owner_inventory_refresh_failed"})
                return
            if type(count) is not int or count < 0:
                self._send_json(503, {"error": "owner_inventory_refresh_failed"})
                return
            self._send_json(200, {"status": "success", "current_ads": count})


        def _portfolio_job_command(self) -> None:
            # Same-origin, per-session token. Local drafts only: no provider
            # calls and no forwarding to the platform Write API.
            target=urlsplit(self.path)
            commands={
                "/api/portfolio/jobs/create",
                "/api/portfolio/jobs/state",
                "/api/portfolio/jobs/review",
                "/api/portfolio/jobs/apply-local",
                "/api/portfolio/jobs/recover",
                "/api/portfolio/jobs/settle-unknown",
                "/api/portfolio/jobs/generate-next",
            }
            if (target.path not in commands or target.query or target.fragment
                    or target.path!=self.path or type(store) is not SnapshotStore
                    or write_proxy is None):
                self._send_json(404, {"error": "not_found"})
                return
            if not self._write_session_authorized():
                self._send_json(403, {"error": "write_session_required"})
                return
            if self.headers.get("Idempotency-Key") is not None:
                self._send_json(400, {"error": "request_key_must_be_in_json"})
                return
            try:
                if self.headers.get("Content-Type","").split(";",1)[0].strip().lower()!="application/json":
                    raise ValueError("invalid content type")
                size=self._content_length(max_bytes=512*1024)
                if size<1:
                    raise ValueError("empty job command")
                previous_timeout=self.connection.gettimeout()
                self.connection.settimeout(_PROXY_BODY_READ_TIMEOUT_SECONDS)
                try:
                    raw=self.rfile.read(size)
                finally:
                    self.connection.settimeout(previous_timeout)
                if len(raw)!=size:
                    raise ValueError("truncated job command")
                payload=strict_json_object(raw)
                if target.path.endswith("/generate-next"):
                    if generation_access is None:
                        self._send_json(404,{"error":"generation_unavailable"})
                        return
                    if not self._generation_session_authorized():
                        self._send_json(403,{"error":"generation_session_required"})
                        return
                    if (set(payload)!={"job_id","acknowledge_provider_cost"}
                            or payload["acknowledge_provider_cost"] is not True):
                        raise PortfolioJobError("explicit single-call consent required")
                    result=generation_access.generate_one_portfolio_proposal(
                        store,payload["job_id"])
                elif target.path.endswith("/create"):
                    if set(payload)!={"mode","skus","search","instruction",
                                     "budget_cents","estimate_cents","request_key"}:
                        raise PortfolioJobError("invalid job creation fields")
                    if payload["mode"]=="all" and payload["skus"]==[]:
                        skus=matching_draft_skus(store,search=payload["search"])
                    elif payload["mode"]=="selected" and payload["search"]=="":
                        skus=payload["skus"]
                    else:
                        raise PortfolioJobError("invalid job selection")
                    result=create_job(store,skus,instruction=payload["instruction"],
                                      request_key=payload["request_key"],
                                      budget_cents=payload["budget_cents"],
                                      estimate_cents=payload["estimate_cents"])
                elif target.path.endswith("/state"):
                    if set(payload)!={"job_id","action"}:
                        raise PortfolioJobError("invalid state command")
                    result=change_state(store,payload["job_id"],payload["action"])
                elif target.path.endswith("/review"):
                    if set(payload)!={"job_id","skus","approve"}:
                        raise PortfolioJobError("invalid review command")
                    result=review_items(store,payload["job_id"],payload["skus"],
                                        approve=payload["approve"])
                elif target.path.endswith("/apply-local"):
                    if set(payload)!={"job_id"}:
                        raise PortfolioJobError("invalid local application")
                    result=apply_approved_local(store,payload["job_id"])
                elif target.path.endswith("/recover"):
                    if set(payload)!={"job_id"}:
                        raise PortfolioJobError("invalid recovery command")
                    result=recover_interrupted(store,payload["job_id"])
                else:
                    if set(payload)!={"job_id","sku","actual_cents","explicitly_checked"}:
                        raise PortfolioJobError("invalid reconciliation command")
                    result=settle_unknown(store,payload["job_id"],payload["sku"],
                        actual_cents=payload["actual_cents"],
                        explicitly_checked=payload["explicitly_checked"])
            except DashboardGenerationBusy:
                self._send_json(409, {"error":"generation_in_progress"})
                return
            except DashboardGenerationLimit:
                self._send_json(429, {"error":"generation_session_limit"})
                return
            except (ValueError,UnicodeError,KeyError,TypeError,RecursionError,
                    PortfolioJobError,TimeoutError):
                self._send_json(400, {"error": "invalid_portfolio_job_command"})
                return
            except (sqlite3.Error,OSError):
                self._send_json(503, {"error": "database_unavailable"})
                return
            except Exception:
                # A provider may charge even if this request failed. The
                # persisted claim is UNKNOWN, never automatically retried.
                self._send_json(502, {"error":"provider_outcome_unknown",
                    "automatic_retry_authorized":False})
                return
            self._send_json(200,result)

        def do_POST(self) -> None:
            if urlsplit(self.path).path.startswith("/api/portfolio/jobs/"):
                self._portfolio_job_command()
                return
            if urlsplit(self.path).path == "/api/dashboard/sync":
                self._manual_sync()
                return
            if urlsplit(self.path).path == "/api/dashboard/generate":
                self._generate_draft()
                return
            if urlsplit(self.path).path == "/api/dashboard/pending-writes/ack":
                self._ack_dashboard_pending_write()
                return
            self._proxy_write("POST")

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_PATCH(self) -> None:
            self._proxy_write("PATCH")

        def do_DELETE(self) -> None:
            self._proxy_write("DELETE")

        def do_OPTIONS(self) -> None:
            # Deliberately no CORS/preflight surface. Browser writes must be
            # same-origin and carry the per-process dashboard write token.
            self._method_not_allowed()

    return DashboardHandler


class LoopbackDashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(
    store: SnapshotStore | StorageOwnerClient,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    analytics_contract: AnalyticsContract | None = None,
    write_proxy: DashboardWriteProxy | None = None,
    generation_access: DashboardGenerationAccess | None = None,
    public_origin_port: int | None = None,
    sync_once: Callable[[], int] | None = None,
) -> LoopbackDashboardServer:
    if host != "127.0.0.1":
        raise ValueError("dashboard must bind to 127.0.0.1")
    if (
        isinstance(port, bool)
        or not isinstance(port, int)
        or not 0 <= port <= 65535
    ):
        raise ValueError("port must be an integer between 0 and 65535")
    if public_origin_port is not None and (
        type(public_origin_port) is not int
        or not 1 <= public_origin_port <= 65535
    ):
        raise ValueError("public_origin_port must be an integer between 1 and 65535")

    if generation_access is not None and not isinstance(generation_access, DashboardGenerationAccess):
        raise TypeError("generation_access must be DashboardGenerationAccess or None")
    if sync_once is not None and (not callable(sync_once) or write_proxy is None):
        raise ValueError("manual sync requires an authenticated local Write session")
    if write_proxy is not None and (
        isinstance(store, StorageOwnerClient) or write_proxy.owner_reconciliation_enabled
    ):
        # The old client remains read/claim only. A dedicated owner composition
        # checks its persisted receipt AND independent witness for each ACK.
        if not _owner_reconciliation_allowed(store, write_proxy):
            raise ValueError("independent storage dashboard Writes are not yet attested")
        store.verify_completion_profile()
    query = MarkQueryService(store)
    analytics = AnalyticsService(store, contract=analytics_contract)
    return LoopbackDashboardServer(
        (host, port),
        _handler_factory(
            store, query, analytics, write_proxy, generation_access,
            public_origin_port=public_origin_port,
            sync_once=sync_once,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    if container_draft_denied():
        print("mark-api: unattested container draft cannot run Python entrypoints",
              file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(
        description="Serve the local read-only dashboard with optional explicit draft generation.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--db", type=Path,
        help="Trusted offline SQLite database (never mount in untrusted runtime).",
    )
    source.add_argument(
        "--owner-socket", type=Path,
        help="Read-only analytics over the independent SQLite-owner Unix socket.",
    )
    parser.add_argument(
        "--owner-uid", type=int,
        help="Required exact SO_PEERCRED UID for --owner-socket (no DB fallback).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Loopback port (default: 8765).",
    )
    parser.add_argument(
        "--enable-generation",
        action="store_true",
        help="Explicitly unlock locally authenticated, individually consented, billable text/image draft generation; defaults off.",
    )
    parser.add_argument(
        "--reaction-metric",
        choices=REACTION_METRICS,
        default=None,
        help=(
            "Explicit interpretation of 'wie viele geschrieben haben'; "
            "unset by default."
        ),
    )
    parser.add_argument(
        "--objective-metric",
        choices=ANALYTICS_METRICS,
        default=None,
        help=(
            "Explicit analytics objective used to preselect rankings; "
            "unset by default."
        ),
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Explicitly initialize a new SQLite store; omit on normal starts.",
    )
    args = parser.parse_args(argv)
    if args.enable_generation:
        if not os.environ.get("OPENAI_API_KEY"):
            parser.error("--enable-generation requires OPENAI_API_KEY in the local process environment")
        # Check optional dependency before SQLite, token generation or bind.
        # Import alone never contacts the provider or spends request budget.
        try:
            from openai import OpenAI as _GenerationSdkClient
        except Exception:
            parser.error("--enable-generation requires installed mark-api[generation]")
        if not callable(_GenerationSdkClient):
            parser.error("--enable-generation requires a functional OpenAI SDK")

    if args.owner_socket is not None:
        if args.init_db:
            parser.error("--init-db is forbidden with --owner-socket")
        if args.owner_uid is None:
            parser.error("--owner-uid is required for --owner-socket")
        try:
            store = StorageOwnerClient(
                args.owner_socket, expected_owner_uid=args.owner_uid,
            )
            if not store.is_ready():
                parser.error("independent SQLite owner unavailable")
        except (StorageOwnerError, OSError):
            parser.error("independent SQLite owner unavailable")
    else:
        if args.owner_uid is not None:
            parser.error("--owner-uid requires --owner-socket")
        try:
            store = SnapshotStore(args.db, create_if_missing=args.init_db)
        except (OSError, sqlite3.Error):
            parser.error("SQLite database unavailable or not initialized")
    contract = AnalyticsContract(
        reaction_metric=args.reaction_metric,
        objective_metric=args.objective_metric,
    )
    generation_access = (
        DashboardGenerationAccess(ui_token=secrets.token_urlsafe(32))
        if args.enable_generation else None
    )
    server = create_server(
        store,
        port=args.port,
        analytics_contract=contract,
        generation_access=generation_access,
    )
    if generation_access is not None:
        host, port = server.server_address
        print(
            f"Kleinanzeiger: http://{host}:{port}/"
            f"#generation_token={quote(generation_access.ui_token, safe='')}",
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())