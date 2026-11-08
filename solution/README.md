# Dry-run policy engine

Student code for supplier delays, plant outages, and quality holds. It reads the company tables, accepts a structured incident list, and prints proposed tool calls plus graph paths. It does not start `enterprise_server.py`, does not call write tools, and does not write `action_log.jsonl`.

Agent orchestration, the control-tower UI, and scored execution are later milestones. `enterprise.py`, `enterprise_server.py`, and `data/` are organizer files and are not modified.

## Run

From the repository root:

```bash
python -m solution.hth.dry_run --demo
python -m solution.hth.dry_run --demo-quality
python -m solution.hth.dry_run --demo-parse
python -m solution.hth.dry_run --check-data --data hack_the_highway_student_package/hack_the_highway_package/data
python -m solution.hth.dry_run --data hack_the_highway_student_package/hack_the_highway_package/data --input incident.json
python -m unittest discover -s tests -v
```

`--demo` is a fictional delay. `--demo-quality` is a fictional quality hold. `--demo-parse` runs a fictional sentence through a labeled stub extraction. That stub is not a live model call. None of the demos are scored events. `--output` writes the plan JSON. The command refuses to place that file inside the company data directory.

Free text needs `--event-id` and `--parser stub` or `--parser live`. There is no keyword fallback if a parser is not selected. Stub mode reads `--stub-file`, a JSON extraction you wrote or saved, and records `llm_invoked: false`. Live mode reads `HTH_LLM_BASE_URL`, `HTH_LLM_MODEL`, and an optional `HTH_LLM_API_KEY`. The key is sent only as an Authorization header and is not copied into the plan. The client posts to `{HTH_LLM_BASE_URL}/chat/completions`. This repository run did not call a live model.

```bash
python -m solution.hth.dry_run --data hack_the_highway_student_package/hack_the_highway_package/data --text-file message.txt --event-id local-1 --parser stub --stub-file extraction.json
python -m solution.hth.dry_run --data hack_the_highway_student_package/hack_the_highway_package/data --text-file message.txt --event-id local-1 --parser live
```

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

A quality hold may pass `lot_id` alone. Any part, supplier, plant, or dates sent with it must match that lot. Without `lot_id`, pass `part_id`, `plant_id`, and either `build_date` or both `first_build_date` and `last_build_date`. Structured `--input` does not call a model.

Free-text extraction uses categories `supplier_delay`, `plant_outage`, `quality_hold`, `noise`, and `unconfirmed`. The model may return verbatim quotes and date phrases only. Code resolves aliases with whole-word matches, resolves dates from Monday 2027-03-08, and calls the same planner. The model cannot supply production-order ids, customer-order ids, or VINs. `next <weekday>` means the soonest such weekday after today, so on this Monday `next Friday` is 2027-03-12 and `next Monday` is 2027-03-15. A phrase that disagrees with itself is not planned.

If any incident in one message is unconfirmed, or a quote, entity, or date does not resolve, the whole message becomes one `escalate` and no write tools. Noise-only text proposes nothing. Noise next to a resolved incident is ignored. A planner block after a clean resolution, such as no open slot, stays a block. Do not feed `events.json` to a live model from this tool unless you intend to send that text to your configured endpoint.

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

Synthetic companies cover window boundaries, unaffected orders, BOM and receiving-plant filters, weekdays, the tool horizon, capacity, transfers, dealer grouping, loaner rank, compound slot sharing, and quality holds. Text tests use labeled stub extractions, not live model calls. They cover alias word boundaries, relative dates, conflicting dates, quotes missing from the text, forged order or VIN fields, noise, escalation, and a compound message that must not leak a partial write. Planner tests still apply plans to an in-memory `Enterprise` loaded from temporary CSVs. The packaged `data/` directory is only read. Live HTTP parsing is implemented and was not executed.
