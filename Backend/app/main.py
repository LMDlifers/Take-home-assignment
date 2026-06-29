"""FastAPI entrypoint for the planning copilot API.

Defines the application, registers HTTP routes, and keeps request handling thin.
Business rules stay in logic.py; database access stays in db.py; agent flow stays
in agent.py.
"""

from fastapi import FastAPI
from fastapi import HTTPException

from app.agent import answer_question
from app import db
from app.logic import add_load_status
from app.logic import simulate_downtime
from app.schemas import AtRiskOrderResponse
from app.schemas import AskRequest
from app.schemas import AskResponse
from app.schemas import DowntimeSimulationRequest
from app.schemas import DowntimeSimulationResponse
from app.schemas import MachineLoadResponse

app = FastAPI(title="AI Planning Copilot")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    if db.check_db():
        return {"status": "ok", "db": "ok"}
    return {"status": "degraded", "db": "error"}


@app.get("/api/v1/machines/load", response_model=list[MachineLoadResponse])
def machine_loads() -> list[dict]:
    rows = db.get_machine_loads()
    return add_load_status(rows)


@app.get("/api/v1/orders/at-risk", response_model=list[AtRiskOrderResponse])
def at_risk_orders() -> list[dict]:
    return db.get_at_risk_orders()


@app.post("/api/v1/simulate/downtime", response_model=DowntimeSimulationResponse)
def simulate_downtime_route(request: DowntimeSimulationRequest) -> dict:
    machine = db.get_machine(request.machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")

    orders = db.get_active_orders_for_machine(request.machine_id)
    return simulate_downtime(machine, orders, request.downtime_hours)


@app.post("/api/v1/ask", response_model=AskResponse)
def ask(request: AskRequest) -> dict:
    return answer_question(request.question)
