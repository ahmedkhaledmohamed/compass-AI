"""Provider protocols, total deadlines and actual socket cancellation, offline."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from unittest.mock import patch

import httpx
import pytest

from compass.engine.orchestrator import ConnectedHTTPProvider, Orchestrator, TokenUsage
from compass.engine.provider_transport import HTTPInference, ProviderError, RequestControl
from compass.engine.task_repository import TaskRepository


def payload(provider):
    if provider == "anthropic":
        return {
            "model": "served-claude",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": '{"ok":true}'}],
            "usage": {"input_tokens": 12, "output_tokens": 8},
        }
    if provider == "chatgpt":
        return {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "model": "served-chatgpt",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}
                ],
                "usage": {"input_tokens": 12, "output_tokens": 8},
            },
        }
    return {
        "model": "served-model",
        "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
    }


def response(provider, data):
    if provider == "chatgpt":
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=f"data: {json.dumps(data)}\n\n".encode(),
        )
    return httpx.Response(200, json=data)


def transport(provider="openrouter", handler=None):
    return HTTPInference(
        provider,
        "fictional-private-key",
        "selected-serving-provider" if provider in ("openrouter", "huggingface") else "",
        transport=httpx.MockTransport(handler or (lambda _: response(provider, payload(provider)))),
    )


@pytest.mark.parametrize("provider", ["anthropic", "openrouter", "huggingface", "chatgpt"])
def test_provider_wire_contract_and_actual_usage(provider):
    seen = []

    def handler(request):
        seen.append(request)
        return response(provider, payload(provider))

    result = transport(provider, handler).complete(
        "selected evidence", "application rules", "selected-model", 8000, RequestControl()
    )
    request = seen[0]
    body = json.loads(request.content)
    assert result.text == '{"ok":true}'
    assert (result.input_tokens, result.output_tokens) == (12, 8)
    assert result.model.startswith("served-")
    assert "fictional-private-key" not in str(result.metadata())
    assert body["model"] == (
        "selected-model:selected-serving-provider"
        if provider == "huggingface"
        else "selected-model"
    )
    assert request.url.scheme == "https"
    if provider == "anthropic":
        assert request.url.host == "api.anthropic.com"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.headers["x-api-key"] == "fictional-private-key"
    else:
        assert request.headers["Authorization"] == "Bearer fictional-private-key"
    if provider == "openrouter":
        assert body["provider"] == {
            "only": ["selected-serving-provider"],
            "allow_fallbacks": False,
            "data_collection": "deny",
            "require_parameters": True,
        }
    if provider == "chatgpt":
        assert request.url == "https://api.openai.com/v1/responses"
        assert body["store"] is False and body["stream"] is True
        assert body["instructions"] == "application rules"
        assert isinstance(body["input"], list)
        assert "max_output_tokens" not in body and "max_tokens" not in body


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "authentication"),
        (402, "quota"),
        (403, "permission"),
        (408, "timeout"),
        (429, "rate_limited"),
        (503, "unavailable"),
        (302, "invalid_request"),
    ],
)
def test_http_errors_are_distinct_redacted_and_never_retried(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="secret-key raw evidence body",
            headers={"Location": "https://other.invalid"},
        )

    with pytest.raises(ProviderError) as caught:
        transport(handler=handler).complete("source", "rules", "model", 100, RequestControl())
    assert caught.value.code == code
    assert "secret-key" not in str(caught.value)
    assert len(calls) == 1


def test_http_200_error_is_not_a_successful_completion():
    with pytest.raises(ProviderError) as caught:
        transport(
            handler=lambda _: httpx.Response(
                200, json={"error": {"code": 402, "message": "private details"}}
            )
        ).complete("p", "s", "m", 100, RequestControl())
    assert caught.value.code == "quota"


@pytest.mark.parametrize("reason", ["length", "content_filter", "tool_calls"])
def test_truncation_refusal_and_tool_requests_are_rejected(reason):
    data = payload("openrouter")
    data["choices"][0]["finish_reason"] = reason
    with pytest.raises(ProviderError):
        transport(handler=lambda _: httpx.Response(200, json=data)).complete(
            "p", "s", "m", 100, RequestControl()
        )


def test_missing_usage_is_unknown_and_cost_is_not_fabricated():
    data = payload("openrouter")
    data.pop("usage")
    result = transport(handler=lambda _: httpx.Response(200, json=data)).complete(
        "p", "s", "m", 100, RequestControl()
    )
    assert result.input_tokens is None and result.output_tokens is None
    usage = TokenUsage()
    usage.record(result.input_tokens, result.output_tokens)
    assert usage.to_dict()["usage_complete"] is False
    assert usage.estimated_cost_usd is None


@pytest.mark.parametrize("data", [[], {}, {"choices": []}, {"error": "bad"}])
def test_malformed_responses_are_explicit_failures(data):
    with pytest.raises(ProviderError) as caught:
        transport(handler=lambda _: httpx.Response(200, json=data)).complete(
            "p", "s", "m", 100, RequestControl()
        )
    assert caught.value.code == "invalid_response"


def test_chatgpt_delta_without_completed_event_is_never_accepted():
    stream = b'data: {"type":"response.output_text.delta","delta":"partial output"}\n\n'
    with pytest.raises(ProviderError) as caught:
        transport(
            "chatgpt",
            lambda _: httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, content=stream
            ),
        ).complete("p", "s", "m", 100, RequestControl())
    assert caught.value.code == "invalid_response"


def test_chatgpt_midstream_usage_limit_discards_partial_text():
    stream = (
        b'data: {"type":"response.output_text.delta","delta":"partial output"}\n\n'
        b'data: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}}\n\n'
    )
    with pytest.raises(ProviderError) as caught:
        transport(
            "chatgpt",
            lambda _: httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, content=stream
            ),
        ).complete("p", "s", "m", 100, RequestControl())
    assert caught.value.code == "quota"


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        for value in self.body:
            yield bytes([value])


class SlowStream(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        while True:
            await asyncio.sleep(0.02)
            yield b" "

    async def aclose(self):
        self.closed = True


def test_chatgpt_sse_handles_split_utf8_and_crlf_frames():
    data = payload("chatgpt")
    data["response"]["output"][0]["content"][0]["text"] = "Décision"
    body = f"data: {json.dumps(data, ensure_ascii=False)}\r\n\r\n".encode()
    result = transport(
        "chatgpt",
        lambda _: httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=ByteStream(body)
        ),
    ).complete("p", "s", "m", 100, RequestControl())
    assert result.text == "Décision"


def test_total_deadline_closes_even_a_continuously_trickling_response():
    stream = SlowStream()
    with pytest.raises(ProviderError) as caught:
        transport(handler=lambda _: httpx.Response(200, stream=stream)).complete(
            "p", "s", "m", 100, RequestControl(0.1)
        )
    assert caught.value.code == "timeout"
    assert stream.closed


def test_response_size_is_bounded_before_parsing():
    with (
        patch("compass.engine.provider_transport.MAX_RESPONSE_BYTES", 8),
        pytest.raises(ProviderError) as caught,
    ):
        transport().complete("p", "s", "m", 100, RequestControl())
    assert caught.value.code == "output_limit"


def test_no_routing_provider_or_model_is_selected_implicitly():
    with pytest.raises(ProviderError):
        HTTPInference("openrouter", "fixture")
    with pytest.raises(ProviderError):
        HTTPInference("huggingface", "fixture", "auto")
    with pytest.raises(ProviderError):
        transport().complete("p", "s", "openrouter/auto", 100, RequestControl())


def test_orchestrator_controlled_route_preserves_actual_completion_metadata():
    provider = ConnectedHTTPProvider("openrouter", "fixture", "chosen")
    provider.transport = transport()
    orchestrator = Orchestrator(provider, "requested-model")
    completed = []
    assert orchestrator.ask_json("p", control=RequestControl(), on_completion=completed.append) == {
        "ok": True
    }
    assert completed[0].model == "served-model"
    assert orchestrator.usage.input_tokens == 12


def test_cancellation_closes_a_real_pending_loopback_socket():
    received, closed, cancelled = Event(), Event(), Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.flush()
            received.set()
            self.connection.settimeout(2)
            if self.connection.recv(1) == b"":
                closed.set()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    client = HTTPInference("openrouter", "fictional-key", "selected")
    original = client._request_data

    def loopback(*args):
        _, headers, body = original(*args)
        return f"http://127.0.0.1:{server.server_port}/fixture", headers, body

    try:
        with (
            patch.object(client, "_request_data", side_effect=loopback),
            ThreadPoolExecutor(1) as pool,
        ):
            future = pool.submit(
                client.complete, "p", "s", "m", 100, RequestControl(2, cancelled.is_set)
            )
            assert received.wait(2)
            cancelled.set()
            with pytest.raises(ProviderError) as caught:
                future.result(timeout=2)
            assert caught.value.code == "cancelled"
            assert closed.wait(2)
    finally:
        cancelled.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_v2_generation_history_migrates_with_backup(tmp_path):
    repo = TaskRepository(tmp_path)
    with sqlite3.connect(repo.path) as db:
        db.execute("ALTER TABLE jobs DROP COLUMN completion")
        db.execute("PRAGMA user_version=2")
    TaskRepository(tmp_path)
    backups = list((repo.path.parent / "task-backups").glob("tasks-v2-*.sql"))
    assert len(backups) == 1
    with sqlite3.connect(":memory:") as restored:
        restored.executescript(backups[0].read_text())
        assert restored.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "completion" not in [row[1] for row in restored.execute("PRAGMA table_info(jobs)")]


def task_service(tmp_path):
    from compass.config import ProductConfig, save_config
    from compass.engine.knowledge_graph import KnowledgeGraph
    from compass.engine.task_service import TaskService
    from compass.models.sources import Evidence, SourceType

    save_config(ProductConfig(name="Offline transport integration"), tmp_path)
    KnowledgeGraph(tmp_path / ".compass" / "knowledge").add(
        Evidence(
            id="goal",
            source_type=SourceType.DOCS,
            source_name="selected",
            connector="docs",
            title="Goal",
            content="Goal.",
        )
    )
    service = TaskService(tmp_path)
    task = service.create("What next?", "PM", "Choose direction", ["selected"])
    return service, task


def test_task_records_actual_model_provider_and_usage_and_preserves_them_on_edit(tmp_path):
    from compass.engine.task_jobs import JobManager
    from compass.models.tasks import DraftBody

    service, task = task_service(tmp_path)
    body = {
        "title": "Decision",
        "summary": "Review this draft.",
        "claims": [
            {
                "kind": "supported",
                "text": "A goal exists.",
                "citations": [{"evidence_id": "goal", "start": 0, "end": 5, "quote": "Goal."}],
            }
        ],
        "proposed_action": "Investigate",
        "alternatives": ["Defer"],
        "unknowns": [],
    }
    data = payload("openrouter")
    data["choices"][0]["message"]["content"] = json.dumps(body)
    provider = ConnectedHTTPProvider("openrouter", "fixture", "chosen")
    provider.transport = transport(handler=lambda _: httpx.Response(200, json=data))
    manager = JobManager()
    try:
        job = manager.start(
            service,
            task.id,
            task.revision,
            "brief",
            "identity-request",
            Orchestrator(provider, "requested-model"),
        )
        task = manager.wait(service, task.id, job["id"])
        assert task.brief.model == "served-model"
        assert task.brief.provider == "openrouter:selected-serving-provider"
        assert task.brief.input_tokens == 12 and task.brief.output_tokens == 8
        assert manager.get(service, task.id, job["id"])["completion"]["model"] == "served-model"
        task = service.revise(task.id, task.revision, "brief", DraftBody.model_validate(body))
        assert task.brief.input_tokens == 12 and task.brief.output_tokens == 8
    finally:
        manager.close()


def test_job_cancellation_interrupts_real_transport_without_waiting_for_provider_reply(tmp_path):
    from compass.engine.task_jobs import JobManager

    service, task = task_service(tmp_path)
    stream = SlowStream()
    started = Event()

    def handler(_):
        started.set()
        return httpx.Response(200, stream=stream)

    provider = ConnectedHTTPProvider("openrouter", "fixture", "chosen")
    provider.transport = transport(handler=handler)
    manager = JobManager()
    try:
        job = manager.start(
            service, task.id, task.revision, "brief", "cancel-http", Orchestrator(provider, "model")
        )
        assert started.wait(2)
        manager.cancel(service, task.id, job["id"])
        import time

        deadline = time.monotonic() + 2
        while manager.get(service, task.id, job["id"])["draining"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not manager.get(service, task.id, job["id"])["draining"]
        assert stream.closed
        assert service.tasks.read(task.id).brief is None
    finally:
        manager.close()


def test_plan_quota_error_in_http_429_is_not_misclassified_as_rate_limiting():
    data = {
        "error": {
            "code": "subscription_sharing_usage_limit_exceeded",
            "message": "private diagnostics",
        }
    }
    with pytest.raises(ProviderError) as caught:
        transport("chatgpt", lambda _: httpx.Response(429, json=data)).complete(
            "p", "s", "m", 100, RequestControl()
        )
    assert caught.value.code == "quota"
    assert "private diagnostics" not in str(caught.value)


def test_truncated_output_retains_known_usage_without_accepting_text():
    data = payload("openrouter")
    data["choices"][0]["finish_reason"] = "length"
    with pytest.raises(ProviderError) as caught:
        transport(handler=lambda _: httpx.Response(200, json=data)).complete(
            "p", "s", "m", 100, RequestControl()
        )
    assert caught.value.code == "output_limit"
    assert caught.value.completion["input_tokens"] == 12
    assert "text" not in caught.value.completion


def test_provider_environment_never_falls_back_to_another_accounts_key(monkeypatch):
    from compass.engine.orchestrator import _create_default_provider

    monkeypatch.setenv("COMPASS_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fictional-other-account-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError) as caught:
        _create_default_provider()
    assert caught.value.code == "authentication"
