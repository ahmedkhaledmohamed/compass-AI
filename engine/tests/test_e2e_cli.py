"""End-to-end smoke tests for the Compass pipeline.

Tests the full flow: init → connect → ingest → reconcile → discover
using the FastAPI TestClient with mocked LLM calls.

A separate @pytest.mark.slow class runs with a real API key.
"""

import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import compass.server as server_module
from compass.server import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
def demo_workspace(tmp_path):
    """Create a workspace with all 5 demo source types."""
    ws = tmp_path / "syncflow"
    ws.mkdir()

    # Code source
    code_dir = ws / "code"
    code_dir.mkdir()
    (code_dir / "sync_engine.py").write_text(
        '"""SyncFlow Sync Engine"""\n'
        "POLL_INTERVAL_SECONDS = 5  # increased from 1s, 'temporary' fix\n"
        "MAX_RETRIES = 1  # reduced from 5 after retry storms\n"
        "CONNECTION_POOL_SIZE = 50  # unchanged since launch despite 4x growth\n"
        "# NOTE: No batch export, no webhook system, no SSO, no public API\n"
    )

    # Docs/strategy source
    docs_dir = ws / "strategy"
    docs_dir.mkdir()
    (docs_dir / "product-strategy.md").write_text(
        "# SyncFlow Product Strategy — Q1 2026\n\n"
        "## P0: Real-time Sync (Our Moat)\n"
        "All integrations sync within 5 seconds — guaranteed.\n"
        "The sync engine is solid and battle-tested.\n"
        "Target: 99.9% sync success rate.\n\n"
        "## P1: Enterprise Security\n"
        "SSO, SOC2, audit logging. Target: end of Q1.\n"
    )

    # Analytics/data source
    analytics_dir = ws / "analytics"
    analytics_dir.mkdir()
    (analytics_dir / "product_metrics.csv").write_text(
        "metric,date,value,trend\n"
        "sync_success_rate,2026-01,87%,declining\n"
        "sync_p95_latency_ms,2026-01,14200,increasing\n"
        "active_connections,2026-01,41000,growing\n"
        "nps_score,2026-01,42,flat\n"
    )

    # Interview source
    interviews_dir = ws / "interviews"
    interviews_dir.mkdir()
    (interviews_dir / "customer-a.md").write_text(
        "# Interview: Enterprise Customer A\n"
        "Sync failures are our biggest pain. We lost a deal because of reliability.\n"
        "Need SSO for procurement approval.\n"
    )

    # Support source
    support_dir = ws / "support"
    support_dir.mkdir()
    (support_dir / "support_tickets.csv").write_text(
        "id,title,category,priority,status\n"
        "T-1001,Sync stopped working for 4 hours,sync_failure,high,resolved\n"
        "T-1007,Lost data during sync failure,sync_failure,critical,resolved\n"
        "T-1024,Lost deal because of sync reliability,sync_failure,critical,open\n"
        "T-1025,Sync completely down for 2 hours,sync_failure,critical,open\n"
        "T-1026,Evaluating Zapier as backup,churn_risk,critical,open\n"
    )

    return ws


# ---------------------------------------------------------------------------
# Mock LLM responses
# ---------------------------------------------------------------------------

MOCK_RECONCILE_RESPONSE = {
    "conflicts": [
        {
            "title": "Sync reliability claims contradict actual performance",
            "description": (
                "DOCS claims sync engine is 'solid and battle-tested' with 99.9% target, "
                "but CODE shows degraded config (5s polling, 1 retry, stale pool) and "
                "DATA shows 87% success rate with 14s P95 latency."
            ),
            "severity": "high",
            "signal_strength": 4,
            "source_a_evidence": ["Product Strategy"],
            "source_b_evidence": ["sync_engine.py", "product_metrics.csv"],
            "recommendation": "Acknowledge sync reliability gap and prioritize engineering fixes.",
        }
    ]
}

MOCK_DISCOVER_RESPONSE = {
    "opportunities": [
        {
            "title": "Fix sync reliability before it becomes existential",
            "description": (
                "Sync is the product's moat but all evidence shows it's degrading. "
                "87% success rate, 14s latency, customers evaluating alternatives."
            ),
            "confidence": "high",
            "evidence_summary": "Support tickets, analytics metrics, code review, strategy doc",
            "estimated_impact": "Prevent churn of enterprise customers worth $200k+ ARR",
            "cited_evidence_titles": ["Product Strategy", "sync_engine.py"],
            "related_conflict_titles": ["Sync reliability claims contradict actual performance"],
        },
        {
            "title": "Add SSO to unblock enterprise procurement",
            "description": "Multiple enterprise customers report SSO blocking renewal.",
            "confidence": "medium",
            "evidence_summary": "Interview: Enterprise Customer A, support tickets",
            "estimated_impact": "Unblock $100k+ in pending enterprise deals",
            "cited_evidence_titles": ["Interview: Enterprise Customer A"],
            "related_conflict_titles": [],
        },
    ]
}


