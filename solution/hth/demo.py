"""Fictional one-week company used by ``--demo``. Not a scored event."""

from __future__ import annotations

from datetime import date

from .ontology import build_ontology

HORIZON = date(2027, 3, 12)


def build_demo():
    """Return ``(ontology, event)`` for a single supplier delay with spare capacity."""
    tables = {
        "plants.csv": [
            {"plant_id": "PA", "name": "Plant A", "city": "A", "slots_per_day": "5"},
            {"plant_id": "PB", "name": "Plant B", "city": "B", "slots_per_day": "5"},
        ],
        "plant_models.csv": [
            {"plant_id": "PA", "model": "CAR"},
            {"plant_id": "PA", "model": "VAN"},
            {"plant_id": "PB", "model": "CAR"},
        ],
        "models.csv": [
            {"model": "CAR", "segment": ""},
            {"model": "VAN", "segment": ""},
        ],
        "parts.csv": [{"part_id": "BAT", "name": "battery", "category": ""}],
        "bom.csv": [{"model": "CAR", "part_id": "BAT"}],
        "suppliers.csv": [{"supplier_id": "SUP", "name": "Supplier", "city": ""}],
        "dealers.csv": [
            {"dealer_id": "D1", "name": "Dealer", "city": "", "region": "", "transit_days": ""},
        ],
        "customers.csv": [
            {"customer_id": "CU-P", "name": "Pat", "tier": "Platinum"},
            {"customer_id": "CU-G", "name": "Glen", "tier": "Gold"},
            {"customer_id": "CU-S", "name": "Sam", "tier": "Standard"},
            {"customer_id": "CU-U", "name": "Uma", "tier": "Standard"},
        ],
        "loaners.csv": [{"dealer_id": "D1", "loaners_available": "1"}],
        "production_orders.csv": [
            {"production_order_id": "PO-A", "plant_id": "PA", "build_date": "2027-03-08", "model": "CAR", "units": "10"},
            {"production_order_id": "PO-B", "plant_id": "PA", "build_date": "2027-03-09", "model": "CAR", "units": "10"},
            {"production_order_id": "PO-C", "plant_id": "PA", "build_date": "2027-03-08", "model": "VAN", "units": "10"},
            {"production_order_id": "PO-D", "plant_id": "PB", "build_date": "2027-03-08", "model": "CAR", "units": "10"},
            {"production_order_id": "PO-E", "plant_id": "PA", "build_date": "2027-03-10", "model": "CAR", "units": "10"},
            {"production_order_id": "PO-F", "plant_id": "PA", "build_date": "2027-03-11", "model": "CAR", "units": "10"},
        ],
        "customer_orders.csv": [
            {
                "customer_order_id": "CO-A",
                "production_order_id": "PO-A",
                "dealer_id": "D1",
                "customer_id": "CU-P",
                "model": "CAR",
                "promised_date": "2027-03-20",
            },
            {
                "customer_order_id": "CO-B",
                "production_order_id": "PO-B",
                "dealer_id": "D1",
                "customer_id": "CU-G",
                "model": "CAR",
                "promised_date": "2027-03-10",
            },
            {
                "customer_order_id": "CO-S",
                "production_order_id": "PO-A",
                "dealer_id": "D1",
                "customer_id": "CU-S",
                "model": "CAR",
                "promised_date": "2027-03-09",
            },
            {
                "customer_order_id": "CO-U",
                "production_order_id": "PO-C",
                "dealer_id": "D1",
                "customer_id": "CU-U",
                "model": "VAN",
                "promised_date": "2027-03-09",
            },
        ],
        "inbound_shipments.csv": [
            {
                "shipment_id": "SH-DEMO",
                "supplier_id": "SUP",
                "part_id": "BAT",
                "plant_id": "PA",
                "eta_date": "2027-03-08",
                "covers_days": "99",
            }
        ],
    }
    event = {
        "event_id": "demo-delay",
        "incidents": [{"kind": "supplier_delay", "shipment_id": "SH-DEMO", "new_eta": "2027-03-10"}],
    }
    return build_ontology(tables, HORIZON), event
