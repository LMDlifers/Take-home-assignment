"""Agent orchestration for natural-language planning questions.

Classifies whether a question is in scope, asks the router model for visible
reasoning, routes to the right tool, and logs each action for auditability.
"""

import json
import math
import re
import uuid
from decimal import Decimal
from decimal import ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from typing import Any
from typing import Literal

import httpx
import psycopg
import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError

from app import db
from app import logic
from app.config import settings

OLLAMA_BASE_URL = settings.ollama_base_url
LLM_ROUTER_MODEL = settings.llm_router_model
LLM_SQL_MODEL = settings.llm_sql_model
LLM_JUDGE_MODEL = settings.llm_judge_model
LLM_ANSWER_MODEL = settings.llm_answer_model
LLM_EXPLANATION_MODEL = settings.llm_explanation_model
LLM_FOLLOWUP_MODEL = settings.llm_followup_model
ROUTER_FALLBACK_REASONING = "DeepSeek router reasoning was unavailable; deterministic parsing routed this request."
JUDGE_FALLBACK_REASON = "Judge unavailable; kept deterministic fallback confidence."
EMCS_ENTROPY_WEIGHT = settings.emcs_entropy_weight
ALLOWED_TOOLS = {"run_sql", "check_load", "simulate_downtime", "get_priority", "recommend", "refuse", "count_orders"}
DEFAULT_FOLLOW_UPS = ["Which machines are causing the most delays?", "Show high-priority orders due this week."]


class SQLResponse(BaseModel):
    """Structured response expected from Ollama SQL generation."""

    model_config = ConfigDict(extra="forbid")

    query: str
    params: list[str | int | float] = Field(default_factory=list)


class ExtractedQuestion(BaseModel):
    """Structured intent and entities extracted from a user question."""

    model_config = ConfigDict(extra="forbid")

    intent: str = "unknown"
    work_order_ids: list[str] = Field(default_factory=list)
    machine_ids: list[str] = Field(default_factory=list)
    downtime_hours: float | None = None
    priority_max: int | None = None
    due_window_days: int | None = None
    in_scope: bool = True
    tool: str | None = None
    reason: str | None = None


class RouterResult(BaseModel):
    """Router extraction plus the model-visible reasoning used for explanation."""

    extracted: ExtractedQuestion
    reasoning: str = ROUTER_FALLBACK_REASONING
    used_model: str | None = None


class ConfidenceJudgeResponse(BaseModel):
    """Structured answer-support score from the judge model."""

    model_config = ConfigDict(extra="forbid")

    value_estimate: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    verdict: Literal["supported", "partially_supported", "unsupported", "not_applicable"]
    reason: str
    issues: list[str] = Field(default_factory=list)


class FollowUpResponse(BaseModel):
    """Structured planner follow-up suggestions."""

    model_config = ConfigDict(extra="forbid")

    follow_ups: list[str] = Field(default_factory=list, max_length=3)


SCHEDULING_TERMS = {
    "order",
    "orders",
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
    "status",
    "downtime",
}

INTENT_TO_TOOL = {
    "machine_load": "check_load",
    "simulate_downtime": "simulate_downtime",
    "priority_orders": "get_priority",
    "recommend_actions": "recommend",
    "work_order_count": "count_orders",
    "work_order_risk": "run_sql",
    "work_order_status": "run_sql",
    "delayed_orders": "run_sql",
}


def answer_question(question: str) -> dict[str, Any]:
    """Answer one natural-language planning question."""
    session_id = str(uuid.uuid4()) # for recording into agent_action_log database
    trace: list[dict[str, Any]] = []

    routed = extract_question(question)
    extracted = routed.extracted
    trace.append(
        {
            "step": "planner",
            "result": extracted.model_dump(),
            "reasoning": routed.reasoning,
            "model": routed.used_model,
        }
    )
    if not extracted.in_scope or route_extracted(question, extracted) == "refuse":
        response = refuse_response(question)
        skip_confidence_judge(trace, "Out-of-scope refusal is deterministic.")
        attach_trace(response, trace)
        safe_log_action(session_id, response)
        return response

    validation_response = validate_entities(question, extracted)
    if validation_response is not None:
        trace.append({"step": "entity_validation", "status": "failed", "message": validation_response["answer"]})
        skip_confidence_judge(trace, "Entity validation failure is deterministic.")
        attach_trace(validation_response, trace)
        safe_log_action(session_id, validation_response)
        return validation_response
    trace.append({"step": "entity_validation", "status": "passed"})

    tool = route_extracted(question, extracted)
    trace.append({"step": "route", "tool": tool, "intent": extracted.intent})
    if tool == "check_load":
        response = check_load_tool(question)
    elif tool == "get_priority":
        response = get_priority_tool(question, extracted)
    elif tool == "simulate_downtime":
        response = simulate_downtime_tool(question, extracted)
    elif tool == "recommend":
        response = recommend_tool(question)
    elif tool == "count_orders":
        response = count_orders_tool(question, extracted)
    else:
        response = run_sql_tool(question, tool)

    apply_confidence_judge(response, trace)
    attach_trace(response, trace)
    safe_log_action(session_id, response)
    return response


