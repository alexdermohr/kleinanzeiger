# Text- und Bildentwürfe für Anzeigen

Stand: 20.10.2026. **Technischer Funktionsumfang:** Ein installierbarer
`mark-api-generate`-Befehl und die optionale lokale Dashboard-Ansicht
erstellen nicht veröffentlichte Text- und Bildvorschläge. Nur der Nutzer
entscheidet über eine spätere Übernahme oder Anzeigenaktion. Generierte
Inhalte sind kein Beleg für Zustand, Marke oder Aussehen des Artikels.

## Installation und explizite Aktivierung

```bash
python -m pip install '.[generation]'
# Schlüssel ausschließlich lokal im Prozessumfeld, nicht in Dateien/URL.
export OPENAI_API_KEY='...'
```

`python -m mark_api` und `mark-api-offline` sind die sicheren
Produktstarts für Local/Private. Sie bleiben standardmäßig
plattformfrei und benötigen keinen KI-Schlüssel. Für bewusst angeforderte
Entwürfe schaltet `--enable-generation` die separate lokale
Generierungsfunktion frei:

```bash
python -m mark_api --db /pfad/zur/bestehenden-mark.sqlite --enable-generation
# Alternativ der eigene Standalone-Einstieg:
mark-api-dashboard --db /pfad/zur/bestehenden-mark.sqlite --enable-generation
```

Auch mit aktivierter Generierung bleiben im Offline-Launcher sämtliche
Kleinanzeigen-Reads, CDP-Verbindungen und Plattformwrites deaktiviert.
Ein fehlender Schlüssel oder SDK-Zusatz verhindert den Start ohne
stillschweigende Datenbankinitialisierung. Die ausdrückliche Aktivierung
allein löst noch **keinen** Provider-Aufruf aus.

Die CLI gibt die Dashboard-Adresse mit einem zufälligen, pro Prozess neu
erzeugten `#generation_token=...`-URL-Fragment aus. Dieser Token ist
lokales Sitzungsgeheimnis: nicht weitergeben oder in Logs speichern.
Die Browseroberfläche übernimmt ihn in `sessionStorage` und entfernt
das Fragment aus der sichtbaren URL. Der Backend-API-Schlüssel gelangt
niemals in Browsercode. Ein fehlender Schlüssel verhindert bereits den
Dashboard-Start mit `--enable-generation`. Ohne Option ist
`/api/dashboard/generate` nicht erreichbar (404).

**Für jede einzelne Anfrage** bestätigt der Benutzer zusätzlich, dass
seine Artikelangaben an einen externen KI-Anbieter gehen und Kosten
entstehen können. Die Bestätigung verfällt sofort nach einer Anfrage.
Die lokale API verlangt den korrekten Sitzungstoken, einen exakten
same-origin `Origin`, einen CSRF-Marker und begrenztes JSON.
Es gibt weder CORS noch Plattform-Cookies/-Schreibcapabilities, keine
automatischen Provider-Retries und höchstens **10 Generationen pro
Dashboard-Prozess**, davon jeweils nur eine gleichzeitig. Ein
unklarer/verlorener Provider-Erfolg zählt zum Budget und löst keinen
automatischen Wiederholungsversuch aus.

Das Dashboard zeigt Text und Bilder in einer gesonderten
**Entwurfsvorschau** an. Bei aktivierter *separater* sicherer Write-UX
kann der Text ausdrücklich in die Neue-Anzeige-Maske übernommen oder
ein generiertes Bild lokal zur Dateiauswahl hinzugefügt werden. Bestehende
Textfelder werden nicht ohne Benutzerbestätigung ersetzt. Das ist
**keine** Veröffentlichung und auch keine selbsttätige Media-Stage-Aktion.
Beim reinen Standalone-Dashboard bleiben die Übernahmeknöpfe
korrekterweise deaktiviert, weil es keine Plattformwrite-Befugnis hat;
Entwurfstexte lassen sich dort manuell kopieren.

## Separater CLI-Einstieg

```bash
printf '%s\n' 'Gut erhaltenes Trekkingrad, Größe M, Preis 120 Euro' > artikel.txt
mark-api-generate text --prompt-file artikel.txt --acknowledge-provider-cost
# JSON: title, description, status=draft, published=false

printf '%s\n' 'Neutrale Illustration eines Trekkingrads' > bildidee.txt
mark-api-generate image --prompt-file bildidee.txt --output bild-entwurf.png --acknowledge-provider-cost
# Ausschließlich neue lokale PNG-Datei, Modus 0600
```

