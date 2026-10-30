"""Imports preserve source atomicity through cancellation, limits and recovery."""
import json
import sqlite3
import subprocess
import sys
import time
from threading import Event
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from compass.config import ProductConfig, SourceConfig, load_config, save_config
from compass.engine.evidence_repository import EvidenceRepository
from compass.engine.import_jobs import ImportManager
from compass.engine.local_import import MAX_FILE_BYTES, read_local_source
from compass.engine.task_jobs import close_job_manager
from compass.engine.task_repository import TaskConflict, TaskNotFound
from compass.engine.task_service import TaskService
from compass.models.sources import Evidence, SourceType
from compass.server import app


@pytest.fixture
def setup(tmp_path):
    sources = []
    for name in ("one", "two"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "brief.md").write_text(f"# {name}\nNew {name} evidence.")
        sources.append(SourceConfig(type="docs", name=name, path=str(folder)))
    save_config(ProductConfig(name="Imports", sources=sources), tmp_path)
    repo = EvidenceRepository(tmp_path / ".compass" / "knowledge")
    for source in sources:
        repo.upsert([Evidence(id="old-" + source.name, source_id=source.id, source_name=source.name,
            source_type=SourceType.DOCS, connector="docs", title="Old", content=f"Old {source.name} evidence.")])
    manager = ImportManager()
    release = Event()
    yield tmp_path, sources, repo, manager, release
    release.set()
    manager.close()
    for future in list(manager.futures.values()):
        future.result(timeout=4)
    close_job_manager()


