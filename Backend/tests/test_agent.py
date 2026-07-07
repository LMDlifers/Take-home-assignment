from datetime import date

import psycopg
import pytest

from app import agent
from app import db


@pytest.fixture(autouse=True)
def no_audit_log(monkeypatch) -> None:
    monkeypatch.setattr(db, "log_agent_action", lambda **kwargs: None)


def fail_if_generate_sql_called(question: str) -> tuple[str, tuple]:
    raise AssertionError("deterministic tool should not generate SQL with Qwen")


def capture_audit_logs(monkeypatch) -> list[dict]:
    logs = []

    def fake_log_agent_action(**kwargs) -> None:
        logs.append(kwargs)

    monkeypatch.setattr(db, "log_agent_action", fake_log_agent_action)
    return logs


def sample_machines() -> list[dict]:
    return [
        {"machine_id": "M3", "machine_name": "Hydraulic Press Line 1", "machine_type": "Press", "capacity_hours_day": 10, "available_hours_today": 10, "current_status": "available"},
        {"machine_id": "M6", "machine_name": "Vertical Milling Machine 1", "machine_type": "Mill", "capacity_hours_day": 6, "available_hours_today": 6, "current_status": "available"},
        {"machine_id": "M5", "machine_name": "Laser Cutter 500W", "machine_type": "Laser", "capacity_hours_day": 7.5, "available_hours_today": 7.5, "current_status": "available"},
        {"machine_id": "M4", "machine_name": "Hydraulic Press Line 2", "machine_type": "Press", "capacity_hours_day": 10, "available_hours_today": 0, "current_status": "unavailable"},
        {"machine_id": "M2", "machine_name": "CNC Machining Centre Beta", "machine_type": "CNC Mill", "capacity_hours_day": 8, "available_hours_today": 4.5, "current_status": "partial"},
    ]


def sample_open_orders() -> list[dict]:
    return [
        {"wo_id": "WO-1002", "required_machine": "M3", "processing_time_hr": 9, "priority": 1, "status": "in_progress"},
        {"wo_id": "WO-1007", "required_machine": "M3", "processing_time_hr": 7, "priority": 1, "status": "pending"},
        {"wo_id": "WO-1016", "required_machine": "M3", "processing_time_hr": 3, "priority": 2, "status": "pending"},
        {"wo_id": "WO-1011", "required_machine": "M6", "processing_time_hr": 3.5, "priority": 3, "status": "pending"},
        {"wo_id": "WO-1015", "required_machine": "M6", "processing_time_hr": 4, "priority": 1, "status": "pending"},
        {"wo_id": "WO-1006", "required_machine": "M5", "processing_time_hr": 3, "priority": 2, "status": "in_progress"},
        {"wo_id": "WO-1013", "required_machine": "M5", "processing_time_hr": 2, "priority": 4, "status": "pending"},
        {"wo_id": "WO-1019", "required_machine": "M5", "processing_time_hr": 3, "priority": 1, "status": "pending"},
    ]


def test_out_of_scope_question_is_refused() -> None:
    response = agent.answer_question("What is the capital of France?")

    assert response["tool_used"] == "refuse"
    assert response["data"] == []


def test_six_assessment_questions_route_to_expected_tools() -> None:
    assert agent.route_tool("Which work orders are delayed?") == "run_sql"
    assert agent.route_tool("Which machines are overloaded?") == "check_load"
    assert agent.route_tool("Why is WO-1003 at risk?") == "run_sql"
    assert agent.route_tool("What happens if M2 is down for 4 extra hours?") == "simulate_downtime"
    assert agent.route_tool("Show high-priority orders due this week.") == "get_priority"
    assert agent.route_tool("Recommend actions to reduce delays.") == "recommend"


def test_ask_uses_generated_safe_sql(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", lambda question: ("SELECT wo_id FROM work_orders", ()))
    monkeypatch.setattr(db, "fetch_all", lambda sql, params=(): [{"wo_id": "WO-1003"}])
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "WO-1003 is delayed.")

    response = agent.answer_question("Show work orders on M1")

    assert response["tool_used"] == "run_sql"
    assert response["sql_used"] == "SELECT wo_id FROM work_orders"
    assert response["answer"] == "WO-1003 is delayed."


def test_successful_run_sql_is_logged(monkeypatch) -> None:
    logs = capture_audit_logs(monkeypatch)
    monkeypatch.setattr(agent, "generate_sql", lambda question: ("SELECT wo_id FROM work_orders", ()))
    monkeypatch.setattr(db, "fetch_all", lambda sql, params=(): [{"wo_id": "WO-1003"}])
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "WO-1003 is delayed.")

    agent.answer_question("Show work orders on M1")

    assert logs[0]["action_type"] == "query_generated"
    assert logs[0]["input_question"] == "Show work orders on M1"
    assert logs[0]["sql_generated"] == "SELECT wo_id FROM work_orders"
    assert logs[0]["confidence"] == 0.75
    assert logs[0]["result_summary"] == "Found 1 work orders: WO-1003."


