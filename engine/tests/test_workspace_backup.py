"""Recovery restores canonical history without connection authority or job replay."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from compass.config import AuthConfig, ProductConfig, SourceConfig, load_config, save_config
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.task_export import export_task
from compass.engine.task_jobs import close_job_manager, get_job_manager
from compass.engine.task_service import TaskService
from compass.engine.workspace_backup import create_backup, encode, read_backup, restore_backup
from compass.models.documents import StoredDocument
from compass.models.sources import Evidence, SourceType
from compass.models.tasks import Artifact, DraftBody


@pytest.fixture
def saved(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    source = SourceConfig(
        type="docs",
        name="Selected docs",
        path="/fictional/local-source",
        options={"api_key": "fictional-config-secret"},
        auth=AuthConfig(credential_ref="private-credential-ref"),
    )
    save_config(ProductConfig(name="Fictional product", sources=[source]), root)
    evidence = Evidence(
        id="selected",
        source_id=source.id,
        source_name=source.name,
        source_type=SourceType.DOCS,
        connector="docs",
        title="Strategy",
        content="😀 Retention is a goal.",
        metadata={"relative_path": "strategy.md"},
    )
    kg = KnowledgeGraph(root / ".compass" / "knowledge")
    kg.add(evidence)
    service = TaskService(root)
    task = service.create("What next?", "PM", "Choose investigation", [source.id])
    body = DraftBody.model_validate(
        {
            "title": "Reviewed decision",
            "summary": "Saved human summary",
            "claims": [
                {
                    "text": "Retention is a goal",
                    "kind": "supported",
                    "citations": [
                        {
                            "evidence_id": evidence.id,
                            "start": 0,
                            "end": len(evidence.content),
                            "quote": evidence.content,
                        }
                    ],
                }
            ],
            "proposed_action": "Investigate",
            "alternatives": ["Defer"],
            "unknowns": ["Measured impact"],
        }
    )
    task = service.tasks.update(
        task.id,
        task.revision,
        lambda task: setattr(
            task,
            "brief",
            Artifact(
                body=body,
                reviews=["accepted"],
                state="accepted",
                decision="Human direction",
                model="actual-model",
                provider="fixture",
            ),
        ),
    )
    docs = root / ".compass" / "documents"
    docs.mkdir()
    doc = StoredDocument(
        id="legacy-document",
        title="Older saved artifact",
        content_markdown="# Earlier human work\nPreserve this exact copy.",
    )
    (docs / f"{doc.id}.json").write_text(doc.model_dump_json())
    (root / ".compass" / "private.env").write_text("fictional-provider-secret")
    output = root / ".compass" / "output"
    output.mkdir(exist_ok=True)
    (output / "separate-export.md").write_text("Separate output stays original")
    destination = tmp_path / "saved.compass-backup.json"
    yield root, service, task, evidence, destination
    close_job_manager()


def test_backup_restore_retains_edits_snapshots_revisions_and_legacy_without_authority(
    saved, tmp_path
):
    root, service, task, evidence, destination = saved
    before = service.tasks.read(task.id).model_dump_json()
    receipt = create_backup(root, destination)
    assert receipt["tasks"] == 1 and receipt["revisions"] == 2 and receipt["documents"] == 1
    assert destination.stat().st_mode & 0o777 == 0o600
    encoded = destination.read_text()
    for secret in (
        "fictional-config-secret",
        "private-credential-ref",
        "fictional-provider-secret",
        "/fictional/local-source",
    ):
        assert secret not in encoded
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    (parent / "existing-file").write_text("Sentinel")
    result = restore_backup(destination, parent)
    restored = Path(result["workspace_path"])
    assert restored.parent == parent and restored != root
    assert TaskService(restored).tasks.read(task.id).model_dump_json() == before
    assert TaskService(restored).tasks.read(task.id, 1).brief is None
    assert TaskService(restored).selected_evidence(task)[0].content == evidence.content
    assert TaskService(restored).stale(task) is False
    exported = export_task(TaskService(restored), task.id, task.revision, "brief")
    assert (
        "Human direction" in exported["markdown"] and "Saved human summary" in exported["markdown"]
    )
    config = load_config(restored)
    assert (
        config.sources[0].path is None
        and config.sources[0].auth is None
        and not config.sources[0].options
    )
    assert (restored / ".compass" / "documents" / "legacy-document.json").read_bytes() == (
        root / ".compass" / "documents" / "legacy-document.json"
    ).read_bytes()
    assert not (restored / ".compass" / "private.env").exists()
    assert not (restored / ".compass" / "output" / "separate-export.md").exists()
    assert (parent / "existing-file").read_text() == "Sentinel"
    assert service.tasks.read(task.id).model_dump_json() == before


def rewrite(destination, change):
    archive = json.loads(destination.read_text())
    change(archive["payload"])
    archive["sha256"] = hashlib.sha256(encode(archive["payload"]).encode()).hexdigest()
    destination.write_text(encode(archive))


@pytest.mark.parametrize(
    "tamper", ["checksum", "snapshot", "citation", "path", "orphan_revision", "legacy_path"]
)
def test_corrupt_or_rewritten_unsafe_backup_never_creates_a_workspace(saved, tmp_path, tamper):
    _, _, _, _, destination = saved
    create_backup(saved[0], destination)
    if tamper == "checksum":
        destination.write_text(
            destination.read_text().replace("Saved human summary", "Tampered summary")
        )
    elif tamper == "snapshot":
        rewrite(destination, lambda data: data["snapshots"][-1].update(payload="[{}]"))
    elif tamper == "citation":

        def changed(data):
            for table in ("tasks", "revisions"):
                for row in data[table]:
                    task = json.loads(row["payload"])
                    if task["brief"]:
                        task["brief"]["body"]["claims"][0]["citations"][0]["quote"] = (
                            "Not the saved quote"
                        )
                        row["payload"] = encode(task)

        rewrite(destination, changed)
    elif tamper == "path":
        rewrite(
            destination, lambda data: data["product"]["sources"][0].update(path="/unapproved/path")
        )
    elif tamper == "legacy_path":
        rewrite(destination, lambda data: data["documents"][0].update(id="../outside"))
    else:
        rewrite(destination, lambda data: data.update(revisions=[]))
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    with pytest.raises((ValueError, TypeError)):
        restore_backup(destination, parent)
    assert list(parent.iterdir()) == []


def test_active_jobs_restore_interrupted_and_completed_history_is_not_replayed(saved, tmp_path):
    root, service, task, _, destination = saved
    get_job_manager()._own(service.tasks)
    with service.tasks._connect() as db:
        db.execute(
            "INSERT INTO jobs(id,task_id,base_revision,kind,request_id,state,stage,created_at,updated_at,deadline,model,provider) VALUES (?,?,?,'brief','orphan-request','running','generating',1,1,9999999999,'fixture-model','fixture')",
            ("a" * 32, task.id, 1),
        )
    with service.evidence.transaction() as db:
        db.execute(
            "INSERT INTO import_jobs VALUES (?,?,?,9999999999,1,?)",
            (
                "b" * 32,
                "orphan-import",
                "running",
                encode(
                    {
                        "selection": [],
                        "stage": "reading",
                        "results": [
                            {
                                "source_id": load_config(root).sources[0].id,
                                "name": "Selected docs",
                                "items": 0,
                                "state": "reading",
                            }
                        ],
                        "message": "",
                    }
                ),
            ),
        )
    create_backup(root, destination)
    parent = tmp_path / "restored"
    parent.mkdir()
    result = restore_backup(destination, parent)
    new = TaskService(Path(result["workspace_path"]))
    with new.tasks._connect() as db:
        row = db.execute("SELECT state,message FROM jobs").fetchone()
    assert row[0] == "interrupted" and "No inference request was replayed" in row[1]
    with new.evidence._lock:
        row = new.evidence._db.execute("SELECT state,payload FROM import_jobs").fetchone()
    assert row[0] == "interrupted" and json.loads(row[1])["results"][0]["state"] == "interrupted"


def test_failed_restore_publication_removes_only_its_staging_folder(saved, tmp_path):
    create_backup(saved[0], saved[4])
    parent = tmp_path / "parent"
    parent.mkdir()
    sentinel = parent / "existing"
    sentinel.mkdir()
    (sentinel / "keep").write_text("Existing work")
    with (
        patch(
            "compass.engine.workspace_backup.os.rename",
            side_effect=OSError("Fictional disk failure"),
        ),
        pytest.raises(OSError),
    ):
        restore_backup(saved[4], parent)
    assert list(parent.iterdir()) == [sentinel]
    assert (sentinel / "keep").read_text() == "Existing work"


def test_backup_rejects_symlinks_duplicate_fields_and_busy_operations(saved, tmp_path):
    root, service, _, _, destination = saved
    lease = get_job_manager().reserve_import(service.tasks)
    try:
        with pytest.raises(ValueError, match="Finish or cancel"):
            create_backup(root, destination)
    finally:
        get_job_manager().release_import(lease)
    destination.write_text('{"format":"one","format":"two"}')
    with pytest.raises(ValueError, match="duplicate"):
        read_backup(destination)
    link = tmp_path / "linked.compass-backup.json"
    link.symlink_to(destination)
    with pytest.raises(OSError):
        read_backup(link)


def test_failed_backup_readback_preserves_existing_archive_and_removes_temporary_file(saved):
    root, _, _, _, destination = saved
    destination.write_text("Previous backup sentinel")
    with (
        patch(
            "compass.engine.workspace_backup.read_backup",
            side_effect=ValueError("Fictional readback mismatch"),
        ),
        pytest.raises(ValueError, match="readback"),
    ):
        create_backup(root, destination)
    assert destination.read_text() == "Previous backup sentinel"
    assert not list(destination.parent.glob(".compass-backup-*.tmp"))


def test_backup_size_limit_keeps_prior_archive(saved):
    root, _, _, _, destination = saved
    destination.write_text("Previous backup sentinel")
    with (
        patch("compass.engine.workspace_backup.MAX_BYTES", 10),
        pytest.raises(ValueError, match="250 MB"),
    ):
        create_backup(root, destination)
    assert destination.read_text() == "Previous backup sentinel"


def test_authenticated_backup_restore_endpoints_are_not_renderer_capabilities(
    saved, tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from compass import server

    monkeypatch.setattr(server, "_engine_token", "fictional-backup-session")
    client = TestClient(server.app, headers={"Authorization": "Bearer fictional-backup-session"})
    root, _, _, _, destination = saved
    request = {"workspace_path": str(root), "backup_path": str(destination)}
    assert client.post("/workspace/backup", json=request).status_code == 200
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    response = client.post(
        "/workspace/restore", json={"backup_path": str(destination), "parent_path": str(parent)}
    )
    assert response.status_code == 200
    assert Path(response.json()["workspace_path"]).parent == parent
    assert TestClient(server.app).post("/workspace/backup", json=request).status_code == 401


@pytest.mark.parametrize("boundary", ["before_publish", "after_publish"])
def test_process_stop_at_restore_publication_preserves_backup_and_existing_folders(
    saved, tmp_path, boundary
):
    import os
    import subprocess
    import sys
    import time

    create_backup(saved[0], saved[4])
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    sentinel = parent / "existing.txt"
    sentinel.write_text("Existing user file")
    marker = tmp_path / "restore-ready"
    script = """
