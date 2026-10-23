"""Kernlogik des Klinik-Dienstplans (ohne GUI, daher direkt testbar).

Enthält Feiertage, Datenbankzugriff mit wiederholbaren Migrationen, die Regelprüfung,
den automatischen Monatsentwurf und den Tauschassistenten.

Wichtig: Die Regelprüfung nutzt ausschließlich die im Projekt hinterlegten Regeln
(Schichteignung, Urlaub, Abwesenheiten, max. Arbeitstage, Folgetage, Ruhezeit als
Hausregel, Mindestbesetzung). Sie ist KEINE vollständige Prüfung von Arbeitszeitrecht,
Tarif- oder Hausregeln.
"""
import calendar
import csv
import json
import os
import random
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache

APP_NAME = 'Klinik Dienstplan'
BASE = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, 'data')
DB_PATH = os.path.join(DATA_DIR, 'dienstplan.db')
MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober',
          'November', 'Dezember']
WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
WEEKDAYS_LONG = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']

SCHEMA_VERSION = 6
DEFAULT_SETTINGS = {
    # Hausregeln mit konservativen Standardwerten. Sie sind in der App änderbar und
    # ausdrücklich KEINE Aussage über gesetzliche Konformität.
    'min_rest_hours': '11',
    'max_consecutive_days': '5',
    # Bedenkzeit beim Klick-Tausch: so viele Sekunden kann ein Tausch noch abgebrochen werden.
    'swap_delay_seconds': '5',
    # Toleranz in Stunden, ab der Soll-Stunden als erfüllt gelten (Ist innerhalb ± Toleranz).
    'hours_tolerance': '4',
}
TEST_LEAVE_NOTE = 'Testurlaub (Testdaten)'
CONTEXT_PAD_DAYS = 14

# Regeln, die die App bewusst NICHT oder nur teilweise prüft. Wird überall angezeigt,
# wo geprüft wird, damit keine Vollständigkeit vorgetäuscht wird.
UNCHECKED_RULES = [
    'Arbeitszeitgesetz (Höchstarbeitszeit, Pausen, Ausgleichszeiträume) – nicht geprüft',
    'Tarifvertrag, Betriebsvereinbarungen und Hausregeln außerhalb der App-Einstellungen – nicht geprüft',
    'Qualifikationsmix pro Schicht (z. B. Fachkraftquote) – nicht modelliert',
    'Soll-/Iststunden – nur als Information angezeigt',
]


# ---------------------------------------------------------------------------
# Kalender
# ---------------------------------------------------------------------------

