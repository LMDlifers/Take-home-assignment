import psycopg
import pytest

from app import agent
from app import db


@pytest.fixture(autouse=True)
def no_audit_log(monkeypatch) -> None:
    monkeypatch.setattr(db, "log_agent_action", lambda **kwargs: None)


def fail_if_generate_sql_called(question: str) -> str:
    raise AssertionError("deterministic tool should not generate SQL with Qwen")


def capture_audit_logs(monkeypatch) -> list[dict]:
    logs = []

    def fake_log_agent_action(**kwargs) -> None:
        logs.append(kwargs)

    monkeypatch.setattr(db, "log_agent_action", fake_log_agent_action)
    return logs


def test_out_of_scope_question_is_refused() -> None:
    response = agent.answer_question("What is the capital of France?")

    assert response["tool_used"] == "refuse"
    assert response["data"] == []


def test_ask_uses_generated_safe_sql(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", lambda question: "SELECT wo_id FROM work_orders")
    monkeypatch.setattr(db, "fetch_all", lambda sql: [{"wo_id": "WO-1003"}])
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "WO-1003 is delayed.")

    response = agent.answer_question("Which work orders are delayed?")

    assert response["tool_used"] == "run_sql"
    assert response["sql_used"] == "SELECT wo_id FROM work_orders"
    assert response["answer"] == "WO-1003 is delayed."


def test_successful_run_sql_is_logged(monkeypatch) -> None:
    logs = capture_audit_logs(monkeypatch)
    monkeypatch.setattr(agent, "generate_sql", lambda question: "SELECT wo_id FROM work_orders")
    monkeypatch.setattr(db, "fetch_all", lambda sql: [{"wo_id": "WO-1003"}])
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "WO-1003 is delayed.")

    agent.answer_question("Which work orders are delayed?")

    assert logs[0]["action_type"] == "query_generated"
    assert logs[0]["input_question"] == "Which work orders are delayed?"
    assert logs[0]["sql_generated"] == "SELECT wo_id FROM work_orders"
    assert logs[0]["confidence"] == 0.75
    assert logs[0]["result_summary"] == "Found 1 work orders: WO-1003."


def test_check_load_tool_uses_deterministic_query(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "M3 is overloaded.")
    monkeypatch.setattr(
        db,
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

    response = agent.answer_question("Which machines are overloaded?")

    assert response["tool_used"] == "check_load"
    assert "v_machine_load" in response["sql_used"]
    assert response["data"][0]["load_status"] == "overloaded"


def test_check_load_is_logged_as_query_generated(monkeypatch) -> None:
    logs = capture_audit_logs(monkeypatch)
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "M3 is overloaded.")
    monkeypatch.setattr(
        db,
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

    agent.answer_question("Which machines are overloaded?")

    assert logs[0]["action_type"] == "query_generated"
    assert logs[0]["result_summary"] == "Found 1 machines: M3."


def test_get_priority_tool_uses_priority_view(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "High-priority orders are due soon.")
    monkeypatch.setattr(
        db,
        "get_priority_queue",
        lambda: [{"wo_id": "WO-1001", "priority": 1, "days_remaining": 1}],
    )

    response = agent.answer_question("Show high-priority orders due this week.")

    assert response["tool_used"] == "get_priority"
    assert "v_priority_queue" in response["sql_used"]
    assert response["data"][0]["wo_id"] == "WO-1001"


def test_simulate_downtime_tool_parses_machine_and_hours(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "WO-1008 is affected.")
    monkeypatch.setattr(
        db,
        "get_machine",
        lambda machine_id: {"machine_id": machine_id, "available_hours_today": 4.5},
    )
    monkeypatch.setattr(
        db,
        "get_active_orders_for_machine",
        lambda machine_id: [
            {
                "wo_id": "WO-1008",
                "product_code": "PART-F",
                "required_machine": machine_id,
                "processing_time_hr": 5.5,
                "priority": 2,
                "status": "pending",
            }
        ],
    )

    response = agent.answer_question("What happens if M2 is down for 4 extra hours?")

    assert response["tool_used"] == "simulate_downtime"
    assert response["data"][0]["machine_id"] == "M2"
    assert response["data"][0]["affected_orders"][0]["wo_id"] == "WO-1008"


def test_simulate_downtime_is_logged_as_simulation(monkeypatch) -> None:
    logs = capture_audit_logs(monkeypatch)
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "WO-1008 is affected.")
    monkeypatch.setattr(
        db,
        "get_machine",
        lambda machine_id: {"machine_id": machine_id, "available_hours_today": 4.5},
    )
    monkeypatch.setattr(
        db,
        "get_active_orders_for_machine",
        lambda machine_id: [{"wo_id": "WO-1008", "processing_time_hr": 5.5}],
    )

    agent.answer_question("What happens if M2 is down for 4 extra hours?")

    assert logs[0]["action_type"] == "simulation"
    assert logs[0]["sql_generated"] is None
    assert logs[0]["result_summary"] == "Simulation affected 1 work orders: WO-1008."


