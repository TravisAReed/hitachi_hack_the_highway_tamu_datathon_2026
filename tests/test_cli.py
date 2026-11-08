"""The dry-run command prints a plan and does not need the company files for --demo."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_demo_command(self):
        completed = subprocess.run(
            [sys.executable, "-m", "solution.hth.dry_run", "--demo"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["event_id"], "demo-delay")
        self.assertEqual(
            [move["production_order_id"] for move in payload["moves"]],
            ["PO-A", "PO-B"],
        )
        self.assertTrue(all(move["to"]["date"] == "2027-03-10" for move in payload["moves"]))
        notices = [call for call in payload["tool_calls"] if call["tool"] == "dealer.notify"]
        self.assertEqual(notices[0]["args"]["customer_order_ids"], ["CO-A", "CO-B", "CO-S"])
        assigned = [call["args"]["customer_order_id"] for call in payload["tool_calls"] if call["tool"] == "crm.assign_loaner"]
        self.assertEqual(assigned, ["CO-A"])
        reasons = {row["production_order_id"]: row["reason"] for row in payload["findings"]}
        self.assertEqual(reasons["PO-C"], "bom_miss")
        self.assertEqual(reasons["PO-D"], "wrong_plant")
        self.assertEqual(reasons["PO-E"], "on_or_after_new_eta")
        self.assertEqual(reasons["PO-F"], "on_or_after_new_eta")
        self.assertEqual(payload["incidents"][0]["covers_days"], 99)
        self.assertNotIn("CO-U", notices[0]["args"]["customer_order_ids"])

    def test_quality_demo_command(self):
        completed = subprocess.run(
            [sys.executable, "-m", "solution.hth.dry_run", "--demo-quality"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["event_id"], "demo-quality")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["tool_calls"][0]["tool"], "quality.hold_lot")
        stops = [call for call in payload["tool_calls"] if call["tool"] == "dealer.stop_sale"]
        self.assertEqual(stops[0]["args"]["vins"], ["VIN-STOCK"])
        owners = [call["args"]["customer_id"] for call in payload["tool_calls"] if call["tool"] == "crm.notify_customer"]
        self.assertEqual(owners, ["CU-OWN"])
        reasons = {row["vin"]: row["reason"] for row in payload["vehicle_findings"]}
        self.assertEqual(reasons["VIN-VAN"], "bom_miss")
        self.assertEqual(reasons["VIN-OTHER"], "wrong_plant")

    def test_output_inside_data_dir_is_refused(self):
        data = ROOT / "hack_the_highway_student_package" / "hack_the_highway_package" / "data"
        with tempfile.TemporaryDirectory() as tmp:
            incident = Path(tmp) / "incident.json"
            incident.write_text(
                json.dumps({"event_id": "none", "incidents": []}),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "solution.hth.dry_run",
                    "--data",
                    str(data),
                    "--input",
                    str(incident),
                    "--output",
                    str(data / "plan.json"),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertFalse((data / "plan.json").exists())
        self.assertIn("refusing", completed.stderr)


if __name__ == "__main__":
    unittest.main()
