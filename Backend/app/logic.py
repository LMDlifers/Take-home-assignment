"""Deterministic scheduling rules.

Implements delay detection, machine load calculation, downtime simulation, and
recommendation helpers in testable Python code instead of relying on the LLM for
core correctness.
"""

from datetime import date
from decimal import Decimal
from decimal import ROUND_HALF_UP
from typing import Any


def load_status(load_pct: float) -> str:
    """Classify machine load according to the assignment thresholds."""
    if load_pct > 100:
        return "overloaded"
    if load_pct > 85:
        return "at_risk"
    return "normal" #Anything 85% or below is considered normal.


def calculate_machine_loads(
    machines: list[dict[str, Any]],
    orders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Calculate queued machine load in application code."""
    queued_by_machine = {machine["machine_id"]: 0.0 for machine in machines}
    active_count_by_machine = {machine["machine_id"]: 0 for machine in machines}
    for order in orders:
        if order["status"] in {"pending", "in_progress"}:
            queued_by_machine[order["required_machine"]] += float(order["processing_time_hr"])
            active_count_by_machine[order["required_machine"]] += 1

    rows = []
    for machine in machines:
        capacity = float(machine["capacity_hours_day"])
        queued = queued_by_machine[machine["machine_id"]]
        load_pct = float(
            Decimal(str(queued / capacity * 100)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        ) if capacity else 0
        row = dict(machine)
        row["queued_hours"] = queued
        row["active_order_count"] = active_count_by_machine[machine["machine_id"]]
        row["load_pct"] = load_pct
        row["load_status"] = load_status(load_pct)
        rows.append(row)

    return sorted(rows, key=lambda row: (-float(row["load_pct"]), row["machine_id"]))


def detect_at_risk_orders(
    rows: list[dict[str, Any]],
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Detect delayed, blocked, and capacity-risk orders in application code."""
    today = today or date.today()
    risks = []
    for row in rows:
        status = row["status"]
        due_date = row["due_date"]
        machine_status = row["machine_status"]
        over_available = float(row["processing_time_hr"]) > float(row["available_hours_today"])
        due_now = status in {"pending", "in_progress"} and due_date <= today
        blocked = status == "pending" and machine_status == "unavailable"

        if status != "delayed" and not due_now and not blocked and not over_available:
            continue

        item = dict(row)
        if machine_status == "unavailable":
            item["risk_reason"] = "Machine unavailable - cannot schedule"
        elif over_available:
            item["risk_reason"] = "Processing time exceeds available machine hours today"
        elif due_date <= today:
            item["risk_reason"] = "Due date passed and not completed"
        else:
            item["risk_reason"] = "Order is already flagged delayed"
        risks.append(item)

    return sorted(risks, key=lambda row: (int(row["priority"]), row["due_date"], row["wo_id"]))


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
        if order.get("status") == "delayed" and order.get("machine_status") != "unavailable":
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
                    "action": f"Reschedule low-priority work off {machine_id}",
                    "machine_id": machine_id,
                    "affected_work_orders": low_priority,
                    "reason": "Machine is overloaded; move lower-priority work to protect urgent orders.",
                }
            )

    return recommendations or [
        {
            "action": "No immediate corrective action",
            "reason": "No overloaded machines or blocked orders were found.",
        }
    ]
