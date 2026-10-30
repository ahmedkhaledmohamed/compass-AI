"""Explicit authenticated clients for application tests; auth rejection tests stay separate."""

import pytest

from compass import server


@pytest.fixture
def engine_headers(monkeypatch):
    monkeypatch.setattr(server, "_engine_token", "fictional-engine-session")
    return {"Authorization": "Bearer fictional-engine-session"}
