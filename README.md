# AI Planning Copilot for Shop Floor Scheduling

Backend-only FastAPI project for the A*Star take-home assignment.

Current status: Day 1 foundation is implemented. The app can be containerised
with PostgreSQL and Ollama, and the health endpoint checks whether the API can
reach the database.

## What Exists So Far

- FastAPI backend under `Backend/app/`
- PostgreSQL seed data in `Data/seed.sql`
- Docker Compose setup for:
  - `api` - FastAPI service
  - `db` - PostgreSQL database
  - `ollama` - local LLM service for later phases
- Health endpoint:
  - `GET /api/v1/health`
- Basic health tests in `Backend/tests/`

Agent routing, scheduling logic, `/api/v1/ask`, and LLM explanations are not
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

## Database Seed Check

After the containers are running:

```powershell
docker exec -it scheduling_db psql -U postgres -d scheduling_db -c "SELECT COUNT(*) FROM work_orders;"
```

The count should be greater than `0`.

## Local Tests

After installing dependencies:

```powershell
python -m pytest Backend\tests
```
