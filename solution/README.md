# Dry-run policy engine

Student code for supplier delays, plant outages, and quality holds. It reads the company tables, accepts a structured incident list, and prints proposed tool calls plus graph paths. It does not start `enterprise_server.py`, does not call write tools, and does not write `action_log.jsonl`.

Free-text parsing, agent orchestration, the control-tower UI, and scored execution are later milestones. `enterprise.py`, `enterprise_server.py`, and `data/` are organizer files and are not modified.

## Run

From the repository root:

```bash
python -m solution.hth.dry_run --demo
python -m solution.hth.dry_run --demo-quality
python -m solution.hth.dry_run --check-data --data hack_the_highway_student_package/hack_the_highway_package/data
python -m solution.hth.dry_run --data hack_the_highway_student_package/hack_the_highway_package/data --input incident.json
python -m unittest discover -s tests -v
```

`--demo` is a fictional delay. `--demo-quality` is a fictional quality hold. Neither is one of the 15 scored events. `--output` writes the plan JSON. The command refuses to place that file inside the company data directory.

Exit `0` means a plan was produced. `status` is `ok` or `blocked`. Exit `2` means the input or the tables could not be read. A blocked plan has an empty `tool_calls` list.

## Input

```json
{
  "event_id": "example-1",
  "incidents": [
    {
      "kind": "supplier_delay",
      "shipment_id": "SH-EXAMPLE",
      "new_eta": "2027-03-18"
    },
    {
      "kind": "plant_outage",
      "plant_id": "P_EXAMPLE",
      "start_date": "2027-03-12",
      "end_date": "2027-03-15"
    },
    {
      "kind": "quality_hold",
      "lot_id": "LOT-EXAMPLE",
      "part_id": "PART-EXAMPLE",
      "plant_id": "P_EXAMPLE"
    }
  ]
}
```

Replace the example ids with ids from the tables. This shape is not a scored event.

A delay may omit `shipment_id` and pass `part_id`, `plant_id`, and `original_eta` instead. Optional ids must match the shipment or the event is blocked. `new_eta` is required. Several incidents share one slot map and one loaner pool.

A quality hold may pass `lot_id` alone. Any part, supplier, plant, or dates sent with it must match that lot. Without `lot_id`, pass `part_id`, `plant_id`, and either `build_date` or both `first_build_date` and `last_build_date`. Do not feed `events.json` to this command. Those messages are free text, and this milestone does not classify them.

## What the plan contains

`tool_calls` uses the simulator argument names: `mes.reschedule`, `mes.transfer`, `dealer.notify`, `crm.assign_loaner`, `quality.hold_lot`, `dealer.stop_sale`, and `crm.notify_customer`. `moves` explains the slot choice. `findings` gives every production order a schedule reason. `vehicle_findings` gives every finished vehicle a quality reason. `graph_paths` cites Shipment → Part → Model → ProductionOrder → CustomerOrder → Dealer / Customer, Plant → ProductionOrder → CustomerOrder → Dealer / Customer, or Lot → ProductionOrder → Vehicle → Dealer / Customer. `assumptions` is the rule list below.

## Assumptions

These close gaps in the handbook. Uncertain or contradictory inputs produce `status: blocked` and no tool calls.

- One production order uses one slot, whatever `units` says.
- A delay affects the receiving plant only, for models whose BOM contains the part, on build dates in `[original ETA, new ETA)`. `covers_days` is ignored. The first legal day is the new ETA.
- An outage is inclusive. The plant restarts the next working day. Weekend outage endpoints are allowed. Delay ETAs must be weekdays.
- Orders before today (2027-03-08) are already built. They are not moved and do not create notices or loaners.
- The planner fills same-plant tool days on or after the legal start before it transfers. A transfer uses another plant that builds the model, and only on those same days. It does not move the build earlier than the disruption end.
- Placement order is original build date, then `production_order_id`. The transfer plant tie-break is the lowest `plant_id`.
- An order hit by several incidents moves once, on or after the latest legal start. Dealer lists and loaner pools use the union.
- One `dealer.notify` per dealer, customer-order ids sorted lexicographically, only affected orders.
- Loaners are per affected customer order: Platinum, then earlier promised date, then customer-order id. Standard gets none. A missing pool blocks the event.
- A quality hold selects one lot. A single `build_date` picks the lot whose window contains it, and vehicles then use that lot's full inclusive dates. An exact window must equal the lot's first and last dates. Several matching lots, or a supplied window that is not one lot's dates, block the event.
- Affected vehicles are at the lot's plant on those dates, and the production order's model uses the part. The vehicle must match that order's plant, model, and build date.
- Unsold `in_stock` vehicles are one `dealer.stop_sale` per dealer. Each sold owner gets one `crm.notify_customer`. An unsold vehicle is not also notified. A sold vehicle with no customer blocks the event. Quality actions do not consume slots or loaners.
- If any affected order cannot be placed, or lot evidence is ambiguous, every tool call is withheld.

## Tests

Synthetic companies cover window boundaries, unaffected orders, BOM and receiving-plant filters, weekdays, the tool horizon, capacity, transfers, dealer grouping, loaner rank, compound slot sharing, and quality holds (inclusive lot dates, wrong plant or model, sold versus unsold, missing owners, repeated and ambiguous lots, and a mixed delay plus quality event). Tests apply plans to an in-memory `Enterprise` loaded from a temporary copy of synthetic CSVs. The packaged `data/` directory is only read.
