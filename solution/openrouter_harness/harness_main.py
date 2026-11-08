"""Standalone runner. Live mode talks only to the pinned OpenRouter chat endpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fictional OpenRouter evaluation. See RUN.txt.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true", help="Offline checks. No key and no network.")
    mode.add_argument("--live", action="store_true", help="Call the pinned free route. Requires the environment key.")
    mode.add_argument(
        "--diagnose",
        action="store_true",
        help="One fictional supplier-delay request. No retries and no account query.",
    )
    parser.add_argument("--confirm-free-quota", action="store_true")
    parser.add_argument("--budget", type=int, default=10)
    parser.add_argument("--output", type=Path, help="Write sanitized JSON here.")
    args = parser.parse_args(argv)
    if args.budget < 1 or args.budget > 10:
        return _fail("budget must be from 1 to 10")
    try:
        if args.self_test:
            report = self_test(args.budget)
        else:
            if not args.confirm_free_quota:
                return _fail(
                    "refusing the live eval without --confirm-free-quota. "
                    "Free accounts allow 50 requests/day and 20/minute, and failed attempts count."
                )
            report = diagnose() if args.diagnose else live_run(args.budget)
    except Exception as exc:
        return _fail(_public_message(exc))
    code = _emit(report, args.output)
    if report.get("integration_error"):
        return _fail(str(report["integration_error"]))
    return code


def self_test(budget: int) -> dict:
    _block_network()
    from hth.llm import (
        OPENROUTER_BASE_URL,
        OPENROUTER_MODEL,
        OPENROUTER_PROVIDER,
        LlmConfig,
        LiveCallError,
        OpenAICompatibleClient,
        build_chat_request,
        extraction_json_schema,
        load_openrouter_config,
    )
    from hth.openrouter_eval import CASES, QueueTransport, ScriptedExtractor, run_evaluation

    _refuse_data_reads()
    if len(CASES) != 10:
        raise RuntimeError("the bundled suite is not the 10 fictional cases")
    sentinel = "sentinel-key-value-not-real"
    config = LlmConfig(OPENROUTER_BASE_URL, OPENROUTER_MODEL, sentinel, route="openrouter")
    request = build_chat_request(config, CASES[0]["text"])
    body = request.body
    encoded = json.dumps(body)
    if sentinel in encoded or "Authorization" in encoded:
        raise RuntimeError("request body failed the secret check")
    if body["model"] != OPENROUTER_MODEL:
        raise RuntimeError("model pin failed")
    if body["response_format"]["type"] != "json_schema" or body["response_format"]["json_schema"]["strict"] is not True:
        raise RuntimeError("strict schema failed")
    if body["response_format"]["json_schema"]["schema"] != extraction_json_schema():
        raise RuntimeError("request schema drifted from the validator schema")
    provider = body["provider"]
    if provider != {
        "only": [OPENROUTER_PROVIDER],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
    }:
        raise RuntimeError("provider routing failed")
    if "models" in body or "fallbacks" in body:
        raise RuntimeError("fallback fields are present")
    try:
        load_openrouter_config({})
    except Exception as exc:
        if "HTH_LLM_API_KEY" not in str(exc) or sentinel in str(exc):
            raise RuntimeError("missing-key check failed") from None
    else:
        raise RuntimeError("missing key was accepted")
    try:
        load_openrouter_config({"HTH_LLM_API_KEY": sentinel, "HTH_LLM_MODEL": "openai/gpt-4o"})
    except Exception as exc:
        if sentinel in str(exc):
            raise RuntimeError("model refusal exposed the sentinel") from None
    else:
        raise RuntimeError("a different model was accepted")

    scripted = ScriptedExtractor([case["response"] for case in CASES])
    report = run_evaluation(scripted, budget)
    if scripted.llm_invoked or urllib.request.urlopen is not _blocked_urlopen:
        raise RuntimeError("scripted run touched the network")
    for case, row in zip(CASES, report["results"]):
        if row["decision"] != case["decision"] or row["writes"] != case["writes"]:
            raise RuntimeError("local decision drifted for " + case["id"])
        if case["text"] not in scripted.prompts:
            raise RuntimeError("fixture prompt was not the bundled case")
    _abort_checks(config, LiveCallError, OpenAICompatibleClient, QueueTransport, run_evaluation)
    _window_check()
    guarded = _guarded_mock_run(config, sentinel)
    if guarded["http_attempts"] != 10 or guarded["http_attempts"] != guarded["transport_requests"]:
        raise RuntimeError("guarded live plumbing did not attempt one HTTP call per case")
    if guarded.get("integration_error"):
        raise RuntimeError("guarded live plumbing was reported as an integration failure")
    for case, row in zip(CASES, guarded["results"]):
        if row["decision"] != case["decision"] or row["writes"] != case["writes"]:
            raise RuntimeError("guarded mock decision drifted for " + case["id"])
    zero_attempt = _integration_error(
        {"http_attempts": 0, "aborted": None, "results": [{"decision": "rejected"}]}
    )
    if zero_attempt != "live evaluation made no HTTP attempt":
        raise RuntimeError("a live run with zero HTTP attempts was treated as success")
    report["self_test"] = "ok"
    report["network_calls"] = 0
    report["guarded_mock_cases"] = guarded["cases"]
    report["guarded_mock_http_attempts"] = guarded["http_attempts"]
    report["guarded_mock_local_rejections"] = guarded["local_rejections"]
    report["zero_attempt_is_failure"] = True
    diagnostic = _diagnose_mock(config, sentinel)
    if diagnostic["http_attempts"] != 1 or diagnostic["transport_requests"] != 1:
        raise RuntimeError("diagnostic sent more than one request")
    if diagnostic["case_id"] != "supplier-delay" or diagnostic["completions"] != 0:
        raise RuntimeError("diagnostic did not stay on the first fictional case")
    if diagnostic["limit_origin"] != "openrouter_platform":
        raise RuntimeError("platform 429 fixture was not recognized")
    report["diagnostic_mock_http_attempts"] = diagnostic["http_attempts"]
    report["diagnostic_mock_limit_origin"] = diagnostic["limit_origin"]
    report["module_sha256"] = _module_hashes()
    report["live_success_claimed"] = False
    return _sanitize(report, sentinel)


def diagnose(config=None, transport=None) -> dict:
    """One chat request, the first fictional supplier-delay case. No second call and no key lookup."""
    from hth.llm import OpenAICompatibleClient, load_openrouter_config
    from hth.openrouter_eval import CASES, run_evaluation

    if CASES[0]["id"] != "supplier-delay":
        raise RuntimeError("diagnostic case is not the fictional supplier delay")
    if config is None:
        config = load_openrouter_config()
    secret = config.api_key or ""
    inner = transport or urllib.request.urlopen
    capped = _OneShotTransport(inner)
    client = _GuardedClient(OpenAICompatibleClient(config, transport=capped), _StartWindow())
    try:
        report = run_evaluation(client, 1)
    finally:
        config = None
    report["diagnostic"] = True
    report["case_id"] = "supplier-delay"
    report["network_attempts"] = int(report.get("http_attempts") or 0)
    report["completions"] = 1 if client.llm_invoked else 0
    report["transport_requests"] = capped.calls
    error = report["results"][0].get("http_error") if report["results"] else None
    report["limit_origin"] = error.get("limit_origin") if isinstance(error, dict) else None
    report["limit_origin_basis"] = error.get("limit_origin_basis") if isinstance(error, dict) else None
    report["live_success_claimed"] = False
    report["self_test"] = None
    if capped.calls > 1:
        report["integration_error"] = "diagnostic sent more than one request"
    problem = _integration_error(report)
    if problem and not report.get("integration_error"):
        report["integration_error"] = problem
    return _sanitize(report, secret)


def live_run(budget: int) -> dict:
    from hth.llm import OpenAICompatibleClient, load_openrouter_config
    from hth.openrouter_eval import run_evaluation

    config = load_openrouter_config()
    secret = config.api_key or ""
    client = _GuardedClient(OpenAICompatibleClient(config), _StartWindow())
    try:
        report = run_evaluation(client, budget)
    finally:
        config = None
    report["live_success_claimed"] = False
    report["self_test"] = None
    problem = _integration_error(report)
    if problem:
        report["integration_error"] = problem
    return _sanitize(report, secret)


def _abort_checks(config, error_type, client_type, transport_type, run_evaluation) -> None:
    from hth.openrouter_eval import CASES

    checks = (
        (401, "auth"),
        (402, "payment"),
        (429, "quota"),
    )
    for status, kind in checks:
        transport = transport_type([(status, {})])
        client = client_type(config, transport=transport)
        stopped = run_evaluation(client, 10)
        if stopped["aborted"] != kind or stopped["calls"] != 1 or client.attempts != 1 or len(transport.requests) != 1:
            raise RuntimeError(kind + " abort check failed")
    mismatch = transport_type([(200, {"model": config.model, "provider": "Other", "choices": [{"message": {"content": "{}"}}]})])
    client = client_type(config, transport=mismatch)
    try:
        client.complete(CASES[3]["text"])
    except error_type as exc:
        if exc.kind != "fallback" or client.attempts != 1:
            raise RuntimeError("provider mismatch check failed") from None
    else:
        raise RuntimeError("provider mismatch was accepted")
    other_model = transport_type(
        [(200, {"model": "other/paid", "provider": "Phala", "choices": [{"message": {"content": "{}"}}]})]
    )
    client = client_type(config, transport=other_model)
    try:
        client.complete(CASES[3]["text"])
    except error_type as exc:
        if exc.kind != "fallback" or client.attempts != 1:
            raise RuntimeError("model mismatch check failed") from None
    else:
        raise RuntimeError("model mismatch was accepted")


def _window_check() -> None:
    from hth.llm import LiveCallError

    clock = {"now": 0.0}

    def now() -> float:
        return clock["now"]

    window = _StartWindow(clock=now)
    for _ in range(19):
        window.claim()
    try:
        window.claim()
    except LiveCallError as exc:
        if exc.kind != "quota":
            raise RuntimeError("rate window check failed") from None
    else:
        raise RuntimeError("the 20th start in one minute was allowed")
    clock["now"] = 60.0
    window.claim()


class _OneShotTransport:
    """Stop before a second socket call. This is not a retry."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def __call__(self, request, timeout=60):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("diagnostic network cap exceeded")
        return self._inner(request, timeout)


