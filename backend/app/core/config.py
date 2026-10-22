from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "BAEUGO API"
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://baeugo:baeugo@localhost:5432/baeugo"

    jwt_secret_key: str = "change-me"
    jwt_expire_minutes: int = 60 * 24

    cors_origins: list[str] = ["http://localhost:3000"]
    media_root: str = "/data/media"


@lru_cache
def get_settings() -> Settings:
    return Settings()
