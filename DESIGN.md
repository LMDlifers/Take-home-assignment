# Design Notes

## Core Approach

This project uses a deterministic Python controller with local LLM enrichment.
The API does not let the LLM orchestrate every action. Instead, `agent.py`
routes each question to a known tool path, runs deterministic business logic or
safe SQL, and then optionally asks Qwen to explain the result in plain English.

This keeps the critical scheduling behavior predictable while still allowing a
natural language interface through `/api/v1/ask`.

## Why Local Qwen

The project uses `qwen2.5:3b` through Ollama because it is free, private, and
able to run on consumer-grade hardware without external API keys.

The tradeoff is that a small language model is less reliable than a frontier
model. It can time out, produce weak reasoning, or generate invalid SQL. The
implementation assumes this and avoids depending on the model for critical
business calculations.

## Reliability Patterns

- Known scheduling questions are routed to deterministic tools.
- Core calculations such as machine load, at-risk orders, downtime simulation,
  and recommendations are implemented in Python.
- Qwen is used for SQL generation only in the `run_sql` path.
- Generated SQL must pass Python safety checks before execution.
- Bad SQL, unsafe SQL, or LLM failures return controlled responses instead of
  crashing the API.
- If explanation generation fails, the API falls back to a deterministic summary.

## Schema Context

Prompt context is stored in YAML under `Backend/prompts/`. The schema context
lists the allowed tables, views, columns, known values, and business rules used
by Qwen.

This reduces column hallucination and makes SQL generation more reliable. The
tradeoff is that the schema context is static. If the database schema changes,
the YAML must be updated manually.

## Audit Logging

Each `/api/v1/ask` response writes a best-effort audit row to
`agent_action_log`. The log records the question, selected tool, generated SQL,
summary, confidence, and timestamp.

Audit logging is intentionally non-blocking. If the log insert fails, the user
still receives the scheduling answer.

## Known Tradeoffs

- The router is less flexible than a fully autonomous agent, but more reliable
  for a take-home assignment with fixed business questions.
- Local SLM inference avoids cloud dependencies, but can be slower and less
  accurate.
- Static YAML schema context is simple and transparent, but not self-updating.
- The UI is intentionally minimal so the backend behavior remains the focus.
