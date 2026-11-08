"""Synthetic companies for policy tests. Not the packaged dataset."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from solution.hth.calendarutil import FIRST_IDX, LAST_IDX, idx_to_date
from solution.hth.ontology import REQUIRED_FILES, build_ontology

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

FIELDS = {
    "plants.csv": ("plant_id", "name", "city", "slots_per_day"),
    "plant_models.csv": ("plant_id", "model"),
    "models.csv": ("model", "segment"),
    "parts.csv": ("part_id", "name", "category"),
    "bom.csv": ("model", "part_id"),
    "suppliers.csv": ("supplier_id", "name", "city"),
    "production_orders.csv": ("production_order_id", "plant_id", "build_date", "model", "units"),
    "customer_orders.csv": ("customer_order_id", "production_order_id", "dealer_id", "customer_id", "model", "promised_date"),
    "customers.csv": ("customer_id", "name", "tier"),
    "dealers.csv": ("dealer_id", "name", "city", "region", "transit_days"),
    "loaners.csv": ("dealer_id", "loaners_available"),
    "inbound_shipments.csv": ("shipment_id", "supplier_id", "part_id", "plant_id", "eta_date", "covers_days"),
    "part_lots.csv": ("lot_id", "part_id", "supplier_id", "plant_id", "first_build_date", "last_build_date"),
    "finished_vehicles.csv": ("vin", "production_order_id", "model", "plant_id", "build_date", "dealer_id", "status", "customer_id"),
    "aliases.csv": ("alias", "entity_type", "entity_id"),
    "calendar.csv": ("day_index", "date", "weekday"),
}


class Company:
    def __init__(self, horizon_end: date | None = None):
        self.horizon_end = horizon_end
        self.plants: list[dict[str, str]] = []
        self.plant_models: list[dict[str, str]] = []
        self.models: list[dict[str, str]] = []
        self.parts: list[dict[str, str]] = []
        self.bom: list[dict[str, str]] = []
        self.suppliers: list[dict[str, str]] = []
        self.orders: list[dict[str, str]] = []
        self.customers: list[dict[str, str]] = []
        self.dealers: list[dict[str, str]] = []
        self.loaners: list[dict[str, str]] = []
        self.shipments: list[dict[str, str]] = []
        self.customer_orders: list[dict[str, str]] = []
        self.lots: list[dict[str, str]] = []
        self.vehicles: list[dict[str, str]] = []
        self.aliases: list[dict[str, str]] = []
        self._model_ids: set[str] = set()

    def add_plant(self, plant_id: str, slots: int, models: list[str]) -> Company:
        self.plants.append({"plant_id": plant_id, "name": plant_id, "city": "", "slots_per_day": str(slots)})
        for model in models:
            self.plant_models.append({"plant_id": plant_id, "model": model})
            if model not in self._model_ids:
                self._model_ids.add(model)
                self.models.append({"model": model, "segment": ""})
        return self

    def add_part(self, part_id: str, models: list[str]) -> Company:
        self.parts.append({"part_id": part_id, "name": part_id, "category": ""})
        for model in models:
            self.bom.append({"model": model, "part_id": part_id})
        return self

    def add_supplier(self, supplier_id: str) -> Company:
        self.suppliers.append({"supplier_id": supplier_id, "name": supplier_id, "city": ""})
        return self

    def add_shipment(
        self,
        shipment_id: str,
        supplier_id: str,
        part_id: str,
        plant_id: str,
        eta: date,
        covers_days: int = 5,
    ) -> Company:
        self.shipments.append(
            {
                "shipment_id": shipment_id,
                "supplier_id": supplier_id,
                "part_id": part_id,
                "plant_id": plant_id,
                "eta_date": eta.isoformat(),
                "covers_days": str(covers_days),
            }
        )
        return self

    def add_dealer(self, dealer_id: str, pool: int) -> Company:
        self.dealers.append({"dealer_id": dealer_id, "name": dealer_id, "city": "", "region": "", "transit_days": ""})
        self.loaners.append({"dealer_id": dealer_id, "loaners_available": str(pool)})
        return self

    def add_customer(self, customer_id: str, tier: str) -> Company:
        self.customers.append({"customer_id": customer_id, "name": customer_id, "tier": tier})
        return self

    def add_order(self, order_id: str, plant_id: str, build: date, model: str, units: int = 1) -> Company:
        self.orders.append(
            {
                "production_order_id": order_id,
                "plant_id": plant_id,
                "build_date": build.isoformat(),
                "model": model,
                "units": str(units),
            }
        )
        return self

    def add_customer_order(
        self,
        customer_order_id: str,
        production_order_id: str,
        dealer_id: str,
        customer_id: str,
        model: str,
        promised: date,
    ) -> Company:
        self.customer_orders.append(
            {
                "customer_order_id": customer_order_id,
                "production_order_id": production_order_id,
                "dealer_id": dealer_id,
                "customer_id": customer_id,
                "model": model,
                "promised_date": promised.isoformat(),
            }
        )
        return self

    def tables(self) -> dict[str, list[dict[str, str]]]:
        return {
            "plants.csv": self.plants,
            "plant_models.csv": self.plant_models,
            "models.csv": self.models,
            "parts.csv": self.parts,
            "bom.csv": self.bom,
            "suppliers.csv": self.suppliers,
            "production_orders.csv": self.orders,
            "customer_orders.csv": self.customer_orders,
            "customers.csv": self.customers,
            "dealers.csv": self.dealers,
            "loaners.csv": self.loaners,
            "inbound_shipments.csv": self.shipments,
            "part_lots.csv": self.lots,
            "finished_vehicles.csv": self.vehicles,
            "aliases.csv": self.aliases,
        }

    def ontology(self):
        return build_ontology(self.tables(), self.horizon_end)

    def write(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        tables = self.tables()
        calendar = []
        for index in range(FIRST_IDX, LAST_IDX + 1):
            day = idx_to_date(index)
            calendar.append({"day_index": str(index), "date": day.isoformat(), "weekday": WEEKDAYS[day.weekday()]})
        tables["calendar.csv"] = calendar
        missing = [name for name in REQUIRED_FILES if name not in tables]
        if missing:
            raise RuntimeError(f"synthetic writer is missing {missing}")
        for name, rows in tables.items():
            with (directory / name).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS[name])
                writer.writeheader()
                writer.writerows(rows)


def delay_event(event_id: str = "synthetic", **incident) -> dict:
    body = {"kind": "supplier_delay"}
    body.update(incident)
    return {"event_id": event_id, "incidents": [body]}


def tool_names(result: dict) -> list[str]:
    return [call["tool"] for call in result["tool_calls"]]


def calls(result: dict, tool: str) -> list[dict]:
    return [call for call in result["tool_calls"] if call["tool"] == tool]


def reasons(result: dict, incident_index: int = 0) -> dict[str, str]:
    return {
        row["production_order_id"]: row["reason"]
        for row in result["findings"]
        if row["incident_index"] == incident_index
    }


def moved_to(result: dict) -> dict[str, str]:
    return {move["production_order_id"]: move["to"]["date"] for move in result["moves"]}