class _StartWindow:
    """Abort before a 20th start inside 60 seconds. This is not a retry."""

    def __init__(self, clock=None):
        import time

        self.clock = time.monotonic if clock is None else clock
        self.starts: list[float] = []

    def claim(self) -> None:
        from hth.llm import LiveCallError

        now = self.clock()
        self.starts = [stamp for stamp in self.starts if now - stamp < 60]
        if len(self.starts) >= 19:
            raise LiveCallError(None, "quota")
        self.starts.append(now)


class _GuardedClient:
    """Rate-window around the live adapter. Attribute reads use the inner client."""

    def __init__(self, inner, window: _StartWindow):
        self._inner = inner
        self._window = window

    def complete(self, event_text: str) -> dict:
        self._window.claim()
        return self._inner.complete(event_text)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def _diagnose_mock(config, sentinel: str) -> dict:
    from email.message import Message
    from io import BytesIO
    from urllib.error import HTTPError

    from hth.llm import OPENROUTER_BASE_URL, describe_http_error
    from hth.openrouter_eval import CASES

    _redaction_fixtures(config.api_key, describe_http_error)
    body = json.dumps(
        {
            "id": "gen-diagnostic1",
            "error": {
                "code": 429,
                "message": "Rate limit exceeded",
                "metadata": {"error_type": "rate_limit_exceeded"},
            },
        }
    ).encode("utf-8")
    headers = Message()
    headers["X-RateLimit-Limit"] = "50"
    headers["X-RateLimit-Remaining"] = "0"
    headers["X-RateLimit-Reset"] = "1762646400000"
    headers["Retry-After"] = "60"
    headers["Set-Cookie"] = "session=secret"
    headers["Authorization"] = "Bearer " + sentinel

    def transport(request, timeout):
        del timeout
        transport.requests.append(request)
        raise HTTPError(request.full_url, 429, "Too Many Requests", headers, BytesIO(body))

    transport.requests = []
    report = diagnose(config, transport)
    encoded = json.dumps(report)
    if sentinel in encoded or "session=secret" in encoded or "Bearer" in encoded:
        raise RuntimeError("diagnostic report kept a credential")
    if report["results"][0]["id"] != "supplier-delay":
        raise RuntimeError("diagnostic used a different case")
    sent = json.loads(transport.requests[0].data.decode("utf-8"))
    if CASES[0]["text"] not in sent["messages"][1]["content"] or CASES[1]["text"] in sent["messages"][1]["content"]:
        raise RuntimeError("diagnostic did not send only the first fictional case")
    if transport.requests[0].full_url != OPENROUTER_BASE_URL + "/chat/completions":
        raise RuntimeError("diagnostic used an unexpected URL")
    if "Set-Cookie" in encoded or "Authorization" in encoded:
        raise RuntimeError("diagnostic kept a forbidden header")
    return report


