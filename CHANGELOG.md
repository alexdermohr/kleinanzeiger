# Änderungen am Kleinanzeiger

Hier steht, was sich für Nutzerinnen und Nutzer mit jeder veröffentlichten Version ändert.
Die wichtigsten Änderungen erscheinen bei einem verfügbaren Update direkt im Kontrollfenster.

## [1.8.2] - 2026-11-22

### Behoben

- KDE: Escape in Ja/Nein- und Hinweisdialogen wird korrekt als Abbruch behandelt. Die native KDialog-Rückgabe 2 führte bisher fälschlich zu einer Fehlermeldung.
- Fehlerhafte KDialog-Aufrufe mit Diagnose auf stderr werden nicht mehr als normaler Abbruch verschluckt, auch wenn deren Rückgabecode 1 lautet.
- Bei Abbruch keine Änderungen an Anzeigen, SQLite, SACD-Suche, Kundendaten oder Abruffrequenz.

## [1.8.1] - 2026-11-22

### Behoben

- **Automatischer SACD-Suchlauf nach einem Neustart:** Wenn der systemd-Timer früher als 30 Minuten nach dem letzten erfolgreichen Abruf startet, wird dieser erwartete Leerlauf ausdrücklich als „übersprungen (Cooldown)“ protokolliert und nicht länger fälschlich als fehlgeschlagene Suche angezeigt.
- **Abrufschutz unverändert:** Ein übersprungener Lauf fragt weder Kleinanzeigen ab noch verändert er Suchzeitpunkt, rotierenden Suchbegriff, gespeicherte Inserate, Berichte oder SQLite-Historie. Echte Fehler wie beschädigte Daten, parallele Abrufe oder HTTP-/Layout-Probleme werden weiterhin mit Fehlerstatus gemeldet. Der reguläre 30-Minuten-Timer bleibt bestehen.

## [1.8.0] - 2026-11-21

### Neu

- **Linux/KDE zeigt offizielle Updates automatisch an:** Die Kontrollzentrale prüft bei jedem Start einmal im Hintergrund die neueste stabile Veröffentlichung auf GitHub. Neuere Versionsnummern erscheinen im Aktionsmenü und in der Diagnose, ohne andere Funktionen bei langsamer oder fehlender Internetverbindung aufzuhalten.
- **Versionshinweise auf Wunsch öffnen:** Unter **Updates / Version prüfen** lässt sich die offizielle Release-Seite nach ausdrücklicher Bestätigung im Browser öffnen. Der Vergleich ist numerisch, die GitHub-Weiterleitung auf genau dieses Repository begrenzt und vorläufige Versionen werden nicht als stabiles Update dargestellt. Linux-Updates werden nicht automatisch installiert; lokale Daten, SACD-Timer und bestehende Programmversionen bleiben erhalten.

## [1.7.1] - 2026-11-20

### Behoben

- Die getrennte **Farbe prüfen**-Liste bleibt nach einem Suchlauf mit SACD-Angeboten ohne belegte Silberfarbe auch bei den folgenden rotierenden Suchbegriffen sichtbar. Die letzten sechs erfolgreichen Berichte werden dafür wiederverwendet, höchstens 72 Stunden alte Beobachtungen.
- Der Bericht nennt den **zuletzt beobachteten Zeitpunkt** und behauptet keine aktuelle Verfügbarkeit. Bereits als silbern bestätigte Inserats-IDs sowie Anzeigen, die bei einer neueren Sichtung ausdrücklich schwarz oder anderweitig ungeeignet sind, verschwinden auch über folgende Suchläufe hinweg aus der Fotoprüfliste. Es entstehen weder zusätzliche Netzabrufe noch automatische Farb- oder Schnäppchenalarme.

## [1.7.0] - 2026-11-20

### Neu

