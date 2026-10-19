import os
import sys
import sqlite3
import calendar
import random
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import date, datetime, timedelta

APP_NAME = 'Klinik Dienstplan'
BASE = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'dienstplan.db')
MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember']
WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']


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


def bw_holidays(year):
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
    return fixed


class Store:
    def __init__(self):
        self.db = sqlite3.connect(DB_PATH)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
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
        ''')
        if self.db.execute('SELECT COUNT(*) FROM shifts').fetchone()[0] == 0:
            self.db.executemany('INSERT INTO shifts(name,start,end,hours,color,coverage) VALUES(?,?,?,?,?,?)', [
                ('Frühdienst', '06:00', '14:00', 8, '#f3b84b', 1),
                ('Spätdienst', '14:00', '22:00', 8, '#7786ed', 1),
                ('Nachtdienst', '22:00', '06:00', 8, '#64748b', 1)])
        self.db.commit()
        self.demo_seeded_now = self.seed_demo_staff()

    def all(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def run(self, sql, args=()):
        self.db.execute(sql, args)
        self.db.commit()

    def seed_demo_staff(self):
        seeded = self.db.execute("SELECT value FROM settings WHERE key='demo_staff_seeded'").fetchone()
        if seeded:
            return False
        if self.db.execute('SELECT COUNT(*) FROM employees').fetchone()[0] == 0:
            shifts = self.all('SELECT id FROM shifts')
            demo = [
                ('Demo Anna Weber', 'Pflegefachkraft', 38.5, 5, 'Frühdienst bevorzugt'),
                ('Demo Ben Fischer', 'Pflegefachkraft', 38.5, 5, 'Spätdienst bevorzugt'),
                ('Demo Clara Hoffmann', 'Pflegefachkraft', 30.0, 4, 'Wochenende vermeiden'),
                ('Demo David Schneider', 'Pflegefachkraft', 38.5, 5, 'Nachtdienst bevorzugt'),
                ('Demo Emma Bauer', 'Stationsleitung', 38.5, 5, 'Frühdienst bevorzugt'),
                ('Demo Felix Wagner', 'Pflegefachkraft', 32.0, 4, ''),
                ('Demo Greta Klein', 'Pflegefachkraft', 38.5, 5, ''),
                ('Demo Hassan Yilmaz', 'Pflegefachkraft', 30.0, 4, 'Spätdienst bevorzugt'),
            ]
            for name, role, hours, max_days, prefs in demo:
                cur = self.db.execute('INSERT INTO employees(name,role,weekly_hours,max_days,preferences) VALUES(?,?,?,?,?)',
                                      (name, role, hours, max_days, prefs))
                self.db.executemany('INSERT INTO eligibility(employee_id,shift_id) VALUES(?,?)',
                                    [(cur.lastrowid, s['id']) for s in shifts])
        self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('demo_staff_seeded','1')")
        self.db.commit()
        return True


def build_assignments(store, month, clear_existing=False):
    """Create a fair, editable draft and report unfilled staffing slots."""
    employees = store.all('SELECT * FROM employees WHERE active=1 ORDER BY name')
    shifts = store.all('SELECT * FROM shifts ORDER BY start')
    if not employees or not shifts:
        return 0
    start = month.replace(day=1)
    last_day = calendar.monthrange(start.year, start.month)[1]
    end = date(start.year, start.month, last_day)
    if clear_existing:
        store.run('DELETE FROM assignments WHERE day BETWEEN ? AND ?', (start.isoformat(), end.isoformat()))
    elig = {e['id']: {r['shift_id'] for r in store.all('SELECT shift_id FROM eligibility WHERE employee_id=?', (e['id'],))} for e in employees}
    history = {e['id']: [(r['day'], r['shift_id']) for r in store.all(
        'SELECT day,shift_id FROM assignments WHERE employee_id=? AND day<?', (e['id'], start.isoformat()))] for e in employees}
    hours = {e['id']: 0.0 for e in employees}
    worked = {e['id']: set() for e in employees}
    unavailable = {e['id']: set(x.strip() for x in (e['unavailable'] or '').split(',') if x.strip()) for e in employees}
    for e in employees:
        for leave in store.all('SELECT start_date,end_date FROM leave_periods WHERE employee_id=? AND start_date<=? AND end_date>=?',
                               (e['id'], end.isoformat(), start.isoformat())):
            d = max(start, date.fromisoformat(leave['start_date']))
            stop = min(end, date.fromisoformat(leave['end_date']))
            while d <= stop:
                unavailable[e['id']].add(d.isoformat())
                d += timedelta(days=1)
    prefs = {e['id']: [p.strip().lower() for p in (e['preferences'] or '').split(',') if p.strip()] for e in employees}
    missing = 0
    for offset in range(last_day):
        day = start + timedelta(days=offset)
        ds = day.isoformat()
        for shift in shifts:
            for _ in range(max(0, int(shift['coverage']))):
                candidates = []
                for e in employees:
                    eid = e['id']
                    if ds in unavailable[eid] or any(old_day == ds for old_day, _ in history[eid]):
                        continue
                    if elig[eid] and shift['id'] not in elig[eid]:
                        continue
                    if day.weekday() >= 5 and any('wochenende vermeiden' in p for p in prefs[eid]):
                        continue
                    week_start = (day - timedelta(days=day.weekday())).isoformat()
                    week_end = (day + timedelta(days=6-day.weekday())).isoformat()
                    days_this_week = sum(week_start <= worked_day <= week_end for worked_day in worked[eid])
                    if days_this_week >= int(e['max_days'] or 5):
                        continue
                    if all((day - timedelta(days=n)).isoformat() in worked[eid] for n in range(1, 6)):
                        continue
                    previous = [(old_day, old_sid) for old_day, old_sid in history[eid]
                                if old_day == (day - timedelta(days=1)).isoformat()]
                    previous_name = next((s['name'] for s in shifts if previous and s['id'] == previous[-1][1]), '')
                    if 'nacht' in previous_name.lower() and ('früh' in shift['name'].lower() or shift['start'] < '09:00'):
                        continue
                    preference_bonus = sum(1 for p in prefs[eid] if p.split()[0] in shift['name'].lower())
                    target_hours = max(1.0, float(e['weekly_hours'] or 38.5) * (last_day / 7))
                    load = hours[eid] / target_hours
                    weekend_penalty = 4 if day.weekday() >= 5 else 0
                    score = load * 100 + len(worked[eid]) * 0.6 + weekend_penalty - preference_bonus * 18 + random.random() * 5
                    candidates.append((score, e))
                if not candidates:
                    missing += 1
                    continue
                e = min(candidates, key=lambda item: item[0])[1]
                store.run('INSERT OR REPLACE INTO assignments(day,employee_id,shift_id) VALUES(?,?,?)', (ds, e['id'], shift['id']))
                hours[e['id']] += float(shift['hours'])
                worked[e['id']].add(ds)
                history[e['id']].append((ds, shift['id']))
    return missing


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.store = Store()
        self.month = date.today().replace(day=1)
        if self.store.demo_seeded_now and not self.store.all('SELECT id FROM assignments LIMIT 1'):
            build_assignments(self.store, self.month)
        self.title(APP_NAME)
        self.geometry('1380x900')
        self.minsize(1050, 700)
        self.configure(bg='#f3f5f9')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame', background='#f3f5f9')
        style.configure('TLabel', background='#f3f5f9', foreground='#172033', font=('Segoe UI', 10))
        style.configure('Title.TLabel', font=('Segoe UI', 22, 'bold'), foreground='#111827')
        style.configure('Sub.TLabel', foreground='#64748b')
        style.configure('TButton', font=('Segoe UI', 10), padding=(10, 7))
        style.configure('Accent.TButton', background='#4f46e5', foreground='white')
        style.map('Accent.TButton', background=[('active', '#4338ca')])
        style.configure('Treeview', font=('Segoe UI', 10), rowheight=34, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('Segoe UI', 9, 'bold'), background='#e9edf5')
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=16, pady=12)
        self.plan_tab = ttk.Frame(self.tabs, padding=16)
        self.emp_tab = ttk.Frame(self.tabs, padding=16)
        self.leave_tab = ttk.Frame(self.tabs, padding=16)
        self.shift_tab = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(self.plan_tab, text='  Dienstplan  ')
        self.tabs.add(self.emp_tab, text='  Mitarbeitende  ')
        self.tabs.add(self.leave_tab, text='  Urlaub  ')
        self.tabs.add(self.shift_tab, text='  Schichten  ')
        self.build_plan()
        self.build_employees()
        self.build_leave()
        self.build_shifts()

    def header(self, parent, title, subtitle):
        ttk.Label(parent, text=title, style='Title.TLabel').pack(anchor='w')
        ttk.Label(parent, text=subtitle, style='Sub.TLabel').pack(anchor='w', pady=(3, 14))

    def build_plan(self):
        p = self.plan_tab
        self.header(p, 'Dienstplan', 'Monatskalender · Klicke auf einen Tag, um die Tagesplanung zu öffnen')
        bar = ttk.Frame(p)
        bar.pack(fill='x', pady=(0, 7))
        ttk.Button(bar, text='‹', command=lambda: self.shift_month(-1)).pack(side='left')
        self.month_label = ttk.Label(bar, text='', font=('Segoe UI', 15, 'bold'))
        self.month_label.pack(side='left', padx=12)
        ttk.Button(bar, text='›', command=lambda: self.shift_month(1)).pack(side='left')
        ttk.Button(bar, text='Monat neu planen', style='Accent.TButton', command=self.generate).pack(side='right', padx=(8, 0))
        ttk.Button(bar, text='CSV exportieren', command=self.export_csv).pack(side='right')
        self.calendar_frame = ttk.Frame(p)
        self.calendar_frame.pack(fill='both', expand=True, pady=(4, 7))
        ttk.Label(p, text='Sa/So und Feiertage sind nur leicht markiert und werden ganz normal mitbesetzt. Feiertage: Baden-Württemberg.',
                  style='Sub.TLabel').pack(anchor='w')
        self.refresh_plan()

    def shift_month(self, offset):
        year, month = self.month.year, self.month.month + offset
        if month < 1:
            year, month = year - 1, 12
        elif month > 12:
            year, month = year + 1, 1
        self.month = date(year, month, 1)
        self.refresh_plan()

    def refresh_plan(self):
        self.month_label.config(text=f'{MONTHS[self.month.month - 1]} {self.month.year}')
        for child in self.calendar_frame.winfo_children():
            child.destroy()
        frame = self.calendar_frame
        for col, name in enumerate(WEEKDAYS):
            ttk.Label(frame, text=name.upper(), anchor='center', font=('Segoe UI', 9, 'bold')).grid(row=0, column=col, sticky='ew', padx=2, pady=(0, 4))
            frame.columnconfigure(col, weight=1, uniform='weekday')
        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(self.month.year, self.month.month)
        for row, week in enumerate(weeks, start=1):
            frame.rowconfigure(row, weight=1, uniform='week', minsize=105)
            for col, day in enumerate(week):
                in_month = day.month == self.month.month
                holiday = bw_holidays(day.year).get(day)
                weekend = day.weekday() >= 5
                bg = '#e8edf5' if weekend or holiday else '#ffffff'
                if not in_month:
                    bg = '#f0f2f6'
                cell = tk.Frame(frame, bg=bg, width=150, height=105, highlightbackground='#d5dbe6', highlightthickness=1, cursor='hand2')
                cell.grid(row=row, column=col, sticky='nsew', padx=2, pady=2)
                cell.grid_propagate(False)
                date_text = str(day.day) if in_month else f'{day.day}'
                fg = '#172033' if in_month else '#a0a8b5'
                top = tk.Frame(cell, bg=bg)
                top.pack(fill='x', padx=6, pady=(3, 1))
                tk.Label(top, text=date_text, bg=bg, fg=fg, font=('Segoe UI', 10, 'bold')).pack(side='left')
                if holiday and in_month:
                    tk.Label(top, text=holiday, bg=bg, fg='#8a5b11', font=('Segoe UI', 8, 'bold')).pack(side='right')
                if in_month:
                    for line, color in self.day_summary(day):
                        tk.Label(cell, text=line, bg=bg, fg=color, font=('Segoe UI', 8), anchor='w', justify='left',
                                 wraplength=150).pack(fill='x', padx=6, pady=0)
                    for widget in (cell, top, *top.winfo_children()):
                        widget.bind('<Double-Button-1>', lambda _event, d=day: self.open_day(d))
                        widget.bind('<Button-1>', lambda _event, d=day: self.open_day(d))

    def day_summary(self, day):
        ds = day.isoformat()
        assignments = self.store.all('''SELECT s.name AS shift, s.coverage, e.name FROM assignments a
            JOIN employees e ON e.id=a.employee_id JOIN shifts s ON s.id=a.shift_id
            WHERE a.day=? ORDER BY s.start,e.name''', (ds,))
        leave = self.store.all('''SELECT e.name FROM leave_periods l JOIN employees e ON e.id=l.employee_id
            WHERE l.start_date<=? AND l.end_date>=? ORDER BY e.name''', (ds, ds))
        shifts = self.store.all('SELECT * FROM shifts ORDER BY start')
        lines = []
        for sh in shifts:
            names = [r['name'].replace('Demo ', '') for r in assignments if r['shift'] == sh['name']]
            lines.append((f"{sh['name'].replace('dienst','').replace('Dienst','')} {len(names)}/{sh['coverage']}: {', '.join(names) if names else '—'}", '#34445d'))
        if leave:
            names = ', '.join(r['name'].replace('Demo ', '') for r in leave)
            lines.append((f'Urlaub: {names}', '#8b5e34'))
        return lines

    def open_day(self, day):
        employees = self.store.all('SELECT * FROM employees WHERE active=1 ORDER BY name')
        shifts = self.store.all('SELECT * FROM shifts ORDER BY start')
        leave_ids = {r['employee_id'] for r in self.store.all('SELECT employee_id FROM leave_periods WHERE start_date<=? AND end_date>=?', (day.isoformat(), day.isoformat()))}
        assigned = {r['employee_id']: r['shift_id'] for r in self.store.all('SELECT employee_id,shift_id FROM assignments WHERE day=?', (day.isoformat(),))}
        win = tk.Toplevel(self)
        win.title(f'Dienstplanung · {day:%d.%m.%Y}')
        win.geometry('570x650')
        win.transient(self)
        win.grab_set()
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=f'{WEEKDAYS[day.weekday()]}, {day:%d.%m.%Y}', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        holiday = bw_holidays(day.year).get(day)
        if holiday:
            ttk.Label(frame, text=f'Feiertag: {holiday} · regulärer Dienstbetrieb', style='Sub.TLabel').pack(anchor='w', pady=(2, 10))
        else:
            ttk.Label(frame, text='Jede Person einzeln auf Frei oder eine Schicht setzen.', style='Sub.TLabel').pack(anchor='w', pady=(2, 10))
        canvas = tk.Canvas(frame, highlightthickness=0, bg='#f3f5f9')
        scroll = ttk.Scrollbar(frame, orient='vertical', command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        vars_by_id = {}
        labels_by_id = {}
        values = {'Frei': None}
        values.update({f"{s['name']} ({s['start']}–{s['end']})": s['id'] for s in shifts})
        by_shift = {v: k for k, v in values.items()}
        for i, e in enumerate(employees):
            ttk.Label(inner, text=e['name'].replace('Demo ', ''), width=24).grid(row=i, column=0, sticky='w', padx=(0, 8), pady=3)
            if e['id'] in leave_ids:
                ttk.Label(inner, text='Urlaub', foreground='#8b5e34').grid(row=i, column=1, sticky='w', pady=3)
                vars_by_id[e['id']] = None
                labels_by_id[e['id']] = 'Urlaub'
                continue
            current = by_shift.get(assigned.get(e['id']), 'Frei')
            var = tk.StringVar(value=current)
            ttk.Combobox(inner, textvariable=var, values=list(values), state='readonly', width=31).grid(row=i, column=1, sticky='ew', pady=3)
            vars_by_id[e['id']] = var
        def save_day():
            for e in employees:
                var = vars_by_id.get(e['id'])
                if var is None:
                    continue
                sid = values[var.get()]
                if sid is None:
                    self.store.run('DELETE FROM assignments WHERE day=? AND employee_id=?', (day.isoformat(), e['id']))
                else:
                    eligible = {r['shift_id'] for r in self.store.all('SELECT shift_id FROM eligibility WHERE employee_id=?', (e['id'],))}
                    if eligible and sid not in eligible:
                        if not messagebox.askyesno('Schicht-Eignung prüfen', f"{e['name']} ist für dieses Schichtmodell nicht als geeignet eingetragen. Trotzdem zuweisen?", parent=win):
                            continue
                    self.store.run('INSERT INTO assignments(day,employee_id,shift_id) VALUES(?,?,?) ON CONFLICT(day,employee_id) DO UPDATE SET shift_id=excluded.shift_id', (day.isoformat(), e['id'], sid))
            win.destroy()
            self.refresh_plan()
        ttk.Button(frame, text='Tagesplan speichern', style='Accent.TButton', command=save_day).pack(side='bottom', anchor='e', pady=(10, 0))

    def generate(self):
        if not self.store.all('SELECT id FROM employees WHERE active=1'):
            messagebox.showinfo('Dienstplan', 'Lege zuerst Mitarbeitende an.')
            return
        if not messagebox.askyesno('Monat neu planen', 'Bestehende Einträge in diesem Monat werden ersetzt. Urlaub bleibt geschützt. Fortfahren?'):
            return
        missing = build_assignments(self.store, self.month, clear_existing=True)
        self.refresh_plan()
        if missing:
            messagebox.showwarning('Plan erstellt – Lücken prüfen', f'Es konnten {missing} Schichtplätze nicht besetzt werden. Prüfe die Mindestbesetzung, Eignungen, Abwesenheiten und Mitarbeitendenzahl.')
        else:
            messagebox.showinfo('Plan erstellt', 'Der Monatsentwurf ist erstellt. Bitte prüfe die Besetzung und Arbeitszeitregeln vor der Freigabe.')

    def export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension='.csv', initialfile=f'Dienstplan_{self.month:%Y_%m}.csv', filetypes=[('CSV-Datei', '*.csv')])
        if not path:
            return
        rows = self.store.all('''SELECT a.day,e.name,e.role,s.name AS shift,s.start,s.end,s.hours FROM assignments a
            JOIN employees e ON e.id=a.employee_id JOIN shifts s ON s.id=a.shift_id WHERE a.day LIKE ? ORDER BY a.day,e.name''',
            (self.month.strftime('%Y-%m') + '-%',))
        import csv
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(['Datum', 'Mitarbeiter', 'Bereich', 'Schicht', 'Beginn', 'Ende', 'Stunden'])
            writer.writerows([tuple(row) for row in rows])
        messagebox.showinfo('Export fertig', f'Der Plan wurde exportiert:\n{path}')

    def build_employees(self):
        p = self.emp_tab
        self.header(p, 'Mitarbeitende', 'Demo-Personen sind Testdaten und können bearbeitet, deaktiviert oder gelöscht werden.')
        bar = ttk.Frame(p)
        bar.pack(fill='x', pady=(0, 10))
        ttk.Button(bar, text='＋ Mitarbeiter hinzufügen', style='Accent.TButton', command=lambda: self.employee_form()).pack(side='left')
        ttk.Button(bar, text='Bearbeiten', command=self.edit_employee).pack(side='left', padx=8)
        ttk.Button(bar, text='Deaktivieren', command=self.deactivate_employee).pack(side='left')
        self.emp_tree = ttk.Treeview(p, columns=('name', 'role', 'hours', 'days', 'prefs', 'active'), show='headings')
        for c, title, width in [('name', 'Name', 210), ('role', 'Bereich / Qualifikation', 200), ('hours', 'Wochenstunden', 115), ('days', 'Max. Tage/Woche', 120), ('prefs', 'Wünsche', 310), ('active', 'Status', 90)]:
            self.emp_tree.heading(c, text=title); self.emp_tree.column(c, width=width, anchor='w')
        self.emp_tree.pack(fill='both', expand=True)
        self.refresh_employees()

    def refresh_employees(self):
        if hasattr(self, 'emp_tree'):
            self.emp_tree.delete(*self.emp_tree.get_children())
            for e in self.store.all('SELECT * FROM employees ORDER BY active DESC,name'):
                self.emp_tree.insert('', 'end', iid=str(e['id']), values=(e['name'], e['role'], e['weekly_hours'], e['max_days'], e['preferences'], 'Aktiv' if e['active'] else 'Inaktiv'))
        if hasattr(self, 'leave_tree'):
            self.refresh_leave()
        if hasattr(self, 'calendar_frame'):
            self.refresh_plan()

    def employee_form(self, e=None):
        win = tk.Toplevel(self); win.title('Mitarbeiter bearbeiten' if e else 'Mitarbeiter hinzufügen'); win.transient(self); win.grab_set()
        f = ttk.Frame(win, padding=18); f.pack(fill='both', expand=True)
        defaults = {'name': '', 'role': '', 'weekly_hours': '38.5', 'max_days': '5', 'preferences': '', 'unavailable': ''}
        vars_ = {k: tk.StringVar(value=str(e[k] if e else value)) for k, value in defaults.items()}
        labels = [('name', 'Name'), ('role', 'Bereich / Qualifikation'), ('weekly_hours', 'Vertragsstunden pro Woche'), ('max_days', 'Max. Arbeitstage pro Woche'), ('preferences', 'Wünsche (z. B. Frühdienst bevorzugt, Wochenende vermeiden)'), ('unavailable', 'Einzelne Abwesenheitstage (YYYY-MM-DD, kommasepariert)')]
        for i, (key, label) in enumerate(labels):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky='w', pady=6)
            ttk.Entry(f, textvariable=vars_[key], width=56).grid(row=i, column=1, sticky='ew', pady=6)
        ttk.Label(f, text='Einsetzbare Schichten').grid(row=6, column=0, sticky='nw', pady=8)
        shifts = self.store.all('SELECT * FROM shifts ORDER BY start')
        chosen = {r['shift_id'] for r in self.store.all('SELECT shift_id FROM eligibility WHERE employee_id=?', (e['id'],))} if e else set()
        checks = []
        box = ttk.Frame(f); box.grid(row=6, column=1, sticky='w')
        for shift in shifts:
            var = tk.BooleanVar(value=(not chosen or shift['id'] in chosen))
            ttk.Checkbutton(box, text=shift['name'], variable=var).pack(anchor='w')
            checks.append((shift['id'], var))
        def save():
            if not vars_['name'].get().strip():
                messagebox.showerror('Eingabe prüfen', 'Bitte einen Namen eingeben.', parent=win); return
            try:
                hours, max_days = float(vars_['weekly_hours'].get()), int(vars_['max_days'].get())
            except ValueError:
                messagebox.showerror('Eingabe prüfen', 'Stunden und Arbeitstage müssen Zahlen sein.', parent=win); return
            values = (vars_['name'].get().strip(), vars_['role'].get().strip(), hours, max_days, vars_['preferences'].get().strip(), vars_['unavailable'].get().strip())
            if e:
                self.store.run('UPDATE employees SET name=?,role=?,weekly_hours=?,max_days=?,preferences=?,unavailable=? WHERE id=?', (*values, e['id']))
                eid = e['id']
            else:
                self.store.run('INSERT INTO employees(name,role,weekly_hours,max_days,preferences,unavailable) VALUES(?,?,?,?,?,?)', values)
                eid = self.store.db.execute('SELECT last_insert_rowid()').fetchone()[0]; self.store.db.commit()
            self.store.run('DELETE FROM eligibility WHERE employee_id=?', (eid,))
            for sid, var in checks:
                if var.get(): self.store.run('INSERT INTO eligibility(employee_id,shift_id) VALUES(?,?)', (eid, sid))
            win.destroy(); self.refresh_employees()
        ttk.Button(f, text='Speichern', style='Accent.TButton', command=save).grid(row=7, column=1, sticky='e', pady=(14, 0))
        f.columnconfigure(1, weight=1)

    def edit_employee(self):
        selected = self.emp_tree.selection()
        if not selected:
            messagebox.showinfo('Mitarbeitende', 'Bitte zuerst eine Person auswählen.'); return
        employee = self.store.all('SELECT * FROM employees WHERE id=?', (int(selected[0]),))[0]
        self.employee_form(employee)

    def deactivate_employee(self):
        selected = self.emp_tree.selection()
        if not selected: return
        if messagebox.askyesno('Deaktivieren', 'Mitarbeiter deaktivieren? Plandaten bleiben erhalten.'):
            self.store.run('UPDATE employees SET active=0 WHERE id=?', (int(selected[0]),)); self.refresh_employees()

    def build_leave(self):
        p = self.leave_tab
        self.header(p, 'Urlaubsplanung', 'Urlaubszeiträume eintragen; der automatische Monatsplan berücksichtigt diese Tage.')
        bar = ttk.Frame(p); bar.pack(fill='x', pady=(0, 10))
        ttk.Button(bar, text='＋ Urlaub eintragen', style='Accent.TButton', command=self.leave_form).pack(side='left')
        ttk.Button(bar, text='Urlaub löschen', command=self.delete_leave).pack(side='left', padx=8)
        self.leave_tree = ttk.Treeview(p, columns=('name', 'start', 'end', 'days', 'note'), show='headings')
        for c, title, width in [('name', 'Mitarbeiter', 220), ('start', 'Von', 120), ('end', 'Bis', 120), ('days', 'Kalendertage', 120), ('note', 'Notiz', 300)]:
            self.leave_tree.heading(c, text=title); self.leave_tree.column(c, width=width, anchor='w')
        self.leave_tree.pack(fill='both', expand=True)
        self.refresh_leave()

    def refresh_leave(self):
        if not hasattr(self, 'leave_tree'): return
        self.leave_tree.delete(*self.leave_tree.get_children())
        for leave in self.store.all('''SELECT l.*,e.name FROM leave_periods l JOIN employees e ON e.id=l.employee_id ORDER BY l.start_date,e.name'''):
            days = (date.fromisoformat(leave['end_date']) - date.fromisoformat(leave['start_date'])).days + 1
            self.leave_tree.insert('', 'end', iid=str(leave['id']), values=(leave['name'], leave['start_date'], leave['end_date'], days, leave['note']))

    def leave_form(self):
        employees = self.store.all('SELECT id,name FROM employees WHERE active=1 ORDER BY name')
        if not employees:
            messagebox.showinfo('Urlaub', 'Lege zuerst Mitarbeitende an.'); return
        win = tk.Toplevel(self); win.title('Urlaub eintragen'); win.transient(self); win.grab_set()
        f = ttk.Frame(win, padding=18); f.pack(fill='both', expand=True)
        ttk.Label(f, text='Mitarbeiter').grid(row=0, column=0, sticky='w', pady=6)
        names = {f"{e['name']} (ID {e['id']})": e['id'] for e in employees}
        who = tk.StringVar(value=next(iter(names)))
        ttk.Combobox(f, textvariable=who, values=list(names), state='readonly', width=37).grid(row=0, column=1, pady=6)
        vals = {'start': date.today().replace(day=1).isoformat(), 'end': date.today().replace(day=1).isoformat(), 'note': ''}
        vars_ = {key: tk.StringVar(value=value) for key, value in vals.items()}
        for row, (key, label) in enumerate([('start', 'Von (YYYY-MM-DD)'), ('end', 'Bis (YYYY-MM-DD)'), ('note', 'Notiz')], start=1):
            ttk.Label(f, text=label).grid(row=row, column=0, sticky='w', pady=6)
            ttk.Entry(f, textvariable=vars_[key], width=40).grid(row=row, column=1, pady=6)
        def save():
            try:
                first, last = date.fromisoformat(vars_['start'].get()), date.fromisoformat(vars_['end'].get())
                if last < first: raise ValueError
            except ValueError:
                messagebox.showerror('Datum prüfen', 'Bitte gültige Daten im Format YYYY-MM-DD eingeben; Bis darf nicht vor Von liegen.', parent=win); return
            eid = names[who.get()]
            self.store.run('INSERT INTO leave_periods(employee_id,start_date,end_date,note) VALUES(?,?,?,?)',
                           (eid, first.isoformat(), last.isoformat(), vars_['note'].get().strip()))
            self.store.run('DELETE FROM assignments WHERE employee_id=? AND day BETWEEN ? AND ?',
                           (eid, first.isoformat(), last.isoformat()))
            win.destroy(); self.refresh_leave(); self.refresh_plan()
        ttk.Button(f, text='Urlaub speichern', style='Accent.TButton', command=save).grid(row=4, column=1, sticky='e', pady=(12, 0))

    def delete_leave(self):
        selected = self.leave_tree.selection()
        if selected and messagebox.askyesno('Urlaub löschen', 'Ausgewählten Urlaubszeitraum löschen?'):
            self.store.run('DELETE FROM leave_periods WHERE id=?', (int(selected[0]),)); self.refresh_leave(); self.refresh_plan()

    def build_shifts(self):
        p = self.shift_tab
        self.header(p, 'Schichtmodelle', 'Zeiten, Stunden und tägliche Mindestbesetzung einstellen.')
        bar = ttk.Frame(p); bar.pack(fill='x', pady=(0, 10))
        ttk.Button(bar, text='＋ Schicht hinzufügen', style='Accent.TButton', command=lambda: self.shift_form()).pack(side='left')
        ttk.Button(bar, text='Bearbeiten', command=self.edit_shift).pack(side='left', padx=8)
        ttk.Button(bar, text='Löschen', command=self.delete_shift).pack(side='left')
        self.shift_tree = ttk.Treeview(p, columns=('name', 'time', 'hours', 'coverage'), show='headings')
        for c, title, width in [('name', 'Schicht', 220), ('time', 'Arbeitszeit', 220), ('hours', 'Anrechenbare Stunden', 180), ('coverage', 'Mindestbesetzung pro Tag', 220)]:
            self.shift_tree.heading(c, text=title); self.shift_tree.column(c, width=width, anchor='w')
        self.shift_tree.pack(fill='both', expand=True)
        self.refresh_shifts()

    def refresh_shifts(self):
        if not hasattr(self, 'shift_tree'): return
        self.shift_tree.delete(*self.shift_tree.get_children())
        for shift in self.store.all('SELECT * FROM shifts ORDER BY start'):
            self.shift_tree.insert('', 'end', iid=str(shift['id']), values=(shift['name'], f"{shift['start']}–{shift['end']}", shift['hours'], shift['coverage']))

    def shift_form(self, shift=None):
        win = tk.Toplevel(self); win.title('Schichtmodell'); win.transient(self); win.grab_set()
        f = ttk.Frame(win, padding=18); f.pack(fill='both', expand=True)
        data = {'name': shift['name'] if shift else '', 'start': shift['start'] if shift else '06:00',
                'end': shift['end'] if shift else '14:00', 'hours': str(shift['hours'] if shift else 8),
                'coverage': str(shift['coverage'] if shift else 1), 'color': shift['color'] if shift else '#4f46e5'}
        vars_ = {key: tk.StringVar(value=value) for key, value in data.items()}
        fields = [('name', 'Name'), ('start', 'Beginn (HH:MM)'), ('end', 'Ende (HH:MM)'), ('hours', 'Stunden'), ('coverage', 'Mindestbesetzung pro Tag'), ('color', 'Farbe (Hex)')]
        for i, (key, title) in enumerate(fields):
            ttk.Label(f, text=title).grid(row=i, column=0, sticky='w', pady=6)
            ttk.Entry(f, textvariable=vars_[key], width=32).grid(row=i, column=1, pady=6)
        def save():
            try:
                datetime.strptime(vars_['start'].get(), '%H:%M'); datetime.strptime(vars_['end'].get(), '%H:%M')
                hours, coverage = float(vars_['hours'].get()), int(vars_['coverage'].get())
                if coverage < 0 or hours <= 0: raise ValueError
            except ValueError:
                messagebox.showerror('Eingabe prüfen', 'Bitte gültige Zeiten (HH:MM), Stunden und eine Mindestbesetzung ab 0 eingeben.', parent=win); return
            values = (vars_['name'].get().strip(), vars_['start'].get(), vars_['end'].get(), hours, vars_['color'].get().strip(), coverage)
            if shift:
                self.store.run('UPDATE shifts SET name=?,start=?,end=?,hours=?,color=?,coverage=? WHERE id=?', (*values, shift['id']))
            else:
                self.store.run('INSERT INTO shifts(name,start,end,hours,color,coverage) VALUES(?,?,?,?,?,?)', values)
            win.destroy(); self.refresh_shifts(); self.refresh_employees(); self.refresh_plan()
        ttk.Button(f, text='Speichern', style='Accent.TButton', command=save).grid(row=6, column=1, sticky='e', pady=12)

    def edit_shift(self):
        selected = self.shift_tree.selection()
        if selected:
            shift = self.store.all('SELECT * FROM shifts WHERE id=?', (int(selected[0]),))[0]
            self.shift_form(shift)

    def delete_shift(self):
        selected = self.shift_tree.selection()
        if selected and messagebox.askyesno('Löschen', 'Schichtmodell und zugehörige Zuweisungen löschen?'):
            self.store.run('DELETE FROM shifts WHERE id=?', (int(selected[0]),)); self.refresh_shifts(); self.refresh_employees(); self.refresh_plan()


if __name__ == '__main__':
    App().mainloop()
