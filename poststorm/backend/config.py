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

DEV_JWT_SECRET = "dev-insecure-change-me"  # fixed dev default → reproducible demo; warn if used in prod


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

    # Auth / multi-tenancy (12-factor: all overridable via env)
    jwt_secret: str = DEV_JWT_SECRET
    jwt_ttl_seconds: int = 1800
    seed_tenants: str = "demo:reviewer"   # comma-separated "<tenant>:<role>"
    rate_burst: int = 60                  # token-bucket capacity per tenant
    rate_rps: float = 5.0                 # token-bucket refill rate (tokens/sec)
    admin_bootstrap_key: str = ""         # raw admin key seeded in prod (empty = none)
    demo_mode: bool = True                # enables GET /auth/demo-token for the dashboard


@lru_cache
def get_settings() -> Settings:
    return Settings()