import sys
from pathlib import Path
from threading import Event
import compass.engine.workspace_backup as recovery
original=recovery.os.rename
def stop(source,destination):
    if sys.argv[4]=='after_publish':
        original(source,destination)
    Path(sys.argv[3]).write_text('ready')
    Event().wait(30)
recovery.os.rename=stop
recovery.restore_backup(Path(sys.argv[1]),Path(sys.argv[2]))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(saved[4]), str(parent), str(marker), boundary],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), (
            process.stderr.read().decode()
            if process.poll() is not None
            else "Restore did not reach publication"
        )
        process.kill()
        process.wait(timeout=5)
        assert sentinel.read_text() == "Existing user file"
        read_backup(saved[4])
        published = list(parent.glob("Compass restored *"))
        assert len(published) == int(boundary == "after_publish")
        if published:
            assert (
                TaskService(published[0]).tasks.read(saved[2].id).brief.decision
                == "Human direction"
            )
        retried = restore_backup(saved[4], parent)
        assert (
            TaskService(Path(retried["workspace_path"])).tasks.read(saved[2].id).brief.decision
            == "Human direction"
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()


def test_backup_api_explains_the_history_limit_without_overwriting_saved_files(saved, monkeypatch):
    from fastapi.testclient import TestClient

    from compass import server

    monkeypatch.setattr(server, "_engine_token", "fictional-backup-session")
    root, _, _, _, destination = saved
    destination.write_text("Previous backup sentinel")
    with patch("compass.engine.workspace_backup.MAX_BYTES", 10):
        response = TestClient(
            server.app, headers={"Authorization": "Bearer fictional-backup-session"}
        ).post(
            "/workspace/backup", json={"workspace_path": str(root), "backup_path": str(destination)}
        )
    assert response.status_code == 422 and "250 MB" in response.json()["detail"]
    assert destination.read_text() == "Previous backup sentinel"


def test_malformed_import_history_is_rejected_before_restore_staging(saved, tmp_path):
    create_backup(saved[0], saved[4])
    rewrite(
        saved[4],
        lambda data: data["imports"].append(
            {
                "id": "c" * 32,
                "request_id": "invalid-import",
                "state": "running",
                "deadline": 100,
                "created_at": 1,
                "payload": encode({"selection": [], "results": ["invalid-result"]}),
            }
        ),
    )
    with (
        patch("compass.engine.workspace_backup.tempfile.mkdtemp") as stage,
        pytest.raises(ValueError, match="import history"),
    ):
        restore_backup(saved[4], tmp_path)
    stage.assert_not_called()


def test_partial_import_coverage_is_preserved_in_backup_and_restore(saved, tmp_path):
    root, service, _, _, destination = saved
    source = load_config(root).sources[0]
    coverage = {
        "files_read": 1,
        "bytes_read": 20,
        "skipped_files": 1,
        "skipped_directories": 0,
        "skipped_examples": ["unsupported.pdf"],
        "complete": False,
    }
    payload = {
        "selection": [
            {"id": source.id, "name": source.name, "type": source.type, "config_hash": "a" * 64}
        ],
        "stage": "completed",
        "message": "Some files were omitted.",
        "results": [
            {
                "source_id": source.id,
                "name": source.name,
                "items": 1,
                "state": "completed",
                "coverage": coverage,
            }
        ],
    }
    with service.evidence.transaction() as db:
        db.execute(
            "INSERT INTO import_jobs VALUES (?,?,?,100,1,?)",
            ("d" * 32, "partial-import", "partial", encode(payload)),
        )
    create_backup(root, destination)
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    restored = TaskService(Path(restore_backup(destination, parent)["workspace_path"]))
    with restored.evidence._lock:
        state, raw = restored.evidence._db.execute(
            "SELECT state,payload FROM import_jobs"
        ).fetchone()
    assert state == "partial" and json.loads(raw)["results"][0]["coverage"] == coverage
