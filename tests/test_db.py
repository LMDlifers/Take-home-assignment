from app import db


def test_log_agent_action_inserts_audit_row(monkeypatch) -> None:
    calls = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            calls.append((sql, params))

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(db.psycopg, "connect", lambda database_url: FakeConnection())

    db.log_agent_action(
        session_id="00000000-0000-0000-0000-000000000001",
        action_type="query_generated",
        input_question="Which machines are overloaded?",
        sql_generated="SELECT * FROM v_machine_load",
        result_summary="Found 4 machines: M3, M6, M7, M5.",
        confidence=0.75,
    )

    sql, params = calls[0]

    assert "INSERT INTO agent_action_log" in sql
    assert params == (
        "00000000-0000-0000-0000-000000000001",
        "query_generated",
        "Which machines are overloaded?",
        "SELECT * FROM v_machine_load",
        "Found 4 machines: M3, M6, M7, M5.",
        0.75,
        None,
    )
