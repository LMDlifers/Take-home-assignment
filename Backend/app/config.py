"""Application configuration.

Centralizes environment-backed settings so model choices, service URLs, and
database connection details are visible in one place.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/scheduling_db",
    )
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    llm_router_model: str = os.getenv("LLM_ROUTER_MODEL", "qwen2.5:3b")
    llm_sql_model: str = os.getenv("LLM_SQL_MODEL", os.getenv("LLM_MODEL", "qwen2.5:3b"))
    llm_explanation_model: str = os.getenv("LLM_EXPLANATION_MODEL", os.getenv("LLM_SQL_MODEL", "qwen2.5:3b"))
    llm_router_timeout_seconds: int = int(os.getenv("LLM_ROUTER_TIMEOUT_SECONDS", "30"))
    llm_sql_timeout_seconds: int = int(os.getenv("LLM_SQL_TIMEOUT_SECONDS", "30"))
    llm_explanation_timeout_seconds: int = int(os.getenv("LLM_EXPLANATION_TIMEOUT_SECONDS", "30"))
    llm_router_temperature: float = float(os.getenv("LLM_ROUTER_TEMPERATURE", "0"))
    llm_router_num_predict: int = int(os.getenv("LLM_ROUTER_NUM_PREDICT", "160"))
    llm_sql_temperature: float = float(os.getenv("LLM_SQL_TEMPERATURE", "0"))
    llm_sql_num_predict: int = int(os.getenv("LLM_SQL_NUM_PREDICT", "200"))
    llm_explanation_temperature: float = float(os.getenv("LLM_EXPLANATION_TEMPERATURE", "0.3"))
    llm_explanation_num_predict: int = int(os.getenv("LLM_EXPLANATION_NUM_PREDICT", "220"))


settings = Settings()
