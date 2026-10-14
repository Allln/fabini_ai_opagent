from app.agent.orchestrator import AgentOrchestrator
from app.config import Settings
from app.db.repository import SQLiteRepository
from app.models import ApprovalStatus, RunRequest, RunStatus, ToolCallRequest, ToolCallStatus
from app.tools.registry import ToolRegistry


def build_orchestrator(max_iterations: int = 8) -> AgentOrchestrator:
    settings = Settings(
        agent_mode="mock",
        database_path=":memory:",
        max_iterations=max_iterations,
    )
    repository = SQLiteRepository(":memory:")
    return AgentOrchestrator(
        settings,
        repository,
        ToolRegistry(settings.tool_timeout_seconds, audit_query=repository.get_order_audit),
    )


def test_successful_diagnosis_uses_multiple_read_only_tools() -> None:
    orchestrator = build_orchestrator()

    result = orchestrator.start(
        RunRequest(request="Find out why order 100245 was not shipped and suggest next steps.")
    )

    assert result.status is RunStatus.COMPLETED
    assert [call.name for call in result.tool_calls] == [
        "get_order",
        "get_payment",
        "get_fulfillment",
        "get_sync_logs",
        "get_order_audit",
    ]
    assert "synchronization" in (result.summary or "").lower()
    assert result.findings == [
        "Fulfillment status is blocked. Reason: sync_error.",
        "Latest synchronization event: Warehouse API timeout.",
    ]
    assert all(call.status is ToolCallStatus.SUCCEEDED for call in result.tool_calls)


def test_side_effect_pauses_then_executes_after_approval() -> None:
    orchestrator = build_orchestrator()

    waiting = orchestrator.start(
        RunRequest(request="Investigate order 100245 and retry sync if it is blocked.")
    )

    assert waiting.status is RunStatus.WAITING_APPROVAL
    approval = waiting.approvals[0]
    assert approval.status is ApprovalStatus.PENDING
    assert "retry_sync" not in [call.name for call in waiting.tool_calls]

    completed = orchestrator.resolve_approval(
        waiting.run_id,
        approval.id,
        "approve",
    )

    assert completed.status is RunStatus.COMPLETED
    assert completed.approvals[0].status is ApprovalStatus.EXECUTED
    assert [call.name for call in completed.tool_calls][-1] == "retry_sync"


def test_healthy_order_returns_shipped_findings_without_approval() -> None:
    orchestrator = build_orchestrator()

    result = orchestrator.start(RunRequest(request="Investigate order 100247."))

    assert result.status is RunStatus.COMPLETED
    assert result.approvals == []
    assert result.findings == [
        "Fulfillment status is shipped.",
        "Latest synchronization event: Shipment synced.",
    ]
    assert [call.name for call in result.tool_calls] == [
        "get_order",
        "get_payment",
        "get_fulfillment",
        "get_sync_logs",
        "get_order_audit",
    ]


def test_ticket_creation_requires_approval_then_executes() -> None:
    orchestrator = build_orchestrator()

    waiting = orchestrator.start(
        RunRequest(request="Investigate order 100245 and create an incident ticket.")
    )

    assert waiting.status is RunStatus.WAITING_APPROVAL
    assert waiting.approvals[0].tool_name == "create_ticket"

    completed = orchestrator.resolve_approval(
        waiting.run_id,
        waiting.approvals[0].id,
        "approve",
    )

    assert completed.status is RunStatus.COMPLETED
    assert completed.approvals[0].status is ApprovalStatus.EXECUTED
    assert completed.tool_calls[-1].name == "create_ticket"
    assert completed.tool_calls[-1].result["status"] == "created"


def test_request_without_order_id_stops_before_tool_calling() -> None:
    orchestrator = build_orchestrator()

    result = orchestrator.start(RunRequest(request="Show me the current warehouse status."))

    assert result.status is RunStatus.COMPLETED
    assert result.tool_calls == []
    assert "numeric order ID" in (result.summary or "")


def test_rejected_side_effect_is_blocked_without_tool_execution() -> None:
    orchestrator = build_orchestrator()
    waiting = orchestrator.start(
        RunRequest(request="Investigate order 100245 and retry sync if it is blocked.")
    )

    result = orchestrator.resolve_approval(
        waiting.run_id,
        waiting.approvals[0].id,
        "reject",
    )

    assert result.status is RunStatus.BLOCKED
    assert result.approvals[0].status is ApprovalStatus.REJECTED
    assert "retry_sync" not in [call.name for call in result.tool_calls]


def test_tool_failure_is_recorded_and_returns_safe_result() -> None:
    orchestrator = build_orchestrator()

    result = orchestrator.start(RunRequest(request="Investigate order 999999."))

    assert result.status is RunStatus.COMPLETED
    assert result.tool_calls[0].status is ToolCallStatus.FAILED
    assert "not found" in (result.tool_calls[0].error or "")
    assert result.errors
    assert "No operational action" in (result.summary or "")


def test_iteration_limit_has_distinct_terminal_status() -> None:
    orchestrator = build_orchestrator(max_iterations=2)

    result = orchestrator.start(RunRequest(request="Investigate order 100245."))

    assert result.status is RunStatus.LIMIT_REACHED
    assert result.summary == "The agent stopped at its iteration limit."


def test_duplicate_approval_is_idempotent() -> None:
    orchestrator = build_orchestrator()
    waiting = orchestrator.start(
        RunRequest(request="Investigate order 100245 and retry sync if it is blocked.")
    )
    approval = waiting.approvals[0]

    first = orchestrator.resolve_approval(waiting.run_id, approval.id, "approve")
    second = orchestrator.resolve_approval(waiting.run_id, approval.id, "approve")

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert [call.name for call in second.tool_calls].count("retry_sync") == 1


def test_order_audit_tool_reads_previous_calls_and_approvals(tmp_path) -> None:
    database_path = str(tmp_path / "audit.db")
    settings = Settings(agent_mode="mock", database_path=database_path)
    repository = SQLiteRepository(database_path)
    orchestrator = AgentOrchestrator(
        settings,
        repository,
        ToolRegistry(settings.tool_timeout_seconds, audit_query=repository.get_order_audit),
    )

    first = orchestrator.start(
        RunRequest(request="Investigate order 100245 and retry sync if it is blocked.")
    )
    orchestrator.resolve_approval(first.run_id, first.approvals[0].id, "approve")

    second = orchestrator.start(RunRequest(request="Investigate order 100245."))

    assert "Local SQLite audit for order 100245" in (second.summary or "")
    audit_call = next(call for call in second.tool_calls if call.name == "get_order_audit")
    assert any(
        record["source"] == "approvals"
        and record["tool_name"] == "retry_sync"
        and record["status"] == "executed"
        for record in (audit_call.result or {}).get("records", [])
    )


def test_tool_registry_rejects_unknown_or_malformed_calls() -> None:
    registry = ToolRegistry()

    unknown = registry.execute(
        ToolCallRequest(name="delete_order", arguments={"order_id": "100245"})
    )
    malformed = registry.execute(
        ToolCallRequest(name="get_order", arguments={"order_id": "not-an-id"})
    )

    assert not unknown.ok
    assert "Unknown tool" in (unknown.error or "")
    assert not malformed.ok
    assert "Invalid arguments" in (malformed.error or "")
