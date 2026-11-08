"""Synthetic quality-hold boundaries, exclusions, and mixed events."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solution.hth.policy import QUALITY_MESSAGE, plan_event
from tests.factories import Company, calls

START = date(2027, 2, 26)
END = date(2027, 3, 1)
BEFORE = date(2027, 2, 25)
AFTER = date(2027, 3, 2)


def base() -> Company:
    return (
        Company()
        .add_plant("PA", 5, ["CAR", "VAN"])
        .add_plant("PB", 5, ["CAR"])
        .add_part("BAT", ["CAR"])
        .add_supplier("SUP")
        .add_dealer("D1", 1)
        .add_dealer("D2", 1)
        .add_customer("CU-OWN", "Gold")
        .add_customer("CU-ELSE", "Platinum")
        .add_order("PO-START", "PA", START, "CAR")
        .add_order("PO-END", "PA", END, "CAR")
        .add_order("PO-BEFORE", "PA", BEFORE, "CAR")
        .add_order("PO-AFTER", "PA", AFTER, "CAR")
        .add_order("PO-VAN", "PA", START, "VAN")
        .add_order("PO-PB", "PB", START, "CAR")
        .add_lot("LOT-1", "BAT", "SUP", "PA", START, END)
    )


def with_fleet(company: Company | None = None) -> Company:
    company = company or base()
    return (
        company.add_vehicle("VIN-SOLD-2", "PO-START", "CAR", "PA", START, "D1", "sold", "CU-OWN")
        .add_vehicle("VIN-SOLD-1", "PO-START", "CAR", "PA", START, "D1", "sold", "CU-OWN")
        .add_vehicle("VIN-1", "PO-END", "CAR", "PA", END, "D1", "in_stock", "CU-ELSE")
        .add_vehicle("VIN-2", "PO-END", "CAR", "PA", END, "D2", "in_stock")
        .add_vehicle("VIN-BEFORE", "PO-BEFORE", "CAR", "PA", BEFORE, "D1", "in_stock")
        .add_vehicle("VIN-AFTER", "PO-AFTER", "CAR", "PA", AFTER, "D1", "in_stock")
        .add_vehicle("VIN-VAN", "PO-VAN", "VAN", "PA", START, "D1", "in_stock")
        .add_vehicle("VIN-PB", "PO-PB", "CAR", "PB", START, "D2", "sold", "CU-ELSE")
    )


def hold(**fields) -> dict:
    body = {"kind": "quality_hold"}
    body.update(fields)
    return {"event_id": "quality", "incidents": [body]}


def vehicle_reasons(result: dict, incident_index: int = 0) -> dict[str, str]:
    return {
        row["vin"]: row["reason"]
        for row in result["vehicle_findings"]
        if row["incident_index"] == incident_index
    }


class QualityBoundaryTests(unittest.TestCase):
    def test_inclusive_dates_split_sold_and_unsold_and_ignore_others(self):
        result = plan_event(with_fleet().ontology(), hold(lot_id="LOT-1"))
        self.assertEqual(result["status"], "ok", result["blockers"])
        self.assertEqual(result["affected_orders"], [])
        self.assertEqual(result["moves"], [])
        self.assertEqual(
            vehicle_reasons(result),
            {
                "VIN-SOLD-2": "affected_sold",
                "VIN-SOLD-1": "affected_sold",
                "VIN-1": "affected_in_stock",
                "VIN-2": "affected_in_stock",
                "VIN-BEFORE": "outside_lot_dates",
                "VIN-AFTER": "outside_lot_dates",
                "VIN-VAN": "bom_miss",
                "VIN-PB": "wrong_plant",
            },
        )
        self.assertEqual(calls(result, "quality.hold_lot")[0]["args"], {"lot_id": "LOT-1"})
        stops = {call["args"]["dealer_id"]: call["args"]["vins"] for call in calls(result, "dealer.stop_sale")}
        self.assertEqual(stops, {"D1": ["VIN-1"], "D2": ["VIN-2"]})
        notices = calls(result, "crm.notify_customer")
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["args"]["customer_id"], "CU-OWN")
        self.assertEqual(notices[0]["args"]["message"], QUALITY_MESSAGE)
        self.assertFalse(calls(result, "crm.assign_loaner"))
        chains = result["graph_paths"][0]["chains"]
        sold = next(chain for chain in chains if "Vehicle:VIN-SOLD-1" in chain)
        self.assertEqual(
            sold,
            ["Lot:LOT-1", "ProductionOrder:PO-START", "Vehicle:VIN-SOLD-1", "Dealer:D1", "Customer:CU-OWN"],
        )
        stock = next(chain for chain in chains if "Vehicle:VIN-1" in chain)
        self.assertEqual(stock, ["Lot:LOT-1", "ProductionOrder:PO-END", "Vehicle:VIN-1", "Dealer:D1"])
        joined = " ".join(" ".join(chain) for chain in result["graph_paths"][0]["chains"])
        self.assertNotIn("VIN-VAN", joined)
        self.assertNotIn("VIN-PB", joined)
        self.assertNotIn("VIN-BEFORE", joined)

    def test_point_date_uses_the_full_lot_window(self):
        result = plan_event(with_fleet().ontology(), hold(part_id="BAT", plant_id="PA", build_date="2027-03-01"))
        self.assertEqual(result["status"], "ok", result["blockers"])
        self.assertEqual(result["incidents"][0]["first_build_date"], "2027-02-26")
        self.assertEqual(result["incidents"][0]["last_build_date"], "2027-03-01")
        self.assertEqual(vehicle_reasons(result)["VIN-SOLD-1"], "affected_sold")
        self.assertEqual(vehicle_reasons(result)["VIN-1"], "affected_in_stock")

    def test_narrow_window_does_not_clip_or_guess(self):
        result = plan_event(
            with_fleet().ontology(),
            hold(part_id="BAT", plant_id="PA", first_build_date="2027-02-26", last_build_date="2027-02-26"),
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["tool_calls"], [])
        self.assertIn("no lot matches", result["blockers"][0])


class QualityGuardTests(unittest.TestCase):
    def test_contradiction_ambiguity_and_repeat(self):
        fleet = with_fleet().add_supplier("SUP2").add_lot("LOT-2", "BAT", "SUP2", "PA", START, START)
        ontology = fleet.ontology()
        conflict = plan_event(ontology, hold(lot_id="LOT-1", part_id="OTHER"))
        self.assertEqual(conflict["tool_calls"], [])
        self.assertIn("contradicts lot", conflict["blockers"][0])

        ambiguous = plan_event(ontology, hold(part_id="BAT", plant_id="PA", build_date="2027-02-26"))
        self.assertIn("ambiguous lot match", ambiguous["blockers"][0])
        self.assertEqual(ambiguous["tool_calls"], [])

        specific = plan_event(
            ontology, hold(part_id="BAT", plant_id="PA", supplier_id="SUP", build_date="2027-02-26")
        )
        self.assertEqual(specific["status"], "ok", specific["blockers"])
        self.assertEqual(specific["incidents"][0]["lot_id"], "LOT-1")

        repeated = plan_event(
            fleet.ontology(),
            {
                "event_id": "repeat",
                "incidents": [
                    {"kind": "quality_hold", "lot_id": "LOT-1"},
                    {"kind": "quality_hold", "lot_id": "LOT-1"},
                ],
            },
        )
        self.assertEqual(repeated["status"], "ok", repeated["blockers"])
        self.assertEqual(len(calls(repeated, "quality.hold_lot")), 1)
        self.assertEqual(len(calls(repeated, "crm.notify_customer")), 1)
        self.assertEqual(sum(len(call["args"]["vins"]) for call in calls(repeated, "dealer.stop_sale")), 2)

    def test_missing_owner_unknown_status_and_inconsistent_record_block(self):
        missing = base().add_vehicle("VIN-BARE", "PO-START", "CAR", "PA", START, "D1", "sold")
        result = plan_event(missing.ontology(), hold(lot_id="LOT-1"))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["tool_calls"], [])
        self.assertIn("missing_owner for VIN-BARE", result["blockers"][0])

        unknown = base().add_vehicle("VIN-ODD", "PO-START", "CAR", "PA", START, "D1", "scrapped", "CU-OWN")
        odd = plan_event(unknown.ontology(), hold(lot_id="LOT-1"))
        self.assertIn("unknown_status for VIN-ODD", odd["blockers"][0])
        self.assertEqual(odd["tool_calls"], [])

        mismatched = base().add_vehicle("VIN-BAD", "PO-VAN", "CAR", "PA", START, "D1", "in_stock")
        bad = plan_event(mismatched.ontology(), hold(lot_id="LOT-1"))
        self.assertIn("inconsistent_record for VIN-BAD", bad["blockers"][0])
        self.assertEqual(bad["quality"]["holds"], [])

    def test_two_lots_stop_a_shared_vehicle_once(self):
        company = (
            base()
            .add_part("CHIP", ["CAR"])
            .add_lot("LOT-CHIP", "CHIP", "SUP", "PA", START, END)
            .add_vehicle("VIN-1", "PO-END", "CAR", "PA", END, "D1", "in_stock")
        )
        result = plan_event(
            company.ontology(),
            {
                "event_id": "two-lots",
                "incidents": [
                    {"kind": "quality_hold", "lot_id": "LOT-CHIP"},
                    {"kind": "quality_hold", "lot_id": "LOT-1"},
                ],
            },
        )
        self.assertEqual(result["status"], "ok", result["blockers"])
        self.assertEqual(
            [call["args"]["lot_id"] for call in calls(result, "quality.hold_lot")],
            ["LOT-1", "LOT-CHIP"],
        )
        self.assertEqual(calls(result, "dealer.stop_sale")[0]["args"]["vins"], ["VIN-1"])


class MixedEventTests(unittest.TestCase):
    def test_quality_does_not_take_slots_or_loaners_from_a_delay(self):
        company = (
            with_fleet()
            .add_shipment("SH-1", "SUP", "BAT", "PA", date(2027, 3, 8))
            .add_customer("CU-P", "Platinum")
            .add_order("PO-FUTURE", "PA", date(2027, 3, 8), "CAR")
            .add_customer_order("CO-FUTURE", "PO-FUTURE", "D1", "CU-P", "CAR", date(2027, 3, 20))
        )
        result = plan_event(
            company.ontology(),
            {
                "event_id": "mixed",
                "incidents": [
                    {"kind": "supplier_delay", "shipment_id": "SH-1", "new_eta": "2027-03-10"},
                    {"kind": "quality_hold", "lot_id": "LOT-1"},
                ],
            },
        )
        self.assertEqual(result["status"], "ok", result["blockers"])
        self.assertEqual([move["production_order_id"] for move in result["moves"]], ["PO-FUTURE"])
        self.assertEqual(
            [call["args"]["customer_order_id"] for call in calls(result, "crm.assign_loaner")],
            ["CO-FUTURE"],
        )
        self.assertEqual(
            [call["args"]["customer_id"] for call in calls(result, "crm.notify_customer")],
            ["CU-OWN"],
        )
        self.assertEqual([call["tool"] for call in result["tool_calls"][:3]], ["mes.reschedule", "dealer.notify", "crm.assign_loaner"])
        self.assertEqual(result["tool_calls"][3]["tool"], "quality.hold_lot")
        self.assertIn("dealer.stop_sale", [call["tool"] for call in result["tool_calls"]])
        self.assertNotIn("PO-START", [move["production_order_id"] for move in result["moves"]])
        self.assertEqual(result["graph_paths"][0]["kind"], "supplier_delay")
        self.assertEqual(result["graph_paths"][1]["kind"], "quality_hold")


if __name__ == "__main__":
    unittest.main()
