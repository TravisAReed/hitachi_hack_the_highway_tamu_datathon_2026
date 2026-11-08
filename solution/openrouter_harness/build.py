"""Assemble a zip that runs without this repository."""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "solution" / "hth"
HARNESS = Path(__file__).resolve().parent / "harness_main.py"

VENDORED = (
    "__init__.py",
    "calendarutil.py",
    "incidents.py",
    "dates.py",
    "ontology.py",
    "policy.py",
    "agents.py",
    "llm.py",
    "interpret.py",
    "openrouter_eval.py",
)

RUN_TEXT = """Standalone fictional OpenRouter evaluation

Python 3.11 or newer. No extra packages. This zip does not contain challenge
tables, events, credentials, or an enterprise client.

Offline self-test. No API key and no network:

    python3 hth_openrouter_eval.zip --self-test
    python3 hth_openrouter_eval.zip --self-test --output self_test.json

Live run, only on the computer where HTH_LLM_API_KEY is already in the
environment. Do not put the key on the command line or in a file. The harness
reads os.environ and does not print the key or request headers.

    python3 hth_openrouter_eval.zip --live --confirm-free-quota --output eval_result.json

Limits: model xingchen-agi/xing4.0-29b-a4b:free, Phala only, strict JSON schema,
data_collection deny, no model fallback, at most 10 cases, one HTTP attempt
each, sequential and under 20 starts in any 60 seconds. Auth, payment, quota,
and a provider or model mismatch abort the suite. Run one copy at a time.

The JSON report separates cases, http_attempts, and local_rejections. cases
counts messages considered. http_attempts counts transport calls, including
failures. A live run that considers cases but makes no HTTP attempt exits 2
and sets integration_error. That condition does not consume quota.

The output JSON has decisions, tool names, and the returned model and provider
when the endpoint sends them. If the API key would appear in that JSON, the
program refuses to write it.
"""


def build_zip(destination: Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(HARNESS, "__main__.py")
        archive.writestr("RUN.txt", RUN_TEXT)
        for name in VENDORED:
            source = PACKAGE / name
            archive.write(source, f"hth/{name}")
    return destination


def main() -> None:
    destination = build_zip(Path("hth_openrouter_eval.zip"))
    print(destination.resolve())


if __name__ == "__main__":
    main()
