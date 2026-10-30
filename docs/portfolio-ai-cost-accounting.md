# KI-Kostenbericht – Bestandsanzeigen und lokale Entwürfe

Der lokale Bericht `GET /api/portfolio/report?days=7|30|90` zählt
ab dieser Änderung **beide** persistenten KI-Auftragsarten:
`portfolio_job_items` für lokale unveröffentlichte Entwürfe und
`portfolio_existing_job_items` für bestätigte eigene veröffentlichte Anzeigen.

Bis einschließlich v0.1.21 berücksichtigte die Kostenübersicht nur
Entwurfsaufträge. Dadurch konnten Kostenreservierungen und UNKNOWN-Fälle
der wesentlich größeren Eigenanzeigen-KI-Aufträge im Kontrolling fehlen.

## Felder und Rechnungsgrenzen

Die bisherigen Felder in `ai_usage_estimate_all_time` bleiben erhalten:
- `usd_cent_estimate`: protokollierte, nicht manuell geklärte Providerkosten
  als USD-Cent-Schätzung. Keine eigenständig geprüfte Anbieterrechnung.
- `usd_cent_reserved`: Summe der weiterhin gesperrten USD-Cent-Beträge
  aller Auftragsitems (einschließlich möglicher UNKNOWN-/INFLIGHT-Fälle).
- `unknown_outcomes`: aktuell unklare Providerergebnisse.
- `inflight_outcomes`: laufende, möglicherweise schon kostenpflichtige Calls.
- `invoice_verified = false`: auch wenn einzelne Kosten extern geprüft
  wurden, ist damit **keine** gesamte KI-Rechnung attestiert.

Ergänzt:
- `usd_cent_checked_unknown`: bei `error_code='manually_settled'`
  unabhängig und ausdrücklich nachgeprüfte USD-Cent-Kosten. Der persistente
  Status kann nach einem weiteren **lokalen** Review von \`rejected\` zu
  \`approved\` wechseln, ohne dass ein neuer Vorschlag oder Plattformwrite
  entsteht. Die bereits geprüften Kosten bleiben trotzdem ausweisbar.
- `usd_cent_known_total`: Token-Schätzung plus nachgeprüfte UNKNOWN-Beträge,
  **ohne** reservierte, noch unklare Gebühren.
- `source_breakdown.local_draft_jobs` und
  `source_breakdown.published_owned_ad_jobs`: pro Klasse `items`,
  `token_estimate`, `checked`, `reserved`, `unknown`, `inflight`.
  Ein `null`-Teil bedeutet: Die Tabelle wurde noch nie angelegt;
  er bedeutet nicht „es wurden nachweislich keine Rechnungen gestellt“.

Alle Beträge sind **USD-Cent**, keine Euro-Beträge. Es gibt keine
automatische Währungsumrechnung, keine externe Rechnungsabfrage und keine
doppelte Zählung eines manuell geklärten Provider-Calls in der Token-Schätzung.

## Sicherheit und Anzeige

Der gesamte Datenabgleich läuft in **einer lokalen SQLite-Lesetransaktion**.
Fehlende optionale Tabellen werden nicht erzeugt. Ungültige negative
Beträge, fremde SQLite-Typen oder widersprüchliche
`manually_settled`-Zustände werden als Datenbankfehler abgewiesen;
der Dashboard-HTTP-Handler sendet dann 503 statt falsche Erfolgssummen.

Das bereits vorhandene Dashboard zeigt
`usd_cent_estimate` als Token-basierten Betrag. Die manuell geklärten
Gebühren stehen zusätzlich getrennt in der lokalen Bericht-JSON-Antwort;
die **sichtbare Ergänzung dieser Zeile** gehört in einen separaten
UI-Slice, weil die Dashboarddatei gleichzeitig durch den aktiven
100er-KI-Stapel-Writer bearbeitet wird. Bis dahin dürfen die
`usd_cent_checked_unknown` nicht mit `usd_cent_estimate` verwechselt werden.

Vertrag: weder Plattform-Read noch Plattform-Write, keine KI-Provideranfrage
und kein Rücksetzen oder Neuversuchen eines UNKNOWN-Claims. Keine
unbeabsichtigten Kosten durch das Öffnen des Berichts.

Technische Tests: bestehende lokale Entwurfskosten, reine Eigenanzeigenkosten,
UNKNOWN-Reservierung, externe Kostenklärung, 10.000 synthetische
Eigenanzeigen-KI-Items, Korruptionsablehnung und gebundene
HTTP-Lokalsitzung. Die Tests verwenden ausschließlich synthetische Daten.
