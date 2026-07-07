from datetime import date

import psycopg
import pytest

from app import agent
from app import db


def fake_qwen_response(messages, **kwargs):
    prompt = messages[0]["content"]
    schema = kwargs.get("format_schema")
    if schema == agent.SQLResponse.model_json_schema():
        return '{"query":"SELECT wo_id FROM work_orders","params":[]}'
    if "Scheduling classify-route-plan assistant" in prompt:
        lower_prompt = prompt.lower()
        if "question: what happens if machine beta" in lower_prompt:
            return '{"in_scope":true,"intent":"simulate_downtime","tool":"simulate_downtime","work_order_ids":[],"machine_ids":["M2"],"downtime_hours":4,"priority_max":null,"due_window_days":null,"reason":"Downtime simulation."}'
        if "question: what is the capital of france" in lower_prompt:
            return '{"in_scope":false,"intent":"unknown","tool":"refuse","work_order_ids":[],"machine_ids":[],"downtime_hours":null,"priority_max":null,"due_window_days":null,"reason":"Outside scope."}'
        return '{"in_scope":true,"intent":"unknown","tool":null,"work_order_ids":[],"machine_ids":[],"downtime_hours":null,"priority_max":null,"due_window_days":null,"reason":"Scheduling question."}'
    if "Factory planning explanation writer" in prompt:
        return "The returned scheduling evidence supports this answer and highlights the relevant machines or work orders."
    raise AssertionError("unexpected Qwen call")


@pytest.fixture(autouse=True)
def no_audit_log(monkeypatch) -> None:
    monkeypatch.setattr(db, "log_agent_action", lambda **kwargs: None)
    monkeypatch.setattr(db, "work_order_exists", lambda wo_id: True)
    monkeypatch.setattr(db, "machine_exists", lambda machine_id: True)
    monkeypatch.setattr(db, "get_machine_aliases", lambda: [])

    def fake_ollama_chat(messages, **kwargs):
        if kwargs.get("model") in {
            agent.LLM_SQL_MODEL,
            agent.LLM_EXPLANATION_MODEL,
        }:
            return fake_qwen_response(messages, **kwargs)
        raise AssertionError("unexpected Ollama call")

    monkeypatch.setattr(agent, "ollama_chat", fake_ollama_chat)


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


