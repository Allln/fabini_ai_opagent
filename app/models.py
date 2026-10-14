from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    LIMIT_REACHED = "limit_reached"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    FAILED = "failed"


class ToolCallStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"


class RunRequest(BaseModel):
    request: str = Field(min_length=3, max_length=2000)
    mode: str | None = Field(default=None, pattern="^(mock|gemini)$")

    @field_validator("request")
    @classmethod
    def request_must_contain_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("request must contain non-whitespace text")
        return normalized


class ToolCallRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: str(uuid4()))


class ToolResult(BaseModel):
    name: str
    call_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    status: ToolCallStatus | None = None


class ModelTurn(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


class ToolCallRecord(BaseModel):
    call_id: str
    sequence: int
    name: str
    arguments: dict[str, Any]
    status: ToolCallStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime


class ApprovalRecord(BaseModel):
    id: UUID
    action_key: str
    tool_name: str
    arguments: dict[str, Any]
    status: ApprovalStatus
    created_at: datetime
    resolved_at: datetime | None = None


class AgentStep(BaseModel):
    sequence: int
    kind: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AgentRunResult(BaseModel):
    run_id: UUID
    request: str
    mode: str
    status: RunStatus
    summary: str | None = None
    findings: list[str] = Field(default_factory=list)
    proposed_actions: list[str] = Field(default_factory=list)
    steps: list[AgentStep] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
