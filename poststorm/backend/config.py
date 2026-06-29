from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Find .env in the project dir first, then the repo root — works regardless of CWD.
# In containers, config comes from injected env vars and no .env file is needed.
_CANDIDATES = [
    Path(__file__).resolve().parents[1] / ".env",  # poststorm/.env
    Path(__file__).resolve().parents[2] / ".env",  # repo-root .env
]
_ENV_PATH = next((str(p) for p in _CANDIDATES if p.exists()), str(_CANDIDATES[0]))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_PATH), extra="ignore")

    cerebras_api_key: str = ""
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    cerebras_model: str = "gemma-4-31b"
    gemini_api_key: str = ""

    # Deployment / security knobs (12-factor: all overridable via env)
    cors_origins: str = "http://localhost:8000"
    log_level: str = "INFO"
    max_batch: int = 48  # hard cap on documents per job (fan-out / cost guard)
    database_url: str = "sqlite:///./data/ledger.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
