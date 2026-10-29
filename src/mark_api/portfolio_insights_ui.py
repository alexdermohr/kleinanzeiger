"""Customer-facing inventory comparisons and local price rules, without platform effects."""

INSIGHTS_HTML = """
    <section id="inventory-diff-panel" class="panel" hidden>
      <h2>Bestandsänderungen</h2>
      <p>Vergleich der letzten zwei bestätigten Kontoabgleiche. Nicht mehr sichtbar bedeutet nicht automatisch verkauft oder gelöscht.</p>
      <label>Änderungstyp
        <select id="inventory-diff-filter">
          <option value="all">Alle Anzeigen</option>
          <option value="newly_observed">Neu beobachtet</option>
          <option value="changed">Geändert</option>
          <option value="paused">Pausiert</option>
          <option value="unclear">Unklar / nicht sichtbar</option>
          <option value="unchanged">Unverändert</option>
          <option value="no_baseline">Ohne Vergleichsrunde</option>
          <option value="unverified">Nicht aktuell bestätigt</option>
        </select>
      </label>
      <button id="inventory-diff-load" type="button">Bestand vergleichen</button>
      <button id="inventory-diff-next" type="button" disabled>Nächste 50</button>
      <p id="inventory-diff-status" role="status" aria-live="polite">Noch kein Vergleich geladen.</p>
      <div class="table-scroll"><table>
        <thead><tr><th>Anzeigen-ID</th><th>Änderung</th><th>Vorher</th><th>Jetzt</th><th>Quelle und Zeitpunkt</th></tr></thead>
        <tbody id="inventory-diff-body"></tbody>
      </table></div>
      <details id="autopilot-disclosure" class="feature-disclosure">
        <summary>Priorisierter Tagesplan (lokal und kostenlos)</summary>
        <p>Vorrang für unklare KI-Kosten, neue importierte E-Mail-Hinweise, manuelle Wiedervorlagen und offene KI-Vorschläge. Nur Prüfvorschläge aus gespeicherten Daten; keine automatische Kontoaktion, kein Nachrichtenversand, kein KI-Aufruf.</p>
        <button id="autopilot-load" type="button">Aufgaben prüfen</button>
        <button id="autopilot-next" type="button" disabled>Nächste 50 Aufgaben</button>
        <p id="autopilot-status" role="status" aria-live="polite">Noch keine Aufgabenprüfung durchgeführt.</p>
        <div class="table-scroll"><table>
          <thead><tr><th>Priorität</th><th>Anzeige</th><th>Prüfschritt</th><th>Lokaler Nachweis</th></tr></thead>
          <tbody id="autopilot-body"></tbody>
        </table></div>
      </details>
    </section>
    <section id="price-rules-panel" class="panel" hidden>
      <h2>Preisregeln für lokale Artikel</h2>
      <p>Vorschläge für importierte, unveröffentlichte Entwürfe. Weder der Artikelpreis noch eine Anzeige auf Kleinanzeigen wird automatisch geändert.</p>
      <div class="catalog-toolbar">
        <label>Lokale Artikelnummer <input id="price-rule-sku" maxlength="80" autocomplete="off"></label>
        <button id="price-rule-open" type="button">Artikel laden</button>
        <button id="price-rule-list" type="button">Gespeicherte Regeln</button>
      </div>
      <p id="price-rule-original">Zuerst einen lokalen Artikel laden oder in der Artikelliste „Preisregel“ wählen.</p>
      <div class="catalog-toolbar">
        <label>Mindestpreis in ganzen Euro <input id="price-rule-min" type="number" min="1" max="99999999" step="1"></label>
        <label>Änderungsschritt in ganzen Euro <input id="price-rule-step" type="number" min="1" max="99999999" step="1"></label>
        <label>Wartezeit ab Speichern in Tagen <input id="price-rule-wait" type="number" min="0" max="3650" step="1"></label>
      </div>
      <p id="price-rule-preview" role="status" aria-live="polite">Keine Preisänderung freigegeben.</p>
      <button id="price-rule-save" type="button" disabled>Regel lokal speichern</button>
      <button id="price-rule-delete" type="button" disabled>Lokale Regel entfernen</button>
      <p id="price-rule-status" role="status" aria-live="polite"></p>
      <div class="table-scroll"><table>
        <thead><tr><th>Artikel</th><th>Ausgangspreis</th><th>Mindestpreis</th><th>Schritt</th><th>Wartezeit</th><th>Vorschlag / Begründung</th><th>Aktion</th></tr></thead>
        <tbody id="price-rule-body"></tbody>
      </table></div>
      <button id="price-rule-next" type="button" disabled>Nächste 50 Regeln</button>
    </section>
"""

