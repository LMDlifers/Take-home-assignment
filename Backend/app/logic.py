"""Deterministic scheduling rules.

Implements delay detection, machine load calculation, downtime simulation, and
recommendation helpers in testable Python code instead of relying on the LLM for
core correctness.
"""

from typing import Any


def load_status(load_pct: float) -> str:
    """Classify machine load according to the assignment thresholds."""
    if load_pct > 100:
        return "overloaded"
    if load_pct > 85:
        return "at_risk"
    return "normal"


def add_load_status(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach load_status to machine load rows."""
    labelled = []
    for row in rows:
        item = dict(row)
        item["load_status"] = load_status(float(item["load_pct"] or 0))
        labelled.append(item)
    return labelled


def simulate_downtime(
    machine: dict[str, Any],
    orders: list[dict[str, Any]],
    downtime_hours: float,
) -> dict[str, Any]:
    """Calculate which active orders no longer fit after extra downtime."""
    original_available = float(machine["available_hours_today"])
    new_available = max(original_available - downtime_hours, 0)
    affected_orders = []

    for order in orders:
        processing_time = float(order["processing_time_hr"])
        if processing_time > new_available:
            affected = dict(order)
            affected["estimated_extra_delay_hours"] = processing_time - new_available
            affected_orders.append(affected)

    return {
        "machine_id": machine["machine_id"],
        "original_available_hours": original_available,
        "new_available_hours": new_available,
        "downtime_hours": downtime_hours,
        "affected_orders": affected_orders,
    }


def recommend_actions(
    machine_loads: list[dict[str, Any]],
    at_risk_orders: list[dict[str, Any]],
    active_orders_by_machine: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Return simple planner actions from deterministic risk and load data."""
    active_orders_by_machine = active_orders_by_machine or {}
    recommendations = []

    unavailable_machines = sorted(
        {
            order["required_machine"]
            for order in at_risk_orders
            if order.get("machine_status") == "unavailable"
        }
    )
    for machine_id in unavailable_machines:
        affected = [
            order["wo_id"]
            for order in at_risk_orders
            if order.get("required_machine") == machine_id
        ]
        recommendations.append(
            {
                "action": f"Escalate repair for {machine_id}",
                "machine_id": machine_id,
                "affected_work_orders": affected,
                "reason": "Machine is unavailable and blocking active or delayed orders.",
            }
        )

    for order in at_risk_orders:
        if order.get("status") == "delayed" and order.get("required_machine") != "M4":
            recommendations.append(
                {
                    "action": f"Reroute or split {order['wo_id']}",
                    "wo_id": order["wo_id"],
                    "machine_id": order["required_machine"],
                    "reason": order["risk_reason"],
                }
            )

    for machine in machine_loads:
        if machine.get("load_status") != "overloaded":
            continue

        machine_id = machine["machine_id"]
        low_priority = [
            order["wo_id"]
            for order in active_orders_by_machine.get(machine_id, [])
            if int(order.get("priority", 0)) >= 4
        ]
        if low_priority:
            recommendations.append(
                {
                    "action": f"Move low-priority work off {machine_id}",
                    "machine_id": machine_id,
                    "affected_work_orders": low_priority,
                    "reason": "Machine is overloaded; move lower-priority work to protect urgent orders.",
                }
            )
        else:
            recommendations.append(
                {
                    "action": f"Rebalance queue on {machine_id}",
                    "machine_id": machine_id,
                    "load_pct": machine["load_pct"],
                    "reason": "Queued work exceeds daily capacity.",
                }
            )

    return recommendations or [
        {
            "action": "No immediate corrective action",
            "reason": "No overloaded machines or blocked orders were found.",
        }
    ]
