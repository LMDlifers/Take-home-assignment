"""Agent orchestration for natural-language planning questions.

Classifies whether a question is in scope, routes it to the right tool, calls
deterministic data/business functions, asks the LLM to explain retrieved facts,
and logs each action for auditability.
"""

import json
import os
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import psycopg
import yaml

from app import db
from app import logic

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:3b")

SCHEDULING_TERMS = {
    "machine",
    "machines",
    "work order",
    "work orders",
    "wo-",
    "delayed",
    "delay",
    "risk",
    "overloaded",
    "load",
    "capacity",
    "priority",
    "due",
    "schedule",
    "downtime",
}


def answer_question(question: str) -> dict[str, Any]:
    """Answer one natural-language planning question."""
    session_id = str(uuid.uuid4())

    if not is_planning_question(question):
        response = refuse_response(question)
        safe_log_action(session_id, response)
        return response

    tool = route_tool(question)
    if tool == "check_load":
        response = check_load_tool(question)
    elif tool == "get_priority":
        response = get_priority_tool(question)
    elif tool == "simulate_downtime":
        response = simulate_downtime_tool(question)
    elif tool == "recommend":
        response = recommend_tool(question)
    else:
        response = run_sql_tool(question, tool)

    safe_log_action(session_id, response)
    return response


def run_sql_tool(question: str, tool: str = "run_sql") -> dict[str, Any]:
    """Generate a safe SELECT query, execute it, and explain the result."""
    try:
        generated = generate_sql(question)
    except httpx.HTTPError:
        return llm_unavailable_response(question, tool)

    sql, params = generated if isinstance(generated, tuple) else (generated, ())
    if not db.is_safe_select(sql):
        return {
            "question": question,
            "tool_used": tool,
            "sql_used": sql,
            "data": [],
            "answer": "I could not generate a safe read-only query for that question.",
            "explanation": "The generated query was rejected before execution.",
            "confidence": 0.2,
            "follow_ups": ["Which work orders are delayed?", "Show high-priority orders due this week."],
        }

    data = db.fetch_all(sql, params)
    return answer_from_data(question, tool, sql, data)


def check_load_tool(question: str) -> dict[str, Any]:
    """Answer load questions with deterministic Python load calculation."""
    data = logic.calculate_machine_loads(db.get_machines(), db.get_open_orders())
    if "overloaded" in question.lower():
        data = [row for row in data if row["load_status"] == "overloaded"]
    return answer_from_data(question, "check_load", db.MACHINE_LOAD_SQL.strip(), data)


def get_priority_tool(question: str) -> dict[str, Any]:
    """Answer priority and due-soon questions with deterministic filtering."""
    q = question.lower()
    max_priority = 2 if re.search(r"\b(?:p2|priority\s*2|priority\s*two)\b", q) else 1
    # ponytail: seed brief expects critical orders due by tomorrow; widen if planners need full-week P1s.
    due_limit = 7 if max_priority == 2 else 1
    data = [
        row
        for row in db.get_priority_orders()
        if int(row["priority"]) <= max_priority and int(row["days_remaining"]) <= due_limit
    ]
    return answer_from_data(question, "get_priority", db.PRIORITY_QUEUE_SQL.strip(), data)


def simulate_downtime_tool(question: str) -> dict[str, Any]:
    """Parse one machine and downtime duration, then run deterministic simulation."""
    machine_match = re.search(r"\bM\d+\b", question, flags=re.IGNORECASE)
    hours_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:extra\s+|additional\s+|more\s+)?(?:hours?|hrs?|hr|h)\b",
        question.lower(),
    )
    if machine_match is None or hours_match is None:
        return {
            "question": question,
            "tool_used": "simulate_downtime",
            "sql_used": None,
            "data": [],
            "answer": "Please include a machine ID and downtime hours, for example: M2 down for 4 hours.",
            "explanation": "Downtime simulation needs one machine and one duration.",
            "confidence": 0.4,
            "follow_ups": ["What happens if M2 is down for 4 extra hours?"],
        }

    machine_id = machine_match.group(0).upper()
    downtime_hours = float(hours_match.group(1))
    machine = db.get_machine(machine_id)
    if machine is None:
        return {
            "question": question,
            "tool_used": "simulate_downtime",
            "sql_used": None,
            "data": [],
            "answer": f"I could not find machine {machine_id}.",
            "explanation": "Use a machine ID from the seeded machine list, such as M1 or M2.",
            "confidence": 0.4,
            "follow_ups": ["What happens if M2 is down for 4 extra hours?"],
        }

    orders = db.get_orders_for_simulation(machine_id)
    data = [logic.simulate_downtime(machine, orders, downtime_hours)]
    return answer_from_data(question, "simulate_downtime", None, data, confidence=0.8)


