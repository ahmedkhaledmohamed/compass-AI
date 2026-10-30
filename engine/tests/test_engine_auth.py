"""The local engine requires its private launch token before any endpoint runs."""

import pytest
from fastapi.testclient import TestClient

from compass import server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_engine_token", "fictional-engine-session")
    return TestClient(server.app)


@pytest.mark.parametrize("authorization", [None, "", "Bearer wrong-session", "Basic fictional-engine-session"])
def test_unauthorized_requests_cannot_access_health_or_credentials(client, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.get("/health", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    response = client.post("/credentials/inject", headers=headers, json={
        "provider": "fictional", "access_token": "fictional-token",
    })
    assert response.status_code == 401


def test_browser_preflight_is_not_granted_api_access(client):
    response = client.options("/credentials/inject", headers={
        "Origin": "https://untrusted.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers


def test_authorized_health_request_succeeds_without_cors_headers(client):
    response = client.get("/health", headers={
        "Authorization": "Bearer fictional-engine-session",
        "Origin": "https://untrusted.example",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert "access-control-allow-origin" not in response.headers


def test_missing_engine_configuration_fails_closed(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_engine_token", "")
    workspace = tmp_path / "must-not-be-created"
    response = client.post("/init", json={"name": "Fictional", "workspace_path": str(workspace)})
    assert response.status_code == 503
    assert not workspace.exists()


def test_previous_session_token_is_rejected_after_rotation(client, monkeypatch):
    monkeypatch.setattr(server, "_engine_token", "next-fictional-session")
    assert client.get("/health", headers={"Authorization": "Bearer fictional-engine-session"}).status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer next-fictional-session"}).status_code == 200


@pytest.mark.parametrize("endpoint", ["/tasks/list", "/tasks/create", "/tasks/jobs/start", "/tasks/jobs/cancel"])
def test_unauthorized_task_operations_do_not_create_workspace_state(client, tmp_path, endpoint):
    workspace = tmp_path / "must-remain-absent"
    result = client.post(endpoint, json={"workspace_path": str(workspace)})
    assert result.status_code == 401
    assert not workspace.exists()