def easter_sunday(year):
    """Meeus/Jones/Butcher Gregorian Easter algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


@lru_cache(maxsize=64)
def _bw_holidays_cached(year):
    easter = easter_sunday(year)
    fixed = {
        date(year, 1, 1): 'Neujahr', date(year, 1, 6): 'Heilige Drei Könige',
        date(year, 5, 1): 'Tag der Arbeit', date(year, 10, 3): 'Tag der Deutschen Einheit',
        date(year, 11, 1): 'Allerheiligen', date(year, 12, 25): '1. Weihnachtstag',
        date(year, 12, 26): '2. Weihnachtstag',
    }
    offsets = [(-2, 'Karfreitag'), (1, 'Ostermontag'), (39, 'Christi Himmelfahrt'),
               (50, 'Pfingstmontag'), (60, 'Fronleichnam')]
    fixed.update({easter + timedelta(days=offset): name for offset, name in offsets})
    return tuple(sorted(fixed.items()))


def bw_holidays(year):
    """Gesetzliche Feiertage Baden-Württemberg {date: Name}."""
    return dict(_bw_holidays_cached(year))


def holiday_name(day):
    return bw_holidays(day.year).get(day)


def is_weekend(day):
    return day.weekday() >= 5


def month_bounds(month):
    start = month.replace(day=1)
    return start, date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])


def month_grid(month):
    """Wochen (Listen mit 7 Daten, Montag zuerst) inkl. Rand-Tagen aus Vor-/Folgemonat."""
    return calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)


def add_months(month, offset):
    index = month.year * 12 + (month.month - 1) + offset
    return date(index // 12, index % 12 + 1, 1)


def daterange(first, last):
    d = first
    while d <= last:
        yield d
        d += timedelta(days=1)


def fmt_day(day):
    return f'{day:%d.%m.}'


def fmt_hours(value):
    return f'{value:.1f}'.replace('.', ',').replace(',0', '')


# ---------------------------------------------------------------------------
# Datenbank, Migrationen, Demo-Daten
# ---------------------------------------------------------------------------

BASE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS employees(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT DEFAULT '',
    weekly_hours REAL DEFAULT 38.5, max_days INTEGER DEFAULT 5,
    active INTEGER DEFAULT 1, notes TEXT DEFAULT '', preferences TEXT DEFAULT '', unavailable TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS shifts(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
    hours REAL NOT NULL, color TEXT DEFAULT '#4f46e5', coverage INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS eligibility(
    employee_id INTEGER, shift_id INTEGER, PRIMARY KEY(employee_id,shift_id),
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE,
    FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS assignments(
    id INTEGER PRIMARY KEY, day TEXT NOT NULL, employee_id INTEGER NOT NULL, shift_id INTEGER,
    UNIQUE(day,employee_id), FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE,
    FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS leave_periods(
    id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, start_date TEXT NOT NULL,
    end_date TEXT NOT NULL, note TEXT DEFAULT '',
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''


def _columns(db, table):
    return {row[1] for row in db.execute(f'PRAGMA table_info({table})')}


def _migrate_to_v2(db):
    """Demo-Kennzeichnung, Tauschprotokoll (Undo), Hausregel-Einstellungen, Indizes.

    Jeder Schritt ist idempotent und kann gefahrlos mehrfach laufen."""
    if 'is_demo' not in _columns(db, 'employees'):
        db.execute('ALTER TABLE employees ADD COLUMN is_demo INTEGER DEFAULT 0')
    # Frühere Versionen haben Demo-Personen mit dem Präfix "Demo " angelegt.
    db.execute("UPDATE employees SET is_demo=1 WHERE name LIKE 'Demo %' AND COALESCE(is_demo,0)=0")
    db.execute('''CREATE TABLE IF NOT EXISTS swap_log(
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, description TEXT NOT NULL,
        before_json TEXT NOT NULL, after_json TEXT NOT NULL, undone INTEGER DEFAULT 0)''')
    for key, value in DEFAULT_SETTINGS.items():
        db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (key, value))
    db.execute('CREATE INDEX IF NOT EXISTS idx_assignments_day ON assignments(day)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_leave_employee ON leave_periods(employee_id,start_date,end_date)')


def _migrate_to_v3(db):
    """Einstellung für die Bedenkzeit beim Klick-Tausch (idempotent)."""
    db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',
               ('swap_delay_seconds', DEFAULT_SETTINGS['swap_delay_seconds']))


def _migrate_to_v4(db):
    """Maximalbesetzung je Schicht (mehrere Personen pro Schicht) und Stunden-Toleranz (idempotent)."""
    if 'max_staff' not in _columns(db, 'shifts'):
        db.execute('ALTER TABLE shifts ADD COLUMN max_staff INTEGER DEFAULT 0')
        # Startwert: Mindestbesetzung + 2 – in „Schichten & Regeln“ änderbar, 0 = keine Obergrenze.
        db.execute('UPDATE shifts SET max_staff = COALESCE(coverage, 1) + 2')
    db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',
               ('hours_tolerance', DEFAULT_SETTINGS['hours_tolerance']))


def _migrate_to_v5(db):
    """Allgemeines Änderungsprotokoll für mehrstufiges Rückgängig/Wiederholen (idempotent).

    state: 0 = rückgängig machbar, 1 = rückgängig gemacht (wiederholbar), 2 = verworfen,
    3 = gesperrt (betroffene Person wurde gelöscht)."""
    db.execute('''CREATE TABLE IF NOT EXISTS change_log(
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL, description TEXT NOT NULL,
        changes_json TEXT NOT NULL, extra_json TEXT NOT NULL DEFAULT '{}', state INTEGER NOT NULL DEFAULT 0)''')
    has_swaps = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='swap_log'").fetchone()[0]
    if has_swaps and not db.execute('SELECT COUNT(*) FROM change_log').fetchone()[0]:
        for row in db.execute('SELECT * FROM swap_log ORDER BY id').fetchall():
            before, after = json.loads(row[3]), json.loads(row[4])
            changes = [[b[0], b[1], b[2], a[2]] for b, a in zip(before, after)]
            db.execute('INSERT INTO change_log(created_at,kind,description,changes_json,state) VALUES(?,?,?,?,?)',
                       (row[1], 'swap', row[2], json.dumps(changes), 0 if row[5] == 0 else 2))


def _migrate_to_v6(db):
    """Persönliche feste Wochenschichten und konfigurierbare Schichtfolgeregeln."""
    db.executescript('''
    CREATE TABLE IF NOT EXISTS employee_shift_patterns(
        employee_id INTEGER NOT NULL, weekday INTEGER NOT NULL, shift_id INTEGER NOT NULL,
        PRIMARY KEY(employee_id, weekday),
        FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE,
        FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS shift_transition_rules(
        id INTEGER PRIMARY KEY, after_shift_id INTEGER NOT NULL, blocked_shift_id INTEGER NOT NULL,
        days_after INTEGER NOT NULL DEFAULT 1,
        UNIQUE(after_shift_id, blocked_shift_id),
        FOREIGN KEY(after_shift_id) REFERENCES shifts(id) ON DELETE CASCADE,
        FOREIGN KEY(blocked_shift_id) REFERENCES shifts(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS shift_streak_rules(
        shift_id INTEGER PRIMARY KEY, max_consecutive INTEGER NOT NULL DEFAULT 4,
        rest_days INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(shift_id) REFERENCES shifts(id) ON DELETE CASCADE);
    ''')
    _seed_default_shift_rules(db)


def _seed_default_shift_rules(db):
    """Legt die zwei gewünschten Beispielregeln bei Standard-Schichten einmalig an."""
    seeded = db.execute("SELECT 1 FROM settings WHERE key='default_shift_rules_seeded'").fetchone()
    if seeded:
        return
    names = {str(row['name']).strip().lower(): row['id'] for row in db.execute('SELECT id,name FROM shifts')}
    night = next((names[n] for n in ('nachtdienst', 'nacht') if n in names), None)
    early = next((names[n] for n in ('frühdienst', 'fruehdienst', 'früh', 'frueh') if n in names), None)
    late = next((names[n] for n in ('spätdienst', 'spaetdienst', 'spät', 'spaet') if n in names), None)
    if not any((night, early, late)):
        return  # bei einer leeren Datenbank werden die Standardschichten erst nach der Migration angelegt
    if night and early:
        db.execute('INSERT OR IGNORE INTO shift_transition_rules(after_shift_id,blocked_shift_id,days_after) '
                   'VALUES(?,?,1)', (night, early))
    if late:
        db.execute('INSERT OR IGNORE INTO shift_streak_rules(shift_id,max_consecutive,rest_days) VALUES(?,4,2)',
                   (late,))
    db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('default_shift_rules_seeded','1')")


MIGRATIONS = {2: _migrate_to_v2, 3: _migrate_to_v3, 4: _migrate_to_v4, 5: _migrate_to_v5,
              6: _migrate_to_v6}

DEMO_STAFF = [
    # Fiktive Kennungen statt Namen – eindeutig als Testdaten erkennbar.
    ('Demo Alpha', 'Pflegefachkraft', 38.5, 5, 'Frühdienst bevorzugt', ('früh', 'spät', 'nacht')),
    ('Demo Bravo', 'Pflegefachkraft', 38.5, 5, 'Spätdienst bevorzugt', ('früh', 'spät', 'nacht')),
    ('Demo Charlie', 'Pflegefachkraft', 30.0, 4, 'Wochenende vermeiden', ('früh', 'spät')),
    ('Demo Delta', 'Pflegefachkraft', 38.5, 5, 'Nachtdienst bevorzugt', ('früh', 'spät', 'nacht')),
    ('Demo Echo', 'Stationsleitung', 38.5, 5, 'Frühdienst bevorzugt', ('früh', 'spät')),
    ('Demo Foxtrot', 'Pflegefachkraft', 32.0, 4, '', ('früh', 'spät', 'nacht')),
    ('Demo Golf', 'Pflegefachkraft', 38.5, 5, '', ('früh', 'spät', 'nacht')),
    ('Demo Hotel', 'Pflegehilfe', 30.0, 4, 'Spätdienst bevorzugt', ('früh', 'spät')),
    ('Demo India', 'Pflegehilfe', 38.5, 5, '', ('früh', 'spät', 'nacht')),
    ('Demo Juliett', 'Pflegefachkraft', 38.5, 5, 'Nachtdienst bevorzugt', ('spät', 'nacht')),
]
DEFAULT_SHIFTS = [  # Name, Beginn, Ende, Stunden, Farbe, Mindestbesetzung, Maximalbesetzung
    ('Frühdienst', '06:00', '14:00', 8, '#f5b544', 2, 4),
    ('Spätdienst', '14:00', '22:00', 8, '#8b7cf6', 2, 4),
    ('Nachtdienst', '22:00', '06:00', 8, '#38bdf8', 1, 2),
]


class Store:
    def __init__(self, path=None):
        self.path = path or DB_PATH
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        existed = os.path.exists(self.path) and os.path.getsize(self.path) > 0
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.backup_path = None
        self.migrate(existed)
        if self.db.execute('SELECT COUNT(*) FROM shifts').fetchone()[0] == 0:
            with self.db:
                self.db.executemany('INSERT INTO shifts(name,start,end,hours,color,coverage,max_staff) '
                                    'VALUES(?,?,?,?,?,?,?)', DEFAULT_SHIFTS)
        with self.db:
            _seed_default_shift_rules(self.db)
        self.demo_seeded_now = self.seed_demo_staff()

    # -- Migrationen -------------------------------------------------------
    def schema_version(self):
        return self.db.execute('PRAGMA user_version').fetchone()[0]

    def migrate(self, existed=True):
        version = self.schema_version()
        if version >= SCHEMA_VERSION:
            self.db.executescript(BASE_SCHEMA)
            return
        has_tables = self.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
        if existed and has_tables:
            self.backup_path = self._backup(f'vor_migration_v{SCHEMA_VERSION}')
        self.db.executescript(BASE_SCHEMA)
        for target in range(version + 1, SCHEMA_VERSION + 1):
            step = MIGRATIONS.get(target)
            with self.db:
                if step:
                    step(self.db)
                self.db.execute(f'PRAGMA user_version={target}')

    def _backup(self, label):
        folder = os.path.join(os.path.dirname(os.path.abspath(self.path)), 'backups')
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        target = os.path.join(folder, f'{os.path.splitext(os.path.basename(self.path))[0]}_{label}_{stamp}.db')
        counter = 1
        while os.path.exists(target):
            target = target.replace('.db', f'_{counter}.db')
            counter += 1
        dest = sqlite3.connect(target)
        try:
            self.db.backup(dest)
        finally:
            dest.close()
        return target

    # -- Hilfen ------------------------------------------------------------
    def all(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def run(self, sql, args=()):
        with self.db:
            self.db.execute(sql, args)

    def setting(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        if row:
            return row[0]
        return DEFAULT_SETTINGS.get(key, default)

    def set_setting(self, key, value):
        self.run('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (key, str(value)))

    def seed_demo_staff(self):
        """Legt Demo-Personen genau einmal an – und nur, wenn noch niemand existiert."""
        if self.db.execute("SELECT value FROM settings WHERE key='demo_staff_seeded'").fetchone():
            return False
        seeded = False
        with self.db:
            if self.db.execute('SELECT COUNT(*) FROM employees').fetchone()[0] == 0:
                shifts = self.all('SELECT id,name FROM shifts')
                for name, role, hours, max_days, prefs, kinds in DEMO_STAFF:
                    cur = self.db.execute(
                        'INSERT INTO employees(name,role,weekly_hours,max_days,preferences,notes,is_demo) '
                        'VALUES(?,?,?,?,?,?,1)',
                        (name, role, hours, max_days, prefs, 'Testdaten – fiktive Demo-Person'))
                    matching = [s['id'] for s in shifts if any(k in s['name'].lower() for k in kinds)]
                    ids = matching or [s['id'] for s in shifts]
                    self.db.executemany('INSERT INTO eligibility(employee_id,shift_id) VALUES(?,?)',
                                        [(cur.lastrowid, sid) for sid in ids])
                seeded = True
            self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('demo_staff_seeded','1')")
        if seeded:
            # Testurlaube im aktuellen und im Folgemonat, damit Urlaub sofort sichtbar ist.
            this_month = date.today().replace(day=1)
            add_test_leaves(self, [this_month, add_months(this_month, 1)], log=False)
        return seeded

    def close(self):
        self.db.close()


# ---------------------------------------------------------------------------
# Datenmodell für die Planung
# ---------------------------------------------------------------------------

@lru_cache(maxsize=256)
def _parse_hm(value):
    return datetime.strptime(value, '%H:%M').time()


@dataclass(frozen=True)
class Shift:
    id: int
    name: str
    start: str
    end: str
    hours: float
    color: str
    coverage: int
    max_staff: int = 0  # 0 = keine Obergrenze

    @property
    def cap(self):
        return self.max_staff if self.max_staff and self.max_staff > 0 else None

    @property
    def is_night(self):
        return self.end <= self.start or self.start >= '20:00'

    def interval(self, day):
        begin = datetime.combine(day, _parse_hm(self.start))
        finish = datetime.combine(day, _parse_hm(self.end))
        if finish <= begin:
            finish += timedelta(days=1)
        return begin, finish

    @property
    def short(self):
        short = re.sub(r'(?i)dienst$', '', self.name).strip()
        return short or self.name

    @property
    def time_label(self):
        return f'{self.start}–{self.end}'

    @property
    def compact_time(self):
        def short(t):
            return t[:2] if t.endswith(':00') else t
        return f'{short(self.start)}–{short(self.end)}'


@dataclass(frozen=True)
class Employee:
    id: int
    name: str
    role: str
    weekly_hours: float
    max_days: int
    active: bool
    preferences: str
    unavailable: frozenset
    is_demo: bool

    @property
    def display(self):
        return self.name

    @property
    def pref_list(self):
        return [p.strip().lower() for p in (self.preferences or '').split(',') if p.strip()]

    @property
    def avoids_weekend(self):
        return any('wochenende vermeiden' in p for p in self.pref_list)


def initials(name):
    clean = re.sub(r'^(demo|test)\s+', '', name.strip(), flags=re.I)
    words = [w for w in re.split(r'[\s\-_.]+', clean) if w]
    if not words:
        return '?'
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return words[0][:2].upper()


def chip_labels(employees):
    """Eindeutige, kurze Kennzeichen (Initialen) für Namenschips."""
    labels, taken = {}, set()
    for e in sorted(employees, key=lambda x: x.id):
        base = initials(e.name)
        label = base
        if label in taken:
            clean = re.sub(r'^(demo|test)\s+', '', e.name.strip(), flags=re.I).replace(' ', '')
            label = clean[:3].upper() or base
        n = 2
        while label in taken:
            label = f'{base}{n}'
            n += 1
        taken.add(label)
        labels[e.id] = label
    return labels


@dataclass
class Check:
    rule: str
    status: str  # 'ok' | 'info' | 'warn' | 'block'
    message: str

    @property
    def blocking(self):
        return self.status == 'block'


class PlanContext:
    """Schnappschuss der Planungsdaten eines Zeitraums inkl. Puffer für Regelprüfungen."""

    def __init__(self, store, start, end, pad=CONTEXT_PAD_DAYS):
        self.store = store
        self.start, self.end = start, end
        self.lo, self.hi = start - timedelta(days=pad), end + timedelta(days=pad)
        self.min_rest_hours = float(store.setting('min_rest_hours') or 0)
        self.max_consecutive = int(float(store.setting('max_consecutive_days') or 0))
        self.hours_tolerance = float(store.setting('hours_tolerance') or 4)
        self.shift_list = [Shift(r['id'], r['name'], r['start'], r['end'], float(r['hours']), r['color'] or '#8b5cf6',
                                 int(r['coverage'] or 0),
                                 int(r['max_staff'] or 0) if 'max_staff' in r.keys() else 0)
                           for r in store.all('SELECT * FROM shifts ORDER BY start,name')]
        self.shifts = {s.id: s for s in self.shift_list}
        self.employees = {}
        for r in store.all('SELECT * FROM employees ORDER BY name'):
            keys = r.keys()
            unavailable = frozenset(x.strip() for x in (r['unavailable'] or '').split(',') if x.strip())
            self.employees[r['id']] = Employee(r['id'], r['name'], r['role'] or '', float(r['weekly_hours'] or 0),
                                               int(r['max_days'] or 0), bool(r['active']), r['preferences'] or '',
                                               unavailable, bool(r['is_demo']) if 'is_demo' in keys else False)
        self.active = [e for e in self.employees.values() if e.active]
        self.elig = {eid: set() for eid in self.employees}
        for r in store.all('SELECT employee_id,shift_id FROM eligibility'):
            self.elig.setdefault(r['employee_id'], set()).add(r['shift_id'])
        self.patterns = {(r['employee_id'], int(r['weekday'])): r['shift_id']
                         for r in store.all('SELECT employee_id,weekday,shift_id FROM employee_shift_patterns')}
        self.transition_rules = [dict(r) for r in store.all(
            'SELECT after_shift_id,blocked_shift_id,days_after FROM shift_transition_rules')]
        self.streak_rules = {r['shift_id']: (int(r['max_consecutive']), int(r['rest_days']))
                             for r in store.all('SELECT shift_id,max_consecutive,rest_days FROM shift_streak_rules')}
        self.plan = {}
        for r in store.all('SELECT day,employee_id,shift_id FROM assignments WHERE day BETWEEN ? AND ? '
                           'AND shift_id IS NOT NULL', (self.lo.isoformat(), self.hi.isoformat())):
            if r['shift_id'] in self.shifts:
                self.plan[(r['employee_id'], r['day'])] = r['shift_id']
        self.leave_periods = [dict(r) for r in store.all(
            'SELECT * FROM leave_periods WHERE start_date<=? AND end_date>=? ORDER BY start_date',
            (self.hi.isoformat(), self.lo.isoformat()))]
        self.leave_days = {}
        for lp in self.leave_periods:
            first = max(self.lo, date.fromisoformat(lp['start_date']))
            last = min(self.hi, date.fromisoformat(lp['end_date']))
            for d in daterange(first, last):
                self.leave_days.setdefault(lp['employee_id'], set()).add(d.isoformat())
        self.labels = chip_labels(self.employees.values())

    @classmethod
    def for_month(cls, store, month):
        start, end = month_bounds(month)
        return cls(store, start, end)

    # -- Abfragen ----------------------------------------------------------
    def on_leave(self, eid, day):
        return day.isoformat() in self.leave_days.get(eid, ())

    def absent(self, eid, day):
        emp = self.employees.get(eid)
        return bool(emp and day.isoformat() in emp.unavailable)

    def eligible(self, eid, sid):
        allowed = self.elig.get(eid) or set()
        return not allowed or sid in allowed  # leer = für alle Schichten einsetzbar (bisheriges Verhalten)

    def shift_of(self, eid, day, overlay=None):
        key = (eid, day.isoformat())
        if overlay is not None and key in overlay:
            return overlay[key]
        return self.plan.get(key)

    def staff_on(self, day, sid, overlay=None):
        ds = day.isoformat()
        ids = {eid for (eid, d), s in self.plan.items() if d == ds and s == sid}
        if overlay:
            for (eid, d), s in overlay.items():
                if d != ds:
                    continue
                if s == sid:
                    ids.add(eid)
                else:
                    ids.discard(eid)
        return sorted(ids, key=lambda e: self.employees[e].name.lower() if e in self.employees else '')

    def month_hours(self, eid, overlay=None):
        total = 0.0
        for d in daterange(self.start, self.end):
            sid = self.shift_of(eid, d, overlay)
            if sid:
                total += self.shifts[sid].hours
        return total

    def leave_days_in_range(self, eid):
        lo, hi = self.start.isoformat(), self.end.isoformat()
        return sum(1 for d in self.leave_days.get(eid, ()) if lo <= d <= hi)

    def target_hours(self, eid):
        """Soll-Stunden im Zeitraum: Wochenstunden / 7 je Kalendertag; Urlaubstage werden angerechnet
        (reduzieren das Soll). Annahme der App – keine tarifliche Berechnung."""
        emp = self.employees[eid]
        days = (self.end - self.start).days + 1 - self.leave_days_in_range(eid)
        return round(max(0.0, emp.weekly_hours / 7 * days), 1)

    def leave_on(self, day):
        ds = day.isoformat()
        return [eid for eid, days in self.leave_days.items() if ds in days]

    # -- Regelprüfung ------------------------------------------------------
    def check_assignment(self, eid, day, sid, overlay=None, include_ok=True):
        """Prüft, ob Person `eid` am Tag `day` die Schicht `sid` arbeiten darf.

        `overlay` enthält hypothetische Änderungen {(eid, iso): sid|None}; die Schicht selbst
        wird für die Prüfung automatisch eingesetzt."""
        emp = self.employees[eid]
        shift = self.shifts[sid]
        ov = dict(overlay or {})
        ov[(eid, day.isoformat())] = sid
        checks = []

        def add(rule, ok, message_ok, message_fail, status_fail='block'):
            if ok:
                if include_ok:
                    checks.append(Check(rule, 'ok', message_ok))
            else:
                checks.append(Check(rule, status_fail, message_fail))

        add('Schichteignung', self.eligible(eid, sid), f'{emp.name} ist für {shift.name} eingetragen.',
            f'{emp.name} ist nicht für {shift.name} als einsetzbar eingetragen.')
        fixed_sid = self.patterns.get((eid, day.weekday()))
        if fixed_sid:
            fixed = self.shifts.get(fixed_sid)
            add('Feste Wochenschicht', fixed_sid == sid,
                f'{emp.name} hat {shift.name} als feste Wochenschicht eingetragen.',
                f'{emp.name} hat an diesem Wochentag fest {fixed.name if fixed else "eine andere Schicht"} '
                f'eingetragen, nicht {shift.name}.')
        add('Urlaub', not self.on_leave(eid, day), f'Kein Urlaub am {fmt_day(day)}.',
            f'{emp.name} hat am {fmt_day(day)} Urlaub.')
        add('Abwesenheit', not self.absent(eid, day), f'Kein Abwesenheitstag am {fmt_day(day)}.',
            f'{emp.name} ist am {fmt_day(day)} als abwesend eingetragen.')
        if not emp.active:
            checks.append(Check('Status', 'block', f'{emp.name} ist deaktiviert.'))

        # Max. Arbeitstage pro Kalenderwoche (Mo–So)
        week_start = day - timedelta(days=day.weekday())
        worked = sum(1 for n in range(7) if self.shift_of(eid, week_start + timedelta(days=n), ov))
        limit = emp.max_days or 7
        kw = day.isocalendar()[1]
        add('Max. Arbeitstage', worked <= limit, f'KW {kw}: {worked} von max. {limit} Tagen.',
            f'{emp.name}: KW {kw} hätte {worked} Arbeitstage, erlaubt sind {limit}.')

        # Aufeinanderfolgende Arbeitstage
        if self.max_consecutive > 0:
            first = last = day
            while first - timedelta(days=1) >= self.lo and self.shift_of(eid, first - timedelta(days=1), ov):
                first -= timedelta(days=1)
            while last + timedelta(days=1) <= self.hi and self.shift_of(eid, last + timedelta(days=1), ov):
                last += timedelta(days=1)
            run = (last - first).days + 1
            add('Folgetage', run <= self.max_consecutive,
                f'{run} Arbeitstag(e) am Stück (Grenze {self.max_consecutive}).',
                f'{emp.name}: {run} Arbeitstage am Stück ({fmt_day(first)}–{fmt_day(last)}), '
                f'Hausregel max. {self.max_consecutive}.')

        # Ruhezeit zwischen Schichten (Hausregel, generisch über Uhrzeiten)
        if self.min_rest_hours > 0:
            begin, finish = shift.interval(day)
            rest_ok = True
            prev_day, next_day = day - timedelta(days=1), day + timedelta(days=1)
            prev_sid = self.shift_of(eid, prev_day, ov)
            if prev_sid:
                prev = self.shifts[prev_sid]
                gap = (begin - prev.interval(prev_day)[1]).total_seconds() / 3600
                if gap < self.min_rest_hours:
                    rest_ok = False
                    checks.append(Check('Ruhezeit', 'block', self._rest_msg(emp, prev, prev_day, shift, day, gap)))
            next_sid = self.shift_of(eid, next_day, ov)
            if next_sid:
                nxt = self.shifts[next_sid]
                gap = (nxt.interval(next_day)[0] - finish).total_seconds() / 3600
                if gap < self.min_rest_hours:
                    rest_ok = False
                    checks.append(Check('Ruhezeit', 'block', self._rest_msg(emp, shift, day, nxt, next_day, gap)))
            if rest_ok and include_ok:
                checks.append(Check('Ruhezeit', 'ok', f'Mind. {fmt_hours(self.min_rest_hours)} h Ruhe vor/nach der Schicht.'))

        if is_weekend(day) and emp.avoids_weekend:
            checks.append(Check('Wunsch', 'warn', f'{emp.name} möchte Wochenenden vermeiden.'))
        checks.extend(self._configured_shift_checks(eid, day, ov))
        return checks

    def _rest_msg(self, emp, first, first_day, second, second_day, gap):
        return (f'{emp.name}: nur {fmt_hours(max(gap, 0))} h Ruhe zwischen {first.name} ({fmt_day(first_day)}) '
                f'und {second.name} ({fmt_day(second_day)}), Hausregel {fmt_hours(self.min_rest_hours)} h.')

    def _configured_shift_checks(self, eid, day, overlay):
        """Prüft Regeln zu Schichtfolgen und Serien für den hypothetischen Plan."""
        emp = self.employees[eid]
        checks = []
        assigned = {}
        for (person, iso), shift_id in self.plan.items():
            if person == eid and shift_id:
                assigned[date.fromisoformat(iso)] = shift_id
        for (person, iso), shift_id in overlay.items():
            if person == eid:
                target = date.fromisoformat(iso)
                if shift_id:
                    assigned[target] = shift_id
                else:
                    assigned.pop(target, None)

        for rule in self.transition_rules:
            after_sid = rule['after_shift_id']
            blocked_sid = rule['blocked_shift_id']
            days_after = int(rule['days_after'])
            if after_sid not in self.shifts or blocked_sid not in self.shifts:
                continue
            for offset in range(1, days_after + 1):
                previous = day - timedelta(days=offset)
                following = day + timedelta(days=offset)
                if assigned.get(previous) == after_sid and assigned.get(day) == blocked_sid:
                    checks.append(Check('Schichtfolge', 'block',
                                        f'{emp.name}: {self.shifts[blocked_sid].name} ist innerhalb von '
                                        f'{days_after} Tag(en) nach {self.shifts[after_sid].name} gesperrt.'))
                    break
                if assigned.get(day) == after_sid and assigned.get(following) == blocked_sid:
                    checks.append(Check('Schichtfolge', 'block',
                                        f'{emp.name}: nach {self.shifts[after_sid].name} darf innerhalb von '
                                        f'{days_after} Tag(en) kein {self.shifts[blocked_sid].name} folgen.'))
                    break

        for streak_sid, (maximum, rest_days) in self.streak_rules.items():
            if streak_sid not in self.shifts or maximum < 1:
                continue
            dates = sorted(d for d, value in assigned.items() if value == streak_sid)
            runs = []
            for current in dates:
                if not runs or current != runs[-1][-1] + timedelta(days=1):
                    runs.append([current])
                else:
                    runs[-1].append(current)
            for run in runs:
                if len(run) > maximum and day in run:
                    checks.append(Check('Schichtserie', 'block',
                                        f'{emp.name}: maximal {maximum} aufeinanderfolgende Dienste '
                                        f'„{self.shifts[streak_sid].name}“ erlaubt.'))
                    break
                if len(run) >= maximum and rest_days:
                    rest = {run[-1] + timedelta(days=n) for n in range(1, rest_days + 1)}
                    if day in rest and assigned.get(day):
                        checks.append(Check('Ruhetage nach Schichtserie', 'block',
                                            f'{emp.name}: nach {maximum} aufeinanderfolgenden Diensten '
                                            f'„{self.shifts[streak_sid].name}“ sind {rest_days} volle Ruhetage nötig.'))
                        break
        return checks

    def coverage_checks(self, pairs, overlay):
        checks = []
        for ds, sid in sorted(p for p in pairs if p[1]):
            if sid not in self.shifts:
                continue
            shift, day = self.shifts[sid], date.fromisoformat(ds)
            before = len(self.staff_on(day, sid))
            after = len(self.staff_on(day, sid, overlay))
            label = f'{shift.name} {fmt_day(day)}: {before}/{shift.coverage} → {after}/{shift.coverage}'
            if after >= shift.coverage or after >= before:
                status = 'ok' if after >= shift.coverage else 'warn'
                msg = label + ('' if status == 'ok' else ' (war bereits unterbesetzt)')
            else:
                status, msg = 'block', label + ' – Mindestbesetzung würde unterschritten.'
            checks.append(Check('Mindestbesetzung', status, msg))
            if shift.cap and after > shift.cap and after > before:
                checks.append(Check('Maximalbesetzung', 'warn',
                                    f'{shift.name} {fmt_day(day)}: {after} Personen, Maximalbesetzung {shift.cap}.'))
        return checks


# ---------------------------------------------------------------------------
# Tauschassistent
# ---------------------------------------------------------------------------

KIND_LABELS = {
    'same_day': 'Tausch am selben Tag',
    'cross_day': 'Tausch über zwei Tage',
    'takeover': 'Übernahme (ohne Gegenschicht)',
}


@dataclass
class SwapOption:
    kind: str
    a_id: int
    b_id: int
    a_day: date
    a_shift: int
    b_day: date = None
    b_shift: int = None
    changes: list = field(default_factory=list)  # [(eid, iso, alt_sid, neu_sid)]
    checks_a: list = field(default_factory=list)
    checks_b: list = field(default_factory=list)
    checks_cov: list = field(default_factory=list)

    @property
    def all_checks(self):
        return self.checks_a + self.checks_b + self.checks_cov

    @property
    def blockers(self):
        return [c for c in self.all_checks if c.blocking]

    @property
    def warnings(self):
        return [c for c in self.all_checks if c.status == 'warn']

    @property
    def valid(self):
        return not self.blockers

    @property
    def overlay(self):
        return {(eid, iso): new for eid, iso, _old, new in self.changes}

    def sort_key(self):
        order = {'same_day': 0, 'cross_day': 1, 'takeover': 2}[self.kind]
        distance = abs((self.b_day - self.a_day).days) if self.b_day else 0
        return (not self.valid, len(self.blockers), order, len(self.warnings), distance)

    def before_after(self, ctx, eid):
        """[(Datum, vorher, nachher)] für die vom Tausch betroffenen Tage einer Person."""
        days = sorted({date.fromisoformat(iso) for _e, iso, _o, _n in self.changes})
        rows = []
        for d in days:
            old = ctx.shift_of(eid, d)
            new = ctx.shift_of(eid, d, self.overlay)
            rows.append((d, _shift_label(ctx, old, eid, d), _shift_label(ctx, new, eid, d)))
        return rows

    def headline(self, ctx):
        a, b = ctx.employees[self.a_id], ctx.employees[self.b_id]
        sa = ctx.shifts[self.a_shift]
        if self.kind == 'takeover':
            return f'{b.name} übernimmt {sa.name} am {fmt_day(self.a_day)}'
        sb = ctx.shifts[self.b_shift]
        return f'{a.name}: {sa.short} {fmt_day(self.a_day)} ⇄ {b.name}: {sb.short} {fmt_day(self.b_day)}'


def _shift_label(ctx, sid, eid, day):
    if sid:
        s = ctx.shifts[sid]
        return f'{s.short} {s.compact_time}'
    if ctx.on_leave(eid, day):
        return 'Urlaub'
    return 'Frei'


def evaluate_swap(ctx, kind, a_id, a_day, b_id, b_day=None):
    a_sid = ctx.shift_of(a_id, a_day)
    if not a_sid:
        raise ValueError('Die ausgewählte Person hat an diesem Tag keine Schicht.')
    opt = SwapOption(kind, a_id, b_id, a_day, a_sid)
    a_iso = a_day.isoformat()
    pre = []  # Prüfungen, die vor den Regelprüfungen feststehen (Doppelbelegung)
    if kind == 'same_day':
        b_sid = ctx.shift_of(b_id, a_day)
        if not b_sid or b_sid == a_sid:
            raise ValueError('Kein sinnvoller Tausch am selben Tag.')
        opt.b_day, opt.b_shift = a_day, b_sid
        opt.changes = [(a_id, a_iso, a_sid, b_sid), (b_id, a_iso, b_sid, a_sid)]
    elif kind == 'takeover':
        b_existing = ctx.shift_of(b_id, a_day)
        if b_existing:
            pre.append(('b', Check('Doppelte Schicht', 'block',
                                   f'{ctx.employees[b_id].name} arbeitet am {fmt_day(a_day)} bereits '
                                   f'{ctx.shifts[b_existing].name}.')))
        opt.changes = [(a_id, a_iso, a_sid, None), (b_id, a_iso, b_existing, a_sid)]
    elif kind == 'cross_day':
        if b_day is None or b_day == a_day:
            raise ValueError('Für einen Tausch über Tage wird ein anderes Datum benötigt.')
        b_iso = b_day.isoformat()
        b_sid = ctx.shift_of(b_id, b_day)
        if not b_sid:
            raise ValueError('Tauschpartner hat an diesem Datum keine Schicht.')
        opt.b_day, opt.b_shift = b_day, b_sid
        a_on_b = ctx.shift_of(a_id, b_day)
        b_on_a = ctx.shift_of(b_id, a_day)
        if a_on_b:
            pre.append(('a', Check('Doppelte Schicht', 'block',
                                   f'{ctx.employees[a_id].name} arbeitet am {fmt_day(b_day)} bereits '
                                   f'{ctx.shifts[a_on_b].name}.')))
        if b_on_a:
            pre.append(('b', Check('Doppelte Schicht', 'block',
                                   f'{ctx.employees[b_id].name} arbeitet am {fmt_day(a_day)} bereits '
                                   f'{ctx.shifts[b_on_a].name}.')))
        opt.changes = [(a_id, a_iso, a_sid, None), (a_id, b_iso, a_on_b, b_sid),
                       (b_id, b_iso, b_sid, None), (b_id, a_iso, b_on_a, a_sid)]
    else:
        raise ValueError(kind)

    overlay = opt.overlay
    for side, check in pre:
        (opt.checks_a if side == 'a' else opt.checks_b).append(check)
    no_double = Check('Doppelte Schicht', 'ok', 'Keine zweite Schicht am selben Tag.')
    for eid, iso, _old, new in opt.changes:
        day = date.fromisoformat(iso)
        fixed_sid = ctx.patterns.get((eid, day.weekday()))
        if fixed_sid and new != fixed_sid:
            check = Check('Feste Wochenschicht', 'block',
                          f'{ctx.employees[eid].name}: die feste Wochenschicht '
                          f'„{ctx.shifts[fixed_sid].name}“ am {fmt_day(day)} darf nicht entfernt oder getauscht werden.')
            (opt.checks_a if eid == a_id else opt.checks_b).append(check)
    for eid, target in ((a_id, opt.checks_a), (b_id, opt.checks_b)):
        if not any(c.rule == 'Doppelte Schicht' for c in target):
            target.append(no_double)
        for e2, iso, _old, new in opt.changes:
            if e2 == eid and new:
                target.extend(ctx.check_assignment(eid, date.fromisoformat(iso), new, overlay))
    pairs = set()
    for _e, iso, old, new in opt.changes:
        pairs.add((iso, old))
        pairs.add((iso, new))
    opt.checks_cov = ctx.coverage_checks(pairs, overlay)
    return opt


def swap_between(ctx, a_id, a_day, b_id, b_day):
    """Tausch zwischen zwei angeklickten Namenschips (A am a_day, B am b_day).

    Liefert eine geprüfte SwapOption oder ValueError mit verständlicher Begründung,
    wenn ein Tausch keinen Sinn ergibt. Es wird nichts gespeichert."""
    if a_id == b_id:
        raise ValueError('Das ist dieselbe Person – bitte eine andere Person anklicken.')
    a_sid, b_sid = ctx.shift_of(a_id, a_day), ctx.shift_of(b_id, b_day)
    if not a_sid or not b_sid:
        raise ValueError('Beide Personen müssen an den angeklickten Tagen eine Schicht haben.')
    if a_day == b_day:
        if a_sid == b_sid:
            raise ValueError(f'Beide arbeiten am {fmt_day(a_day)} bereits {ctx.shifts[a_sid].name} – '
                             'ein Tausch würde nichts ändern.')
        return evaluate_swap(ctx, 'same_day', a_id, a_day, b_id)
    return evaluate_swap(ctx, 'cross_day', a_id, a_day, b_id, b_day)


def find_swap_options(ctx, a_id, a_day, window=7):
    """Alle geprüften Tausch-/Übernahmeoptionen für die Schicht von `a_id` am `a_day`."""
    a_sid = ctx.shift_of(a_id, a_day)
    if not a_sid:
        return []
    options = []
    for b in ctx.active:
        if b.id == a_id:
            continue
        b_sid = ctx.shift_of(b.id, a_day)
        if b_sid and b_sid != a_sid:
            options.append(evaluate_swap(ctx, 'same_day', a_id, a_day, b.id))
        elif not b_sid:
            options.append(evaluate_swap(ctx, 'takeover', a_id, a_day, b.id))
        for offset in range(-window, window + 1):
            other = a_day + timedelta(days=offset)
            if offset == 0 or other < ctx.start or other > ctx.end:
                continue
            if ctx.shift_of(b.id, other):
                options.append(evaluate_swap(ctx, 'cross_day', a_id, a_day, b.id, other))
    options.sort(key=SwapOption.sort_key)
    return options


def explain_no_options(ctx, options):
    """Verständliche Begründung, wenn kein gültiger Tausch möglich ist."""
    if not ctx.active or len(ctx.active) < 2:
        return 'Es gibt keine weiteren aktiven Mitarbeitenden, mit denen getauscht werden könnte.'
    if not options:
        return ('Im Zeitraum ±7 Tage hat niemand eine passende Schicht oder einen freien Tag für eine Übernahme.')
    counts = Counter()
    for opt in options:
        for rule in {c.rule for c in opt.blockers}:
            counts[rule] += 1
    parts = [f'{rule} ({n}×)' for rule, n in counts.most_common(4)]
    return ('Kein Vorschlag erfüllt alle geprüften Regeln. Häufigste Hinderungsgründe: ' + ', '.join(parts) +
            '. Tipp: Prüfe Schichteignungen, Urlaube oder die Hausregeln unter „Schichten“.')


def partner_highlights(options):
    """{(employee_id, iso)} der Namenschips, die als möglicher Tauschpartner leuchten sollen."""
    marks = set()
    for opt in options:
        if opt.valid and opt.kind in ('same_day', 'cross_day'):
            marks.add((opt.b_id, opt.b_day.isoformat()))
    return marks


class StaleSwapError(RuntimeError):
    pass


def _current_sid(db, eid, iso):
    row = db.execute('SELECT shift_id FROM assignments WHERE employee_id=? AND day=?', (eid, iso)).fetchone()
    return row[0] if row else None


def _write_assignment(db, eid, iso, sid):
    if sid is None:
        db.execute('DELETE FROM assignments WHERE employee_id=? AND day=?', (eid, iso))
    else:
        db.execute('INSERT INTO assignments(day,employee_id,shift_id) VALUES(?,?,?) '
                   'ON CONFLICT(day,employee_id) DO UPDATE SET shift_id=excluded.shift_id', (iso, eid, sid))


def apply_swap(store, option):
    """Speichert einen Tausch – erst nach erneuter Prüfung gegen den aktuellen Datenbankstand."""
    ctx = PlanContext(store, min(option.a_day, option.b_day or option.a_day),
                      max(option.a_day, option.b_day or option.a_day))
    fresh = evaluate_swap(ctx, option.kind, option.a_id, option.a_day, option.b_id, option.b_day)
    if [c[:4] for c in fresh.changes] != [c[:4] for c in option.changes]:
        raise StaleSwapError('Der Dienstplan wurde inzwischen geändert. Bitte Vorschläge neu laden.')
    if not fresh.valid:
        raise StaleSwapError('Der Tausch ist nach aktuellem Stand nicht mehr zulässig: ' +
                             '; '.join(c.message for c in fresh.blockers))
    description = fresh.headline(ctx)
    with store.db:
        for eid, iso, old, _new in fresh.changes:
            if _current_sid(store.db, eid, iso) != old:
                raise StaleSwapError('Der Dienstplan wurde inzwischen geändert. Bitte Vorschläge neu laden.')
        for eid, iso, _old, new in fresh.changes:
            _write_assignment(store.db, eid, iso, new)
        return log_change(store.db, 'swap', 'Tausch: ' + description, fresh.changes)


# ---------------------------------------------------------------------------
# Änderungsprotokoll: mehrstufiges Rückgängig / Wiederholen
# ---------------------------------------------------------------------------

UNDO_LIMIT = 200  # ältere Schritte werden verworfen


def log_change(db, kind, description, changes, extra=None):
    """Protokolliert eine Planänderung als einen Rückgängig-Schritt (innerhalb der laufenden Transaktion).

    changes: [(employee_id, iso_tag, alte_schicht|None, neue_schicht|None)]
    extra: {'leaves_added': [Urlaubszeile], 'leaves_deleted': [Urlaubszeile]}"""
    changes = [[e, d, o, n] for e, d, o, n in changes if o != n]
    extra = {k: v for k, v in (extra or {}).items() if v}
    if not changes and not extra:
        return None
    db.execute('UPDATE change_log SET state=2 WHERE state=1')  # neue Änderung verwirft „Wiederholen“
    cur = db.execute('INSERT INTO change_log(created_at,kind,description,changes_json,extra_json) VALUES(?,?,?,?,?)',
                     (datetime.now().isoformat(timespec='seconds'), kind, description, json.dumps(changes),
                      json.dumps(extra)))
    db.execute('DELETE FROM change_log WHERE id <= (SELECT id FROM change_log ORDER BY id DESC LIMIT 1 OFFSET ?)',
               (UNDO_LIMIT,))
    return cur.lastrowid


def _leave_row(db, leave_id):
    row = db.execute('SELECT id,employee_id,start_date,end_date,note FROM leave_periods WHERE id=?',
                     (leave_id,)).fetchone()
    return list(row) if row else None


def _insert_leave_row(db, row):
    if db.execute('SELECT 1 FROM employees WHERE id=?', (row[1],)).fetchone():
        db.execute('INSERT OR IGNORE INTO leave_periods(id,employee_id,start_date,end_date,note) VALUES(?,?,?,?,?)',
                   tuple(row))


def undo_info(store):
    """(Beschreibung nächstes Rückgängig, Anzahl, Beschreibung nächstes Wiederholen, Anzahl)."""
    undo = store.db.execute('SELECT description FROM change_log WHERE state=0 ORDER BY id DESC LIMIT 1').fetchone()
    redo = store.db.execute('SELECT description FROM change_log WHERE state=1 ORDER BY id ASC LIMIT 1').fetchone()
    n_undo = store.db.execute('SELECT COUNT(*) FROM change_log WHERE state=0').fetchone()[0]
    n_redo = store.db.execute('SELECT COUNT(*) FROM change_log WHERE state=1').fetchone()[0]
    return (undo[0] if undo else None), n_undo, (redo[0] if redo else None), n_redo


def _step(store, undo):
    if undo:
        row = store.db.execute('SELECT * FROM change_log WHERE state=0 ORDER BY id DESC LIMIT 1').fetchone()
    else:
        row = store.db.execute('SELECT * FROM change_log WHERE state=1 ORDER BY id ASC LIMIT 1').fetchone()
    if not row:
        raise StaleSwapError('Es gibt keinen Schritt, der ' + ('rückgängig gemacht' if undo else 'wiederholt') +
                             ' werden kann.')
    changes = json.loads(row['changes_json'])
    extra = json.loads(row['extra_json'] or '{}')
    add_key, del_key = ('leaves_added', 'leaves_deleted') if undo else ('leaves_deleted', 'leaves_added')
    existing = {r[0] for r in store.db.execute('SELECT id FROM employees')}
    with store.db:
        # Sicherheitsprüfung: Nur wenn die betroffenen Einträge noch genau so aussehen wie nach (bzw. vor)
        # diesem Schritt, wird zurückgesetzt – spätere Änderungen werden nie überschrieben.
        for eid, iso, old, new in changes:
            expected = new if undo else old
            if eid in existing and _current_sid(store.db, eid, iso) != expected:
                raise StaleSwapError(f'„{row["description"]}“ kann nicht ' +
                                     ('rückgängig gemacht' if undo else 'wiederholt') +
                                     ' werden, weil die betroffenen Tage danach anders geändert wurden '
                                     '(z. B. über ein gelöschtes Schichtmodell). Bitte manuell prüfen.')
        for leave in extra.get(add_key, []):
            store.db.execute('DELETE FROM leave_periods WHERE id=?', (leave[0],))
        for leave in extra.get(del_key, []):
            _insert_leave_row(store.db, leave)
        for eid, iso, old, new in (reversed(changes) if undo else changes):
            if eid in existing:
                _write_assignment(store.db, eid, iso, old if undo else new)
        store.db.execute('UPDATE change_log SET state=? WHERE id=?', (1 if undo else 0, row['id']))
    return row['description']


def undo_last_change(store):
    """Macht den jüngsten Schritt rückgängig (mehrfach aufrufbar = mehrere Schritte)."""
    return _step(store, True)


def redo_next_change(store):
    """Wiederholt den zuletzt rückgängig gemachten Schritt."""
    return _step(store, False)


def last_undoable_change(store):
    return store.db.execute('SELECT * FROM change_log WHERE state=0 ORDER BY id DESC LIMIT 1').fetchone()


# Kompatibilität mit früheren Funktionsnamen
last_undoable_swap = last_undoable_change
undo_last_swap = undo_last_change


# ---------------------------------------------------------------------------
# Automatischer Monatsentwurf
# ---------------------------------------------------------------------------

@dataclass
class OpenSlot:
    day: date
    shift_id: int
    shift_name: str
    missing: int
    reason: str = ''


@dataclass
class GenerationResult:
    filled: int = 0
    open_slots: list = field(default_factory=list)
    extra: int = 0  # Zusatzdienste über der Mindestbesetzung zur Erfüllung der Soll-Stunden

    @property
    def missing(self):
        return sum(s.missing for s in self.open_slots)


def is_special_day(day):
    """Samstag, Sonntag oder Feiertag – für die faire Verteilung relevant (werden normal besetzt)."""
    return is_weekend(day) or holiday_name(day) is not None


def _fair_shares(ctx, total, pool):
    """Verteilt `total` Dienste proportional zu den Soll-Stunden auf die Personen in `pool`."""
    targets = {e.id: ctx.target_hours(e.id) for e in pool}
    weight = sum(targets.values()) or 1.0
    return {eid: total * t / weight for eid, t in targets.items()}


def generate_month(store, month, clear_existing=False, rng=None, fill_hours=True, attempts=6):
    """Erstellt mehrere Entwürfe mit unterschiedlicher Zufallsreihenfolge und speichert den besten
    (wenigste offene Plätze, dann geringste Abweichung von den Soll-Stunden). Siehe `_draft_month`."""
    plan = compute_month_plan(store, month, clear_existing, rng, fill_hours, attempts)
    return save_month_plan(store, month, plan, clear_existing)


def compute_month_plan(store, month, clear_existing=False, rng=None, fill_hours=True, attempts=6):
    """Rechnet den besten Entwurf, ohne zu speichern. `store` darf auch ein ReadStore sein
    (eigene Verbindung), damit die Berechnung in einem Hintergrund-Thread laufen kann."""
    rng = rng or random.Random()
    best = None
    for _ in range(max(1, attempts)):
        draft = _draft_month(store, month, clear_existing, random.Random(rng.random()), fill_hours)
        if draft is None:
            return None
        if best is None or draft[0] < best[0]:
            best = draft
        if best[0][:3] == (0, 0, 0):
            break
    return best[1], best[2]


def save_month_plan(store, month, plan, clear_existing=False):
    """Speichert einen mit compute_month_plan berechneten Entwurf (als ein Rückgängig-Schritt)."""
    if plan is None:
        return GenerationResult()
    inserts, result = plan
    start, end = month_bounds(month)
    lo, hi = start.isoformat(), end.isoformat()
    with store.db:
        before = {(r[0], r[1]): r[2] for r in store.db.execute(
            'SELECT employee_id, day, shift_id FROM assignments WHERE day BETWEEN ? AND ?', (lo, hi))}
        if clear_existing:
            store.db.execute('DELETE FROM assignments WHERE day BETWEEN ? AND ?', (lo, hi))
        store.db.executemany('INSERT INTO assignments(day,employee_id,shift_id) VALUES(?,?,?) '
                             'ON CONFLICT(day,employee_id) DO UPDATE SET shift_id=excluded.shift_id', inserts)
        after = {(r[0], r[1]): r[2] for r in store.db.execute(
            'SELECT employee_id, day, shift_id FROM assignments WHERE day BETWEEN ? AND ?', (lo, hi))}
        changes = [(eid, ds, before.get((eid, ds)), after.get((eid, ds)))
                   for eid, ds in sorted(set(before) | set(after)) if before.get((eid, ds)) != after.get((eid, ds))]
        label = 'neu geplant' if clear_existing else 'offene Plätze gefüllt'
        log_change(store.db, 'generate', f'Monat {MONTHS[start.month - 1]} {start.year} {label} '
                                         f'({len(changes)} Änderungen)', changes)
    result.filled = len(inserts)
    return result


class ReadStore:
    """Nur-Lese-Zugriff mit eigener Verbindung (z. B. für Berechnungen im Hintergrund-Thread)."""

    def __init__(self, path):
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    def all(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def setting(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        if row:
            return row[0]
        return DEFAULT_SETTINGS.get(key, default)

    def close(self):
        self.db.close()


def _draft_month(store, month, clear_existing, rng, fill_hours):
    """Erstellt einen fairen, editierbaren Entwurf.

    Phase 1 besetzt jede Schicht mindestens mit der Mindestbesetzung – auch Samstage, Sonntage und
    Feiertage. Phase 2 (fill_hours) plant zusätzliche Dienste, bis alle möglichst ihre Soll-Stunden
    erreichen; dabei können mehrere Personen in derselben Schicht arbeiten (bis zur Maximalbesetzung).
    Wochenend-/Feiertags- und Nachtdienste werden proportional zu den Soll-Stunden verteilt.
    Bestehende Einträge bleiben erhalten, außer `clear_existing=True`."""
    start, end = month_bounds(month)
    ctx = PlanContext(store, start, end)
    result = GenerationResult()
    if not ctx.active or not ctx.shift_list:
        return None
    lo, hi = start.isoformat(), end.isoformat()
    if clear_existing:
        for key in [k for k in ctx.plan if lo <= k[1] <= hi]:
            del ctx.plan[key]
    pattern_inserts = []
    for day in daterange(start, end):
        for e in ctx.active:
            sid = ctx.patterns.get((e.id, day.weekday()))
            if not sid or (e.id, day.isoformat()) in ctx.plan:
                continue
            blockers = [c for c in ctx.check_assignment(e.id, day, sid, include_ok=False) if c.blocking]
            if not blockers:
                ctx.plan[(e.id, day.isoformat())] = sid
                pattern_inserts.append((day.isoformat(), e.id, sid))
    active_ids = {e.id for e in ctx.active}
    targets = {e.id: ctx.target_hours(e.id) for e in ctx.active}
    hours = {e.id: ctx.month_hours(e.id) for e in ctx.active}
    special_cnt, night_cnt = Counter(), Counter()
    count = Counter()
    for (eid, ds), sid in ctx.plan.items():
        if lo <= ds <= hi:
            count[(ds, sid)] += 1
            if eid in active_ids:
                special_cnt[eid] += is_special_day(date.fromisoformat(ds))
                night_cnt[eid] += ctx.shifts[sid].is_night
    days = list(daterange(start, end))
    night_ids = {s.id for s in ctx.shift_list if s.is_night}
    special_total = sum(s.coverage for s in ctx.shift_list) * sum(1 for d in days if is_special_day(d))
    night_total = sum(s.coverage for s in ctx.shift_list if s.is_night) * len(days)
    fair_special = _fair_shares(ctx, special_total, ctx.active)
    night_pool = [e for e in ctx.active if any(ctx.eligible(e.id, sid) for sid in night_ids)]
    fair_night = _fair_shares(ctx, night_total, night_pool)
    inserts = list(pattern_inserts)

    def fairness_cost(e, day, shift):
        cost = 0.0
        if is_special_day(day):
            fair = fair_special.get(e.id, 0.5)
            cost += 35 * (special_cnt[e.id] + 1) / max(0.5, fair)
            cost += 45 * max(0.0, special_cnt[e.id] + 1 - fair - 1.5)  # deutlich über fairem Anteil: teuer
            if e.avoids_weekend:
                cost += 60
        if shift.is_night:
            fair = fair_night.get(e.id, 0.5)
            cost += 35 * (night_cnt[e.id] + 1) / max(0.5, fair)
            cost += 45 * max(0.0, night_cnt[e.id] + 1 - fair - 1.5)
        bonus = sum(1 for p in e.pref_list if p.split()[0] and p.split()[0] in shift.name.lower())
        return cost - bonus * 18

    def assign(eid, day, shift):
        ds = day.isoformat()
        ctx.plan[(eid, ds)] = shift.id
        hours[eid] += shift.hours
        count[(ds, shift.id)] += 1
        special_cnt[eid] += is_special_day(day)
        night_cnt[eid] += shift.is_night
        inserts.append((ds, eid, shift.id))

    # Phase 1: Mindestbesetzung
    for day in days:
        ds = day.isoformat()
        for shift in ctx.shift_list:
            need = max(0, shift.coverage - count[(ds, shift.id)])
            for slot in range(need):
                candidates, reasons = [], Counter()
                for e in ctx.active:
                    if (e.id, ds) in ctx.plan:
                        reasons['bereits eingeteilt'] += 1
                        continue
                    blockers = [c for c in ctx.check_assignment(e.id, day, shift.id, include_ok=False) if c.blocking]
                    if blockers:
                        for rule in {c.rule for c in blockers}:
                            reasons[rule] += 1
                        continue
                    load = hours[e.id] / max(1.0, targets[e.id]) * 100
                    candidates.append((load + fairness_cost(e, day, shift) + rng.random() * 3, e.id))
                if not candidates:
                    reason = ', '.join(f'{r} ({n})' for r, n in reasons.most_common(3))
                    result.open_slots.append(OpenSlot(day, shift.id, shift.name, need - slot, reason))
                    break
                assign(min(candidates)[1], day, shift)

    # Phase 2: Soll-Stunden auffüllen (Zusatzdienste, mehrere Personen pro Schicht)
    if fill_hours:
        progress = True
        while progress:
            progress = False
            for e in sorted(ctx.active, key=lambda x: hours[x.id] - targets[x.id]):
                deficit = targets[e.id] - hours[e.id]
                best = None
                for day in days:
                    ds = day.isoformat()
                    if (e.id, ds) in ctx.plan:
                        continue
                    for shift in ctx.shift_list:
                        if deficit <= shift.hours / 2:  # ein weiterer Dienst würde weiter vom Soll wegführen
                            continue
                        n = count[(ds, shift.id)]
                        if shift.cap and n >= shift.cap:
                            continue
                        if any(c.blocking for c in ctx.check_assignment(e.id, day, shift.id, include_ok=False)):
                            continue
                        spread = (n - shift.coverage) * 40  # Zusatzdienste gleichmäßig über den Monat verteilen
                        cost = spread + fairness_cost(e, day, shift) + rng.random() * 3
                        if best is None or cost < best[0]:
                            best = (cost, day, shift)
                if best:
                    assign(e.id, best[1], best[2])
                    result.extra += 1
                    progress = True

    tol = ctx.hours_tolerance
    deviation = [abs(hours[e.id] - targets[e.id]) for e in ctx.active]
    # Fairness des Ergebnisses: Abweichung vom fairen Anteil (wie in fairness_report berechnet)
    real_special = _fair_shares(ctx, sum(special_cnt[e.id] for e in ctx.active), ctx.active)
    real_night = _fair_shares(ctx, sum(night_cnt[e.id] for e in night_pool), night_pool)
    unfair = sum(max(0.0, abs(special_cnt[e.id] - real_special[e.id]) - 1.5)
                 for e in ctx.active if not e.avoids_weekend)
    unfair += sum(max(0.0, abs(night_cnt[e.id] - real_night[e.id]) - 1.5) for e in night_pool)
    score = (result.missing, sum(1 for d in deviation if d > tol), round(unfair, 1), round(sum(deviation), 1))
    return score, inserts, result


def build_assignments(store, month, clear_existing=False, rng=None):
    """Kompatibilität: liefert die Anzahl nicht besetzbarer Schichtplätze."""
    return generate_month(store, month, clear_existing, rng).missing


# ---------------------------------------------------------------------------
# Stunden & Fairness
# ---------------------------------------------------------------------------

@dataclass
class PersonStats:
    employee: Employee
    target: float
    actual: float
    shifts: int
    special: int
    special_fair: float
    nights: int
    nights_fair: float
    leave_days: int
    tolerance: float

    @property
    def diff(self):
        return round(self.actual - self.target, 1)

    @property
    def status(self):
        if abs(self.diff) <= self.tolerance:
            return 'ok'
        return 'under' if self.diff < 0 else 'over'

    @property
    def status_text(self):
        return {'ok': 'erfüllt', 'under': 'unter Soll', 'over': 'über Soll'}[self.status]


def fairness_report(ctx):
    """Soll/Ist-Stunden und Verteilung von Wochenend-/Feiertags- und Nachtdiensten im Zeitraum."""
    lo, hi = ctx.start.isoformat(), ctx.end.isoformat()
    special, nights, shifts = Counter(), Counter(), Counter()
    for (eid, ds), sid in ctx.plan.items():
        if lo <= ds <= hi:
            shifts[eid] += 1
            special[eid] += is_special_day(date.fromisoformat(ds))
            nights[eid] += ctx.shifts[sid].is_night
    pool = [e for e in ctx.active]
    night_ids = {s.id for s in ctx.shift_list if s.is_night}
    night_pool = [e for e in pool if any(ctx.eligible(e.id, sid) for sid in night_ids)]
    fair_special = _fair_shares(ctx, sum(special[e.id] for e in pool), pool)
    fair_night = _fair_shares(ctx, sum(nights[e.id] for e in night_pool), night_pool)
    rows = []
    for e in sorted(pool, key=lambda x: x.name.lower()):
        rows.append(PersonStats(e, ctx.target_hours(e.id), ctx.month_hours(e.id), shifts[e.id], special[e.id],
                                round(fair_special.get(e.id, 0), 1), nights[e.id], round(fair_night.get(e.id, 0), 1),
                                ctx.leave_days_in_range(e.id), ctx.hours_tolerance))
    return rows


# ---------------------------------------------------------------------------
# Testurlaube (nur für Demo-Personen)
# ---------------------------------------------------------------------------

def plan_test_leaves(store, months):
    """Plant Testurlaube für aktive Demo-Personen (ohne zu speichern) -> [(id, Name, von, bis)].

    Abwechselnd die Hälfte der Demo-Personen pro Monat, 3–5 Tage, gestaffelt. Personen, die im
    Zeitraum schon Urlaub haben, werden übersprungen – es wird also nie etwas doppelt angelegt."""
    demo = store.all('SELECT id,name FROM employees WHERE is_demo=1 AND active=1 ORDER BY id')
    plans = []
    for m_index, month in enumerate(months):
        first, last = month_bounds(month)
        for idx, e in enumerate(demo):
            if idx % 2 != m_index % 2:
                continue
            slot = idx // 2  # gestaffelt: höchstens eine Demo-Person gleichzeitig im Testurlaub
            begin = first.replace(day=min(2 + (slot % 5) * 6, last.day))
            finish = min(last, begin + timedelta(days=2 + idx % 3))
            if overlapping_leave(store, e['id'], first, last):
                continue
            plans.append((e['id'], e['name'], begin, finish))
    return plans


def add_test_leaves(store, months, log=True):
    """Legt geplante Testurlaube an (ein Rückgängig-Schritt). Rückgabe: (angelegte Urlaube, entfernte Dienste)."""
    plans = plan_test_leaves(store, months)
    leaves, removed = [], []
    with store.db:
        for eid, _name, begin, finish in plans:
            leave, gone = _add_leave_rows(store.db, eid, begin, finish, TEST_LEAVE_NOTE, True)
            leaves.append(leave)
            removed += gone
        if log:
            log_change(store.db, 'test_leave', f'{len(leaves)} Testurlaube angelegt', removed,
                       {'leaves_added': leaves})
    return plans, len(removed)


def remove_test_leaves(store):
    with store.db:
        rows = [list(r) for r in store.db.execute(
            'SELECT id,employee_id,start_date,end_date,note FROM leave_periods WHERE note=?', (TEST_LEAVE_NOTE,))]
        store.db.execute('DELETE FROM leave_periods WHERE note=?', (TEST_LEAVE_NOTE,))
        log_change(store.db, 'test_leave_delete', f'{len(rows)} Testurlaube entfernt', [], {'leaves_deleted': rows})
    return len(rows)


# ---------------------------------------------------------------------------
# Übersicht, Tagesplanung, Urlaub, Export
# ---------------------------------------------------------------------------

@dataclass
class MonthOverview:
    open_slots: list
    open_total: int
    leave_rows: list  # [(Employee, start, end, Tage im Monat, Notiz)]
    leave_days_total: int
    warnings: list  # [Check]


def month_overview(ctx):
    open_slots = []
    for day in daterange(ctx.start, ctx.end):
        for shift in ctx.shift_list:
            have = len(ctx.staff_on(day, shift.id))
            if have < shift.coverage:
                open_slots.append(OpenSlot(day, shift.id, shift.name, shift.coverage - have))
    leave_rows, total = [], 0
    for lp in ctx.leave_periods:
        first = max(ctx.start, date.fromisoformat(lp['start_date']))
        last = min(ctx.end, date.fromisoformat(lp['end_date']))
        if first > last or lp['employee_id'] not in ctx.employees:
            continue
        n = (last - first).days + 1
        total += n
        leave_rows.append((ctx.employees[lp['employee_id']], date.fromisoformat(lp['start_date']),
                           date.fromisoformat(lp['end_date']), n, lp['note'] or ''))
    seen, warnings = set(), []
    for (eid, ds), sid in sorted(ctx.plan.items(), key=lambda kv: kv[0][1]):
        if not (ctx.start.isoformat() <= ds <= ctx.end.isoformat()) or eid not in ctx.employees:
            continue
        for c in ctx.check_assignment(eid, date.fromisoformat(ds), sid, include_ok=False):
            if c.status == 'block' and c.message not in seen:
                seen.add(c.message)
                warnings.append(c)
    return MonthOverview(open_slots, sum(s.missing for s in open_slots), leave_rows, total, warnings)


def preview_day_changes(ctx, day, changes):
    """Prüft geplante Änderungen eines Tages {eid: sid|None} -> {eid: [Check]} (nur Auffälligkeiten)."""
    overlay = {(eid, day.isoformat()): sid for eid, sid in changes.items()}
    result = {}
    for eid, sid in changes.items():
        if sid:
            issues = [c for c in ctx.check_assignment(eid, day, sid, overlay, include_ok=False)
                      if c.status in ('block', 'warn')]
            if issues:
                result[eid] = issues
        else:
            fixed_sid = ctx.patterns.get((eid, day.weekday()))
            if fixed_sid:
                result[eid] = [Check('Feste Wochenschicht', 'block',
                                     f'{ctx.employees[eid].name}: die feste Wochenschicht '
                                     f'„{ctx.shifts[fixed_sid].name}“ am {fmt_day(day)} darf nicht entfernt werden.')]
    pairs = {(day.isoformat(), s.id) for s in ctx.shift_list}
    cov = [c for c in ctx.coverage_checks(pairs, overlay) if c.status != 'ok']
    if cov:
        result['coverage'] = cov
    return result


def apply_day_changes(store, day, changes):
    iso = day.isoformat()
    with store.db:
        logged = [(eid, iso, _current_sid(store.db, eid, iso), sid) for eid, sid in changes.items()]
        for eid, sid in changes.items():
            _write_assignment(store.db, eid, iso, sid)
        log_change(store.db, 'day', f'Tagesplanung {day:%d.%m.%Y} ({len(logged)} Änderung(en))', logged)


def assignments_in_range(store, eid, first, last):
    return store.all('SELECT a.day, s.name AS shift FROM assignments a LEFT JOIN shifts s ON s.id=a.shift_id '
                     'WHERE a.employee_id=? AND a.day BETWEEN ? AND ? ORDER BY a.day',
                     (eid, first.isoformat(), last.isoformat()))


def overlapping_leave(store, eid, first, last):
    return store.all('SELECT * FROM leave_periods WHERE employee_id=? AND start_date<=? AND end_date>=?',
                     (eid, last.isoformat(), first.isoformat()))


def _add_leave_rows(db, eid, first, last, note, remove_assignments):
    if last < first:
        raise ValueError('Das Ende darf nicht vor dem Beginn liegen.')
    cur = db.execute('INSERT INTO leave_periods(employee_id,start_date,end_date,note) VALUES(?,?,?,?)',
                     (eid, first.isoformat(), last.isoformat(), note))
    removed = []
    if remove_assignments:
        removed = [(eid, r[0], r[1], None) for r in db.execute(
            'SELECT day, shift_id FROM assignments WHERE employee_id=? AND day BETWEEN ? AND ?',
            (eid, first.isoformat(), last.isoformat()))]
        db.execute('DELETE FROM assignments WHERE employee_id=? AND day BETWEEN ? AND ?',
                   (eid, first.isoformat(), last.isoformat()))
    return _leave_row(db, cur.lastrowid), removed


def _employee_name(db, eid):
    row = db.execute('SELECT name FROM employees WHERE id=?', (eid,)).fetchone()
    return row[0] if row else f'ID {eid}'


def add_leave(store, eid, first, last, note='', remove_assignments=True, log=True):
    if last < first:
        raise ValueError('Das Ende darf nicht vor dem Beginn liegen.')
    with store.db:
        leave, removed = _add_leave_rows(store.db, eid, first, last, note, remove_assignments)
        if log:
            log_change(store.db, 'leave', f'Urlaub {_employee_name(store.db, eid)} {first:%d.%m.}–{last:%d.%m.%Y}',
                       removed, {'leaves_added': [leave]})
    return len(removed)


def delete_leave(store, leave_id):
    with store.db:
        leave = _leave_row(store.db, leave_id)
        if not leave:
            return
        store.db.execute('DELETE FROM leave_periods WHERE id=?', (leave_id,))
        log_change(store.db, 'leave_delete', f'Urlaub entfernt: {_employee_name(store.db, leave[1])} '
                   f'{date.fromisoformat(leave[2]):%d.%m.}–{date.fromisoformat(leave[3]):%d.%m.%Y}', [],
                   {'leaves_deleted': [leave]})


def employee_delete_impact(store, eid, today=None):
    """Was beim Löschen einer Person mit entfernt würde (nur lesen, nichts ändern)."""
    today = (today or date.today()).isoformat()
    one = lambda sql: store.db.execute(sql, (eid,) if sql.count('?') == 1 else (eid, today)).fetchone()[0]
    return {
        'assignments': one('SELECT COUNT(*) FROM assignments WHERE employee_id=?'),
        'future': one('SELECT COUNT(*) FROM assignments WHERE employee_id=? AND day>=?'),
        'past': one('SELECT COUNT(*) FROM assignments WHERE employee_id=? AND day<?'),
        'leave': one('SELECT COUNT(*) FROM leave_periods WHERE employee_id=?'),
    }


def delete_employee(store, eid):
    """Löscht eine Person endgültig samt Diensten, Urlauben und Schichteignung.

    Vorher wird automatisch eine Sicherung der Datenbank in data/backups angelegt. Tausche aus dem
    Protokoll, an denen die Person beteiligt war, können danach nicht mehr rückgängig gemacht werden.
    Rückgabe: (Name, Pfad der Sicherung)."""
    row = store.db.execute('SELECT name FROM employees WHERE id=?', (eid,)).fetchone()
    if not row:
        raise ValueError('Diese Person existiert nicht (mehr).')
    backup = store._backup('vor_loeschen')
    with store.db:
        for log in store.all('SELECT id,changes_json,extra_json FROM change_log WHERE state IN (0,1)'):
            extra = json.loads(log['extra_json'] or '{}')
            involved = any(entry[0] == eid for entry in json.loads(log['changes_json'])) or any(
                leave[1] == eid for key in ('leaves_added', 'leaves_deleted') for leave in extra.get(key, []))
            if involved:  # diese Schritte lassen sich ohne die Person nicht mehr sauber zurücksetzen
                store.db.execute('UPDATE change_log SET state=3 WHERE id=?', (log['id'],))
        # Explizit statt nur über ON DELETE CASCADE, damit auch ältere Datenbanken sauber bleiben.
        store.db.execute('DELETE FROM assignments WHERE employee_id=?', (eid,))
        store.db.execute('DELETE FROM leave_periods WHERE employee_id=?', (eid,))
        store.db.execute('DELETE FROM eligibility WHERE employee_id=?', (eid,))
        store.db.execute('DELETE FROM employees WHERE id=?', (eid,))
    return row[0], backup


def export_csv(store, month, path):
    rows = store.all('''SELECT a.day,e.name,e.role,s.name AS shift,s.start,s.end,s.hours FROM assignments a
        JOIN employees e ON e.id=a.employee_id JOIN shifts s ON s.id=a.shift_id WHERE a.day LIKE ? ORDER BY a.day,e.name''',
                     (month.strftime('%Y-%m') + '-%',))
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(['Datum', 'Mitarbeiter', 'Bereich', 'Schicht', 'Beginn', 'Ende', 'Stunden'])
        writer.writerows([tuple(row) for row in rows])
    return len(rows)
