from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "BAEUGO API"
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://baeugo:baeugo@localhost:5433/baeugo"

    jwt_secret_key: str = "change-me"
    jwt_expire_minutes: int = 60 * 24

    cors_origins: list[str] = ["http://localhost:3000"]
    media_root: str = "/data/media"
    gemini_api_key: str = ""
    gemini_model: str = ""
    gemini_review_model: str = ""
    whisper_model_dir: str = "/data/models/whisper"
    whisper_device: str = "cpu"
    whisper_cpu_threads: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
