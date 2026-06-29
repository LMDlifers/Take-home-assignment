from app import agent
from app import db


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


def test_unsafe_generated_sql_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(agent, "generate_sql", lambda question: "DELETE FROM work_orders")

    response = agent.answer_question("Which work orders are delayed?")

    assert response["data"] == []
    assert response["confidence"] == 0.2


def test_sql_safety_rejects_multiple_statements() -> None:
    assert db.is_safe_select("SELECT * FROM work_orders") is True
    assert db.is_safe_select("SELECT * FROM work_orders; DROP TABLE work_orders") is False


def test_prompt_config_loads_yaml() -> None:
    config = agent.prompt_config()

    assert config["schema"]["views"]["v_machine_load"]["purpose"]


def test_sql_prompt_includes_schema_meanings() -> None:
    prompt = agent.sql_prompt()

    assert "v_machine_load" in prompt
    assert "v_at_risk_orders" in prompt
    assert "priority" in prompt
    assert "load_pct > 100" in prompt