`--prompt-file -` liest aus stdin. `--prompt` ist möglich, aber
ungeeignet für vertrauliche Daten, da Prozessargumente sichtbar sein
können. `--model` erlaubt nur gültige, vom Anbieter verfügbare
Modellnamen. Der voreingestellte Textmodus verwendet `gpt-5-mini`,
der Bildmodus `gpt-image-2.5-flare`. Das ältere Modell
`gpt-image-1` wird am 23.10.2026 abgeschaltet. Der Anbieter muss für das eigene
API-Konto freigeschaltet sein; ein solcher Zugang wird nicht behauptet.
Ein Aufruf kann kostenpflichtig sein. Der installierte CLI-Einstieg verweigert
ohne das ausschließlich für den aktuellen Aufruf ausdrücklich gesetzte Flag
--acknowledge-provider-cost jeden Providerkontakt, auch bei gesetztem API-Key.
Vor einem einzelnen Text- oder Bildaufruf zeigt er den gewählten Modus,
das tatsächliche Modell sowie die derzeit bekannte, unverbindliche
modellabhängige Kostenbasis an (ebenfalls bei Verwendung des Flags).
CLI-Skripte müssen diese Bestätigung für jeden Aufruf bewusst setzen;
es gibt keine dauerhafte globale oder implizite Zustimmung.

OpenAI-Standard-Listenpreise (20.10.2026, USD):
Text mit gpt-5-mini: 0.25 USD pro 1 Mio Text-Eingabetokens und
2.00 USD pro 1 Mio Text-Ausgabetokens.
Bilder mit gpt-image-2.5-flare: 5.00 USD pro 1 Mio
Text-Eingabetokens und 30.00 USD pro 1 Mio Bild-Ausgabetokens.
Der tatsächliche Betrag hängt vom Tokenverbrauch und ggf. weiteren
modellabhängigen Eingabe- oder Cachekosten ab. Weder ein fixer
Bildpreis noch eine Kostenobergrenze sind zugesichert.
Für abweichende Modelle muss der Nutzer den aktuellen Tarif selbst prüfen;
keine frei erfundenen Modellpreise verwenden.
Vor jeder Kostenbestätigung die gültigen Tarife unter
https://developers.openai.com/api/docs/pricing prüfen.

Das Dashboard zeigt dieselben beiden Standardmodelle und Preisbasen
oberhalb des einmalig anzuklickenden Zustimmungsfeldes an. Sein
Backend verlangt weiterhin pro Anfrage den gesonderten
acknowledge_provider_cost-Wert sowie Origin und Sitzungstoken.

Der Textanbieter erhält die Vorgabe, ausschließlich vom Benutzer
belegte Fakten zu verwenden, und muss ein streng strukturiertes JSON
liefern. Die Ergebnisse werden auf die Längen der bestehenden
Anzeigenfelder und gültige Unicode-Zeichen begrenzt. PNG-Daten müssen
1024 × 1024 Pixel groß sein, gültige CRCs und eine **vollständig
dekomprimierbare**, begrenzte RGB-/RGBA-Bildfläche einschließlich
gültiger Zeilenfilter besitzen. Ein nur syntaktisch plausibles PNG mit
verkürztem/überlangem Raster wird verweigert. Die CLI überschreibt
keine existierende Datei und erlaubt keine Symlink-Umleitung.
Providerfehler, Prompts und Zugangsdaten erscheinen nicht im Fehlerlog.

## Verifikation und verbleibende Grenzen

Synthetische Fake-Provider- und echte Loopback-HTTP-Tests prüfen
Positiv-/Negativfälle ohne echte OpenAI- oder Kleinanzeigen-Anfragen.
Ein installierter Endnutzer-Test mit tatsächlich berechtigtem
Providerkonto und einem vom Benutzer geprüften Entwurf ist für die
endgültige Abnahme separat notwendig. Die Generierung ist unabhängig vom PrivateWeb-Produktlauncher bereits
über den Offline-Produktstart erreichbar. Eine mögliche weitere
Integration in den PrivateWeb-Launcher darf erst nach der eigenständig
überprüften Sicherheitskorrektur (PR #127) erfolgen. Periodischer Sync und unabhängige
A6-Recovery bleiben eigenständige Lieferlücken.