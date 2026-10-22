# Periodischer Bestandsabgleich – technisch getestete Read-only-Komponente

Stand: 20. Oktober 2026. Die Klasse `PeriodicOwnerSync` unterstützt
synthetisch geprüfte SQLite-Read-/Fehlerjournale, bestätigte Leerbestände,
ABSENT-Übergänge und begrenztes Scheduling. Diese Komponente ist **keine
Freigabe** zur Nutzung automatisierter Kleinanzeigen-Schnittstellen.

## Öffentlicher Einstieg: fail-closed

Der Modulstart `python -m mark_api.periodic_sync` verweigert bis zur
unabhängigen Prüfung eines tatsächlich zugelassenen offiziellen Transports
und der konto-/scopegebundenen Plattformberechtigung alle CDP-/Plattformreads,
auch mit `--once` oder `--immediate`. Er öffnet dabei weder die SQLite-DB
noch einen Browser und liefert Exit-Code 2. Eine angemeldete Chrome-Sitzung
oder ein lokales Flag reicht nicht als Rechtfertigung (Issue #111).

Beispiel zur **negativen Kontrolle**, kein auszuführender Plattform-Read:

    python -m mark_api.periodic_sync --db /pfad/mark.sqlite --cdp-port 9222 --once
    # Exit 2: automatic Kleinanzeigen/CDP reads are disabled

Eigene bereitgestellte `.eml`-Dateien oder offizielle Exporte bleiben
über den unabhängigen Offline-Import verfügbar. Der rein synthetische
`PeriodicOwnerSync(runtime_factory=FakeRuntime, ...)`-Integrationsseam bleibt
für Regressionen erhalten; daraus entsteht keinerlei Plattformautorität.

## Verbindliche Betriebs- und Sicherheitsgrenze

**Nur bei später nachgewiesener offizieller Berechtigung und freigegebenem Transport: Als alleiniger Benutzer der CDP-Lesesitzung verwenden. Nicht parallel
zu einem Plattformwriter, einem zweiten Inventurprozess oder einem anderen
Browserautomaten mit derselben Konto-/CDP-Sitzung starten.** Die vorhandene
Write API und der neue Standalone-Synchronisierer besitzen noch keinen
gemeinsamen prozessübergreifenden CDP-Mutex. Die produktweite automatische
Aktivierung ist bis zum Nachweis dieser Exklusivität weiterhin NO-GO.
Die Browseranmeldung ist keine behauptete externe Plattformberechtigung.

Der Normalstart führt den ersten Read erst nach dem konfigurierten Intervall
aus. --immediate ist eine ausdrückliche Ausnahme. Intervalle sind auf 60 bis
86400 Sekunden begrenzt. Jeder weitere Read beginnt erst nach dem Ende des
vorhergehenden Versuchs; es gibt keine parallelen Requests oder
Catch-up-Schleife.

Vor jedem Browserkontakt wird ein eigener persistenter in_progress-Eintrag
im vorhandenen SQLite-sync_attempts-Journal eröffnet. Erfolgreiche
Bestandsbeobachtungen und success_empty/success_nonempty einschließlich
ABSENT-Übergänge werden atomar gemeinsam geschrieben. Ein bereits als
ABSENT dokumentiertes Inserat erhält bei nachfolgenden leeren Bestandsreads
keine weiteren identischen ABSENT-Zeilen; die neuesten Zustände werden
mit **einem** read-only SQLite-Stream ermittelt, dessen letzte Zustände
anhand echter UTC-Zeitpunkte (einschließlich Mikrosekunden) und Eintrags-IDs
verglichen werden. Unterschiedliche Zeitzonen-Offsets dürfen eine
ABSENT-/ACTIVE-Reihenfolge nicht verfälschen. Es gibt weder einen neuen
SQLite-Verbindungsaufbau noch einen vollständigen History-Sort pro Anzeige.
Erst eine erneute Beobachtung als vorhanden erlaubt einen späteren
ABSENT-Übergang.
Fehlerhafte oder unauthentifizierte Reads überschreiben weder vorher
bestätigte Snapshots noch den letzten tatsächlich erfolgreichen Abgleich.
**Jede fehlgeschlagene Inventur beendet den Polling-Prozess mit Fehlercode.**
Das umfasst Transportfehler: Ein Login-Redirect kann von der darunterliegenden
CDP-Komponente statt als UNAUTHENTICATED auch als TRANSPORT_ERROR erscheinen.
Aus Sicherheitsgründen startet der Scheduler in diesem unklaren Zustand keine
neuen automatischen Managementabfragen. Nach Prüfung ist ein expliziter
Neustart notwendig, auch nach einem bloß temporären Netzwerkfehler.

SIGINT und SIGTERM beenden die Planung weiterer Zyklen. Auch wenn das
Stoppsignal erst zwischen zwei Zykluschecks eingeht, endet der Prozess
regulär ohne neuen Browserzugriff und ohne irreführenden Fehlercode.
Ein bereits begonnener Browser-Read darf keine falsche Erfolgsaussage erzeugen.
Ein Prozessabbruch bzw. SIGKILL kann in_progress hinterlassen: Der Ausgang
bleibt dann ungeklärt. Ein unklarer SQLite-Commit wird niemals durch einen
blinden Retry oder einen erfundenen Erfolgsstatus kaschiert; der bestehende
CAS beim Versuchabschluss entscheidet unabhängig. Wenn auch der Versuch,
eine Fehlerklasse zu speichern, wegen ausgefallener SQLite scheitert,
erscheint nach außen ausschließlich der begrenzte Fehler samt Exit-Code 2;
der angefangene Versuch bleibt dokumentiert als in_progress/ungeklärt.

Das Modul stellt weder Plattform-POST-/PATCH-/DELETE-Operationen noch
eine Write API bereit, verlangt keinen generischen Freigabe-Boolean
und erzeugt keine Credential-/Cookieexporte. Die endgültige Verbindung
mit dem Produktlauncher, vollständige installierte Endnutzerläufe sowie
ein exklusiver gemeinsamer CDP-/Write-Scheduler bleiben gesondert zu
implementieren und zu prüfen.