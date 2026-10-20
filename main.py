"""Klinik Dienstplan – Startpunkt der lokalen Desktop-App.

Aufbau:
    planner_core.py   Datenbank, Migrationen, Regeln, Generator, Tauschassistent (ohne GUI, testbar)
    planner_theme.py  Dunkles Theme und Tkinter-Bausteine (Datumsauswahl, Dialoge, Tooltips)
    planner_ui.py     Hauptfenster mit Monatskalender, Tauschassistent und Verwaltungsseiten

Die Kernfunktionen werden hier re-exportiert, damit bestehende Aufrufe wie
`main.bw_holidays()` oder `main.build_assignments()` weiter funktionieren.
"""
from planner_core import *  # noqa: F401,F403  (Kompatibilität)
from planner_core import Store, build_assignments, bw_holidays, easter_sunday  # noqa: F401


def main():
    import planner_ui  # erst hier laden, damit Kernlogik und Tests ohne Tkinter-Fenster nutzbar bleiben
    planner_ui.run()


if __name__ == '__main__':
    main()
