"""API-adapter integration tests — entry auth + HITL resume ownership.

Exercises src/api/server.py over HTTP (FastAPI TestClient). These are the layer
that guards the retrofit invariants:
  - entry auth: unauthenticated caller → 401 (not the old fail-open path where
    the graph's S-1 gate rejected an ANONYMOUS caller after the fact);
  - resume ownership: a suspended checkpoint may only be resumed by the caller
    whose session_id it recorded (fail-closed), across /resume.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

_TOKEN = "test-invoke-token-fin113"

_INPUT = json.dumps({
    "regulators": ["FSA"],
    "domains": ["capital"],
    "institution_type": "mega-bank",
    "output_format": "brief",
})


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", _TOKEN)
    # Inject FakeLLM before the server module builds a real Anthropic client.
    import src.services.llm_client as llc
    from tests.conftest import FakeLLM, RecordingFetcher

    monkeypatch.setattr(llc, "build_llm", lambda config, secrets: FakeLLM())

    import importlib

    import src.api.server as server

    importlib.reload(server)
    sub = server.agent._nodes["main"].get_subgraph()
    sub._nodes["live_source_fetch"]._service._fetcher = RecordingFetcher()
    return TestClient(server.app), server


def _auth(token: str = _TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _payload(session_id: str = "sess-owner") -> dict:
    return {"input": _INPUT, "session_id": session_id}


def test_invoke_without_token_is_401(client):
    c, _ = client
    assert c.post("/invoke", json=_payload()).status_code == 401


def test_invoke_wrong_token_is_401(client):
    c, _ = client
    assert c.post("/invoke", json=_payload(), headers=_auth("nope")).status_code == 401


def test_invoke_good_token_suspends_at_hitl(client):
    c, _ = client
    r = c.post("/invoke", json=_payload(), headers=_auth())
    assert r.status_code == 200
    assert r.json()["status"] == "awaiting_human"
    assert r.json()["thread_id"]


def test_resume_without_token_is_401(client):
    c, _ = client
    th = c.post("/invoke", json=_payload(), headers=_auth()).json()["thread_id"]
    r = c.post("/resume", json={"thread_id": th, "feedback": {"action": "approve"}, "session_id": "sess-owner"})
    assert r.status_code == 401


def test_resume_foreign_session_is_403(client):
    c, _ = client
    th = c.post("/invoke", json=_payload(), headers=_auth()).json()["thread_id"]
    r = c.post("/resume", json={"thread_id": th, "feedback": {"action": "approve"}, "session_id": "attacker"},
               headers=_auth())
    assert r.status_code == 403


def test_resume_unknown_thread_is_404(client):
    c, _ = client
    c.post("/invoke", json=_payload(), headers=_auth())
    r = c.post("/resume", json={"thread_id": "00000000-0000-0000-0000-000000000000",
                                "feedback": {"action": "approve"}, "session_id": "sess-owner"}, headers=_auth())
    assert r.status_code == 404


def test_invoke_explicit_anonymous_middleware_is_401(monkeypatch):
    """A middleware that explicitly stamps ANONYMOUS must still be treated as
    unauthenticated (401) — test the trust VALUE, not attribute presence."""
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", _TOKEN)
    import src.services.llm_client as llc
    from tests.conftest import FakeLLM

    monkeypatch.setattr(llc, "build_llm", lambda config, secrets: FakeLLM())

    import importlib

    from framework.schemas.trust_level import TrustLevel

    import src.api.server as server

    importlib.reload(server)

    @server.app.middleware("http")
    async def _stamp_anonymous(request, call_next):
        request.state.trust_level = TrustLevel.ANONYMOUS
        return await call_next(request)

    c = TestClient(server.app)
    assert c.post("/invoke", json=_payload()).status_code == 401


def test_resume_correct_owner_reaches_success(client):
    c, _ = client
    th = c.post("/invoke", json=_payload(), headers=_auth()).json()["thread_id"]
    r = c.post("/resume", json={"thread_id": th, "feedback": {"action": "approve"}, "session_id": "sess-owner"},
               headers=_auth())
    assert r.status_code == 200
    assert r.json()["status"] == "success"


def test_resume_replay_after_completion_is_409(client):
    """A completed thread must not be resumable again — the pending
    gate (empty snapshot.next) rejects a replayed resume."""
    c, _ = client
    th = c.post("/invoke", json=_payload(), headers=_auth()).json()["thread_id"]
    first = c.post("/resume", json={"thread_id": th, "feedback": {"action": "approve"}, "session_id": "sess-owner"},
                   headers=_auth())
    assert first.status_code == 200 and first.json()["status"] == "success"
    replay = c.post("/resume", json={"thread_id": th, "feedback": {"action": "approve"}, "session_id": "sess-owner"},
                    headers=_auth())
    assert replay.status_code == 409