def run_sql_tool(question: str, tool: str = "run_sql") -> dict[str, Any]:
    """Generate a safe SELECT query, execute it, and explain the result."""
    try:
        generated = generate_sql(question) # Uses LLM
    except httpx.HTTPError:
        return llm_unavailable_response(question, tool)
    except (ValueError, ValidationError) as error:
        return sql_error_response(question, tool, explanation=str(error))

    sql, params = generated if isinstance(generated, tuple) else (generated, ())
    if not db.is_safe_select(sql):
        return sql_error_response(question, tool, sql, "The generated query was rejected before execution.")

    try:
        data = db.fetch_all(sql, params)
    except (ValueError, psycopg.Error) as error:
        return sql_error_response(question, tool, sql, f"The generated query could not be executed safely: {error}")
    return answer_from_data(question, tool, sql, data, confidence_source="llm")


def check_load_tool(question: str) -> dict[str, Any]:
    """Answer load questions with deterministic Python load calculation."""
    q = question.lower()
    data = logic.calculate_machine_loads(db.get_machines(), db.get_open_orders())
    if "not overloaded" in q or "not over loaded" in q:
        data = [row for row in data if row["load_status"] != "overloaded"]
    elif "overloaded" in q:
        data = [row for row in data if row["load_status"] == "overloaded"]
    elif "unavailable" in q:
        data = [row for row in data if row["current_status"] == "unavailable"]
    elif "partially available" in q or "partial" in q:
        data = [row for row in data if row["current_status"] == "partial"]
    return answer_from_data(question, "check_load", db.MACHINE_LOAD_SQL.strip(), data)


def get_priority_tool(question: str, extracted: ExtractedQuestion | None = None) -> dict[str, Any]:
    """Answer priority and due-soon questions with deterministic filtering."""
    q = question.lower()
    max_priority = extracted.priority_max if extracted and extracted.priority_max else None
    if max_priority is None:
        max_priority = 2 if re.search(r"\b(?:high.priority|p2|priority\s*2|priority\s*two)\b", q) else 1
    due_limit = extracted.due_window_days if extracted and extracted.due_window_days else None
    if due_limit is None:
        due_limit = 7 if "week" in q else 3
    data = [
        row
        for row in db.get_priority_orders()
        if int(row["priority"]) <= max_priority and int(row["days_remaining"]) <= due_limit
    ]
    data = sorted(data, key=lambda row: (int(row["priority"]), int(row["days_remaining"]), row["wo_id"]))
    return answer_from_data(question, "get_priority", db.PRIORITY_QUEUE_SQL.strip(), data)


def simulate_downtime_tool(question: str, extracted: ExtractedQuestion | None = None) -> dict[str, Any]:
    """Parse one machine and downtime duration, then run deterministic simulation."""
    machine_id = (extracted.machine_ids[0] if extracted and extracted.machine_ids else None)
    downtime_hours = extracted.downtime_hours if extracted else None
    if machine_id is None:
        machine_match = re.search(r"\bM\d+\b", question, flags=re.IGNORECASE)
        machine_id = machine_match.group(0).upper() if machine_match else None
    if downtime_hours is None:
        hours_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:extra\s+|additional\s+|more\s+)?(?:hours?|hrs?|hr|h)\b",
            question.lower(),
        )
        downtime_hours = float(hours_match.group(1)) if hours_match else None
    if machine_id is None or downtime_hours is None:
        return {
            "question": question,
            "tool_used": "simulate_downtime",
            "sql_used": None,
            "data": [],
            "answer": "Please include a machine ID and downtime hours, for example: M2 down for 4 hours.",
            "explanation": "Downtime simulation needs one machine and one duration.",
            "confidence": 0.4,
            "_confidence_source": "fallback",
            "follow_ups": ["What happens if M2 is down for 4 extra hours?"],
        }

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
            "_confidence_source": "fallback",
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


def delayed_orders_tool(question: str) -> dict[str, Any]:
    """Return delayed work orders from a fixed read query."""
    sql = """
SELECT wo_id, status, due_date, required_machine, priority
FROM work_orders
WHERE status = 'delayed'
ORDER BY wo_id ASC
"""
    try:
        data = db.fetch_all(sql)
    except (ValueError, psycopg.Error) as error:
        return sql_error_response(question, "run_sql", sql.strip(), f"The fixed delayed-orders query failed safely: {error}")
    return answer_from_data(question, "run_sql", sql.strip(), data, confidence_source="fixed_sql")


def count_orders_tool(question: str, extracted: ExtractedQuestion) -> dict[str, Any]:
    """Answer simple work-order status count questions without generated SQL."""
    status = count_status_from_question(question)
    if status is None:
        return run_sql_tool(question)
    sql = "SELECT status, COUNT(*)::int AS count FROM work_orders WHERE status = %s GROUP BY status"
    data = db.fetch_all(sql, (status,)) or [{"status": status, "count": 0}]
    return answer_from_data(question, "run_sql", sql, data, confidence_source="fixed_sql")


def work_order_status_tool(question: str, extracted: ExtractedQuestion) -> dict[str, Any]:
    """Answer direct work-order status questions from a fixed status query."""
    wo_id = first_work_order_id(question, extracted)
    if wo_id is None:
        return run_sql_tool(question)
    data = db.get_work_order_status(wo_id)
    return answer_from_data(question, "run_sql", db.WORK_ORDER_STATUS_SQL.strip(), data, confidence_source="fixed_sql")


