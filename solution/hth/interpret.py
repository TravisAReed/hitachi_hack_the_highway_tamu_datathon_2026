"""Turn one free-text message into a dry-run plan.

The model may only quote the message. Alias matching, dates, and tool arguments
belong to deterministic code. A compound message that is partly unresolved
becomes one escalate and no write tools.
"""

from __future__ import annotations

from .dates import DateError, parse_date_mention
from .llm import SchemaError, StubModel, validate_extraction
from .ontology import Ontology, normalize_name
from .policy import ASSUMPTIONS, plan_event

GATE = (
    "A compound message is gated as a whole. Any unconfirmed incident, false confirmation, "
    "quote that is not in the text, or entity or date that does not resolve uniquely becomes "
    "one escalate and no write tools. Noise mixed with resolved incidents is ignored. "
    "A later planner block, such as no legal slot, stays a block and is not rewritten as escalate."
)

WRITE_TOOLS = frozenset(
    {
        "mes.reschedule",
        "mes.transfer",
        "dealer.notify",
        "dealer.stop_sale",
        "crm.assign_loaner",
        "crm.notify_customer",
        "quality.hold_lot",
    }
)


class InterpretError(ValueError):
    """The model output cannot be validated. No plan is proposed."""


def interpret_and_plan(ontology: Ontology, event_id: str, event_text: str, client) -> dict:
    if not isinstance(event_text, str) or not event_text.strip():
        raise InterpretError("event text is empty")
    if not isinstance(event_id, str) or not event_id.strip():
        raise InterpretError("event_id is empty")
    mode = getattr(client, "mode", None)
    if mode not in {"stub", "live"}:
        raise InterpretError("parser client must be a stub or a live adapter")
    try:
        raw = client.complete(event_text)
        incidents = validate_extraction(raw)
    except SchemaError as exc:
        raise InterpretError(str(exc)) from exc
    grounded = _ground(ontology, event_text, incidents)
    provenance = _provenance(client, True, grounded.decision, grounded.reason, grounded.traces)
    if grounded.decision == "noise":
        result = _empty_plan(event_id.strip(), "no operational incident; no write tools proposed")
    elif grounded.decision == "escalate":
        result = _escalate_plan(event_id.strip(), grounded.reason)
    else:
        result = plan_event(ontology, {"event_id": event_id.strip(), "incidents": grounded.structured})
        if result["status"] == "ok":
            provenance = _provenance(client, True, "plan", "resolved incidents passed schema and table checks", grounded.traces)
        else:
            provenance = _provenance(
                client,
                True,
                "blocked",
                "interpretation resolved, and the policy engine withheld writes",
                grounded.traces,
            )
    result["interpretation"] = provenance
    result["assumptions"] = list(ASSUMPTIONS) + [GATE]
    return result


def _provenance(client, schema_valid: bool, decision: str, reason: str, traces: list[dict]) -> dict:
    invoked = bool(getattr(client, "llm_invoked", False)) and getattr(client, "mode", "") == "live"
    return {
        "mode": "live" if invoked else "stub",
        "model": getattr(client, "model_name", None) if invoked else None,
        "llm_invoked": invoked,
        "schema_valid": schema_valid,
        "decision": decision,
        "reason": reason,
        "gate": GATE,
        "incidents": traces,
    }


class _Grounded:
    def __init__(self, decision: str, reason: str, structured: list[dict], traces: list[dict]):
        self.decision = decision
        self.reason = reason
        self.structured = structured
        self.traces = traces


def _ground(ontology: Ontology, event_text: str, incidents: list[dict]) -> _Grounded:
    traces = []
    structured = []
    saw_noise = False
    for index, incident in enumerate(incidents):
        trace, built, problem = _ground_one(ontology, event_text, index, incident)
        traces.append(trace)
        if problem:
            return _Grounded("escalate", problem, [], traces)
        if built is None:
            saw_noise = True
            continue
        structured.append(built)
    if not structured:
        if saw_noise:
            return _Grounded("noise", "every incident was noise", [], traces)
        return _Grounded("escalate", "no confirmed operational incident", [], traces)
    return _Grounded("plan", "resolved incidents", structured, traces)