def _mock_ask_json(prompt, system="", model="", max_tokens=4096):
    """Return mock responses based on which engine is calling."""
    system_lower = system.lower()
    # Check for discoverer first — its system prompt is about synthesizing opportunities
    if "synthesize" in system_lower or "product opportunities" in system_lower:
        return MOCK_DISCOVER_RESPONSE
    # Reconciler — its system prompt is about finding conflicts between sources
    if "reconcil" in system_lower or "finds meaningful\nconflicts" in system_lower:
        return MOCK_RECONCILE_RESPONSE
    return {"conflicts": [], "opportunities": []}


# ---------------------------------------------------------------------------
# E2E Pipeline Test (mocked LLM)
# ---------------------------------------------------------------------------

class TestE2EPipeline:
    """Full pipeline test with mocked LLM calls — no API key required."""

    def _setup_workspace(self, client, workspace):
        """Init workspace and connect all 5 sources."""
        # Init
        res = client.post("/init", json={
            "name": "SyncFlow",
            "description": "Team sync tool",
            "workspace_path": str(workspace),
        })
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

        # Connect sources (matching connector registry keys)
        sources = [
            ("code", "code", workspace / "code"),
            ("docs", "strategy", workspace / "strategy"),
            ("analytics", "analytics", workspace / "analytics"),
            ("interviews", "interviews", workspace / "interviews"),
            ("support", "support", workspace / "support"),
        ]
        for source_type, name, path in sources:
            res = client.post("/connect", json={
                "workspace_path": str(workspace),
                "source_type": source_type,
                "name": name,
                "path": str(path),
            })
            assert res.status_code == 200, f"Connect {source_type} failed: {res.text}"
            assert res.json()["status"] == "ok"

    def test_init_connect_ingest(self, client, demo_workspace):
        """Pipeline stages 1-3: init, connect, ingest."""
        self._setup_workspace(client, demo_workspace)

        # Ingest
        res = client.post("/ingest", json={"workspace_path": str(demo_workspace)})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["total"] >= 5  # at least one item per source

        # Verify evidence store persisted
        compass_dir = demo_workspace / ".compass"
        assert compass_dir.exists()

        kg_dir = compass_dir / "knowledge"
        assert kg_dir.exists()
        evidence_store = kg_dir / "evidence.sqlite3"
        assert evidence_store.exists()

        from compass.engine.evidence_repository import EvidenceRepository
        store_data = EvidenceRepository(kg_dir).read()[1]
        assert isinstance(store_data, list)
        assert len(store_data) >= 5

    def test_evidence_endpoint(self, client, demo_workspace):
        """Verify evidence is queryable after ingest."""
        self._setup_workspace(client, demo_workspace)
        client.post("/ingest", json={"workspace_path": str(demo_workspace)})

        res = client.post("/evidence", json={"workspace_path": str(demo_workspace)})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["count"] >= 5

        # Check we have evidence from multiple source types
        source_types = {item["source_type"] for item in data["items"]}
        assert len(source_types) >= 3  # at least code, docs, and one judgment source

    @patch("compass.engine.reconciler.ask_json", side_effect=_mock_ask_json)
    def test_reconcile(self, mock_llm, client, demo_workspace):
        """Pipeline stage 4: reconcile with mocked LLM."""
        self._setup_workspace(client, demo_workspace)
        client.post("/ingest", json={"workspace_path": str(demo_workspace)})

        res = client.post("/reconcile", json={"workspace_path": str(demo_workspace)})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["count"] >= 1

        # Verify conflict report written to disk
        report_path = demo_workspace / ".compass" / "output" / "conflict-report.md"
        assert report_path.exists()
        report_text = report_path.read_text()
        assert "Conflict Report" in report_text
        assert "Sync reliability" in report_text

    @patch("compass.engine.discoverer.ask_json", side_effect=_mock_ask_json)
    @patch("compass.engine.reconciler.ask_json", side_effect=_mock_ask_json)
    def test_discover(self, mock_reconcile, mock_discover, client, demo_workspace):
        """Pipeline stage 5: discover with mocked LLM."""
        self._setup_workspace(client, demo_workspace)
        client.post("/ingest", json={"workspace_path": str(demo_workspace)})

        res = client.post("/discover", json={"workspace_path": str(demo_workspace)})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["count"] >= 1

        # Verify opportunities cached to disk
        cache_path = demo_workspace / ".compass" / "opportunities_cache.json"
        assert cache_path.exists()
        cache = json.loads(cache_path.read_text())
        assert len(cache) >= 1
        assert cache[0]["title"]  # has a title
        assert cache[0]["confidence"] in ("high", "medium", "low")

    @patch("compass.engine.discoverer.ask_json", side_effect=_mock_ask_json)
    @patch("compass.engine.reconciler.ask_json", side_effect=_mock_ask_json)
    def test_full_pipeline(self, mock_reconcile, mock_discover, client, demo_workspace):
        """Full pipeline: init → connect → ingest → reconcile → discover.

        Asserts all persistence artifacts exist at the end.
        """
        self._setup_workspace(client, demo_workspace)

        # Ingest
        res = client.post("/ingest", json={"workspace_path": str(demo_workspace)})
        assert res.status_code == 200

        # Reconcile
        res = client.post("/reconcile", json={"workspace_path": str(demo_workspace)})
        assert res.status_code == 200
        assert res.json()["count"] >= 1

        # Discover
        res = client.post("/discover", json={"workspace_path": str(demo_workspace)})
        assert res.status_code == 200
        assert res.json()["count"] >= 1

        # --- Verify all persistence artifacts ---
        compass_dir = demo_workspace / ".compass"

        # Config
        config_path = compass_dir / "compass.yaml"
        assert config_path.exists()

        # Evidence store
        evidence_path = compass_dir / "knowledge" / "evidence.sqlite3"
        assert evidence_path.exists()
        from compass.engine.evidence_repository import EvidenceRepository
        store = EvidenceRepository(evidence_path.parent).read()[1]
        assert isinstance(store, list)
        assert len(store) >= 5

        # Conflict report
        report_path = compass_dir / "output" / "conflict-report.md"
        assert report_path.exists()

        # Opportunities cache
        cache_path = compass_dir / "opportunities_cache.json"
        assert cache_path.exists()
        cache = json.loads(cache_path.read_text())
        assert len(cache) >= 1


