"""Desktop connection changes must not restore an environment-funded account."""

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from compass import server
from compass.engine import orchestrator
from compass.engine.provider_transport import ProviderError


def test_explicit_disconnect_never_falls_back_to_environment_key(monkeypatch):
    monkeypatch.setattr(orchestrator, "_instance", None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fictional-environment-key")
    orchestrator.disconnect_orchestrator()
    with pytest.raises(ProviderError) as caught:
        orchestrator.get_orchestrator().ask("No request should leave the machine")
    assert caught.value.code == "authentication"


def test_busy_connection_cannot_be_cleared(monkeypatch, engine_headers):
    from unittest.mock import Mock

    from fastapi.testclient import TestClient

    monkeypatch.setattr(server, "get_job_manager", lambda: Mock(busy=lambda: True))
    with TestClient(server.app, headers=engine_headers) as client:
        assert client.post("/connections/clear").status_code == 409


def test_cli_boot_registers_authenticated_connection_endpoints():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = dict(os.environ, COMPASS_ENGINE_TOKEN="fictional-native-session")
    for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "HF_TOKEN", "COMPASS_CHATGPT_ACCESS_TOKEN"):
        environment.pop(name, None)
    process = subprocess.Popen([sys.executable, "-m", "compass.server", str(port)], env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=0.3) as client:
            deadline = time.monotonic()+8
            while True:
                assert process.poll() is None, "Engine exited during startup"
                try:
                    result = client.get("/connections/readiness", headers={"Authorization":"Bearer fictional-native-session"})
                    break
                except httpx.TransportError:
                    if time.monotonic()>deadline:
                        raise
                    time.sleep(0.05)
            assert result.status_code == 200
            assert result.json()["busy"] is False
            assert result.json()["configured"] is False
            assert client.post("/connections/clear").status_code == 401
            assert client.post("/connections/clear", headers={"Authorization":"Bearer fictional-native-session"}).status_code == 200
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
