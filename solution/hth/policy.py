"""Deterministic dry-run planner for supplier delays and plant outages.

The planner proposes tool calls. It does not post them and it does not touch
the simulator. Assumptions that close handbook gaps are listed in
``ASSUMPTIONS`` and copied into every result.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from .calendarutil import is_working_day, next_working_day
from .incidents import EventInput, PlantOutageInput, SupplierDelayInput, parse_event
from .ontology import TIERS, Ontology

SCHEMA_VERSION = 1

DEALER_MESSAGE = "Production for the listed customer orders is delayed or rescheduled."

ASSUMPTIONS = (
    "A production order uses one build slot, matching the simulator, regardless of units.",
    "The delay window is [original ETA, new ETA). covers_days does not extend it. The first legal build day is the new ETA.",
    "An outage window is inclusive. The first legal build day is the next working day after the outage end, which is also the first tool day on or after the calendar day after the outage.",
    "Only orders on a tool working day from today through the horizon are moved. Orders dated before today are already built and create no notices or loaners.",
    "Same-plant days on or after the legal start are filled before any transfer. A transfer may use only those same days, at another plant that builds the model. Earlier days are not used.",
    "Orders are placed by original build date, then production_order_id. On a tie between plants, the lowest plant_id wins.",
    "An order in several incidents is moved once. Its legal start is the latest of those incidents. Slots and loaner pools are shared across the event.",
    "Each dealer with an affected customer order gets one dealer.notify, with those customer-order ids sorted lexicographically and no others.",
    "Loaner candidates are affected customer orders in Platinum or Gold, ordered by Platinum, then earlier promised date, then customer_order_id. Standard orders are not assigned a loaner. One candidate is one customer order.",
    "If a required fact is missing, contradictory, or ambiguous, or any affected order cannot be placed, the event is blocked and no tool calls are proposed.",
)

MOVE_TOOLS = frozenset({"mes.reschedule", "mes.transfer"})


class ResolveError(Exception):
    pass


@dataclass(frozen=True)
class ResolvedDelay:
    index: int
    shipment_id: str
    supplier_id: str
    part_id: str
    plant_id: str
    original_eta: date
    new_eta: date
    legal_start: date
    covers_days: int
    bom_models: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedOutage:
    index: int
    plant_id: str
    start_date: date
    end_date: date
    legal_start: date


Resolved = ResolvedDelay | ResolvedOutage


def plan_event(ontology: Ontology, event: EventInput | dict) -> dict:
    parsed = parse_event(event) if isinstance(event, dict) else event
    if not isinstance(parsed, EventInput):
        raise TypeError("event must be an EventInput or a dict")
    blockers: list[str] = []
    resolved: list[Resolved] = []
    for index, incident in enumerate(parsed.incidents):
        try:
            resolved.append(_resolve(ontology, index, incident))
        except ResolveError as exc:
            blockers.append(f"incidents[{index}]: {exc}")
    if blockers:
        return _result(parsed.event_id, "blocked", blockers, resolved, [], [], [], [], [], ["tool calls withheld because the event is blocked"])
    if not resolved:
        return _result(
            parsed.event_id,
            "ok",
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            ["no incidents supplied"],
        )

    findings, affected_by_incident, after_horizon = _classify(ontology, resolved)
    if after_horizon:
        blockers.append(
            "an order inside a disruption is after the scheduling horizon: " + ", ".join(after_horizon)
        )
        return _result(
            parsed.event_id,
            "blocked",
            blockers,
            resolved,
            [],
            findings,
            [],
            [],
            _graphs(ontology, resolved, affected_by_incident),
            ["tool calls withheld because the event is blocked"],
        )

    constraints = _merge(affected_by_incident, resolved)
    moves, place_blockers = _place(ontology, constraints)
    affected_orders = _affected_payload(ontology, constraints)
    graphs = _graphs(ontology, resolved, affected_by_incident)
    if place_blockers:
        return _result(
            parsed.event_id,
            "blocked",
            place_blockers,
            resolved,
            affected_orders,
            findings,
            [],
            [],
            graphs,
            ["tool calls withheld because the event is blocked"],
        )

    notice_blockers, notices, decisions = _commercial(ontology, list(constraints))
    if notice_blockers:
        return _result(
            parsed.event_id,
            "blocked",
            notice_blockers,
            resolved,
            affected_orders,
            findings,
            [],
            [],
            graphs,
            ["tool calls withheld because the event is blocked"],
        )

    tool_calls = _tool_calls(parsed.event_id, moves, notices, decisions)
    _check_invariants(tool_calls, list(constraints))
    notes: list[str] = []
    if not constraints:
        notes.append("no future production orders fall inside the disruption windows")
    return _result(
        parsed.event_id,
        "ok",
        [],
        resolved,
        affected_orders,
        findings,
        moves,
        decisions,
        graphs,
        notes,
        tool_calls,
    )


def _resolve(ontology: Ontology, index: int, incident: SupplierDelayInput | PlantOutageInput) -> Resolved:
    if isinstance(incident, SupplierDelayInput):
        return _resolve_delay(ontology, index, incident)
    return _resolve_outage(ontology, index, incident)


def _resolve_delay(ontology: Ontology, index: int, incident: SupplierDelayInput) -> ResolvedDelay:
    if not is_working_day(incident.new_eta):
        raise ResolveError(f"new_eta {incident.new_eta.isoformat()} is not a working day")
    if incident.shipment_id:
        shipment = ontology.shipments.get(incident.shipment_id)
        if shipment is None:
            raise ResolveError(f"unknown shipment_id {incident.shipment_id}")
        if incident.part_id and incident.part_id != shipment.part_id:
            raise ResolveError("part_id contradicts shipment")
        if incident.plant_id and incident.plant_id != shipment.plant_id:
            raise ResolveError("plant_id contradicts shipment")
        if incident.supplier_id and incident.supplier_id != shipment.supplier_id:
            raise ResolveError("supplier_id contradicts shipment")
        if incident.original_eta and incident.original_eta != shipment.eta_date:
            raise ResolveError("original_eta contradicts shipment")
    else:
        if not (incident.part_id and incident.plant_id and incident.original_eta):
            raise ResolveError("need shipment_id or part_id, plant_id, and original_eta")
        if incident.part_id not in ontology.parts:
            raise ResolveError(f"unknown part_id {incident.part_id}")
        if incident.plant_id not in ontology.plants:
            raise ResolveError(f"unknown plant_id {incident.plant_id}")
        if incident.supplier_id and incident.supplier_id not in ontology.suppliers:
            raise ResolveError(f"unknown supplier_id {incident.supplier_id}")
        if not is_working_day(incident.original_eta):
            raise ResolveError(f"original_eta {incident.original_eta.isoformat()} is not a working day")
        matched = list(ontology.shipments_by_key.get((incident.part_id, incident.plant_id, incident.original_eta), ()))
        if incident.supplier_id:
            matched = [sid for sid in matched if ontology.shipments[sid].supplier_id == incident.supplier_id]
        if not matched:
            raise ResolveError("no shipment matches part_id, plant_id, and original_eta")
        if len(matched) > 1:
            raise ResolveError("ambiguous shipment match")
        shipment = ontology.shipments[matched[0]]
    if incident.new_eta <= shipment.eta_date:
        raise ResolveError("new_eta must be after original_eta")
    return ResolvedDelay(
        index,
        shipment.shipment_id,
        shipment.supplier_id,
        shipment.part_id,
        shipment.plant_id,
        shipment.eta_date,
        incident.new_eta,
        incident.new_eta,
        shipment.covers_days,
        ontology.models_using(shipment.part_id),
    )


def _resolve_outage(ontology: Ontology, index: int, incident: PlantOutageInput) -> ResolvedOutage:
    if incident.plant_id not in ontology.plants:
        raise ResolveError(f"unknown plant_id {incident.plant_id}")
    if incident.end_date < incident.start_date:
        raise ResolveError("end_date is before start_date")
    return ResolvedOutage(
        index,
        incident.plant_id,
        incident.start_date,
        incident.end_date,
        next_working_day(incident.end_date),
    )


def _classify(
    ontology: Ontology, incidents: list[Resolved]
) -> tuple[list[dict], dict[int, list[str]], list[str]]:
    findings: list[dict] = []
    affected: dict[int, list[str]] = {incident.index: [] for incident in incidents}
    after_horizon: list[str] = []
    orders = sorted(ontology.orders.values(), key=lambda order: order.production_order_id)
    for incident in incidents:
        for order in orders:
            reason = _reason(ontology, incident, order)
            findings.append(
                {
                    "incident_index": incident.index,
                    "production_order_id": order.production_order_id,
                    "reason": reason,
                }
            )
            if reason == "affected":
                affected[incident.index].append(order.production_order_id)
            elif reason == "after_horizon":
                after_horizon.append(order.production_order_id)
    return findings, affected, sorted(set(after_horizon))


def _reason(ontology: Ontology, incident: Resolved, order) -> str:
    if isinstance(incident, ResolvedDelay):
        if order.plant_id != incident.plant_id:
            return "wrong_plant"
        if order.model not in ontology.part_models.get(incident.part_id, frozenset()):
            return "bom_miss"
        if order.build_date < incident.original_eta:
            return "before_window"
        if order.build_date >= incident.new_eta:
            return "on_or_after_new_eta"
    else:
        if order.plant_id != incident.plant_id:
            return "wrong_plant"
        if order.build_date < incident.start_date or order.build_date > incident.end_date:
            return "outside_outage"
    if order.build_date < ontology.today:
        return "already_built"
    if order.build_date > ontology.horizon_end:
        return "after_horizon"
    return "affected"


def _merge(affected_by_incident: dict[int, list[str]], incidents: list[Resolved]) -> dict[str, dict]:
    by_index = {incident.index: incident for incident in incidents}
    merged: dict[str, dict] = {}
    for index, order_ids in affected_by_incident.items():
        for order_id in order_ids:
            slot = merged.setdefault(order_id, {"incident_indexes": [], "legal_starts": []})
            slot["incident_indexes"].append(index)
            slot["legal_starts"].append(by_index[index].legal_start)
    for slot in merged.values():
        slot["legal_start"] = max(slot["legal_starts"])
    return merged


def _place(ontology: Ontology, constraints: dict[str, dict]) -> tuple[list[dict], list[str]]:
    occupancy: dict[tuple[str, date], set[str]] = defaultdict(set)
    live = set(ontology.destination_days)
    for order in ontology.orders.values():
        if order.build_date in live:
            occupancy[(order.plant_id, order.build_date)].add(order.production_order_id)

    def free(plant_id: str, day: date) -> int:
        return ontology.plants[plant_id].slots_per_day - len(occupancy[(plant_id, day)])

    moves: list[dict] = []
    order_ids = sorted(constraints, key=lambda oid: (ontology.orders[oid].build_date, oid))
    for order_id in order_ids:
        order = ontology.orders[order_id]
        legal_start = constraints[order_id]["legal_start"]
        legal_days = [day for day in ontology.destination_days if day >= legal_start]
        placed = None
        checked: list[str] = []
        for day in legal_days:
            checked.append(day.isoformat())
            slots = free(order.plant_id, day)
            if slots > 0:
                placed = _move(order, order.plant_id, day, "reschedule", "mes.reschedule", legal_start, slots, checked)
                break
        if placed is None:
            alternates = [plant for plant in ontology.plants_building(order.model) if plant != order.plant_id]
            for day in legal_days:
                for plant_id in alternates:
                    slots = free(plant_id, day)
                    if slots > 0:
                        placed = _move(
                            order, plant_id, day, "transfer", "mes.transfer", legal_start, slots, checked
                        )
                        placed["transfer_plants"] = alternates
                        break
                if placed is not None:
                    break
            if placed is None:
                return moves, [
                    f"no legal slot for {order_id} on or after {legal_start.isoformat()} "
                    f"at {order.plant_id} or transfer plants {alternates}"
                ]
        occupancy[(order.plant_id, order.build_date)].discard(order_id)
        occupancy[(placed["to"]["plant_id"], date.fromisoformat(placed["to"]["date"]))].add(order_id)
        moves.append(placed)
    return moves, []


def _move(order, plant_id: str, day: date, decision: str, tool: str, legal_start: date, slots: int, checked: list[str]) -> dict:
    return {
        "production_order_id": order.production_order_id,
        "from": {"plant_id": order.plant_id, "date": order.build_date.isoformat()},
        "to": {"plant_id": plant_id, "date": day.isoformat()},
        "decision": decision,
        "tool": tool,
        "legal_start": legal_start.isoformat(),
        "free_slots_before": slots,
        "checked_same_plant_days": list(checked),
    }


def _commercial(ontology: Ontology, affected_ids: list[str]) -> tuple[list[str], list[dict], list[dict]]:
    by_dealer: dict[str, list[str]] = defaultdict(list)
    candidates = []
    standards = []
    blockers = []
    for order_id in affected_ids:
        for customer_order in ontology.customer_orders_for(order_id):
            by_dealer[customer_order.dealer_id].append(customer_order.customer_order_id)
            customer = ontology.customers[customer_order.customer_id]
            if customer.tier not in TIERS:
                blockers.append(f"customer {customer.customer_id} has unknown tier {customer.tier}")
            elif customer.tier == "Standard":
                standards.append(customer_order)
            else:
                candidates.append(customer_order)
    if blockers:
        return blockers, [], []
    missing = sorted({co.dealer_id for co in candidates} - set(ontology.loaners))
    if missing:
        return [f"no loaner pool for {', '.join(missing)}"], [], []

    notices = []
    for dealer_id in sorted(by_dealer):
        notices.append({"dealer_id": dealer_id, "customer_order_ids": sorted(set(by_dealer[dealer_id]))})

    candidates.sort(
        key=lambda co: (
            0 if ontology.customers[co.customer_id].tier == "Platinum" else 1,
            co.promised_date,
            co.customer_order_id,
        )
    )
    pools = dict(ontology.loaners)
    decisions = []
    for customer_order in candidates:
        dealer_id = customer_order.dealer_id
        remaining = pools[dealer_id]
        tier = ontology.customers[customer_order.customer_id].tier
        if remaining <= 0:
            decisions.append(_decision(customer_order, tier, "skip_pool", remaining))
            continue
        decisions.append(_decision(customer_order, tier, "assign", remaining))
        pools[dealer_id] = remaining - 1
    for customer_order in sorted(standards, key=lambda co: co.customer_order_id):
        decisions.append(_decision(customer_order, "Standard", "skip_tier", None))
    return [], notices, decisions


def _decision(customer_order, tier: str, decision: str, remaining: int | None) -> dict:
    return {
        "customer_order_id": customer_order.customer_order_id,
        "customer_id": customer_order.customer_id,
        "dealer_id": customer_order.dealer_id,
        "tier": tier,
        "promised_date": customer_order.promised_date.isoformat(),
        "decision": decision,
        "pool_remaining_before": remaining,
    }


def _tool_calls(event_id: str, moves: list[dict], notices: list[dict], decisions: list[dict]) -> list[dict]:
    calls = []
    for move in moves:
        if move["tool"] == "mes.reschedule":
            args = {
                "production_order_id": move["production_order_id"],
                "new_date": move["to"]["date"],
            }
        else:
            args = {
                "production_order_id": move["production_order_id"],
                "to_plant_id": move["to"]["plant_id"],
                "new_date": move["to"]["date"],
            }
        calls.append({"event_id": event_id, "tool": move["tool"], "args": args})
    for notice in notices:
        calls.append(
            {
                "event_id": event_id,
                "tool": "dealer.notify",
                "args": {
                    "dealer_id": notice["dealer_id"],
                    "customer_order_ids": notice["customer_order_ids"],
                    "message": DEALER_MESSAGE,
                },
            }
        )
    for decision in decisions:
        if decision["decision"] != "assign":
            continue
        calls.append(
            {
                "event_id": event_id,
                "tool": "crm.assign_loaner",
                "args": {"customer_order_id": decision["customer_order_id"]},
            }
        )
    return calls


def _check_invariants(tool_calls: list[dict], affected_ids: list[str]) -> None:
    moved = [call["args"]["production_order_id"] for call in tool_calls if call["tool"] in MOVE_TOOLS]
    if len(moved) != len(set(moved)) or set(moved) != set(affected_ids):
        raise RuntimeError("move proposals do not match the affected production orders")
    dealers = [call["args"]["dealer_id"] for call in tool_calls if call["tool"] == "dealer.notify"]
    if len(dealers) != len(set(dealers)):
        raise RuntimeError("duplicate dealer notice")
    loaners = [call["args"]["customer_order_id"] for call in tool_calls if call["tool"] == "crm.assign_loaner"]
    if len(loaners) != len(set(loaners)):
        raise RuntimeError("duplicate loaner assignment")


def _affected_payload(ontology: Ontology, constraints: dict[str, dict]) -> list[dict]:
    rows = []
    for order_id in sorted(constraints, key=lambda oid: (ontology.orders[oid].build_date, oid)):
        order = ontology.orders[order_id]
        slot = constraints[order_id]
        rows.append(
            {
                "production_order_id": order_id,
                "plant_id": order.plant_id,
                "model": order.model,
                "build_date": order.build_date.isoformat(),
                "units": order.units,
                "incident_indexes": list(slot["incident_indexes"]),
                "legal_start": slot["legal_start"].isoformat(),
                "customer_order_ids": list(ontology.customer_orders_by_po.get(order_id, ())),
            }
        )
    return rows


def _graphs(ontology: Ontology, incidents: list[Resolved], affected_by_incident: dict[int, list[str]]) -> list[dict]:
    graphs = []
    for incident in incidents:
        order_ids = sorted(
            affected_by_incident.get(incident.index, []),
            key=lambda oid: (ontology.orders[oid].build_date, oid),
        )
        chains: list[list[str]] = []
        if isinstance(incident, ResolvedDelay):
            kind = "supplier_delay"
        else:
            kind = "plant_outage"
        for order_id in order_ids:
            order = ontology.orders[order_id]
            if isinstance(incident, ResolvedDelay):
                prefix = [
                    f"Shipment:{incident.shipment_id}",
                    f"Part:{incident.part_id}",
                    f"Model:{order.model}",
                    f"ProductionOrder:{order_id}",
                ]
            else:
                prefix = [f"Plant:{incident.plant_id}", f"ProductionOrder:{order_id}"]
            customers = ontology.customer_orders_for(order_id)
            if not customers:
                chains.append(prefix)
                continue
            for customer_order in customers:
                chains.append(
                    prefix
                    + [
                        f"CustomerOrder:{customer_order.customer_order_id}",
                        f"Dealer:{customer_order.dealer_id}",
                        f"Customer:{customer_order.customer_id}",
                    ]
                )
        graphs.append(
            {
                "incident_index": incident.index,
                "kind": kind,
                "chains": chains,
                "edges": _edges(chains),
            }
        )
    return graphs


def _edges(chains: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, str]] = set()
    edges: list[list[str]] = []
    for chain in chains:
        for left, right in zip(chain, chain[1:]):
            key = (left, right)
            if key in seen:
                continue
            seen.add(key)
            edges.append([left, right])
    return edges


def _incident_dict(incident: Resolved) -> dict:
    if isinstance(incident, ResolvedDelay):
        return {
            "index": incident.index,
            "kind": "supplier_delay",
            "shipment_id": incident.shipment_id,
            "supplier_id": incident.supplier_id,
            "part_id": incident.part_id,
            "plant_id": incident.plant_id,
            "original_eta": incident.original_eta.isoformat(),
            "new_eta": incident.new_eta.isoformat(),
            "legal_start": incident.legal_start.isoformat(),
            "covers_days": incident.covers_days,
            "bom_models": list(incident.bom_models),
        }
    return {
        "index": incident.index,
        "kind": "plant_outage",
        "plant_id": incident.plant_id,
        "start_date": incident.start_date.isoformat(),
        "end_date": incident.end_date.isoformat(),
        "legal_start": incident.legal_start.isoformat(),
    }


def _result(
    event_id: str,
    status: str,
    blockers: list[str],
    incidents: list[Resolved],
    affected_orders: list[dict],
    findings: list[dict],
    moves: list[dict],
    decisions: list[dict],
    graphs: list[dict],
    notes: list[str],
    tool_calls: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "status": status,
        "assumptions": list(ASSUMPTIONS),
        "blockers": blockers,
        "notes": notes,
        "incidents": [_incident_dict(incident) for incident in incidents],
        "affected_orders": affected_orders,
        "findings": findings,
        "moves": moves,
        "loaner_decisions": decisions,
        "graph_paths": graphs,
        "tool_calls": tool_calls or [],
    }
