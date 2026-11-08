"""The standalone zip matches the repo modules and does not call the network."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solution.hth.openrouter_eval import CASES, ScriptedExtractor, run_evaluation
from solution.openrouter_harness.build import VENDORED, build_zip

PACKAGE = ROOT / "solution" / "hth"
SENTINEL = "sentinel-key-value-not-real"


class StandaloneHarnessTests(unittest.TestCase):
    def test_zip_bytes_and_offline_decisions_match_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive = build_zip(directory / "hth_openrouter_eval.zip")
            with zipfile.ZipFile(archive) as zipped:
                names = set(zipped.namelist())
                self.assertIn("__main__.py", names)
                self.assertIn("RUN.txt", names)
                for name in names:
                    lowered = name.lower()
                    self.assertNotIn("events.json", lowered)
                    self.assertNotIn("enterprise", lowered)
                    self.assertFalse(lowered.endswith(".env"))
                for name in VENDORED:
                    self.assertEqual(zipped.read(f"hth/{name}"), (PACKAGE / name).read_bytes())
            output = directory / "self_test.json"
            env = os.environ.copy()
            env["HTH_LLM_API_KEY"] = SENTINEL
            env.pop("PYTHONPATH", None)
            completed = subprocess.run(
                [sys.executable, str(archive), "--self-test", "--output", str(output)],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            combined = completed.stdout + completed.stderr + (output.read_text(encoding="utf-8") if output.exists() else "")
            self.assertNotIn(SENTINEL, combined)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["self_test"], "ok")
            self.assertEqual(report["network_calls"], 0)
            self.assertFalse(report["live_success_claimed"])
            self.assertEqual(report["calls"], 10)
            for name, digest in report["module_sha256"].items():
                self.assertEqual(hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest(), digest)
            local = run_evaluation(ScriptedExtractor([case["response"] for case in CASES]), 10)
            self.assertEqual(
                [(row["id"], row["decision"], row["tools"], row["writes"]) for row in report["results"]],
                [(row["id"], row["decision"], row["tools"], row["writes"]) for row in local["results"]],
            )

    def test_live_flags_fail_closed_without_a_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive = build_zip(directory / "hth_openrouter_eval.zip")
            env = os.environ.copy()
            env["HTH_LLM_API_KEY"] = SENTINEL
            env.pop("PYTHONPATH", None)
            unconfirmed = subprocess.run(
                [sys.executable, str(archive), "--live"],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(unconfirmed.returncode, 2)
            self.assertIn("confirm-free-quota", unconfirmed.stderr)
            self.assertNotIn(SENTINEL, unconfirmed.stdout + unconfirmed.stderr)
            missing = env.copy()
            missing.pop("HTH_LLM_API_KEY", None)
            refused = subprocess.run(
                [sys.executable, str(archive), "--live", "--confirm-free-quota"],
                cwd=directory,
                check=False,
                capture_output=True,
                text=True,
                env=missing,
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("HTH_LLM_API_KEY", refused.stderr)
            self.assertNotIn("live model request failed", refused.stderr)


if __name__ == "__main__":
    unittest.main()
