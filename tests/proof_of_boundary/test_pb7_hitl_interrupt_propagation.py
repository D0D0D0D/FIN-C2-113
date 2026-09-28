# tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py
#
# CONDITIONAL: Required only when config/config.yaml has hitl.enabled: true
# Templates that do not use HITL will see this file produce SKIPPED results.
#
# Replace MyMainNode (and the state dict) with the node that calls interrupt().
#
# Two test cases verify the PB-7 boundary behavior:
#
#   test_pb7_hitl_interrupt_propagates()
#       Verifies that interrupt() raises GraphInterrupt and the signal propagates
#       through BaseNode.__call__() to the LangGraph engine — NOT caught by the
#       application error boundary.
#
#   test_pb7_hitl_allowed_false_skips_interrupt()
#       Verifies that the hitl_allowed=False guard prevents interrupt() from
#       firing — no GraphInterrupt raised, no deadlock.
#

from __future__ import annotations

from contextlib import contextmanager
from itertools import count
import pathlib
import warnings

import pytest
from framework.schemas.agent_status import AgentStatus
from langchain_core.runnables.config import var_child_runnable_config
from langgraph._internal._constants import (
    CONFIG_KEY_CHECKPOINT_NS,
    CONFIG_KEY_SCRATCHPAD,
    CONFIG_KEY_SEND,
)
from langgraph._internal._scratchpad import PregelScratchpad
from langgraph.errors import GraphInterrupt

from src.nodes.report_draft_review_node import ReportDraftReviewNode

# ---------------------------------------------------------------------------
# Conditional skip — only runs when config/config.yaml has hitl.enabled: true
# ---------------------------------------------------------------------------

_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "config" / "config.yaml"


def _hitl_enabled() -> bool:
    """Return True when config/config.yaml declares hitl.enabled: true.

    An absent or unreadable config.yaml warns instead of skipping silently —
    either case is ambiguous (never shipped / broken
    vs. genuinely non-HITL) and should not look like a clean auto-waiver.
    """
    if not _CONFIG_PATH.exists():
        warnings.warn(
            f"{_CONFIG_PATH} not found — PB-7 skipped without verifying hitl.enabled. "
            "If this template calls interrupt(), ship config/config.yaml before release.",
            stacklevel=2,
        )
        return False
    try:
        import yaml  # pyyaml==6.0.1 — pinned in scaffold/pyproject.toml dependencies

        data = yaml.safe_load(_CONFIG_PATH.read_text())
    except Exception as exc:
        warnings.warn(
            f"{_CONFIG_PATH} could not be read as YAML ({exc}) — PB-7 skipped without "
            "verifying hitl.enabled. If this template calls interrupt(), fix config/config.yaml "
            "before release.",
            stacklevel=2,
        )
        return False
    hitl = (data or {}).get("hitl", {}) if isinstance(data, dict) else None
    if not isinstance(hitl, dict):
        warnings.warn(
            f"{_CONFIG_PATH} does not have the expected 'hitl:' mapping shape — PB-7 skipped "
            "without verifying hitl.enabled. If this template calls interrupt(), fix "
            "config/config.yaml before release.",
            stacklevel=2,
        )
        return False
    return bool(hitl.get("enabled", False))


pytestmark = pytest.mark.skipif(
    not _hitl_enabled(),
    reason="config/config.yaml does not set hitl.enabled: true — PB-7 not applicable",
)

# ---------------------------------------------------------------------------
# Helper — build a minimal base state for the node under test
# ---------------------------------------------------------------------------

def _base_state(**overrides) -> dict:
    """Return a minimal state dict for PB-7 tests."""
    state = {
        # Framework-managed fields
        "caller_trust_level": "VERIFIED_EXTERNAL",
        "correlation_id": "pb7-test",
        "node_history": [],
        "error_log": [],
        "status": AgentStatus.SUCCESS.value,
        # HITL fields
        "hitl_allowed": True,           # overridden per test case
        "hitl_count": 0,
        # ReportDraftReviewNode fields read before interrupt()
        "compliance_report": {"p1": [{"title": "PB-7 draft"}]},
        "fetch_errors": [],
        "refine_rounds": 0,
    }
    state.update(overrides)
    return state


@contextmanager
def _interrupt_runtime():
    """Provide the LangGraph task context that interrupt() requires."""
    call_counter = count().__next__
    interrupt_counter = count().__next__
    subgraph_counter = count().__next__
    config = {
        "configurable": {
            CONFIG_KEY_CHECKPOINT_NS: "pb7-test",
            CONFIG_KEY_SCRATCHPAD: PregelScratchpad(
                step=0,
                stop=1,
                call_counter=call_counter,
                interrupt_counter=interrupt_counter,
                get_null_resume=lambda _consume: None,
                resume=[],
                subgraph_counter=subgraph_counter,
            ),
            CONFIG_KEY_SEND: lambda _writes: None,
        }
    }
    token = var_child_runnable_config.set(config)
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


# ---------------------------------------------------------------------------
# PB-7-A: interrupt() raises GraphInterrupt and propagates
# ---------------------------------------------------------------------------


def test_pb7_hitl_interrupt_propagates() -> None:
    """PB-7: interrupt() raises GraphInterrupt and propagates (not caught by app boundary).

    Covers the first PB-7 assertion:
      GraphInterrupt reaches LangGraph engine; status is NOT set to error.

    The state contains a reviewable draft and passes the VERIFIED_EXTERNAL S-1
    gate, so execution reaches the real interrupt() site.
    """
    node = ReportDraftReviewNode()
    state = _base_state(hitl_allowed=True)

    with _interrupt_runtime():
        with pytest.raises(GraphInterrupt):
            node(state)

    assert state["status"] != AgentStatus.ERROR.value
    assert state["error_log"] == []


# ---------------------------------------------------------------------------
# PB-7-B: hitl_allowed=False guard prevents deadlock
# ---------------------------------------------------------------------------


def test_pb7_hitl_allowed_false_skips_interrupt() -> None:
    """PB-7 guard: hitl_allowed=False must NOT raise GraphInterrupt (no deadlock).

    Covers the PB-7 hitl_allowed guard:
      When hitl_allowed=False, the node must check the flag before calling
      interrupt() and skip the HITL path entirely.

    The same reviewable draft is supplied, but hitl_allowed=False must take the
    auto-approval branch before interrupt().
    """
    node = ReportDraftReviewNode()
    state = _base_state(hitl_allowed=False)

    result = node(state)

    assert result["hitl_draft"] == state["compliance_report"]
    assert result["review_decision"] == "approved"
    assert result["status"] == AgentStatus.SUCCESS.value
