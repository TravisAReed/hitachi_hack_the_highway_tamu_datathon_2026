"""Role handoffs are real proposals. These tests do not call a live model."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solution.hth.agents import CARE, DEALER, SUPPLY, SharedResources, propose_dealer, validate_role_calls
from solution.hth.interpret import interpret_and_plan, stub_client
from solution.hth.policy import plan_event
from tests.factories import Company
from tests.test_interpret import OUTAGE_TEXT, outage_stub, plant_world


def scheduled_delay() -> tuple[Company, dict]:
    company = (
        Company(date(2027, 3, 12))
        .add_plant("PA", 5, ["CAR"])
        .add_part("BAT", ["CAR"])
        .add_supplier("SUP")
        .add_dealer("D1", 1)
        .add_customer("CU-P", "Platinum")
        .add_shipment("SH-1", "SUP", "BAT", "PA", date(2027, 3, 8))
        .add_order("PO-A", "PA", date(2027, 3, 8), "CAR")
        .add_customer_order("CO-A", "PO-A", "D1", "CU-P", "CAR", date(2027, 3, 20))
    )
    event = {
        "event_id": "role-delay",
        "incidents": [{"kind": "supplier_delay", "shipment_id": "SH-1", "new_eta": "2027-03-10"}],
    }
    return company, event


class RoleContributionTests(unittest.TestCase):
    def test_delay_routes_moves_notices_and_loaners_to_different_roles(self):
        company, event = scheduled_delay()
        ontology = company.ontology()
        before = dict(ontology.loaners)
        result = plan_event(ontology, event)
        self.assertEqual(result["status"], "ok", result["blockers"])
        self.assertEqual(ontology.loaners, before)
        by_role = {}
        for hop in result["orchestration"]["handoffs"]:
            by_role.setdefault(hop["to"], []).append(hop)
        self.assertIn("mes.reschedule", by_role[SUPPLY][0]["tools"])
        self.assertNotIn("dealer.notify", by_role[SUPPLY][0]["tools"])
        self.assertIn("dealer.notify", by_role[DEALER][0]["tools"])
        self.assertNotIn("crm.assign_loaner", by_role[DEALER][0]["tools"])
        self.assertIn("crm.assign_loaner", by_role[CARE][-1]["tools"])
        self.assertNotIn("mes.reschedule", by_role[CARE][-1]["tools"])
        self.assertEqual(result["orchestration"]["specialist_llm_calls"], 0)
        self.assertTrue(result["orchestration"]["resources"]["shared_slot_map"])
        self.assertTrue(result["orchestration"]["resources"]["shared_loaner_pool"])

    def test_quality_does_not_ask_supply_to_move_orders(self):
        company = (
            Company()
            .add_plant("PA", 5, ["CAR"])
            .add_part("BAT", ["CAR"])
            .add_supplier("SUP")
            .add_dealer("D1", 1)
            .add_customer("CU-OWN", "Gold")
            .add_lot("LOT-1", "BAT", "SUP", "PA", date(2027, 2, 26), date(2027, 3, 1))
            .add_order("PO-OLD", "PA", date(2027, 2, 26), "CAR")
            .add_vehicle("VIN-SOLD", "PO-OLD", "CAR", "PA", date(2027, 2, 26), "D1", "sold", "CU-OWN")
            .add_vehicle("VIN-STOCK", "PO-OLD", "CAR", "PA", date(2027, 2, 26), "D1", "in_stock")
        )
        result = plan_event(
            company.ontology(),
            {"event_id": "role-quality", "incidents": [{"kind": "quality_hold", "lot_id": "LOT-1"}]},
        )
        by_role = {hop["to"]: hop for hop in result["orchestration"]["handoffs"]}
        self.assertEqual(by_role[SUPPLY]["tools"], [])
        self.assertEqual(by_role[SUPPLY]["proposal_count"], 0)
        self.assertIn("dealer.stop_sale", by_role[DEALER]["tools"])
        self.assertIn("quality.hold_lot", by_role[CARE]["tools"])
        self.assertIn("crm.notify_customer", by_role[CARE]["tools"])
        self.assertNotIn("crm.assign_loaner", by_role[CARE]["tools"])

    def test_noise_and_unconfirmed_do_not_call_specialists_for_writes(self):
        ontology = plant_world().ontology()
        noise = interpret_and_plan(
            ontology,
            "role-noise",
            "Where is the brochure PDF?",
            stub_client(
                {
                    "incidents": [
                        {
                            "category": "noise",
                            "confirmed": True,
                            "evidence_quotes": ["brochure PDF"],
                            "entities": [],
                            "dates": [],
                        }
                    ]
                }
            ),
        )
        self.assertEqual(noise["tool_calls"], [])
        self.assertTrue(all(hop["called"] is False for hop in noise["orchestration"]["handoffs"]))

        rumor = interpret_and_plan(
            ontology,
            "role-rumor",
            OUTAGE_TEXT + " Nothing confirmed.",
            stub_client(
                {
                    "incidents": [
                        {
                            "category": "unconfirmed",
                            "confirmed": False,
                            "evidence_quotes": ["Nothing confirmed"],
                            "entities": [],
                            "dates": [],
                        }
                    ]
                }
            ),
        )
        self.assertEqual([call["tool"] for call in rumor["tool_calls"]], ["escalate"])
        self.assertTrue(all(hop["called"] is False for hop in rumor["orchestration"]["handoffs"]))
        self.assertEqual(rumor["orchestration"]["specialist_llm_calls"], 0)

    def test_allowlist_rejects_a_supply_proposal_that_notifies_a_dealer(self):
        errors = validate_role_calls(
            SUPPLY,
            [{"tool": "dealer.notify", "args": {"dealer_id": "D1", "customer_order_ids": ["CO-A"], "message": "x"}}],
        )
        self.assertTrue(errors)
        self.assertIn("allowlist", errors[0])
        clean = validate_role_calls(
            SUPPLY,
            [{"tool": "mes.reschedule", "args": {"production_order_id": "PO-A", "new_date": "2027-03-10"}}],
        )
        self.assertEqual(clean, [])

    def test_dealer_role_groups_orders_and_does_not_assign_loaners(self):
        company, event = scheduled_delay()
        ontology = company.ontology()
        resources = SharedResources.for_ontology(ontology)
        before = dict(resources.loaners)
        dealer = propose_dealer(ontology, ["PO-A"], [])
        self.assertEqual(dealer.counts["notice_count"], 1)
        self.assertEqual(resources.loaners, before)
        self.assertTrue(all(call["tool"] in {"dealer.notify", "dealer.stop_sale"} for call in dealer.tools))
        del event

    def test_events_do_not_share_a_loaner_pool(self):
        company, event = scheduled_delay()
        ontology = company.ontology()
        first = plan_event(ontology, event)
        second = plan_event(ontology, {**event, "event_id": "role-delay-2"})
        self.assertEqual(
            [call["args"] for call in first["tool_calls"] if call["tool"] == "crm.assign_loaner"],
            [call["args"] for call in second["tool_calls"] if call["tool"] == "crm.assign_loaner"],
        )
        self.assertEqual(ontology.loaners["D1"], 1)


class ArchitectureExportTests(unittest.TestCase):
    def test_export_uses_loaded_counts_and_no_specialist_model(self):
        data = ROOT / "hack_the_highway_student_package" / "hack_the_highway_package" / "data"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "architecture.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "solution.hth.dry_run",
                    "--data",
                    str(data),
                    "--export-architecture",
                    str(destination),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(destination.read_text(encoding="utf-8"))
        plants = next(node for node in document["nodes"] if node["id"] == "Plant")
        self.assertEqual(plants["records"], 6)
        self.assertEqual(document["roles"]["specialist_llm_calls"], 0)
        linked = {edge["from"] for edge in document["edges"]} | {edge["to"] for edge in document["edges"]}
        self.assertIn("Shipment", linked)
        self.assertIn("Lot", linked)
        self.assertTrue(all(role["llm"] is False for role in document["roles"]["specialists"]))


if __name__ == "__main__":
    unittest.main()
