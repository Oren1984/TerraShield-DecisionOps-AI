"""Runtime configuration, read from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TS_", env_file=None, extra="ignore")

    database_url: str = "postgresql+psycopg://terrashield:terrashield@localhost:5432/terrashield"
    scenario_dir: str = str(_REPO_ROOT / "scenarios")
    log_level: str = "INFO"

    # Sessions
    session_ttl_hours: float = 8.0
    session_idle_minutes: float = 60.0
    demo_session_ttl_hours: float = 2.0
    cookie_secure: bool = False
    login_max_failures: int = 5
    login_lockout_minutes: float = 5.0

    # Model provider (mock | local). Mock is the default for a clean clone.
    default_mode: str = "mock"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_embed_model: str = "nomic-embed-text"
    agent_timeout_s: float = 300.0
    agent_max_tokens: int = 900
    agent_context_tokens: int = 6144
    agent_max_retries: int = 1
    max_revision_rounds: int = 1
    run_max_seconds: float = 3600.0

    # Worker
    worker_poll_s: float = 1.0
    lease_seconds: float = 60.0
    job_max_attempts: int = 3
    sim_step_delay_ms: int = 350

    # Planning
    solver_time_limit_s: float = 10.0
    max_replan_iterations: int = 3
    sensitivity_samples: int = 16

    # Retrieval
    retrieval_top_k: int = 4
    retrieval_min_similarity: float = 0.2


settings = Settings()