def work_order_risk_tool(question: str, extracted: ExtractedQuestion | None = None) -> dict[str, Any]:
    """Answer direct work-order risk questions from the seeded risk view."""
    wo_id = first_work_order_id(question, extracted)
    if wo_id is None:
        return run_sql_tool(question)

    data = db.get_at_risk_order(wo_id)
    return answer_from_data(question, "run_sql", db.AT_RISK_ORDER_SQL.strip(), data, confidence_source="fixed_sql")


def answer_from_data(
    question: str,
    tool: str,
    sql: str | None,
    data: list[dict[str, Any]],
    confidence: float = 0.75,
    confidence_source: str = "deterministic",
) -> dict[str, Any]:
    """Build the common /ask response from retrieved or computed data."""
    draft = deterministic_answer(question, tool, data) or fallback_answer(data)
    answer = draft
    answer_generation = {
        "source": "fallback",
        "model": LLM_ANSWER_MODEL,
        "draft": draft,
    }
    try:
        answer = freestyle_answer(question, tool, sql, data, draft)
        answer_generation["source"] = "llm"
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        answer_generation["reason"] = "Answer model unavailable; kept deterministic draft."
    explanation = draft
    explanation_generation = {
        "source": "fallback",
        "model": LLM_EXPLANATION_MODEL,
    }
    try:
        explanation = explain_result(question, tool, sql, data, draft, answer)
        explanation_generation["source"] = "llm"
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        explanation_generation["reason"] = "Explanation model unavailable; kept deterministic draft."
    follow_ups = DEFAULT_FOLLOW_UPS
    followup_generation = {
        "source": "fallback",
        "model": LLM_FOLLOWUP_MODEL,
    }
    try:
        follow_ups = generate_follow_ups(question, tool, data, answer, explanation)
        followup_generation["source"] = "llm"
    except (httpx.HTTPError, ValueError, ValidationError, KeyError, TypeError):
        followup_generation["reason"] = "Follow-up model unavailable; kept default suggestions."
    return {
        "question": question,
        "tool_used": tool,
        "sql_used": sql,
        "data": data,
        "answer": answer,
        "explanation": explanation,
        "confidence": confidence,
        "_confidence_source": confidence_source,
        "_answer_generation": answer_generation,
        "_explanation_generation": explanation_generation,
        "_followup_generation": followup_generation,
        "follow_ups": follow_ups,
    }


def attach_trace(response: dict[str, Any], trace: list[dict[str, Any]]) -> None:
    """Attach observable pipeline trace."""
    trace.extend(
        [
            {
                "step": "tool_result",
                "tool": response.get("tool_used"),
                "sql_used": response.get("sql_used"),
                "rows": len(response.get("data") or []),
            },
            {
                "step": "answer_generation",
                "answer": response.get("answer"),
                **response.pop("_answer_generation", {"source": "deterministic"}),
                "note": "Answer is LLM-written from retrieved evidence when available.",
            },
            {
                "step": "explanation_generation",
                "explanation": response.get("explanation"),
                **response.pop("_explanation_generation", {"source": "deterministic"}),
                "note": "Explanation is LLM-written from retrieved evidence when available.",
            },
            {
                "step": "followup_generation",
                "follow_ups": response.get("follow_ups"),
                **response.pop("_followup_generation", {"source": "deterministic"}),
                "note": "Follow-ups are LLM-written from the response context when available.",
            },
            {"step": "audit_log", "status": "best_effort"},
        ]
    )
    response["trace"] = trace


def skip_confidence_judge(trace: list[dict[str, Any]], reason: str) -> None:
    """Record that confidence stayed deterministic."""
    trace.append({"step": "confidence_judge", "status": "skipped", "reason": reason})


def apply_confidence_judge(response: dict[str, Any], trace: list[dict[str, Any]]) -> None:
    """Calibrate response confidence with deterministic or LLM EMCS."""
    source = response.pop("_confidence_source", "deterministic")
    if source in {"deterministic", "fixed_sql"}:
        value_estimate = 0.92 if source == "fixed_sql" else 0.95
        confidence_score = 0.95
        calibration = emcs_calibration(value_estimate, confidence_score)
        response["confidence"] = calibration["emcs_score"]
        trace.append(
            {
                "step": "confidence_judge",
                "status": "ok",
                "source": "deterministic",
                **calibration,
                "verdict": "supported",
                "reason": "Deterministic evidence path; EMCS calibrated without calling the judge model.",
                "issues": [],
            }
        )
        return

    if source == "fallback":
        trace.append(
            {
                "step": "confidence_judge",
                "status": "fallback",
                "source": "fallback",
                "reason": "Fallback response kept its existing confidence.",
                "score": response.get("confidence"),
            }
        )
        return

    try:
        judged = judge_confidence(response)
    except (httpx.HTTPError, ValueError, ValidationError, KeyError, TypeError):
        trace.append(
            {
                "step": "confidence_judge",
                "status": "fallback",
                "source": "fallback",
                "model": LLM_JUDGE_MODEL,
                "reason": JUDGE_FALLBACK_REASON,
                "score": response.get("confidence"),
            }
        )
        return

    calibration = emcs_calibration(judged.value_estimate, judged.confidence_score)
    response["confidence"] = calibration["emcs_score"]
    trace.append(
        {
            "step": "confidence_judge",
            "status": "ok",
            "source": "llm",
            "model": LLM_JUDGE_MODEL,
            **calibration,
            "verdict": judged.verdict,
            "reason": judged.reason,
            "issues": judged.issues,
        }
    )