- **Mehr Suchabdeckung bei unveränderter Abruffrequenz:** Die bisher kaum ertragreiche Marantz-Silber-Suche wird durch „SACD Player“ ersetzt. Alle drei Suchvarianten rotieren weiterhin mit genau einem Abruf je 30 Minuten.
- **Farbe prüfen:** Plausible SACD-Modelle ohne Silberangabe im Titel stehen nur auf einer getrennten Foto-Prüfliste im HTML-/JSON-Bericht. Sie werden nicht als silberne Treffer, neue Inserate oder Schnäppchen gemeldet. Schwarze, defekte, Zubehör- und reine CD-Geräte bleiben ausgeschlossen.
- **Persönliche Preisziele pro erkanntem Modell:** Im Linux/KDE-Schnäppchenjäger unter Monitoring ein eigenes EUR-Maximum setzen oder löschen. Ein frischer Angebotsfestpreis unter der Grenze wird einmalig als **persönliches Preisziel erreicht** gemeldet – getrennt von modellbezogenen Marktmedianen. Ohne Preisziel oder vier echte Modellpeers wird kein Marktwert behauptet.
- Preisziele liegen atomar in einer gesonderten privaten Datei; bestehende Suchprofile, EML-Radar, SQLite-Historie, systemd-Timer und Rückfallmöglichkeit bleiben erhalten.

## [1.6.0] - 2026-11-20

### Neu

- Die öffentliche Linux/KDE-SACD-Suche rotiert drei feste Suchbegriffe (allgemeine silberne SACD-Player, SACD plus Silber, Marantz SACD Silber). Pro systemd-Lauf erfolgt weiterhin genau eine HTTPS-Abfrage, höchstens alle 30 Minuten. Bestehende Zeitstempel-Sperrdateien werden ohne Verlust der Abrufsperre gelesen.
- **Konservativer Modellpreisvergleich:** Erst bei vier anderen, höchstens 72 Stunden alten Anzeigen desselben identifizierbaren Herstellermodells und Zustands wird ein Angebotsmedian gebildet. 30 Prozent unter diesem Median gelten als auffällig günstig; andere Modelle, ältere Preise und unerkannte Varianten bleiben unbewertet.
- Neu oder erst später bewertbare auffällig günstige SACD-Angebote erhalten einmalige, preisversionsgebundene Meldungen; der erste öffentliche Lauf bleibt ohne Altanzeigenalarm. Keine Doppelmeldungen bei identischen wiederholten Abfragen.
- HTML und JSON unterscheiden Kandidaten ohne Vergleichsgrundlage von konservativ bewerteten Angeboten und zeigen Anzahl der Modellpeers sowie den Median nur, wenn die Grundlage ausreicht. Kein Anspruch auf bestätigte Verkaufspreise oder geprüften Gerätezustand.

## [1.5.2] - 2026-11-19

### Verbessert

- SACD-Kandidatenbericht zeigt die tatsächlich beobachteten Festpreisangebote aufsteigend nach Preis, mit Original-Links. Unterschiedliche Modelle werden nicht als vergleichbare Schnäppchen gewertet.
- KDE: Im Schnäppchenjäger unter Monitoring kann die Desktop-Benachrichtigung mit einer klar gekennzeichneten Testmeldung überprüft werden – ohne fiktive Anzeigen und ohne Änderung der SQLite-Historie.
- Anleitung und Kontrollzentrale beschreiben die bereits produktive automatische SACD-Suche statt veralteter gegenteiliger Hinweise. Die öffentliche Suche bleibt getrennt vom EML-Radar.

## [1.5.1] - 2026-11-19

### Behoben

- Öffentliche SACD-Suche nutzt unter NixOS mit isoliertem Python automatisch das vertrauenswürdige System-Zertifikatsbündel, falls die Python-Laufzeit keine CA-Zertifikate enthält; bereits konfigurierte OpenSSL-CA-Verzeichnisse bleiben nutzbar. TLS-Hostname und Zertifikatprüfung bleiben aktiv; keine unsicheren HTTPS-Ausnahmen.

## [1.5.0] - 2026-11-19

### Neu

- Linux/KDE: Eigene, öffentliche Suche nach silbernen SACD-Playern. Separates optionales Profil ohne E-Mail oder API-Schlüssel; systemd-Benutzertimer höchstens alle 30 Minuten.
- Bestehende SACD-Titelprüfung, lokale SQLite-Historie und HTML-/JSON-Berichte erkennen neu erschienene IDs und spätere Festpreisänderungen einmalig. Erster Abruf ist eine Bestandsaufnahme ohne Altanzeigenalarm.
- Technische Sperren und unbekannte Webseitenlayouts werden nicht umgangen; fehlgeschlagene Abrufe lassen letzte gültige Ergebnisse und das getrennte EML-Profil unverändert.

