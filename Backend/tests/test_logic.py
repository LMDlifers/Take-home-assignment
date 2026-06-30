from app.logic import load_status
from app.logic import recommend_actions
from app.logic import simulate_downtime


def test_load_status_thresholds() -> None:
    assert load_status(101) == "overloaded"
    assert load_status(90) == "at_risk"
    assert load_status(50) == "normal"


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
            {"wo_id": "WO-OK", "processing_time_hr": 0.25},
        ],
        4,
    )

    assert [order["wo_id"] for order in result["affected_orders"]] == ["WO-1008"]
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
    assert "Move low-priority work off M5" in actions
