from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent.orchestrator import AgentOrchestrator
from app.config import Settings, get_settings
from app.db.repository import SQLiteRepository
from app.models import AgentRunResult, RunRequest
from app.tools.registry import ToolRegistry


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]


def create_app(
    settings: Settings | None = None,
    repository: SQLiteRepository | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_repository = repository or SQLiteRepository(resolved_settings.database_path)
    orchestrator = AgentOrchestrator(
        resolved_settings,
        resolved_repository,
        ToolRegistry(
            resolved_settings.tool_timeout_seconds,
            audit_query=resolved_repository.get_order_audit,
        ),
    )
    application = FastAPI(
        title="FABINI AI Operations Agent",
        version="0.1.0",
        description="Bounded operations diagnosis and approval POC.",
    )
    application.state.orchestrator = orchestrator

    def require_api_access(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        configured_token = resolved_settings.api_token
        if configured_token is None:
            return
        scheme, _, presented_token = (authorization or "").partition(" ")
        if (
            scheme.lower() != "bearer"
            or not secrets.compare_digest(
                presented_token,
                configured_token.get_secret_value(),
            )
        ):
            raise HTTPException(
                status_code=401,
                detail="A valid bearer token is required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": resolved_settings.agent_mode}

    @application.post("/api/runs", response_model=AgentRunResult)
    def start_run(
        payload: RunRequest,
        _: None = Depends(require_api_access),
    ) -> AgentRunResult:
        return orchestrator.start(payload)

    @application.get("/api/runs/{run_id}", response_model=AgentRunResult)
    def get_run(
        run_id: UUID,
        _: None = Depends(require_api_access),
    ) -> AgentRunResult:
        result = orchestrator.get_result(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return result

    @application.post(
        "/api/runs/{run_id}/approvals/{approval_id}",
        response_model=AgentRunResult,
    )
    def resolve_approval(
        run_id: UUID,
        approval_id: UUID,
        payload: ApprovalDecision,
        _: None = Depends(require_api_access),
    ) -> AgentRunResult:
        try:
            return orchestrator.resolve_approval(run_id, approval_id, payload.decision)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    static_directory = Path(__file__).resolve().parents[1] / "static"
    application.mount("/", StaticFiles(directory=static_directory, html=True), name="static")
    return application


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=False)


if __name__ == "__main__":
    run()
