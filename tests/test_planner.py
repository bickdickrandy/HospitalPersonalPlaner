"""Tests für Kalenderregeln, Migration, Demo-Initialisierung, Generator und Tauschassistent."""
import os
import random
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta

import planner_core as pc

LEGACY_SCHEMA = '''
CREATE TABLE employees(id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT DEFAULT '', weekly_hours REAL DEFAULT 38.5,
    max_days INTEGER DEFAULT 5, active INTEGER DEFAULT 1, notes TEXT DEFAULT '', preferences TEXT DEFAULT '',
    unavailable TEXT DEFAULT '');
CREATE TABLE shifts(id INTEGER PRIMARY KEY, name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
    hours REAL NOT NULL, color TEXT DEFAULT '#4f46e5', coverage INTEGER DEFAULT 1);
CREATE TABLE eligibility(employee_id INTEGER, shift_id INTEGER, PRIMARY KEY(employee_id,shift_id));
CREATE TABLE assignments(id INTEGER PRIMARY KEY, day TEXT NOT NULL, employee_id INTEGER NOT NULL, shift_id INTEGER,
    UNIQUE(day,employee_id));
CREATE TABLE leave_periods(id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL, start_date TEXT NOT NULL,
    end_date TEXT NOT NULL, note TEXT DEFAULT '');
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''


class TempStoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'data', 'test.db')
        self.store = pc.Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def blank(self, shifts=(('Frühdienst', '06:00', '14:00', 1), ('Spätdienst', '14:00', '22:00', 1),
                            ('Nachtdienst', '22:00', '06:00', 1))):
        """Leere, kontrollierte Ausgangslage ohne Demo-Personen."""
        with self.store.db:
            self.store.db.execute('DELETE FROM employees')
            self.store.db.execute('DELETE FROM shifts')
            self.store.db.execute('DELETE FROM leave_periods')
        ids = {}
        for name, start, end, coverage in shifts:
            cur = self.store.db.execute('INSERT INTO shifts(name,start,end,hours,coverage) VALUES(?,?,?,?,?)',
                                        (name, start, end, 8, coverage))
            ids[name[:4].lower()] = cur.lastrowid
        self.store.db.commit()
        return ids

    def person(self, name, eligible=None, max_days=5, prefs='', unavailable=''):
        cur = self.store.db.execute('INSERT INTO employees(name,role,max_days,preferences,unavailable) '
                                    'VALUES(?,?,?,?,?)', (name, 'Pflegefachkraft', max_days, prefs, unavailable))
        for sid in eligible or []:
            self.store.db.execute('INSERT INTO eligibility(employee_id,shift_id) VALUES(?,?)', (cur.lastrowid, sid))
        self.store.db.commit()
        return cur.lastrowid

    def assign(self, eid, day, sid):
        self.store.run('INSERT INTO assignments(day,employee_id,shift_id) VALUES(?,?,?)', (day.isoformat(), eid, sid))

    def ctx(self, month=date(2026, 10, 1)):
        return pc.PlanContext.for_month(self.store, month)


class CalendarTests(unittest.TestCase):
    def test_month_grid_contains_neighbour_days(self):
        weeks = pc.month_grid(date(2026, 10, 1))
        self.assertEqual(weeks[0][0], date(2026, 9, 28))  # Montag aus dem Vormonat
        self.assertTrue(all(len(w) == 7 for w in weeks))
        self.assertEqual(weeks[-1][-1], date(2026, 11, 1))

    def test_holidays_and_weekends(self):
        self.assertEqual(pc.holiday_name(date(2026, 10, 3)), 'Tag der Deutschen Einheit')
        self.assertEqual(pc.holiday_name(date(2026, 11, 1)), 'Allerheiligen')
        self.assertEqual(pc.holiday_name(date(2027, 3, 26)), 'Karfreitag')
        self.assertIsNone(pc.holiday_name(date(2026, 10, 5)))
        self.assertTrue(pc.is_weekend(date(2026, 10, 3)))
        self.assertFalse(pc.is_weekend(date(2026, 10, 5)))

    def test_add_months_across_year(self):
        self.assertEqual(pc.add_months(date(2026, 12, 1), 1), date(2027, 1, 1))
        self.assertEqual(pc.add_months(date(2026, 1, 1), -1), date(2025, 12, 1))

    def test_chip_labels_are_unique(self):
        emps = [pc.Employee(i, n, '', 38.5, 5, True, '', frozenset(), False)
                for i, n in enumerate(['Demo Alpha', 'Demo Alpha Zwei', 'Alma', 'Alpha'], start=1)]
        labels = pc.chip_labels(emps)
        self.assertEqual(len(set(labels.values())), len(emps))
        self.assertEqual(labels[1], 'AL')


class DatabaseInitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_database_gets_marked_demo_staff_once(self):
        path = os.path.join(self.temp.name, 'fresh.db')
        store = pc.Store(path)
        try:
            self.assertTrue(store.demo_seeded_now)
            rows = store.all('SELECT name,is_demo,notes FROM employees')
            self.assertEqual(len(rows), len(pc.DEMO_STAFF))
            self.assertTrue(all(r['is_demo'] == 1 and r['name'].startswith('Demo ') for r in rows))
            self.assertEqual(store.schema_version(), pc.SCHEMA_VERSION)
        finally:
            store.close()
        again = pc.Store(path)
        try:
            self.assertFalse(again.demo_seeded_now)
            self.assertEqual(again.db.execute('SELECT COUNT(*) FROM employees').fetchone()[0], len(pc.DEMO_STAFF))
        finally:
            again.close()

    def test_no_demo_data_when_staff_exists_and_existing_plan_untouched(self):
        path = os.path.join(self.temp.name, 'legacy.db')
        db = sqlite3.connect(path)
        db.executescript(LEGACY_SCHEMA)
        db.execute("INSERT INTO employees(name) VALUES('Station A Person 1')")
        db.execute("INSERT INTO shifts(name,start,end,hours,coverage) VALUES('Frühdienst','06:00','14:00',8,1)")
        db.execute("INSERT INTO assignments(day,employee_id,shift_id) VALUES('2026-10-05',1,1)")
        db.commit()
        db.close()
        store = pc.Store(path)
        try:
            self.assertFalse(store.demo_seeded_now)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM employees').fetchone()[0], 1)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM assignments').fetchone()[0], 1)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM shifts').fetchone()[0], 1)
        finally:
            store.close()

    def test_migration_of_legacy_database_is_safe_and_repeatable(self):
        path = os.path.join(self.temp.name, 'data', 'dienstplan.db')
        os.makedirs(os.path.dirname(path))
        db = sqlite3.connect(path)
        db.executescript(LEGACY_SCHEMA)
        db.execute("INSERT INTO employees(name) VALUES('Demo Altperson')")
        db.execute("INSERT INTO employees(name) VALUES('Echte Person')")
        db.execute("INSERT INTO settings(key,value) VALUES('demo_staff_seeded','1')")
        db.commit()
        db.close()
        store = pc.Store(path)
        try:
            self.assertEqual(store.schema_version(), pc.SCHEMA_VERSION)
            self.assertTrue(store.backup_path and os.path.exists(store.backup_path))
            flags = dict(store.db.execute('SELECT name,is_demo FROM employees').fetchall())
            self.assertEqual(flags, {'Demo Altperson': 1, 'Echte Person': 0})
            self.assertEqual(store.setting('min_rest_hours'), '11')
        finally:
            store.close()
        backup = sqlite3.connect(store.backup_path)
        self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 0)  # Sicherung = Altstand
        backup.close()
        again = pc.Store(path)
        try:
            self.assertIsNone(again.backup_path)  # keine weitere Migration nötig
            self.assertEqual(again.db.execute('SELECT COUNT(*) FROM employees').fetchone()[0], 2)
        finally:
            again.close()


class GeneratorTests(TempStoreCase):
    def test_weekends_and_holidays_are_staffed(self):
        result = pc.generate_month(self.store, date(2026, 10, 1), clear_existing=True, rng=random.Random(7))
        self.assertEqual(result.missing, 0)
        ctx = self.ctx()
        for day in (date(2026, 10, 3), date(2026, 10, 4), date(2026, 10, 31)):  # Feiertag, Sonntag, Samstag
            for shift in ctx.shift_list:
                self.assertGreaterEqual(len(ctx.staff_on(day, shift.id)), shift.coverage, (day, shift.name))

    def test_generated_plan_respects_rules(self):
        pc.generate_month(self.store, date(2026, 10, 1), clear_existing=True, rng=random.Random(11))
        overview = pc.month_overview(self.ctx())
        self.assertEqual(overview.warnings, [])

    def test_absence_days_and_leave_respected(self):
        s = self.blank()
        a = self.person('Testperson A', unavailable='2026-10-07')
        b = self.person('Testperson B')
        for i in range(4):
            self.person(f'Testperson {i}')
        pc.add_leave(self.store, b, date(2026, 10, 12), date(2026, 10, 18))
        pc.generate_month(self.store, date(2026, 10, 1), rng=random.Random(1))
        ctx = self.ctx()
        self.assertIsNone(ctx.shift_of(a, date(2026, 10, 7)))
        self.assertTrue(all(ctx.shift_of(b, date(2026, 10, d)) is None for d in range(12, 19)))

    def test_open_slots_are_counted(self):
        s = self.blank()
        self.person('Einzige Person', eligible=[s['früh']])
        result = pc.generate_month(self.store, date(2026, 10, 1), rng=random.Random(2))
        # Spät- und Nachtdienst kann niemand übernehmen; Frühdienst max. 5 Tage/Woche.
        self.assertGreaterEqual(result.missing, 62)
        self.assertTrue(any('Schichteignung' in slot.reason for slot in result.open_slots))
        self.assertEqual(pc.month_overview(self.ctx()).open_total, result.missing)

    def test_fill_mode_keeps_existing_entries(self):
        s = self.blank()
        a = self.person('Person A')
        for i in range(6):
            self.person(f'Person {i}')
        self.assign(a, date(2026, 10, 5), s['nach'])
        pc.generate_month(self.store, date(2026, 10, 1), clear_existing=False, rng=random.Random(4))
        self.assertEqual(self.ctx().shift_of(a, date(2026, 10, 5)), s['nach'])

    def test_rest_time_blocks_late_then_early(self):
        s = self.blank()
        a = self.person('Person A')
        self.assign(a, date(2026, 10, 5), s['spät'])
        checks = self.ctx().check_assignment(a, date(2026, 10, 6), s['früh'])
        self.assertTrue(any(c.rule == 'Ruhezeit' and c.blocking for c in checks))
        checks = self.ctx().check_assignment(a, date(2026, 10, 6), s['spät'])
        self.assertFalse(any(c.blocking for c in checks))


class SwapTests(TempStoreCase):
    def setUp(self):
        super().setUp()
        self.s = self.blank()
        self.day = date(2026, 10, 14)  # Mittwoch

    def option(self, options, kind, b_id, b_day=None):
        return next(o for o in options if o.kind == kind and o.b_id == b_id and (b_day is None or o.b_day == b_day))

    def test_valid_same_day_swap_is_suggested_and_highlighted(self):
        a = self.person('Person A')
        b = self.person('Person B')
        self.assign(a, self.day, self.s['früh'])
        self.assign(b, self.day, self.s['spät'])
        options = pc.find_swap_options(self.ctx(), a, self.day)
        opt = self.option(options, 'same_day', b)
        self.assertTrue(opt.valid, [c.message for c in opt.blockers])
        self.assertIn((b, self.day.isoformat()), pc.partner_highlights(options))
        before_after = opt.before_after(self.ctx(), a)
        self.assertEqual(before_after[0][1].split()[0], 'Früh')
        self.assertEqual(before_after[0][2].split()[0], 'Spät')

    def test_ineligible_partner_is_rejected(self):
        a = self.person('Person A')
        b = self.person('Person B', eligible=[self.s['spät']])
        self.assign(a, self.day, self.s['früh'])
        self.assign(b, self.day, self.s['spät'])
        opt = self.option(pc.find_swap_options(self.ctx(), a, self.day), 'same_day', b)
        self.assertFalse(opt.valid)
        self.assertIn('Schichteignung', {c.rule for c in opt.blockers})

    def test_partner_on_leave_cannot_take_over(self):
        a = self.person('Person A')
        b = self.person('Person B')
        self.assign(a, self.day, self.s['früh'])
        pc.add_leave(self.store, b, self.day, self.day)
        opt = self.option(pc.find_swap_options(self.ctx(), a, self.day), 'takeover', b)
        self.assertFalse(opt.valid)
        self.assertIn('Urlaub', {c.rule for c in opt.blockers})

    def test_absence_day_blocks_swap(self):
        a = self.person('Person A')
        b = self.person('Person B', unavailable=self.day.isoformat())
        self.assign(a, self.day, self.s['früh'])
        opt = self.option(pc.find_swap_options(self.ctx(), a, self.day), 'takeover', b)
        self.assertIn('Abwesenheit', {c.rule for c in opt.blockers})

    def test_cross_day_swap_rejects_double_shift(self):
        a = self.person('Person A')
        b = self.person('Person B')
        other = self.day + timedelta(days=2)
        self.assign(a, self.day, self.s['früh'])
        self.assign(a, other, self.s['früh'])
        self.assign(b, other, self.s['spät'])
        opt = self.option(pc.find_swap_options(self.ctx(), a, self.day), 'cross_day', b, other)
        self.assertFalse(opt.valid)
        self.assertIn('Doppelte Schicht', {c.rule for c in opt.blockers})

    def test_rest_time_violation_rejected(self):
        a = self.person('Person A')
        b = self.person('Person B')
        self.assign(a, self.day, self.s['früh'])
        self.assign(b, self.day, self.s['spät'])
        self.assign(b, self.day + timedelta(days=1), self.s['früh'])
        self.assign(a, self.day - timedelta(days=1), self.s['nach'])  # A: Nacht am Vortag -> Früh = 0 h Ruhe
        options = pc.find_swap_options(self.ctx(), a, self.day)
        opt = self.option(options, 'same_day', b)
        # A bekäme Spätdienst nach Nachtdienst (8 h Ruhe) -> nicht zulässig
        self.assertFalse(opt.valid)
        self.assertIn('Ruhezeit', {c.rule for c in opt.blockers})

    def test_max_days_and_consecutive_days(self):
        a = self.person('Person A')
        b = self.person('Person B', max_days=3)
        self.assign(a, self.day, self.s['früh'])
        for d in (12, 13, 15):  # Mo, Di, Do bereits gearbeitet
            self.assign(b, date(2026, 10, d), self.s['früh'])
        opt = self.option(pc.find_swap_options(self.ctx(), a, self.day), 'takeover', b)
        rules = {c.rule for c in opt.blockers}
        self.assertIn('Max. Arbeitstage', rules)
        self.store.set_setting('max_consecutive_days', 3)
        self.store.run('UPDATE employees SET max_days=7 WHERE id=?', (b,))
        opt = self.option(pc.find_swap_options(self.ctx(), a, self.day), 'takeover', b)
        self.assertIn('Folgetage', {c.rule for c in opt.blockers})

    def test_coverage_after_swap_checked(self):
        s = self.blank(shifts=(('Frühdienst', '06:00', '14:00', 1), ('Spätdienst', '14:00', '22:00', 1)))
        a = self.person('Person A')
        b = self.person('Person B')
        c = self.person('Person C')
        self.assign(a, self.day, s['früh'])
        self.assign(b, self.day, s['spät'])
        self.assign(b, self.day + timedelta(days=1), s['spät'])
        self.assign(a, self.day + timedelta(days=1), s['spät'])  # A arbeitet am Folgetag schon
        opt = pc.evaluate_swap(self.ctx(), 'cross_day', a, self.day, b, self.day + timedelta(days=1))
        self.assertIn('Mindestbesetzung', {x.rule for x in opt.blockers})

    def test_no_partners_gives_explanation(self):
        a = self.person('Person A')
        self.assign(a, self.day, self.s['früh'])
        ctx = self.ctx()
        options = pc.find_swap_options(ctx, a, self.day)
        self.assertEqual(options, [])
        self.assertIn('keine weiteren aktiven Mitarbeitenden', pc.explain_no_options(ctx, options))
        b = self.person('Person B', eligible=[self.s['nach']])
        ctx = self.ctx()
        options = pc.find_swap_options(ctx, a, self.day)
        self.assertFalse(any(o.valid for o in options))
        self.assertIn('Schichteignung', pc.explain_no_options(ctx, options))

    def test_apply_and_undo_swap(self):
        a = self.person('Person A')
        b = self.person('Person B')
        self.assign(a, self.day, self.s['früh'])
        self.assign(b, self.day, self.s['spät'])
        opt = self.option(pc.find_swap_options(self.ctx(), a, self.day), 'same_day', b)
        pc.apply_swap(self.store, opt)
        ctx = self.ctx()
        self.assertEqual(ctx.shift_of(a, self.day), self.s['spät'])
        self.assertEqual(ctx.shift_of(b, self.day), self.s['früh'])
        pc.undo_last_swap(self.store)
        ctx = self.ctx()
        self.assertEqual(ctx.shift_of(a, self.day), self.s['früh'])
        self.assertEqual(ctx.shift_of(b, self.day), self.s['spät'])
        self.assertIsNone(pc.last_undoable_swap(self.store))

    def test_stale_swap_and_unsafe_undo_are_refused(self):
        a = self.person('Person A')
        b = self.person('Person B')
        self.assign(a, self.day, self.s['früh'])
        self.assign(b, self.day, self.s['spät'])
        opt = self.option(pc.find_swap_options(self.ctx(), a, self.day), 'same_day', b)
        self.store.run('UPDATE assignments SET shift_id=? WHERE employee_id=?', (self.s['nach'], b))
        with self.assertRaises(pc.StaleSwapError):
            pc.apply_swap(self.store, opt)
        self.store.run('UPDATE assignments SET shift_id=? WHERE employee_id=?', (self.s['spät'], b))
        pc.apply_swap(self.store, opt)
        self.store.run('DELETE FROM assignments WHERE employee_id=?', (a,))  # spätere manuelle Änderung
        with self.assertRaises(pc.StaleSwapError):
            pc.undo_last_swap(self.store)
        self.assertEqual(self.ctx().shift_of(b, self.day), self.s['früh'])  # nichts überschrieben

    def test_finding_options_changes_nothing(self):
        a = self.person('Person A')
        b = self.person('Person B')
        self.assign(a, self.day, self.s['früh'])
        self.assign(b, self.day, self.s['spät'])
        before = self.store.all('SELECT day,employee_id,shift_id FROM assignments ORDER BY id')
        pc.find_swap_options(self.ctx(), a, self.day)
        after = self.store.all('SELECT day,employee_id,shift_id FROM assignments ORDER BY id')
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in after])


class LeaveAndDayTests(TempStoreCase):
    def test_leave_removes_conflicting_assignments_and_reports_count(self):
        s = self.blank()
        a = self.person('Person A')
        self.assign(a, date(2026, 10, 5), s['früh'])
        self.assign(a, date(2026, 10, 9), s['früh'])
        self.assertEqual(len(pc.assignments_in_range(self.store, a, date(2026, 10, 5), date(2026, 10, 7))), 1)
        removed = pc.add_leave(self.store, a, date(2026, 10, 5), date(2026, 10, 7), 'Test')
        self.assertEqual(removed, 1)
        self.assertEqual(self.ctx().shift_of(a, date(2026, 10, 9)), s['früh'])
        self.assertEqual(len(pc.overlapping_leave(self.store, a, date(2026, 10, 7), date(2026, 10, 8))), 1)
        with self.assertRaises(ValueError):
            pc.add_leave(self.store, a, date(2026, 10, 9), date(2026, 10, 8))

    def test_day_preview_reports_problems(self):
        s = self.blank()
        a = self.person('Person A', eligible=[s['früh']])
        issues = pc.preview_day_changes(self.ctx(), date(2026, 10, 10), {a: s['nach']})
        self.assertIn('Schichteignung', {c.rule for c in issues[a]})
        self.assertIn('coverage', issues)

    def test_csv_export(self):
        s = self.blank()
        a = self.person('Person A')
        self.assign(a, date(2026, 10, 3), s['früh'])
        path = os.path.join(self.temp.name, 'out.csv')
        self.assertEqual(pc.export_csv(self.store, date(2026, 10, 1), path), 1)
        with open(path, encoding='utf-8-sig') as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], 'Datum;Mitarbeiter;Bereich;Schicht;Beginn;Ende;Stunden')
        self.assertTrue(lines[1].startswith('2026-10-03;Person A'))


class ClickSwapTests(TempStoreCase):
    """Zwei Namenschips nacheinander anklicken: swap_between prüft, speichert aber nichts."""

    def setUp(self):
        super().setUp()
        self.s = self.blank()
        self.day = date(2026, 10, 14)

    def test_same_day_click_swap_valid(self):
        a, b = self.person('Person A'), self.person('Person B')
        self.assign(a, self.day, self.s['früh'])
        self.assign(b, self.day, self.s['spät'])
        opt = pc.swap_between(self.ctx(), a, self.day, b, self.day)
        self.assertEqual(opt.kind, 'same_day')
        self.assertTrue(opt.valid)
        self.assertEqual(self.ctx().shift_of(a, self.day), self.s['früh'])  # nichts gespeichert

    def test_cross_day_click_swap_outside_suggestion_window(self):
        a, b = self.person('Person A'), self.person('Person B')
        self.assign(a, date(2026, 10, 2), self.s['früh'])
        self.assign(b, date(2026, 10, 27), self.s['spät'])
        opt = pc.swap_between(self.ctx(), a, date(2026, 10, 2), b, date(2026, 10, 27))
        self.assertEqual(opt.kind, 'cross_day')
        self.assertTrue(opt.valid, [c.message for c in opt.blockers])

    def test_click_swap_rejects_incompatible_partner(self):
        a, b = self.person('Person A'), self.person('Person B', eligible=[self.s['spät']])
        self.assign(a, self.day, self.s['früh'])
        self.assign(b, self.day, self.s['spät'])
        opt = pc.swap_between(self.ctx(), a, self.day, b, self.day)
        self.assertFalse(opt.valid)
        self.assertIn('Schichteignung', {c.rule for c in opt.blockers})

    def test_meaningless_clicks_raise(self):
        a, b = self.person('Person A'), self.person('Person B')
        self.assign(a, self.day, self.s['früh'])
        self.assign(b, self.day, self.s['früh'])
        with self.assertRaises(ValueError):
            pc.swap_between(self.ctx(), a, self.day, b, self.day)  # gleiche Schicht
        with self.assertRaises(ValueError):
            pc.swap_between(self.ctx(), a, self.day, a, self.day + timedelta(days=1))  # gleiche Person
        with self.assertRaises(ValueError):
            pc.swap_between(self.ctx(), a, self.day, b, self.day + timedelta(days=1))  # B hat dort keine Schicht

    def test_swap_delay_setting_default(self):
        self.assertEqual(self.store.setting('swap_delay_seconds'), '5')


class HoursAndFairnessTests(TempStoreCase):
    """Soll-Stunden, Zusatzdienste (mehrere Personen pro Schicht) und faire Verteilung."""

    def test_target_hours_credit_leave(self):
        s = self.blank()
        a = self.person('Person A')
        self.store.run('UPDATE employees SET weekly_hours=35 WHERE id=?', (a,))
        self.assertAlmostEqual(self.ctx().target_hours(a), 155.0)  # 35/7*31
        pc.add_leave(self.store, a, date(2026, 10, 5), date(2026, 10, 11))
        self.assertAlmostEqual(self.ctx().target_hours(a), 120.0)  # 7 Urlaubstage angerechnet

    def test_demo_month_everyone_meets_target_and_fair(self):
        result = pc.generate_month(self.store, date(2026, 10, 1), clear_existing=True, rng=random.Random(9))
        self.assertEqual(result.missing, 0)
        self.assertGreater(result.extra, 0)  # Zusatzdienste über der Mindestbesetzung
        rows = pc.fairness_report(self.ctx())
        self.assertTrue(all(r.status == 'ok' for r in rows), [(r.employee.name, r.diff) for r in rows])
        for r in rows:
            if not r.employee.avoids_weekend:
                self.assertLessEqual(abs(r.special - r.special_fair), 2.5, r.employee.name)
            if r.nights_fair:
                self.assertLessEqual(abs(r.nights - r.nights_fair), 3.0, r.employee.name)
        self.assertEqual(pc.month_overview(self.ctx()).warnings, [])

    def test_several_people_per_shift_but_never_above_max(self):
        pc.generate_month(self.store, date(2026, 10, 1), clear_existing=True, rng=random.Random(4))
        ctx = self.ctx()
        counts = {}
        for (eid, ds), sid in ctx.plan.items():
            if ds.startswith('2026-10'):
                counts[(ds, sid)] = counts.get((ds, sid), 0) + 1
        self.assertTrue(any(n > ctx.shifts[sid].coverage for (ds, sid), n in counts.items()))
        for (ds, sid), n in counts.items():
            self.assertLessEqual(n, ctx.shifts[sid].cap, (ds, ctx.shifts[sid].name))

    def test_without_fill_hours_only_minimum_coverage(self):
        result = pc.generate_month(self.store, date(2026, 10, 1), clear_existing=True, rng=random.Random(4),
                                   fill_hours=False)
        self.assertEqual(result.extra, 0)
        ctx = self.ctx()
        for day in pc.daterange(date(2026, 10, 1), date(2026, 10, 31)):
            for shift in ctx.shift_list:
                self.assertEqual(len(ctx.staff_on(day, shift.id)), shift.coverage)

    def test_coverage_check_warns_above_max(self):
        s = self.blank(shifts=(('Frühdienst', '06:00', '14:00', 1),))
        self.store.run('UPDATE shifts SET max_staff=1')
        a, b = self.person('Person A'), self.person('Person B')
        self.assign(a, date(2026, 10, 6), s['früh'])
        issues = pc.preview_day_changes(self.ctx(), date(2026, 10, 6), {b: s['früh']})
        self.assertIn('Maximalbesetzung', {c.rule for c in issues['coverage']})


class TestLeaveTests(TempStoreCase):
    def test_fresh_demo_database_has_staggered_test_leaves(self):
        rows = self.store.all('SELECT * FROM leave_periods WHERE note=?', (pc.TEST_LEAVE_NOTE,))
        self.assertGreaterEqual(len(rows), 8)
        demo_ids = {r['id'] for r in self.store.all('SELECT id FROM employees WHERE is_demo=1')}
        self.assertTrue(all(r['employee_id'] in demo_ids for r in rows))
        # höchstens eine Demo-Person gleichzeitig im Testurlaub
        days = {}
        for r in rows:
            for d in pc.daterange(date.fromisoformat(r['start_date']), date.fromisoformat(r['end_date'])):
                days[d] = days.get(d, 0) + 1
        self.assertEqual(max(days.values()), 1)

    def test_add_test_leaves_is_idempotent_and_only_for_demo(self):
        s = self.blank()
        demo = self.person('Demo Testperson')
        self.store.run('UPDATE employees SET is_demo=1 WHERE id=?', (demo,))
        real = self.person('Station Person')
        self.assign(demo, date(2026, 10, 3), s['früh'])
        added, removed = pc.add_test_leaves(self.store, [date(2026, 10, 1)])
        self.assertEqual([a[0] for a in added], [demo])
        self.assertEqual(removed, 1)  # Dienst im Testurlaub wurde entfernt (UI fragt vorher nach)
        again, _ = pc.add_test_leaves(self.store, [date(2026, 10, 1)])
        self.assertEqual(again, [])
        self.assertEqual(pc.overlapping_leave(self.store, real, date(2026, 10, 1), date(2026, 10, 31)), [])
        self.assertEqual(pc.remove_test_leaves(self.store), 1)

    def test_migration_v4_adds_max_staff(self):
        path = os.path.join(self.temp.name, 'v3.db')
        db = sqlite3.connect(path)
        db.executescript(LEGACY_SCHEMA)
        db.execute('ALTER TABLE employees ADD COLUMN is_demo INTEGER DEFAULT 0')  # Stand nach Migration v2/v3
        db.execute("INSERT INTO employees(name) VALUES('Station Person')")
        db.execute("INSERT INTO settings(key,value) VALUES('demo_staff_seeded','1')")
        db.execute("INSERT INTO shifts(name,start,end,hours,coverage) VALUES('Frühdienst','06:00','14:00',8,2)")
        db.execute('PRAGMA user_version=3')
        db.commit()
        db.close()
        store = pc.Store(path)
        try:
            self.assertEqual(store.schema_version(), pc.SCHEMA_VERSION)
            self.assertEqual(store.all('SELECT max_staff FROM shifts')[0][0], 4)
            self.assertEqual(store.setting('hours_tolerance'), '4')
        finally:
            store.close()


class DeleteEmployeeTests(TempStoreCase):
    def test_delete_removes_person_data_and_creates_backup(self):
        s = self.blank()
        a, b = self.person('Person A'), self.person('Person B')
        self.assign(a, date(2020, 1, 6), s['früh'])        # Vergangenheit
        self.assign(a, date(2099, 1, 6), s['früh'])        # Zukunft
        self.assign(b, date(2099, 1, 6), s['spät'])
        pc.add_leave(self.store, a, date(2099, 2, 1), date(2099, 2, 3))
        impact = pc.employee_delete_impact(self.store, a)
        self.assertEqual((impact['assignments'], impact['past'], impact['future'], impact['leave']), (2, 1, 1, 1))
        name, backup = pc.delete_employee(self.store, a)
        self.assertEqual(name, 'Person A')
        self.assertTrue(os.path.exists(backup))
        for table in ('employees WHERE id=?', 'assignments WHERE employee_id=?', 'leave_periods WHERE employee_id=?',
                      'eligibility WHERE employee_id=?'):
            self.assertEqual(self.store.db.execute(f'SELECT COUNT(*) FROM {table}', (a,)).fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM assignments WHERE employee_id=?',
                                               (b,)).fetchone()[0], 1)  # andere bleiben unberührt
        backup_db = sqlite3.connect(backup)
        self.assertEqual(backup_db.execute('SELECT COUNT(*) FROM employees WHERE id=?', (a,)).fetchone()[0], 1)
        backup_db.close()
        with self.assertRaises(ValueError):
            pc.delete_employee(self.store, a)

    def test_swap_with_deleted_person_is_no_longer_undoable(self):
        a, b = self.person('Person A'), self.person('Person B')
        s = {r['name'][:4].lower(): r['id'] for r in self.store.all('SELECT id,name FROM shifts')}
        day = date(2026, 10, 14)
        self.assign(a, day, s['früh'])
        self.assign(b, day, s['spät'])
        opt = pc.swap_between(self.ctx(), a, day, b, day)
        pc.apply_swap(self.store, opt)
        pc.delete_employee(self.store, b)
        self.assertIsNone(pc.last_undoable_swap(self.store))


class UndoRedoTests(TempStoreCase):
    """Mehrstufiges Rückgängig/Wiederholen über alle Arten von Planänderungen."""

    def snapshot(self):
        return (sorted(tuple(r) for r in self.store.all('SELECT day,employee_id,shift_id FROM assignments')),
                sorted(tuple(r) for r in self.store.all('SELECT employee_id,start_date,end_date,note FROM leave_periods')))

    def test_multi_step_undo_and_redo_across_actions(self):
        s = self.blank()
        a, b, c = self.person('Person A'), self.person('Person B'), self.person('Person C')
        day = date(2026, 10, 14)
        self.assign(a, day, s['früh'])
        self.assign(b, day, s['spät'])
        states = [self.snapshot()]
        pc.apply_swap(self.store, pc.swap_between(self.ctx(), a, day, b, day))                     # 1 Tausch
        states.append(self.snapshot())
        pc.apply_day_changes(self.store, day, {c: s['nach']})                                    # 2 Tagesplanung
        states.append(self.snapshot())
        pc.add_leave(self.store, a, day, day + timedelta(days=1))                                 # 3 Urlaub (entfernt Dienst)
        states.append(self.snapshot())
        pc.generate_month(self.store, date(2026, 10, 1), rng=random.Random(1), attempts=1)       # 4 Monat planen
        states.append(self.snapshot())
        self.assertEqual(pc.undo_info(self.store)[1], 4)
        for expected in reversed(states[:-1]):                                                    # 4× zurück
            pc.undo_last_change(self.store)
            self.assertEqual(self.snapshot(), expected)
        with self.assertRaises(pc.StaleSwapError):
            pc.undo_last_change(self.store)
        for expected in states[1:]:                                                               # 4× vor
            pc.redo_next_change(self.store)
            self.assertEqual(self.snapshot(), expected)
        self.assertEqual(pc.undo_info(self.store)[3], 0)

    def test_new_change_discards_redo(self):
        s = self.blank()
        a = self.person('Person A')
        pc.apply_day_changes(self.store, date(2026, 10, 5), {a: s['früh']})
        pc.undo_last_change(self.store)
        self.assertEqual(pc.undo_info(self.store)[3], 1)
        pc.apply_day_changes(self.store, date(2026, 10, 6), {a: s['spät']})
        self.assertEqual(pc.undo_info(self.store)[3], 0)
        with self.assertRaises(pc.StaleSwapError):
            pc.redo_next_change(self.store)

    def test_undo_refuses_when_rows_changed_outside_log(self):
        s = self.blank()
        a = self.person('Person A')
        pc.apply_day_changes(self.store, date(2026, 10, 5), {a: s['früh']})
        self.store.run('UPDATE assignments SET shift_id=? WHERE employee_id=?', (s['spät'], a))  # nicht protokolliert
        with self.assertRaises(pc.StaleSwapError):
            pc.undo_last_change(self.store)
        self.assertEqual(self.ctx().shift_of(a, date(2026, 10, 5)), s['spät'])  # nichts überschrieben

    def test_leave_delete_and_test_leaves_are_undoable(self):
        s = self.blank()
        demo = self.person('Demo Person')
        self.store.run('UPDATE employees SET is_demo=1 WHERE id=?', (demo,))
        before = self.snapshot()
        pc.add_test_leaves(self.store, [date(2026, 10, 1)])
        pc.remove_test_leaves(self.store)
        self.assertEqual(self.snapshot(), before)
        pc.undo_last_change(self.store)     # Testurlaube wieder da
        self.assertEqual(len(self.snapshot()[1]), 1)
        pc.undo_last_change(self.store)     # Testurlaube wieder weg
        self.assertEqual(self.snapshot(), before)
        pc.add_leave(self.store, demo, date(2026, 11, 2), date(2026, 11, 3))
        leave_id = self.store.all('SELECT id FROM leave_periods')[0][0]
        pc.delete_leave(self.store, leave_id)
        pc.undo_last_change(self.store)
        self.assertEqual(len(self.snapshot()[1]), 1)

    def test_undo_history_is_limited(self):
        s = self.blank()
        a = self.person('Person A')
        for i in range(pc.UNDO_LIMIT + 15):
            pc.apply_day_changes(self.store, date(2026, 1, 1) + timedelta(days=i), {a: s['früh']})
        self.assertLessEqual(self.store.db.execute('SELECT COUNT(*) FROM change_log').fetchone()[0], pc.UNDO_LIMIT + 1)


if __name__ == '__main__':
    unittest.main()