def _ground_one(ontology: Ontology, event_text: str, index: int, incident: dict):
    trace = {
        "category": incident["category"],
        "confirmed": incident["confirmed"],
        "quotes_in_text": False,
        "resolutions": [],
        "dates": [],
    }
    missing_quote = [quote for quote in incident["evidence_quotes"] if not _in_text(quote, event_text)]
    if missing_quote:
        return trace, None, f"incidents[{index}] evidence quote was not found in the event text"
    trace["quotes_in_text"] = True
    if incident["category"] == "noise":
        return trace, None, None
    if incident["category"] == "unconfirmed" or incident["confirmed"] is False:
        return trace, None, f"incidents[{index}] is unconfirmed"
    try:
        entities = _resolve_entities(ontology, event_text, incident["entities"])
        dates = _resolve_dates(event_text, incident["dates"])
    except (DateError, _ResolveFailure) as exc:
        return trace, None, f"incidents[{index}] {exc}"
    trace["resolutions"] = entities["trace"]
    trace["dates"] = dates["trace"]
    try:
        built = _to_structured(incident["category"], entities["values"], dates["values"])
        conflict = _table_conflict(ontology, built)
        if conflict:
            raise _ResolveFailure(conflict)
    except _ResolveFailure as exc:
        return trace, None, f"incidents[{index}] {exc}"
    return trace, built, None


def _table_conflict(ontology: Ontology, built: dict) -> str | None:
    if built["kind"] == "supplier_delay" and "shipment_id" in built:
        shipment = ontology.shipments.get(built["shipment_id"])
        if shipment is None:
            return "shipment id was not found"
        if built.get("part_id") and built["part_id"] != shipment.part_id:
            return "part contradicts the shipment"
        if built.get("plant_id") and built["plant_id"] != shipment.plant_id:
            return "plant contradicts the shipment"
        if built.get("supplier_id") and built["supplier_id"] != shipment.supplier_id:
            return "supplier contradicts the shipment"
        if built.get("original_eta") and built["original_eta"] != shipment.eta_date.isoformat():
            return "original date contradicts the shipment"
    if built["kind"] == "quality_hold" and "lot_id" in built:
        lot = ontology.lots.get(built["lot_id"])
        if lot is None:
            return "lot id was not found"
        if built.get("part_id") and built["part_id"] != lot.part_id:
            return "part contradicts the lot"
        if built.get("plant_id") and built["plant_id"] != lot.plant_id:
            return "plant contradicts the lot"
        if built.get("supplier_id") and built["supplier_id"] != lot.supplier_id:
            return "supplier contradicts the lot"
        point = built.get("build_date")
        if point and not lot.first_build_date.isoformat() <= point <= lot.last_build_date.isoformat():
            return "build date is outside the lot"
        if built.get("first_build_date") and (
            built["first_build_date"] != lot.first_build_date.isoformat()
            or built.get("last_build_date") != lot.last_build_date.isoformat()
        ):
            return "lot dates contradict the lot record"
    return None


def _resolve_entities(ontology: Ontology, event_text: str, rows: list[dict]) -> dict:
    values: dict[str, str] = {}
    trace = []
    for row in rows:
        mention = row["mention"]
        if not _in_text(mention, event_text):
            raise _ResolveFailure(f"mention {mention!r} was not found in the event text")
        hit = ontology.resolve_mention(mention, row["role"])
        trace.append(
            {
                "role": row["role"],
                "mention": mention,
                "status": hit.status,
                "entity_type": hit.entity_type,
                "entity_id": hit.entity_id,
            }
        )
        if hit.status != "found" or hit.entity_id is None:
            raise _ResolveFailure(f"{row['role']} mention {mention!r} is {hit.status}")
        previous = values.get(row["role"])
        if previous is not None and previous != hit.entity_id:
            raise _ResolveFailure(f"{row['role']} mentions resolve to different ids")
        values[row["role"]] = hit.entity_id
    return {"values": values, "trace": trace}


