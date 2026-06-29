"""PostgreSQL access helpers.

Owns database connection setup, safe read queries, and agent_action_log writes.
Callers should receive plain Python data structures, not raw cursor objects.
"""

import os

import psycopg

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/scheduling_db",
)


def check_db() -> bool:
    """Return whether PostgreSQL is reachable."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() == (1,)
    except psycopg.Error:
        return False
