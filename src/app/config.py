from functools import lru_cache
from ipaddress import ip_address
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_MAX_ITERATIONS = 8
DEFAULT_TOOL_TIMEOUT_SECONDS = 10.0
DEFAULT_APP_HOST = "127.0.0.1"
DEFAULT_APP_PORT = 8000
APPROVAL_TIMEOUT_MINUTES = 15
READ_ONLY_TOOL_MAX_ATTEMPTS = 2
ORDER_AUDIT_RECORD_LIMIT = 5
GEMINI_TEMPERATURE = 0
GEMINI_SYSTEM_INSTRUCTION = (
    "You are a cautious operations agent. Investigate using read-only tools first. "
    "When an order ID is being investigated, use get_order_audit to include related "
    "local audit records in the final answer. "
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
    api_token: SecretStr | None = Field(default=None, validation_alias="API_TOKEN")

    @model_validator(mode="after")
    def validate_network_access(self) -> "Settings":
        host = self.app_host.strip().lower()
        try:
            loopback = ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"

        if not loopback and self.api_token is None:
            raise ValueError("API_TOKEN is required when APP_HOST is not loopback")
        if self.api_token is not None and len(self.api_token.get_secret_value()) < 32:
            raise ValueError("API_TOKEN must contain at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
