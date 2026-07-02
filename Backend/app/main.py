"""FastAPI entrypoint for the planning copilot API.

Defines the application, registers HTTP routes, and keeps request handling thin.
Business rules stay in logic.py; database access stays in db.py; agent flow stays
in agent.py.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.agent import answer_question
from app import db
from app.logic import calculate_machine_loads
from app.logic import detect_at_risk_orders
from app.logic import simulate_downtime
from app.schemas import AtRiskOrderResponse
from app.schemas import AskRequest
from app.schemas import AskResponse
from app.schemas import DowntimeSimulationRequest
from app.schemas import DowntimeSimulationResponse
from app.schemas import MachineLoadResponse

app = FastAPI(title="AI Planning Copilot")


def frontend_dir() -> Path:
    """Return the frontend folder in local dev or inside the Docker image."""
    main_file = Path(__file__).resolve()
    for base in (main_file.parents[2], main_file.parents[1]):
        candidate = base / "Frontend"
        if candidate.exists():
            return candidate
    return main_file.parents[1] / "Frontend"


FRONTEND_DIR = frontend_dir()
app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
def ui_redirect() -> RedirectResponse:
    return RedirectResponse("/ui/")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    if db.check_db():
        return {"status": "ok", "db": "ok"}
    return {"status": "degraded", "db": "error"}


@app.get("/api/v1/machines/load", response_model=list[MachineLoadResponse])
def machine_loads() -> list[dict]:
    return calculate_machine_loads(db.get_machines(), db.get_open_orders())


@app.get("/api/v1/orders/at-risk", response_model=list[AtRiskOrderResponse])
def at_risk_orders() -> list[dict]:
    return detect_at_risk_orders(db.get_orders_with_machine_state())


@app.post("/api/v1/simulate/downtime", response_model=DowntimeSimulationResponse)
def simulate_downtime_route(request: DowntimeSimulationRequest) -> dict:
    machine = db.get_machine(request.machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")

    orders = db.get_orders_for_simulation(request.machine_id)
    return simulate_downtime(machine, orders, request.downtime_hours)


@app.post("/api/v1/ask", response_model=AskResponse)
def ask(request: AskRequest) -> dict:
    return answer_question(request.question)