def test_recommend_tool_uses_deterministic_rules(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "Escalate M4 and rebalance M5.")
    monkeypatch.setattr(
        db,
        "get_machine_loads",
        lambda: [
            {
                "machine_id": "M5",
                "machine_name": "Laser Cutter 500W",
                "machine_type": "Laser",
                "capacity_hours_day": 7.5,
                "available_hours_today": 7.5,
                "current_status": "available",
                "queued_hours": 8,
                "load_pct": 106,
            }
        ],
    )
    monkeypatch.setattr(
        db,
        "get_at_risk_orders",
        lambda: [
            {
                "wo_id": "WO-1003",
                "required_machine": "M4",
                "status": "delayed",
                "machine_status": "unavailable",
                "risk_reason": "Machine unavailable",
            }
        ],
    )
    monkeypatch.setattr(
        db,
        "get_active_orders_for_machine",
        lambda machine_id: [{"wo_id": "WO-1013", "priority": 4}],
    )

    response = agent.answer_question("Recommend actions to reduce delays.")

    assert response["tool_used"] == "recommend"
    assert response["data"][0]["action"] == "Escalate repair for M4"


def test_recommend_is_logged_as_recommendation(monkeypatch) -> None:
    logs = capture_audit_logs(monkeypatch)
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "Escalate M4.")
    monkeypatch.setattr(
        db,
        "get_machine_loads",
        lambda: [
            {
                "machine_id": "M5",
                "machine_name": "Laser Cutter 500W",
                "machine_type": "Laser",
                "capacity_hours_day": 7.5,
                "available_hours_today": 7.5,
                "current_status": "available",
                "queued_hours": 8,
                "load_pct": 106,
            }
        ],
    )
    monkeypatch.setattr(
        db,
        "get_at_risk_orders",
        lambda: [
            {
                "wo_id": "WO-1003",
                "required_machine": "M4",
                "status": "delayed",
                "machine_status": "unavailable",
                "risk_reason": "Machine unavailable",
            }
        ],
    )
    monkeypatch.setattr(db, "get_active_orders_for_machine", lambda machine_id: [])

    agent.answer_question("Recommend actions to reduce delays.")

    assert logs[0]["action_type"] == "recommendation"
    assert logs[0]["sql_generated"] is None
    assert logs[0]["result_summary"].startswith("Generated 2 recommendations:")


def test_unsafe_generated_sql_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", lambda question: "DELETE FROM work_orders")

    response = agent.answer_question("Which work orders are delayed?")

    assert response["data"] == []
    assert response["confidence"] == 0.2


def test_out_of_scope_refusal_is_logged_as_clarification(monkeypatch) -> None:
    logs = capture_audit_logs(monkeypatch)

    response = agent.answer_question("What is the capital of France?")

    assert response["tool_used"] == "refuse"
    assert logs[0]["action_type"] == "clarification"
    assert logs[0]["result_summary"] == "No records returned."


def test_logging_failure_does_not_break_response(monkeypatch) -> None:
    def broken_log_agent_action(**kwargs) -> None:
        raise psycopg.OperationalError("logging failed")

    monkeypatch.setattr(db, "log_agent_action", broken_log_agent_action)

    response = agent.answer_question("What is the capital of France?")

    assert response["tool_used"] == "refuse"


def test_sql_safety_rejects_multiple_statements() -> None:
    assert db.is_safe_select("SELECT * FROM work_orders") is True
    assert db.is_safe_select("SELECT * FROM work_orders; DROP TABLE work_orders") is False


def test_prompt_files_load() -> None:
    assert agent.schema_context()["schema"]["views"]["v_machine_load"]["purpose"]
    assert agent.sql_generation_config()["rules"]
    assert agent.explanation_config()["rules"]


def test_sql_prompt_includes_known_values() -> None:
    prompt = agent.sql_prompt()

    assert "v_machine_load" in prompt
    assert "v_at_risk_orders" in prompt
    assert "M1" in prompt
    assert "available" in prompt
    assert "partial" in prompt
    assert "unavailable" in prompt
    assert "Critical" in prompt
    assert "load_pct > 100" in prompt
