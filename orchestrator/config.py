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

    # --- model choice per role ---------------------------------------------
    model_head: str = "gpt-4o-mini"
    model_developer: str = "gpt-4o"
    model_realtime_fixer: str = "gpt-4o-mini"
    model_makefile_tester: str = "gpt-4o-mini"


@lru_cache
def get_settings() -> Settings:
    return Settings()
