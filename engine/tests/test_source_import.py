"""Import failure must preserve prior evidence across every entrypoint."""

from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from compass.cli import app as cli
from compass.config import ProductConfig, SourceConfig, get_compass_dir, save_config
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.source_import import import_source
from compass.models.sources import Evidence, SourceType
from compass.server import app


@pytest.fixture
def workspace(tmp_path):
    sources = []
    for name in ("one", "two"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "brief.md").write_text(f"# {name}\nOriginal {name} evidence")
        sources.append(SourceConfig(type="docs", name=name, path=str(folder)))
    save_config(ProductConfig(name="Test", sources=sources), tmp_path)
    kg = KnowledgeGraph(get_compass_dir(tmp_path) / "knowledge")
    for source in sources:
        import_source(kg, source)
    return tmp_path, sources, kg


@pytest.mark.parametrize("failure", ["inaccessible", "fetch", "empty", "invalid", "commit"])
def test_failed_source_preserves_current_snapshot(workspace, failure):
    _, sources, kg = workspace
    before = kg.store.items
    connector = Mock()
    connector.validate.return_value = failure != "inaccessible"
    connector.ingest.return_value = [] if failure == "empty" else [Evidence(
        source_type=SourceType.DOCS, connector="docs", title="new", content=" " if failure == "invalid" else "new content",
    )]
    if failure == "fetch":
        connector.ingest.side_effect = OSError("simulated source failure")
    with patch("compass.engine.source_import.get_connector", return_value=Mock(return_value=connector)):
        if failure == "commit":
            with patch.object(kg, "replace_source", side_effect=OSError("disk full")), pytest.raises(OSError):
                import_source(kg, sources[0])
        else:
            with pytest.raises((OSError, ValueError)):
                import_source(kg, sources[0])
    assert kg.store.items == before


@pytest.mark.parametrize("endpoint", ["ingest", "refresh"])
def test_http_partial_failure_reports_status_and_keeps_sibling(workspace, endpoint, engine_headers):
    path, _, kg = workspace
    old_one = next(item for item in kg.store.items if item.source_name == "one")
    (path / "one" / "brief.md").unlink()
    (path / "two" / "brief.md").write_text("# Updated\nNew sibling evidence")
    with TestClient(app, headers=engine_headers) as client:
        response = client.post(f"/{endpoint}", json={"workspace_path": str(path)})
    assert response.status_code == 200
    assert response.json()["status"] == "partial"
    assert "error" in response.json()["sources"][0]
    assert kg.get_by_id(old_one.id) == old_one
    assert {item.source_name for item in kg.store.items} == {"one", "two"}
    assert len(kg) == 2
    assert next(item for item in kg.store.items if item.source_name == "two").title == "Updated"


def test_http_all_sources_fail_reports_error(workspace, engine_headers):
    path, _, kg = workspace
    before = kg.store.items
    for name in ("one", "two"):
        (path / name / "brief.md").unlink()
    with TestClient(app, headers=engine_headers) as client:
        result = client.post("/ingest", json={"workspace_path": str(path)}).json()
    assert result["status"] == "error"
    assert kg.store.items == before


@pytest.mark.parametrize("command", ["ingest", "refresh"])
def test_cli_failure_returns_nonzero_and_preserves_saved_source(workspace, monkeypatch, command):
    path, _, kg = workspace
    old = next(item for item in kg.store.items if item.source_name == "one")
    (path / "one" / "brief.md").unlink()
    monkeypatch.chdir(path)
    result = CliRunner().invoke(cli, [command])
    assert result.exit_code == 1, result.output
    assert "errors" in result.output
    assert kg.get_by_id(old.id) == old
    assert len(kg) == 2


@pytest.mark.parametrize("tool", ["compass_ingest", "compass_refresh"])
def test_mcp_failure_is_explicit_and_preserves_evidence(workspace, tool):
    from compass import mcp_server

    path, _, kg = workspace
    old = next(item for item in kg.store.items if item.source_name == "one")
    (path / "one" / "brief.md").unlink()
    with patch.object(mcp_server, "_get_workspace", return_value=path):
        result = getattr(mcp_server, tool)()
    assert "with errors" in result
    assert kg.get_by_id(old.id) == old
    assert len(kg) == 2


def test_discovery_stops_before_model_call_if_import_fails(workspace):
    from compass.workflows.discovery import DiscoveryWorkflow

    path, _, kg = workspace
    old = next(item for item in kg.store.items if item.source_name == "one")
    (path / "one" / "brief.md").unlink()
    flow = DiscoveryWorkflow(path)
    with patch.object(flow, "_reconcile") as reconcile:
        result = flow.run()
    assert result["status"] == "error"
    reconcile.assert_not_called()
    assert kg.get_by_id(old.id) == old


def test_document_read_failure_aborts_whole_source(workspace):
    path, sources, kg = workspace
    before = kg.store.items
    (path / "one" / "unreadable.md").write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="Could not read document"):
        import_source(kg, sources[0])
    assert kg.store.items == before
