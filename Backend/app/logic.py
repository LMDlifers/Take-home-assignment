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
