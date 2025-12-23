
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATA_DIR: str = "data"
    MAX_SECONDS: int = 3600
    MAX_TRACES: int = 300
    MAX_TOTAL_SAMPLES: int = 50_000_000
    MAX_ESTIMATED_BYTES: int = 300 * 1024 * 1024

    class Config:
        env_file = ".env"

settings = Settings()
