"""Synthetic delay, outage, capacity, loaner, and compound cases."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solution.hth.incidents import InputError
from solution.hth.policy import DEALER_MESSAGE, plan_event
from tests.factories import Company, calls, delay_event, moved_to, reasons

MON = date(2027, 3, 8)
TUE = date(2027, 3, 9)
WED = date(2027, 3, 10)
THU = date(2027, 3, 11)
FRI = date(2027, 3, 12)
NEXT_MON = date(2027, 3, 15)
NEXT_TUE = date(2027, 3, 16)
PAST_THU = date(2027, 3, 4)
PAST_FRI = date(2027, 3, 5)
WEEK_HORIZON = FRI


def week_company() -> Company:
    return Company(WEEK_HORIZON).add_supplier("SUP").add_dealer("D1", 3).add_customer("CU-P", "Platinum")


class DelayBoundaryTests(unittest.TestCase):
    def test_window_bom_plant_and_covers_days(self):
        company = (
            week_company()
            .add_plant("PA", 5, ["CAR", "VAN"])
            .add_plant("PB", 5, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", PAST_THU, covers_days=99)
            .add_dealer("D2", 3)
            .add_dealer("D3", 3)
            .add_customer("CU-OLD", "Gold")
            .add_customer("CU-VAN", "Gold")
            .add_customer("CU-OTHER", "Gold")
            .add_customer("CU-S", "Standard")
            .add_order("PO-OLD1", "PA", PAST_THU, "CAR")
            .add_order("PO-OLD2", "PA", PAST_FRI, "CAR")
            .add_order("PO-MON", "PA", MON, "CAR", units=100)
            .add_order("PO-TUE", "PA", TUE, "CAR")
            .add_order("PO-ETA", "PA", WED, "CAR")
            .add_order("PO-LATER", "PA", THU, "CAR")
            .add_order("PO-OTHER", "PB", MON, "CAR")
            .add_order("PO-VAN", "PA", MON, "VAN")
            .add_customer_order("CO-OLD", "PO-OLD1", "D1", "CU-OLD", "CAR", MON)
            .add_customer_order("CO-MON", "PO-MON", "D1", "CU-P", "CAR", THU)
            .add_customer_order("CO-STD", "PO-MON", "D1", "CU-S", "CAR", TUE)
            .add_customer_order("CO-TUE", "PO-TUE", "D2", "CU-P", "CAR", THU)
            .add_customer_order("CO-VAN", "PO-VAN", "D1", "CU-VAN", "VAN", THU)
            .add_customer_order("CO-OTHER", "PO-OTHER", "D3", "CU-OTHER", "CAR", THU)
        )
        pools = dict(company.ontology().loaners)
        result = plan_event(
            company.ontology(),
            delay_event(shipment_id="SH-1", new_eta="2027-03-10"),
        )
        self.assertEqual(result["status"], "ok", result["blockers"])
        self.assertEqual(company.ontology().loaners, pools)
        self.assertEqual(
            reasons(result),
            {
                "PO-OLD1": "already_built",
                "PO-OLD2": "already_built",
                "PO-MON": "affected",
                "PO-TUE": "affected",
                "PO-ETA": "on_or_after_new_eta",
                "PO-LATER": "on_or_after_new_eta",
                "PO-OTHER": "wrong_plant",
                "PO-VAN": "bom_miss",
            },
        )
        self.assertEqual(result["incidents"][0]["covers_days"], 99)
        self.assertEqual(moved_to(result), {"PO-MON": "2027-03-10", "PO-TUE": "2027-03-10"})
        self.assertEqual([move["decision"] for move in result["moves"]], ["reschedule", "reschedule"])
        self.assertEqual(result["moves"][0]["free_slots_before"], 4)
        notices = calls(result, "dealer.notify")
        self.assertEqual([notice["args"]["dealer_id"] for notice in notices], ["D1", "D2"])
        self.assertEqual(notices[0]["args"]["customer_order_ids"], ["CO-MON", "CO-STD"])
        self.assertEqual(notices[1]["args"]["customer_order_ids"], ["CO-TUE"])
        self.assertEqual(notices[0]["args"]["message"], DEALER_MESSAGE)
        self.assertNotIn("CO-OLD", str(notices))
        self.assertNotIn("CO-VAN", str(notices))
        self.assertNotIn("CO-OTHER", str(notices))
        assigned = [row["customer_order_id"] for row in result["loaner_decisions"] if row["decision"] == "assign"]
        self.assertEqual(assigned, ["CO-MON", "CO-TUE"])
        skipped = {row["customer_order_id"]: row["decision"] for row in result["loaner_decisions"]}
        self.assertEqual(skipped["CO-STD"], "skip_tier")
        chain = result["graph_paths"][0]["chains"][0]
        self.assertEqual(
            chain[:4],
            ["Shipment:SH-1", "Part:BAT", "Model:CAR", "ProductionOrder:PO-MON"],
        )
        joined = " ".join(" ".join(item) for item in result["graph_paths"][0]["chains"])
        self.assertNotIn("PO-VAN", joined)
        self.assertNotIn("PO-OTHER", joined)
        self.assertNotIn("PO-OLD1", joined)
        for call in result["tool_calls"]:
            if "new_date" in call["args"]:
                self.assertLess(date.fromisoformat(call["args"]["new_date"]).weekday(), 5)

    def test_second_plan_matches_and_does_not_mutate_pools(self):
        company = (
            week_company()
            .add_plant("PA", 5, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON, covers_days=99)
            .add_order("PO-A", "PA", MON, "CAR")
            .add_customer_order("CO-A", "PO-A", "D1", "CU-P", "CAR", FRI)
        )
        ontology = company.ontology()
        before = dict(ontology.loaners)
        event = delay_event(shipment_id="SH-1", new_eta="2027-03-10")
        first = plan_event(ontology, event)
        second = plan_event(ontology, event)
        self.assertEqual(first, second)
        self.assertEqual(ontology.loaners, before)

    def test_order_without_customers_only_reschedules(self):
        company = (
            week_company()
            .add_plant("PA", 5, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON)
            .add_order("PO-A", "PA", MON, "CAR")
        )
        result = plan_event(company.ontology(), delay_event(shipment_id="SH-1", new_eta="2027-03-10"))
        self.assertEqual(result["status"], "ok", result["blockers"])
        self.assertEqual([call["tool"] for call in result["tool_calls"]], ["mes.reschedule"])
        self.assertEqual(result["graph_paths"][0]["chains"], [["Shipment:SH-1", "Part:BAT", "Model:CAR", "ProductionOrder:PO-A"]])


class OutageTests(unittest.TestCase):
    def test_inclusive_bounds_and_weekend_end(self):
        company = (
            Company(date(2027, 3, 19))
            .add_plant("PA", 5, ["CAR"])
            .add_plant("PB", 5, ["CAR"])
            .add_order("PO-FRI", "PA", FRI, "CAR")
            .add_order("PO-MON", "PA", NEXT_MON, "CAR")
            .add_order("PO-TUE", "PA", NEXT_TUE, "CAR")
            .add_order("PO-OTHER", "PB", FRI, "CAR")
        )
        weekend = plan_event(
            company.ontology(),
            {
                "event_id": "outage-weekend",
                "incidents": [
                    {"kind": "plant_outage", "plant_id": "PA", "start_date": "2027-03-12", "end_date": "2027-03-13"}
                ],
            },
        )
        self.assertEqual(weekend["status"], "ok", weekend["blockers"])
        self.assertEqual(reasons(weekend)["PO-FRI"], "affected")
        self.assertEqual(reasons(weekend)["PO-MON"], "outside_outage")
        self.assertEqual(reasons(weekend)["PO-OTHER"], "wrong_plant")
        self.assertEqual(moved_to(weekend), {"PO-FRI": "2027-03-15"})
        self.assertEqual(weekend["incidents"][0]["legal_start"], "2027-03-15")
        self.assertEqual(weekend["graph_paths"][0]["chains"][0][0], "Plant:PA")
        self.assertNotIn("Shipment:", weekend["graph_paths"][0]["chains"][0][0])

        inclusive = plan_event(
            company.ontology(),
            {
                "event_id": "outage-span",
                "incidents": [
                    {"kind": "plant_outage", "plant_id": "PA", "start_date": "2027-03-12", "end_date": "2027-03-15"}
                ],
            },
        )
        self.assertEqual(reasons(inclusive)["PO-FRI"], "affected")
        self.assertEqual(reasons(inclusive)["PO-MON"], "affected")
        self.assertEqual(reasons(inclusive)["PO-TUE"], "outside_outage")
        self.assertEqual(moved_to(inclusive), {"PO-FRI": "2027-03-16", "PO-MON": "2027-03-16"})

    def test_outage_on_last_tool_day_is_blocked(self):
        company = Company().add_plant("PA", 5, ["CAR"]).add_plant("PB", 5, ["CAR"]).add_order("PO-LAST", "PA", date(2027, 4, 2), "CAR")
        result = plan_event(
            company.ontology(),
            {
                "event_id": "horizon",
                "incidents": [
                    {"kind": "plant_outage", "plant_id": "PA", "start_date": "2027-04-02", "end_date": "2027-04-02"}
                ],
            },
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["moves"], [])
        self.assertTrue(any("no legal slot" in blocker and "PO-LAST" in blocker for blocker in result["blockers"]))
        self.assertIn("ProductionOrder:PO-LAST", result["graph_paths"][0]["chains"][0])
        self.assertNotIn("2027-04-03", str(result["tool_calls"]))
        self.assertNotIn("2027-04-05", str(result["tool_calls"]))


class CapacityAndTransferTests(unittest.TestCase):
    def test_units_do_not_consume_extra_slots(self):
        company = (
            week_company()
            .add_plant("PA", 2, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON)
            .add_order("PO-A", "PA", MON, "CAR", units=100)
            .add_order("PO-B", "PA", TUE, "CAR", units=1)
        )
        result = plan_event(company.ontology(), delay_event(shipment_id="SH-1", new_eta="2027-03-10"))
        self.assertEqual(result["status"], "ok", result["blockers"])
        self.assertEqual(moved_to(result), {"PO-A": "2027-03-10", "PO-B": "2027-03-10"})

    def test_same_plant_is_used_before_an_earlier_transfer(self):
        company = (
            week_company()
            .add_plant("PA", 1, ["CAR", "VAN"])
            .add_plant("PB", 1, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON)
            .add_order("PO-A", "PA", MON, "CAR")
            .add_order("PO-FILL-TUE", "PA", TUE, "VAN")
            .add_order("PO-FILL-WED", "PA", WED, "VAN")
        )
        result = plan_event(company.ontology(), delay_event(shipment_id="SH-1", new_eta="2027-03-09"))
        self.assertEqual(result["status"], "ok", result["blockers"])
        self.assertEqual(result["moves"][0]["decision"], "reschedule")
        self.assertEqual(result["moves"][0]["to"], {"plant_id": "PA", "date": "2027-03-11"})
        self.assertNotIn("mes.transfer", [call["tool"] for call in result["tool_calls"]])

    def test_transfer_uses_earliest_legal_day_and_lowest_plant_id(self):
        company = (
            week_company()
            .add_plant("PA", 1, ["CAR", "VAN"])
            .add_plant("P_C", 1, ["CAR"])
            .add_plant("P_B", 1, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON)
            .add_order("PO-A", "PA", MON, "CAR")
        )
        for index, day in enumerate((TUE, WED, THU, FRI)):
            company.add_order(f"PO-FILL-{index}", "PA", day, "VAN")
        result = plan_event(company.ontology(), delay_event(shipment_id="SH-1", new_eta="2027-03-09"))
        self.assertEqual(result["status"], "ok", result["blockers"])
        move = result["moves"][0]
        self.assertEqual(move["decision"], "transfer")
        self.assertEqual(move["to"], {"plant_id": "P_B", "date": "2027-03-09"})
        self.assertEqual(
            move["checked_same_plant_days"],
            ["2027-03-09", "2027-03-10", "2027-03-11", "2027-03-12"],
        )
        self.assertEqual(result["tool_calls"][0]["tool"], "mes.transfer")
        self.assertEqual(
            result["tool_calls"][0]["args"],
            {"production_order_id": "PO-A", "to_plant_id": "P_B", "new_date": "2027-03-09"},
        )

    def test_exhausted_horizon_withholds_every_call(self):
        company = (
            week_company()
            .add_plant("PA", 1, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON)
            .add_order("PO-A", "PA", MON, "CAR")
            .add_order("PO-B", "PA", TUE, "CAR")
            .add_order("PO-C", "PA", WED, "CAR")
        )
        result = plan_event(company.ontology(), delay_event(shipment_id="SH-1", new_eta="2027-03-11"))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["moves"], [])
        self.assertIn("PO-C", result["blockers"][0])
        self.assertEqual(
            [row["production_order_id"] for row in result["affected_orders"]],
            ["PO-A", "PO-B", "PO-C"],
        )


class LoanerAndDealerTests(unittest.TestCase):
    def test_rank_and_pool_and_exact_dealer_groups(self):
        company = (
            week_company()
            .add_plant("PA", 5, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON)
            .add_dealer("D2", 1)
            .add_customer("CU-G-EARLY", "Gold")
            .add_customer("CU-G-LATE", "Gold")
            .add_customer("CU-S", "Standard")
            .add_customer("CU-P2", "Platinum")
            .add_order("PO-A", "PA", MON, "CAR")
            .add_order("PO-B", "PA", TUE, "CAR")
            .add_order("PO-KEEP", "PA", THU, "CAR")
            .add_customer_order("CO-0002", "PO-A", "D1", "CU-G-EARLY", "CAR", date(2027, 3, 10))
            .add_customer_order("CO-0001", "PO-A", "D1", "CU-P", "CAR", date(2027, 3, 20))
            .add_customer_order("CO-0003", "PO-A", "D1", "CU-G-LATE", "CAR", date(2027, 3, 12))
            .add_customer_order("CO-0004", "PO-A", "D1", "CU-S", "CAR", date(2027, 3, 9))
            .add_customer_order("CO-0005", "PO-B", "D2", "CU-P2", "CAR", date(2027, 3, 25))
            .add_customer_order("CO-0009", "PO-KEEP", "D1", "CU-S", "CAR", date(2027, 3, 9))
        )
        company.loaners = [{"dealer_id": "D1", "loaners_available": "1"}, {"dealer_id": "D2", "loaners_available": "1"}]
        result = plan_event(company.ontology(), delay_event(shipment_id="SH-1", new_eta="2027-03-11"))
        self.assertEqual(result["status"], "ok", result["blockers"])
        notices = {call["args"]["dealer_id"]: call["args"]["customer_order_ids"] for call in calls(result, "dealer.notify")}
        self.assertEqual(notices["D1"], ["CO-0001", "CO-0002", "CO-0003", "CO-0004"])
        self.assertEqual(notices["D2"], ["CO-0005"])
        self.assertNotIn("CO-0009", notices["D1"])
        decisions = {row["customer_order_id"]: row["decision"] for row in result["loaner_decisions"]}
        self.assertEqual(decisions["CO-0001"], "assign")
        self.assertEqual(decisions["CO-0002"], "skip_pool")
        self.assertEqual(decisions["CO-0003"], "skip_pool")
        self.assertEqual(decisions["CO-0004"], "skip_tier")
        self.assertEqual(decisions["CO-0005"], "assign")
        assigned = [call["args"]["customer_order_id"] for call in calls(result, "crm.assign_loaner")]
        self.assertEqual(assigned, ["CO-0001", "CO-0005"])


class CompoundTests(unittest.TestCase):
    def test_shared_slots_and_global_loaner_priority(self):
        company = (
            week_company()
            .add_plant("PA", 1, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", TUE)
            .add_customer("CU-G", "Gold")
            .add_order("PO-A", "PA", MON, "CAR")
            .add_order("PO-B", "PA", TUE, "CAR")
            .add_customer_order("CO-A", "PO-A", "D1", "CU-P", "CAR", date(2027, 3, 20))
            .add_customer_order("CO-B", "PO-B", "D1", "CU-G", "CAR", date(2027, 3, 10))
        )
        company.loaners = [{"dealer_id": "D1", "loaners_available": "1"}]
        result = plan_event(
            company.ontology(),
            {
                "event_id": "compound",
                "incidents": [
                    {"kind": "supplier_delay", "shipment_id": "SH-1", "new_eta": "2027-03-11"},
                    {"kind": "plant_outage", "plant_id": "PA", "start_date": "2027-03-08", "end_date": "2027-03-08"},
                ],
            },
        )
        self.assertEqual(result["status"], "ok", result["blockers"])
        self.assertEqual(moved_to(result), {"PO-A": "2027-03-10", "PO-B": "2027-03-11"})
        self.assertEqual([call["tool"] for call in result["tool_calls"][:2]], ["mes.reschedule", "mes.reschedule"])
        notices = calls(result, "dealer.notify")
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["args"]["customer_order_ids"], ["CO-A", "CO-B"])
        assigned = [call["args"]["customer_order_id"] for call in calls(result, "crm.assign_loaner")]
        self.assertEqual(assigned, ["CO-A"])

    def test_overlapping_incidents_move_an_order_once(self):
        company = (
            week_company()
            .add_plant("PA", 5, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON)
            .add_order("PO-A", "PA", MON, "CAR")
            .add_customer_order("CO-A", "PO-A", "D1", "CU-P", "CAR", FRI)
        )
        result = plan_event(
            company.ontology(),
            {
                "event_id": "overlap",
                "incidents": [
                    {"kind": "plant_outage", "plant_id": "PA", "start_date": "2027-03-08", "end_date": "2027-03-09"},
                    {"kind": "supplier_delay", "shipment_id": "SH-1", "new_eta": "2027-03-12"},
                ],
            },
        )
        self.assertEqual(result["status"], "ok", result["blockers"])
        self.assertEqual(moved_to(result), {"PO-A": "2027-03-12"})
        self.assertEqual(result["affected_orders"][0]["incident_indexes"], [0, 1])
        self.assertEqual(result["affected_orders"][0]["legal_start"], "2027-03-12")
        self.assertEqual(len(calls(result, "mes.reschedule")), 1)
        self.assertEqual(len(calls(result, "dealer.notify")), 1)
        self.assertEqual(len(result["graph_paths"]), 2)
        for graph in result["graph_paths"]:
            self.assertIn("ProductionOrder:PO-A", graph["chains"][0])


class FailClosedTests(unittest.TestCase):
    def test_bad_dates_and_identity_conflicts(self):
        company = (
            week_company()
            .add_plant("PA", 5, ["CAR"])
            .add_plant("PB", 5, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON)
            .add_shipment("SH-2", "SUP", "BAT", "PA", MON)
            .add_order("PO-A", "PA", MON, "CAR")
        )
        ontology = company.ontology()
        saturday = plan_event(ontology, delay_event(shipment_id="SH-1", new_eta="2027-03-13"))
        self.assertEqual(saturday["status"], "blocked")
        self.assertEqual(saturday["tool_calls"], [])
        self.assertIn("not a working day", saturday["blockers"][0])

        same_day = plan_event(ontology, delay_event(shipment_id="SH-1", new_eta="2027-03-08"))
        self.assertIn("new_eta must be after original_eta", same_day["blockers"][0])

        conflict = plan_event(
            ontology,
            delay_event(shipment_id="SH-1", plant_id="PB", new_eta="2027-03-10"),
        )
        self.assertIn("contradicts shipment", conflict["blockers"][0])
        self.assertEqual(conflict["tool_calls"], [])

        ambiguous = plan_event(
            ontology,
            delay_event(part_id="BAT", plant_id="PA", original_eta="2027-03-08", new_eta="2027-03-10"),
        )
        self.assertIn("ambiguous shipment", ambiguous["blockers"][0])

        unknown = plan_event(
            ontology,
            {"event_id": "bad-plant", "incidents": [{"kind": "plant_outage", "plant_id": "NOPE", "start_date": "2027-03-08", "end_date": "2027-03-08"}]},
        )
        self.assertIn("unknown plant_id", unknown["blockers"][0])

        backwards = plan_event(
            ontology,
            {"event_id": "backwards", "incidents": [{"kind": "plant_outage", "plant_id": "PA", "start_date": "2027-03-10", "end_date": "2027-03-08"}]},
        )
        self.assertIn("end_date is before start_date", backwards["blockers"][0])

    def test_unknown_fields_are_rejected(self):
        ontology = week_company().add_plant("PA", 1, ["CAR"]).ontology()
        with self.assertRaises(InputError):
            plan_event(ontology, {"event_id": "extra", "incidents": [], "note": "nope"})
        with self.assertRaises(InputError):
            plan_event(ontology, {"event_id": "q", "incidents": [{"kind": "quality_hold", "lot_id": "LOT-X", "note": "x"}]})

    def test_one_invalid_incident_withholds_the_valid_one(self):
        company = (
            week_company()
            .add_plant("PA", 5, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_shipment("SH-1", "SUP", "BAT", "PA", MON)
            .add_order("PO-A", "PA", MON, "CAR")
            .add_customer_order("CO-A", "PO-A", "D1", "CU-P", "CAR", FRI)
        )
        result = plan_event(
            company.ontology(),
            {
                "event_id": "mixed",
                "incidents": [
                    {"kind": "supplier_delay", "shipment_id": "SH-1", "new_eta": "2027-03-10"},
                    {
                        "kind": "plant_outage",
                        "plant_id": "MISSING",
                        "start_date": "2027-03-08",
                        "end_date": "2027-03-08",
                    },
                ],
            },
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["incidents"][0]["shipment_id"], "SH-1")
        self.assertIn("unknown plant_id", result["blockers"][0])

    def test_empty_incidents_do_nothing(self):
        result = plan_event(week_company().add_plant("PA", 1, ["CAR"]).ontology(), {"event_id": "empty", "incidents": []})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tool_calls"], [])

    def test_solution_sources_do_not_name_scored_events(self):
        for path in (ROOT / "solution").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("events.json", text, path)
            self.assertNotIn("E-01", text, path)


if __name__ == "__main__":
    unittest.main()
