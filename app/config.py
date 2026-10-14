from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    agent_mode: Literal["gemini", "mock"] = "mock"
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = "gemini-2.5-flash"
    database_path: str = Field(default="fabini.db", validation_alias="DATABASE_PATH")
    max_iterations: int = Field(default=8, ge=1, le=20, validation_alias="MAX_ITERATIONS")
    tool_timeout_seconds: float = Field(
        default=3.0,
        gt=0.0,
        le=30.0,
        validation_alias="TOOL_TIMEOUT_SECONDS",
    )
    app_host: str = Field(default="127.0.0.1", validation_alias="APP_HOST")
    app_port: int = Field(default=8000, ge=1, le=65535, validation_alias="APP_PORT")


@lru_cache
def get_settings() -> Settings:
    return Settings()
