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


PARSE_TEXT = "Plant update: Huntsville line is dark from next Tuesday through next Thursday."


def build_parse_demo():
    """Fictional outage text plus a labeled stub extraction. Not a live model call."""
    tables = {
        "plants.csv": [{"plant_id": "PA", "name": "Plant A", "city": "A", "slots_per_day": "5"}],
        "plant_models.csv": [{"plant_id": "PA", "model": "CAR"}],
        "models.csv": [{"model": "CAR", "segment": ""}],
        "parts.csv": [{"part_id": "BAT", "name": "battery", "category": ""}],
        "bom.csv": [{"model": "CAR", "part_id": "BAT"}],
        "suppliers.csv": [{"supplier_id": "SUP", "name": "Supplier", "city": ""}],
        "dealers.csv": [{"dealer_id": "D1", "name": "Dealer", "city": "", "region": "", "transit_days": ""}],
        "customers.csv": [{"customer_id": "CU-1", "name": "Casey", "tier": "Standard"}],
        "loaners.csv": [{"dealer_id": "D1", "loaners_available": "1"}],
        "production_orders.csv": [
            {"production_order_id": "PO-TUE", "plant_id": "PA", "build_date": "2027-03-09", "model": "CAR", "units": "1"},
            {"production_order_id": "PO-WED", "plant_id": "PA", "build_date": "2027-03-10", "model": "CAR", "units": "1"},
            {"production_order_id": "PO-THU", "plant_id": "PA", "build_date": "2027-03-11", "model": "CAR", "units": "1"},
        ],
        "aliases.csv": [{"alias": "Huntsville", "entity_type": "plant", "entity_id": "PA"}],
    }
    stub = {
        "incidents": [
            {
                "category": "plant_outage",
                "confirmed": True,
                "evidence_quotes": ["Huntsville line is dark from next Tuesday through next Thursday"],
                "entities": [{"role": "plant", "mention": "Huntsville line"}],
                "dates": [
                    {"role": "outage_start", "mention": "next Tuesday"},
                    {"role": "outage_end", "mention": "next Thursday"},
                ],
            }
        ]
    }
    return build_ontology(tables, HORIZON), "demo-parse", PARSE_TEXT, stub


def build_quality_demo():
    """Return a fictional quality hold with one sold owner and one unsold vehicle."""
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
        "models.csv": [{"model": "CAR", "segment": ""}, {"model": "VAN", "segment": ""}],
        "parts.csv": [{"part_id": "BAT", "name": "battery", "category": ""}],
        "bom.csv": [{"model": "CAR", "part_id": "BAT"}],
        "suppliers.csv": [{"supplier_id": "SUP", "name": "Supplier", "city": ""}],
        "dealers.csv": [
            {"dealer_id": "D1", "name": "Dealer", "city": "", "region": "", "transit_days": ""},
            {"dealer_id": "D2", "name": "Other", "city": "", "region": "", "transit_days": ""},
        ],
        "customers.csv": [
            {"customer_id": "CU-OWN", "name": "Owen", "tier": "Gold"},
            {"customer_id": "CU-ELSE", "name": "Else", "tier": "Standard"},
        ],
        "loaners.csv": [
            {"dealer_id": "D1", "loaners_available": "1"},
            {"dealer_id": "D2", "loaners_available": "1"},
        ],
        "production_orders.csv": [
            {"production_order_id": "PO-MON", "plant_id": "PA", "build_date": "2027-02-22", "model": "CAR", "units": "2"},
            {"production_order_id": "PO-WED", "plant_id": "PA", "build_date": "2027-02-24", "model": "CAR", "units": "1"},
            {"production_order_id": "PO-VAN", "plant_id": "PA", "build_date": "2027-02-22", "model": "VAN", "units": "1"},
            {"production_order_id": "PO-PB", "plant_id": "PB", "build_date": "2027-02-22", "model": "CAR", "units": "1"},
        ],
        "part_lots.csv": [
            {
                "lot_id": "LOT-DEMO",
                "part_id": "BAT",
                "supplier_id": "SUP",
                "plant_id": "PA",
                "first_build_date": "2027-02-22",
                "last_build_date": "2027-02-24",
            }
        ],
        "finished_vehicles.csv": [
            {
                "vin": "VIN-SOLD",
                "production_order_id": "PO-MON",
                "model": "CAR",
                "plant_id": "PA",
                "build_date": "2027-02-22",
                "dealer_id": "D1",
                "status": "sold",
                "customer_id": "CU-OWN",
            },
            {
                "vin": "VIN-STOCK",
                "production_order_id": "PO-WED",
                "model": "CAR",
                "plant_id": "PA",
                "build_date": "2027-02-24",
                "dealer_id": "D2",
                "status": "in_stock",
                "customer_id": "",
            },
            {
                "vin": "VIN-VAN",
                "production_order_id": "PO-VAN",
                "model": "VAN",
                "plant_id": "PA",
                "build_date": "2027-02-22",
                "dealer_id": "D1",
                "status": "in_stock",
                "customer_id": "",
            },
            {
                "vin": "VIN-OTHER",
                "production_order_id": "PO-PB",
                "model": "CAR",
                "plant_id": "PB",
                "build_date": "2027-02-22",
                "dealer_id": "D2",
                "status": "sold",
                "customer_id": "CU-ELSE",
            },
        ],
    }
    event = {
        "event_id": "demo-quality",
        "incidents": [{"kind": "quality_hold", "lot_id": "LOT-DEMO"}],
    }
    return build_ontology(tables), event
