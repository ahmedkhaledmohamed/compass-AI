"""Tests for the FastAPI server endpoints using TestClient.

LLM-dependent endpoints are not tested here (they require a real API key).
This covers: /health, /init, /connect, /ingest, /evidence, /configure.
"""

import os

import pytest
from fastapi.testclient import TestClient

import compass.server as server_module
from compass.server import app


@pytest.fixture(autouse=True)
def reset_server_state():
    """Reset global server state between tests."""
    server_module._kg = None
    server_module._kg_workspace_path = None
    yield
    server_module._kg = None
    server_module._kg_workspace_path = None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server_module, "_engine_token", "fictional-engine-session")
    return TestClient(app, headers={"Authorization": "Bearer fictional-engine-session"})


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with demo sources."""
    ws = tmp_path / "test-product"
    ws.mkdir()

    # Create docs
    docs_dir = ws / "docs"
    docs_dir.mkdir()
    (docs_dir / "strategy.md").write_text("# Strategy\nMobile-first approach for Q1.")

    # Create interviews
    interviews_dir = ws / "interviews"
    interviews_dir.mkdir()
    (interviews_dir / "interview-1.md").write_text("# Interview: User A\nSync is unreliable.")

    return ws


class TestHealth:
    def test_health(self, client):
        res = client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ready"
        assert "version" in data


class TestInit:
    def test_init_workspace(self, client, tmp_path):
        ws = str(tmp_path / "new-product")
        res = client.post("/init", json={
            "name": "Test Product",
            "description": "A test product",
            "workspace_path": ws,
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["name"] == "Test Product"


    def test_desktop_init_preserves_existing_config_and_does_not_create_git(self, client, tmp_path):
        ws = tmp_path / "existing-product"
        request = {"name": "Original", "workspace_path": str(ws)}
        assert client.post("/init", json=request).status_code == 200
        config = ws / ".compass" / "compass.yaml"
        original = config.read_bytes()
        assert not (ws / ".git").exists()
        repeated = client.post("/init", json={**request, "name": "Replacement"})
        assert repeated.status_code == 409
        assert config.read_bytes() == original



class TestConnect:
    def test_connect_docs(self, client, workspace):
        # First init
        client.post("/init", json={
            "name": "Test",
            "workspace_path": str(workspace),
        })

        docs_dir = workspace / "docs"
        res = client.post("/connect", json={
            "workspace_path": str(workspace),
            "source_type": "docs",
            "name": "strategy-docs",
            "path": str(docs_dir),
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["accessible"] is True


class TestIngest:
    def test_ingest(self, client, workspace):
        # Init and connect
        client.post("/init", json={"name": "Test", "workspace_path": str(workspace)})
        client.post("/connect", json={
            "workspace_path": str(workspace),
            "source_type": "docs",
            "name": "docs",
            "path": str(workspace / "docs"),
        })
        client.post("/connect", json={
            "workspace_path": str(workspace),
            "source_type": "interviews",
            "name": "interviews",
            "path": str(workspace / "interviews"),
        })

        res = client.post("/ingest", json={"workspace_path": str(workspace)})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["total"] >= 2

    def test_ingest_no_sources(self, client, workspace):
        client.post("/init", json={"name": "Test", "workspace_path": str(workspace)})
        res = client.post("/ingest", json={"workspace_path": str(workspace)})
        assert res.status_code == 400


class TestEvidence:
    def test_evidence_after_ingest(self, client, workspace):
        # Setup
        client.post("/init", json={"name": "Test", "workspace_path": str(workspace)})
        client.post("/connect", json={
            "workspace_path": str(workspace),
            "source_type": "docs",
            "name": "docs",
            "path": str(workspace / "docs"),
        })
        client.post("/ingest", json={"workspace_path": str(workspace)})

        res = client.post("/evidence", json={"workspace_path": str(workspace)})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["count"] >= 1


class TestConfigure:
    def test_configure_with_key(self, client):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key-for-testing"
        try:
            res = client.post("/configure", json={
                "api_key": "sk-ant-test-key-for-testing",
                "model": "claude-sonnet-4-20250514",
                "provider": "anthropic",
            })
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ok"
            assert data["provider"] == "anthropic"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_configure_missing_key(self, client, monkeypatch):
        # Remove any existing key and prevent dotenv from reloading it
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import dotenv
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: None)
        res = client.post("/configure", json={
            "api_key": "",
            "model": "",
            "provider": "anthropic",
        })
        # Should fail because no API key
        assert res.status_code == 400


class TestSearch:
    def test_search_no_evidence(self, client, workspace):
        """Search with no evidence should return 400."""
        client.post("/init", json={"name": "Test", "workspace_path": str(workspace)})
        res = client.post("/search", json={
            "workspace_path": str(workspace),
            "query": "sync",
        })
        # _get_kg raises 400 when no evidence
        assert res.status_code == 400
