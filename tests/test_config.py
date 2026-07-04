from app.config import settings


def test_settings_expose_core_runtime_config() -> None:
    assert settings.database_url
    assert settings.ollama_base_url
    assert settings.llm_router_model
    assert settings.llm_sql_model
    assert settings.llm_judge_model
    assert settings.llm_router_timeout_seconds > 0
    assert settings.llm_sql_timeout_seconds > 0
    assert settings.llm_judge_timeout_seconds > 0