def clamp_unit(value: float) -> float:
    """Clamp a score into the public 0..1 range."""
    return min(max(float(value), 0.0), 1.0)


def clamp_probability(value: float) -> float:
    """Clamp probability away from exact edges for entropy math."""
    return min(max(float(value), 1e-9), 1 - 1e-9)


def bernoulli_entropy(confidence_score: float) -> float:
    """Return Bernoulli entropy for evaluator confidence."""
    confidence = clamp_probability(confidence_score)
    return -(confidence * math.log(confidence) + (1 - confidence) * math.log(1 - confidence))


def emcs_calibration(
    value_estimate: float,
    confidence_score: float,
    entropy_weight: float = EMCS_ENTROPY_WEIGHT,
) -> dict[str, float]:
    """Return EMCS-calibrated confidence fields."""
    value = clamp_unit(value_estimate)
    confidence = clamp_unit(confidence_score)
    entropy = bernoulli_entropy(confidence)
    entropy_norm = entropy / math.log(2)
    emcs_score = value * (1 - entropy_weight * entropy_norm)
    emcs_score = clamp_unit(emcs_score)
    return {
        "value_estimate": round(value, 4),
        "confidence_score": round(confidence, 4),
        "entropy": round(entropy, 4),
        "entropy_norm": round(entropy_norm, 4),
        "entropy_weight": round(entropy_weight, 4),
        "emcs_score": round(emcs_score, 4),
        "score": round(emcs_score, 4),
    }


def judge_confidence(response: dict[str, Any]) -> ConfidenceJudgeResponse:
    """Ask Qwen to score whether the answer is supported by returned evidence."""
    content = ollama_chat(
        [{"role": "user", "content": judge_prompt(response)}],
        model=LLM_JUDGE_MODEL,
        format_schema=ConfidenceJudgeResponse.model_json_schema(),
        options={"temperature": 0, "num_predict": 200},
        timeout=settings.llm_judge_timeout_seconds,
    )
    return ConfidenceJudgeResponse.model_validate_json(content)


def deterministic_answer(question: str, tool: str, data: list[dict[str, Any]]) -> str | None:
    """Return planner-friendly summaries for known deterministic shapes."""
    if not data:
        return None

    q = question.lower()
    if tool == "check_load":
        if any(term in q for term in ("highest load", "most active queued work")):
            row = data[0]
            if "most active queued work" in q:
                return (
                    f"{row['machine_id']} has the most active queued work with "
                    f"{row.get('active_order_count', 0)} active orders and {format_number(row['queued_hours'])} queued hours."
                )
            return (
                f"{row['machine_id']} has the highest load with "
                f"{format_number(row['queued_hours'])} queued hours and {format_number(row['load_pct'])}% load."
            )
        if "unavailable" in q:
            machines = [
                f"{row['machine_id']} ({row['machine_name']}) is unavailable with {format_number(row['available_hours_today'])} available hours today"
                for row in data
            ]
            return f"{'; '.join(machines)}."
        if "partially available" in q or "partial" in q:
            machines = [
                f"{row['machine_id']} with {format_one_decimal(row['available_hours_today'])} available hours"
                for row in sorted(data, key=lambda item: item["machine_id"])
            ]
            return f"The partially available machines are {', '.join(machines)}."
        machines = [
            f"{row['machine_id']} ({format_number(row['load_pct'])}%)"
            for row in data
            if row.get("machine_id") and row.get("load_pct") is not None
        ]
        if machines:
            qualifier = "not overloaded" if "not overloaded" in q else "overloaded" if "overloaded" in q else "highest-load"
            return f"The {qualifier} machines are {', '.join(machines)}."

    if tool == "get_priority":
        ids = [str(row["wo_id"]) for row in data if row.get("wo_id")]
        if ids:
            priorities = sorted({f"P{row['priority']}" for row in data if row.get("priority")})
            return f"High-priority orders due in this window are {', '.join(ids)} ({', '.join(priorities)})."

    if tool == "simulate_downtime":
        row = data[0]
        affected = row.get("affected_orders", [])
        ids = [str(order["wo_id"]) for order in affected if order.get("wo_id")]
        if ids:
            delayed = [str(order["wo_id"]) for order in affected if order.get("status") == "delayed"]
            new_risk = [str(order["wo_id"]) for order in affected if order.get("status") != "delayed"]
            parts = [
                f"{row['machine_id']} would have {format_number(row['new_available_hours'])} hours left after {format_number(row['downtime_hours'])} extra hours down."
            ]
            if new_risk:
                parts.append(f"Newly affected orders: {', '.join(new_risk)}.")
            if delayed:
                parts.append(f"Already delayed orders deepen: {', '.join(delayed)}.")
            return " ".join(parts)

    actions = [str(row["action"]) for row in data if row.get("action")]
    if actions:
        return f"Recommended actions: {'; '.join(actions)}."

    counts = [row for row in data if row.get("status") and row.get("count") is not None]
    if counts:
        row = counts[0]
        label = str(row["status"]).replace("_", "-")
        return f"{row['count']} {label} work orders."

    work_orders = [str(row["wo_id"]) for row in data if row.get("wo_id")]
    if work_orders:
        risk_reasons = [str(row["risk_reason"]) for row in data if row.get("risk_reason")]
        if risk_reasons:
            row = data[0]
            machine = row.get("required_machine")
            reason = risk_reasons[0]
            if machine and reason.startswith("Machine unavailable"):
                return f"{work_orders[0]} is at risk: Machine {machine} is unavailable, so {work_orders[0]} cannot be scheduled."
            if machine and reason.startswith("Processing time exceeds"):
                return f"{work_orders[0]} is at risk: it requires {machine}; processing time exceeds available machine hours today."
            return f"{', '.join(work_orders)} is at risk: {reason}."
        statuses = [str(row["status"]) for row in data if row.get("status")]
        machines = [str(row["required_machine"]) for row in data if row.get("required_machine")]
        if len(work_orders) == 1 and statuses:
            row = data[0]
            machine = f", requires {machines[0]}" if machines else ""
            priority = f", priority P{row['priority']}" if row.get("priority") is not None else ""
            due = f", due {row['due_date']}" if row.get("due_date") else ""
            return f"{work_orders[0]} is {statuses[0]}{machine}{priority}{due}."
        return f"Work orders returned: {', '.join(work_orders)}."

    return None