def test_ask_uses_generated_safe_sql(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", lambda question: ("SELECT wo_id FROM work_orders", ()))
    monkeypatch.setattr(db, "fetch_all", lambda sql, params=(): [{"wo_id": "WO-1003"}])

    response = agent.answer_question("Show all work orders")

    assert response["tool_used"] == "run_sql"
    assert response["sql_used"] == "SELECT wo_id FROM work_orders"
    assert response["answer"] == "Work orders returned: WO-1003."


def test_successful_run_sql_is_logged(monkeypatch) -> None:
    logs = capture_audit_logs(monkeypatch)
    monkeypatch.setattr(agent, "generate_sql", lambda question: ("SELECT wo_id FROM work_orders", ()))
    monkeypatch.setattr(db, "fetch_all", lambda sql, params=(): [{"wo_id": "WO-1003"}])

    agent.answer_question("Show all work orders")

    assert logs[0]["action_type"] == "query_generated"
    assert logs[0]["input_question"] == "Show all work orders"
    assert logs[0]["sql_generated"] == "SELECT wo_id FROM work_orders"
    assert logs[0]["confidence"] == 0.75
    assert logs[0]["result_summary"] == "Found 1 work orders: WO-1003."


def test_check_load_tool_uses_deterministic_query(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(db, "get_machines", sample_machines)
    monkeypatch.setattr(db, "get_open_orders", sample_open_orders)

    response = agent.answer_question("Which machines are overloaded?")

    assert response["tool_used"] == "check_load"
    assert "v_machine_load" in response["sql_used"]
    assert [row["machine_id"] for row in response["data"]] == ["M3", "M6", "M5"]
    assert response["answer"] != response["explanation"]
    assert response["explanation"].startswith("The returned scheduling evidence")


def test_check_load_is_logged_as_query_generated(monkeypatch) -> None:
    logs = capture_audit_logs(monkeypatch)
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(db, "get_machines", lambda: [sample_machines()[0]])
    monkeypatch.setattr(db, "get_open_orders", lambda: sample_open_orders()[:3])

    agent.answer_question("Which machines are overloaded?")

    assert logs[0]["action_type"] == "query_generated"
    assert logs[0]["result_summary"] == "Found 1 machines: M3."


def test_get_priority_tool_uses_priority_view(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(
        db,
        "get_priority_orders",
        lambda: [{"wo_id": "WO-1001", "priority": 1, "days_remaining": 1}],
    )

    response = agent.answer_question("Show high-priority orders due this week.")

    assert response["tool_used"] == "get_priority"
    assert "v_priority_queue" in response["sql_used"]
    assert response["data"][0]["wo_id"] == "WO-1001"


def test_get_priority_tool_returns_p1_and_p2_due_this_week(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
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
        "WO-1005",
        "WO-1002",
        "WO-1003",
        "WO-1001",
        "WO-1007",
        "WO-1015",
        "WO-1019",
        "WO-1008",
    ]
    assert "WO-1008" in response["answer"]


def test_simulate_downtime_tool_parses_machine_and_hours(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
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


def test_work_order_risk_question_uses_generated_sql(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "generate_sql",
        lambda question: (
            "SELECT wo_id, required_machine, machine_status, risk_reason FROM v_at_risk_orders WHERE wo_id = %s",
            ("WO-1003",),
        ),
    )
    monkeypatch.setattr(
        db,
        "fetch_all",
        lambda sql, params=(): [
            {
                "wo_id": params[0],
                "required_machine": "M4",
                "machine_status": "unavailable",
                "risk_reason": "Machine M4 is unavailable",
            }
        ],
    )

    response = agent.answer_question("Why is WO-1003 at risk?")

    assert response["tool_used"] == "run_sql"
    assert "v_at_risk_orders" in response["sql_used"]
    assert response["data"][0]["wo_id"] == "WO-1003"
    assert response["answer"] == "WO-1003 is at risk: Machine M4 is unavailable."


def test_shorthand_work_order_status_uses_generated_sql(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "generate_sql",
        lambda question: ("SELECT wo_id, status, required_machine FROM work_orders WHERE wo_id = %s", ("WO-1003",)),
    )
    monkeypatch.setattr(
        db,
        "fetch_all",
        lambda sql, params=(): [
            {
                "wo_id": params[0],
                "status": "delayed",
                "required_machine": "M4",
                "machine_status": "unavailable",
            }
        ],
    )

    response = agent.answer_question("what is 1003 current status?")

    assert response["tool_used"] == "run_sql"
    assert response["data"][0]["wo_id"] == "WO-1003"
    assert response["answer"] == "WO-1003 is delayed, requires M4."
    assert response["trace"][0]["step"] == "planner"
    assert response["trace"][0]["result"]["work_order_ids"] == ["WO-1003"]
    assert response["trace"][2]["step"] == "route"
    assert response["trace"][2]["intent"] == "work_order_status"


def test_missing_work_order_fails_before_sql(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(db, "work_order_exists", lambda wo_id: False)

    response = agent.answer_question("is WO-9999 delayed?")

    assert response["tool_used"] == "refuse"
    assert response["answer"] == "Work order WO-9999 was not found."
    assert response["trace"][1]["status"] == "failed"
    assert response["trace"][2]["step"] == "confidence"
    assert response["trace"][2]["status"] == "skipped"


def test_slm_can_extract_machine_alias_for_downtime(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(db, "get_machine_aliases", lambda: [{"machine_id": "M2", "machine_name": "CNC Machining Centre Beta", "machine_type": "CNC Mill"}])
    monkeypatch.setattr(
        agent,
        "ollama_chat",
        lambda messages, **kwargs: '{"intent":"simulate_downtime","work_order_ids":[],"machine_ids":["M2"],"downtime_hours":4,"priority_max":null,"due_window_days":null}',
    )
    monkeypatch.setattr(db, "get_machine", lambda machine_id: {"machine_id": machine_id, "available_hours_today": 4.5})
    monkeypatch.setattr(
        db,
        "get_orders_for_simulation",
        lambda machine_id: [{"wo_id": "WO-1008", "processing_time_hr": 5.5, "status": "pending"}],
    )

    response = agent.answer_question("what happens if machine beta is down 4 more hours?")

    assert response["tool_used"] == "simulate_downtime"
    assert response["data"][0]["machine_id"] == "M2"
    assert response["data"][0]["affected_orders"][0]["wo_id"] == "WO-1008"


def test_unsafe_generated_sql_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", lambda question: ("DELETE FROM work_orders", ()))

    response = agent.answer_question("Show all work orders")

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


def test_generate_sql_uses_structured_output(monkeypatch) -> None:
    calls = []

    def fake_ollama_chat(messages, **kwargs):
        calls.append(kwargs)
        return '{"query":"SELECT wo_id FROM work_orders WHERE wo_id = %s","params":["WO-1003"]}'

    monkeypatch.setattr(agent, "ollama_chat", fake_ollama_chat)

    sql, params = agent.generate_sql("Why is WO-1003 at risk?")

    assert sql == "SELECT wo_id FROM work_orders WHERE wo_id = %s"
    assert params == ("WO-1003",)
    assert calls[0]["model"] == agent.LLM_SQL_MODEL
    assert calls[0]["format_schema"] == agent.SQLResponse.model_json_schema()
    assert calls[0]["options"] == {"temperature": 0, "num_predict": 200}


def test_route_question_uses_router_model(monkeypatch) -> None:
    calls = []

    def fake_ollama_chat(messages, **kwargs):
        calls.append(kwargs)
        return '{"in_scope":true,"intent":"work_order_status","tool":"run_sql","work_order_ids":["WO-1003"],"machine_ids":[],"downtime_hours":null,"priority_max":null,"due_window_days":null,"reason":"1003 is shorthand for WO-1003, and the user asks for status."}'

    monkeypatch.setattr(agent, "ollama_chat", fake_ollama_chat)

    routed = agent.route_question_with_slm("what is 1003 current status?")

    assert routed.extracted.work_order_ids == ["WO-1003"]
    assert routed.reasoning.startswith("1003 is shorthand")
    assert calls[0]["model"] == agent.LLM_ROUTER_MODEL
    assert "format_schema" not in calls[0]
    assert calls[0]["options"] == {"temperature": 0, "num_predict": 160}


def test_parse_router_response_splits_think_and_json() -> None:
    routed = agent.parse_router_response(
        '<think>Use the machine-load route.</think>{"intent":"machine_load","work_order_ids":[],"machine_ids":[],"downtime_hours":null,"priority_max":null,"due_window_days":null}'
    )

    assert routed.reasoning == "Use the machine-load route."
    assert routed.extracted.intent == "machine_load"


def test_parse_router_response_tolerates_missing_think() -> None:
    routed = agent.parse_router_response(
        '{"intent":"work_order_status","work_order_ids":["1003"],"machine_ids":[],"downtime_hours":null,"priority_max":null,"due_window_days":null}'
    )

    assert routed.reasoning == agent.ROUTER_FALLBACK_REASONING
    assert routed.extracted.work_order_ids == ["WO-1003"]


def test_parse_router_response_tolerates_empty_think() -> None:
    routed = agent.parse_router_response(
        '<think></think>{"intent":"machine_load","work_order_ids":[],"machine_ids":[],"downtime_hours":null,"priority_max":null,"due_window_days":null}'
    )

    assert routed.reasoning == agent.ROUTER_FALLBACK_REASONING
    assert routed.extracted.intent == "machine_load"


def test_malformed_router_response_falls_back_to_deterministic(monkeypatch) -> None:
    def broken_router(question):
        raise ValueError("bad json")

    monkeypatch.setattr(agent, "route_question_with_slm", broken_router)

    routed = agent.extract_question("Which machines are overloaded?")

    assert routed.extracted.intent == "machine_load"
    assert routed.reasoning == agent.ROUTER_FALLBACK_REASONING


def test_router_machine_ids_do_not_pollute_general_load_questions() -> None:
    merged = agent.merge_extractions(
        agent.ExtractedQuestion(intent="machine_load"),
        agent.ExtractedQuestion(intent="machine_load", machine_ids=["M1", "M2"]),
    )

    assert merged.machine_ids == []


def test_generate_sql_rejects_markdown_fenced_output(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "ollama_chat",
        lambda messages, **kwargs: "```json\n{\"query\":\"SELECT wo_id FROM work_orders\",\"params\":[]}\n```",
    )

    with pytest.raises(agent.ValidationError):
        agent.generate_sql("Which work orders are delayed?")


def test_generate_sql_rejects_dollar_placeholder(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "ollama_chat",
        lambda messages, **kwargs: '{"query":"SELECT wo_id FROM work_orders WHERE wo_id = $1","params":["WO-1003"]}',
    )

    with pytest.raises(ValueError, match="not \\$1"):
        agent.generate_sql("Why is WO-1003 at risk?")


def test_generate_sql_rejects_placeholder_param_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "ollama_chat",
        lambda messages, **kwargs: '{"query":"SELECT wo_id FROM work_orders WHERE wo_id = %s","params":[]}',
    )

    with pytest.raises(ValueError, match="placeholder count"):
        agent.generate_sql("Why is WO-1003 at risk?")


def test_generate_sql_rejects_quoted_placeholder(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "ollama_chat",
        lambda messages, **kwargs: '{"query":"SELECT wo_id FROM work_orders WHERE wo_id = \'%s\'","params":["WO-1003"]}',
    )

    with pytest.raises(ValueError, match="Do not quote"):
        agent.generate_sql("Why is WO-1003 at risk?")


def test_db_error_returns_safe_run_sql_response(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "generate_sql",
        lambda question: ("SELECT wo_id FROM work_orders WHERE wo_id = %s", ("WO-1003",)),
    )

    def broken_fetch_all(sql, params=()):
        raise psycopg.ProgrammingError("bad generated SQL")

    monkeypatch.setattr(db, "fetch_all", broken_fetch_all)

    response = agent.answer_question("Show all work orders")

    assert response["tool_used"] == "run_sql"
    assert response["sql_used"] == "SELECT wo_id FROM work_orders WHERE wo_id = %s"
    assert response["data"] == []
    assert response["confidence"] == 0.2


def test_prompt_files_load() -> None:
    assert agent.schema_context()["schema"]["views"]["v_machine_load"]["purpose"]
    assert agent.sql_generation_config()["rules"]
    assert agent.router_config()["rules"]
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


def test_router_prompt_includes_aliases_and_examples() -> None:
    prompt = agent.router_prompt(
        "what happens if machine beta is down 4 more hours?",
        [{"machine_id": "M2", "machine_name": "CNC Machining Centre Beta"}],
    )

    assert "Scheduling classify-route-plan assistant" in prompt
    assert "reason" in prompt
    assert "CNC Machining Centre Beta" in prompt
    assert "simulate_downtime" in prompt


def test_apply_confidence_uses_source_score() -> None:
    trace = []
    response = {
        "question": "Which machines are overloaded?",
        "tool_used": "check_load",
        "sql_used": None,
        "data": [{"machine_id": "M3"}],
        "answer": "M3 is overloaded.",
        "confidence": 0.75,
        "_confidence_source": "deterministic",
    }

    agent.apply_confidence(response, trace)

    assert response["confidence"] == 0.92
    assert trace[0] == {"step": "confidence", "status": "ok", "source": "deterministic", "score": 0.92}


def test_apply_confidence_keeps_fallback_score() -> None:
    trace = []
    response = {
        "question": "Show all work orders",
        "tool_used": "run_sql",
        "sql_used": None,
        "data": [],
        "answer": "I could not generate a safe read-only query for that question.",
        "confidence": 0.2,
        "_confidence_source": "fallback",
    }

    agent.apply_confidence(response, trace)

    assert response["confidence"] == 0.2
    assert trace[0]["status"] == "fallback"


def test_answer_question_includes_confidence_trace(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", fail_if_generate_sql_called)
    monkeypatch.setattr(db, "get_machines", sample_machines)
    monkeypatch.setattr(db, "get_open_orders", sample_open_orders)

    response = agent.answer_question("Which machines are overloaded?")

    confidence_steps = [step for step in response["trace"] if step["step"] == "confidence"]
    assert confidence_steps
    assert confidence_steps[0]["status"] == "ok"
    assert confidence_steps[0]["source"] == "deterministic"