# ---------------------------------------------------------------------------
# Slow tests — require real API key (ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestE2ERealLLM:
    """Full pipeline with real LLM calls. Run with: pytest -m slow

    Requires ANTHROPIC_API_KEY or TASKFORCE_API_KEY to be set.
    """

    @pytest.fixture(autouse=True)
    def skip_without_api_key(self):
        has_key = bool(
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("TASKFORCE_API_KEY")
        )
        if not has_key:
            pytest.skip("No API key set — skipping slow LLM tests")

    def test_full_pipeline_real_llm(self, client, demo_workspace):
        """Full pipeline with real LLM. Should complete in <90s."""
        # Init
        res = client.post("/init", json={
            "name": "SyncFlow",
            "description": "Team sync tool",
            "workspace_path": str(demo_workspace),
        })
        assert res.status_code == 200

        # Connect all sources
        sources = [
            ("code", "code", demo_workspace / "code"),
            ("docs", "strategy", demo_workspace / "strategy"),
            ("analytics", "analytics", demo_workspace / "analytics"),
            ("interviews", "interviews", demo_workspace / "interviews"),
            ("support", "support", demo_workspace / "support"),
        ]
        for source_type, name, path in sources:
            res = client.post("/connect", json={
                "workspace_path": str(demo_workspace),
                "source_type": source_type,
                "name": name,
                "path": str(path),
            })
            assert res.status_code == 200

        # Ingest
        res = client.post("/ingest", json={"workspace_path": str(demo_workspace)})
        assert res.status_code == 200
        assert res.json()["total"] >= 5

        # Reconcile (real LLM)
        res = client.post("/reconcile", json={"workspace_path": str(demo_workspace)})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        # Real LLM should find at least 1 conflict in this data
        assert data["count"] >= 1

        # Discover (real LLM)
        res = client.post("/discover", json={"workspace_path": str(demo_workspace)})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["count"] >= 1

        # Verify structural validity of opportunities
        for opp in data["opportunities"]:
            assert opp["title"]
            assert opp["description"]
            assert opp["confidence"] in ("high", "medium", "low")
            assert opp["rank"] >= 1

        # Verify persistence
        compass_dir = demo_workspace / ".compass"
        assert (compass_dir / "knowledge" / "evidence.sqlite3").exists()
        assert (compass_dir / "output" / "conflict-report.md").exists()
        assert (compass_dir / "opportunities_cache.json").exists()