def format_number(value: Any) -> str:
    """Format numeric values without noisy trailing decimals."""
    number = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return str(int(number)) if number == number.to_integral() else f"{number:.1f}"


def format_one_decimal(value: Any) -> str:
    """Format operational hours with one decimal place."""
    return f"{Decimal(str(value)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):.1f}"


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
        "explanation": "Check that Ollama is running and that the configured model has been pulled.",
        "confidence": 0.1,
        "_confidence_source": "fallback",
        "follow_ups": ["Which work orders are delayed?", "Which machines are overloaded?"],
    }


def sql_error_response(
    question: str,
    tool: str,
    sql: str | None = None,
    explanation: str = "The generated SQL response was not valid.",
) -> dict[str, Any]:
    """Return the standard response when generated SQL is malformed or unsafe."""
    return {
        "question": question,
        "tool_used": tool,
        "sql_used": sql,
        "data": [],
        "answer": "I could not generate a safe read-only query for that question.",
        "explanation": explanation,
        "confidence": 0.2,
        "_confidence_source": "fallback",
        "follow_ups": ["Which work orders are delayed?", "Show high-priority orders due this week."],
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
        # Accept scheduling IDs such as WO-1003 or M4 even without other keywords.
        re.search(r"\b(?:wo-\d+|m\d+|\d{4})\b", q)
    )


def extract_work_order_id(question: str) -> str | None:
    """Return a work-order ID token such as WO-1003, if present."""
    # Extract direct scheduling IDs like WO-1003 from a user question.
    match = re.search(r"\bWO-\d+\b", question, flags=re.IGNORECASE)
    return match.group(0).upper() if match else None


def extract_work_order_ids(question: str) -> list[str]:
    """Return work-order IDs, including shorthand like 1003 when context implies an order."""
    ids = {match.upper() for match in re.findall(r"\bWO-\d+\b", question, flags=re.IGNORECASE)}
    q = question.lower()
    if any(term in q for term in ("order", "work", "wo", "status", "delayed", "risk", "blocked", "current")):
        ids.update(f"WO-{number}" for number in re.findall(r"\b(10\d{2})\b", question))
    return sorted(ids)


def first_work_order_id(question: str, extracted: ExtractedQuestion | None = None) -> str | None:
    """Return the first extracted work-order ID from structured or direct parsing."""
    if extracted and extracted.work_order_ids:
        return extracted.work_order_ids[0]
    ids = extract_work_order_ids(question)
    return ids[0] if ids else None


def is_work_order_risk_question(question: str) -> bool:
    """Return whether a question asks why one work order is at risk."""
    q = question.lower()
    return first_work_order_id(question) is not None and any(
        term in q for term in ("risk", "why", "blocked")
    )


def extract_question(question: str) -> RouterResult:
    """Plan with DeepSeek first, using deterministic parsing for trusted facts."""
    deterministic = deterministic_extract_question(question)
    try:
        routed = route_question_with_slm(question)
    except (httpx.HTTPError, ValueError, ValidationError):
        return RouterResult(extracted=deterministic)
    return RouterResult(
        extracted=merge_extractions(deterministic, routed.extracted),
        reasoning=routed.reasoning,
        used_model=routed.used_model,
    )


