"""Structured incident input. Free text and model calls stay out of this layer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DELAY_FIELDS = frozenset(
    {"kind", "shipment_id", "supplier_id", "part_id", "plant_id", "original_eta", "new_eta"}
)
OUTAGE_FIELDS = frozenset({"kind", "plant_id", "start_date", "end_date"})
EVENT_FIELDS = frozenset({"event_id", "incidents"})


class InputError(ValueError):
    """The JSON shape is not a structured event. Semantic gaps are blockers."""


@dataclass(frozen=True)
class SupplierDelayInput:
    shipment_id: str | None
    supplier_id: str | None
    part_id: str | None
    plant_id: str | None
    original_eta: date | None
    new_eta: date


@dataclass(frozen=True)
class PlantOutageInput:
    plant_id: str
    start_date: date
    end_date: date


@dataclass(frozen=True)
class EventInput:
    event_id: str
    incidents: tuple[SupplierDelayInput | PlantOutageInput, ...]


def _optional_id(payload: dict, field: str) -> str | None:
    if field not in payload or payload[field] is None:
        return None
    value = payload[field]
    if not isinstance(value, str):
        raise InputError(f"{field} must be a string")
    value = value.strip()
    return value or None


def _required_str(payload: dict, field: str) -> str:
    value = _optional_id(payload, field)
    if value is None:
        raise InputError(f"{field} must be a non-empty string")
    return value


def _parse_date(payload: dict, field: str, required: bool) -> date | None:
    if field not in payload or payload[field] is None:
        if required:
            raise InputError(f"{field} is required (YYYY-MM-DD)")
        return None
    raw = payload[field]
    if not isinstance(raw, str) or not DATE_RE.match(raw):
        raise InputError(f"{field} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise InputError(f"{field} must be YYYY-MM-DD") from exc


def parse_event(payload: object) -> EventInput:
    if not isinstance(payload, dict):
        raise InputError("event must be a JSON object with event_id and incidents")
    extra = set(payload) - EVENT_FIELDS
    if extra:
        raise InputError(f"unknown event fields: {sorted(extra)}")
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise InputError("event_id must be a non-empty string")
    raw_incidents = payload.get("incidents")
    if not isinstance(raw_incidents, list):
        raise InputError("incidents must be a list")
    incidents: list[SupplierDelayInput | PlantOutageInput] = []
    for index, item in enumerate(raw_incidents):
        incidents.append(_parse_incident(index, item))
    return EventInput(event_id.strip(), tuple(incidents))


def _parse_incident(index: int, item: object) -> SupplierDelayInput | PlantOutageInput:
    if not isinstance(item, dict):
        raise InputError(f"incidents[{index}] must be an object")
    kind = item.get("kind")
    if not isinstance(kind, str):
        raise InputError(f"incidents[{index}].kind must be a string")
    if kind == "supplier_delay":
        extra = set(item) - DELAY_FIELDS
        if extra:
            raise InputError(f"incidents[{index}] has unknown fields {sorted(extra)}")
        new_eta = _parse_date(item, "new_eta", required=True)
        assert new_eta is not None
        return SupplierDelayInput(
            _optional_id(item, "shipment_id"),
            _optional_id(item, "supplier_id"),
            _optional_id(item, "part_id"),
            _optional_id(item, "plant_id"),
            _parse_date(item, "original_eta", required=False),
            new_eta,
        )
    if kind == "plant_outage":
        extra = set(item) - OUTAGE_FIELDS
        if extra:
            raise InputError(f"incidents[{index}] has unknown fields {sorted(extra)}")
        start = _parse_date(item, "start_date", required=True)
        end = _parse_date(item, "end_date", required=True)
        assert start is not None and end is not None
        return PlantOutageInput(_required_str(item, "plant_id"), start, end)
    if kind == "quality_hold":
        raise InputError("quality_hold is not accepted by this dry-run; omit it until that milestone")
    raise InputError(f"incidents[{index}].kind {kind!r} is not supported")
