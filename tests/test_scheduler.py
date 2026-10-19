import os
import tempfile
import unittest
from datetime import date

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
        self.old_db_path = main.DB_PATH
        main.DB_PATH = os.path.join(self.temp.name, 'test.db')
        self.store = main.Store()

    def tearDown(self):
        self.store.db.close()
        main.DB_PATH = self.old_db_path
        self.temp.cleanup()

    def test_demo_staff_are_seeded_once(self):
        self.assertTrue(self.store.demo_seeded_now)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM employees').fetchone()[0], 8)
        second = main.Store()
        try:
            self.assertFalse(second.demo_seeded_now)
            self.assertEqual(second.db.execute('SELECT COUNT(*) FROM employees').fetchone()[0], 8)
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


if __name__ == '__main__':
    unittest.main()