def deterministic_extract_question(question: str) -> ExtractedQuestion:
    """Use cheap exact parsing before paying for model inference."""
    q = question.lower()
    intent = "unknown"
    if "recommend" in q or "action" in q or "reduce delay" in q:
        intent = "recommend_actions"
    elif "downtime" in q or "down" in q:
        intent = "simulate_downtime"
    elif "how many" in q and count_status_from_question(question):
        intent = "work_order_count"
    elif "priority" in q or "due this week" in q or "due soon" in q:
        intent = "priority_orders"
    elif (
        "load" in q
        or "overloaded" in q
        or "capacity" in q
        or "queued work" in q
        or "unavailable" in q
        or "partially available" in q
        or "partial" in q
    ):
        intent = "machine_load"
    elif is_work_order_risk_question(question):
        intent = "work_order_risk"
    elif first_work_order_id(question) and any(term in q for term in ("status", "current", "progress", "delayed")):
        intent = "work_order_status"
    elif "delayed" in q or "delay" in q:
        intent = "delayed_orders"

    # # Match downtime duration (e.g., "4.5 hours", "12h", "2 extra hrs")
    hours_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:extra\s+|additional\s+|more\s+)?(?:hours?|hrs?|hr|h)\b",
        q,
    )
    machine_ids = [match.upper() for match in re.findall(r"\bM\d+\b", question, flags=re.IGNORECASE)]
    priority_max = 2 if re.search(r"\b(?:high.priority|p2|priority\s*2|priority\s*two)\b", q) else None
    return ExtractedQuestion(
        intent=intent,
        work_order_ids=extract_work_order_ids(question),
        machine_ids=sorted(set(machine_ids)),
        downtime_hours=float(hours_match.group(1)) if hours_match else None,
        priority_max=priority_max,
        due_window_days=7 if "week" in q else None,
        in_scope=is_planning_question(question),
    )


def extraction_is_complete(extracted: ExtractedQuestion) -> bool:
    """Return whether deterministic parsing found enough to route safely."""
    if extracted.intent in {"unknown", "simulate_downtime"} and not extracted.machine_ids:
        return False
    if extracted.intent == "simulate_downtime" and extracted.downtime_hours is None:
        return False
    return extracted.intent != "unknown" or bool(extracted.work_order_ids)


def merge_extractions(primary: ExtractedQuestion, secondary: ExtractedQuestion) -> ExtractedQuestion:
    """Prefer deterministic facts, filling gaps from SLM output."""
    intent = secondary.intent if secondary.intent != "unknown" else primary.intent
    machine_ids = primary.machine_ids
    if not machine_ids and intent == "simulate_downtime":
        machine_ids = secondary.machine_ids
    return ExtractedQuestion(
        intent=intent,
        work_order_ids=primary.work_order_ids or secondary.work_order_ids,
        machine_ids=machine_ids,
        downtime_hours=primary.downtime_hours if primary.downtime_hours is not None else secondary.downtime_hours,
        priority_max=primary.priority_max if primary.priority_max is not None else secondary.priority_max,
        due_window_days=primary.due_window_days if primary.due_window_days is not None else secondary.due_window_days,
        in_scope=secondary.in_scope if secondary.in_scope is not None else primary.in_scope,
        tool=secondary.tool or primary.tool,
        reason=secondary.reason or primary.reason,
    )


def route_question_with_slm(question: str) -> RouterResult:
    """Ask DeepSeek to reason about routing, then parse its final JSON."""
    prompt = router_prompt(question, db.get_machine_aliases())
    content = ollama_chat(
        [{"role": "user", "content": prompt}],
        model=LLM_ROUTER_MODEL,
        options={"temperature": 0, "num_predict": 160},
        timeout=settings.llm_router_timeout_seconds,
    )
    return parse_router_response(content)


def parse_router_response(content: str) -> RouterResult:
    """Split DeepSeek <think> reasoning from the final extraction JSON."""
    think_match = re.search(r"<think>(.*?)</think>", content, flags=re.IGNORECASE | re.DOTALL)
    reasoning = think_match.group(1).strip() if think_match else ""
    payload = re.sub(r"<think>.*?</think>", "", content, flags=re.IGNORECASE | re.DOTALL).strip()
    payload_match = re.search(r"\{.*\}", payload, flags=re.DOTALL)
    if payload_match is None:
        raise ValueError("Router response did not include JSON.")
    extracted = ExtractedQuestion.model_validate_json(payload_match.group(0))
    return RouterResult(
        extracted=normalize_extracted(extracted),
        reasoning=reasoning or extracted.reason or ROUTER_FALLBACK_REASONING,
        used_model=LLM_ROUTER_MODEL,
    )


def normalize_extracted(extracted: ExtractedQuestion) -> ExtractedQuestion:
    """Normalize IDs returned by the SLM before DB validation."""
    work_orders = []
    for value in extracted.work_order_ids:
        value = str(value).upper()
        if re.fullmatch(r"\d{4}", value):
            value = f"WO-{value}"
        if re.fullmatch(r"WO-\d+", value):
            work_orders.append(value)
    machines = [
        str(value).upper()
        for value in extracted.machine_ids
        if re.fullmatch(r"M\d+", str(value), flags=re.IGNORECASE)
    ]
    return ExtractedQuestion(
        intent=extracted.intent,
        work_order_ids=sorted(set(work_orders)),
        machine_ids=sorted(set(machines)),
        downtime_hours=extracted.downtime_hours,
        priority_max=extracted.priority_max,
        due_window_days=extracted.due_window_days,
        in_scope=extracted.in_scope,
        tool=extracted.tool if extracted.tool in ALLOWED_TOOLS else None,
        reason=extracted.reason,
    )


