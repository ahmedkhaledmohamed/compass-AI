"""Native grants stay ephemeral; durable jobs retain only connection metadata."""
import json
import sqlite3
from threading import Event
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from compass.config import ProductConfig, save_config
from compass.engine.knowledge_graph import KnowledgeGraph
from compass.engine.native_inference import NativeGrant, NativeInference
from compass.engine.orchestrator import Orchestrator
from compass.engine.provider_transport import ProviderError, RequestControl
from compass.engine.task_jobs import close_job_manager, get_job_manager
from compass.engine.task_service import TaskService
from compass.models.sources import Evidence, SourceType


def grant(**change):
    return NativeGrant.model_validate({"url":"http://127.0.0.1:12345/complete", "token":"a"*64,
        "connection":{"id":"b"*32,"revision":7,"provider":"openrouter","model":"fixture/model","serving_provider":"fixture-serving"},**change})


@pytest.mark.parametrize("url", ["https://provider.invalid/complete","http://localhost:123/complete","http://127.0.0.1:123/other","http://127.0.0.1:123/complete?url=remote","http://user@127.0.0.1:123/complete"])
def test_grant_rejects_noncanonical_broker_urls(url):
    with pytest.raises(ValidationError):
        grant(url=url)


def test_grant_repr_masks_capability_and_transport_does_not_have_provider_credentials():
    native = grant()
    assert native.token.get_secret_value() not in repr(native)
    binding = {"task_id":"fixture"}
    seen = []
    def response(request):
        seen.append(request)
        return httpx.Response(200,json={"text":"{}","model":"actual-model","provider":"openrouter:fixture-serving","input_tokens":3,"output_tokens":None})
    provider = NativeInference(native,binding,transport=httpx.MockTransport(response))
    result = provider.complete_controlled("fictional prompt","system","fixture/model",100,RequestControl())
    assert result.model=="actual-model" and result.output_tokens is None
    assert json.loads(seen[0].content)["binding"]==binding
    assert seen[0].headers["Authorization"]=="Bearer "+native.token.get_secret_value()
    assert not hasattr(provider,"_credential")
    with pytest.raises(ProviderError,match="permission"):
        provider.complete_controlled("p","s","other-model",100,RequestControl())
    assert len(seen)==1


def test_cancel_closes_the_broker_request_without_a_completion():
    import asyncio
    started,cancelled,closed=Event(),Event(),Event()
    async def response(request):
        started.set()
        try:
            await asyncio.sleep(5)
        finally:
            closed.set()
    provider=NativeInference(grant(),{},transport=httpx.MockTransport(response))
    from threading import Timer
    timer=Timer(.05,cancelled.set);timer.start()
    try:
        with pytest.raises(ProviderError) as caught:
            provider.complete_controlled("p","s","fixture/model",100,RequestControl(2,cancelled.is_set))
        assert caught.value.code=="cancelled"
        assert started.is_set() and closed.is_set()
    finally:
        timer.cancel()


def test_native_job_receipt_survives_reopen_but_capability_is_never_persisted(tmp_path):
    save_config(ProductConfig(name="Fictional"),tmp_path)
    item=Evidence(id="goal",source_name="Docs",source_type=SourceType.DOCS,connector="docs",title="Strategy",content="Improve retention.")
    KnowledgeGraph(tmp_path/".compass"/"knowledge").add(item)
    service=TaskService(tmp_path)
    task=service.create("What next?","PM","Choose",["Docs"])
    body={"title":"Decision","summary":"A draft","claims":[{"text":"Retention is a goal","kind":"supported","citations":[{"evidence_id":item.id,"start":0,"end":len(item.content),"quote":item.content}]}],"proposed_action":"Investigate","alternatives":["Defer"],"unknowns":["Impact"]}
    native=grant()
    provider=NativeInference(native,{},transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"text":json.dumps(body),"model":"actual-model","provider":"openrouter:fixture-serving","input_tokens":10,"output_tokens":5})))
    try:
        with patch("compass.engine.task_service.get_orchestrator",return_value=Orchestrator(provider,"fixture/model")):
            job=service.start_job(task.id,task.revision,"brief","native-request")
            saved=get_job_manager().wait(service,task.id,job["id"])
        assert saved.brief.connection.id==native.connection.id
        result=get_job_manager().get(service,task.id,job["id"])
        assert result["connection"]==native.connection.model_dump()
        assert result["completion"]["model"]=="actual-model"
        assert TaskService(tmp_path).tasks.read(task.id).brief.connection.revision==7
        with sqlite3.connect(service.tasks.path) as db:
            dump="\n".join(db.iterdump())
        assert native.token.get_secret_value() not in dump
        assert "127.0.0.1:12345" not in dump
    finally:
        close_job_manager()


def test_desktop_rejects_credential_configuration_and_ungranted_generation(monkeypatch):
    from fastapi.testclient import TestClient

    from compass import server
    app=server.app
    monkeypatch.setattr(server,"_engine_token","fictional-engine-session")
    monkeypatch.setenv("COMPASS_NATIVE_INFERENCE_ONLY","1")
    client=TestClient(app,headers={"Authorization":"Bearer fictional-engine-session"})
    assert client.post("/configure",json={"api_key":"fictional"}).status_code==403
    assert client.post("/credentials/inject",json={"provider":"anthropic","access_token":"fictional"}).status_code==403
    assert client.post("/tasks/jobs/start",json={"workspace_path":"/fictional","task_id":"task","expected_revision":1,"kind":"brief","request_id":"request"}).status_code==403


def test_version_four_backup_preserves_prior_tasks_before_connection_metadata(tmp_path):
    from compass.engine.task_repository import TaskRepository
    save_config(ProductConfig(name="Fictional"),tmp_path)
    repository=TaskRepository(tmp_path)
    with repository._connect() as db:
        db.execute("ALTER TABLE jobs DROP COLUMN connection")
        db.execute("PRAGMA user_version=4")
    TaskRepository(tmp_path)
    backup=next((tmp_path/".compass"/"task-backups").glob("tasks-v4-*.sql"))
    assert backup.stat().st_mode&0o777==0o600
    with sqlite3.connect(":memory:") as restored:
        restored.executescript(backup.read_text())
        assert restored.execute("PRAGMA user_version").fetchone()[0]==4
        assert "connection" not in [row[1] for row in restored.execute("PRAGMA table_info(jobs)")]
    with repository._connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0]==5
        assert "connection" in [row[1] for row in db.execute("PRAGMA table_info(jobs)")]
