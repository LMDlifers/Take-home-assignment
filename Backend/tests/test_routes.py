from datetime import date

from fastapi.testclient import TestClient

from app import main


def test_machine_loads_add_status(monkeypatch) -> None:
    monkeypatch.setattr(
        main.db,
        "get_machine_loads",
        lambda: [
            {
                "machine_id": "M3",
                "machine_name": "Hydraulic Press Line 1",
                "machine_type": "Press",
                "capacity_hours_day": 10,
                "available_hours_today": 10,
                "current_status": "available",
                "queued_hours": 19,
                "load_pct": 190,
            }
        ],
    )

    response = TestClient(main.app).get("/api/v1/machines/load")

    assert response.status_code == 200
    assert response.json()[0]["load_status"] == "overloaded"


def test_at_risk_orders_returns_list(monkeypatch) -> None:
    monkeypatch.setattr(
        main.db,
        "get_at_risk_orders",
        lambda: [
            {
                "wo_id": "WO-1003",
                "product_code": "PART-C",
                "quantity": 60,
                "required_machine": "M4",
                "processing_time_hr": 5,
                "priority": 1,
                "due_date": date(2026, 6, 29),
                "status": "delayed",
                "available_hours_today": 0,
                "machine_status": "unavailable",
                "risk_reason": "Machine unavailable - cannot schedule",
            }
        ],
    )

    response = TestClient(main.app).get("/api/v1/orders/at-risk")

    assert response.status_code == 200
    assert response.json()[0]["wo_id"] == "WO-1003"


def test_simulate_downtime_returns_affected_orders(monkeypatch) -> None:
    monkeypatch.setattr(
        main.db,
        "get_machine",
        lambda machine_id: {
            "machine_id": machine_id,
            "available_hours_today": 4.5,
        },
    )
    monkeypatch.setattr(
        main.db,
        "get_active_orders_for_machine",
        lambda machine_id: [
            {
                "wo_id": "WO-1008",
                "product_code": "PART-F",
                "required_machine": machine_id,
                "processing_time_hr": 5.5,
                "priority": 2,
                "due_date": date(2026, 6, 30),
                "status": "pending",
            }
        ],
    )

    response = TestClient(main.app).post(
        "/api/v1/simulate/downtime",
        json={"machine_id": "M2", "downtime_hours": 4},
    )

    assert response.status_code == 200
    assert response.json()["affected_orders"][0]["wo_id"] == "WO-1008"


def test_ask_route_returns_response_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "answer_question",
        lambda question: {
            "question": question,
            "tool_used": "check_load",
            "sql_used": "SELECT machine_id FROM v_machine_load",
            "data": [{"machine_id": "M3"}],
            "answer": "M3 is overloaded.",
            "explanation": "M3 is overloaded.",
            "confidence": 0.75,
            "follow_ups": ["Show high-priority orders due this week."],
        },
    )

    response = TestClient(main.app).post(
        "/api/v1/ask",
        json={"question": "Which machines are overloaded?"},
    )

    assert response.status_code == 200
    assert response.json()["tool_used"] == "check_load"
