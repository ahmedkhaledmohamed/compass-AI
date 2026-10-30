"""Cancellation, deadlines, crash recovery and transactional generation fences."""

import sqlite3
import subprocess
import sys
import time
from threading import Event
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from compass.config import ProductConfig, save_config
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.task_jobs import JobManager, JobStopped
from compass.engine.task_repository import TaskConflict, TaskNotFound, TaskRepository
from compass.engine.task_service import TaskService
from compass.models.sources import Evidence, SourceType
from compass.server import app


@pytest.fixture
def job_setup(tmp_path):
    save_config(ProductConfig(name="Fictional jobs"), tmp_path)
    kg = KnowledgeGraph(tmp_path / ".compass" / "knowledge")
    kg.add(Evidence(id="one", source_name="strategy", source_type=SourceType.DOCS,
                    connector="docs", title="Goal", content="Selected goal."))
    service = TaskService(tmp_path)
    task = service.create("Which investigation?", "PM", "Choose a direction", ["strategy"])
    body = {"title": "Decision", "summary": "Review the proposal.", "claims": [
        {"kind": "supported", "text": "A goal is selected.", "citations": [
            {"evidence_id": "one", "start": 0, "end": 14, "quote": "Selected goal."}]}],
        "proposed_action": "Investigate", "alternatives": ["Defer"], "unknowns": []}
    model = Mock(default_model="fixture-model")
    model.ask_json.return_value = body
    started, release = Event(), Event()
    manager = JobManager()
    yield tmp_path, service, task, body, model, manager, started, release
    release.set()
    manager.close()


def block(setup):
    def response(*args, **kwargs):
        setup[6].set()
        assert setup[7].wait(4), "Test did not release its fictional provider"
        return setup[3]
    setup[4].ask_json.side_effect = response


def start(setup, request_id="request", timeout=3):
    return setup[5].start(setup[1], setup[2].id, setup[2].revision, "brief", request_id, setup[4], timeout)