## [1.4.1] - 2026-11-17

### Behoben

- SACD-Bericht zeigt weiterhin historisch beobachtete Kandidaten mit ungeprüfter Verfügbarkeit.
- KDE-Monitoring zeigt lokalen EML-Eingang, letzte Auswertung und Importdiagnosen; der SACD-Bericht lässt sich direkt aus dem Menü öffnen.

## [1.4.0] - 2026-11-17

### Neu
- Genau eine vordefinierte Käufer-Radar-Kategorie „SACD-Player silber“ für Deutschland ohne Preislimit: KDE-Knopf „Silberne SACD-Player beobachten“ und CLI `kleinanzeiger-radar create-silver-sacd`. Idempotente, getrennte Profilidentität; Start, Status und Stop bleiben bewusst gewählt.
- Konservative Mailtitelprüfung auf SACD-Disc-Player und explizite Silberfarbe; ND8006, Defektanzeigen, Zubehör und unklare Angaben werden diagnostiziert. Foto und Funktion bleiben selbst zu prüfen.
- Neue passende Geräte und echte Preisänderungen erscheinen einmalig in HTML/JSON und optional als Desktop-Hinweis. Kategorie ist immer „nicht bewertbar“ ohne Modellpreisvergleich; `hits=0`, zusätzlich `candidate_events` und `reference_type=category_unpriced`. Bestehende exakte Modellprofile behalten ihre Bewertung.
- Offizielle Suchadresse und Erklärung zum nativen Suchauftrag im eigenen Konto. Kein Plattform- oder Mailboxzugriff, keine Aktivierung bei Erstellung. Strenger Ein-Angebot-Parser; echte Suchmailvorlagen und produktiver EML-Zulauf bleiben unverifiziert.
- Windows-Release-, ZIP-, Manifest- und Installationsreferenzen konsistent auf 1.4.0 aktualisiert.

## [1.3.0] - 2026-11-17

### Neu
- Linux/KDE: „Neues Suchprofil einrichten“ erstellt nach Eingabe eines exakten Modells einen privaten, getrennten EML-Eingang mit eigener Konfiguration, Historie und HTML-/JSON-Berichten. Wiederholen überschreibt keine vorhandene Konfiguration; andere Modelle erhalten eigene SHA-256-Identitäten.
- Nachträglich beurteilbare Angebote werden im EML-Modus einmalig gemeldet, auch wenn das günstige Inserat vor den vier erforderlichen Vergleichsangeboten eingetroffen ist.
- Auch bei mehr als fünf rasch eintreffenden EML-Dateien bleibt der lokale Watcher aktiv; vollständige Inseratsdaten werden nicht ins Systemjournal geschrieben.
- Optionales Monitoring über je ein systemd-User-Path/Service-Paar reagiert auf Dateiänderungen, ohne Timer oder Polling. Start erfolgt bewusst im Menü, Status und Stoppen sind dort erreichbar. Installierte Python-Umgebung und KDE-/Nix-PATH für notify-send werden berücksichtigt; Startfehler rollen die Einrichtung zurück.
- KDE: Suchprofile nach lesbarer Modellbezeichnung wählen und eigene EML-Exporte direkt per Dateidialog atomar importieren.
- Neue lokale CLI `kleinanzeiger-radar`: Profile auch ohne Desktop erstellen, vorhandene eigene exportierte EMLs atomar und anhand ihres Inhalts idempotent zustellen. Originale und unbekannte Mails bleiben erhalten.
- Keine automatische Marktabfrage, Mailkonto-Anmeldung oder mitgeliefertes aktives Suchmodell. Der Nutzer muss die Suche auf der Website speichern und eigene EMLs zustellen. Der begrenzte Parser bleibt synthetisch getestet; echte Suchmailtemplates und ein produktiver Zulauf sind weiterhin nicht verifiziert. Windows behält die manuelle CLI-/GUI-Auswertung.

## [1.2.0] - 2026-11-16