def recommend_tool(question: str) -> dict[str, Any]:
    """Return deterministic planner actions from current risk and load data."""
    loads = logic.calculate_machine_loads(db.get_machines(), db.get_open_orders())
    risks = logic.detect_at_risk_orders(db.get_orders_with_machine_state())
    overloaded_ids = [row["machine_id"] for row in loads if row["load_status"] == "overloaded"]
    active_by_machine = {
        machine_id: db.get_orders_for_simulation(machine_id)
        for machine_id in overloaded_ids
    }
    data = logic.recommend_actions(loads, risks, active_by_machine)
    return answer_from_data(question, "recommend", None, data, confidence=0.8)


def answer_from_data(
    question: str,
    tool: str,
    sql: str | None,
    data: list[dict[str, Any]],
    confidence: float = 0.75,
) -> dict[str, Any]:
    """Build the common /ask response from retrieved or computed data."""
    explanation = explain_result(question, data)
    return {
        "question": question,
        "tool_used": tool,
        "sql_used": sql,
        "data": data,
        "answer": explanation,
        "explanation": explanation,
        "confidence": confidence,
        "follow_ups": ["Which machines are causing the most delays?", "Show high-priority orders due this week."],
    }


def refuse_response(question: str) -> dict[str, Any]:
    """Return the standard out-of-scope refusal."""
    return {
        "question": question,
        "tool_used": "refuse",
        "sql_used": None,
        "data": [],
        "answer": "I can only answer scheduling, machine, or work-order questions.",
        "explanation": "Ask about work orders, machine load, priority, delays, or downtime.",
        "confidence": 0.8,
        "follow_ups": ["Which work orders are delayed?", "Which machines are overloaded?"],
    }


def llm_unavailable_response(question: str, tool: str) -> dict[str, Any]:
    """Return the standard response when Qwen SQL generation is unavailable."""
    return {
        "question": question,
        "tool_used": tool,
        "sql_used": None,
        "data": [],
        "answer": "The local LLM is unavailable, so I could not generate SQL for that question.",
        "explanation": "Check that Ollama is running and that qwen2.5:3b has been pulled.",
        "confidence": 0.1,
        "follow_ups": ["Which work orders are delayed?", "Which machines are overloaded?"],
    }


def safe_log_action(session_id: str, response: dict[str, Any]) -> None:
    """Best-effort audit logging; never break the API response."""
    try:
        db.log_agent_action(
            session_id=session_id,
            action_type=audit_action_type(response),
            input_question=response["question"],
            sql_generated=response["sql_used"],
            result_summary=summarize_result(response["data"]),
            confidence=response["confidence"],
        )
    except psycopg.Error:
        return


def audit_action_type(response: dict[str, Any]) -> str:
    """Map visible tools to assignment audit action types."""
    tool = response["tool_used"]
    if tool == "simulate_downtime":
        return "simulation"
    if tool == "recommend":
        return "recommendation"
    if tool == "refuse" or (not response["data"] and response["confidence"] < 0.5):
        return "clarification"
    return "query_generated"


def summarize_result(data: list[dict[str, Any]]) -> str:
    """Create a short audit summary from returned rows."""
    if not data:
        return "No records returned."

    actions = [str(row["action"]) for row in data if row.get("action")]
    if actions:
        return f"Generated {len(data)} recommendations: {'; '.join(actions[:3])}."

    work_orders = [str(row["wo_id"]) for row in data if row.get("wo_id")]
    if work_orders:
        return f"Found {len(data)} work orders: {', '.join(work_orders[:5])}."

    affected = []
    for row in data:
        affected.extend(
            str(order["wo_id"])
            for order in row.get("affected_orders", [])
            if order.get("wo_id")
        )
    if affected:
        return f"Simulation affected {len(affected)} work orders: {', '.join(affected[:5])}."

    machines = [str(row["machine_id"]) for row in data if row.get("machine_id")]
    if machines:
        return f"Found {len(data)} machines: {', '.join(machines[:5])}."

    return f"Found {len(data)} records."


def is_planning_question(question: str) -> bool:
    """Return whether the question is about the scheduling domain."""
    q = question.lower()
    return any(term in q for term in SCHEDULING_TERMS) or bool(
        re.search(r"\b(?:wo-\d+|m\d+)\b", q)
    )