def until(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Job condition did not finish")


def test_success_commits_artifact_and_job_result_together(job_setup):
    _, service, task, _, _, manager, _, _ = job_setup
    job = start(job_setup)
    result = manager.wait(service, task.id, job["id"])
    saved = manager.get(service, task.id, job["id"])
    assert saved["state"] == "succeeded"
    assert saved["result_revision"] == result.revision
    assert service.tasks.read(task.id, saved["result_revision"]).brief == result.brief


def test_cancel_during_provider_call_is_fast_and_never_saves_late_output(job_setup):
    _, service, task, _, model, manager, started, release = job_setup
    block(job_setup)
    job = start(job_setup)
    assert started.wait(2)
    before = time.monotonic()
    cancelled = manager.cancel(service, task.id, job["id"])
    assert time.monotonic() - before < 2
    assert cancelled["state"] == "cancelled"
    assert cancelled["draining"]
    with pytest.raises(TaskConflict, match="still running or stopping"):
        start(job_setup, request_id="second")
    with pytest.raises(JobStopped):
        manager.wait(service, task.id, job["id"])
    release.set()
    until(lambda: not manager.get(service, task.id, job["id"])["draining"])
    assert service.tasks.read(task.id).brief is None
    assert model.ask_json.call_count == 1


def test_deadline_rejects_provider_response_and_preserves_task(job_setup):
    _, service, task, _, _, manager, started, release = job_setup
    block(job_setup)
    job = start(job_setup, timeout=0.1)
    assert started.wait(2)
    until(lambda: manager.get(service, task.id, job["id"])["state"] == "timed_out")
    release.set()
    until(lambda: not manager.get(service, task.id, job["id"])["draining"])
    assert service.tasks.read(task.id) == task


def test_deadline_passing_inside_artifact_transaction_rolls_back_all_writes(job_setup):
    _, service, task, _, _, manager, started, release = job_setup
    block(job_setup)
    job = start(job_setup, timeout=0.15)
    assert started.wait(2)

    def slow_commit(current):
        current.question = "This edit must roll back"
        time.sleep(0.2)

    with pytest.raises(JobStopped):
        service.tasks.update(task.id, task.revision, slow_commit, job_id=job["id"])
    release.set()
    until(lambda: not manager.get(service, task.id, job["id"])["draining"])
    assert service.tasks.read(task.id) == task


def test_duplicate_submission_and_retry_do_not_repeat_inference(job_setup):
    _, service, task, _, model, manager, started, release = job_setup
    block(job_setup)
    first = start(job_setup)
    assert started.wait(2)
    assert start(job_setup)["id"] == first["id"]
    release.set()
    manager.wait(service, task.id, first["id"])
    assert start(job_setup)["id"] == first["id"]
    assert model.ask_json.call_count == 1
    with pytest.raises(TaskConflict, match="different operation"):
        manager.start(service, task.id, task.revision, "handoff", "request", model)


def test_job_errors_are_redacted_without_committing_partial_results(job_setup):
    _, service, task, _, model, manager, _, _ = job_setup
    model.ask_json.side_effect = RuntimeError("secret-fixture-value raw source text")
    job = start(job_setup)
    until(lambda: manager.get(service, task.id, job["id"])["state"] == "failed")
    result = manager.get(service, task.id, job["id"])
    assert result["error_code"] == "provider_error"
    assert "secret-fixture-value" not in str(result)
    assert service.tasks.read(task.id).brief is None


def test_job_lookup_is_scoped_to_task_and_workspace(job_setup, tmp_path):
    _, service, task, _, _, manager, _, _ = job_setup
    job = start(job_setup)
    manager.wait(service, task.id, job["id"])
    with pytest.raises(TaskNotFound):
        manager.get(service, "different-task", job["id"])
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(TaskNotFound):
        manager.get(TaskService(other), task.id, job["id"])


def test_completed_job_survives_manager_restart_without_replay(job_setup):
    _, service, task, _, model, manager, _, _ = job_setup
    job = start(job_setup)
    manager.wait(service, task.id, job["id"])
    manager.close()
    restored = JobManager()
    try:
        assert restored.latest(service, task.id)["state"] == "succeeded"
        assert restored.start(service, task.id, task.revision, "brief", "request", model)["id"] == job["id"]
        assert model.ask_json.call_count == 1
    finally:
        restored.close()


def test_live_engine_lock_and_actual_process_death_recovery(job_setup):
    _, service, task, _, model, manager, _, _ = job_setup
    code = """
import fcntl, os, sqlite3, sys, time
path, task_id = sys.argv[1:]
fd = os.open(path + '/jobs.lock', os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
db = sqlite3.connect(path + '/tasks.sqlite3')
db.execute("INSERT INTO jobs(id,task_id,base_revision,kind,request_id,state,stage,created_at,updated_at,deadline,model,provider) VALUES ('crashed',?,1,'brief','crash-request','running','generating',?,?,?,'fixture','fixture')", (task_id,time.time(),time.time(),time.time()+120))
db.commit()
print('ready', flush=True)
time.sleep(60)
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(service.tasks.path.parent), task.id], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(TaskConflict, match="another engine"):
            manager.latest(service, task.id)
        process.kill()
        process.wait(timeout=3)
        recovered = manager.latest(service, task.id)
        assert recovered["state"] == "interrupted"
        assert service.tasks.read(task.id).brief is None
        model.ask_json.assert_not_called()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_version_one_migration_backup_restores_prior_tasks(job_setup):
    path, service, task, _, _, manager, _, _ = job_setup
    job = start(job_setup)
    task = manager.wait(service, task.id, job["id"])
    task = service.review(task.id, task.revision, "brief", 0, "accepted")
    manager.close()
    with sqlite3.connect(service.tasks.path) as db:
        db.execute("DROP TABLE jobs")
        db.execute("PRAGMA user_version=1")
    reopened = TaskRepository(path)
    assert reopened.read(task.id) == task
    backups = list((service.tasks.path.parent / "task-backups").glob("*.sql"))
    assert len(backups) == 1
    with sqlite3.connect(":memory:") as restored:
        restored.executescript(backups[0].read_text())
        assert restored.execute("PRAGMA user_version").fetchone()[0] == 1
        assert restored.execute("SELECT payload FROM tasks").fetchone()[0] == task.model_dump_json()
        assert restored.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == task.revision


def test_async_api_can_cancel_and_recover_status_after_reopen(job_setup, monkeypatch, engine_headers):
    path, service, task, _, model, manager, started, release = job_setup
    block(job_setup)
    monkeypatch.setattr("compass.engine.task_jobs._manager", manager)
    with patch("compass.engine.task_service.get_orchestrator", return_value=model), TestClient(app, headers=engine_headers) as client:
        operation = {"workspace_path": str(path), "task_id": task.id, "expected_revision": task.revision,
                     "kind": "brief", "request_id": "api-request"}
        response = client.post("/tasks/jobs/start", json=operation)
        assert response.status_code == 202
        job = response.json()["job"]
        assert started.wait(2)
        reference = {"workspace_path": str(path), "task_id": task.id, "job_id": job["id"]}
        assert client.post("/tasks/jobs/latest", json={"workspace_path": str(path), "task_id": task.id}).json()["job"]["id"] == job["id"]
        assert client.post("/tasks/jobs/cancel", json=reference).json()["job"]["state"] == "cancelled"
        release.set()
        until(lambda: not client.post("/tasks/jobs/status", json=reference).json()["job"]["draining"])
        assert service.tasks.read(task.id).brief is None