### Neu
- Windows 11 und Linux: Das lokale Schnäppchenradar kann ausdrücklich freigegebene exportierte Suchmail-Dateien ohne Mailkonto oder zusätzliche Python-Abhängigkeiten lesen. Ein eigenes Profil, ein einmalig gewählter lokaler Suchmailordner und aktuelle Maildatumsangaben sind erforderlich; Anleitung und Profil stehen in docs/schnaeppchenjaeger.md und examples/search-mail-radar.json.
- Der begrenzte Parser unterstützt ausschließlich die dokumentierten direkt verlinkten Text-/HTML-Layouts. Echte Kleinanzeigen-Mailtemplates sind nicht verifiziert. Unsichere oder unvollständige Inhalte werden diagnostiziert, ohne Angebote zu erfinden. Chatmails bleiben beim bisherigen separaten Import.
- Die Windows-Schnäppchenjäger-Oberfläche kann ein eigenes Radar-JSON-Profil ausführen und den HTML-Bericht öffnen; der native Windows-SelfTest prüft Erst- und Wiederholungsimport.
- Die KDE-Kontrollzentrale kann den Radar über eine gewählte JSON-Profildatei ausführen. Veraltete EML-Dateien blockieren den Eingang bei größerem Mailbestand nicht.
- HTML-/JSON-Berichte und eigene Historie zeigen neue Inserate und Preisänderungen. Mindestens vier aktuelle passende Vergleichsangebote sind für einen günstigen Treffer erforderlich. Optional kann vorhandenes Linux notify-send einmal pro Änderung hinweisen; Zustellung ist nicht garantiert. Unter Windows bleiben CLI und Berichte ohne Desktop-Hinweis nutzbar.
- Keine Marktplatzabrufe, Gmail-/IMAP-Anmeldung, automatischen Käufe, KI oder Hintergrundaktivierung. EML-Originale werden ausschließlich gelesen.

## [1.1.0] - 2026-11-16

### Neu
- Lokales Schnäppchenradar mit wiederholt einlesbaren CSV-/JSON-Dateien oder Eingangsordner, exakt benanntem Modellprofil und eigener SQLite-Historie. Neue Inserate und Preisänderungen erscheinen im HTML- und optional JSON-Bericht; identische Wiederholungen erzeugen keine neuen Treffer.
- Transaktionen und SQLite-Sperren schützen wiederholte Timer-Läufe. Fremde Datenbanken werden abgewiesen. Die Bewertung verwendet ausschließlich beobachtete Angebotspreise der Eingaben und meldet bei fehlenden Vergleichsangeboten keine Schnäppchen.
- Keine automatischen Marktplatzabrufe, Käufe, Nachrichten, KI oder zusätzlichen Abhängigkeiten. Bestehende manuelle Windows-/Linux-Auswertung bleibt verfügbar.

## [1.0.15] - 2026-11-15

### Behoben
- Kleinanzeiger-Chrome und Kontoabgleich funktionieren wieder: Eine versehentlich auf die regulären, auf das eigene Chrome-Profil begrenzten CDP-Produktfunktionen angewendete Legacy-Transport-Sperre wurde entfernt.
- Die tatsächlich alten direkten HTTP-, mobilen und Browser-Bot-Adapter bleiben unverändert gesperrt. Vorhandene Kontoanmeldung, Bestandsdaten und notwendige Bestätigungen für Plattformänderungen bleiben erhalten.

## [1.0.14] - 2026-11-12

### Linux/KDE-Ergänzung vom 15. November 2026
- Neue KDE-Kontrollzentrale (`kleinanzeiger-center`) mit Konto-/Offline-Start, SQLite-Backup über die geprüfte CLI, bestätigtem hashgebundenem CSV/XLSX-Artikelimport, lokalem Wochenbericht, Schnäppchenjäger, Diagnose und Handbuch. NixOS-Installation und Desktop-Eintrag sind dokumentiert.

### Behoben
- Direkte Aufrufe der alten privaten Browser-/CDP-Adapter prüfen die Plattformberechtigung vor einem Verbindungsaufbau. Ohne nachgewiesene Berechtigung bleiben sie gesperrt; lokale Offline-Funktionen und synthetische Tests funktionieren weiter.

## [1.0.13] - 2026-11-10