# some common words
def route_tool(question: str) -> str:
    """Pick the visible tool name for the /ask response."""
    q = question.lower()
    if "recommend" in q or "action" in q or "reduce delay" in q:
        return "recommend"
    if "downtime" in q or "down" in q:
        return "simulate_downtime"
    if "priority" in q or "due this week" in q:
        return "get_priority"
    if "load" in q or "overloaded" in q or "capacity" in q:
        return "check_load"
    return "run_sql"


def extract_work_order_id(question: str) -> str | None:
    """Return the first work-order ID in a question."""
    match = re.search(r"\bWO-\d+\b", question, flags=re.IGNORECASE)
    return match.group(0).upper() if match else None


def generate_sql(question: str) -> tuple[str, tuple[Any, ...]]:
    """Ask Qwen for a SELECT query and bind known user values."""
    content = ollama_chat(
        [
            {"role": "system", "content": sql_prompt()},
            {"role": "user", "content": question},
        ]
    )
    sql = re.sub(r"^```(?:sql)?|```$", "", content.strip(), flags=re.IGNORECASE).strip()
    sql = sql.rstrip(";")
    work_order_id = extract_work_order_id(question)
    if work_order_id is None:
        return sql, ()
    if "%s" in sql:
        return sql, (work_order_id,)
    if "$1" in sql:
        return sql.replace("$1", "%s"), (work_order_id,)

    literal = re.compile(rf"(['\"]){re.escape(work_order_id)}\1", flags=re.IGNORECASE)
    if literal.search(sql):
        return literal.sub("%s", sql), (work_order_id,)
    return sql, ()


@lru_cache(maxsize=None)
def load_prompt(name: str) -> dict[str, Any]:
    """Load one YAML prompt template by filename."""
    path = Path(__file__).resolve().parents[1] / "prompts" / name
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def schema_context() -> dict[str, Any]:
    """Return schema meanings, known values, and business rules."""
    return load_prompt("schema_context.yaml")


def sql_generation_config() -> dict[str, Any]:
    """Return Qwen SQL generation role and rules."""
    return load_prompt("sql_generation.yaml")


def explanation_config() -> dict[str, Any]:
    """Return Qwen explanation role, rules, and fallback wording."""
    return load_prompt("explanation.yaml")


def sql_prompt() -> str:
    """Build the SQL generation prompt from YAML context."""
    sql_config = sql_generation_config()
    context = schema_context()
    return f"""{sql_config["agent"]["role"]}

Purpose:
{sql_config["agent"]["model_purpose"]}

Schema context:
{yaml.safe_dump(context["schema"], sort_keys=False)}

Known values:
{yaml.safe_dump(context["known_values"], sort_keys=False)}

Business rules:
{yaml.safe_dump(context["business_rules"], sort_keys=False)}

Rules:
{yaml.safe_dump(sql_config["rules"], sort_keys=False)}
"""


def explain_result(question: str, data: list[dict[str, Any]]) -> str:
    """Ask Qwen to explain returned rows, with a deterministic fallback."""
    config = explanation_config()
    if not data:
        return config["fallback"]["empty_result"]

    payload = json.dumps(data, default=str)
    rules = yaml.safe_dump(config["rules"], sort_keys=False)
    prompt = f"""{config["agent"]["role"]}

    Question asked:
    {question}

    Data returned:
    {payload}

    Rules:
    {rules}
"""
    try:
        return ollama_chat([{"role": "user", "content": prompt}], timeout=5)
    except httpx.HTTPError:
        return fallback_explanation(data)


def fallback_explanation(data: list[dict[str, Any]]) -> str:
    """Return a readable summary when the local LLM is unavailable."""
    affected = [
        str(order["wo_id"])
        for row in data
        for order in row.get("affected_orders", [])
        if order.get("wo_id")
    ]
    if affected:
        return f"Affected work orders: {', '.join(affected[:6])}."

    actions = [str(row["action"]) for row in data if row.get("action")]
    if actions:
        return f"Recommended actions: {'; '.join(actions[:3])}."

    work_orders = [str(row["wo_id"]) for row in data if row.get("wo_id")]
    if work_orders:
        return f"Work orders returned: {', '.join(work_orders[:6])}."

    machines = [str(row["machine_id"]) for row in data if row.get("machine_id")]
    if machines:
        return f"Machines returned: {', '.join(machines[:6])}."

    return f"Found {len(data)} matching scheduling records."


def ollama_chat(messages: list[dict[str, str]], timeout: int = 30) -> str:
    """Call Ollama's chat API."""
    response = httpx.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={"model": LLM_MODEL, "messages": messages, "stream": False},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]