def test_known_delayed_question_uses_deterministic_sql(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(db, "fetch_all", lambda sql, params=(): [{"wo_id": "WO-1003"}])
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "WO-1003 is delayed.")

    response = agent.answer_question("Which work orders are delayed?")

    assert response["tool_used"] == "run_sql"
    assert "WHERE status = 'delayed'" in response["sql_used"]
    assert response["data"][0]["wo_id"] == "WO-1003"


def test_known_wo_1003_question_uses_deterministic_sql(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(db, "fetch_all", lambda sql, params=(): [{"wo_id": params[0]}])
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "WO-1003 is blocked on M4.")

    response = agent.answer_question("Why is WO-1003 at risk?")

    assert response["tool_used"] == "run_sql"
    assert "FROM v_at_risk_orders" in response["sql_used"]
    assert response["data"][0]["wo_id"] == "WO-1003"


def test_check_load_tool_uses_deterministic_query(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "M3 is overloaded.")
    monkeypatch.setattr(db, "get_machines", sample_machines)
    monkeypatch.setattr(db, "get_open_orders", sample_open_orders)

    response = agent.answer_question("Which machines are overloaded?")

    assert response["tool_used"] == "check_load"
    assert "v_machine_load" in response["sql_used"]
    assert [row["machine_id"] for row in response["data"]] == ["M3", "M6", "M5"]


def test_check_load_is_logged_as_query_generated(monkeypatch) -> None:
    logs = capture_audit_logs(monkeypatch)
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "M3 is overloaded.")
    monkeypatch.setattr(db, "get_machines", lambda: [sample_machines()[0]])
    monkeypatch.setattr(db, "get_open_orders", lambda: sample_open_orders()[:3])

    agent.answer_question("Which machines are overloaded?")

    assert logs[0]["action_type"] == "query_generated"
    assert logs[0]["result_summary"] == "Found 1 machines: M3."


def test_get_priority_tool_uses_priority_view(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "High-priority orders are due soon.")
    monkeypatch.setattr(
        db,
        "get_priority_orders",
        lambda: [{"wo_id": "WO-1001", "priority": 1, "days_remaining": 1}],
    )

    response = agent.answer_question("Show high-priority orders due this week.")

    assert response["tool_used"] == "get_priority"
    assert "v_priority_queue" in response["sql_used"]
    assert response["data"][0]["wo_id"] == "WO-1001"


def test_get_priority_tool_returns_expected_p1_due_this_week(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "P1 orders are due soon.")
    monkeypatch.setattr(
        db,
        "get_priority_orders",
        lambda: [
            {"wo_id": "WO-1001", "priority": 1, "days_remaining": 1},
            {"wo_id": "WO-1002", "priority": 1, "days_remaining": 0},
            {"wo_id": "WO-1003", "priority": 1, "days_remaining": 0},
            {"wo_id": "WO-1005", "priority": 1, "days_remaining": -1},
            {"wo_id": "WO-1007", "priority": 1, "days_remaining": 1},
            {"wo_id": "WO-1015", "priority": 1, "days_remaining": 1},
            {"wo_id": "WO-1019", "priority": 1, "days_remaining": 2},
            {"wo_id": "WO-1008", "priority": 2, "days_remaining": 1},
        ],
    )

    response = agent.answer_question("Show high-priority orders due this week.")

    assert [row["wo_id"] for row in response["data"]] == [
        "WO-1001",
        "WO-1002",
        "WO-1003",
        "WO-1005",
        "WO-1007",
        "WO-1015",
    ]


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
        "get_orders_for_simulation",
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
        "get_orders_for_simulation",
        lambda machine_id: [{"wo_id": "WO-1008", "processing_time_hr": 5.5}],
    )

    agent.answer_question("What happens if M2 is down for 4 extra hours?")

    assert logs[0]["action_type"] == "simulation"
    assert logs[0]["sql_generated"] is None
    assert logs[0]["result_summary"] == "Simulation affected 1 work orders: WO-1008."


def test_recommend_tool_uses_deterministic_rules(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "Escalate M4 and rebalance M5.")
    monkeypatch.setattr(db, "get_machines", sample_machines)
    monkeypatch.setattr(db, "get_open_orders", sample_open_orders)
    monkeypatch.setattr(
        db,
        "get_orders_with_machine_state",
        lambda: [
            {
                "wo_id": "WO-1003",
                "product_code": "PART-C",
                "quantity": 60,
                "required_machine": "M4",
                "processing_time_hr": 5,
                "priority": 1,
                "due_date": date(2026, 6, 30),
                "status": "delayed",
                "available_hours_today": 0,
                "machine_status": "unavailable",
            }
        ],
    )
    monkeypatch.setattr(
        db,
        "get_orders_for_simulation",
        lambda machine_id: [{"wo_id": "WO-1013", "priority": 4}],
    )

    response = agent.answer_question("Recommend actions to reduce delays.")

    assert response["tool_used"] == "recommend"
    assert response["data"][0]["action"] == "Escalate repair for M4"