### Behoben
- Ein doppelter Klick auf »Pausieren« oder »Aktivieren« (oder eine Aktion auf eine bereits passende Anzeige) sperrt die Anzeige nicht mehr dauerhaft. Kleinanzeiger meldet verständlich, dass nichts geändert wurde, und die Anzeige bleibt bearbeitbar. Von älteren Versionen so gesperrte Anzeigen werden durch erneutes Anklicken derselben Schaltfläche wieder freigegeben, ohne erneute Plattformaktion.
- Nach einem unklaren Ergebnis bleibt eine Anzeige nicht mehr für immer gesperrt: Nach »Mit Kleinanzeigen abgleichen« lässt sich die Sperre ausdrücklich aufheben. Dabei wird nichts an Kleinanzeigen gesendet und nichts automatisch wiederholt.
- Nach dem Pausieren oder Aktivieren zeigt das Bearbeitungsfeld sofort den neuen Status an.
- Sammelaktionen »Auswahl pausieren/aktivieren« überspringen Anzeigen, die schon im gewünschten Zustand sind, statt die ganze Auswahl abzulehnen. Lehnt Kleinanzeigen eine einzelne Anzeige ohne Änderung ab, läuft der Stapel weiter; bei einem unklaren Ergebnis hält er weiterhin an. Unpassende Anzeigen werden mit ihrer Nummer genannt.

### Verbessert
- Ergebnis- und Statusmeldungen im Dashboard sind auf Deutsch und ohne Fachbegriffe formuliert (zum Beispiel »Pausieren: erledigt und von Kleinanzeigen bestätigt«, »Aktiv«/»Pausiert« statt »active«/»paused«).
- Offene Aktionen werden beim Start mit Anzeigennummer und Aktion genannt (zum Beispiel »Noch offen: Anzeige 123 (Pausieren)«) samt Hinweis, wie sie sich klären lassen.
- Die Kennzahlen für Aufrufe, Merker und Antworten zeigen »Noch keine Daten« beziehungsweise »1.234 (aus 87 Anzeigen)« statt »0 / 0 Ads«.

## [1.0.12] - 2026-11-09

### Behoben
- Bestimmte ungültige oder zu große Anfragen werden im Windows-Kontrollfenster wieder geordnet abgelehnt, statt die lokale Verbindung unerwartet zu trennen. Es werden dabei keine Plattformaktionen automatisch wiederholt.
- Sicherungen einer laufenden SQLite-Datenbank auf Btrfs-Dateisystemen werden nicht mehr allein wegen unterschiedlicher Gerätekennungen derselben Datei abgewiesen. Integritäts- und Wiederherstellungsprüfungen bleiben erhalten.

## [1.0.11] - 2026-11-09

### Behoben
- Nach einer erfolgreich bestätigten Änderung an einer eigenen Anzeige schließt das Dashboard die lokale Schreibbestätigung nun vollständig ab. Dadurch bleibt keine bereits erledigte Anfrage fälschlich als offen stehen.
- Bei einer fehlenden oder unklaren Bestätigung bleibt die betroffene Aktion gesperrt, ohne die Plattformänderung erneut auszulösen.

## [1.0.10] - 2026-11-08

### Verbessert
- Falls Chrome oder die Browsersteuerung beim Start nicht verfügbar ist, können Anwender im lokalen Arbeitsbereich weiterhin Artikel vorbereiten, Verkaufsvermerke pflegen und lokale KI-Aufträge verwalten.
- Der Offline-Arbeitsbereich ist auf die aktuelle Windows-Sitzung beschränkt. Plattform-Schreibaktionen und Kontoabgleich bleiben deaktiviert; frühere Programmversionen können weiterhin gestartet werden.

## [1.0.9] - 2026-11-08

### Neu
- Im Windows-Kontrollfenster unter »Weitere Aktionen → Schnäppchenjäger« eigene CSV-/JSON-Angebote lokal bewerten und als HTML-Bericht öffnen oder eine reguläre Kleinanzeigen-Suche im Browser starten. Kein automatisches Einsammeln.

