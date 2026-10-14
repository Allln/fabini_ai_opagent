from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.config import READ_ONLY_TOOL_MAX_ATTEMPTS
from app.errors import ToolExecutionError
from app.models import ToolCallRequest, ToolCallStatus, ToolResult


class OrderIdInput(BaseModel):
    order_id: str = Field(pattern=r"^\d{4,20}$")


class TicketInput(BaseModel):
    description: str = Field(min_length=10, max_length=1000)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    side_effecting: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.arguments_model.model_json_schema(),
        }


# dummy data
MOCK_ORDERS: dict[str, dict[str, Any]] = {
    "100245": {
        "order_id": "100245",
        "status": "paid",
        "items": 2,
        "warehouse": "CZ-01",
    },
    "100246": {
        "order_id": "100246",
        "status": "paid",
        "items": 1,
        "warehouse": "CZ-02",
    },
    "100247": {
        "order_id": "100247",
        "status": "paid",
        "items": 4,
        "warehouse": "CZ-03",
    },
    "100248": {
        "order_id": "100248",
        "status": "paid",
        "items": 1,
        "warehouse": "CZ-01",
    },
}

MOCK_PAYMENTS: dict[str, dict[str, Any]] = {
    "100245": {"order_id": "100245", "status": "captured", "transaction_id": "pay-100245"},
    "100246": {"order_id": "100246", "status": "captured", "transaction_id": "pay-100246"},
    "100247": {"order_id": "100247", "status": "captured", "transaction_id": "pay-100247"},
    "100248": {"order_id": "100248", "status": "captured", "transaction_id": "pay-100248"},
}

MOCK_FULFILLMENT: dict[str, dict[str, Any]] = {
    "100245": {"order_id": "100245", "status": "blocked", "reason": "sync_error"},
    "100246": {"order_id": "100246", "status": "shipped", "tracking": "CZ123456789"},
    "100247": {"order_id": "100247", "status": "shipped", "tracking": "CZ987654321"},
    "100248": {"order_id": "100248", "status": "pending", "reason": "awaiting_pick"},
}

MOCK_SYNC_LOGS: dict[str, list[dict[str, Any]]] = {
    "100245": [
        {"at": "2026-10-14T07:41:00Z", "status": "failed", "message": "Warehouse API timeout"},
        {"at": "2026-10-14T07:40:45Z", "status": "retryable", "message": "Sync started"},
    ],
    "100246": [
        {"at": "2026-10-14T06:15:00Z", "status": "success", "message": "Shipment synced"},
    ],
    "100247": [
        {"at": "2026-10-14T08:20:00Z", "status": "success", "message": "Shipment synced"},
    ],
    "100248": [
        {"at": "2026-10-14T08:25:00Z", "status": "retryable", "message": "Picking not started"},
    ],
}

_CREATED_TICKETS: dict[str, dict[str, Any]] = {}


def _find[ToolValue](mapping: dict[str, ToolValue], order_id: str, label: str) -> ToolValue:
    try:
        return mapping[order_id]
    except KeyError as exc:
        raise ToolExecutionError(f"{label} not found for order {order_id}") from exc


def _get_order(arguments: dict[str, Any]) -> dict[str, Any]:
    return _find(MOCK_ORDERS, arguments["order_id"], "Order")


def _get_payment(arguments: dict[str, Any]) -> dict[str, Any]:
    return _find(MOCK_PAYMENTS, arguments["order_id"], "Payment")


def _get_fulfillment(arguments: dict[str, Any]) -> dict[str, Any]:
    return _find(MOCK_FULFILLMENT, arguments["order_id"], "Fulfillment")


def _get_sync_logs(arguments: dict[str, Any]) -> dict[str, Any]:
    order_id = arguments["order_id"]
    return {"order_id": order_id, "entries": _find(MOCK_SYNC_LOGS, order_id, "Sync logs")}


def _retry_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    order_id = arguments["order_id"]
    _find(MOCK_ORDERS, order_id, "Order")
    return {"order_id": order_id, "status": "queued", "message": "Synchronization retry queued"}


