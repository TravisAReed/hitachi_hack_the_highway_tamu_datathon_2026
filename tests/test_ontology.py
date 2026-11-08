"""Calendar parity and the packaged tables as a linked index."""

from __future__ import annotations

import hashlib
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "hack_the_highway_student_package" / "hack_the_highway_package"
DATA = PKG / "data"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PKG))

import enterprise

from solution.hth.calendarutil import ABSOLUTE_HORIZON, FIRST_IDX, LAST_IDX, TODAY, date_to_idx, idx_to_date
from solution.hth.ontology import load_ontology


def hash_data() -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in DATA.rglob("*") if item.is_file()):
        digest.update(path.relative_to(DATA).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


DATA_HASH = hash_data()


class CalendarTests(unittest.TestCase):
    def test_tool_days_match_the_simulator(self):
        self.assertEqual(TODAY, enterprise.START)
        self.assertEqual(FIRST_IDX, enterprise.FIRST_IDX)
        self.assertEqual(LAST_IDX, enterprise.LAST_IDX)
        for index in range(FIRST_IDX, LAST_IDX + 1):
            day = idx_to_date(index)
            self.assertEqual(day, enterprise.idx_to_date(index))
            self.assertEqual(date_to_idx(day), enterprise.date_to_idx(day))
            self.assertEqual(date_to_idx(day), index)
        self.assertEqual(ABSOLUTE_HORIZON, date(2027, 4, 2))
        with self.assertRaises(ValueError):
            date_to_idx(date(2027, 3, 6))
        with self.assertRaises(ValueError):
            enterprise.date_to_idx(date(2027, 3, 6))


class RealOntologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ontology = load_ontology(DATA)

    def test_counts_and_links(self):
        self.assertEqual(self.ontology.counts()["plants"], 6)
        self.assertEqual(self.ontology.counts()["production_orders"], 540)
        self.assertEqual(self.ontology.counts()["shipments"], 364)
        self.assertEqual(self.ontology.counts()["customer_orders"], 3263)
        self.assertEqual(self.ontology.counts()["vehicles"], 5348)
        self.assertEqual(self.ontology.counts()["lots"], 364)
        self.assertEqual(self.ontology.counts()["customers"], 7000)
        self.assertEqual(self.ontology.counts()["dealers"], 50)
        self.assertEqual(len(self.ontology.destination_days), 20)
        self.assertEqual(self.ontology.destination_days[0], date(2027, 3, 8))
        self.assertEqual(self.ontology.destination_days[-1], date(2027, 4, 2))
        self.assertTrue(all(len(ids) == 1 for ids in self.ontology.shipments_by_key.values()))
        covered = {}
        for lot in self.ontology.lots.values():
            day = lot.first_build_date
            while day <= lot.last_build_date:
                if day.weekday() < 5:
                    key = (lot.part_id, lot.plant_id, day)
                    self.assertNotIn(key, covered)
                    covered[key] = lot.lot_id
                day += timedelta(days=1)
        self.assertGreater(len(covered), 0)
        for order in self.ontology.orders.values():
            self.assertIn(order.plant_id, self.ontology.plants)
        for customer_order in self.ontology.customer_orders.values():
            self.assertIn(customer_order.production_order_id, self.ontology.orders)

    def test_alias_resolution_is_exact(self):
        rows = (DATA / "aliases.csv").read_text(encoding="utf-8").splitlines()[1:]
        for line in rows:
            alias, entity_type, entity_id = next(csv_row(line))
            hit = self.ontology.resolve_name(alias, entity_type)
            self.assertEqual(hit.status, "found", alias)
            self.assertEqual(hit.entity_id, entity_id)
            padded = self.ontology.resolve_name(f"  {alias.upper()}  ", entity_type)
            self.assertEqual(padded.entity_id, entity_id)
        self.assertEqual(self.ontology.resolve_name("the Texas plant").entity_id, "P_TX")
        self.assertEqual(self.ontology.resolve_name("60 kWh battery pack", "part").entity_id, "BATT-60")
        self.assertEqual(self.ontology.resolve_name("Quanta Silicon", "supplier").entity_id, "SUP-12")
        self.assertEqual(self.ontology.resolve_name("Texas").status, "not_found")
        self.assertEqual(self.ontology.resolve_name("Huntsville line").status, "not_found")
        self.assertEqual(self.ontology.resolve_name("Huntsville", "plant").entity_id, "P_AL")

    def test_packaged_data_is_unchanged(self):
        self.assertEqual(hash_data(), DATA_HASH)


def csv_row(line: str):
    import csv

    yield from csv.reader([line])


if __name__ == "__main__":
    unittest.main()
