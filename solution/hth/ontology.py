"""Load company tables into a linked, read-only index.

The index is the ontology layer later milestones traverse. This milestone's
policy engine uses it for shipments, bills of materials, plants, orders,
customers, dealers, and loaner pools. Lots, vehicles, and aliases are loaded
so later milestones do not need a second reader. Nothing here writes the
source files or calls enterprise tools.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .calendarutil import TODAY, destination_days_through, is_working_day

REQUIRED_FILES = (
    "plants.csv",
    "plant_models.csv",
    "models.csv",
    "parts.csv",
    "bom.csv",
    "suppliers.csv",
    "production_orders.csv",
    "customer_orders.csv",
    "customers.csv",
    "dealers.csv",
    "loaners.csv",
    "inbound_shipments.csv",
    "part_lots.csv",
    "finished_vehicles.csv",
    "aliases.csv",
    "calendar.csv",
)

TIERS = frozenset({"Platinum", "Gold", "Standard"})


class OntologyError(ValueError):
    """The tables are missing a column, a foreign key, or a legal date."""


@dataclass(frozen=True)
class NameHit:
    status: str
    entity_type: str | None = None
    entity_id: str | None = None
    matches: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Plant:
    plant_id: str
    name: str
    city: str
    slots_per_day: int


@dataclass(frozen=True)
class ProductionOrder:
    production_order_id: str
    plant_id: str
    build_date: date
    model: str
    units: int


@dataclass(frozen=True)
class CustomerOrder:
    customer_order_id: str
    production_order_id: str
    dealer_id: str
    customer_id: str
    model: str
    promised_date: date


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    tier: str


@dataclass(frozen=True)
class Dealer:
    dealer_id: str
    name: str
    city: str
    region: str
    transit_days: str


@dataclass(frozen=True)
class Part:
    part_id: str
    name: str
    category: str


@dataclass(frozen=True)
class Supplier:
    supplier_id: str
    name: str
    city: str


@dataclass(frozen=True)
class Shipment:
    shipment_id: str
    supplier_id: str
    part_id: str
    plant_id: str
    eta_date: date
    covers_days: int


@dataclass(frozen=True)
class Lot:
    lot_id: str
    part_id: str
    supplier_id: str
    plant_id: str
    first_build_date: date
    last_build_date: date


@dataclass(frozen=True)
class Vehicle:
    vin: str
    production_order_id: str
    model: str
    plant_id: str
    build_date: date
    dealer_id: str
    status: str
    customer_id: str


def normalize_name(text: str) -> str:
    return " ".join(text.casefold().split())


def _require_columns(filename: str, rows: list[dict[str, str]], columns: tuple[str, ...]) -> None:
    if not rows:
        return
    missing = [c for c in columns if c not in rows[0]]
    if missing:
        raise OntologyError(f"{filename} is missing columns {missing}")


def _cell(filename: str, row: dict[str, str], column: str) -> str:
    value = row.get(column)
    if value is None or value == "":
        raise OntologyError(f"{filename} has an empty {column}")
    return value


def _parse_date(filename: str, raw: str, column: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise OntologyError(f"{filename} {column} is not YYYY-MM-DD: {raw}") from exc


def _parse_int(filename: str, raw: str, column: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise OntologyError(f"{filename} {column} is not an integer: {raw}") from exc


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class Ontology:
    """In-memory company graph. Construction validates foreign keys."""

    def __init__(self, tables: dict[str, list[dict[str, str]]], horizon_end: date | None = None):
        self.plants: dict[str, Plant] = {}
        self.orders: dict[str, ProductionOrder] = {}
        self.customer_orders: dict[str, CustomerOrder] = {}
        self.customers: dict[str, Customer] = {}
        self.dealers: dict[str, Dealer] = {}
        self.parts: dict[str, Part] = {}
        self.suppliers: dict[str, Supplier] = {}
        self.models: dict[str, str] = {}
        self.shipments: dict[str, Shipment] = {}
        self.lots: dict[str, Lot] = {}
        self.vehicles: dict[str, Vehicle] = {}
        self.loaners: dict[str, int] = {}
        self.bom: dict[str, frozenset[str]] = {}
        self.part_models: dict[str, frozenset[str]] = {}
        self.plant_models: dict[str, frozenset[str]] = {}
        self.model_plants: dict[str, tuple[str, ...]] = {}
        self.customer_orders_by_po: dict[str, tuple[str, ...]] = {}
        self.shipments_by_key: dict[tuple[str, str, date], list[str]] = {}
        self.name_index: dict[str, set[tuple[str, str]]] = {}
        self.destination_days: tuple[date, ...] = ()
        self.horizon_end: date = TODAY
        self.today = TODAY
        self._load(tables, horizon_end)

    def _load(self, tables: dict[str, list[dict[str, str]]], horizon_end: date | None) -> None:
        end = horizon_end if horizon_end is not None else _default_horizon()
        try:
            self.destination_days = destination_days_through(end)
        except ValueError as exc:
            raise OntologyError(str(exc)) from exc
        self.horizon_end = self.destination_days[-1]

        plants = tables.get("plants.csv", [])
        plant_models = tables.get("plant_models.csv", [])
        models = tables.get("models.csv", [])
        parts = tables.get("parts.csv", [])
        bom = tables.get("bom.csv", [])
        suppliers = tables.get("suppliers.csv", [])
        orders = tables.get("production_orders.csv", [])
        customer_orders = tables.get("customer_orders.csv", [])
        customers = tables.get("customers.csv", [])
        dealers = tables.get("dealers.csv", [])
        loaners = tables.get("loaners.csv", [])
        shipments = tables.get("inbound_shipments.csv", [])
        lots = tables.get("part_lots.csv", [])
        vehicles = tables.get("finished_vehicles.csv", [])
        aliases = tables.get("aliases.csv", [])
        calendar = tables.get("calendar.csv", [])

        _require_columns("plants.csv", plants, ("plant_id", "name", "city", "slots_per_day"))
        _require_columns("plant_models.csv", plant_models, ("plant_id", "model"))
        _require_columns("models.csv", models, ("model", "segment"))
        _require_columns("parts.csv", parts, ("part_id", "name", "category"))
        _require_columns("bom.csv", bom, ("model", "part_id"))
        _require_columns("suppliers.csv", suppliers, ("supplier_id", "name", "city"))
        _require_columns("production_orders.csv", orders, ("production_order_id", "plant_id", "build_date", "model", "units"))
        _require_columns(
            "customer_orders.csv",
            customer_orders,
            ("customer_order_id", "production_order_id", "dealer_id", "customer_id", "model", "promised_date"),
        )
        _require_columns("customers.csv", customers, ("customer_id", "name", "tier"))
        _require_columns("dealers.csv", dealers, ("dealer_id", "name", "city", "region", "transit_days"))
        _require_columns("loaners.csv", loaners, ("dealer_id", "loaners_available"))
        _require_columns(
            "inbound_shipments.csv",
            shipments,
            ("shipment_id", "supplier_id", "part_id", "plant_id", "eta_date", "covers_days"),
        )
        _require_columns(
            "part_lots.csv",
            lots,
            ("lot_id", "part_id", "supplier_id", "plant_id", "first_build_date", "last_build_date"),
        )
        _require_columns(
            "finished_vehicles.csv",
            vehicles,
            ("vin", "production_order_id", "model", "plant_id", "build_date", "dealer_id", "status", "customer_id"),
        )
        _require_columns("aliases.csv", aliases, ("alias", "entity_type", "entity_id"))
        _require_columns("calendar.csv", calendar, ("day_index", "date", "weekday"))

        for row in plants:
            pid = _cell("plants.csv", row, "plant_id")
            if pid in self.plants:
                raise OntologyError(f"duplicate plant_id {pid}")
            slots = _parse_int("plants.csv", _cell("plants.csv", row, "slots_per_day"), "slots_per_day")
            if slots < 1:
                raise OntologyError(f"plant {pid} slots_per_day must be at least 1")
            self.plants[pid] = Plant(pid, row.get("name", ""), row.get("city", ""), slots)
            self._index_name(pid, "plant", pid)
            if row.get("name"):
                self._index_name(row["name"], "plant", pid)
            if row.get("city"):
                self._index_name(row["city"], "plant", pid)

        for row in models:
            model = _cell("models.csv", row, "model")
            self.models[model] = row.get("segment", "")
            self._index_name(model, "model", model)

        plant_model_sets: dict[str, set[str]] = {pid: set() for pid in self.plants}
        model_plant_sets: dict[str, set[str]] = {}
        for row in plant_models:
            pid = _cell("plant_models.csv", row, "plant_id")
            model = _cell("plant_models.csv", row, "model")
            if pid not in self.plants:
                raise OntologyError(f"plant_models.csv references unknown plant {pid}")
            plant_model_sets[pid].add(model)
            model_plant_sets.setdefault(model, set()).add(pid)
        self.plant_models = {pid: frozenset(models_) for pid, models_ in plant_model_sets.items()}
        self.model_plants = {model: tuple(sorted(pids)) for model, pids in model_plant_sets.items()}

        for row in parts:
            part_id = _cell("parts.csv", row, "part_id")
            if part_id in self.parts:
                raise OntologyError(f"duplicate part_id {part_id}")
            self.parts[part_id] = Part(part_id, row.get("name", ""), row.get("category", ""))
            self._index_name(part_id, "part", part_id)
            if row.get("name"):
                self._index_name(row["name"], "part", part_id)

        for row in suppliers:
            sid = _cell("suppliers.csv", row, "supplier_id")
            if sid in self.suppliers:
                raise OntologyError(f"duplicate supplier_id {sid}")
            self.suppliers[sid] = Supplier(sid, row.get("name", ""), row.get("city", ""))
            self._index_name(sid, "supplier", sid)
            if row.get("name"):
                self._index_name(row["name"], "supplier", sid)

        bom_sets: dict[str, set[str]] = {}
        part_model_sets: dict[str, set[str]] = {}
        for row in bom:
            model = _cell("bom.csv", row, "model")
            part_id = _cell("bom.csv", row, "part_id")
            if part_id not in self.parts:
                raise OntologyError(f"bom.csv references unknown part {part_id}")
            bom_sets.setdefault(model, set()).add(part_id)
            part_model_sets.setdefault(part_id, set()).add(model)
        self.bom = {model: frozenset(parts_) for model, parts_ in bom_sets.items()}
        self.part_models = {part: frozenset(models_) for part, models_ in part_model_sets.items()}

        for row in dealers:
            did = _cell("dealers.csv", row, "dealer_id")
            if did in self.dealers:
                raise OntologyError(f"duplicate dealer_id {did}")
            self.dealers[did] = Dealer(
                did,
                row.get("name", ""),
                row.get("city", ""),
                row.get("region", ""),
                row.get("transit_days", ""),
            )

        for row in customers:
            cid = _cell("customers.csv", row, "customer_id")
            if cid in self.customers:
                raise OntologyError(f"duplicate customer_id {cid}")
            tier = _cell("customers.csv", row, "tier")
            if tier not in TIERS:
                raise OntologyError(f"customer {cid} has unknown tier {tier}")
            self.customers[cid] = Customer(cid, row.get("name", ""), tier)

        for row in orders:
            oid = _cell("production_orders.csv", row, "production_order_id")
            if oid in self.orders:
                raise OntologyError(f"duplicate production_order_id {oid}")
            pid = _cell("production_orders.csv", row, "plant_id")
            if pid not in self.plants:
                raise OntologyError(f"production order {oid} references unknown plant {pid}")
            build = _parse_date("production_orders.csv", _cell("production_orders.csv", row, "build_date"), "build_date")
            if not is_working_day(build):
                raise OntologyError(f"production order {oid} build_date {build.isoformat()} is not a working day")
            units = _parse_int("production_orders.csv", _cell("production_orders.csv", row, "units"), "units")
            if units < 1:
                raise OntologyError(f"production order {oid} units must be at least 1")
            model = _cell("production_orders.csv", row, "model")
            self.orders[oid] = ProductionOrder(oid, pid, build, model, units)

        by_po: dict[str, list[str]] = {}
        for row in customer_orders:
            coid = _cell("customer_orders.csv", row, "customer_order_id")
            if coid in self.customer_orders:
                raise OntologyError(f"duplicate customer_order_id {coid}")
            poid = _cell("customer_orders.csv", row, "production_order_id")
            if poid not in self.orders:
                raise OntologyError(f"customer order {coid} references unknown production order {poid}")
            did = _cell("customer_orders.csv", row, "dealer_id")
            if did not in self.dealers:
                raise OntologyError(f"customer order {coid} references unknown dealer {did}")
            cid = _cell("customer_orders.csv", row, "customer_id")
            if cid not in self.customers:
                raise OntologyError(f"customer order {coid} references unknown customer {cid}")
            promised = _parse_date(
                "customer_orders.csv", _cell("customer_orders.csv", row, "promised_date"), "promised_date"
            )
            self.customer_orders[coid] = CustomerOrder(
                coid,
                poid,
                did,
                cid,
                _cell("customer_orders.csv", row, "model"),
                promised,
            )
            by_po.setdefault(poid, []).append(coid)
        self.customer_orders_by_po = {poid: tuple(sorted(ids)) for poid, ids in by_po.items()}

        for row in loaners:
            did = _cell("loaners.csv", row, "dealer_id")
            if did not in self.dealers:
                raise OntologyError(f"loaners.csv references unknown dealer {did}")
            if did in self.loaners:
                raise OntologyError(f"duplicate loaner pool for {did}")
            count = _parse_int("loaners.csv", _cell("loaners.csv", row, "loaners_available"), "loaners_available")
            if count < 0:
                raise OntologyError(f"dealer {did} loaner pool is negative")
            self.loaners[did] = count

        for row in shipments:
            sid = _cell("inbound_shipments.csv", row, "shipment_id")
            if sid in self.shipments:
                raise OntologyError(f"duplicate shipment_id {sid}")
            supplier = _cell("inbound_shipments.csv", row, "supplier_id")
            part_id = _cell("inbound_shipments.csv", row, "part_id")
            pid = _cell("inbound_shipments.csv", row, "plant_id")
            if supplier not in self.suppliers:
                raise OntologyError(f"shipment {sid} references unknown supplier {supplier}")
            if part_id not in self.parts:
                raise OntologyError(f"shipment {sid} references unknown part {part_id}")
            if pid not in self.plants:
                raise OntologyError(f"shipment {sid} references unknown plant {pid}")
            eta = _parse_date("inbound_shipments.csv", _cell("inbound_shipments.csv", row, "eta_date"), "eta_date")
            if not is_working_day(eta):
                raise OntologyError(f"shipment {sid} eta_date {eta.isoformat()} is not a working day")
            covers = _parse_int("inbound_shipments.csv", _cell("inbound_shipments.csv", row, "covers_days"), "covers_days")
            self.shipments[sid] = Shipment(sid, supplier, part_id, pid, eta, covers)
            self.shipments_by_key.setdefault((part_id, pid, eta), []).append(sid)
            self._index_name(sid, "shipment", sid)
        self.shipments_by_key = {key: tuple(ids) for key, ids in self.shipments_by_key.items()}

        for row in lots:
            lid = _cell("part_lots.csv", row, "lot_id")
            if lid in self.lots:
                raise OntologyError(f"duplicate lot_id {lid}")
            part_id = _cell("part_lots.csv", row, "part_id")
            supplier = _cell("part_lots.csv", row, "supplier_id")
            pid = _cell("part_lots.csv", row, "plant_id")
            if part_id not in self.parts:
                raise OntologyError(f"lot {lid} references unknown part {part_id}")
            if supplier not in self.suppliers:
                raise OntologyError(f"lot {lid} references unknown supplier {supplier}")
            if pid not in self.plants:
                raise OntologyError(f"lot {lid} references unknown plant {pid}")
            first = _parse_date("part_lots.csv", _cell("part_lots.csv", row, "first_build_date"), "first_build_date")
            last = _parse_date("part_lots.csv", _cell("part_lots.csv", row, "last_build_date"), "last_build_date")
            if last < first:
                raise OntologyError(f"lot {lid} last_build_date is before first_build_date")
            self.lots[lid] = Lot(lid, part_id, supplier, pid, first, last)
            self._index_name(lid, "lot", lid)

        for row in vehicles:
            vin = _cell("finished_vehicles.csv", row, "vin")
            if vin in self.vehicles:
                raise OntologyError(f"duplicate vin {vin}")
            poid = _cell("finished_vehicles.csv", row, "production_order_id")
            if poid not in self.orders:
                raise OntologyError(f"vehicle {vin} references unknown production order {poid}")
            did = _cell("finished_vehicles.csv", row, "dealer_id")
            if did not in self.dealers:
                raise OntologyError(f"vehicle {vin} references unknown dealer {did}")
            cid = row.get("customer_id", "") or ""
            if cid and cid not in self.customers:
                raise OntologyError(f"vehicle {vin} references unknown customer {cid}")
            build = _parse_date("finished_vehicles.csv", _cell("finished_vehicles.csv", row, "build_date"), "build_date")
            self.vehicles[vin] = Vehicle(
                vin,
                poid,
                _cell("finished_vehicles.csv", row, "model"),
                _cell("finished_vehicles.csv", row, "plant_id"),
                build,
                did,
                _cell("finished_vehicles.csv", row, "status"),
                cid,
            )

        for row in aliases:
            alias = _cell("aliases.csv", row, "alias")
            kind = _cell("aliases.csv", row, "entity_type")
            entity_id = _cell("aliases.csv", row, "entity_id")
            self._require_alias_target(kind, entity_id)
            self._index_name(alias, kind, entity_id)

        self._check_calendar(calendar)

    def _require_alias_target(self, kind: str, entity_id: str) -> None:
        tables = {
            "plant": self.plants,
            "part": self.parts,
            "supplier": self.suppliers,
            "model": self.models,
            "shipment": self.shipments,
            "lot": self.lots,
        }
        if kind not in tables:
            raise OntologyError(f"alias entity_type {kind} is not supported")
        if entity_id not in tables[kind]:
            raise OntologyError(f"alias references unknown {kind} {entity_id}")

    def _index_name(self, text: str, entity_type: str, entity_id: str) -> None:
        key = normalize_name(text)
        self.name_index.setdefault(key, set()).add((entity_type, entity_id))

    def _check_calendar(self, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        from .calendarutil import date_to_idx, idx_to_date

        for row in rows:
            raw = _cell("calendar.csv", row, "date")
            d = _parse_date("calendar.csv", raw, "date")
            idx = _parse_int("calendar.csv", _cell("calendar.csv", row, "day_index"), "day_index")
            if not is_working_day(d) or date_to_idx(d) != idx or idx_to_date(idx) != d:
                raise OntologyError(f"calendar.csv row {raw} does not match the tool calendar")
            weekday = _cell("calendar.csv", row, "weekday")
            english = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
            if english[d.weekday()] != weekday:
                raise OntologyError(f"calendar.csv weekday mismatch for {raw}")

    def resolve_name(self, text: str, entity_type: str | None = None) -> NameHit:
        """Exact normalized match. Substring nicknames are left unresolved."""
        matches = self.name_index.get(normalize_name(text), set())
        if entity_type is not None:
            matches = {item for item in matches if item[0] == entity_type}
        frozen = tuple(sorted(matches))
        if len(frozen) == 1:
            kind, entity_id = frozen[0]
            return NameHit("found", kind, entity_id, frozen)
        if not frozen:
            return NameHit("not_found", matches=frozen)
        return NameHit("ambiguous", matches=frozen)

    def resolve_mention(self, mention: str, entity_type: str | None = None) -> NameHit:
        """Resolve a surface mention. Exact names win; otherwise whole-word aliases.

        A shorter alias inside a longer one counts only when both name the same
        entity. Two different entities in one mention are ambiguous. Keys shorter
        than three characters are not used for the in-text scan.
        """
        exact = self.resolve_name(mention, entity_type)
        if exact.status in {"found", "ambiguous"}:
            return exact
        normalized = normalize_name(mention)
        found: set[tuple[str, str]] = set()
        for key, matches in self.name_index.items():
            if len(key) < 3 or not _phrase_bounded(key, normalized):
                continue
            for kind, entity_id in matches:
                if entity_type is not None and kind != entity_type:
                    continue
                found.add((kind, entity_id))
        frozen = tuple(sorted(found))
        if len(frozen) == 1:
            kind, entity_id = frozen[0]
            return NameHit("found", kind, entity_id, frozen)
        if not frozen:
            return NameHit("not_found")
        return NameHit("ambiguous", matches=frozen)

    def models_using(self, part_id: str) -> tuple[str, ...]:
        return tuple(sorted(self.part_models.get(part_id, ())))

    def plants_building(self, model: str) -> tuple[str, ...]:
        return self.model_plants.get(model, ())

    def customer_orders_for(self, production_order_id: str) -> tuple[CustomerOrder, ...]:
        ids = self.customer_orders_by_po.get(production_order_id, ())
        return tuple(self.customer_orders[coid] for coid in ids)

    def counts(self) -> dict[str, int]:
        return {
            "plants": len(self.plants),
            "models": len(self.models),
            "parts": len(self.parts),
            "suppliers": len(self.suppliers),
            "bom_models": len(self.bom),
            "production_orders": len(self.orders),
            "customer_orders": len(self.customer_orders),
            "customers": len(self.customers),
            "dealers": len(self.dealers),
            "shipments": len(self.shipments),
            "lots": len(self.lots),
            "vehicles": len(self.vehicles),
            "aliases": sum(len(v) for v in self.name_index.values()),
            "loaner_pools": len(self.loaners),
            "destination_days": len(self.destination_days),
        }


def _phrase_bounded(phrase: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _default_horizon() -> date:
    from .calendarutil import ABSOLUTE_HORIZON

    return ABSOLUTE_HORIZON


def build_ontology(tables: dict[str, list[dict[str, str]]], horizon_end: date | None = None) -> Ontology:
    return Ontology(tables, horizon_end)


def load_ontology(data_dir: str | Path, horizon_end: date | None = None) -> Ontology:
    root = Path(data_dir)
    tables: dict[str, list[dict[str, str]]] = {}
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise OntologyError(f"{root} is missing {missing}")
    for name in REQUIRED_FILES:
        tables[name] = read_csv(root / name)
    return Ontology(tables, horizon_end)
