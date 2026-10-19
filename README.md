# Klinik Dienstplan

Eine lokale Desktop-Anwendung für die Planung von Krankenhaus-Schichten. Die Daten bleiben in einer SQLite-Datei im Ordner `data` neben der Anwendung. Dadurch lässt sich der gesamte Ordner auf einen USB-Stick kopieren.

## Starten

Python 3.10 oder neuer mit Tkinter:

```bash
python main.py
```

## Windows-EXE bauen

Auf einem Windows-PC einmalig Python installieren und im Projektordner `build_windows.bat` starten. Das Skript erzeugt `dist\Klinik-Dienstplan\Klinik-Dienstplan.exe`. Den gesamten Ordner `dist\Klinik-Dienstplan` auf den USB-Stick kopieren; die Datenbank liegt im Unterordner `data`.

## Funktionen der ersten Version

- Mitarbeitende mit Bereich/Qualifikation, Vertragsstunden, maximalen Arbeitstagen, Schicht-Eignung, Wünschen und Abwesenheitstagen verwalten
- Schichtmodelle mit Zeiten, Stunden und Mindestbesetzung anlegen und ändern
- Monatsplan automatisch erzeugen; Wochenendwünsche, Abwesenheit, Schicht-Eignung und einfache Ruhezeitregeln fließen in die Zuteilung ein
- Einträge per Doppelklick ändern oder löschen
- CSV-Export für den gewählten Monat
- Lokale SQLite-Speicherung ohne Cloudkonto

## Hinweis

Der automatische Plan ist ein Planungsvorschlag. Die Klinik muss Besetzung, Qualifikationen, Ruhezeiten, Tarif-/Arbeitszeitregeln und Betriebsvereinbarungen vor einer Freigabe prüfen. Für echte Dienstplanung sollten die Regeln mit den Vorgaben des Hauses abgestimmt und der Generator entsprechend erweitert werden.
