from __future__ import annotations

import re
from typing import Protocol

from google.genai import errors

from app.models import ModelTurn, ToolCallRequest, ToolResult
from app.tools.registry import ToolRegistry


class AgentModel(Protocol):
    def start(self, request: str) -> ModelTurn: ...

    def submit_tool_results(self, results: list[ToolResult]) -> ModelTurn: ...


class MockAgentModel:
    """Deterministic model used for demos and tests without network access."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._request = ""
        self._order_id = ""
        self._stage = 0
        self._observations: dict[str, dict] = {}

    def start(self, request: str) -> ModelTurn:
        self._request = request
        match = re.search(r"\b\d{4,20}\b", request)
        self._order_id = match.group(0) if match else ""
        if not self._order_id:
            return ModelTurn(text="I need a numeric order ID before I can investigate the request.")
        self._stage = 1
        return ModelTurn(
            tool_calls=[ToolCallRequest(name="get_order", arguments={"order_id": self._order_id})]
        )

    def submit_tool_results(self, results: list[ToolResult]) -> ModelTurn:
        for result in results:
            if result.ok and result.result is not None:
                self._observations[result.name] = result.result

        if any(not result.ok for result in results):
            failed = next(result for result in results if not result.ok)
            return ModelTurn(
                text=(
                    f"I could not complete the investigation because {failed.error}. "
                    "No operational action was taken."
                )
            )

        if self._stage == 1:
            self._stage = 2
            return ModelTurn(
                tool_calls=[
                    ToolCallRequest(name="get_payment", arguments={"order_id": self._order_id})
                ]
            )
        if self._stage == 2:
            self._stage = 3
            return ModelTurn(
                tool_calls=[
                    ToolCallRequest(
                        name="get_fulfillment",
                        arguments={"order_id": self._order_id},
                    )
                ]
            )
        if self._stage == 3:
            self._stage = 4
            return ModelTurn(
                tool_calls=[
                    ToolCallRequest(
                        name="get_sync_logs",
                        arguments={"order_id": self._order_id},
                    )
                ]
            )
        if self._stage == 4:
            self._stage = 5
            request_lower = self._request.lower()
            fulfillment = self._observations.get("get_fulfillment", {})
            sync_failed = fulfillment.get("status") == "blocked"
            if sync_failed and any(
                phrase in request_lower
                for phrase in ("retry", "resync", "repeat synchronization", "execute")
            ):
                return ModelTurn(
                    text="The order is blocked by synchronization and a retry is requested.",
                    tool_calls=[
                        ToolCallRequest(
                            name="retry_sync",
                            arguments={"order_id": self._order_id},
                        )
                    ],
                )
            if any(phrase in request_lower for phrase in ("ticket", "incident")):
                return ModelTurn(
                    text=(
                        "The evidence supports opening an incident for the synchronization failure."
                    ),
                    tool_calls=[
                        ToolCallRequest(
                            name="create_ticket",
                            arguments={
                                "description": (
                                    f"Order {self._order_id} is blocked by a warehouse "
                                    "synchronization failure."
                                )
                            },
                        )
                    ],
                )
            return ModelTurn(text=self._diagnosis())

        return ModelTurn(text=self._diagnosis(action_completed=True))

    def _diagnosis(self, action_completed: bool = False) -> str:
        fulfillment = self._observations.get("get_fulfillment", {})
        logs = self._observations.get("get_sync_logs", {}).get("entries", [])
        latest = logs[0]["message"] if logs else "no synchronization error was recorded"
        action_note = " The requested action was completed." if action_completed else ""
        return (
            f"Order {self._order_id} is {fulfillment.get('status', 'unknown')}; "
            f"payment and order data were available. The latest synchronization evidence "
            f"is: {latest}."
            f"{action_note}"
        )


class GeminiAgentModel:
    """Google Gemini adapter using native function calling."""

    def __init__(self, api_key: str, model_name: str, registry: ToolRegistry) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name
        self._contents: list[types.Content] = []
        self._tool = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=definition.name,
                    description=definition.description,
                    parameters_json_schema=definition.arguments_model.model_json_schema(),
                )
                for definition in registry.definitions()
            ]
        )

    def start(self, request: str) -> ModelTurn:
        self._contents = [
            self._types.Content(
                role="user",
                parts=[self._types.Part.from_text(text=request)],
            )
        ]
        return self._generate()

    def submit_tool_results(self, results: list[ToolResult]) -> ModelTurn:
        parts = [
            self._types.Part.from_function_response(
                name=result.name,
                response={
                    "ok": result.ok,
                    "result": result.result,
                    "error": result.error,
                },
            )
            for result in results
        ]
        self._contents.append(self._types.Content(role="user", parts=parts))
        return self._generate()

    def _generate(self) -> ModelTurn:
        try:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=self._contents,
                config=self._types.GenerateContentConfig(
                    system_instruction=(
                        "You are a cautious operations agent. Investigate using read-only "
                        "tools first. Use retry_sync or create_ticket only when the user "
                        "explicitly requests that action; the host application will gate it "
                        "with approval. Never invent tool results. Give a concise final answer."
                    ),
                    tools=[self._tool],
                    temperature=0,
                ),
            )
        except errors.APIError as exc:
            raise RuntimeError(f"Gemini API error: {exc}") from exc
        if not response.candidates or response.candidates[0].content is None:
            raise RuntimeError("Gemini returned no candidate content")
        content = response.candidates[0].content
        self._contents.append(content)
        calls: list[ToolCallRequest] = []
        text_parts: list[str] = []
        for part in content.parts or []:
            if part.text:
                text_parts.append(part.text)
            if part.function_call:
                calls.append(
                    ToolCallRequest(
                        name=part.function_call.name,
                        arguments=dict(part.function_call.args or {}),
                    )
                )
        return ModelTurn(text=" ".join(text_parts).strip() or None, tool_calls=calls)
