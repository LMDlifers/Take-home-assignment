"""FastAPI entrypoint for the planning copilot API.

Defines the application, registers HTTP routes, and keeps request handling thin.
Business rules stay in logic.py; database access stays in db.py; agent flow stays
in agent.py.
"""

from fastapi import FastAPI

from app.db import check_db

app = FastAPI(title="AI Planning Copilot")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    if check_db():
        return {"status": "ok", "db": "ok"}
    return {"status": "degraded", "db": "error"}
