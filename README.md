# Klinik Dienstplan

Lokale Desktop-App (Python/Tkinter, SQLite) zur Dienstplanung im Krankenhaus – dunkles, technisches Design mit Violett-Akzenten. Keine Cloud, kein Konto; alle Daten liegen in `data/dienstplan.db`.

## Starten

Voraussetzung: Python 3.10 oder neuer **mit Tkinter** (beim Windows-Installer von python.org enthalten). Es werden keine Fremdpakete benötigt.

```bash
python main.py
```

**Erster Start mit leerer Datenbank:** Es werden zehn fiktive **Demo-Personen** („Demo Alpha“ … „Demo Juliett“, markiert als *Testdaten*), drei Standardschichten (Früh 2–4, Spät 2–4, Nacht 1–2 Personen), gestaffelte **Testurlaube** im aktuellen und im Folgemonat und ein Beispielplan für den aktuellen Monat angelegt. Das passiert nur einmal und nur, wenn noch keine Mitarbeitenden existieren. Bestehende Pläne werden nie überschrieben.

**Bestehende Datenbank:** Beim ersten Start nach dem Update wird die Datenbank automatisch migriert. Vorher wird eine Sicherung unter `data/backups/` angelegt. Die Migration ist wiederholbar (Schema-Version über `PRAGMA user_version`, aktuell 5) und ändert keine Plandaten. Personen, deren Name mit `Demo ` beginnt, werden als Testdaten gekennzeichnet.

## Funktionen

### Dienstplan (Monat oder Woche)
- Umschalter **Monat | Woche**. Die Wochenansicht zeigt volle Namen in großen Chips (statt Initialen) und listet Abwesende mit Namen; die Auswahl wird gespeichert.
- Monat/Woche wechseln mit ‹ › oder Bild↑/Bild↓, „Heute“-Knopf.
- Tage aus Vor-/Folgemonat werden dezent dargestellt (Klick wechselt dorthin).
- Samstag/Sonntag dunkler, Feiertage (Baden-Württemberg) violett getönt und mit Namen – **alle Tage werden normal geplant und besetzt**.
- Jede Schicht als eigene Schichtkarte: Name, konfigurierte Uhrzeit, Besetzung („1/2“), Farbbalken. Unterbesetzte Schichten sind rot markiert, offene Plätze erscheinen als gestrichelte „+“-Chips.
- Mitarbeitende als Namenschips mit Initialen; Hover zeigt Name, Bereich, Schichtzeit, Monatsstunden und Wünsche.
- Urlaub (blau) und einzelne Abwesenheitstage (gelb) sind direkt in der Tageskarte sichtbar.
- Filter nach Bereich/Qualifikation und nach Schicht.
- Seitenpanel „Übersicht“ (Knopf **◧ Übersicht**, standardmäßig eingeklappt, damit der Kalender die volle Breite hat): offene Schichtplätze, Urlaubstage, Regelhinweise, Soll-Stunden, letzter Planlauf. Ist es eingeklappt, erscheint bei einer Auswahl unten eine kleine Infokarte mit Tauschpartnern bzw. Gründen.
- Alle Knöpfe, Eingabefelder, Karten und Umschalter sind abgerundet.
- Schichten sind nicht fest codiert – eigene Schichtmodelle erscheinen automatisch.

### Tauschen per Klick (schnellster Weg)
1. Namenschip anklicken → die Person leuchtet violett. Passende Tauschpartner leuchten mit, alle anderen Namen werden abgedunkelt.
2. Einen zweiten Namen anklicken **oder den ersten Namen mit der Maus auf den zweiten ziehen** → die App prüft den Tausch sofort (gleicher Tag oder zwei verschiedene Tage im Monat).
   - **Zulässig:** Unten im Kalender erscheint ein Hinweisbalken mit Countdown. Nach Ablauf der **Bedenkzeit (Standard 5 s)** wird gespeichert. Bis dahin: **Esc**, „Abbrechen“ oder erneuter Klick auf die erste Person bricht ab – nichts wird gespeichert. „Jetzt tauschen“ bzw. Enter speichert sofort.
   - **Nicht zulässig:** Es wird nichts geändert; der angeklickte Name wird rot markiert und das Seitenpanel nennt die Gründe.
3. Danach lässt sich der Tausch mit ↶ bzw. Strg+Z zurücknehmen (siehe „Rückgängig & Wiederholen“).

Die Bedenkzeit ist unter „Schichten & Regeln“ einstellbar (0–30 s; 0 = sofort speichern). Wird während eines laufenden Countdowns die App geschlossen, der Monat gewechselt oder ein Tag geöffnet, wird der Tausch **nicht** gespeichert.