def _create_ticket(arguments: dict[str, Any]) -> dict[str, Any]:
    description = arguments["description"]
    digest = hashlib.sha256(description.encode("utf-8")).hexdigest()[:8]
    ticket_id = f"INC-{digest.upper()}"
    ticket = {"ticket_id": ticket_id, "status": "created", "description": description}
    _CREATED_TICKETS[ticket_id] = ticket
    return ticket


class ToolRegistry:
    def __init__(
        self,
        timeout_seconds: float = 3.0,
        audit_query: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.audit_query = audit_query
        self._definitions = {
            definition.name: definition
            for definition in (
                ToolDefinition(
                    "get_order",
                    "Get order details by order ID.",
                    OrderIdInput,
                    _get_order,
                ),
                ToolDefinition(
                    "get_payment",
                    "Get payment state by order ID.",
                    OrderIdInput,
                    _get_payment,
                ),
                ToolDefinition(
                    "get_fulfillment",
                    "Get fulfillment and shipment state by order ID.",
                    OrderIdInput,
                    _get_fulfillment,
                ),
                ToolDefinition(
                    "get_sync_logs",
                    "Get synchronization history by order ID.",
                    OrderIdInput,
                    _get_sync_logs,
                ),
                ToolDefinition(
                    "get_order_audit",
                    "Get related local SQLite tool calls and approvals for an order.",
                    OrderIdInput,
                    self._get_order_audit,
                ),
                ToolDefinition(
                    "retry_sync",
                    "Queue one synchronization retry for an order.",
                    OrderIdInput,
                    _retry_sync,
                    side_effecting=True,
                ),
                ToolDefinition(
                    "create_ticket",
                    "Create an incident ticket with the supplied description.",
                    TicketInput,
                    _create_ticket,
                    side_effecting=True,
                ),
            )
        }

    def _get_order_audit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.audit_query is None:
            raise ToolExecutionError("Order audit tool is not connected to the SQLite repository")
        return self.audit_query(arguments["order_id"])

    def get(self, name: str) -> ToolDefinition:
        return self._definitions[name]

    def definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def action_key(self, request: ToolCallRequest) -> str:
        payload = json.dumps(request.arguments, sort_keys=True, separators=(",", ":"))
        return f"{request.name}:{payload}"

    def validate(self, request: ToolCallRequest) -> tuple[ToolDefinition, dict[str, Any]]:
        definition = self._definitions.get(request.name)
        if definition is None:
            raise ToolExecutionError(f"Unknown tool: {request.name}")
        try:
            validated = definition.arguments_model.model_validate(request.arguments)
        except ValidationError as exc:
            raise ToolExecutionError(f"Invalid arguments for {request.name}: {exc}") from exc
        return definition, validated.model_dump()

    def execute(self, request: ToolCallRequest) -> ToolResult:
        try:
            definition, arguments = self.validate(request)
        except ToolExecutionError as exc:
            return ToolResult(
                name=request.name,
                call_id=request.call_id,
                ok=False,
                error=str(exc),
                status=ToolCallStatus.FAILED,
            )

        attempts = 1 if definition.side_effecting else READ_ONLY_TOOL_MAX_ATTEMPTS
        last_error: str | None = None
        for _ in range(attempts):
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(definition.handler, arguments)
            try:
                result = future.result(timeout=self.timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                return ToolResult(
                    name=request.name,
                    call_id=request.call_id,
                    ok=False,
                    error=f"{request.name} timed out after {self.timeout_seconds:g}s",
                    status=ToolCallStatus.TIMED_OUT,
                )
            except ToolExecutionError as exc:
                last_error = str(exc)
                executor.shutdown(wait=True, cancel_futures=True)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                executor.shutdown(wait=True, cancel_futures=True)
            else:
                executor.shutdown(wait=True, cancel_futures=True)
                return ToolResult(
                    name=request.name,
                    call_id=request.call_id,
                    ok=True,
                    result=result,
                    status=ToolCallStatus.SUCCEEDED,
                )

        return ToolResult(
            name=request.name,
            call_id=request.call_id,
            ok=False,
            error=last_error or f"{request.name} failed",
            status=ToolCallStatus.FAILED,
        )
