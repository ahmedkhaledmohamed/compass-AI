"""Durable human review, citation scope and late-response protections."""

from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from compass.config import ProductConfig, save_config
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.task_repository import TaskConflict, TaskNotFound, TaskRepository
from compass.engine.task_service import TaskService
from compass.models.sources import Evidence, SourceType
from compass.models.tasks import DraftBody
from compass.server import app


@pytest.fixture
def setup(tmp_path):
    save_config(ProductConfig(name="Task test"), tmp_path)
    kg = KnowledgeGraph(tmp_path / ".compass" / "knowledge")
    kg.add_many([
        Evidence(id="selected", source_name="strategy", source_type=SourceType.DOCS,
                 connector="docs", title="Strategy", content="Approved goal: improve retention."),
        Evidence(id="private", source_name="other", source_type=SourceType.DOCS,
                 connector="docs", title="Unselected", content="Unrelated confidential context."),
    ])
    service = TaskService(tmp_path)
    task = service.create("What should we prioritize?", "Product lead", "Choose one investigation", ["strategy"])
    body = {
        "title": "Retention decision", "summary": "Review this draft before deciding.",
        "claims": [{"text": "Retention is an approved goal.", "kind": "supported", "citations": [
            {"evidence_id": "selected", "start": 0, "end": 33, "quote": "Approved goal: improve retention."},
        ]}],
        "proposed_action": "Investigate retention barriers.", "alternatives": ["Defer investigation"],
        "unknowns": ["No measured retention data was selected"],
    }
    # Python offsets are Unicode code points, end exclusive.
    body["claims"][0]["citations"][0]["end"] = len(body["claims"][0]["citations"][0]["quote"])
    orchestrator = Mock(default_model="test-model")
    orchestrator.ask_json.return_value = body
    with patch("compass.engine.task_service.get_orchestrator", return_value=orchestrator):
        yield tmp_path, kg, service, task, body, orchestrator


def generated(setup):
    _, _, service, task, _, _ = setup
    return service.generate(task.id, task.revision, "brief")


def reviewed(setup):
    task = generated(setup)
    return setup[2].review(task.id, task.revision, "brief", 0, "accepted")


def test_pinned_scope_and_untrusted_evidence_prompt(setup):
    _, _, service, task, _, model = setup
    result = generated(setup)
    assert result.brief.state == "draft"
    assert result.brief.reviews == ["unreviewed"]
    prompt = model.ask_json.call_args.args[0]
    assert "Approved goal" in prompt
    assert "confidential context" not in prompt
    assert "untrusted data" in model.ask_json.call_args.kwargs["system"]
    assert [item.id for item in service.selected_evidence(task)] == ["selected"]


@pytest.mark.parametrize("failure", ["unknown_id", "unselected_id", "quote", "offset", "review_state"])
def test_invalid_model_output_never_commits_an_artifact(setup, failure):
    _, _, service, task, body, _ = setup
    citation = body["claims"][0]["citations"][0]
    if failure == "unknown_id":
        citation["evidence_id"] = "invented"
    elif failure == "unselected_id":
        citation["evidence_id"] = "private"
    elif failure == "quote":
        citation["quote"] = "invented support"
    elif failure == "offset":
        citation["end"] = 1000
    else:
        body["state"] = "accepted"
    with pytest.raises((ValueError, ValidationError)):
        service.generate(task.id, task.revision, "brief")
    assert service.tasks.read(task.id) == task


def test_acceptance_requires_explicit_claim_review_and_decision(setup):
    service = setup[2]
    task = generated(setup)
    with pytest.raises(ValueError, match="Review every claim"):
        service.accept(task.id, task.revision, "brief", "Investigate retention")
    task = service.review(task.id, task.revision, "brief", 0, "accepted")
    with pytest.raises(ValueError, match="Record your decision"):
        service.accept(task.id, task.revision, "brief", " ")
    task = service.accept(task.id, task.revision, "brief", "Investigate retention")
    assert task.brief.state == "accepted"
    assert task.brief.decision == "Investigate retention"


def test_corrections_and_reviews_survive_restart_and_preserve_history(setup):
    path, _, service, _, body, _ = setup
    task = reviewed(setup)
    prior_revision = task.revision
    body["claims"][0]["text"] = "User corrected the interpretation."
    task = service.revise(task.id, task.revision, "brief", DraftBody.model_validate(body))
    assert task.brief.reviews == ["unreviewed"]
    assert task.brief.edited_by_user
    reopened = TaskService(path).tasks.read(task.id)
    assert reopened == task
    assert service.tasks.read(task.id, prior_revision).brief.reviews == ["accepted"]