def validate_entities(question: str, extracted: ExtractedQuestion) -> dict[str, Any] | None:
    """Fail fast when extracted entities do not exist in live data."""
    for wo_id in extracted.work_order_ids:
        if not db.work_order_exists(wo_id):
            return entity_not_found_response(question, f"Work order {wo_id} was not found.")
    for machine_id in extracted.machine_ids:
        if not db.machine_exists(machine_id):
            return entity_not_found_response(question, f"Machine {machine_id} was not found.")
    return None


def count_status_from_question(question: str) -> str | None:
    """Return a work-order status for simple count questions."""
    q = question.lower()
    if "in progress" in q or "in-progress" in q:
        return "in_progress"
    if "pending" in q:
        return "pending"
    if "completed" in q or "complete" in q:
        return "completed"
    if "delayed" in q:
        return "delayed"
    if "on hold" in q or "on_hold" in q:
        return "on_hold"
    return None


def entity_not_found_response(question: str, message: str) -> dict[str, Any]:
    """Return a deterministic missing-entity answer."""
    return {
        "question": question,
        "tool_used": "refuse",
        "sql_used": None,
        "data": [],
        "answer": message,
        "explanation": message,
        "confidence": 0.9,
        "follow_ups": ["Which work orders are delayed?", "Which machines are overloaded?"],
    }


def route_extracted(question: str, extracted: ExtractedQuestion) -> str:
    """Route from structured extraction, falling back to keyword routing."""
    if extracted.tool in ALLOWED_TOOLS:
        return extracted.tool
    return INTENT_TO_TOOL.get(extracted.intent) or route_tool(question)


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


def generate_sql(question: str) -> tuple[str, tuple[Any, ...]]:
    """Ask Qwen for a structured SELECT query and parameters."""
    content = ollama_chat(
        [
            {
                "role": "system",
                "content": sql_prompt(),
            },
            {"role": "user", "content": question},
        ],
        model=LLM_SQL_MODEL,
        format_schema=SQLResponse.model_json_schema(),
        options={"temperature": 0, "num_predict": 200},
        timeout=settings.llm_sql_timeout_seconds,
    )
    generated = SQLResponse.model_validate_json(content)
    sql = generated.query.strip()
    sql = sql.rstrip(";")
    params = tuple(generated.params)
    validate_generated_sql(sql, params)
    return sql, params


def validate_generated_sql(sql: str, params: tuple[Any, ...]) -> None:
    """Validate placeholder usage before SQL reaches psycopg."""
    if "$1" in sql:
        raise ValueError("Use %s placeholders, not $1 placeholders.")
    if "'%s'" in sql or '"%s"' in sql:
        raise ValueError("Do not quote %s placeholders.")
    if sql.count("%s") != len(params):
        raise ValueError("SQL placeholder count does not match params count.")


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


def router_config() -> dict[str, Any]:
    """Return DeepSeek router role, rules, and few-shot examples."""
    return load_prompt("router.yaml")


def judge_config() -> dict[str, Any]:
    """Return Qwen confidence judge role and rules."""
    return load_prompt("judge.yaml")


def answer_config() -> dict[str, Any]:
    """Return Qwen grounded answer-writing role and rules."""
    return load_prompt("answer.yaml")


def explanation_config() -> dict[str, Any]:
    """Return Qwen grounded explanation-writing role and rules."""
    return load_prompt("explanation.yaml")


def followup_config() -> dict[str, Any]:
    """Return Qwen follow-up suggestion role and rules."""
    return load_prompt("followups.yaml")


def router_prompt(question: str, aliases: list[dict[str, Any]]) -> str:
    """Build the reasoning-router prompt from YAML."""
    config = router_config()
    return config["template"].format(
        role=config["agent"]["role"],
        purpose=config["agent"]["model_purpose"],
        rules=yaml.safe_dump(config["rules"], sort_keys=False),
        aliases=json.dumps(aliases, default=str),
        examples=yaml.safe_dump(config["examples"], sort_keys=False),
        question=question,
    )


def entity_extraction_config() -> dict[str, Any]:
    """Backward-compatible alias for the active router prompt config."""
    return router_config()


def entity_extraction_prompt(question: str, aliases: list[dict[str, Any]]) -> str:
    """Backward-compatible alias for the active router prompt."""
    return router_prompt(question, aliases)


def sql_prompt() -> str:
    """Build the SQL generation prompt from YAML context."""
    sql_config = sql_generation_config()
    context = schema_context()
    return sql_config["template"].format(
        role=sql_config["agent"]["role"],
        purpose=sql_config["agent"]["model_purpose"],
        schema=yaml.safe_dump(context["schema"], sort_keys=False),
        known_values=yaml.safe_dump(context["known_values"], sort_keys=False),
        business_rules=yaml.safe_dump(context["business_rules"], sort_keys=False),
        rules=yaml.safe_dump(sql_config["rules"], sort_keys=False),
        examples=yaml.safe_dump(sql_config.get("examples", []), sort_keys=False),
    )


def judge_prompt(response: dict[str, Any]) -> str:
    """Build the confidence-judge prompt from YAML."""
    config = judge_config()
    evidence = {
        "question": response.get("question"),
        "tool_used": response.get("tool_used"),
        "sql_used": response.get("sql_used"),
        "data": response.get("data"),
        "answer": response.get("answer"),
        "fallback_confidence": response.get("confidence"),
    }
    return config["template"].format(
        role=config["agent"]["role"],
        purpose=config["agent"]["model_purpose"],
        rules=yaml.safe_dump(config["rules"], sort_keys=False),
        value_rubric=yaml.safe_dump(config["value_rubric"], sort_keys=False),
        confidence_rubric=yaml.safe_dump(config["confidence_rubric"], sort_keys=False),
        evidence=json.dumps(evidence, default=str, ensure_ascii=True),
    )


