"""API request and response schemas.

Defines Pydantic models for endpoint inputs and outputs so /ask and supporting
routes return predictable JSON that matches the assessment contract.
"""

from datetime import date

from pydantic import BaseModel, Field


class MachineLoadResponse(BaseModel):
    machine_id: str
    machine_name: str
    machine_type: str
    capacity_hours_day: float
    available_hours_today: float
    current_status: str
    queued_hours: float
    load_pct: float
    load_status: str


class AtRiskOrderResponse(BaseModel):
    wo_id: str
    product_code: str
    quantity: int
    required_machine: str
    processing_time_hr: float
    priority: int
    due_date: date
    status: str
    available_hours_today: float
    machine_status: str
    risk_reason: str


class DowntimeSimulationRequest(BaseModel):
    machine_id: str
    downtime_hours: float = Field(ge=0)


class AffectedOrderResponse(BaseModel):
    wo_id: str
    product_code: str
    required_machine: str
    processing_time_hr: float
    priority: int
    due_date: date
    status: str
    estimated_extra_delay_hours: float


class DowntimeSimulationResponse(BaseModel):
    machine_id: str
    original_available_hours: float
    new_available_hours: float
    downtime_hours: float
    affected_orders: list[AffectedOrderResponse]


class AskRequest(BaseModel):
    question: str = Field(min_length=1)


class AskResponse(BaseModel):
    question: str
    tool_used: str
    sql_used: str | None
    data: list[dict]
    answer: str
    explanation: str
    confidence: float
    follow_ups: list[str]
