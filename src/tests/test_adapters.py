from types import SimpleNamespace
from unittest.mock import patch

from app.agent.adapters import GeminiAgentModel
from app.models import ToolResult
from app.tools.registry import ToolRegistry


class FakeModels:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_content(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            content = SimpleNamespace(
                parts=[
                    SimpleNamespace(text="I will inspect the order.", function_call=None),
                    SimpleNamespace(
                        function_call=SimpleNamespace(
                            name="get_order",
                            args={"order_id": "100245"},
                        ),
                        text=None,
                    ),
                ]
            )
        else:
            content = SimpleNamespace(
                parts=[SimpleNamespace(text="The order was found.", function_call=None)]
            )
        return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


def test_gemini_adapter_translates_function_calls_and_results() -> None:
    models = FakeModels()
    client = FakeClient(models)

    with patch("google.genai.Client", return_value=client):
        model = GeminiAgentModel("test-key", "test-model", ToolRegistry())
        first_turn = model.start("Investigate order 100245.")
        second_turn = model.submit_tool_results(
            [
                ToolResult(
                    name="get_order",
                    call_id="call-1",
                    ok=True,
                    result={"order_id": "100245"},
                )
            ]
        )

    assert first_turn.text == "I will inspect the order."
    assert first_turn.tool_calls[0].name == "get_order"
    assert first_turn.tool_calls[0].arguments == {"order_id": "100245"}
    assert second_turn.text == "The order was found."
    assert len(models.calls) == 2