INSIGHTS_JS = r"""
let inventoryDiffCursor = null;
let inventoryDiffEpoch = null;
let inventoryDiffFilterSerial = 0;
let inventoryDiffBusy = false;
let priceRuleCursor = null;
let priceRuleTarget = null;
let priceRuleBusy = false;
let priceRuleListBusy = false;
let priceRuleLoadSerial = 0;
let autopilotNextOffset = null;
let autopilotBusy = false;

function renderPortfolioInsights() {
  const available = jobsAvailable && localUiReady();
  for (const id of ["inventory-diff-panel", "price-rules-panel", "nav-inventory-diff", "nav-price-rules"]) {
    const element = optionalById(id);
    if (element) element.hidden = !available;
  }
}

function insightCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value === null || value === undefined ? "—" : String(value);
  row.appendChild(cell);
  return cell;
}

async function readInsight(path) {
  const response = await fetch(path, {cache: "no-store", headers: _jobHeaders()});
  if (!response.ok) {
    if (response.status === 409) throw new Error("Bestand inzwischen geändert. Bitte den Vergleich neu laden.");
    throw new Error("Lokale Daten nicht verfügbar. Bitte neu laden; es wurde keine Plattformaktion ausgeführt.");
  }
  return response.json();
}

async function loadInventoryDiff(next = false) {
  if (!jobsAvailable || !localUiReady() || inventoryDiffBusy || (next && !inventoryDiffCursor)) return false;
  const filter = byId("inventory-diff-filter").value || "all";
  const filterSerial = inventoryDiffFilterSerial;
  inventoryDiffBusy = true;
  byId("inventory-diff-load").disabled = true;
  byId("inventory-diff-next").disabled = true;
  const status = byId("inventory-diff-status");
  status.textContent = "Vergleiche gespeicherte Kontoabgleiche …";
  try {
    const query = new URLSearchParams({limit: "50"});
    if (filter !== "all") query.set("state", filter);
    if (next) { query.set("after", inventoryDiffCursor); query.set("epoch", inventoryDiffEpoch); }
    const page = await readInsight("/api/portfolio/inventory-diff?" + query.toString());
    if (filterSerial !== inventoryDiffFilterSerial ||
        (byId("inventory-diff-filter").value || "all") !== filter) return false;
    if (!page || !Array.isArray(page.items) || typeof page.epoch !== "string" ||
        (page.state_filter && page.state_filter !== filter)) throw new Error("Ungültige Vergleichsdaten.");
    inventoryDiffCursor = page.has_more ? page.next_after : null;
    inventoryDiffEpoch = page.epoch;
    const body = byId("inventory-diff-body");
    body.replaceChildren();
    const states = {newly_observed: "Neu beobachtet", changed: "Geändert", paused: "Pausiert",
                    unchanged: "Unverändert", no_baseline: "Erster Abgleich", unclear: "Unklar", unverified: "Nicht aktuell bestätigt"};
    for (const item of page.items) {
      const row = document.createElement("tr");
      insightCell(row, item.ad_id);
      const label = item.current && item.current.state === "absent" ? "Nicht mehr im Bestand" : (states[item.state] || "Unklar");
      insightCell(row, label + ": " + item.reason);
      insightCell(row, item.previous ? [item.previous.title || "", item.previous.state].join(" · ") : "—");
      insightCell(row, item.current ? [item.current.title || "", item.current.state].join(" · ") : "Nicht bestätigt");
      insightCell(row, item.current ? item.current.source + " · " + item.current.observed_at : "—");
      body.appendChild(row);
    }
    const summary = page.total === null
      ? page.items.length + " passende Anzeigen auf dieser Seite (global gefiltert)."
      : page.items.length + " von " + page.total + " Anzeigen.";
    status.textContent = page.state === "ready"
      ? summary + " Aktuell: " + page.confirmed_at + " (" + page.source + "). Vorher: " + (page.previous_confirmed_at || "noch kein Vergleich") + "."
      : "Kein aktueller, vollständiger Vergleich bestätigt. Letzter erfolgreicher Abgleich: " + (page.last_confirmed_at || "noch keiner") + ". Fehlende Daten sind kein Löschbeleg.";
    return true;
  } catch (error) {
    inventoryDiffCursor = null;
    inventoryDiffEpoch = null;
    byId("inventory-diff-body").replaceChildren();
    status.textContent = error.message;
    return false;
  } finally {
    inventoryDiffBusy = false;
    byId("inventory-diff-load").disabled = !localUiReady();
    byId("inventory-diff-next").disabled = !inventoryDiffCursor || !localUiReady();
  }
}

async function loadAutopilotAttention(next = false) {
  if (!jobsAvailable || !localUiReady() || autopilotBusy || (next && autopilotNextOffset === null)) return false;
  const offset = next ? autopilotNextOffset : 0;
  autopilotBusy = true;
  byId("autopilot-load").disabled = true;
  byId("autopilot-next").disabled = true;
  const status = byId("autopilot-status");
  status.textContent = "Prüfe ausschließlich lokal gespeicherte Aufgaben …";
  try {
    const query = new URLSearchParams({offset: String(offset), limit: "50"});
    const page = await readInsight("/api/portfolio/attention?" + query.toString());
    if (!page || !Array.isArray(page.items) || page.offset !== offset ||
        page.platform_writes !== 0 || page.provider_requests !== 0 ||
        page.platform_messages_read !== false ||
        page.automatic_execution_authorized !== false ||
        !Number.isSafeInteger(page.total) || page.total < 0 ||
        page.items.length > 50 || typeof page.has_more !== "boolean" ||
        (page.has_more && (!Number.isSafeInteger(page.next_offset) || page.next_offset <= offset))) {
      throw new Error("Unklare lokale Priorisierung. Erneut laden; keine Aktion starten.");
    }
    const labels = {
      ai_uncertain: "Unklaren KI-Anbieteraufruf unabhängig prüfen; nie blind wiederholen",
      eml_unreviewed: "Neue importierte E-Mail-Benachrichtigung lokal prüfen; kein Live-Posteingang",
      manual_overdue: "Manuelle Wiedervorlage überfällig; kein Plattform-Ablaufbeleg",
      ai_proposal: "Gespeicherten KI-Vorschlag mit Original vergleichen und lokal freigeben/ablehnen",
      manual_due_soon: "Manuelle Wiedervorlage innerhalb von sieben Tagen prüfen"
    };
    const body = byId("autopilot-body");
    body.replaceChildren();
    for (const item of page.items) {
      if (!Object.prototype.hasOwnProperty.call(labels, item.kind) ||
          !Number.isSafeInteger(item.priority) || typeof item.ad_id !== "string" ||
          typeof item.reference !== "string" || typeof item.evidence !== "string") {
        throw new Error("Ungültige lokale Aufgabendaten. Keine Aktion starten.");
      }
      const row = document.createElement("tr");
      insightCell(row, item.priority + 1);
      insightCell(row, item.ad_id);
      insightCell(row, labels[item.kind]);
      insightCell(row, item.reference + " · " + item.evidence);
      body.appendChild(row);
    }
    autopilotNextOffset = page.has_more ? page.next_offset : null;
    status.textContent = page.total + " lokale Prüfpunkte; davon " +
      (page.counts?.ai_uncertain || 0) + " unklare/laufende KI-Vorgänge und " +
      (page.counts?.eml_unreviewed || 0) + " neue importierte Hinweise. " +
      "Keine KI-Kosten oder Plattformaktion durch die Prüfung. Seite " +
      (Math.floor(offset / 50) + 1) + ".";
    return true;
  } catch (error) {
    autopilotNextOffset = null;
    byId("autopilot-body").replaceChildren();
    status.textContent = error.message;
    return false;
  } finally {
    autopilotBusy = false;
    byId("autopilot-load").disabled = !localUiReady();
    byId("autopilot-next").disabled = autopilotNextOffset === null || !localUiReady();
  }
}

function priceRuleNumber(id, minimum, maximum) {
  const raw = byId(id).value.trim();
  if (!/^[0-9]{1,8}$/.test(raw)) throw new Error("Bitte ganze Zahlen innerhalb der angezeigten Grenzen eingeben.");
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) throw new Error("Preis oder Wartezeit liegt außerhalb der zulässigen Grenzen.");
  return value;
}

function previewPriceRule() {
  const target = priceRuleTarget;
  const writable = !!target && target.sku === byId("price-rule-sku").value.trim()
    && Number.isSafeInteger(target.local_price_eur) && localUiReady() && !priceRuleBusy;
  byId("price-rule-save").disabled = !writable;
  byId("price-rule-delete").disabled = !target || !target.rule || !localUiReady() || priceRuleBusy;
  if (!writable) { byId("price-rule-preview").textContent = "Artikel laden. Keine Preisänderung freigegeben."; return; }
  try {
    const floor = priceRuleNumber("price-rule-min", 1, 99999999);
    const step = priceRuleNumber("price-rule-step", 1, 99999999);
    const wait = priceRuleNumber("price-rule-wait", 0, 3650);
    if (floor > target.local_price_eur) throw new Error("Der Mindestpreis darf nicht über dem lokalen Ausgangspreis liegen.");
    const proposed = Math.max(floor, target.local_price_eur - step);
    byId("price-rule-preview").textContent = "Vorschau: " + target.local_price_eur + " € → " + proposed
      + " €; frühestens " + wait + " Tage nach Speichern dieser Regel. "
      + (proposed === target.local_price_eur ? "Mindestpreis erreicht; keine Senkung. " : "Ein Änderungsschritt, begrenzt durch den Mindestpreis. ")
      + "Nur Vorschlag; der Artikelpreis bleibt unverändert.";
  } catch (error) {
    byId("price-rule-preview").textContent = error.message;
    byId("price-rule-save").disabled = true;
  }
}

async function loadPriceRule(sku) {
  if (!jobsAvailable || !localUiReady() || priceRuleBusy) return false;
  const selected = String(sku).trim();
  const serial = ++priceRuleLoadSerial;
  priceRuleTarget = null;
  byId("price-rule-sku").value = selected;
  previewPriceRule();
  byId("price-rule-status").textContent = "Lade lokalen Ausgangspreis und Regel …";
  try {
    const data = await readInsight("/api/portfolio/price-rules/detail?sku=" + encodeURIComponent(selected));
    if (serial !== priceRuleLoadSerial || byId("price-rule-sku").value.trim() !== selected) return false;
    if (!data || data.sku !== selected) throw new Error("Artikelzuordnung nicht bestätigt.");
    priceRuleTarget = data;
    const rule = data.rule;
    byId("price-rule-original").textContent = "Artikel " + data.sku + ": "
      + (data.local_price_eur === null ? "kein verfügbarer lokaler Entwurf" : "Ausgangspreis " + data.local_price_eur + " €")
      + ". Quelle: lokaler importierter Artikel; kein ausgelesener Plattformpreis.";
    byId("price-rule-min").value = rule ? String(rule.minimum_price_eur) : String(data.local_price_eur || "");
    byId("price-rule-step").value = rule ? String(rule.reduction_step_eur) : "1";
    byId("price-rule-wait").value = rule ? String(rule.wait_days) : "7";
    byId("price-rule-status").textContent = rule ? rule.reason + " Vorschlag frühestens ab " + rule.eligible_at : "Noch keine Regel gespeichert.";
    previewPriceRule();
    const panel = optionalById("price-rules-panel");
    if (panel && typeof panel.scrollIntoView === "function") panel.scrollIntoView({block: "start"});
    return true;
  } catch (error) {
    if (serial === priceRuleLoadSerial) byId("price-rule-status").textContent = error.message;
    return false;
  }
}

async function loadPriceRules(next = false) {
  if (!jobsAvailable || !localUiReady() || priceRuleListBusy || (next && !priceRuleCursor)) return false;
  priceRuleListBusy = true;
  byId("price-rule-list").disabled = true;
  byId("price-rule-next").disabled = true;
  try {
    const query = new URLSearchParams({limit: "50"});
    if (next) query.set("after", priceRuleCursor);
    const page = await readInsight("/api/portfolio/price-rules?" + query.toString());
    if (!page || !Array.isArray(page.rules)) throw new Error("Ungültige Regelliste.");
    priceRuleCursor = page.has_more ? page.next_after : null;
    const body = byId("price-rule-body");
    body.replaceChildren();
    for (const rule of page.rules) {
      const row = document.createElement("tr");
      for (const value of [rule.sku, rule.local_price_eur, rule.minimum_price_eur, rule.reduction_step_eur, rule.wait_days + " Tage"]) insightCell(row, value);
      insightCell(row, (rule.suggested_price_eur === null ? "Kein Preisvorschlag" : rule.suggested_price_eur + " €") + ": " + rule.reason + " Frühestens " + rule.eligible_at);
      const action = insightCell(row, "");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Regel bearbeiten";
      button.addEventListener("click", () => loadPriceRule(rule.sku));
      action.appendChild(button);
      body.appendChild(row);
    }
    if (!page.rules.length) byId("price-rule-status").textContent = "Keine gespeicherten Regeln auf dieser Seite.";
    return true;
  } catch (error) {
    priceRuleCursor = null;
    byId("price-rule-body").replaceChildren();
    byId("price-rule-status").textContent = error.message;
    return false;
  } finally {
    priceRuleListBusy = false;
    byId("price-rule-list").disabled = !localUiReady();
    byId("price-rule-next").disabled = !priceRuleCursor || !localUiReady();
  }
}

async function mutatePriceRule(action) {
  if (!jobsAvailable || !localUiReady() || priceRuleBusy || !priceRuleTarget) return false;
  const target = priceRuleTarget;
  if (target.sku !== byId("price-rule-sku").value.trim()) return false;
  const payload = {action, sku: target.sku, expected_revision: target.rule ? target.rule.revision : 0,
                   request_key: _newJobRequestKey()};
  try {
    if (action === "set") {
      payload.minimum_price_eur = priceRuleNumber("price-rule-min", 1, 99999999);
      payload.reduction_step_eur = priceRuleNumber("price-rule-step", 1, 99999999);
      payload.wait_days = priceRuleNumber("price-rule-wait", 0, 3650);
    } else if (action !== "delete" || !target.rule) { return false; }
  } catch (error) { byId("price-rule-status").textContent = error.message; return false; }
  priceRuleBusy = true;
  previewPriceRule();
  try {
    const response = await fetch("/api/portfolio/price-rules/mutate", {
      method: "POST", cache: "no-store", headers: {..._jobHeaders(), "Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error("Regel nicht bestätigt. Artikel neu laden und Werte prüfen; nicht blind wiederholen.");
    const result = await response.json();
    if (result.sku !== target.sku || result.platform_writes !== 0) throw new Error("Speicherergebnis unklar. Regel neu laden.");
    priceRuleTarget = null;
    byId("price-rule-status").textContent = "Lokal gespeichert. Kein Artikelpreis und keine Plattformanzeige wurde geändert. Artikel für weitere Änderungen neu laden.";
    await loadPriceRules();
    return true;
  } catch (error) {
    priceRuleTarget = null;
    byId("price-rule-status").textContent = error.message;
    return false;
  } finally {
    priceRuleBusy = false;
    previewPriceRule();
  }
}

for (const [id, handler] of [
  ["inventory-diff-load", () => loadInventoryDiff()], ["inventory-diff-next", () => loadInventoryDiff(true)],
  ["autopilot-load", () => loadAutopilotAttention()], ["autopilot-next", () => loadAutopilotAttention(true)],
  ["nav-inventory-diff", () => loadInventoryDiff()], ["nav-price-rules", () => loadPriceRules()],
  ["price-rule-open", () => loadPriceRule(byId("price-rule-sku").value)],
  ["price-rule-list", () => loadPriceRules()], ["price-rule-next", () => loadPriceRules(true)],
  ["price-rule-save", () => mutatePriceRule("set")], ["price-rule-delete", () => mutatePriceRule("delete")],
]) {
  const element = optionalById(id);
  if (element) element.addEventListener("click", handler);
}
const inventoryFilter = optionalById("inventory-diff-filter");
if (inventoryFilter) inventoryFilter.addEventListener("change", () => {
  // A changed filter invalidates both the cursor and every in-flight reply.
  inventoryDiffFilterSerial += 1;
  inventoryDiffCursor = null;
  inventoryDiffEpoch = null;
  byId("inventory-diff-body").replaceChildren();
  byId("inventory-diff-next").disabled = true;
  if (!inventoryDiffBusy) return loadInventoryDiff();
  byId("inventory-diff-status").textContent = "Filter gewechselt. Den Vergleich danach erneut laden.";
  return false;
});
for (const id of ["price-rule-min", "price-rule-step", "price-rule-wait"]) {
  const element = optionalById(id);
  if (element) element.addEventListener("input", previewPriceRule);
}
const priceRuleSku = optionalById("price-rule-sku");
if (priceRuleSku) priceRuleSku.addEventListener("input", () => {
  priceRuleLoadSerial += 1;
  priceRuleTarget = null;
  previewPriceRule();
});
"""