def test_handoff_requires_accepted_direction_and_brief_changes_invalidate_it(setup):
    service = setup[2]
    task = generated(setup)
    with pytest.raises(ValueError, match="choose a direction"):
        service.generate(task.id, task.revision, "handoff")
    task = service.review(task.id, task.revision, "brief", 0, "accepted")
    task = service.accept(task.id, task.revision, "brief", "Investigate retention")
    task = service.generate(task.id, task.revision, "handoff")
    assert task.handoff.state == "draft"
    assert task.handoff.reviews == ["unreviewed"]
    task = service.review(task.id, task.revision, "brief", 0, "rejected")
    assert task.handoff is None
    assert task.brief.state == "draft"


def test_rejected_claims_do_not_enter_handoff_context(setup):
    service = setup[2]
    body = setup[4]
    body["claims"].append({"text": "Rejected proposal should not be reused", "kind": "proposal", "citations": []})
    task = generated(setup)
    task = service.review(task.id, task.revision, "brief", 0, "accepted")
    task = service.review(task.id, task.revision, "brief", 1, "rejected")
    task = service.accept(task.id, task.revision, "brief", "Investigate retention")
    service.generate(task.id, task.revision, "handoff")
    assert "Rejected proposal should not be reused" not in setup[5].ask_json.call_args.args[0]


def test_late_model_response_cannot_overwrite_a_review(setup):
    service = setup[2]
    task = generated(setup)

    def late_response(*args, **kwargs):
        service.review(task.id, task.revision, "brief", 0, "accepted")
        return setup[4]

    setup[5].ask_json.side_effect = late_response
    with pytest.raises(TaskConflict):
        service.generate(task.id, task.revision, "brief")
    assert service.tasks.read(task.id).brief.reviews == ["accepted"]


def test_stale_sources_keep_original_passages_and_block_acceptance(setup):
    _, kg, service, _, _, _ = setup
    task = reviewed(setup)
    kg.replace_source("strategy", [Evidence(
        id="new", source_name="strategy", source_type=SourceType.DOCS,
        connector="docs", title="Changed strategy", content="New goal: reliability.",
    )])
    assert service.stale(task)
    assert service.selected_evidence(task)[0].content == "Approved goal: improve retention."
    with pytest.raises(TaskConflict, match="Selected evidence changed"):
        service.accept(task.id, task.revision, "brief", "Investigate retention")


def test_unselected_source_refresh_does_not_make_task_stale(setup):
    _, kg, service, task, _, _ = setup
    kg.replace_source("other", [])
    assert not service.stale(task)


def test_provider_failure_and_context_budget_never_create_artifacts(setup):
    service, task = setup[2:4]
    setup[5].ask_json.side_effect = RuntimeError("provider unavailable")
    with pytest.raises(RuntimeError):
        generated(setup)
    assert service.tasks.read(task.id).brief is None
    with patch("compass.engine.task_service.MAX_CONTEXT_CHARS", 10), pytest.raises(ValueError, match="context limit"):
        generated(setup)
    assert service.tasks.read(task.id).brief is None


def test_task_ids_cannot_read_another_workspace(setup, tmp_path):
    path, _, _, task, _, _ = setup
    other = path / "other-workspace"
    other.mkdir()
    with pytest.raises(TaskNotFound):
        TaskRepository(other).read(task.id)


def test_api_create_review_reopen_and_conflicting_save(setup, engine_headers):
    path, _, service, _, body, _ = setup
    with TestClient(app, headers=engine_headers) as client:
        response = client.post("/tasks/create", json={
            "workspace_path": str(path), "question": "API question", "reader": "PM",
            "completion_condition": "Choose a direction", "source_names": ["strategy"],
        })
        assert response.status_code == 200
        task = response.json()["task"]
        operation = {"workspace_path": str(path), "task_id": task["id"],
                     "expected_revision": task["revision"], "kind": "brief"}
        response = client.post("/tasks/generate", json=operation)
        assert response.status_code == 200
        operation["expected_revision"] = response.json()["task"]["revision"]
        response = client.post("/tasks/review", json={**operation, "claim_index": 0, "decision": "accepted"})
        assert response.status_code == 200
        assert client.post("/tasks/revise", json={**operation, "body": body}).status_code == 409
        reopened = client.post("/tasks/get", json={"workspace_path": str(path), "task_id": task["id"]})
        assert reopened.json()["task"]["brief"]["reviews"] == ["accepted"]
        assert client.post("/tasks/get", json={"workspace_path": str(path), "task_id": "missing"}).status_code == 404
        assert len(service.tasks.list()) == 2


def test_task_storage_and_evidence_symlinks_cannot_escape_workspace(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".compass").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="inside the selected workspace"):
        TaskService(workspace)
    assert not (outside / "tasks.sqlite3").exists()
    (workspace / ".compass").unlink()
    (workspace / ".compass").mkdir()
    (workspace / ".compass" / "knowledge").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="inside the selected workspace"):
        TaskService(workspace)
    assert not (outside / "evidence.sqlite3").exists()


def test_new_task_database_is_private(tmp_path):
    import stat

    repository = TaskRepository(tmp_path)
    assert stat.S_IMODE(repository.path.stat().st_mode) == 0o600