def answer_prompt(question: str, tool: str, sql: str | None, data: list[dict[str, Any]], draft: str) -> str:
    """Build the grounded freestyle answer prompt from YAML."""
    config = answer_config()
    evidence = {
        "question": question,
        "tool_used": tool,
        "sql_used": sql,
        "data": data,
    }
    return config["template"].format(
        role=config["agent"]["role"],
        purpose=config["agent"]["model_purpose"],
        rules=yaml.safe_dump(config["rules"], sort_keys=False),
        draft=draft,
        evidence=json.dumps(evidence, default=str, ensure_ascii=True),
    )


def explanation_prompt(
    question: str,
    tool: str,
    sql: str | None,
    data: list[dict[str, Any]],
    draft: str,
    answer: str,
) -> str:
    """Build the grounded planner explanation prompt from YAML."""
    config = explanation_config()
    context = schema_context()
    evidence = {
        "question": question,
        "tool_used": tool,
        "sql_used": sql,
        "data": data,
        "answer": answer,
        "deterministic_draft": draft,
    }
    return config["template"].format(
        role=config["agent"]["role"],
        purpose=config["agent"]["model_purpose"],
        rules=yaml.safe_dump(config["rules"], sort_keys=False),
        business_rules=yaml.safe_dump(context["business_rules"], sort_keys=False),
        evidence=json.dumps(evidence, default=str, ensure_ascii=True),
    )


def followup_prompt(
    question: str,
    tool: str,
    data: list[dict[str, Any]],
    answer: str,
    explanation: str,
) -> str:
    """Build the grounded follow-up suggestion prompt from YAML."""
    config = followup_config()
    evidence = {
        "question": question,
        "tool_used": tool,
        "data": data,
        "answer": answer,
        "explanation": explanation,
    }
    return config["template"].format(
        role=config["agent"]["role"],
        purpose=config["agent"]["model_purpose"],
        rules=yaml.safe_dump(config["rules"], sort_keys=False),
        evidence=json.dumps(evidence, default=str, ensure_ascii=True),
    )


def freestyle_answer(question: str, tool: str, sql: str | None, data: list[dict[str, Any]], draft: str) -> str:
    """Ask Qwen to write the final answer using only returned evidence."""
    content = ollama_chat(
        [{"role": "user", "content": answer_prompt(question, tool, sql, data, draft)}],
        model=LLM_ANSWER_MODEL,
        options={"temperature": 0.4, "num_predict": 160},
        timeout=settings.llm_answer_timeout_seconds,
    ).strip()
    if not content:
        raise ValueError("Answer model returned empty content.")
    return content


def explain_result(
    question: str,
    tool: str,
    sql: str | None,
    data: list[dict[str, Any]],
    draft: str,
    answer: str,
) -> str:
    """Ask Qwen to explain the returned scheduling evidence in planner language."""
    content = ollama_chat(
        [{"role": "user", "content": explanation_prompt(question, tool, sql, data, draft, answer)}],
        model=LLM_EXPLANATION_MODEL,
        options={"temperature": 0.3, "num_predict": 220},
        timeout=settings.llm_explanation_timeout_seconds,
    ).strip()
    if not content:
        raise ValueError("Explanation model returned empty content.")
    return content


def generate_follow_ups(
    question: str,
    tool: str,
    data: list[dict[str, Any]],
    answer: str,
    explanation: str,
) -> list[str]:
    """Ask Qwen for planner-relevant follow-up questions."""
    content = ollama_chat(
        [{"role": "user", "content": followup_prompt(question, tool, data, answer, explanation)}],
        model=LLM_FOLLOWUP_MODEL,
        format_schema=FollowUpResponse.model_json_schema(),
        options={"temperature": 0.2, "num_predict": 128},
        timeout=settings.llm_followup_timeout_seconds,
    )
    follow_ups = [item.strip() for item in FollowUpResponse.model_validate_json(content).follow_ups if item.strip()]
    if not follow_ups:
        raise ValueError("Follow-up model returned no suggestions.")
    return follow_ups[:3]


def fallback_answer(data: list[dict[str, Any]]) -> str:
    """Return a readable answer when a result shape has no custom template."""
    if not data:
        return "I did not find matching scheduling records for that question."

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


def ollama_chat(
    messages: list[dict[str, str]],
    timeout: int = 30,
    model: str | None = None,
    format_schema: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    include_thinking: bool = False,
) -> str:
    """Call Ollama's chat API."""
    payload: dict[str, Any] = {"model": model or LLM_SQL_MODEL, "messages": messages, "stream": False}
    if format_schema is not None:
        payload["format"] = format_schema
    if options is not None:
        payload["options"] = options

    response = httpx.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    message = response.json()["message"]
    content = message["content"]
    thinking = message.get("thinking")
    if include_thinking and thinking:
        return f"<think>{thinking.strip()}</think>\n{content}"
    return content
