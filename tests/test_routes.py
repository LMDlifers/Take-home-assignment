from datetime import date

from fastapi.testclient import TestClient

from app import main


def test_root_redirects_to_ui() -> None:
    response = TestClient(main.app).get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"


def test_ui_serves_plain_html() -> None:
    response = TestClient(main.app).get("/ui/")

    assert response.status_code == 200
    assert "Shop floor scheduling agent" in response.text
    assert "/api/v1/ask" in response.text


def test_machine_loads_add_status(monkeypatch) -> None:
    monkeypatch.setattr(
        main.db,
        "get_machines",
        lambda: [
            {
                "machine_id": "M3",
                "machine_name": "Hydraulic Press Line 1",
                "machine_type": "Press",
                "capacity_hours_day": 10,
                "available_hours_today": 10,
                "current_status": "available",
            }
        ],
    )
    monkeypatch.setattr(
        main.db,
        "get_open_orders",
        lambda: [
            {"wo_id": "WO-1002", "required_machine": "M3", "processing_time_hr": 9, "status": "in_progress"},
            {"wo_id": "WO-1007", "required_machine": "M3", "processing_time_hr": 7, "status": "pending"},
            {"wo_id": "WO-1016", "required_machine": "M3", "processing_time_hr": 3, "status": "pending"},
        ],
    )

    response = TestClient(main.app).get("/api/v1/machines/load")

    assert response.status_code == 200
    assert response.json()[0]["load_status"] == "overloaded"


def test_at_risk_orders_returns_list(monkeypatch) -> None:
    monkeypatch.setattr(
        main.db,
        "get_orders_with_machine_state",
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
        "get_orders_for_simulation",
        lambda machine_id: [
            {
                "wo_id": "WO-1008",
                "product_code": "PART-F",
                "required_machine": machine_id,
                "processing_time_hr": 5.5,
                "priority": 2,
                "due_date": date(2026, 6, 30),
                "status": "pending",
            },
            {
                "wo_id": "WO-1009",
                "product_code": "PART-G",
                "required_machine": machine_id,
                "processing_time_hr": 6,
                "priority": 2,
                "due_date": date(2026, 6, 29),
                "status": "delayed",
            }
        ],
    )

    response = TestClient(main.app).post(
        "/api/v1/simulate/downtime",
        json={"machine_id": "M2", "downtime_hours": 4},
    )

    assert response.status_code == 200
    assert [row["wo_id"] for row in response.json()["affected_orders"]] == ["WO-1008", "WO-1009"]


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
            "trace": [{"step": "scope_check", "in_scope": True}],
        },
    )

    response = TestClient(main.app).post(
        "/api/v1/ask",
        json={"question": "Which machines are overloaded?"},
    )

    assert response.status_code == 200
    assert response.json()["tool_used"] == "check_load"
    assert response.json()["trace"][0]["step"] == "scope_check"
