"""Agent orchestration for natural-language planning questions.

Classifies whether a question is in scope, routes it to the right tool, calls
deterministic data/business functions, asks the LLM to explain retrieved facts,
and logs each action for auditability.
"""

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml

from app import db

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
    if not is_planning_question(question):
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

    tool = route_tool(question)
    try:
        sql = generate_sql(question)
    except httpx.HTTPError:
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

    data = db.fetch_all(sql)
    explanation = explain_result(question, data)
    return {
        "question": question,
        "tool_used": tool,
        "sql_used": sql,
        "data": data,
        "answer": explanation,
        "explanation": explanation,
        "confidence": 0.75,
        "follow_ups": ["Which machines are causing the most delays?", "Show high-priority orders due this week."],
    }


def is_planning_question(question: str) -> bool:
    """Return whether the question is about the scheduling domain."""
    q = question.lower()
    return any(term in q for term in SCHEDULING_TERMS)


def route_tool(question: str) -> str:
    """Pick the visible tool name for the /ask response."""
    q = question.lower()
    if "priority" in q or "due this week" in q:
        return "get_priority"
    if "load" in q or "overloaded" in q or "capacity" in q:
        return "check_load"
    return "run_sql"


def generate_sql(question: str) -> str:
    """Ask Qwen for a SELECT query and strip common formatting noise."""
    content = ollama_chat(
        [
            {"role": "system", "content": sql_prompt()},
            {"role": "user", "content": question},
        ]
    )
    sql = re.sub(r"^```(?:sql)?|```$", "", content.strip(), flags=re.IGNORECASE).strip()
    return sql.rstrip(";")


@lru_cache(maxsize=1)
def prompt_config() -> dict[str, Any]:
    """Load prompt and schema context for the planning copilot."""
    path = Path(__file__).resolve().parents[1] / "prompts" / "planning_copilot.yaml"
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def sql_prompt() -> str:
    """Build the SQL generation prompt from YAML context."""
    config = prompt_config()
    return f"""{config["agent"]["role"]}

Purpose:
{config["agent"]["model_purpose"]}

Schema context:
{yaml.safe_dump(config["schema"], sort_keys=False)}

Business rules:
{yaml.safe_dump(config["business_rules"], sort_keys=False)}

Rules:
{yaml.safe_dump(config["sql_generation"]["rules"], sort_keys=False)}
"""


def explain_result(question: str, data: list[dict[str, Any]]) -> str:
    """Ask Qwen to explain returned rows, with a deterministic fallback."""
    if not data:
        return "I did not find matching scheduling records for that question."

    payload = json.dumps(data, default=str)
    prompt = (
        f"Question asked: {question}\n"
        f"Data returned: {payload}\n"
        "Write a clear 3 sentence planning answer. Mention specific work order or machine IDs. "
        "Do not mention SQL, JSON, or databases. Do not invent facts."
    )
    try:
        return ollama_chat([{"role": "user", "content": prompt}])
    except httpx.HTTPError:
        ids = ", ".join(str(row.get("wo_id") or row.get("machine_id")) for row in data[:5])
        return f"Found {len(data)} matching scheduling records: {ids}."


def ollama_chat(messages: list[dict[str, str]]) -> str:
    """Call Ollama's chat API."""
    response = httpx.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={"model": LLM_MODEL, "messages": messages, "stream": False},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]
