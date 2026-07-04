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
    llm_router_model: str = os.getenv("LLM_ROUTER_MODEL", "deepseek-r1:1.5b")
    llm_sql_model: str = os.getenv("LLM_SQL_MODEL", os.getenv("LLM_MODEL", "qwen2.5:3b"))
    llm_judge_model: str = os.getenv("LLM_JUDGE_MODEL", os.getenv("LLM_SQL_MODEL", "qwen2.5:3b"))
    llm_router_timeout_seconds: int = int(os.getenv("LLM_ROUTER_TIMEOUT_SECONDS", "30"))
    llm_sql_timeout_seconds: int = int(os.getenv("LLM_SQL_TIMEOUT_SECONDS", "30"))
    llm_judge_timeout_seconds: int = int(os.getenv("LLM_JUDGE_TIMEOUT_SECONDS", "30"))


settings = Settings()
