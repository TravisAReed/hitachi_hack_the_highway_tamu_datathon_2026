"""Offline checks for the pinned OpenRouter request. No network and no challenge events."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solution.hth.llm import (
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    OPENROUTER_PROVIDER,
    LlmConfig,
    LiveCallError,
    OpenAICompatibleClient,
    build_chat_request,
    describe_http_error,
    load_llm_config,
    load_openrouter_config,
)
from solution.hth.openrouter_eval import CASES, QueueTransport, ScriptedExtractor, run_evaluation

DATA = ROOT / "hack_the_highway_student_package" / "hack_the_highway_package" / "data"
WRITE_TOOLS = {
    "mes.reschedule",
    "mes.transfer",
    "dealer.notify",
    "dealer.stop_sale",
    "crm.assign_loaner",
    "crm.notify_customer",
    "quality.hold_lot",
}


def envelope(content: dict, model: str = OPENROUTER_MODEL, provider: str = "Phala") -> dict:
    return {
        "model": model,
        "provider": provider,
        "choices": [{"message": {"content": json.dumps(content)}}],
    }


class OpenRouterRequestTests(unittest.TestCase):
    def test_request_pins_schema_provider_and_omits_fallbacks(self):
        config = LlmConfig(OPENROUTER_BASE_URL, OPENROUTER_MODEL, "test-key-not-real", route="openrouter")
        request = build_chat_request(config, "A fictional picnic note.")
        body = request.body
        self.assertEqual(body["model"], OPENROUTER_MODEL)
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertFalse(body["response_format"]["json_schema"]["schema"]["additionalProperties"])
        self.assertEqual(body["provider"]["only"], [OPENROUTER_PROVIDER])
        self.assertFalse(body["provider"]["allow_fallbacks"])
        self.assertTrue(body["provider"]["require_parameters"])
        self.assertEqual(body["provider"]["data_collection"], "deny")
        self.assertNotIn("models", body)
        self.assertNotIn("fallbacks", body)
        self.assertEqual(body["reasoning"], {"enabled": False, "exclude": True})
        self.assertNotIn("test-key-not-real", json.dumps(body))
        self.assertEqual(request.headers["Authorization"], "Bearer test-key-not-real")

    def test_openrouter_config_refuses_another_model_and_missing_key(self):
        with self.assertRaises(Exception) as missing:
            load_openrouter_config({})
        self.assertIn("HTH_LLM_API_KEY", str(missing.exception))
        self.assertNotIn("sk-", str(missing.exception))
        with self.assertRaises(Exception) as other:
            load_llm_config(
                {
                    "HTH_LLM_BASE_URL": OPENROUTER_BASE_URL,
                    "HTH_LLM_MODEL": "openai/gpt-4o",
                    "HTH_LLM_API_KEY": "test-key-not-real",
                }
            )
        self.assertIn(OPENROUTER_MODEL, str(other.exception))
        self.assertNotIn("test-key-not-real", str(other.exception))

    def test_mock_transport_records_provider_and_does_not_retry(self):
        config = LlmConfig(OPENROUTER_BASE_URL, OPENROUTER_MODEL, "test-key-not-real", route="openrouter")
        transport = QueueTransport([(200, envelope(CASES[0]["response"]))])
        client = OpenAICompatibleClient(config, transport=transport)
        parsed = client.complete(CASES[0]["text"])
        self.assertEqual(parsed["incidents"][0]["category"], "supplier_delay")
        self.assertEqual(client.response_model, OPENROUTER_MODEL)
        self.assertEqual(client.response_provider, "Phala")
        self.assertEqual(client.attempts, 1)
        sent = json.loads(transport.requests[0].data.decode("utf-8"))
        self.assertEqual(sent["provider"]["only"], ["phala"])
        self.assertNotIn("test-key-not-real", json.dumps(sent))

    def test_mismatched_provider_and_payment_do_not_retry(self):
        config = LlmConfig(OPENROUTER_BASE_URL, OPENROUTER_MODEL, "test-key-not-real", route="openrouter")
        mismatch = QueueTransport([(200, envelope(CASES[3]["response"], provider="Other"))])
        client = OpenAICompatibleClient(config, transport=mismatch)
        with self.assertRaises(LiveCallError) as caught:
            client.complete("Where did someone park the picnic basket?")
        self.assertEqual(caught.exception.kind, "fallback")
        self.assertEqual(client.attempts, 1)
        self.assertFalse(client.llm_invoked)

        payment = QueueTransport([(402, {})])
        paid = OpenAICompatibleClient(config, transport=payment)
        with self.assertRaises(LiveCallError) as pay:
            paid.complete("Where did someone park the picnic basket?")
        self.assertEqual(pay.exception.kind, "payment")
        self.assertEqual(paid.attempts, 1)
        self.assertEqual(len(payment.requests), 1)


class FictionalEvaluationTests(unittest.TestCase):
    def test_scripted_suite_covers_categories_without_challenge_text(self):
        events = json.loads((DATA / "events.json").read_text(encoding="utf-8"))
        for case in CASES:
            for event in events:
                self.assertNotIn(case["text"], event["text"])
                self.assertNotIn(event["text"], case["text"])
        categories = {case["response"]["incidents"][0]["category"] for case in CASES}
        self.assertTrue({"supplier_delay", "plant_outage", "quality_hold", "noise", "unconfirmed"} <= categories)
        client = ScriptedExtractor([case["response"] for case in CASES])
        report = run_evaluation(client, budget=10)
        self.assertFalse(report["live_success_claimed"])
        self.assertEqual(report["calls"], 10)
        self.assertFalse(client.llm_invoked)
        by_id = {row["id"]: row for row in report["results"]}
        self.assertEqual(by_id["noise"]["decision"], "noise")
        self.assertFalse(by_id["noise"]["writes"])
        self.assertEqual(by_id["unconfirmed"]["decision"], "escalate")
        self.assertEqual(by_id["unconfirmed"]["tools"], ["escalate"])
        self.assertEqual(by_id["mixed-fail-closed"]["decision"], "escalate")
        self.assertFalse(any(tool in WRITE_TOOLS for tool in by_id["mixed-fail-closed"]["tools"]))
        self.assertEqual(by_id["injection"]["decision"], "rejected")
        self.assertEqual(by_id["ambiguous-entities"]["decision"], "escalate")
        self.assertEqual(by_id["supplier-delay"]["decision"], "plan")
        self.assertTrue(by_id["supplier-delay"]["writes"])
        self.assertNotIn("events.json", " ".join(client.prompts))

    def test_budget_and_auth_abort(self):
        client = ScriptedExtractor([case["response"] for case in CASES])
        report = run_evaluation(client, budget=2)
        self.assertEqual(report["calls"], 2)
        self.assertIsNone(report["aborted"])

        config = LlmConfig(OPENROUTER_BASE_URL, OPENROUTER_MODEL, "test-key-not-real", route="openrouter")
        transport = QueueTransport([(401, {})])
        live = OpenAICompatibleClient(config, transport=transport)
        stopped = run_evaluation(live, budget=10)
        self.assertEqual(stopped["calls"], 1)
        self.assertEqual(stopped["http_attempts"], 1)
        self.assertEqual(stopped["aborted"], "auth")
        self.assertEqual(live.attempts, 1)
        self.assertFalse(stopped["live_success_claimed"])

    def test_cli_refuses_unconfirmed_quota_without_printing_a_key(self):
        env = os.environ.copy()
        env["HTH_LLM_API_KEY"] = "test-key-not-real"
        completed = subprocess.run(
            [sys.executable, "-m", "solution.hth.dry_run", "--evaluate-openrouter"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("confirm-free-quota", completed.stderr)
        self.assertNotIn("test-key-not-real", completed.stderr + completed.stdout)


class RateLimitDiagnosticTests(unittest.TestCase):
    def test_429_origins_and_redaction_use_one_attempt(self):
        from email.message import Message
        from io import BytesIO
        from urllib.error import HTTPError

        secret = "sentinel-key-value-not-real"
        platform = _error(
            429,
            {"id": "gen-platform1", "error": {"code": 429, "message": "Rate limit exceeded " + secret, "metadata": {"error_type": "rate_limit_exceeded"}}},
            {"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1762646400000", "Set-Cookie": "session=secret", "Authorization": "Bearer " + secret},
        )
        platform_detail = describe_http_error(platform, secret)
        self.assertEqual(platform_detail["limit_origin"], "openrouter_platform")
        self.assertEqual(platform_detail["request_id"], "gen-platform1")
        self.assertEqual(platform_detail["retry_after"], None)
        upstream = _error(
            429,
            {"error": {"code": 429, "message": "Provider returned error", "metadata": {"provider_code": "rate_limited", "provider_name": "Phala", "raw": "sk-or-v1-exampletoken"}}},
            {"Retry-After": "17"},
        )
        upstream_detail = describe_http_error(upstream, secret)
        self.assertEqual(upstream_detail["limit_origin"], "upstream_provider")
        self.assertEqual(upstream_detail["retry_after"], "17")
        self.assertNotIn("sk-or-v1-exampletoken", json.dumps(upstream_detail))
        unknown = _error(429, {"error": {"code": 429, "message": "Rate limit exceeded"}}, {"Retry-After": "9"})
        self.assertEqual(describe_http_error(unknown, secret)["limit_origin"], "unknown")
        conflict = _error(
            429,
            {"error": {"code": 429, "message": "Rate limit exceeded", "metadata": {"provider_code": "rate_limited"}}},
            {"X-RateLimit-Limit": "20", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1"},
        )
        self.assertEqual(describe_http_error(conflict, secret)["limit_origin"], "unknown")
        hidden = json.dumps(platform_detail)
        self.assertNotIn(secret, hidden)
        self.assertNotIn("session=secret", hidden)
        self.assertNotIn("Bearer", hidden)
        self.assertNotIn("Authorization", hidden)

        calls = []

        def transport(request, timeout):
            del timeout
            calls.append(request)
            header_map = Message()
            header_map["Retry-After"] = "3"
            raise HTTPError(request.full_url, 429, "Too Many Requests", header_map, BytesIO(b'{"error":{"code":429,"message":"Rate limit exceeded"}}'))

        config = LlmConfig(OPENROUTER_BASE_URL, OPENROUTER_MODEL, secret, route="openrouter")
        client = OpenAICompatibleClient(config, transport=transport)
        with self.assertRaises(LiveCallError) as caught:
            client.complete("Cedar Cart note: shipment SH-9 is late.")
        self.assertEqual(client.attempts, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(caught.exception.kind, "quota")
        self.assertEqual(caught.exception.detail["limit_origin"], "unknown")
        self.assertNotIn(secret, json.dumps(caught.exception.detail))
        del Message, BytesIO, HTTPError


def _error(status: int, payload: dict, headers: dict):
    from email.message import Message
    from io import BytesIO
    from urllib.error import HTTPError

    header_map = Message()
    for key, value in headers.items():
        header_map[key] = value
    return HTTPError("https://openrouter.ai/api/v1/chat/completions", status, "error", header_map, BytesIO(json.dumps(payload).encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
