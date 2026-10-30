"""Scoped ranking, original offsets, receipts and model-context citation bounds."""
import sqlite3
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from compass.config import ProductConfig, save_config
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.passage_retrieval import (
    RetrievalEmpty,
    RetrievalError,
    line_range,
    passages_for,
    retrieve,
)
from compass.engine.task_jobs import close_job_manager
from compass.engine.task_repository import TaskRepository
from compass.engine.task_service import MAX_CONTEXT_CHARS, SYSTEM, TaskService
from compass.models.sources import Evidence, SourceType
from compass.server import app


def evidence(identifier, content, source="selected", title="Matching title"):
    return Evidence(id=identifier, source_name=source, source_type=SourceType.DOCS, connector="docs",
                    title=title, content=content)


def test_tail_ranking_preserves_original_unicode_offsets_and_stable_locators():
    text = "# Strategy 😀\r\n" + "Unrelated context. " * 5_000 + "\nZephyr retention finding: no launch is approved.\n"
    item = evidence("tail", text)
    passages, receipt = retrieve([item], "snapshot", "zephyr retention finding", search=True)
    assert receipt.status == "partial"
    assert receipt.selection_mode == "keyword_ranked"
    assert "Zephyr retention finding" in passages[0]["content"]
    assert passages[0]["start"] > 60_000
    assert all(text[p["start"]:p["end"]] == p["content"] for p in passages)
    assert [p["chunk_id"] for p in retrieve([item], "snapshot", "zephyr retention finding", search=True)[0]] == [p["chunk_id"] for p in passages]
    assert receipt.provided_characters < receipt.total_characters
    assert [p.rank for p in receipt.passages] == list(range(1, len(passages) + 1))


def test_rank_order_is_retained_and_duplicate_titles_do_not_conflate_sources():
    low = evidence("low", "Retention matters. " + "Other topics. " * 100)
    high = evidence("high", "Retention retention retention retention retention.")
    passages, receipt = retrieve([low, high], "snapshot", "retention", search=True)
    assert passages[0]["id"] == "high"
    assert {p["id"] for p in passages}=={"low","high"}
    assert [p["score"] for p in passages] == sorted((p["score"] for p in passages), reverse=True)
    assert len({p["document_id"] for p in passages}) == len(passages)
    assert receipt.matched_passages == len(passages)


@pytest.mark.parametrize("text", ["a\nb\nc\n", "a\r\nb\r\nc\r\n", "a\rb\rc\r"])
def test_line_locators_handle_lf_crlf_and_cr(text):
    start = text.index("b")
    end = text.index("c")
    assert line_range(text, start, end) == (2, 2)
    chunk = next(passages_for(evidence("lines", text)))
    assert (chunk["line_start"], chunk["line_end"]) == (1, 3)


def test_search_no_match_is_explicit_and_query_operators_are_data():
    item = evidence("one", "A retention goal was selected.")
    passages, receipt = retrieve([item], "snapshot", "unicorn", search=True)
    assert passages == []
    assert receipt.status == "empty"
    assert receipt.matched_passages == 0
    assert retrieve([item], "snapshot", 'retention" OR * NOT', search=True)[0]
    complete, all_text = retrieve([item], "snapshot", "What next?")
    assert complete
    assert all_text.selection_mode == "all_saved_text"
    assert all_text.matched_passages is None


def test_search_failure_is_not_returned_as_an_empty_success():
    with patch("compass.engine.passage_retrieval.sqlite3.connect", side_effect=sqlite3.OperationalError("fixture failure")), pytest.raises(RetrievalError, match="search failed"):
        retrieve([evidence("one", "Retention is selected")], "snapshot", "retention", search=True)


@pytest.fixture
def setup(tmp_path):
    save_config(ProductConfig(name="Retrieval"), tmp_path)
    text = "# Selected strategy 😀\nUnshown preface: alpine migration is planned.\n" + "Background material. " * 6_000 + "\nZephyr retention finding: no launch is approved.\n"
    selected = evidence("selected", text, title="Selected strategy")
    secret = evidence("unselected", "Zephyr retention secret from an unselected source", source="private")
    kg = KnowledgeGraph(tmp_path / ".compass" / "knowledge")
    kg.add_many([selected, secret])
    service = TaskService(tmp_path)
    task = service.create("Which Zephyr retention finding matters?", "PM", "Choose a Zephyr investigation", ["selected"])
    model = Mock(default_model="fictional-model")
    yield tmp_path, selected, secret, service, task, model
    close_job_manager()


def body_for(item, quote):
    start = item.content.index(quote)
    return {"title":"Decision", "summary":"Review the saved finding.", "claims":[{"text":"The launch is not approved.","kind":"supported",
        "citations":[{"evidence_id":item.id,"start":start,"end":start+len(quote),"quote":quote}]}],
        "proposed_action":"Investigate retention", "alternatives":["Defer"], "unknowns":["No rollout result is selected."]}


def generate(setup):
    with patch("compass.engine.task_service.get_orchestrator", return_value=setup[5]):
        return setup[3].generate(setup[4].id, setup[4].revision, "brief")


