"""Apply a synthetic plan to an in-memory Enterprise. Source data stays untouched."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "hack_the_highway_student_package" / "hack_the_highway_package"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PKG))

import enterprise

from solution.hth.ontology import load_ontology
from solution.hth.policy import plan_event
from tests.factories import Company
from tests.test_ontology import DATA_HASH, hash_data

MON = date(2027, 3, 8)
TUE = date(2027, 3, 9)


def working_days_from(start: date, end: date) -> list[date]:
    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


class EnterpriseAgreementTests(unittest.TestCase):
    def test_reschedule_notify_and_loaner_match_simulator_state(self):
        company = (
            Company()
            .add_supplier("SUP")
            .add_plant("PA", 5, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON)
            .add_dealer("D1", 1)
            .add_customer("CU-P", "Platinum")
            .add_customer("CU-S", "Standard")
            .add_order("PO-MOVE", "PA", MON, "CAR", units=30)
            .add_order("PO-STAY", "PA", date(2027, 3, 10), "CAR")
            .add_customer_order("CO-P", "PO-MOVE", "D1", "CU-P", "CAR", date(2027, 3, 20))
            .add_customer_order("CO-S", "PO-MOVE", "D1", "CU-S", "CAR", date(2027, 3, 12))
        )
        self._assert_simulator(company, {"event_id": "mem-delay", "incidents": [{"kind": "supplier_delay", "shipment_id": "SH-1", "new_eta": "2027-03-10"}]}, "PO-STAY", "PA", date(2027, 3, 10))

    def test_transfer_matches_simulator_state(self):
        company = (
            Company()
            .add_supplier("SUP")
            .add_plant("PA", 1, ["CAR", "VAN"])
            .add_plant("PB", 1, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON)
            .add_dealer("D1", 2)
            .add_customer("CU-P", "Platinum")
            .add_order("PO-MOVE", "PA", MON, "CAR")
            .add_order("PO-STAY", "PB", date(2027, 3, 16), "CAR")
            .add_customer_order("CO-P", "PO-MOVE", "D1", "CU-P", "CAR", date(2027, 3, 20))
        )
        for index, day in enumerate(working_days_from(TUE, date(2027, 4, 2))):
            company.add_order(f"PO-FILL-{index}", "PA", day, "VAN")
        self._assert_simulator(
            company,
            {"event_id": "mem-transfer", "incidents": [{"kind": "supplier_delay", "shipment_id": "SH-1", "new_eta": "2027-03-09"}]},
            "PO-STAY",
            "PB",
            date(2027, 3, 16),
        )

    def _assert_simulator(self, company: Company, event: dict, stay_id: str, stay_plant: str, stay_day: date):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            company.write(root)
            ontology = load_ontology(root)
            result = plan_event(ontology, event)
            self.assertEqual(result["status"], "ok", result["blockers"])
            world = enterprise.Enterprise(str(root))
            allowed = set(enterprise.TOOLS)
            for call in result["tool_calls"]:
                self.assertIn(call["tool"], allowed)
                self.assertEqual(set(call["args"]), set(enterprise.TOOLS[call["tool"]]["args"]))
                world.call(event["event_id"], call["tool"], call["args"])
            state = world.state(event["event_id"])
            for move in result["moves"]:
                order_id = move["production_order_id"]
                expected = (move["to"]["plant_id"], enterprise.date_to_idx(date.fromisoformat(move["to"]["date"])))
                self.assertEqual(state["loc"][order_id], expected)
            self.assertEqual(state["loc"][stay_id], (stay_plant, enterprise.date_to_idx(stay_day)))
            self.assertFalse(state["escalated"])
            self.assertEqual(state["held"], set())
            self.assertEqual(state["stopped"], set())
            self.assertEqual(state["customers"], set())
            assigned = [call["args"]["customer_order_id"] for call in result["tool_calls"] if call["tool"] == "crm.assign_loaner"]
            self.assertEqual(state["loaned"], set(assigned))
            for call in result["tool_calls"]:
                if call["tool"] == "dealer.notify":
                    self.assertEqual(state["notified"][call["args"]["dealer_id"]], set(call["args"]["customer_order_ids"]))
        self.assertEqual(hash_data(), DATA_HASH)


if __name__ == "__main__":
    unittest.main()
