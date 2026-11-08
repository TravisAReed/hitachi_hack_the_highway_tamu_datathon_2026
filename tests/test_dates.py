"""Date phrases are anchored to Monday 2027-03-08."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solution.hth.dates import DateError, parse_date_mention


class DateMentionTests(unittest.TestCase):
    def test_relative_days_from_the_simulator_today(self):
        self.assertEqual(parse_date_mention("next Tuesday"), date(2027, 3, 9))
        self.assertEqual(parse_date_mention("next Friday"), date(2027, 3, 12))
        self.assertEqual(parse_date_mention("next Monday"), date(2027, 3, 15))
        self.assertEqual(parse_date_mention("this Friday"), date(2027, 3, 12))
        self.assertEqual(parse_date_mention("last Monday"), date(2027, 3, 1))
        self.assertEqual(parse_date_mention("last Thursday"), date(2027, 3, 4))

    def test_explicit_dates_and_weekday_agreement(self):
        self.assertEqual(parse_date_mention("2027-03-18"), date(2027, 3, 18))
        self.assertEqual(parse_date_mention("Monday Mar 22"), date(2027, 3, 22))
        self.assertEqual(parse_date_mention("Thursday Feb 25"), date(2027, 2, 25))
        self.assertEqual(parse_date_mention("next Friday Mar 12"), date(2027, 3, 12))

    def test_conflicts_fail(self):
        with self.assertRaises(DateError):
            parse_date_mention("next Friday Mar 19")
        with self.assertRaises(DateError):
            parse_date_mention("Tuesday Mar 22")
        with self.assertRaises(DateError):
            parse_date_mention("sometime soon")


if __name__ == "__main__":
    unittest.main()
