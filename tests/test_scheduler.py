import os
import random
import tempfile
import unittest
from datetime import date, timedelta

import main


class CalendarRuleTests(unittest.TestCase):
    def test_baden_wuerttemberg_holidays_2026(self):
        holidays = main.bw_holidays(2026)
        self.assertEqual(main.easter_sunday(2026), date(2026, 4, 5))
        self.assertEqual(holidays[date(2026, 4, 3)], 'Karfreitag')
        self.assertEqual(holidays[date(2026, 6, 4)], 'Fronleichnam')
        self.assertEqual(holidays[date(2026, 1, 6)], 'Heilige Drei Könige')


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'test.db')
        self.store = main.Store(self.path)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_demo_staff_are_seeded_once(self):
        self.assertTrue(self.store.demo_seeded_now)
        expected = len(main.DEMO_STAFF)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM employees').fetchone()[0], expected)
        second = main.Store(self.path)
        try:
            self.assertFalse(second.demo_seeded_now)
            self.assertEqual(second.db.execute('SELECT COUNT(*) FROM employees').fetchone()[0], expected)
        finally:
            second.db.close()

    def test_generator_respects_vacation_and_covers_weekend(self):
        employee = self.store.all('SELECT id FROM employees ORDER BY id LIMIT 1')[0]['id']
        self.store.run('INSERT INTO leave_periods(employee_id,start_date,end_date) VALUES(?,?,?)',
                       (employee, '2026-10-10', '2026-10-12'))
        missing = main.build_assignments(self.store, date(2026, 10, 1), clear_existing=True)
        self.assertEqual(missing, 0)
        self.assertEqual(self.store.db.execute(
            'SELECT COUNT(*) FROM assignments WHERE employee_id=? AND day BETWEEN ? AND ?',
            (employee, '2026-10-10', '2026-10-12')).fetchone()[0], 0)
        self.assertGreater(self.store.db.execute(
            'SELECT COUNT(*) FROM assignments WHERE day=?', ('2026-10-10',)).fetchone()[0], 0)

    def test_schema_v6_migrates_existing_database_without_losing_employees(self):
        employee = self.store.db.execute('SELECT id FROM employees LIMIT 1').fetchone()[0]
        self.assertEqual(self.store.schema_version(), 6)
        self.assertEqual(self.store.db.execute('SELECT id FROM employees WHERE id=?', (employee,)).fetchone()[0],
                         employee)
        self.assertIsNotNone(self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='employee_shift_patterns'").fetchone())
        shifts = {r['name']: r['id'] for r in self.store.all('SELECT id,name FROM shifts')}
        self.assertEqual(self.store.db.execute(
            'SELECT days_after FROM shift_transition_rules WHERE after_shift_id=? AND blocked_shift_id=?',
            (shifts['Nachtdienst'], shifts['Frühdienst'])).fetchone()[0], 1)
        streak = self.store.db.execute('SELECT max_consecutive,rest_days FROM shift_streak_rules WHERE shift_id=?',
                                       (shifts['Spätdienst'],)).fetchone()
        self.assertEqual(tuple(streak), (4, 2))

    def test_fixed_weekday_shift_is_blocking_for_other_shifts(self):
        employee = self.store.db.execute(
            "INSERT INTO employees(name,role,weekly_hours,max_days) VALUES('Regel Test','Pflege',38.5,5)"
        ).lastrowid
        shifts = {r['name']: r['id'] for r in self.store.all('SELECT id,name FROM shifts')}
        self.store.run('INSERT INTO employee_shift_patterns(employee_id,weekday,shift_id) VALUES(?,?,?)',
                       (employee, 4, shifts['Nachtdienst']))
        friday = date(2030, 1, 4)
        ctx = main.PlanContext(self.store, friday - timedelta(days=2), friday + timedelta(days=3))
        night = ctx.check_assignment(employee, friday, shifts['Nachtdienst'], include_ok=False)
        early = ctx.check_assignment(employee, friday, shifts['Frühdienst'], include_ok=False)
        self.assertFalse(any(c.rule == 'Feste Wochenschicht' and c.blocking for c in night))
        self.assertTrue(any(c.rule == 'Feste Wochenschicht' and c.blocking for c in early))

    def test_month_generator_places_employee_weekday_pattern(self):
        employee = self.store.db.execute(
            "INSERT INTO employees(name,role,weekly_hours,max_days) VALUES('Regel Test','Pflege',38.5,5)"
        ).lastrowid
        shifts = {r['name']: r['id'] for r in self.store.all('SELECT id,name FROM shifts')}
        friday = date(2030, 1, 4)
        self.store.run('INSERT INTO employee_shift_patterns(employee_id,weekday,shift_id) VALUES(?,?,?)',
                       (employee, friday.weekday(), shifts['Nachtdienst']))
        main.generate_month(self.store, date(2030, 1, 1), clear_existing=True, rng=random.Random(17))
        assigned = self.store.db.execute('SELECT shift_id FROM assignments WHERE employee_id=? AND day=?',
                                         (employee, friday.isoformat())).fetchone()
        self.assertIsNotNone(assigned)
        self.assertEqual(assigned[0], shifts['Nachtdienst'])

    def test_night_shift_can_block_early_shift_next_day(self):
        employee = self.store.db.execute(
            "INSERT INTO employees(name,role,weekly_hours,max_days) VALUES('Regel Test','Pflege',38.5,5)"
        ).lastrowid
        shifts = {r['name']: r['id'] for r in self.store.all('SELECT id,name FROM shifts')}
        self.store.run('''INSERT INTO shift_transition_rules(after_shift_id,blocked_shift_id,days_after) VALUES(?,?,?)
            ON CONFLICT(after_shift_id,blocked_shift_id) DO UPDATE SET days_after=excluded.days_after''',
                       (shifts['Nachtdienst'], shifts['Frühdienst'], 1))
        friday = date(2030, 1, 4)
        self.store.run('INSERT INTO assignments(day,employee_id,shift_id) VALUES(?,?,?)',
                       (friday.isoformat(), employee, shifts['Nachtdienst']))
        ctx = main.PlanContext(self.store, friday - timedelta(days=2), friday + timedelta(days=3))
        checks = ctx.check_assignment(employee, friday + timedelta(days=1),
                                      shifts['Frühdienst'], include_ok=False)
        self.assertTrue(any(c.rule == 'Schichtfolge' and c.blocking for c in checks))

    def test_streak_limit_and_required_rest_days_are_enforced(self):
        employee = self.store.db.execute(
            "INSERT INTO employees(name,role,weekly_hours,max_days) VALUES('Regel Test','Pflege',38.5,7)"
        ).lastrowid
        shifts = {r['name']: r['id'] for r in self.store.all('SELECT id,name FROM shifts')}
        self.store.run('''INSERT INTO shift_streak_rules(shift_id,max_consecutive,rest_days) VALUES(?,?,?)
            ON CONFLICT(shift_id) DO UPDATE SET max_consecutive=excluded.max_consecutive,rest_days=excluded.rest_days''',
                       (shifts['Spätdienst'], 4, 2))
        monday = date(2030, 1, 7)
        for n in range(4):
            day = monday + timedelta(days=n)
            self.store.run('INSERT INTO assignments(day,employee_id,shift_id) VALUES(?,?,?)',
                           (day.isoformat(), employee, shifts['Spätdienst']))
        ctx = main.PlanContext(self.store, monday - timedelta(days=2), monday + timedelta(days=8))
        fifth = ctx.check_assignment(employee, monday + timedelta(days=4),
                                     shifts['Spätdienst'], include_ok=False)
        rest = ctx.check_assignment(employee, monday + timedelta(days=5),
                                    shifts['Frühdienst'], include_ok=False)
        self.assertTrue(any(c.rule == 'Schichtserie' and c.blocking for c in fifth))
        self.assertTrue(any(c.rule == 'Ruhetage nach Schichtserie' and c.blocking for c in rest))


if __name__ == '__main__':
    unittest.main()
