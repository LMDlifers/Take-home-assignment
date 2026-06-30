# AI Planning Copilot for Shop Floor Scheduling

Backend-only FastAPI project for the A*Star take-home assignment.

Current status: deterministic backend endpoints and the `/api/v1/ask` tool
router are implemented. The app can be containerised with PostgreSQL and
Ollama, query seeded scheduling data, run deterministic scheduling tools, and
use Qwen to generate read-only SQL for open-ended SQL questions.

## What Exists So Far

- FastAPI backend under `Backend/app/`
- PostgreSQL seed data in `Data/seed.sql`
- Docker Compose setup for:
  - `api` - FastAPI service
  - `db` - PostgreSQL database
  - `ollama` - local LLM service for later phases
- Health endpoint:
  - `GET /api/v1/health`
- Deterministic scheduling endpoints:
  - `GET /api/v1/machines/load`
  - `GET /api/v1/orders/at-risk`
  - `POST /api/v1/simulate/downtime`
- GenAI endpoint:
  - `POST /api/v1/ask`
- Basic health tests in `Backend/tests/`

`/api/v1/ask` routes questions to simple tools:

- `run_sql` - Qwen generates one safe PostgreSQL `SELECT` query.
- `check_load` - deterministic query against `v_machine_load`.
- `get_priority` - deterministic query against `v_priority_queue`.
- `simulate_downtime` - deterministic Python simulation after reading machine/order data.
- `recommend` - deterministic recommendations from load and risk data.
- `refuse` - out-of-scope questions.

Qwen is used for SQL generation only in the `run_sql` path. Other tools use
deterministic code first, then Qwen may explain the returned data. If Qwen is
unavailable during explanation, the API returns a simple fallback summary.
Each `/api/v1/ask` request writes a best-effort audit row to `agent_action_log`.

## Project Structure

```text
Take-home-assignment/
  docker-compose.yml
  requirements.txt
  Data/
    seed.sql
  Backend/
    Dockerfile
    app/
      main.py
      db.py
      agent.py
      logic.py
      schemas.py
    prompts/
      schema_context.yaml
      sql_generation.yaml
      explanation.yaml
    tests/
      conftest.py
      test_health.py
      test_logic.py
      test_routes.py
      test_agent.py
      test_db.py
```

## Run With Docker

From the project root:

```powershell
docker-compose up --build
```

If your Docker version uses the newer command style:

```powershell
docker compose up --build
```

The API should be available at:

```text
http://localhost:8000
```

Swagger docs:

```text
http://localhost:8000/docs
```

## Health Check

```powershell
curl http://localhost:8000/api/v1/health
```

Expected response when the API and database are both reachable:

```json
{"status":"ok","db":"ok"}
```

If the API is running but PostgreSQL is unavailable:

```json
{"status":"degraded","db":"error"}
```

## Day 2 Endpoints

Machine load:

```powershell
curl http://localhost:8000/api/v1/machines/load
```

At-risk orders:

```powershell
curl http://localhost:8000/api/v1/orders/at-risk
```

Downtime simulation:

```powershell
curl -X POST http://localhost:8000/api/v1/simulate/downtime -H "Content-Type: application/json" -d "{\"machine_id\":\"M2\",\"downtime_hours\":4}"
```

In PowerShell, `curl` is often an alias for `Invoke-WebRequest`. This version
avoids the header parsing issue:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/simulate/downtime" -ContentType "application/json" -Body '{"machine_id":"M2","downtime_hours":4}'
```

## Database Seed Check

After the containers are running:

```powershell
docker exec -it scheduling_db psql -U postgres -d scheduling_db -c "SELECT COUNT(*) FROM work_orders;"
```

The count should be greater than `0`.

## Pull The Local LLM

After the Ollama container is running, pull the model once:

```powershell
docker exec -it scheduling_ollama ollama pull qwen2.5:3b
```

Then test `/ask`:

```powershell
curl -X POST http://localhost:8000/api/v1/ask -H "Content-Type: application/json" -d "{\"question\":\"Which work orders are delayed?\"}"
```

PowerShell-safe version:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/ask" -ContentType "application/json" -Body '{"question":"Which machines are overloaded?"}'
```

Useful assessment questions:

```text
Which work orders are delayed?
Which machines are overloaded?
Why is WO-1003 at risk?
What happens if M2 is down for 4 extra hours?
Show high-priority orders due this week.
Recommend actions to reduce delays.
```

## Audit Logging

`/api/v1/ask` writes one audit row after each response is built. The log records:

- `session_id`
- `action_type`
- `input_question`
- `sql_generated`
- `result_summary`
- `confidence`

Logging is best-effort. If the audit insert fails, the API still returns the
normal response.

Inspect recent logs:

```powershell
docker exec -it scheduling_db psql -U postgres -d scheduling_db -c "SELECT action_type, input_question, sql_generated, result_summary, confidence, created_at FROM agent_action_log ORDER BY created_at DESC LIMIT 5;"
```

## Prompt Templates

Prompt templates live in:

```text
Backend/prompts/
```

- `schema_context.yaml` contains table meanings, known values, views, and business rules.
- `sql_generation.yaml` contains Qwen SQL-generation role and safety instructions.
- `explanation.yaml` contains Qwen explanation style and fallback wording.

The YAML files guide Qwen only. SQL execution safety is still enforced in Python
by `db.is_safe_select()`.

Blueprint configuration notes:

- Prompt context node: loads `schema_context.yaml`; context only, no SQL enforcement.
- SQL generation node: loads `sql_generation.yaml`; Qwen generates candidate `SELECT` SQL for the `run_sql` path only.
- Explanation node: loads `explanation.yaml`; Qwen explains retrieved rows, with fallback wording if needed.
- SQL safety node: Python rejects non-SELECT SQL, destructive SQL, and multiple statements before execution.
- Known values: enum/category-like values live in YAML; live operational records stay in PostgreSQL.

## Local Tests

After installing dependencies:

```powershell
python -m pytest Backend\tests
```

Inside the API container after rebuilding:

```powershell
docker exec -it scheduling_api pytest tests
```
