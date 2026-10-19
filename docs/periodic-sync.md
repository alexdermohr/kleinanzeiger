# Periodischer Bestandsabgleich – isolierte Read-only-Komponente

Stand: 19. Oktober 2026. Der Modulstart python -m mark_api.periodic_sync
bietet einen ausdrücklich gestarteten, ausschließlich lesenden periodischen
Besitzerbestandsabgleich über eine bestehende authentifizierte lokale
Chrome-/Chromium-CDP-Sitzung. Er verwendet den fest auf den Owner-Management-
Ursprung gebundenen Browser-GET aus PR #127, ohne Cookieexport und ohne
WriteCapabilities.

## Bedienung

    # Nur eine bereits initialisierte Mark-SQLite-Datei, keine Neuanlage
    python -m mark_api.periodic_sync --db /pfad/mark.sqlite --cdp-port 9222 --interval-seconds 300

    # Ein unmittelbarer ausdrücklich ausgelöster Read
    python -m mark_api.periodic_sync --db /pfad/mark.sqlite --cdp-port 9222 --once

    # Sofortiger Read, danach regulärer Abstand
    python -m mark_api.periodic_sync --db /pfad/mark.sqlite --cdp-port 9222 --immediate --interval-seconds 300

## Verbindliche Betriebs- und Sicherheitsgrenze

**Nur als alleiniger Benutzer der CDP-Lesesitzung verwenden. Nicht parallel
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
keine weiteren identischen ABSENT-Zeilen; erst eine erneute Beobachtung als
vorhanden erlaubt wieder einen zukünftigen ABSENT-Übergang.
Fehlerhafte oder unauthentifizierte Reads überschreiben weder vorher
bestätigte Snapshots noch den letzten tatsächlich erfolgreichen Abgleich.
**Jede fehlgeschlagene Inventur beendet den Polling-Prozess mit Fehlercode.**
Das umfasst Transportfehler: Ein Login-Redirect kann von der darunterliegenden
CDP-Komponente statt als UNAUTHENTICATED auch als TRANSPORT_ERROR erscheinen.
Aus Sicherheitsgründen startet der Scheduler in diesem unklaren Zustand keine
neuen automatischen Managementabfragen. Nach Prüfung ist ein expliziter
Neustart notwendig, auch nach einem bloß temporären Netzwerkfehler.

SIGINT und SIGTERM beenden die Planung weiterer Zyklen. Ein bereits
begonnener Browser-Read darf keine falsche Erfolgsaussage erzeugen.
Ein Prozessabbruch bzw. SIGKILL kann in_progress hinterlassen: Der Ausgang
bleibt dann ungeklärt. Ein unklarer SQLite-Commit wird niemals durch einen
blinden Retry oder einen erfundenen Erfolgsstatus kaschiert; der bestehende
CAS beim Versuchabschluss entscheidet unabhängig.

Das Modul stellt weder Plattform-POST-/PATCH-/DELETE-Operationen noch
eine Write API bereit, verlangt keinen generischen Freigabe-Boolean
und erzeugt keine Credential-/Cookieexporte. Die endgültige Verbindung
mit dem Produktlauncher, vollständige installierte Endnutzerläufe sowie
ein exklusiver gemeinsamer CDP-/Write-Scheduler bleiben gesondert zu
implementieren und zu prüfen.