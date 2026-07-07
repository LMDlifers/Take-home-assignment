# AI Planning Copilot for Shop Floor Scheduling

FastAPI implementation of the GenAI/Agentic AI take-home assignment. The app answers shop-floor scheduling questions from PostgreSQL seed data, routes each question to a planning tool, explains the result with a local Ollama model, and writes an audit row for each `/ask` request.

## Stack

- API: FastAPI
- Database: PostgreSQL
- LLM: Ollama with `qwen2.5:3b`
- UI: plain HTML served at `/ui/`
- Tests: pytest

## Project Structure

```text
Backend/app/        FastAPI routes, agent flow, DB helpers, schemas, logic
Backend/prompts/    Router, SQL, explanation, and schema-context prompts
Data/seed.sql       PostgreSQL schema, seed data, views, audit table
Documents/          Assignment PDF
frontend/           Static test UI
tests/              Unit and route tests
```

## Run

```bash
docker compose up --build
```

The API starts at:

```text
http://localhost:8000
```

Swagger:

```text
http://localhost:8000/docs
```

Test UI:

```text
http://localhost:8000/ui/
```

First startup can take a while because Ollama may need to pull `qwen2.5:3b`.

## Required Endpoints

```text
GET  /api/v1/health
GET  /api/v1/machines/load
GET  /api/v1/orders/at-risk
POST /api/v1/simulate/downtime
POST /api/v1/ask
```

Example health check:

```bash
curl http://localhost:8000/api/v1/health
```

Example downtime simulation:

```bash
curl -X POST http://localhost:8000/api/v1/simulate/downtime \
  -H "Content-Type: application/json" \
  -d '{"machine_id":"M2","downtime_hours":4}'
```

## Ask Endpoint

```bash
curl -X POST http://localhost:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"Which machines are overloaded?"}'
```

Response fields:

- `question`
- `tool_used`
- `sql_used`
- `data`
- `answer`
- `explanation`
- `confidence`
- `follow_ups`
- `trace`

`answer` is deterministic so required IDs, statuses, and recommended actions stay intact. `explanation` is written by Qwen from the returned evidence. `trace` is kept intentionally so reviewers can see the agent flow.

Useful assessment questions:

```text
Which work orders are delayed?
Which machines are overloaded?
Why is WO-1003 at risk?
What happens if M2 is down for 4 extra hours?
Show high-priority orders due this week.
Recommend actions to reduce delays.
```

## Agent Flow

```text
question
  -> classify and extract entities
  -> route to one tool
  -> execute SQL or deterministic business logic
  -> write deterministic answer
  -> ask Qwen for a grounded explanation
  -> set source-based confidence
  -> write agent_action_log
```

Tools:

- `run_sql`
- `check_load`
- `simulate_downtime`
- `get_priority`
- `recommend`
- `refuse`

## Tests

Inside the API container:

```bash
docker compose exec api pytest -q
```

Local Python may not have the same dependencies installed, so the container command is the reliable check.

## Audit Log

Each `/api/v1/ask` call writes a best-effort row to `agent_action_log`.

```bash
docker compose exec db psql -U postgres -d scheduling_db \
  -c "SELECT action_type, input_question, result_summary, confidence, created_at FROM agent_action_log ORDER BY created_at DESC LIMIT 5;"
```

## Known Limitations

- The database volume preserves seed dates. To re-anchor `CURRENT_DATE` seed data, recreate the database volume before reviewing date-sensitive questions.
- The seed data has a few differences from the PDF answer key; see `DESIGN.md`.
- `/ask` still uses local LLM calls for routing, SQL generation on open lookup questions, and explanation writing, so first responses can be slow on small machines.
- Natural-language coverage is intentionally small. Some shorthand or catalog/location questions, such as `P2 orders`, `Bay C`, or product-family lookups, may need extra routing terms.