def test_large_task_generation_uses_tail_passages_and_persists_receipt(setup):
    root, selected, secret, service, task, model = setup
    model.ask_json.return_value = body_for(selected, "Zephyr retention finding: no launch is approved.")
    saved = generate(setup)
    receipt = saved.brief.retrieval
    assert receipt.status == "partial"
    assert all(locator.evidence_id != secret.id for locator in receipt.passages)
    prompt = model.ask_json.call_args.args[0]
    assert "unselected source" not in prompt
    assert "Unshown preface" not in prompt
    assert len(prompt) + len(SYSTEM) + 100 <= MAX_CONTEXT_CHARS
    assert "Zephyr retention finding" in prompt
    restored = TaskService(root).tasks.read(task.id)
    assert restored.brief.retrieval == receipt
    assert restored.brief.body.claims[0].citations[0].start > 60_000
    revised = service.revise(task.id, saved.revision, "brief", saved.brief.body)
    assert revised.brief.retrieval == receipt
    assert revised.brief.edited_by_user


def test_a_true_quote_outside_the_model_passages_is_rejected(setup):
    setup[5].ask_json.return_value = body_for(setup[1], "Unshown preface: alpine migration is planned.")
    with pytest.raises(ValueError, match="not inside a passage provided"):
        generate(setup)
    assert setup[3].tasks.read(setup[4].id).brief is None


def test_no_match_does_not_call_a_provider(setup):
    task = setup[3].create("Unicorn glider", "PM", "Unicorn glider", ["selected"])
    with patch("compass.engine.task_service.get_orchestrator", return_value=setup[5]), pytest.raises(RetrievalEmpty):
        setup[3].generate(task.id, task.revision, "brief")
    setup[5].ask_json.assert_not_called()


def test_receipts_and_passage_api_are_bound_to_the_pinned_task_scope(setup, engine_headers):
    root, selected, secret, service, task, _ = setup
    with TestClient(app, headers=engine_headers) as client:
        request = {"workspace_path":str(root),"task_id":task.id}
        search = client.post("/tasks/retrieve",json={**request,"query":"Zephyr"}).json()
        assert search["status"] == "ok"
        assert all(p["id"] == selected.id for p in search["passages"])
        assert search["receipt"]["selected_documents"] == 1
        assert client.post("/tasks/passage",json={**request,"evidence_id":secret.id,"start":0,"end":10}).status_code == 422
        quote = "Zephyr retention finding: no launch is approved."
        start = selected.content.index(quote)
        source = client.post("/tasks/passage",json={**request,"evidence_id":selected.id,"start":start,"end":start+len(quote)}).json()
        assert source["passage"]["content"] == quote
        summaries = client.post("/tasks/evidence",json={**request,"summary_only":True}).json()
        assert len(summaries["items"][0]["content"]) <= 500
        assert summaries["items"][0]["content_length"] == len(selected.content)
        assert summaries["items"][0]["content_partial"]
        assert service.selected_evidence(task)[0].content == selected.content


def test_v3_task_history_has_verified_backup_before_receipt_schema_upgrade(setup):
    root, _, _, service, task, _ = setup
    with sqlite3.connect(service.tasks.path) as db:
        db.execute("ALTER TABLE jobs DROP COLUMN connection")
        db.execute("PRAGMA user_version=3")
    migrated = TaskRepository(root)
    assert migrated.read(task.id) == task
    backups = list((root / ".compass" / "task-backups").glob("tasks-v3-*.sql"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(":memory:") as restored:
        restored.executescript(backups[0].read_text())
        assert restored.execute("PRAGMA user_version").fetchone()[0] == 3
        assert restored.execute("SELECT payload FROM tasks WHERE id=?",(task.id,)).fetchone()
    with sqlite3.connect(migrated.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5


def test_required_accepted_support_is_kept_before_ranked_distractors():
    from compass.models.tasks import Citation
    accepted = evidence("accepted", "Approved Zephyr finding.")
    others = [evidence(f"other-{index}",f"rareword{index} rareword{index+1}") for index in range(40)]
    citation = Citation(evidence_id=accepted.id,start=0,end=24,quote="Approved Zephyr finding.")
    query=" ".join(f"rareword{index}" for index in range(40))
    passages,receipt=retrieve([accepted,*others],"snapshot",query,search=True,required=[citation])
    assert passages[0]["id"]==accepted.id
    assert receipt.passages[0].reason=="accepted_brief"
    assert len(passages)<=16
    assert any(p["id"].startswith("other-") for p in passages)


def test_invalid_accepted_quote_and_unbounded_metadata_cannot_enter_context():
    from compass.models.tasks import Citation
    item=evidence("selected","Saved retention finding.")
    item.metadata["source_coverage"]="invalid legacy coverage"
    passages,receipt=retrieve([item],"snapshot","retention")
    assert passages[0]["coverage"] is None
    assert receipt.unknown_import_documents==1
    wrong=Citation(evidence_id=item.id,start=0,end=5,quote="Wrong")
    with pytest.raises(RetrievalError,match="no longer matches"):
        retrieve([item],"snapshot","retention",required=[wrong])
