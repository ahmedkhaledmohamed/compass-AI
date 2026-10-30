"""Source identity and refresh behavior remain stable across named imports."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from compass.config import ProductConfig, SourceConfig, load_config, save_config
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.source_import import import_source
from compass.engine.task_service import TaskService
from compass.models.sources import Evidence, SourceType
from compass.server import app


def local_source(root, name):
    folder = root / name
    folder.mkdir()
    (folder / "brief.md").write_text("# Matching title\nThe goal is improved retention.")
    return SourceConfig(type="docs", name=name, path=str(folder))


def test_legacy_config_identity_is_stable_without_rewriting_on_read(tmp_path):
    directory = tmp_path / ".compass"
    directory.mkdir()
    config_file = directory / "compass.yaml"
    original = b"name: Legacy\nsources:\n- type: docs\n  name: Strategy\n  path: /fictional/docs\n"
    config_file.write_bytes(original)
    first, second = load_config(tmp_path), load_config(tmp_path)
    assert first.sources[0].id == second.sources[0].id
    assert first.sources[0].legacy_names == ["Strategy"]
    assert config_file.read_bytes() == original
    save_config(first, tmp_path)
    assert load_config(tmp_path).sources[0].id == first.sources[0].id


def test_source_scoped_document_and_content_identity_survives_unchanged_refresh(tmp_path):
    one, two = local_source(tmp_path, "one"), local_source(tmp_path, "two")
    save_config(ProductConfig(name="Product", sources=[one, two]), tmp_path)
    kg = KnowledgeGraph(tmp_path / ".compass" / "knowledge")
    original = import_source(kg, one)[0]
    sibling = import_source(kg, two)[0]
    assert original.id != sibling.id
    assert original.document_id != sibling.document_id
    assert original.content_hash == sibling.content_hash
    service = TaskService(tmp_path)
    task = service.create("What next?", "PM", "Choose an investigation", [one.id])
    refreshed = import_source(kg, one)[0]
    assert refreshed.id == original.id
    assert refreshed.document_id == original.document_id
    assert not service.stale(task)
    (tmp_path / "two" / "brief.md").write_text("# Changed sibling\nA different goal.")
    import_source(kg, two)
    assert not service.stale(task)
    (tmp_path / "one" / "brief.md").write_text("# Changed selection\nPrioritize reliability.")
    changed = import_source(kg, one)[0]
    assert changed.id != original.id
    assert changed.document_id == original.document_id
    assert service.stale(task)
    assert service.selected_evidence(task)[0].content == original.content


def test_renaming_a_source_keeps_its_identity_and_pinned_task_fresh(tmp_path, engine_headers):
    source = local_source(tmp_path, "strategy")
    save_config(ProductConfig(name="Product", sources=[source]), tmp_path)
    kg = KnowledgeGraph(tmp_path / ".compass" / "knowledge")
    original = import_source(kg, source)[0]
    service = TaskService(tmp_path)
    task = service.create("What next?", "PM", "Choose", [source.id])
    with TestClient(app, headers=engine_headers) as client:
        renamed = client.post("/connect", json={"workspace_path": str(tmp_path), "source_id": source.id,
            "source_type": "docs", "name": "Renamed strategy", "path": source.path})
    assert renamed.status_code == 200
    current = load_config(tmp_path).sources[0]
    assert current.id == source.id
    assert import_source(kg, current)[0].id == original.id
    assert len(kg) == 1
    assert not service.stale(task)


def test_legacy_name_is_migrated_even_when_renamed_before_first_import(tmp_path, engine_headers):
    source = local_source(tmp_path, "legacy")
    save_config(ProductConfig(name="Product", sources=[source]), tmp_path)
    kg = KnowledgeGraph(tmp_path / ".compass" / "knowledge")
    old = Evidence(id="legacy-id", source_name="legacy", source_type=SourceType.DOCS, connector="docs",
                   title="Old", content="Prior evidence")
    kg.add(old)
    snapshot = kg._repository.read()[0]
    with TestClient(app, headers=engine_headers) as client:
        result = client.post("/connect", json={"workspace_path": str(tmp_path), "source_id": source.id,
            "source_type": "docs", "name": "Renamed", "path": source.path})
    assert result.status_code == 200
    renamed = load_config(tmp_path).sources[0]
    import_source(kg, renamed)
    assert len(kg) == 1
    assert kg.store.items[0].source_id == source.id
    assert kg._repository.read(snapshot)[1][0] == old


def test_new_same_type_sources_do_not_replace_each_other_and_failed_reconnect_keeps_config(tmp_path, engine_headers):
    save_config(ProductConfig(name="Product"), tmp_path)
    one, two = local_source(tmp_path, "one"), local_source(tmp_path, "two")
    with TestClient(app, headers=engine_headers) as client:
        ids = []
        for source in (one, two):
            response = client.post("/connect", json={"workspace_path": str(tmp_path), "source_type": "docs",
                "name": source.name, "path": source.path, "new_source": True})
            assert response.status_code == 200
            ids.append(response.json()["source_id"])
        assert len(set(ids)) == 2
        original = (tmp_path / ".compass" / "compass.yaml").read_bytes()
        duplicate = client.post("/connect", json={"workspace_path": str(tmp_path), "source_type": "docs",
            "name": "one", "path": two.path, "new_source": True})
        assert duplicate.status_code == 409
        inaccessible = client.post("/connect", json={"workspace_path": str(tmp_path), "source_type": "docs",
            "source_id": ids[0], "name": "one", "path": str(tmp_path / "missing")})
        assert inaccessible.status_code == 422
    assert (tmp_path / ".compass" / "compass.yaml").read_bytes() == original
    assert len(load_config(tmp_path).sources) == 2


def test_import_total_reports_retained_evidence_separately_from_imported_count(tmp_path, engine_headers):
    one, two = local_source(tmp_path, "one"), local_source(tmp_path, "two")
    save_config(ProductConfig(name="Product", sources=[one, two]), tmp_path)
    kg = KnowledgeGraph(tmp_path / ".compass" / "knowledge")
    import_source(kg, one)
    import_source(kg, two)
    (tmp_path / "one" / "brief.md").unlink()
    with TestClient(app, headers=engine_headers) as client:
        result = client.post("/ingest", json={"workspace_path": str(tmp_path)}).json()
    assert result["status"] == "partial"
    assert result["total"] == 2
    assert result["imported"] == 1
    assert result["summary"] == {"docs": 2}


def test_failed_atomic_config_replace_preserves_original_and_cleans_temporary(tmp_path):
    save_config(ProductConfig(name="Original"), tmp_path)
    original = (tmp_path / ".compass" / "compass.yaml").read_bytes()
    with patch("compass.config.os.replace", side_effect=OSError("fixture disk failure")), pytest.raises(OSError):
        save_config(ProductConfig(name="Replacement"), tmp_path)
    assert (tmp_path / ".compass" / "compass.yaml").read_bytes() == original
    assert not list((tmp_path / ".compass").glob(".config-*"))

def test_new_source_does_not_claim_legacy_evidence_from_a_different_registration(tmp_path):
    from uuid import uuid4

    source = local_source(tmp_path, "same-name")
    source.id = "src_" + uuid4().hex
    source.legacy_names = []
    save_config(ProductConfig(name="Product", sources=[source]), tmp_path)
    kg = KnowledgeGraph(tmp_path / ".compass" / "knowledge")
    old = Evidence(id="old-unregistered", source_name=source.name, source_type=SourceType.DOCS,
                   connector="docs", title="Legacy", content="Keep this original evidence")
    kg.add(old)
    import_source(kg, source)
    assert kg.get_by_id(old.id) == old
    assert len(kg) == 2
