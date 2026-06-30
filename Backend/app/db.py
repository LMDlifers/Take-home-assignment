"""PostgreSQL access helpers.

Owns database connection setup, safe read queries, and agent_action_log writes.
Callers should receive plain Python data structures, not raw cursor objects.
"""

import os
from typing import Any

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/scheduling_db",
)
FORBIDDEN_SQL = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE")
MACHINE_LOAD_SQL = """
SELECT machine_id, machine_name, machine_type, capacity_hours_day,
       available_hours_today, current_status, queued_hours, load_pct
FROM v_machine_load
ORDER BY load_pct DESC NULLS LAST, machine_id ASC
"""
AT_RISK_ORDERS_SQL = """
SELECT wo_id, product_code, quantity, required_machine,
       processing_time_hr, priority, due_date, status,
       available_hours_today, machine_status, risk_reason
FROM v_at_risk_orders
ORDER BY priority ASC, due_date ASC, wo_id ASC
"""
PRIORITY_QUEUE_SQL = """
SELECT wo_id, product_code, product_name, quantity, required_machine,
       processing_time_hr, priority, due_date, status, days_remaining
FROM v_priority_queue
ORDER BY priority ASC, due_date ASC, wo_id ASC
"""


def check_db() -> bool:
    """Return whether PostgreSQL is reachable."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() == (1,)
    except psycopg.Error:
        return False


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Run a read-only query and return rows as dictionaries."""
    if not is_safe_select(sql):
        raise ValueError("Only safe SELECT queries are allowed")

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def is_safe_select(sql: str) -> bool:
    """Return whether SQL is a single read-only SELECT statement."""
    cleaned = sql.strip()
    upper = cleaned.upper()
    if not upper.startswith("SELECT"):
        return False
    if ";" in cleaned.rstrip(";"):
        return False
    return not any(word in upper.split() for word in FORBIDDEN_SQL)


def get_machine_loads() -> list[dict[str, Any]]:
    """Return all machine load rows from the seeded view."""
    return fetch_all(MACHINE_LOAD_SQL)


def get_at_risk_orders() -> list[dict[str, Any]]:
    """Return at-risk work orders from the seeded view."""
    return fetch_all(AT_RISK_ORDERS_SQL)


def get_priority_queue() -> list[dict[str, Any]]:
    """Return due-soon work orders from the seeded priority view."""
    return fetch_all(PRIORITY_QUEUE_SQL)


def get_machine(machine_id: str) -> dict[str, Any] | None:
    """Return one machine row, or None when the machine does not exist."""
    rows = fetch_all(
        """
        SELECT machine_id, machine_name, capacity_hours_day,
               available_hours_today, current_status
        FROM machines
        WHERE machine_id = %s
        """,
        (machine_id,),
    )
    return rows[0] if rows else None


def get_active_orders_for_machine(machine_id: str) -> list[dict[str, Any]]:
    """Return pending or in-progress orders assigned to one machine."""
    return fetch_all(
        """
        SELECT wo_id, product_code, required_machine, processing_time_hr,
               priority, due_date, status
        FROM work_orders
        WHERE required_machine = %s
          AND status IN ('pending', 'in_progress')
        ORDER BY priority ASC, due_date ASC, wo_id ASC
        """,
        (machine_id,),
    )


def log_agent_action(
    session_id: str,
    action_type: str,
    input_question: str,
    sql_generated: str | None,
    result_summary: str,
    confidence: float,
    tokens_used: int | None = None,
) -> None:
    """Write one agent audit row."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_action_log
                    (session_id, action_type, input_question, sql_generated,
                     result_summary, confidence, tokens_used)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    action_type,
                    input_question,
                    sql_generated,
                    result_summary,
                    confidence,
                    tokens_used,
                ),
            )
