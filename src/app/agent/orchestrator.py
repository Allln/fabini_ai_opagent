from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from app.agent.adapters import AgentModel, GeminiAgentModel, MockAgentModel
from app.agent.policy import is_explicit_action_request
from app.config import APPROVAL_TIMEOUT_MINUTES, Settings
from app.db.repository import SQLiteRepository
from app.errors import ToolExecutionError
from app.models import (
    AgentRunResult,
    AgentStep,
    ApprovalRecord,
    ApprovalStatus,
    ModelTurn,
    RunRequest,
    RunStatus,
    ToolCallRecord,
    ToolCallRequest,
    ToolCallStatus,
    ToolResult,
    utc_now,
)
from app.tools.registry import ToolRegistry


@dataclass
class RunSession:
    result: AgentRunResult
    model: AgentModel
    iterations: int = 0
    next_step: int = 1
    next_event: int = 1
    pending_approval_id: UUID | None = None
    pending_action: ToolCallRequest | None = None
    pending_calls: list[ToolCallRequest] = field(default_factory=list)
    pending_results: list[ToolResult] = field(default_factory=list)
    used_action_keys: set[str] = field(default_factory=set)


class AgentOrchestrator:
    def __init__(
        self,
        settings: Settings,
        repository: SQLiteRepository,
        registry: ToolRegistry,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.registry = registry
        self.sessions: dict[UUID, RunSession] = {}
        self.approval_timeout = timedelta(minutes=APPROVAL_TIMEOUT_MINUTES)

    def start(self, request: RunRequest) -> AgentRunResult:
        mode = request.mode or self.settings.agent_mode
        try:
            model = self._create_model(mode)
        except (RuntimeError, ValueError, ImportError) as exc:
            now = utc_now()
            result = AgentRunResult(
                run_id=uuid4(),
                request=request.request,
                mode=mode,
                status=RunStatus.FAILED,
                summary="The agent could not be started.",
                errors=[str(exc)],
                created_at=now,
                updated_at=now,
            )
            self.repository.create_run(result)
            return result

        now = utc_now()
        result = AgentRunResult(
            run_id=uuid4(),
            request=request.request,
            mode=mode,
            status=RunStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
        session = RunSession(result=result, model=model)
        self.sessions[result.run_id] = session
        self.repository.create_run(result)
        self._event(session, "run_started", {"mode": mode})
        try:
            turn = model.start(request.request)
            return self._advance(session, turn)
        except (RuntimeError, ValueError, ImportError) as exc:
            return self._fail(session, str(exc))

    def resolve_approval(
        self,
        run_id: UUID,
        approval_id: UUID,
        decision: Literal["approve", "reject"],
    ) -> AgentRunResult:
        session = self.sessions.get(run_id)
        if session is None:
            stored = self.repository.get_result(run_id)
            if stored is None:
                raise LookupError(f"Run {run_id} was not found")
            raise RuntimeError("This run is not resumable because its process session is gone")
        approval = next((item for item in session.result.approvals if item.id == approval_id), None)
        if approval is None:
            raise LookupError(f"Approval {approval_id} was not found")
        if approval.status is not ApprovalStatus.PENDING:
            return session.result
        if utc_now() - approval.created_at > self.approval_timeout:
            self._transition_approval(session, approval, ApprovalStatus.EXPIRED)
            session.result.status = RunStatus.BLOCKED
            session.result.summary = "Approval expired; no side effect was executed."
            self._step(session, "approval_expired", session.result.summary)
            return self._save(session)

        if decision == "reject":
            self._transition_approval(session, approval, ApprovalStatus.REJECTED)
            session.result.status = RunStatus.BLOCKED
            session.result.summary = (
                f"Action {approval.tool_name} was rejected; no action was taken."
            )
            self._step(session, "approval_rejected", session.result.summary)
            return self._save(session)

        if session.pending_action is None or session.pending_approval_id != approval_id:
            raise RuntimeError("Approval does not have a pending action")
        self._transition_approval(session, approval, ApprovalStatus.APPROVED)
        action = session.pending_action
        pending_calls = session.pending_calls
        pending_results = session.pending_results
        session.pending_action = None
        session.pending_approval_id = None
        session.pending_calls = []
        session.pending_results = []
        session.used_action_keys.add(self.registry.action_key(action))
        result = self._execute_and_record(session, action)
        if result.ok:
            self._transition_approval(session, approval, ApprovalStatus.EXECUTED)
        else:
            self._transition_approval(session, approval, ApprovalStatus.FAILED)
        pending_results.append(result)
        next_turn = self._process_tool_calls(session, pending_calls, pending_results)
        if next_turn is None:
            return self._save(session)
        try:
            return self._advance(session, next_turn)
        except (RuntimeError, ValueError, ImportError) as exc:
            return self._fail(session, str(exc))

    def get_result(self, run_id: UUID) -> AgentRunResult | None:
        session = self.sessions.get(run_id)
        if session is not None:
            return session.result
        return self.repository.get_result(run_id)

    def _create_model(self, mode: str) -> AgentModel:
        if mode == "mock":
            return MockAgentModel(self.registry)
        if mode == "gemini":
            if not self.settings.gemini_api_key:
                raise ValueError(
                    "GEMINI_API_KEY is required for Gemini mode; select mock mode instead."
                )
            return GeminiAgentModel(
                self.settings.gemini_api_key,
                self.settings.gemini_model,
                self.registry,
            )
        raise ValueError(f"Unsupported agent mode: {mode}")

    def _advance(self, session: RunSession, turn: ModelTurn) -> AgentRunResult:
        while True:
            session.iterations += 1
            if session.iterations > self.settings.max_iterations:
                session.result.status = RunStatus.LIMIT_REACHED
                session.result.summary = "The agent stopped at its iteration limit."
                self._step(session, "limit_reached", session.result.summary)
                return self._save(session)

            if turn.text:
                self._step(session, "model", turn.text)
            if not turn.tool_calls:
                session.result.status = (
                    RunStatus.FAILED if session.result.errors else RunStatus.COMPLETED
                )
                session.result.summary = turn.text or "The agent completed without a summary."
                return self._save(session)

            next_turn = self._process_tool_calls(session, turn.tool_calls, [])
            if next_turn is None:
                return self._save(session)
            turn = next_turn

    def _process_tool_calls(
        self,
        session: RunSession,
        calls: list[ToolCallRequest],
        results: list[ToolResult],
    ) -> ModelTurn | None:
        for index, raw_call in enumerate(calls):
            try:
                definition, arguments = self.registry.validate(raw_call)
            except ToolExecutionError as exc:
                result = ToolResult(
                    name=raw_call.name,
                    call_id=raw_call.call_id,
                    ok=False,
                    error=str(exc),
                    status=ToolCallStatus.FAILED,
                )
                results.append(result)
                self._record_tool_failure(session, raw_call, result)
                continue

            call = raw_call.model_copy(update={"arguments": arguments})
            action_key = self.registry.action_key(call)
            if definition.side_effecting and not is_explicit_action_request(
                session.result.request,
                call.name,
            ):
                result = ToolResult(
                    name=call.name,
                    call_id=call.call_id,
                    ok=False,
                    error=(
                        f"{call.name} was rejected because the user did not explicitly "
                        "request this action"
                    ),
                    status=ToolCallStatus.REJECTED,
                )
                results.append(result)
                self._record_tool_failure(session, call, result)
                continue
            if definition.side_effecting and action_key not in session.used_action_keys:
                approval = ApprovalRecord(
                    id=uuid4(),
                    action_key=action_key,
                    tool_name=call.name,
                    arguments=call.arguments,
                    status=ApprovalStatus.PENDING,
                    created_at=utc_now(),
                )
                session.result.approvals.append(approval)
                session.pending_approval_id = approval.id
                session.pending_action = call
                session.pending_calls = calls[index + 1 :]
                session.pending_results = results
                session.result.proposed_actions.append(f"{call.name} with {call.arguments}")
                session.result.status = RunStatus.WAITING_APPROVAL
                self.repository.create_approval(approval, session.result.run_id)
                self._step(
                    session,
                    "approval_requested",
                    f"Approval required before {call.name}.",
                    {"approval_id": str(approval.id), "tool": call.name},
                )
                return None
            if definition.side_effecting:
                result = ToolResult(
                    name=call.name,
                    call_id=call.call_id,
                    ok=False,
                    error="Duplicate side effect blocked by idempotency policy",
                    status=ToolCallStatus.REJECTED,
                )
                results.append(result)
                self._record_tool_failure(session, call, result)
            else:
                results.append(self._execute_and_record(session, call))

        return session.model.submit_tool_results(results)

    def _execute_and_record(self, session: RunSession, call: ToolCallRequest) -> ToolResult:
        started = utc_now()
        result = self.registry.execute(call)
        finished = utc_now()
        record = ToolCallRecord(
            call_id=call.call_id,
            sequence=len(session.result.tool_calls) + 1,
            name=call.name,
            arguments=call.arguments,
            status=result.status
            or (ToolCallStatus.SUCCEEDED if result.ok else ToolCallStatus.FAILED),
            result=result.result,
            error=result.error,
            started_at=started,
            finished_at=finished,
        )
        session.result.tool_calls.append(record)
        self.repository.record_tool_call(session.result.run_id, record)
        self._add_finding(session, call.name, result)
        self._step(
            session,
            "tool_call",
            f"{call.name} {'succeeded' if result.ok else 'failed'}.",
            {"tool": call.name, "ok": result.ok, "error": result.error},
        )
        if not result.ok and result.error:
            session.result.errors.append(result.error)
        return result

    def _record_tool_failure(
        self,
        session: RunSession,
        call: ToolCallRequest,
        result: ToolResult,
    ) -> None:
        now = utc_now()
        record = ToolCallRecord(
            call_id=call.call_id,
            sequence=len(session.result.tool_calls) + 1,
            name=call.name,
            arguments=call.arguments,
            status=result.status or ToolCallStatus.FAILED,
            error=result.error,
            started_at=now,
            finished_at=now,
        )
        session.result.tool_calls.append(record)
        self.repository.record_tool_call(session.result.run_id, record)
        session.result.errors.append(result.error or "Unknown tool failure")

    def _add_finding(
        self,
        session: RunSession,
        tool_name: str,
        result: ToolResult,
    ) -> None:
        if not result.ok or result.result is None:
            return
        if tool_name == "get_fulfillment":
            status = result.result.get("status", "unknown")
            reason = result.result.get("reason")
            finding = f"Fulfillment status is {status}."
            if reason:
                finding += f" Reason: {reason}."
        elif tool_name == "get_sync_logs":
            entries = result.result.get("entries", [])
            if not entries:
                return
            latest = entries[0]
            finding = f"Latest synchronization event: {latest.get('message', 'unknown')}."
        else:
            return
        if finding not in session.result.findings:
            session.result.findings.append(finding)

    def _fail(self, session: RunSession, message: str) -> AgentRunResult:
        session.result.status = RunStatus.FAILED
        session.result.errors.append(message)
        session.result.summary = "The agent could not complete the request."
        self._step(session, "run_failed", message)
        return self._save(session)

    def _transition_approval(
        self,
        session: RunSession,
        approval: ApprovalRecord,
        status: ApprovalStatus,
    ) -> None:
        now = utc_now()
        previous = approval.status
        if not self.repository.resolve_approval(
            approval.id,
            expected_status=previous,
            status=status,
            resolved_at=now,
        ):
            raise RuntimeError("Approval state changed before it could be resolved")
        approval.status = status
        approval.resolved_at = now
        self._event(
            session,
            "approval_resolved",
            {"approval_id": str(approval.id), "from": previous.value, "to": status.value},
        )

    def _step(
        self,
        session: RunSession,
        kind: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        step = AgentStep(
            sequence=session.next_step,
            kind=kind,
            message=message,
            metadata=metadata or {},
            created_at=utc_now(),
        )
        session.next_step += 1
        session.result.steps.append(step)
        self._event(session, kind, {"message": message, **step.metadata})

    def _event(
        self,
        session: RunSession,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        self.repository.append_event(
            session.result.run_id,
            sequence=session.next_event,
            event_type=event_type,
            payload=payload,
        )
        session.next_event += 1

    def _save(self, session: RunSession) -> AgentRunResult:
        session.result.updated_at = utc_now()
        self.repository.save_result(session.result)
        return session.result
