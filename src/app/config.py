from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_MAX_ITERATIONS = 8
DEFAULT_TOOL_TIMEOUT_SECONDS = 3.0
DEFAULT_APP_HOST = "127.0.0.1"
DEFAULT_APP_PORT = 8000
APPROVAL_TIMEOUT_MINUTES = 15
READ_ONLY_TOOL_MAX_ATTEMPTS = 2
GEMINI_TEMPERATURE = 0
GEMINI_SYSTEM_INSTRUCTION = (
    "You are a cautious operations agent. Investigate using read-only tools first. "
    "Use retry_sync or create_ticket only when the user explicitly requests that action; "
    "the host application will gate it with approval. Never invent tool results. "
    "Give a concise final answer."
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )
    agent_mode: Literal["gemini", "mock"] = "mock"
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = DEFAULT_GEMINI_MODEL
    database_path: str = Field(default="fabini.db", validation_alias="DATABASE_PATH")
    max_iterations: int = Field(
        default=DEFAULT_MAX_ITERATIONS,
        ge=1,
        le=20,
        validation_alias="MAX_ITERATIONS",
    )
    tool_timeout_seconds: float = Field(
        default=DEFAULT_TOOL_TIMEOUT_SECONDS,
        gt=0.0,
        le=30.0,
        validation_alias="TOOL_TIMEOUT_SECONDS",
    )
    app_host: str = Field(default=DEFAULT_APP_HOST, validation_alias="APP_HOST")
    app_port: int = Field(default=DEFAULT_APP_PORT, ge=1, le=65535, validation_alias="APP_PORT")


@lru_cache
def get_settings() -> Settings:
    return Settings()
