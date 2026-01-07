from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATA_DIR: str = "data"
    MAX_SECONDS: int = 3600
    MAX_TRACES: int = 300
    MAX_TOTAL_SAMPLES: int = 50_000_000
    MAX_ESTIMATED_BYTES: int = 300 * 1024 * 1024


settings = Settings()
