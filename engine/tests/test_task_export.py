"""Reviewed exports preserve canonical quotes, edits and explicit provenance."""
import json
from unittest.mock import patch

import pytest

from compass.config import ProductConfig, save_config
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.task_export import block, export_task
from compass.engine.task_repository import TaskConflict
from compass.engine.task_service import TaskService
from compass.models.sources import Evidence, SourceType
from compass.models.tasks import Artifact, DraftBody


@pytest.fixture
def saved(tmp_path):
    save_config(ProductConfig(name="Fictional"),tmp_path)
    content="😀 First line.\r\nObserved retention concern.\r\nNo launch approved."
    item=Evidence(id="selected",source_name="Selected docs",source_type=SourceType.DOCS,connector="docs",title="Strategy",content=content,
                  metadata={"private_path":"/fictional/private/account","access_token":"fictional-secret"})
    other=Evidence(id="unselected",source_name="Other docs",source_type=SourceType.DOCS,connector="docs",title="Private strategy",content="Unselected confidential passage.")
    kg=KnowledgeGraph(tmp_path/".compass"/"knowledge");kg.add_many([item,other])
    service=TaskService(tmp_path)
    task=service.create("Which retention concern matters?","PM","Choose investigation",["Selected docs"])
    quote="Observed retention concern.";start=content.index(quote)
    body=DraftBody.model_validate({"title":"Reviewed & corrected <title>","summary":"Corrected summary","claims":[
        {"text":"A concern was reported.","kind":"supported","citations":[{"evidence_id":item.id,"start":start,"end":start+len(quote),"quote":quote}]},
        {"text":"Rejected observation must not be exported.","kind":"proposal","citations":[]},
        {"text":"Proposed investigation, not implemented.","kind":"proposal","citations":[]}],
        "proposed_action":"Investigate <script>alert('fixture')</script>","alternatives":["Defer"],"unknowns":["Measured impact"]})
    artifact=Artifact(body=body,reviews=["accepted","rejected","accepted"],state="accepted",decision="Chosen direction after review",model="actual-model",provider="openrouter:chosen-route",edited_by_user=True)
    task=service.tasks.update(task.id,task.revision,lambda task:setattr(task,"brief",artifact))
    return service,task,item,kg


def test_export_preserves_reviewed_edits_and_exact_quotes_without_unrelated_data(saved):
    service,task,item,_=saved
    before=service.tasks.read(task.id).model_dump_json()
    result=export_task(service,task.id,task.revision,"brief")
    manifest=result["manifest"];markdown=result["markdown"]
    assert "Corrected summary" in markdown and "Chosen direction after review" in markdown
    assert "Rejected observation must not be exported" not in markdown
    assert "Unselected confidential" not in markdown
    assert "fictional-secret" not in markdown and "/fictional/private" not in markdown
    assert "<script>" not in markdown and "&lt;script&gt;" in markdown
    assert manifest["omitted_rejected_claims"]==1
    assert len(manifest["sources"])==1
    citation=manifest["citations"][0]
    assert item.content[citation["start"]:citation["end"]]==citation["quote"]
    assert (citation["line_start"],citation["line_end"])==(2,2)
    assert manifest["source_freshness_at_export"]=="unchanged"
    assert manifest["retrieval"] is None
    assert "coverage was not recorded" in markdown
    assert block(json.dumps(manifest,ensure_ascii=False,indent=2),"json") in markdown
    assert service.tasks.read(task.id).model_dump_json()==before


def test_stale_export_uses_original_quote_and_warns_in_readable_text_and_manifest(saved):
    service,task,item,kg=saved
    kg.clear();kg.add(item.model_copy(update={"content":"A different source revision."}))
    result=export_task(service,task.id,task.revision,"brief")
    assert result["manifest"]["source_freshness_at_export"]=="needs_review"
    assert "Sources changed: this artifact needs review" in result["markdown"]
    assert "Observed retention concern." in result["markdown"]
    assert "A different source revision" not in result["markdown"]


def test_unaccepted_or_changed_artifact_cannot_export_and_invalid_quotes_fail(saved):
    service,task,_,_=saved
    with pytest.raises(TaskConflict):
        export_task(service,task.id,task.revision-1,"brief")
    task=service.tasks.update(task.id,task.revision,lambda task:setattr(task.brief,"state","draft"))
    with pytest.raises(ValueError,match="Accept the reviewed"):
        export_task(service,task.id,task.revision,"brief")
    def corrupt(task):
        task.brief.state="accepted";task.brief.body.claims[0].citations[0].quote="Incorrect quote"
    task=service.tasks.update(task.id,task.revision,corrupt)
    with pytest.raises(ValueError,match="quote does not match"):
        export_task(service,task.id,task.revision,"brief")


def test_revision_change_while_preparing_export_is_a_conflict(saved):
    service,task,_,_=saved
    original=service.stale
    def changed(value):
        service.tasks.update(task.id,task.revision,lambda task:setattr(task.brief,"decision","New direction"))
        return original(value)
    with patch.object(service,"stale",side_effect=changed),pytest.raises(TaskConflict,match="while preparing"):
        export_task(service,task.id,task.revision,"brief")


def test_source_fences_cannot_escape_their_text_block():
    quote="```\n<script>fictional</script>\n````\n# Untrusted heading"
    fenced=block(quote)
    assert fenced.startswith("`````text\n") and fenced.endswith("\n`````")
    assert fenced[len("`````text\n"):-len("\n`````")]==quote


def test_authenticated_export_api_checks_revision_and_returns_source_manifest(saved,monkeypatch):
    from fastapi.testclient import TestClient

    from compass import server
    service,task,_,_=saved
    monkeypatch.setattr(server,"_engine_token","fictional-export-session")
    client=TestClient(server.app,headers={"Authorization":"Bearer fictional-export-session"})
    request={"workspace_path":str(service.tasks.path.parent.parent),"task_id":task.id,"expected_revision":task.revision,"kind":"brief"}
    response=client.post("/tasks/export",json=request)
    assert response.status_code==200
    assert response.json()["manifest"]["task_revision"]==task.revision
    assert client.post("/tasks/export",json={**request,"expected_revision":1}).status_code==409
    assert TestClient(server.app).post("/tasks/export",json=request).status_code==401


def test_handoff_export_retains_parent_direction_and_its_own_acceptance(saved):
    service,task,_,_=saved
    def handoff(value):
        value.handoff=value.brief.model_copy(deep=True)
        value.handoff.decision="Chosen handoff acceptance"
    task=service.tasks.update(task.id,task.revision,handoff)
    result=export_task(service,task.id,task.revision,"handoff")
    assert result["manifest"]["accepted_brief_direction"]==task.brief.decision
    assert "Chosen direction after review" in result["markdown"]
    assert "Chosen handoff acceptance" in result["markdown"]
    assert result["manifest"]["edited_by_user"] is True