def _resolve_dates(event_text: str, rows: list[dict]) -> dict:
    values = {}
    trace = []
    for row in rows:
        mention = row["mention"]
        if not _in_text(mention, event_text):
            raise _ResolveFailure(f"date mention {mention!r} was not found in the event text")
        resolved = parse_date_mention(mention)
        trace.append({"role": row["role"], "mention": mention, "date": resolved.isoformat()})
        previous = values.get(row["role"])
        if previous is not None and previous != resolved:
            raise _ResolveFailure(f"{row['role']} mentions resolve to different dates")
        values[row["role"]] = resolved
    return {"values": values, "trace": trace}


def _to_structured(category: str, entities: dict[str, str], dates: dict) -> dict:
    if category == "supplier_delay":
        if "new_eta" not in dates:
            raise _ResolveFailure("supplier delay is missing new_eta")
        if "shipment" not in entities and not {"part", "plant"} <= set(entities):
            raise _ResolveFailure("supplier delay needs a shipment or a part and plant")
        if "shipment" not in entities and "original_eta" not in dates:
            raise _ResolveFailure("supplier delay is missing original_eta")
        body = {"kind": "supplier_delay", "new_eta": dates["new_eta"].isoformat()}
        if "shipment" in entities:
            body["shipment_id"] = entities["shipment"]
        if "part" in entities:
            body["part_id"] = entities["part"]
        if "plant" in entities:
            body["plant_id"] = entities["plant"]
        if "supplier" in entities:
            body["supplier_id"] = entities["supplier"]
        if "original_eta" in dates:
            body["original_eta"] = dates["original_eta"].isoformat()
        return body
    if category == "plant_outage":
        if "plant" not in entities:
            raise _ResolveFailure("plant outage is missing a plant")
        if "outage_start" not in dates or "outage_end" not in dates:
            raise _ResolveFailure("plant outage is missing its date range")
        return {
            "kind": "plant_outage",
            "plant_id": entities["plant"],
            "start_date": dates["outage_start"].isoformat(),
            "end_date": dates["outage_end"].isoformat(),
        }
    if category == "quality_hold":
        body = {"kind": "quality_hold"}
        if "lot" in entities:
            body["lot_id"] = entities["lot"]
        if "part" in entities:
            body["part_id"] = entities["part"]
        if "plant" in entities:
            body["plant_id"] = entities["plant"]
        if "supplier" in entities:
            body["supplier_id"] = entities["supplier"]
        if "build_date" in dates:
            body["build_date"] = dates["build_date"].isoformat()
        if "lot_start" in dates or "lot_end" in dates:
            if "lot_start" not in dates or "lot_end" not in dates:
                raise _ResolveFailure("quality hold needs both lot date endpoints")
            body["first_build_date"] = dates["lot_start"].isoformat()
            body["last_build_date"] = dates["lot_end"].isoformat()
        if "lot_id" not in body and ("part_id" not in body or "plant_id" not in body):
            raise _ResolveFailure("quality hold needs a lot or a part and plant")
        if "lot_id" not in body and "build_date" not in body and "first_build_date" not in body:
            raise _ResolveFailure("quality hold is missing date evidence")
        return body
    raise _ResolveFailure("category cannot be planned")


def _empty_plan(event_id: str, note: str) -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "status": "ok",
        "assumptions": [],
        "blockers": [],
        "notes": [note],
        "incidents": [],
        "affected_orders": [],
        "findings": [],
        "vehicle_findings": [],
        "moves": [],
        "loaner_decisions": [],
        "quality": {"holds": [], "stop_sales": [], "customer_notices": []},
        "graph_paths": [],
        "tool_calls": [],
    }


def _escalate_plan(event_id: str, reason: str) -> dict:
    result = _empty_plan(event_id, "unconfirmed or unresolved; no write tools proposed")
    result["tool_calls"] = [
        {"event_id": event_id, "tool": "escalate", "args": {"reason": _short_reason(reason)}}
    ]
    return result


def _short_reason(reason: str) -> str:
    text = " ".join(reason.split())
    if len(text) > 240:
        return text[:237] + "..."
    return text or "the report is not specific enough to act"


def _in_text(fragment: str, event_text: str) -> bool:
    return normalize_name(fragment) in normalize_name(event_text)


class _ResolveFailure(Exception):
    pass


def stub_client(response: dict) -> StubModel:
    return StubModel(response)
