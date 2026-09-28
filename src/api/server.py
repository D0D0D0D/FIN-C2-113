"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for FIN-C2-113.
# Entry points are adapters only — no business logic here.
# HITL: /invoke may return status "awaiting_human" with a thread_id;
# the reviewer answers via /resume with that thread_id and feedback
# ({"action": "approve"} | {"action": "reject"} |
#  {"action": "correct", "additional_regulators": [...], "additional_domains": [...], "instructions": "..."}).

from __future__ import annotations

from pathlib import Path
from typing import Any
import threading
import weakref
from uuid import uuid4

import yaml
from fastapi import FastAPI, Request
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from fastapi import HTTPException

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets import factory as secrets_factory
from src.entry_adapter.auth import _require_verified_trust, _resolve_trust
from src.graph.graph import Graph

_CONFIG_YAML = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def _load_config() -> dict:
    config = yaml.safe_load(_CONFIG_YAML.read_text(encoding="utf-8"))
    return dict(config or {})


def _build_agent() -> Graph:
    config = _load_config()
    secrets = secrets_factory(namespace="fin", agent_name="FinRegulationResearchComplianceReportAgent")
    if "llm" not in config:
        from src.services.llm_client import build_llm
        config["llm"] = build_llm(config, secrets)
    agent = Graph(config=config)
    # The standalone entry point uses an explicit in-memory checkpointer for
    # the HITL interrupt to suspend and resume.
    agent.compile(checkpointer=InMemorySaver())
    agent.provision_secrets(secrets)
    return agent


app = FastAPI(title="FIN-C2-113 FinRegulationResearchComplianceReportAgent")
agent = _build_agent()


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


class ResumeRequest(BaseModel):
    thread_id: str
    feedback: Any
    session_id: str = ""


def _authenticated_ctx(request: Request, session_id: str) -> InvocationContext:
    """Build the InvocationContext AFTER entry authentication.

    Entry authentication is handled by entry_adapter.auth:
    verified middleware trust is used as-is; otherwise INVOKE_AUTH_TOKEN Bearer
    auth promotes ANONYMOUS → VERIFIED_EXTERNAL (401 on mismatch). An
    unauthenticated caller (still ANONYMOUS, no token promotion) → 401. The old
    fallback that defaulted such callers to ANONYMOUS and let them reach the
    graph (where S-1 then rejected them) is replaced by an explicit 401.

    Scope note: this template has NO tenant/org scope model (research is by
    regulator/domain, no per-tenant KB), so only the auth core is wired — not
    the entry-adapter scope layer, which nothing here would consume.
    """
    trust, token_authenticated = _resolve_trust(request)
    if trust is TrustLevel.ANONYMOUS and not token_authenticated:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    _require_verified_trust(trust)
    return InvocationContext(
        session_id=session_id or str(uuid4()),
        caller_trust_level=trust,
        caller_id=getattr(request.state, "caller_id", ""),
    )


def _require_resume_ownership(thread_id: str, ctx: InvocationContext) -> None:
    """Fail-closed checkpoint ownership check for HITL resume.

    Entry auth alone is insufficient: any authenticated caller could replay
    another session's thread_id. Verify the suspended checkpoint exists and its
    recorded session_id matches the caller's before delivering feedback.
    session_id is the primary owner key (token-authenticated callers share
    caller_id=""); a checkpoint with no recorded session_id is un-resumable.
    """
    compiled = getattr(agent, "_compiled", None)
    if compiled is None:
        raise HTTPException(status_code=409, detail="No suspended session for this thread.")
    snapshot = compiled.get_state({"configurable": {"thread_id": thread_id}})
    values = getattr(snapshot, "values", None) or {}
    if not values:
        raise HTTPException(status_code=404, detail="No suspended session for this thread.")
    owner_session = values.get("session_id")
    if not owner_session or owner_session != ctx.session_id:
        raise HTTPException(status_code=403, detail="Session does not own this thread.")
    owner_caller = values.get("caller_id") or ""
    if owner_caller and owner_caller != (ctx.caller_id or ""):
        raise HTTPException(status_code=403, detail="Caller does not own this thread.")
    # Pending-HITL gate (replay guard): a completed thread has an empty
    # snapshot.next, so a resume replayed after completion is rejected 409.
    # propagate_hitl=True → the outer pending checkpoint carries subgraph_thread_id.
    if not getattr(snapshot, "next", None) or not values.get("subgraph_thread_id"):
        raise HTTPException(status_code=409, detail="Thread is not awaiting human input.")


# Per-thread locks serialize the ownership-check + resume for one thread within
# this process (resume TOCTOU guard). Without it two concurrent valid
# resumes can both pass the pending-state check and both replay feedback. A
# durable, multi-process deployment must use a per-thread lease/CAS instead.
_RESUME_LOCKS: "weakref.WeakValueDictionary[str, threading.Lock]" = weakref.WeakValueDictionary()
_RESUME_LOCKS_GUARD = threading.Lock()


def _resume_lock(thread_id: str) -> threading.Lock:
    with _RESUME_LOCKS_GUARD:
        lock = _RESUME_LOCKS.get(thread_id)
        if lock is None:
            lock = threading.Lock()
            _RESUME_LOCKS[thread_id] = lock
        return lock


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request):
    ctx = _authenticated_ctx(request, req.session_id)
    with bound_secrets(agent._secrets_provider):
        return agent.invoke(req.input, ctx=ctx)


@app.post("/resume")
async def resume(req: ResumeRequest, request: Request):
    ctx = _authenticated_ctx(request, req.session_id)
    # Serialize check + resume per thread (resume TOCTOU guard).
    with _resume_lock(req.thread_id):
        _require_resume_ownership(req.thread_id, ctx)
        with bound_secrets(agent._secrets_provider):
            return agent.resume(thread_id=req.thread_id, feedback=req.feedback)


@app.get("/health")
def health():
    return {"status": "ok", "agent": "FinRegulationResearchComplianceReportAgent"}