def finished(manager, root, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = manager.get(root, job_id)
        if result["state"] not in ("queued", "running") and not result["draining"]:
            return result
        time.sleep(0.01)
    raise AssertionError("Import did not finish")


def test_import_publishes_evidence_and_each_source_result_together(setup):
    root, sources, repo, manager, _ = setup
    job = manager.start(root, [source.id for source in sources], "first")
    result = finished(manager, root, job["id"])
    assert result["state"] == "completed"
    assert all(item["state"] == "completed" for item in result["results"])
    assert result["results"][-1]["snapshot_id"] == repo.read()[0]
    assert {item.source_id for item in repo.read()[1]} == {source.id for source in sources}
    assert not any(item.id.startswith("old-") for item in repo.read()[1])
    assert manager.start(root, [source.id for source in sources], "first")["id"] == job["id"]
    with pytest.raises(TaskConflict, match="different source"):
        manager.start(root, [sources[0].id], "first")


def test_cancellation_is_acknowledged_while_reader_drains_and_preserves_current_source(setup):
    root, sources, repo, manager, release = setup
    started = Event()
    def reader(source, check, progress):
        if source.id == sources[1].id:
            started.set()
            assert release.wait(4)
        return read_local_source(source, check, progress)
    manager.reader = reader
    job = manager.start(root, [source.id for source in sources], "cancel")
    assert started.wait(2)
    before = time.monotonic()
    assert manager.cancel(root, job["id"])["cancel_requested"]
    assert time.monotonic() - before < 2
    with pytest.raises(TaskConflict, match="active workspace"):
        manager.start(root, [sources[0].id], "overlap")
    release.set()
    result = finished(manager, root, job["id"])
    assert result["state"] == "cancelled"
    assert result["results"][0]["state"] == "completed"
    assert result["results"][1]["state"] == "cancelled"
    assert next(item for item in repo.read()[1] if item.source_id == sources[1].id).id == "old-two"


def test_cancel_between_snapshot_write_and_commit_rolls_back_source_and_result(setup):
    root, sources, repo, manager, _ = setup
    _, _, owned, _ = manager._repository(root)
    before = repo.read()
    original = owned._write
    def publish(items):
        snapshot = original(items)
        job_id = next(iter(manager.cache))[1]
        manager.cancel(root, job_id)
        return snapshot
    with patch.object(owned, "_write", side_effect=publish):
        job = manager.start(root, [sources[0].id], "during-commit")
        result = finished(manager, root, job["id"])
    assert result["state"] == "cancelled"
    assert result["results"][0]["state"] == "cancelled"
    assert repo.read() == before


def test_import_blocks_generation_and_source_changes_until_drained(setup):
    root, sources, _, manager, release = setup
    started = Event()
    def reader(*args):
        started.set()
        assert release.wait(4)
        return read_local_source(*args)
    manager.reader = reader
    service = TaskService(root)
    task = service.create("What next?", "PM", "Choose", [sources[0].id])
    job = manager.start(root, [sources[0].id], "exclusive")
    assert started.wait(2)
    with pytest.raises(TaskConflict, match="active import"):
        manager.generation.start(service, task.id, task.revision, "brief", "generate", Mock())
    release.set()
    assert finished(manager, root, job["id"])["state"] == "completed"


def test_full_text_tail_and_skipped_format_coverage_are_explicit(setup):
    root, sources, repo, manager, _ = setup
    text = "# Long document\n" + "Full passage. " * 6_000 + "TAIL FACT: retention is unmeasured."
    (root / "one" / "brief.md").write_text(text)
    (root / "one" / "unsupported.pdf").write_bytes(b"fixture binary")
    job = manager.start(root, [sources[0].id], "coverage")
    result = finished(manager, root, job["id"])
    assert result["state"] == "partial"
    coverage = result["results"][0]["coverage"]
    assert coverage["files_read"] == 1
    assert coverage["skipped_files"] == 1
    assert coverage["truncated"] is False
    assert coverage["representation"] == "full_text"
    assert next(item for item in repo.read()[1] if item.source_id == sources[0].id).content == text


def test_oversized_or_symlink_source_preserves_old_source_and_reports_failure(setup):
    root, sources, repo, manager, _ = setup
    (root / "one" / "brief.md").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    before = repo.read()
    job = manager.start(root, [sources[0].id], "limit")
    result = finished(manager, root, job["id"])
    assert result["state"] == "failed"
    assert result["results"][0]["error_code"] == "limit"
    assert repo.read() == before
    (root / "one" / "brief.md").unlink()
    (root / "one" / "brief.md").symlink_to(root / "two" / "brief.md")
    job = manager.start(root, [sources[0].id], "link")
    assert finished(manager, root, job["id"])["state"] == "failed"
    assert repo.read() == before


def test_configuration_change_while_reading_does_not_publish_under_old_selection(setup):
    root, sources, repo, manager, _ = setup
    original = repo.read()
    def reader(source, check, progress):
        items, coverage = read_local_source(source, check, progress)
        config = load_config(root)
        config.sources[0].name = "Externally changed"
        save_config(config, root)
        return items, coverage
    manager.reader = reader
    job = manager.start(root, [sources[0].id], "changed-config")
    result = finished(manager, root, job["id"])
    assert result["state"] == "failed"
    assert "configuration changed" in result["results"][0]["message"]
    assert repo.read() == original


def test_v1_migration_keeps_verified_private_backup_and_existing_snapshots(tmp_path):
    repo = EvidenceRepository(tmp_path)
    item = Evidence(id="legacy", source_type=SourceType.DOCS, connector="docs", title="Saved", content="Keep this")
    repo.upsert([item])
    snapshot = repo.read()
    repo._db.execute("DROP TABLE import_jobs")
    repo._db.execute("PRAGMA user_version=1")
    repo._db.close()
    migrated = EvidenceRepository(tmp_path)
    assert migrated.read() == snapshot
    backups = list(tmp_path.glob("evidence-v1-*.sql"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(":memory:") as restored:
        restored.executescript(backups[0].read_text())
        assert restored.execute("PRAGMA user_version").fetchone()[0] == 1
        assert restored.execute("SELECT payload FROM snapshots WHERE id=?", (snapshot[0],)).fetchone()
    assert migrated._db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_unknown_job_is_scoped_to_the_workspace(setup):
    root, sources, _, manager, _ = setup
    job = manager.start(root, [sources[0].id], "scoped")
    finished(manager, root, job["id"])
    other = root / "other"
    save_config(ProductConfig(name="Other"), other)
    with pytest.raises(TaskNotFound):
        manager.get(other, job["id"])


def test_import_api_authentication_and_selected_source_contract(tmp_path, engine_headers):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "brief.md").write_text("# Selected\nRetain this evidence.")
    source = SourceConfig(type="docs", name="Docs", path=str(folder))
    save_config(ProductConfig(name="HTTP", sources=[source]), tmp_path)
    request = {"workspace_path": str(tmp_path), "source_ids": [source.id], "request_id": "http"}
    with TestClient(app) as unauthorized:
        assert unauthorized.post("/imports/start", json=request).status_code == 401
    with TestClient(app, headers=engine_headers) as client:
        response = client.post("/imports/start", json=request)
        assert response.status_code == 202
        job_id = response.json()["job"]["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.post("/imports/status", json={"workspace_path": str(tmp_path), "job_id": job_id}).json()["job"]
            if result["state"] == "completed":
                break
            time.sleep(0.01)
        assert result["state"] == "completed"
        assert result["source_ids"] == [source.id]
        assert "selection" not in result


def test_deadline_rejects_a_late_reader_without_replacing_previous_evidence(setup):
    root, sources, repo, manager, release = setup
    started = Event()
    before = repo.read()
    def reader(*args):
        started.set()
        assert release.wait(4)
        return read_local_source(*args)
    manager.reader = reader
    job = manager.start(root, [sources[0].id], "deadline", timeout_seconds=0.04)
    assert started.wait(2)
    time.sleep(0.06)
    assert manager.get(root, job["id"])["cancel_requested"]
    release.set()
    assert finished(manager, root, job["id"])["state"] == "timed_out"
    assert repo.read() == before


def test_generation_blocks_import_until_the_provider_request_drains(setup):
    root, sources, _, manager, release = setup
    started = Event()
    def response(*args, **kwargs):
        started.set()
        assert release.wait(4)
        return {}
    model = Mock(default_model="fictional-model")
    model.ask_json.side_effect = response
    service = TaskService(root)
    task = service.create("What next?", "PM", "Choose", [sources[0].id])
    generation = manager.generation.start(service, task.id, task.revision, "brief", "generation-first", model)
    assert started.wait(2)
    with pytest.raises(TaskConflict, match="active workspace"):
        manager.start(root, [sources[0].id], "import-overlap")
    manager.generation.cancel(service, task.id, generation["id"])
    release.set()


def test_process_crash_recovers_unfinished_job_without_replay_and_keeps_completed_source(setup):
    root, sources, repo, manager, _ = setup
    script = r"""
import json, sys
from pathlib import Path
from threading import Event
from compass.config import load_config
from compass.engine.import_jobs import ImportManager
from compass.engine.local_import import read_local_source
root=Path(sys.argv[1])
def reader(source,check,progress):
    if source.name=='two':
        (root/'reader-blocked').write_text('blocked')
        Event().wait(60)
    return read_local_source(source,check,progress)
manager=ImportManager(reader)
job=manager.start(root,[source.id for source in load_config(root).sources],'crash-test')
(root/'child-job.json').write_text(json.dumps({'id':job['id']}))
Event().wait(60)
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and child.poll() is None:
            if (root / "reader-blocked").exists() and (root / "child-job.json").exists():
                break
            time.sleep(0.01)
        assert (root / "reader-blocked").exists(), child.stderr.read() if child.poll() is not None else "Child did not reach the blocked reader"
        job_id = json.loads((root / "child-job.json").read_text())["id"]
        child.kill()
        child.wait(timeout=2)
        manager.reader = Mock(side_effect=AssertionError("An interrupted import must never be replayed"))
        recovered = manager.get(root, job_id)
        assert recovered["state"] == "interrupted"
        assert recovered["results"][0]["state"] == "completed"
        assert recovered["results"][1]["state"] == "interrupted"
        manager.reader.assert_not_called()
        items = repo.read()[1]
        assert next(item for item in items if item.source_id == sources[0].id).id != "old-one"
        assert next(item for item in items if item.source_id == sources[1].id).id == "old-two"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)
