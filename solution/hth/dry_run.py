"""Command line for a dry-run plan. Never calls enterprise write tools."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .calendarutil import is_working_day
from .demo import build_demo, build_parse_demo, build_quality_demo
from .incidents import InputError
from .interpret import InterpretError, interpret_and_plan, stub_client
from .llm import LlmConfigError, OpenAICompatibleClient, SchemaError, load_llm_config
from .ontology import OntologyError, load_ontology
from .policy import plan_event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Propose delay, outage, quality-hold, noise, and escalation actions. "
            "Does not call the enterprise server or write an action log."
        )
    )
    parser.add_argument("--data", type=Path, help="Company CSV directory from the challenge package")
    parser.add_argument("--input", type=Path, help="JSON object with event_id and structured incidents")
    parser.add_argument("--text", help="Free-text event. Requires --event-id and --parser.")
    parser.add_argument("--text-file", type=Path, help="File containing one free-text event")
    parser.add_argument("--event-id", help="Event id to stamp on proposed calls for a free-text event")
    parser.add_argument("--parser", choices=("stub", "live"), help="Free-text parser. There is no heuristic fallback.")
    parser.add_argument("--stub-file", type=Path, help="JSON extraction from a labeled stub. Not a live model response.")
    parser.add_argument("--output", type=Path, help="Write the plan JSON here. Default is stdout.")
    parser.add_argument("--horizon", help="Optional horizon end YYYY-MM-DD, a working day on or before 2027-04-02")
    parser.add_argument("--demo", action="store_true", help="Run a fictional one-week delay. Ignores the company files.")
    parser.add_argument("--demo-quality", action="store_true", help="Run a fictional quality hold. Ignores the company files.")
    parser.add_argument(
        "--demo-parse",
        action="store_true",
        help="Parse a fictional sentence with a labeled stub. This is not a live model call.",
    )
    parser.add_argument("--check-data", action="store_true", help="Load the ontology and print row counts")
    args = parser.parse_args(argv)
    demo_flags = (args.demo, args.demo_quality, args.demo_parse)
    if sum(bool(flag) for flag in demo_flags) > 1:
        return _fail("pass only one demo flag")
    demo = any(demo_flags)
    if args.text and args.text_file:
        return _fail("pass only one of --text and --text-file")
    if (args.text or args.text_file) and args.input:
        return _fail("pass structured --input or free text, not both")
    if demo and (args.input or args.check_data or args.text or args.text_file):
        return _fail("a demo does not take --input, --text, or --check-data")
    if not demo and args.data is None:
        return _fail("pass --data, or pass a demo flag")
    if args.input and args.check_data:
        return _fail("pass either --input or --check-data")
    text_mode = bool(args.text or args.text_file)
    if text_mode and not args.event_id:
        return _fail("free text needs --event-id")
    if text_mode and args.parser is None:
        return _fail("free text needs --parser stub or --parser live; there is no heuristic fallback")
    if args.parser == "live" and args.stub_file:
        return _fail("--parser live does not take --stub-file")
    if args.parser == "stub" and text_mode and args.stub_file is None:
        return _fail("--parser stub needs --stub-file")
    if not demo and not text_mode and args.input is None and not args.check_data:
        return _fail("pass --input, --text, --check-data, or a demo flag")

    horizon = None
    if args.horizon:
        try:
            horizon = date.fromisoformat(args.horizon)
        except ValueError:
            return _fail("--horizon must be YYYY-MM-DD")
        if not is_working_day(horizon):
            return _fail("--horizon must be a working day")

    try:
        if args.demo:
            ontology, event = build_demo()
            payload = plan_event(ontology, event)
        elif args.demo_quality:
            ontology, event = build_quality_demo()
            payload = plan_event(ontology, event)
        elif args.demo_parse:
            ontology, event_id, text, stub = build_parse_demo()
            payload = interpret_and_plan(ontology, event_id, text, stub_client(stub))
        elif args.check_data:
            ontology = load_ontology(args.data, horizon)
            payload = {"status": "ok", "data": str(args.data), "counts": ontology.counts()}
        elif text_mode:
            ontology = load_ontology(args.data, horizon)
            text = args.text if args.text is not None else args.text_file.read_text(encoding="utf-8")
            if args.parser == "stub":
                client = stub_client(_read_event(args.stub_file))
            else:
                client = OpenAICompatibleClient(load_llm_config())
            payload = interpret_and_plan(ontology, args.event_id, text, client)
        else:
            ontology = load_ontology(args.data, horizon)
            payload = plan_event(ontology, _read_event(args.input))
    except (InputError, InterpretError, LlmConfigError, SchemaError, OntologyError, OSError, json.JSONDecodeError) as exc:
        return _fail(str(exc))

    return _emit(payload, args.output, None if demo else args.data)


def _read_event(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise InputError("input JSON must be an object with event_id and incidents")
    return payload


def _emit(payload: dict, output: Path | None, data_root: Path | None) -> int:
    text = json.dumps(payload, indent=2) + "\n"
    if output is None:
        sys.stdout.write(text)
        return 0
    destination = output.resolve()
    if data_root is not None:
        root = data_root.resolve()
        if destination == root or root in destination.parents:
            return _fail("refusing to write the plan inside the company data directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return 0


def _fail(message: str) -> int:
    sys.stderr.write(json.dumps({"status": "error", "blockers": [message]}) + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
