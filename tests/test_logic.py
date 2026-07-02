from datetime import date

from app.logic import calculate_machine_loads
from app.logic import detect_at_risk_orders
from app.logic import load_status
from app.logic import recommend_actions
from app.logic import simulate_downtime


def test_load_status_thresholds() -> None:
    assert load_status(101) == "overloaded"
    assert load_status(90) == "at_risk"
    assert load_status(50) == "normal"


def test_calculate_machine_loads_finds_expected_overloads() -> None:
    machines = [
        {"machine_id": "M3", "machine_name": "Press 1", "machine_type": "Press", "capacity_hours_day": 10, "available_hours_today": 10, "current_status": "available"},
        {"machine_id": "M6", "machine_name": "Mill 1", "machine_type": "Mill", "capacity_hours_day": 6, "available_hours_today": 6, "current_status": "available"},
        {"machine_id": "M5", "machine_name": "Laser", "machine_type": "Laser", "capacity_hours_day": 7.5, "available_hours_today": 7.5, "current_status": "available"},
    ]
    orders = [
        {"required_machine": "M3", "processing_time_hr": 9, "status": "in_progress"},
        {"required_machine": "M3", "processing_time_hr": 7, "status": "pending"},
        {"required_machine": "M3", "processing_time_hr": 3, "status": "pending"},
        {"required_machine": "M6", "processing_time_hr": 3.5, "status": "pending"},
        {"required_machine": "M6", "processing_time_hr": 4, "status": "pending"},
        {"required_machine": "M5", "processing_time_hr": 3, "status": "in_progress"},
        {"required_machine": "M5", "processing_time_hr": 2, "status": "pending"},
        {"required_machine": "M5", "processing_time_hr": 3, "status": "pending"},
    ]

    result = {row["machine_id"]: row for row in calculate_machine_loads(machines, orders)}

    assert result["M3"]["load_pct"] == 190
    assert result["M6"]["load_pct"] == 125
    assert result["M5"]["load_pct"] == 106.7
    assert all(row["load_status"] == "overloaded" for row in result.values())


def test_detect_at_risk_orders_covers_assignment_rules() -> None:
    rows = [
        {"wo_id": "WO-1003", "product_code": "PART-C", "quantity": 60, "required_machine": "M4", "processing_time_hr": 5, "priority": 1, "due_date": date(2026, 6, 30), "status": "delayed", "available_hours_today": 0, "machine_status": "unavailable"},
        {"wo_id": "WO-1002", "product_code": "PART-B", "quantity": 80, "required_machine": "M3", "processing_time_hr": 9, "priority": 1, "due_date": date(2026, 6, 30), "status": "in_progress", "available_hours_today": 10, "machine_status": "available"},
        {"wo_id": "WO-BLOCK", "product_code": "PART-C", "quantity": 1, "required_machine": "M4", "processing_time_hr": 1, "priority": 2, "due_date": date(2026, 7, 2), "status": "pending", "available_hours_today": 0, "machine_status": "unavailable"},
        {"wo_id": "WO-1008", "product_code": "PART-F", "quantity": 200, "required_machine": "M2", "processing_time_hr": 5.5, "priority": 2, "due_date": date(2026, 7, 1), "status": "pending", "available_hours_today": 4.5, "machine_status": "partial"},
        {"wo_id": "WO-OK", "product_code": "PART-A", "quantity": 1, "required_machine": "M1", "processing_time_hr": 1, "priority": 5, "due_date": date(2026, 7, 10), "status": "pending", "available_hours_today": 8, "machine_status": "available"},
    ]

    result = detect_at_risk_orders(rows, today=date(2026, 6, 30))

    assert [row["wo_id"] for row in result] == ["WO-1002", "WO-1003", "WO-1008", "WO-BLOCK"]
    assert result[1]["risk_reason"] == "Machine unavailable - cannot schedule"


def test_simulate_downtime_clamps_available_hours() -> None:
    result = simulate_downtime(
        {"machine_id": "M2", "available_hours_today": 2},
        [{"wo_id": "WO-1", "processing_time_hr": 1, "product_code": "PART-A"}],
        4,
    )

    assert result["new_available_hours"] == 0


def test_simulate_downtime_flags_orders_that_do_not_fit() -> None:
    result = simulate_downtime(
        {"machine_id": "M2", "available_hours_today": 4.5},
        [
            {"wo_id": "WO-1008", "processing_time_hr": 5.5},
            {"wo_id": "WO-1009", "processing_time_hr": 6.0, "status": "delayed"},
            {"wo_id": "WO-OK", "processing_time_hr": 0.25},
        ],
        4,
    )

    assert [order["wo_id"] for order in result["affected_orders"]] == ["WO-1008", "WO-1009"]
    assert result["affected_orders"][0]["estimated_extra_delay_hours"] == 5


def test_recommend_actions_prioritises_blockers_and_low_priority_work() -> None:
    result = recommend_actions(
        [
            {
                "machine_id": "M5",
                "load_pct": 106,
                "load_status": "overloaded",
            }
        ],
        [
            {
                "wo_id": "WO-1003",
                "required_machine": "M4",
                "status": "delayed",
                "machine_status": "unavailable",
                "risk_reason": "Machine unavailable",
            },
            {
                "wo_id": "WO-1009",
                "required_machine": "M2",
                "status": "delayed",
                "machine_status": "partial",
                "risk_reason": "Processing time exceeds available machine hours today",
            },
        ],
        {"M5": [{"wo_id": "WO-1013", "priority": 4}]},
    )

    actions = [row["action"] for row in result]

    assert "Escalate repair for M4" in actions
    assert "Reroute or split WO-1009" in actions
    assert "Reschedule low-priority work off M5" in actions