def _redaction_fixtures(secret: str, describe) -> None:
    from email.message import Message
    from io import BytesIO
    from urllib.error import HTTPError

    upstream = _http_error(
        429,
        {"error": {"code": 429, "message": "Provider returned error", "metadata": {"provider_code": "rate_limited", "provider_name": "Phala"}}},
        {"Retry-After": "17"},
    )
    upstream_detail = describe(upstream, secret)
    if upstream_detail["limit_origin"] != "upstream_provider":
        raise RuntimeError("upstream 429 fixture was not recognized")
    unknown = _http_error(429, {"error": {"code": 429, "message": "Rate limit exceeded"}}, {"Retry-After": "9"})
    if describe(unknown, secret)["limit_origin"] != "unknown":
        raise RuntimeError("an under-specified 429 was classified")
    conflict = _http_error(
        429,
        {"error": {"code": 429, "message": "Rate limit exceeded", "metadata": {"provider_code": "rate_limited"}}},
        {"X-RateLimit-Limit": "20", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1"},
    )
    if describe(conflict, secret)["limit_origin"] != "unknown":
        raise RuntimeError("conflicting 429 evidence was classified")
    leak = _http_error(
        429,
        {
            "error": {
                "code": 429,
                "message": "limit " + secret + " Bearer abc.def.ghi sk-or-v1-exampletoken",
                "metadata": {"raw": secret + ("x" * 500)},
            }
        },
        {"X-Request-Id": "gen-safeid1", "Set-Cookie": "session=secret"},
    )
    hidden = json.dumps(describe(leak, secret))
    if secret in hidden or "sk-or-v1-exampletoken" in hidden or "Bearer abc" in hidden or "session=secret" in hidden:
        raise RuntimeError("429 fixture leaked a credential")
    del Message, BytesIO, HTTPError


def _http_error(status: int, payload: dict, headers: dict):
    from email.message import Message
    from io import BytesIO
    from urllib.error import HTTPError

    header_map = Message()
    for key, value in headers.items():
        header_map[key] = value
    return HTTPError("https://openrouter.ai/api/v1/chat/completions", status, "error", header_map, BytesIO(json.dumps(payload).encode("utf-8")))


def _guarded_mock_run(config, sentinel: str) -> dict:
    """One mocked HTTP attempt per fictional case, through the same wrapper as --live."""
    import json

    from hth.llm import OPENROUTER_BASE_URL, OPENROUTER_MODEL, OpenAICompatibleClient
    from hth.openrouter_eval import CASES, QueueTransport, run_evaluation

    envelopes = []
    for case in CASES:
        envelopes.append(
            (
                200,
                {
                    "model": OPENROUTER_MODEL,
                    "provider": "Phala",
                    "choices": [{"message": {"content": json.dumps(case["response"])}}],
                },
            )
        )
    transport = QueueTransport(envelopes)
    client = _GuardedClient(OpenAICompatibleClient(config, transport=transport), _StartWindow())
    if client.mode != "live":
        raise RuntimeError("guarded client is not a live adapter")
    report = run_evaluation(client, 10)
    if urllib.request.urlopen is not _blocked_urlopen:
        raise RuntimeError("guarded mock replaced the network block")
    for request in transport.requests:
        if request.full_url != OPENROUTER_BASE_URL + "/chat/completions":
            raise RuntimeError("guarded mock used an unexpected URL")
        body = json.loads(request.data.decode("utf-8"))
        encoded = json.dumps(body)
        if sentinel in encoded or "Authorization" in encoded:
            raise RuntimeError("request body failed the secret check")
        if body["model"] != OPENROUTER_MODEL or body["provider"]["only"] != ["phala"]:
            raise RuntimeError("guarded mock dropped the provider pin")
        if body["provider"]["allow_fallbacks"] is not False or body["provider"]["data_collection"] != "deny":
            raise RuntimeError("guarded mock dropped the privacy pin")
        if "models" in body or "fallbacks" in body:
            raise RuntimeError("guarded mock added a fallback")
        if body["response_format"]["json_schema"]["strict"] is not True:
            raise RuntimeError("guarded mock dropped strict schema")
    report["transport_requests"] = len(transport.requests)
    return report


def _integration_error(report: dict) -> str | None:
    attempts = int(report.get("http_attempts") or 0)
    if attempts == 0 and report.get("aborted") is None and report.get("results"):
        return "live evaluation made no HTTP attempt"
    return None


def _summary(output: Path, cleaned: dict) -> dict:
    summary = {
        "wrote": str(output),
        "cases": cleaned.get("cases", cleaned.get("calls")),
        "http_attempts": cleaned.get("http_attempts"),
        "local_rejections": cleaned.get("local_rejections"),
        "aborted": cleaned.get("aborted"),
        "self_test": cleaned.get("self_test"),
    }
    if "guarded_mock_http_attempts" in cleaned:
        summary["guarded_mock_http_attempts"] = cleaned["guarded_mock_http_attempts"]
    if "diagnostic_mock_http_attempts" in cleaned:
        summary["diagnostic_mock_http_attempts"] = cleaned["diagnostic_mock_http_attempts"]
        summary["diagnostic_mock_limit_origin"] = cleaned.get("diagnostic_mock_limit_origin")
    if cleaned.get("diagnostic"):
        summary["diagnostic"] = True
        summary["completions"] = cleaned.get("completions")
        summary["limit_origin"] = cleaned.get("limit_origin")
        summary["network_attempts"] = cleaned.get("network_attempts")
    if cleaned.get("integration_error"):
        summary["integration_error"] = cleaned["integration_error"]
    return summary


def _emit(report: dict, output: Path | None) -> int:
    secret = os.environ.get("HTH_LLM_API_KEY") or ""
    cleaned = _sanitize(report, secret)
    text = json.dumps(cleaned, indent=2)
    if secret and secret in text:
        return _fail("refusing to write a report that contains the API key")
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(json.dumps(_summary(output, cleaned)))
    else:
        print(text)
    return 0


def _sanitize(value, secret: str):
    hidden = {"authorization", "api_key", "headers", "hth_llm_api_key"}
    if isinstance(value, dict):
        return {key: _sanitize(child, secret) for key, child in value.items() if str(key).lower() not in hidden}
    if isinstance(value, list):
        return [_sanitize(child, secret) for child in value]
    if isinstance(value, str) and secret and secret in value:
        return "[redacted]"
    return value


def _public_message(exc: BaseException) -> str:
    message = str(exc)
    secret = os.environ.get("HTH_LLM_API_KEY") or ""
    if secret and secret in message:
        return "request failed"
    return message


def _fail(message: str) -> int:
    secret = os.environ.get("HTH_LLM_API_KEY") or ""
    if secret and secret in message:
        message = "request failed"
    print(message, file=sys.stderr)
    return 2


def _block_network() -> None:
    urllib.request.urlopen = _blocked_urlopen


def _blocked_urlopen(*args, **kwargs):
    del args, kwargs
    raise RuntimeError("network blocked")


def _refuse_data_reads() -> None:
    import builtins

    original = builtins.open

    def guarded(file, *args, **kwargs):
        name = str(file).lower()
        challenge_event = "events" + ".json"
        if challenge_event in name or name.endswith(".env") or "enterprise.py" in name or "enterprise_server.py" in name:
            raise RuntimeError("refusing to read challenge or secret data")
        return original(file, *args, **kwargs)

    builtins.open = guarded


def _module_hashes() -> dict[str, str]:
    import hth.llm as llm
    import hth.openrouter_eval as evaluation

    hashes = {}
    for name, module in (("llm.py", llm), ("openrouter_eval.py", evaluation)):
        data = module.__loader__.get_data(module.__spec__.origin)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


if __name__ == "__main__":
    sys.exit(main())