### Tauschassistent (Seitenpanel)
- Zeigt nach dem ersten Klick geprüfte Vorschläge: Tausch am selben Tag, Tausch über zwei Tage (±7 Tage) und Übernahme ohne Gegenschicht.
- Vorschau „Vorher / Nachher“ für beide Personen; Probleme werden direkt angezeigt, alle Einzelprüfungen unter „Prüfdetails“.
- Vorschläge aus der Liste werden **erst nach „Tausch bestätigen“** gespeichert.
- Gibt es keinen gültigen Vorschlag, erklärt die App die häufigsten Hinderungsgründe; unter „Nicht möglich“ steht pro Person die Begründung.

Geprüft werden für beide Seiten: Schichteignung, Urlaub, einzelne Abwesenheitstage, doppelte Schicht am selben Tag, max. Arbeitstage pro Woche, aufeinanderfolgende Arbeitstage, Ruhezeit zwischen Schichten (aus den Uhrzeiten berechnet) und Mindestbesetzung nach dem Tausch. Vor dem Speichern wird erneut gegen den aktuellen Datenbankstand geprüft.

### Rückgängig & Wiederholen (mehrere Schritte)
Jede Planänderung ist ein Schritt im Verlauf: Tausch, Tagesplanung, „Monat planen“ (der ganze Planlauf als ein Schritt), Urlaub eintragen/entfernen (inkl. der dadurch entfernten Dienste) sowie Testurlaube anlegen/entfernen.

- **↶** (Strg+Z) nimmt den jüngsten Schritt zurück – mehrfach klicken geht beliebig viele Schritte zurück (bis zu 200 werden gespeichert, auch über einen Neustart hinweg).
- **↷** (Strg+Y oder Strg+Umschalt+Z) stellt zurückgenommene Schritte wieder her. Eine neue Änderung verwirft die „Wiederholen“-Liste.
- Der Tooltip am Pfeil zeigt, welcher Schritt als Nächstes zurückgenommen wird.
- Sicherheit: Ein Schritt wird nur zurückgesetzt, wenn die betroffenen Tage noch genau so aussehen wie direkt nach diesem Schritt. Sonst bricht die App mit Erklärung ab und überschreibt nichts.
- Nicht über den Verlauf: Löschen von Mitarbeitenden (dafür gibt es die automatische Sicherung) und Änderungen an Schichtmodellen oder Stammdaten.

### Tagesplanung
Klick auf einen Tag oder eine Schichtkarte öffnet die Tagesplanung: pro Person „Frei“ oder eine Schicht wählen, Besetzung wird live angezeigt. Vor dem Speichern zeigt die App alle Änderungen und Regelverstöße; Speichern trotz Verstoß ist nur nach ausdrücklicher Bestätigung möglich.

### Urlaub
Urlaub mit Datumsauswahl (Kalender-Popup, keine Freitext-Daten) eintragen, anzeigen, filtern und entfernen. Liegen im Zeitraum bereits Dienste, listet die App diese auf und entfernt sie nur nach Bestätigung. Überschneidungen werden gemeldet.

**Testurlaube:** „＋ Testurlaube anlegen“ plant für Demo-Personen gestaffelte Urlaube (3–5 Tage, nie zwei Demo-Personen gleichzeitig) im angezeigten und im Folgemonat. Vorher zeigt die App die Zeiträume und wie viele Dienste dadurch frei werden; gespeichert wird erst nach Bestätigung. Echte Mitarbeitende und Personen mit bereits eingetragenem Urlaub werden nie berührt. „Testurlaube entfernen“ löscht nur Einträge mit der Notiz „Testurlaub (Testdaten)“.

### Geschwindigkeit
- Ein Klick auf einen Namen färbt nur die betroffenen Chips um, statt den Kalender neu aufzubauen. Textbreiten werden zwischengespeichert.
- „Monat planen“ rechnet in einem Hintergrund-Thread mit eigener Datenbankverbindung; das Fenster bleibt bedienbar, und eine Fortschrittsanzeige erscheint.
- Das eingeklappte Seitenpanel wird erst beim Öffnen aufgebaut.

### Automatische Planung
„Monat planen“ bietet „Nur offene Plätze füllen“ (bestehende Einträge bleiben) oder „Komplett neu planen“ (mit zusätzlicher Bestätigung). Der Generator arbeitet in zwei Schritten:

1. **Mindestbesetzung** jeder Schicht an jedem Tag – auch Samstag, Sonntag und Feiertag.
2. **Soll-Stunden erfüllen:** Wer unter seinem Soll liegt, bekommt Zusatzdienste. Dabei arbeiten mehrere Personen in derselben Schicht, höchstens bis zur **Maximalbesetzung** der Schicht. Zusatzdienste werden gleichmäßig über den Monat verteilt (abschaltbar unter „Schichten & Regeln“).

Fairness: Wochenend-/Feiertags- und Nachtdienste werden proportional zu den Soll-Stunden verteilt (Teilzeit entsprechend weniger). Die App erzeugt mehrere Entwürfe und speichert den besten (wenigste offene Plätze → alle im Soll → fairste Verteilung). Berücksichtigt werden außerdem Urlaub, Abwesenheiten, Schichteignung, Max.-Tage, Folgetage, Ruhezeit und Wünsche („Frühdienst bevorzugt“ usw.; „Wochenende vermeiden“ wird stark gewichtet, aber nicht als harte Sperre behandelt, damit Wochenenden besetzbar bleiben). Nicht besetzbare Plätze und nicht erreichte Soll-Stunden werden mit Grund aufgelistet.

