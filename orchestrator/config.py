"""Settings for the orchestrator, read once from the environment.

Mirrors the pattern Collaberry's own backend uses (see
``services/common/collaberry_common/settings.py``): one cached, typed settings
object, loaded from ``.env``, that every module imports rather than reading
``os.environ`` itself.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=("settings_",))

    # --- Collaberry (the target app this system works on) -----------------
    collaberry_gateway_url: str = "http://localhost:8088"
    collaberry_bot_email: str = "agents@collaberry.dev"
    collaberry_bot_password: str = "change-me-please-12345"
    collaberry_bot_display_name: str = "Agent Orchestrator"
    collaberry_board_name: str = "Collaberry"
    # Absolute path to the Collaberry repo checkout — where sub-agents open
    # isolated git worktrees to do real work.
    collaberry_repo_path: str = r"D:\uni\Collaberry"

    # --- 9router (the LLM gateway) -----------------------------------------
    ninerouter_base_url: str = "http://localhost:20128/v1"
    ninerouter_api_key: str = "not-needed-for-local-router"
    # Per-request ceiling. Slow combo routes have been observed taking 120s+
    # (proxy fallbacks inside 9router); anything past this is treated as hung
    # and retried rather than stalling the whole run indefinitely.
    llm_timeout_seconds: float = 180.0
    # Transient-failure retries per call (connection drops, 5xx, combo route
    # churn). 0 disables. Retrying is cheap insurance when a combo's first
    # route is flaky but its fallbacks work.
    llm_max_retries: int = 2
    # Output-token cap. Plans are 3-6 numbered steps; generation time scales
    # with output length, so a bound cuts both latency and spend. Raise it if
    # a role legitimately needs longer replies (e.g. whole-file developer
    # edits use their own higher cap below).
    llm_max_tokens_plan: int = 1500
    llm_max_tokens_code: int = 16000

    # --- planning ------------------------------------------------------------
    # How many cards the head agent plans concurrently. The wall-clock win is
    # roughly linear until 9router's own throughput saturates; 3 is a safe
    # default that turns a 20-minute serial run into ~7 minutes.
    plan_concurrency: int = 3

    # --- model choice per role ---------------------------------------------
    # Point these at a 9router Combo id for graceful degradation (see .env.example).
    model_head: str = "gpt-4o-mini"
    model_developer: str = "gpt-4o"
    model_realtime_fixer: str = "gpt-4o-mini"
    model_makefile_tester: str = "gpt-4o-mini"

    # --- dashboard (phase 5) -----------------------------------------------
    # CLI commands best-effort POST their activity here; the dashboard is
    # entirely optional and never blocks a command if it isn't running.
    dashboard_url: str = "http://127.0.0.1:8800"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8800


@lru_cache
def get_settings() -> Settings:
    return Settings()