### Behoben
- Zubehör, Defekte und abweichende Modell-, Editions- oder Speichervarianten werden nicht mehr als gleichwertige Preisvergleiche ausgegeben.
- Die Auswertung großer Bestände ist schneller; unbekannte Zustandsangaben gelten nicht als belegte Übereinstimmung. Nullpreis-Angebote mit Versand und voneinander abweichende Terabyte-Speicher werden nicht mehr als Preisvergleich missverstanden.

## [1.0.8] - 2026-11-08

### Verbessert
- Im Windows-Kontrollfenster kann eine ausgewählte Sicherung auch bei vorhandenem Datenbestand wiederhergestellt werden.
- Vor der Wiederherstellung wird der aktuelle Bestand automatisch als separate Sicherung mit Prüfnachweis aufbewahrt. Ein ungültiger Sicherungsstand wird vor dem Austausch abgewiesen.

## [1.0.7] - 2026-11-08

### Behoben
- Wenn beim Kontoabgleich der verwendete Chrome-Reiter nicht „Meine Anzeigen“ zeigt, erklärt Kleinanzeiger den gezielten nächsten Schritt statt fälschlich einen Internetausfall zu vermuten.
- Der fehlgeschlagene Abgleich verändert vorhandene Anzeigen nicht und startet keine automatische Wiederholung.

## [1.0.6] - 2026-11-08

### Verbessert
- Im Bestandsvergleich führt „Anzeige prüfen“ direkt zur exakt zugehörigen Eigenanzeige, auch bei großen Beständen mit mehr als 50 Suchtreffern.
- Veraltete oder mehrdeutige lokale Treffer werden nicht als passende Anzeige dargestellt; der Vergleich startet keine Konto-, Plattform- oder KI-Aktion.

## [1.0.5] - 2026-11-08

### Verbessert
- Der Hinweis zum Kontoabgleich schlägt die Schaltfläche nur vor, wenn sie in der aktuellen Sitzung tatsächlich nutzbar ist.
- Ohne Berechtigung für den Kontoabgleich erklärt das Kontrollfenster den lokalen Lesemodus. Gespeicherte Anzeigen bleiben verfügbar.

## [1.0.4] - 2026-11-07

### Verbessert
- Die Änderungsanzeige passt ihre Kurzbeschreibung automatisch an die verfügbare Breite an. „Alle Änderungen“ bleibt auch bei längeren Versionshinweisen gut lesbar und anklickbar.
- Die vollständige Liste der Änderungen ist weiterhin vor und nach einem Update erreichbar.

## [1.0.3] - 2026-11-07

### Neu
- Das Kontrollfenster zeigt die wichtigste Änderung einer verfügbaren Version direkt neben dem Updatehinweis.
- Über „Alle Änderungen ansehen“ sind die vollständigen Versionshinweise vor und nach einem Update abrufbar.
- Jede neue Windows-Veröffentlichung erhält ihre Versionshinweise verbindlich aus diesem Changelog.

### Verbessert
- Updatehinweise erscheinen weiterhin ohne automatische Installation. Ohne Internet bleibt die Anwendung bedienbar.

## [1.0.2] - 2026-11-07

### Verbessert
- Der letzte bestätigte Kontoabgleich wird gut sichtbar mit Datum und Uhrzeit angezeigt.
- Bei Anmelde- oder Verbindungsproblemen erscheinen verständliche Hinweise zum weiteren Vorgehen.
- Ein fehlgeschlagener neuer Abgleich überschreibt nicht den Zeitpunkt des letzten erfolgreichen Abgleichs.

## [1.0.1] - 2026-11-07

### Neu
- Kleinanzeiger erkennt neuere offizielle Versionen beim Öffnen des Kontrollfensters automatisch.
- Die verfügbare Versionsnummer und ein Updateknopf werden angezeigt. Die Installation erfolgt weiterhin nur auf Klick.

## [1.0.0] - 2026-11-07

### Erste Kundenversion
- Eigene Kleinanzeigen verwalten, Bestand abgleichen, Artikel importieren, Kennzahlen und Berichte ansehen.
- Entwürfe mit optionaler KI vorbereiten; kostenpflichtige Aufrufe nur nach ausdrücklicher Freigabe.
- Windows-Einrichtung mit Kundenhandbuch, Sicherung und Wiederherstellung.

Unbeaufsichtigte Massenveröffentlichungen und automatischer Nachrichtenversand sind nicht enthalten.