### Stunden & Fairness
Eigene Seite mit Soll/Ist-Stunden, Differenz und Status (erfüllt / unter / über Soll, Toleranz einstellbar, Standard ± 4 h) sowie Wochenend-/Feiertags- und Nachtdiensten im Vergleich zum fairen Anteil. **Soll** = Wochenstunden ÷ 7 je Kalendertag; Urlaubstage werden angerechnet. Das ist eine Annahme der App, keine tarifliche Zeiterfassung – Überstundenkonten und Übertrag in den Folgemonat werden nicht geführt.

### Mitarbeitende, Schichten & Hausregeln
- Mitarbeitende mit Bereich/Qualifikation, Wochenstunden, max. Arbeitstagen, Wünschen, einzelnen Abwesenheitstagen (per Datumsauswahl) und Schichteignung; aktivieren/deaktivieren.
- **Mitarbeitende löschen:** „Löschen“ zeigt vorher, wie viele zukünftige und vergangene Dienste sowie Urlaube mit entfernt werden. Gelöscht wird erst nach Bestätigung (bei echten Plandaten mit zweiter Rückfrage). Vorher legt die App automatisch eine Sicherung in `data/backups/` an. Wer Plandaten behalten will, nutzt „Aktiv / Inaktiv“.
- Schichtmodelle mit Uhrzeiten, Stunden, Farbe, Mindest- und Maximalbesetzung (0 = unbegrenzt; bestehende Schichten erhalten bei der Migration Mindestbesetzung + 2). Schichten über Mitternacht (z. B. 22:00–06:00) werden korrekt bis in den Folgetag gerechnet.
- Hausregeln: Mindestruhezeit (Standard 11 h), max. Arbeitstage am Stück (Standard 5), Bedenkzeit beim Klick-Tausch (Standard 5 s), Toleranz für Soll-Stunden (Standard ± 4 h) und ob Zusatzdienste geplant werden.
- CSV-Export des Monats (Semikolon, UTF-8 mit BOM, Spalten wie bisher).

## Grenzen der Regelprüfung

Die App prüft nur die oben genannten, in der App hinterlegten Regeln. **Nicht geprüft** werden u. a. Arbeitszeitgesetz (Höchstarbeitszeit, Pausen, Ausgleichszeiträume), Tarifverträge, Dienstvereinbarungen, Qualifikationsmix pro Schicht (z. B. Fachkraftquote) sowie tarifliche Zeitkonten (die Soll-Stunden der App sind eine vereinfachte Annahme). Die Standardwerte der Hausregeln sind keine Rechtsauskunft. Jeder automatisch erstellte Plan ist ein Vorschlag und muss vor der Freigabe fachlich geprüft werden.

## Projektaufbau

| Datei | Inhalt |
|---|---|
| `main.py` | Startpunkt; re-exportiert die Kernfunktionen (Kompatibilität) |
| `planner_core.py` | Datenbank, Migrationen, Demo-Daten, Regelprüfung, Generator, Tauschlogik – ohne GUI |
| `planner_theme.py` | Dunkles Theme mit abgerundeten Elementen, Datumsauswahl, Umschalter, Dialoge, Tooltips |
| `planner_ui.py` | Hauptfenster, Canvas-Kalender (Monat/Woche, Ziehen & Ablegen), Tauschassistent, Auswertung, Verwaltungsseiten |
| `tests/` | Unit-Tests |

Technische Entscheidung: Die App bleibt bei Tkinter (Standardbibliothek, einfache Paketierung). Das ttk-Theme „clam“ wird komplett umgefärbt; der Kalender wird auf einem Canvas gezeichnet, um abgerundete Karten, Leuchteffekte und Hover-Zustände ohne Zusatzbibliothek zu ermöglichen. Die Datumsauswahl ist eine eigene Komponente – `tkcalendar` o. ä. wird nicht benötigt. Für die abgerundeten Knöpfe und Felder erzeugt die App beim Start kantengeglättete PNG-Bilder (nur `zlib`/`struct` aus der Standardbibliothek) und nutzt sie als skalierbare ttk-Elemente – ebenfalls ohne Zusatzpaket.

## Tests

```bash
python -m unittest discover -s tests -v
python -m py_compile main.py
```

## Windows-EXE bauen

Im Projektordner `build_windows.bat` starten. Das Skript führt zuerst die Tests aus, installiert PyInstaller (`requirements-build.txt`) und erzeugt `dist\Klinik-Dienstplan\Klinik-Dienstplan.exe`. Den gesamten Ordner `dist\Klinik-Dienstplan` kopieren; die Datenbank liegt im Unterordner `data` neben der EXE. Eine vorhandene Datenbank wird nicht automatisch mitkopiert.
