"""Schema and role graph. Counts come from a loaded ontology; edges are the tables' foreign keys."""

from __future__ import annotations

from .agents import CARE, COORDINATOR, DEALER, ROLE_TOOLS, SUPPLY
from .ontology import Ontology


def architecture_document(ontology: Ontology | None = None) -> dict:
    counts = ontology.counts() if ontology is not None else {}
    nodes = [
        {"id": "Supplier", "records": counts.get("suppliers")},
        {"id": "Part", "records": counts.get("parts")},
        {"id": "Model", "records": counts.get("models")},
        {"id": "Plant", "records": counts.get("plants")},
        {"id": "Shipment", "records": counts.get("shipments")},
        {"id": "Lot", "records": counts.get("lots")},
        {"id": "ProductionOrder", "records": counts.get("production_orders")},
        {"id": "CustomerOrder", "records": counts.get("customer_orders")},
        {"id": "Customer", "records": counts.get("customers")},
        {"id": "Dealer", "records": counts.get("dealers")},
        {"id": "Vehicle", "records": counts.get("vehicles")},
        {"id": "Alias", "records": counts.get("aliases")},
    ]
    edges = [
        {"from": "Supplier", "to": "Shipment", "via": "supplier_id"},
        {"from": "Part", "to": "Shipment", "via": "part_id"},
        {"from": "Plant", "to": "Shipment", "via": "plant_id"},
        {"from": "Part", "to": "Model", "via": "bom.csv"},
        {"from": "Plant", "to": "Model", "via": "plant_models.csv"},
        {"from": "Plant", "to": "ProductionOrder", "via": "plant_id"},
        {"from": "Model", "to": "ProductionOrder", "via": "model"},
        {"from": "ProductionOrder", "to": "CustomerOrder", "via": "production_order_id"},
        {"from": "CustomerOrder", "to": "Dealer", "via": "dealer_id"},
        {"from": "CustomerOrder", "to": "Customer", "via": "customer_id"},
        {"from": "Supplier", "to": "Lot", "via": "supplier_id"},
        {"from": "Part", "to": "Lot", "via": "part_id"},
        {"from": "Plant", "to": "Lot", "via": "plant_id"},
        {"from": "ProductionOrder", "to": "Vehicle", "via": "production_order_id"},
        {"from": "Vehicle", "to": "Dealer", "via": "dealer_id"},
        {"from": "Vehicle", "to": "Customer", "via": "customer_id"},
        {"from": "Alias", "to": "Plant", "via": "entity_id"},
        {"from": "Alias", "to": "Part", "via": "entity_id"},
        {"from": "Alias", "to": "Supplier", "via": "entity_id"},
    ]
    return {
        "kind": "ontology_and_role_schema",
        "nodes": nodes,
        "edges": edges,
        "roles": {
            "coordinator": COORDINATOR,
            "specialists": [
                {"id": SUPPLY, "llm": False, "tools": sorted(ROLE_TOOLS[SUPPLY])},
                {"id": DEALER, "llm": False, "tools": sorted(ROLE_TOOLS[DEALER])},
                {"id": CARE, "llm": False, "tools": sorted(ROLE_TOOLS[CARE])},
            ],
            "specialist_llm_calls": 0,
            "llm": "Free-text interpretation only, through a labeled stub or a configured live adapter.",
        },
    }
