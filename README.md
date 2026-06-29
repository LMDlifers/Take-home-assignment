# AI Planning Copilot for Shop Floor Scheduling

Backend-only FastAPI project for the A*Star take-home assignment.

Current status: deterministic backend endpoints and the first GenAI `/ask`
endpoint are implemented. The app can be containerised with PostgreSQL and
Ollama, query seeded scheduling data, and use Qwen to generate read-only SQL.

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

`/api/v1/ask` uses Ollama with `qwen2.5:3b` to generate read-only SQL and
explain returned data. Recommendation logic and audit logging are not
implemented yet.

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
      planning_copilot.yaml
    tests/
      test_health.py
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

## Prompt And Schema Context

Prompt and schema context live in:

```text
Backend/prompts/planning_copilot.yaml
```

The YAML gives Qwen table purposes, column meanings, useful views, and business
rules. It is used only to guide SQL generation and explanations; SQL execution
is still guarded by Python safety checks.

## Local Tests

After installing dependencies:

```powershell
python -m pytest Backend\tests
```
