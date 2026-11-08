"""Coordinator and three specialists. They propose. Only the coordinator emits the sequence.

Supply chain owns the shared slot map. Dealer network groups notices and stop-sales.
Customer care owns the shared loaner pool, lot holds, and owner notices. None of them
call a model. A proposal outside a role's allowlist, or any specialist blocker,
discards the whole event.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .ontology import TIERS, Ontology
from .policy import (
    DEALER_MESSAGE,
    QUALITY_MESSAGE,
    ResolvedDelay,
    ResolvedOutage,
    ResolvedQuality,
    _affected_payload,
    _assemble_quality,
    _blocked,
    _check_invariants,
    _classify,
    _classify_quality,
    _commercial,
    _graphs,
    _merge,
    _place,
    _quality_calls,
    _result,
    _tool_calls,
)

SUPPLY = "supply_chain"
DEALER = "dealer_network"
CARE = "customer_care"
COORDINATOR = "coordinator"

ROLE_TOOLS = {
    SUPPLY: frozenset({"mes.reschedule", "mes.transfer"}),
    DEALER: frozenset({"dealer.notify", "dealer.stop_sale"}),
    CARE: frozenset({"crm.assign_loaner", "quality.hold_lot", "crm.notify_customer"}),
}
TOOL_ARGS = {
    "mes.reschedule": frozenset({"production_order_id", "new_date"}),
    "mes.transfer": frozenset({"production_order_id", "to_plant_id", "new_date"}),
    "dealer.notify": frozenset({"dealer_id", "customer_order_ids", "message"}),
    "dealer.stop_sale": frozenset({"dealer_id", "vins"}),
    "crm.assign_loaner": frozenset({"customer_order_id"}),
    "quality.hold_lot": frozenset({"lot_id"}),
    "crm.notify_customer": frozenset({"customer_id", "message"}),
    "escalate": frozenset({"reason"}),
}


@dataclass
class SharedResources:
    """Slot map and loaner pools for one event. A new event builds a new object."""

    loaners: dict
    occupancy: dict
    event_label: str = "event-local"

    @classmethod
    def for_ontology(cls, ontology: Ontology) -> "SharedResources":
        occupancy = defaultdict(set)
        live = set(ontology.destination_days)
        for order in ontology.orders.values():
            if order.build_date in live:
                occupancy[(order.plant_id, order.build_date)].add(order.production_order_id)
        return cls(dict(ontology.loaners), occupancy)


@dataclass
class RolePlan:
    role: str
    called: bool
    reason: str
    proposals: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)


def coordinate(ontology: Ontology, event_id: str, resolved: list, resolve_blockers: list[str]) -> dict:
    handoffs = []
    if resolve_blockers:
        handoffs.append(_skip_all("incident resolution failed"))
        return _with_trace(
            _blocked(event_id, resolve_blockers, resolved, [], [], [], []),
            _trace("withheld", handoffs, 0),
        )
    if not resolved:
        handoffs.append(_skip_all("no incidents supplied"))
        result = _result(
            event_id, "ok", [], [], [], [], [], [], [], ["no incidents supplied"]
        )
        return _with_trace(result, _trace("idle", handoffs, 0))

    resources = SharedResources.for_ontology(ontology)
    schedule = [item for item in resolved if isinstance(item, (ResolvedDelay, ResolvedOutage))]
    qualities = [item for item in resolved if isinstance(item, ResolvedQuality)]
    supply = propose_supply(ontology, schedule, resources)
    handoffs.append(_handoff(supply))
    care_review = review_quality(ontology, qualities)
    handoffs.append(_handoff(care_review))
    if supply.blockers or care_review.blockers:
        return _with_trace(
            _finish_withheld(ontology, event_id, supply.blockers + care_review.blockers, resolved, supply, care_review),
            _trace("withheld", handoffs, 0),
        )

    dealer = propose_dealer(ontology, supply.counts["affected_order_ids"], care_review.counts["stock"])
    handoffs.append(_handoff(dealer))
    if dealer.blockers:
        return _with_trace(
            _finish_withheld(ontology, event_id, dealer.blockers, resolved, supply, care_review),
            _trace("withheld", handoffs, 0),
        )

    care = propose_care(ontology, supply.counts["affected_order_ids"], care_review, resources)
    handoffs.append(_handoff(care))
    if care.blockers:
        return _with_trace(
            _finish_withheld(ontology, event_id, care.blockers, resolved, supply, care_review),
            _trace("withheld", handoffs, 0),
        )

    invalid = []
    invalid.extend(validate_role_calls(SUPPLY, supply.tools))
    invalid.extend(validate_role_calls(DEALER, dealer.tools))
    invalid.extend(validate_role_calls(CARE, care.tools))
    if invalid:
        handoffs.append({"from": COORDINATOR, "to": COORDINATOR, "accepted": False, "reason": "allowlist rejected a proposal"})
        return _with_trace(
            _finish_withheld(ontology, event_id, invalid, resolved, supply, care_review),
            _trace("withheld", handoffs, 0),
        )

    tool_calls = _ordered_calls(event_id, supply, dealer, care)
    _check_invariants(tool_calls, supply.counts["affected_order_ids"])
    notes = []
    if schedule and not supply.counts["constraints"]:
        notes.append("no future production orders fall inside the disruption windows")
    if qualities and not any(care_review.counts["quality_hits"].values()):
        notes.append("quality lot held; no finished vehicles matched the lot")
    graphs = _graphs(
        ontology,
        resolved,
        supply.counts["affected_by_incident"],
        care_review.counts["quality_hits"],
    )
    result = _result(
        event_id,
        "ok",
        [],
        resolved,
        supply.counts["affected_orders"],
        supply.counts["findings"],
        supply.counts["moves"],
        care.counts["decisions"],
        graphs,
        notes,
        tool_calls,
        care_review.counts["vehicle_findings"],
        {
            "holds": care.counts["holds"],
            "stop_sales": dealer.counts["stop_sales"],
            "customer_notices": care.counts["customer_notices"],
        },
    )
    return _with_trace(result, _trace("accepted", handoffs, 0, resources))


def propose_supply(ontology: Ontology, schedule: list, resources: SharedResources) -> RolePlan:
    findings, affected, after_horizon = _classify(ontology, schedule)
    blockers = []
    if after_horizon:
        blockers.append("an order inside a disruption is after the scheduling horizon: " + ", ".join(after_horizon))
    constraints = {} if blockers else _merge(affected, schedule)
    moves, place_blockers = ([], blockers) if blockers else _place(ontology, constraints, resources.occupancy)
    if place_blockers and not blockers:
        blockers = place_blockers
        moves = []
    affected_orders = _affected_payload(ontology, constraints) if constraints else []
    graphs = _graphs(ontology, schedule, affected, {})
    tools = []
    if not blockers:
        tools = [call for call in _tool_calls("pending", moves, [], [])]
    return RolePlan(
        SUPPLY,
        True,
        "schedule incidents share one slot map" if schedule else "no schedule incidents",
        [{"production_order_id": move["production_order_id"], "tool": move["tool"], "reason": move["decision"]} for move in moves],
        tools,
        blockers,
        ["Shipment or Plant to Part and Model to ProductionOrder", "shared slot map"],
        {
            "affected_order_ids": [] if blockers else list(constraints),
            "affected_by_incident": affected,
            "constraints": constraints,
            "findings": findings,
            "moves": moves,
            "affected_orders": affected_orders,
            "graphs": graphs,
            "move_count": len(moves),
        },
    )


def review_quality(ontology: Ontology, qualities: list) -> RolePlan:
    findings, hits, blockers = _classify_quality(ontology, qualities)
    holds, _stops, notices = _assemble_quality(qualities, hits) if not blockers else ([], [], [])
    stock = []
    if not blockers:
        seen = set()
        for incident in qualities:
            for vehicle in hits.get(incident.index, []):
                if vehicle.vin in seen or vehicle.status != "in_stock":
                    continue
                seen.add(vehicle.vin)
                stock.append(vehicle)
    return RolePlan(
        CARE,
        True,
        "quality graph is read before dealer stop-sales" if qualities else "no quality incidents",
        [{"lot_id": hold["lot_id"], "tool": "quality.hold_lot"} for hold in holds],
        [{"tool": "quality.hold_lot", "args": {"lot_id": hold["lot_id"]}} for hold in holds] if not blockers else [],
        blockers,
        ["Lot to ProductionOrder to Vehicle to Dealer or Customer"],
        {
            "vehicle_findings": findings,
            "quality_hits": hits,
            "holds": holds,
            "stock": stock,
            "sold_notices": notices,
            "hold_count": len(holds),
        },
    )


def propose_dealer(ontology: Ontology, affected_ids: list[str], stock: list) -> RolePlan:
    by_dealer = defaultdict(list)
    for order_id in affected_ids:
        for customer_order in ontology.customer_orders_for(order_id):
            if customer_order.dealer_id not in ontology.dealers:
                return RolePlan(DEALER, True, "dealer lookup failed", blockers=[f"unknown dealer on {customer_order.customer_order_id}"])
            by_dealer[customer_order.dealer_id].append(customer_order.customer_order_id)
    notices = [
        {"dealer_id": dealer_id, "customer_order_ids": sorted(set(ids)), "reason": "affected customer orders"}
        for dealer_id, ids in sorted(by_dealer.items())
    ]
    stop_map = defaultdict(list)
    for vehicle in stock:
        stop_map[vehicle.dealer_id].append(vehicle.vin)
    stop_sales = [
        {"dealer_id": dealer_id, "vins": sorted(set(vins)), "reason": "unsold vehicles on quality hold"}
        for dealer_id, vins in sorted(stop_map.items())
    ]
    tools = [
        {"tool": "dealer.notify", "args": {"dealer_id": notice["dealer_id"], "customer_order_ids": notice["customer_order_ids"], "message": DEALER_MESSAGE}}
        for notice in notices
    ]
    tools.extend(
        {"tool": "dealer.stop_sale", "args": {"dealer_id": stop["dealer_id"], "vins": stop["vins"]}}
        for stop in stop_sales
    )
    return RolePlan(
        DEALER,
        True,
        "one notice per dealer and one stop-sale list per dealer",
        notices + stop_sales,
        tools,
        [],
        ["ProductionOrder to CustomerOrder to Dealer", "Vehicle to Dealer"],
        {"notices": notices, "stop_sales": stop_sales, "notice_count": len(notices), "stop_count": len(stop_sales)},
    )


def propose_care(ontology: Ontology, affected_ids: list[str], review: RolePlan, resources: SharedResources) -> RolePlan:
    _blockers, _notices, decisions = _commercial(ontology, affected_ids)
    if _blockers:
        return RolePlan(CARE, True, "loaner ranking failed", blockers=_blockers, evidence=["CustomerOrder to Customer tier"])
    # Recompute assignments against the shared pool so this event cannot reset another event's pools.
    decisions = _assign_from_pool(ontology, affected_ids, resources.loaners)
    holds = review.counts["holds"]
    customer_notices = review.counts["sold_notices"]
    tools = [
        {"tool": "crm.assign_loaner", "args": {"customer_order_id": row["customer_order_id"]}}
        for row in decisions
        if row["decision"] == "assign"
    ]
    tools.extend({"tool": "quality.hold_lot", "args": {"lot_id": hold["lot_id"]}} for hold in holds)
    tools.extend(
        {"tool": "crm.notify_customer", "args": {"customer_id": notice["customer_id"], "message": QUALITY_MESSAGE}}
        for notice in customer_notices
    )
    return RolePlan(
        CARE,
        True,
        "loaners use the event pool; quality holds do not",
        [row for row in decisions if row["decision"] == "assign"] + holds + customer_notices,
        tools,
        [],
        ["CustomerOrder to loaner pool", "sold Vehicle to Customer"],
        {
            "decisions": decisions,
            "holds": holds,
            "customer_notices": customer_notices,
            "loaner_count": sum(row["decision"] == "assign" for row in decisions),
            "hold_count": len(holds),
            "owner_count": len(customer_notices),
        },
    )


def _assign_from_pool(ontology: Ontology, affected_ids: list[str], pools: dict) -> list[dict]:
    candidates = []
    standards = []
    for order_id in affected_ids:
        for customer_order in ontology.customer_orders_for(order_id):
            tier = ontology.customers[customer_order.customer_id].tier
            if tier == "Standard":
                standards.append(customer_order)
            elif tier in TIERS:
                candidates.append(customer_order)
    candidates.sort(
        key=lambda co: (
            0 if ontology.customers[co.customer_id].tier == "Platinum" else 1,
            co.promised_date,
            co.customer_order_id,
        )
    )
    decisions = []
    for customer_order in candidates:
        remaining = pools[customer_order.dealer_id]
        tier = ontology.customers[customer_order.customer_id].tier
        if remaining <= 0:
            decisions.append(_decision_row(customer_order, tier, "skip_pool", remaining))
            continue
        decisions.append(_decision_row(customer_order, tier, "assign", remaining))
        pools[customer_order.dealer_id] = remaining - 1
    for customer_order in sorted(standards, key=lambda co: co.customer_order_id):
        decisions.append(_decision_row(customer_order, "Standard", "skip_tier", None))
    return decisions


def _decision_row(customer_order, tier: str, decision: str, remaining) -> dict:
    return {
        "customer_order_id": customer_order.customer_order_id,
        "customer_id": customer_order.customer_id,
        "dealer_id": customer_order.dealer_id,
        "tier": tier,
        "promised_date": customer_order.promised_date.isoformat(),
        "decision": decision,
        "pool_remaining_before": remaining,
    }


def validate_role_calls(role: str, calls: list[dict]) -> list[str]:
    allowed = ROLE_TOOLS[role]
    errors = []
    for call in calls:
        tool = call.get("tool")
        if tool not in allowed:
            errors.append(f"{role} proposed {tool}, which is outside its allowlist")
            continue
        args = call.get("args", {})
        if set(args) != TOOL_ARGS[tool]:
            errors.append(f"{role} proposed {tool} with unexpected arguments")
    return errors


def gate_trace(kind: str, reason: str) -> dict:
    called = kind == "plan"
    return _trace(
        kind if kind != "plan" else "accepted",
        [
            {
                "from": COORDINATOR,
                "to": role,
                "called": called,
                "accepted": called,
                "reason": reason if not called else "operational incidents",
            }
            for role in (SUPPLY, DEALER, CARE)
        ],
        0,
    )


def _ordered_calls(event_id: str, supply: RolePlan, dealer: RolePlan, care: RolePlan) -> list[dict]:
    calls = _tool_calls(event_id, supply.counts["moves"], dealer.counts["notices"], care.counts["decisions"])
    calls.extend(
        _quality_calls(event_id, care.counts["holds"], dealer.counts["stop_sales"], care.counts["customer_notices"])
    )
    return calls


def _handoff(plan: RolePlan) -> dict:
    return {
        "from": COORDINATOR,
        "to": plan.role,
        "called": plan.called,
        "accepted": not plan.blockers,
        "reason": plan.reason,
        "proposal_count": len(plan.proposals),
        "tools": sorted({call["tool"] for call in plan.tools}),
        "evidence": plan.evidence,
    }


def _skip_all(reason: str) -> dict:
    return {"from": COORDINATOR, "to": "specialists", "called": False, "accepted": False, "reason": reason}


def _trace(gate: str, handoffs: list, specialist_llm_calls: int, resources: SharedResources | None = None) -> dict:
    return {
        "coordinator": COORDINATOR,
        "specialists": [SUPPLY, DEALER, CARE],
        "specialist_llm_calls": specialist_llm_calls,
        "gate": gate,
        "handoffs": handoffs,
        "resources": {
            "scope": resources.event_label if resources else "none",
            "shared_slot_map": resources is not None,
            "shared_loaner_pool": resources is not None,
        },
        "llm_boundary": "LLM interpretation is outside these roles. Specialists are deterministic.",
    }


def _with_trace(result: dict, trace: dict) -> dict:
    result["orchestration"] = trace
    return result


def _finish_withheld(ontology: Ontology, event_id, blockers, resolved, supply: RolePlan, review: RolePlan) -> dict:
    return _blocked(
        event_id,
        blockers,
        resolved,
        supply.counts.get("affected_orders", []),
        supply.counts.get("findings", []),
        _graphs(ontology, resolved, supply.counts.get("affected_by_incident", {}), review.counts.get("quality_hits", {})),
        review.counts.get("vehicle_findings", []),
    )
