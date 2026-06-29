from app.logic import load_status
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
