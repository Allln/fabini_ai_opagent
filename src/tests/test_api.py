from fastapi.testclient import TestClient

from app.config import Settings
from app.db.repository import SQLiteRepository
from app.main import create_app


def test_api_runs_mock_scenario_and_returns_structured_result() -> None:
    settings = Settings(agent_mode="mock", database_path=":memory:")
    repository = SQLiteRepository(":memory:")
    client = TestClient(create_app(settings, repository))

    response = client.post(
        "/api/runs",
        json={"request": "Investigate order 100245.", "mode": "mock"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["mode"] == "mock"
    assert body["tool_calls"][0]["name"] == "get_order"


def test_api_exposes_health_and_static_frontend() -> None:
    settings = Settings(agent_mode="mock", database_path=":memory:")
    repository = SQLiteRepository(":memory:")
    client = TestClient(create_app(settings, repository))

    assert client.get("/health").json() == {"status": "ok", "mode": "mock"}
    frontend = client.get("/")
    assert "FABINI Operations Agent" in frontend.text
    assert "Detected order state" in frontend.text
    assert 'id="state-graph"' in frontend.text


def test_api_approval_endpoint_resumes_pending_action() -> None:
    settings = Settings(agent_mode="mock", database_path=":memory:")
    repository = SQLiteRepository(":memory:")
    client = TestClient(create_app(settings, repository))

    pending = client.post(
        "/api/runs",
        json={
            "request": "Investigate order 100245 and retry sync if it is blocked.",
            "mode": "mock",
        },
    ).json()
    approval_id = pending["approvals"][0]["id"]

    completed = client.post(
        f"/api/runs/{pending['run_id']}/approvals/{approval_id}",
        json={"decision": "approve"},
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["approvals"][0]["status"] == "executed"
