from fastapi.testclient import TestClient

from cloudos import config, employees
from cloudos.api.app import app
from cloudos.contracts import RouteResult


def test_employee_endpoint_returns_codex_result(monkeypatch):
    monkeypatch.setenv("AGENT_API_TOKEN", "test-token")
    config.reset_settings_cache()
    monkeypatch.setattr(
        employees,
        "invoke_employee",
        lambda *args, **kwargs: (
            RouteResult(ok=True, text="calendar ready", model="codex_cli", cost_usd=0.0),
            ["brand/voice.md"],
        ),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/employees/content-strategist/invoke",
            headers={"Authorization": "Bearer test-token"},
            json={"message": "Build my next two weeks"},
        )

    assert response.status_code == 200
    assert response.json()["text"] == "calendar ready"
    assert response.json()["model"] == "codex_cli"
    assert response.json()["context_files"] == ["brand/voice.md"]
    config.reset_settings_cache()


def test_employee_endpoint_requires_auth(monkeypatch):
    monkeypatch.setenv("AGENT_API_TOKEN", "test-token")
    config.reset_settings_cache()
    with TestClient(app) as client:
        response = client.post(
            "/v1/employees/researcher/invoke", json={"message": "research this"}
        )
    assert response.status_code == 401
    config.reset_settings_cache()
