"""PostgreSQL access helpers.

Owns database connection setup, safe read queries, and agent_action_log writes.
Callers should receive plain Python data structures, not raw cursor objects.
"""

import re
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.config import settings

DATABASE_URL = settings.database_url
# SQL keywords blocked to enforce read-only execution safety
FORBIDDEN_SQL = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE")

# Query to get the capacity, queued load, and load percentage for all machines
MACHINE_LOAD_SQL = """
SELECT machine_id, machine_name, machine_type, capacity_hours_day,
       available_hours_today, current_status, queued_hours, load_pct
FROM v_machine_load
ORDER BY load_pct DESC NULLS LAST, machine_id ASC
"""

# Query to retrieve work orders that risk missing their due date or are blocked
AT_RISK_ORDERS_SQL = """
SELECT wo_id, product_code, quantity, required_machine,
       processing_time_hr, priority, due_date, status,
       available_hours_today, machine_status, risk_reason
FROM v_at_risk_orders
ORDER BY priority ASC, due_date ASC, wo_id ASC
"""

AT_RISK_ORDER_SQL = """
SELECT wo_id, product_code, quantity, required_machine,
       processing_time_hr, priority, due_date, status,
       available_hours_today, machine_status, risk_reason
FROM v_at_risk_orders
WHERE wo_id = %s
ORDER BY priority ASC, due_date ASC, wo_id ASC
"""

# Query to rank incomplete work orders due within the next 3 days
PRIORITY_QUEUE_SQL = """
SELECT wo_id, product_code, product_name, quantity, required_machine,
       processing_time_hr, priority, due_date, status, days_remaining
FROM v_priority_queue
ORDER BY priority ASC, due_date ASC, wo_id ASC
"""

WORK_ORDER_STATUS_SQL = """
SELECT wo.wo_id, wo.product_code, p.product_name, wo.quantity,
       wo.required_machine, m.machine_name, wo.processing_time_hr,
       wo.priority, wo.due_date, wo.status, m.current_status AS machine_status,
       m.available_hours_today, wo.notes
FROM work_orders wo
JOIN products p ON p.product_code = wo.product_code
JOIN machines m ON m.machine_id = wo.required_machine
WHERE wo.wo_id = %s
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
    if not cleaned:
        return False
    statement = cleaned[:-1].strip() if cleaned.endswith(";") else cleaned
    # Require one read-only SELECT statement.
    if not re.match(r"SELECT\b", statement, flags=re.IGNORECASE):
        return False
    if ";" in statement or "--" in statement or "/*" in statement or "*/" in statement:
        return False
    # Block dangerous SQL verbs only when they appear as standalone words.
    return not any(
        re.search(rf"\b{word}\b", statement, flags=re.IGNORECASE)
        for word in FORBIDDEN_SQL
    )


def get_at_risk_order(wo_id: str) -> list[dict[str, Any]]:
    """Return one at-risk work order from the seeded view."""
    return fetch_all(AT_RISK_ORDER_SQL, (wo_id,))


def work_order_exists(wo_id: str) -> bool:
    """Return whether one work order exists."""
    return bool(fetch_all("SELECT 1 AS found FROM work_orders WHERE wo_id = %s", (wo_id,)))


def machine_exists(machine_id: str) -> bool:
    """Return whether one machine exists."""
    return bool(fetch_all("SELECT 1 AS found FROM machines WHERE machine_id = %s", (machine_id,)))


def get_work_order_status(wo_id: str) -> list[dict[str, Any]]:
    """Return status and scheduling context for one work order."""
    return fetch_all(WORK_ORDER_STATUS_SQL, (wo_id,))


def get_machine_aliases() -> list[dict[str, str]]:
    """Return machine names used to resolve natural-language machine references."""
    return fetch_all(
        """
        SELECT machine_id, machine_name, machine_type
        FROM machines
        ORDER BY machine_id ASC
        """
    )


def get_machines() -> list[dict[str, Any]]:
    """Return machine rows used by app-owned scheduling rules."""
    return fetch_all(
        """
        SELECT machine_id, machine_name, machine_type, capacity_hours_day,
               available_hours_today, current_status
        FROM machines
        ORDER BY machine_id ASC
        """
    )


def get_open_orders() -> list[dict[str, Any]]:
    """Return pending and in-progress orders for app-owned load calculation."""
    return fetch_all(
        """
        SELECT wo_id, product_code, quantity, required_machine,
               processing_time_hr, priority, due_date, status
        FROM work_orders
        WHERE status IN ('pending', 'in_progress')
        ORDER BY priority ASC, due_date ASC, wo_id ASC
        """
    )


def get_orders_with_machine_state() -> list[dict[str, Any]]:
    """Return incomplete orders with machine availability for risk detection."""
    return fetch_all(
        """
        SELECT wo.wo_id, wo.product_code, wo.quantity, wo.required_machine,
               wo.processing_time_hr, wo.priority, wo.due_date, wo.status,
               m.available_hours_today, m.current_status AS machine_status
        FROM work_orders wo
        JOIN machines m ON m.machine_id = wo.required_machine
        WHERE wo.status NOT IN ('completed', 'on_hold')
        ORDER BY wo.priority ASC, wo.due_date ASC, wo.wo_id ASC
        """
    )


def get_priority_orders() -> list[dict[str, Any]]:
    """Return incomplete orders with product names for priority filtering."""
    return fetch_all(
        """
        SELECT wo.wo_id, wo.product_code, p.product_name, wo.quantity,
               wo.required_machine, wo.processing_time_hr, wo.priority,
               wo.due_date, wo.status, wo.due_date - CURRENT_DATE AS days_remaining
        FROM work_orders wo
        JOIN products p ON p.product_code = wo.product_code
        WHERE wo.status NOT IN ('completed', 'on_hold')
        ORDER BY wo.priority ASC, wo.due_date ASC, wo.wo_id ASC
        """
    )


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


def get_orders_for_simulation(machine_id: str) -> list[dict[str, Any]]:
    """Return active and delayed orders assigned to one machine."""
    return fetch_all(
        """
        SELECT wo_id, product_code, required_machine, processing_time_hr,
               priority, due_date, status
        FROM work_orders
        WHERE required_machine = %s
          AND status IN ('pending', 'in_progress', 'delayed')
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
