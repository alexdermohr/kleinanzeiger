# Artikelbestände per CSV oder Excel importieren

Kleinanzeiger v0.1.7 – Windows 11. Diese Funktion erzeugt ausschließlich **lokale Artikelentwürfe**, niemals Anzeigen auf Kleinanzeigen.

## Schritt für Schritt

1. Die Tabellenkalkulation mit den Spalten `sku,title,description,category,price_eur` anlegen. Optional `images` (Bilddateinamen als Hinweise).
2. Unterstützte deutsche Überschriften: `Artikelnummer,Titel,Beschreibung,Kategorie,Preis,Bilder`. Das Mapping wird bei der Importvorschau angezeigt.
3. Als **CSV mit UTF-8 und Komma oder Semikolon** (auch deutscher Excel-CSV-Export) oder als **Excel XLSX** speichern. Eine Zeile = ein Artikel, maximal 5000 pro Datei, maximal 10 MB Dateigröße.
4. Im Windows-Kontrollfenster Kleinanzeiger gegebenenfalls erst **beenden**, dann **Artikel importieren** anklicken. Datei auswählen. Die Vorschau nennt neue, unveränderte und konfliktbehaftete Artikel sowie exemplarische Titel.
5. Erst nach der ausdrücklichen Bestätigung wird ein SQLite-Backup angelegt. Der Import prüft unveränderte SHA-256-Dateidaten, schreibt die neuen Entwürfe in einer einzigen Datenbanktransaktion und veröffentlicht nichts.
6. Kleinanzeiger erneut starten. Im Dashboard **Artikelentwürfe** öffnen und bei Bedarf nach Titel/SKU suchen, seitenweise blättern, eine Zeile auswählen und **In neue Anzeige** klicken. Der Entwurf füllt Kategorie, Titel, Beschreibung und Preis in das vorhandene Neuanzeigenformular; erst eine **weitere eigene Veröffentlichungshandlung** würde einen Plattform-Create auslösen.
7. **Entwürfe exportieren** im Windows-Fenster erstellt eine neue CSV; bei mehr als 5.000 Zeilen oder 10 MB entstehen zusätzlich Dateien mit den Endungen -part002.csv, -part003.csv usw. im selben Ordner. Jede Datei kann einzeln wieder importiert werden. Bereits vorhandene Dateien werden nicht überschrieben; bei Namenskonflikten wird kein unvollständiger Export behalten. Exportierte Textfelder sind gegen Tabellenformeln geschützt; beim erneuten Import einer unveränderten Kleinanzeiger-Exportdatei werden auch ursprünglich führende Apostrophe korrekt wiederhergestellt.

## Pflichtspalten

| Feld | Regel | Beispiel |
| --- | --- | --- |
| `sku` / `Artikelnummer` | Eindeutige lokale Artikelnummer, 1–80 Zeichen (ASCII-Buchstaben, Zahlen, Punkt, Unterstrich, Bindestrich). Kein Kleinanzeigen-Inserat-ID-Beweis. | `ART-001` |
| `title` / `Titel` | 1–65 UTF-16-Zeichen; nur bekannte Artikelangaben | `Keramikvase – blau` |
| `description` / `Beschreibung` | 1–4000 UTF-16-Zeichen | `Gebraucht, 28 cm hoch` |
| `category` / `Kategorie` | 2–6 Stufen mit `>` getrennt | `Haus & Garten > Dekoration` |
| `price_eur` / `Preis` | Ganze Euro, mindestens 1 und höchstens 99.999.999 | `29` oder `29,00` |
| `images` / `Bilder` (optional) | Bis zu zehn lokale Dateinamen, durch Semikolon getrennt | `front.jpg;detail.png` |

**Wichtig zu Bildern:** Die Namen sind in P1 Hinweise auf Dateien, keine hochgeladenen oder automatisch übergebenen Fotos. Beim späteren Anzeigen-Create müssen echte, zum Artikel passende Bilddateien ausgewählt werden. KI-Bilder sind nicht automatisch authentische Produktfotos.

## Duplikate und Fehler

- Derselbe SKU mit exakt gleichen Daten kann gefahrlos erneut importiert werden und wird als **unverändert** gezählt.
- Ein vorhandener SKU mit abweichendem Inhalt blockiert den gesamten Import. Es werden keine bestehenden Entwürfe überschrieben. Die Datei muss vor dem nächsten Versuch bereinigt werden.
- Doppelter SKU in einer Datei, ungültiger Preis, unzulässige Kategorie, fehlende Pflichtfelder oder Excel-Formeln werden abgewiesen; bei Fehlern keine teilweise Datenbankübernahme.
- Vorläufige Entwürfe und bestätigte Plattform-Inserate bleiben getrennt. Eine importierte Artikelnummer gibt **keine** Berechtigung, fremde oder unbekannte Anzeigen zu bearbeiten.
- Import/Export nur bei beendetem Kleinanzeiger; hier werden keine KI-Anbieter oder Plattformen angesprochen.

## Geltungsbereich

P1 schafft die Grundlage für spätere KI-Stapelaufträge (P2). Der P1-Import **veröffentlicht keine 1000 Anzeigen automatisch** und führt weder Bulk-Write noch kostenpflichtige KI-Aufrufe aus. Die Funktionen werden auf synthetischen CSV-/XLSX-Daten unter Windows geprüft; echte Kundenkonten bleiben unangetastet.