def test_recommend_is_logged_as_recommendation(monkeypatch) -> None:
    logs = capture_audit_logs(monkeypatch)
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(agent, "explain_result", lambda question, data: "Escalate M4.")
    monkeypatch.setattr(db, "get_machines", lambda: [sample_machines()[2]])
    monkeypatch.setattr(db, "get_open_orders", lambda: sample_open_orders()[5:])
    monkeypatch.setattr(
        db,
        "get_orders_with_machine_state",
        lambda: [
            {
                "wo_id": "WO-1003",
                "product_code": "PART-C",
                "quantity": 60,
                "required_machine": "M4",
                "processing_time_hr": 5,
                "priority": 1,
                "due_date": date(2026, 6, 30),
                "status": "delayed",
                "available_hours_today": 0,
                "machine_status": "unavailable",
            }
        ],
    )
    monkeypatch.setattr(db, "get_orders_for_simulation", lambda machine_id: [])

    agent.answer_question("Recommend actions to reduce delays.")

    assert logs[0]["action_type"] == "recommendation"
    assert logs[0]["sql_generated"] is None
    assert logs[0]["result_summary"].startswith("Generated 1 recommendations:")


def test_unsafe_generated_sql_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", lambda question: ("DELETE FROM work_orders", ()))

    response = agent.answer_question("Show work orders on M1")

    assert response["data"] == []
    assert response["confidence"] == 0.2


def test_bad_generated_sql_execution_is_rejected(monkeypatch) -> None:
    def broken_fetch_all(sql, params=()):
        raise psycopg.ProgrammingError("bad generated SQL")

    monkeypatch.setattr(agent, "generate_sql", lambda question: ("SELECT missing_column FROM work_orders", ()))
    monkeypatch.setattr(db, "fetch_all", broken_fetch_all)

    response = agent.answer_question("Show work orders on M1")

    assert response["data"] == []
    assert response["confidence"] == 0.2


def test_placeholder_mismatch_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", lambda question: ("SELECT * FROM work_orders WHERE wo_id = %s AND required_machine = %s", ("WO-1003",)))

    response = agent.answer_question("Show work orders on M1")

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
    assert db.is_safe_select("SELECT * FROM work_orders -- hidden") is False
    assert db.is_safe_select("SELECT * FROM work_orders /* hidden */") is False
    assert db.is_safe_select("") is False


def test_generate_sql_binds_work_order_literals(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "ollama_chat",
        lambda messages: "SELECT wo_id FROM work_orders WHERE wo_id = 'WO-1003'",
    )

    sql, params = agent.generate_sql("Why is WO-1003 at risk?")

    assert sql == "SELECT wo_id FROM work_orders WHERE wo_id = %s"
    assert params == ("WO-1003",)


def test_generate_sql_converts_dollar_placeholder(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "ollama_chat",
        lambda messages: "SELECT wo_id FROM work_orders WHERE wo_id = $1",
    )

    sql, params = agent.generate_sql("Why is WO-1003 at risk?")

    assert sql == "SELECT wo_id FROM work_orders WHERE wo_id = %s"
    assert params == ("WO-1003",)


def test_generate_sql_binds_existing_percent_placeholder(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "ollama_chat",
        lambda messages: "SELECT wo_id FROM work_orders WHERE wo_id = %s",
    )

    sql, params = agent.generate_sql("Why is WO-1003 at risk?")

    assert sql == "SELECT wo_id FROM work_orders WHERE wo_id = %s"
    assert params == ("WO-1003",)


def test_prompt_files_load() -> None:
    assert agent.schema_context()["schema"]["views"]["v_machine_load"]["purpose"]
    assert agent.sql_generation_config()["rules"]
    assert agent.explanation_config()["rules"]


def test_sql_prompt_includes_known_values() -> None:
    prompt = agent.sql_prompt()
    views = agent.schema_context()["schema"]["views"]

    assert "v_machine_load" in prompt
    assert "v_at_risk_orders" in prompt
    assert "wo_id" in views["v_at_risk_orders"]["columns"]
    assert "machine_status" in views["v_at_risk_orders"]["columns"]
    assert "risk_reason" in views["v_at_risk_orders"]["columns"]
    assert "days_remaining" in views["v_priority_queue"]["columns"]
    assert any("Never invent columns" in rule for rule in agent.sql_generation_config()["rules"])
    assert "M1" in prompt
    assert "available" in prompt
    assert "partial" in prompt
    assert "unavailable" in prompt
    assert "Critical" in prompt
    assert "load_pct > 100" in prompt
