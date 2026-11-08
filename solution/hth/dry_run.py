"""Command line for a dry-run plan. Never calls enterprise write tools."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .calendarutil import is_working_day
from .demo import build_demo, build_quality_demo
from .incidents import InputError
from .ontology import OntologyError, load_ontology
from .policy import plan_event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Propose delay, outage, and quality-hold actions. Does not call the enterprise server or write an action log."
    )
    parser.add_argument("--data", type=Path, help="Company CSV directory from the challenge package")
    parser.add_argument("--input", type=Path, help="JSON object with event_id and structured incidents")
    parser.add_argument("--output", type=Path, help="Write the plan JSON here. Default is stdout.")
    parser.add_argument("--horizon", help="Optional horizon end YYYY-MM-DD, a working day on or before 2027-04-02")
    parser.add_argument("--demo", action="store_true", help="Run a fictional one-week delay. Ignores the company files.")
    parser.add_argument("--demo-quality", action="store_true", help="Run a fictional quality hold. Ignores the company files.")
    parser.add_argument("--check-data", action="store_true", help="Load the ontology and print row counts")
    args = parser.parse_args(argv)
    demo = args.demo or args.demo_quality

    if args.demo and args.demo_quality:
        return _fail("pass only one of --demo and --demo-quality")
    if demo and (args.input or args.check_data):
        return _fail("a demo does not take --input or --check-data")
    if not demo and args.data is None:
        return _fail("pass --data, or pass --demo or --demo-quality")
    if args.input and args.check_data:
        return _fail("pass either --input or --check-data")
    if not demo and args.input is None and not args.check_data:
        return _fail("pass --input, --check-data, --demo, or --demo-quality")

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
        elif args.check_data:
            ontology = load_ontology(args.data, horizon)
            payload = {"status": "ok", "data": str(args.data), "counts": ontology.counts()}
        else:
            ontology = load_ontology(args.data, horizon)
            payload = plan_event(ontology, _read_event(args.input))
    except (InputError, OntologyError, OSError, json.JSONDecodeError) as exc:
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
