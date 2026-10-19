# Klinik Dienstplan

Lokale Desktop-App zur Krankenhaus-Personalplanung mit SQLite-Datenspeicherung.

## Starten

Python 3.10 oder neuer mit Tkinter:

```bash
python main.py
```

Beim ersten Start werden acht **fiktive Demo-Mitarbeitende** und ein Beispielplan für den aktuellen Monat angelegt. Die Demo-Namen sind Testdaten und können bearbeitet oder deaktiviert werden. Die Datenbank liegt lokal unter `data/dienstplan.db`.

## Funktionen

- Monatskalender mit einer Kalenderseite pro Monat
- Baden-Württembergische Feiertage sowie Samstage und Sonntage leicht abgedunkelt; alle Tage bleiben planbar
- Tagesansicht zum Bearbeiten der Zuordnung von Mitarbeitenden zu Schichten
- Mitarbeitende mit Vertragsstunden, maximalen Arbeitstagen, Qualifikationen, Wünschen und einzelnen Abwesenheitstagen
- Urlaubszeiträume eintragen und löschen; der automatische Entwurf berücksichtigt Urlaub
- Schichtmodelle mit Arbeitszeiten, Stunden und täglicher Mindestbesetzung bearbeiten
- Automatischen Monatsentwurf erstellen und als CSV exportieren
- Lokale Speicherung ohne Cloudkonto

## Windows-EXE bauen

Auf einem Windows-PC im Projektordner `build_windows.bat` starten. Das Skript erzeugt `dist\\Klinik-Dienstplan\\Klinik-Dienstplan.exe`. Den gesamten Ordner `dist\\Klinik-Dienstplan` auf einen USB-Stick kopieren; die Datenbank bleibt im Unterordner `data`.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Hinweis

Der automatische Plan ist ein Vorschlag. Vor einer Freigabe müssen Besetzung, Qualifikationen, Ruhezeiten sowie die Arbeitszeit-, Tarif- und Hausregeln geprüft werden. Feiertagsnamen folgen der gesetzlichen Feiertagsliste Baden-Württembergs. Die Anwendung ist noch kein geprüfter Ersatz für ein klinisches Dienstplanungssystem.
