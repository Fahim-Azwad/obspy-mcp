"""Server configuration.

Settings are loaded from environment variables and optionally a local `.env` file.
We ignore extra env vars so the repo can share one `.env` for both server + agent
keys (e.g., `GOOGLE_API_KEY`) without crashing the server.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Pydantic v2 settings configuration.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Output/data directory used by MCP tools.
    DATA_DIR: str = "data"

    # Safety limits for waveform requests.
    MAX_SECONDS: int = 3600
    MAX_TRACES: int = 300
    MAX_TOTAL_SAMPLES: int = 50_000_000
    MAX_ESTIMATED_BYTES: int = 300 * 1024 * 1024


# Singleton settings used throughout the server.
settings = Settings()
