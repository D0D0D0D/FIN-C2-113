"""AgentCore Platform v1.0"""

# Inner workflow step 7 — ReportDraftReview (HITL, D6 pattern).
# Presents the draft report via interrupt(); feedback drives the refine loop:
#   approve         → confirm the report → END
#   reject          → terminate without a deliverable
#   correct/refine  → merge additional scope → back to LiveSourceFetch
#
# The refine loop is intentionally UNBOUNDED: every round requires an explicit
# human "correct" feedback (human-gated), so there is no runaway risk — a cap
# that silently finalizes against the reviewer's intent would be worse
# (2026-07-02 review decision). refine_rounds is kept as a counter for
# incremental-fetch behaviour and audit only. hitl_count is backbone-owned
# and never written by this template (docs/02_design.md §HITL).

from __future__ import annotations

from typing import ClassVar

from langgraph.types import interrupt

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.hitl_status import HitlStatus, extract_action
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_DEFAULT_REGULATORS = ("FSA", "BOJ", "Basel", "SEC", "APRA")


class ReportDraftReviewNode(FunctionNode):
    """Human review of the draft report; drives the additional-research loop."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, known_regulators: list | None = None):
        super().__init__()
        # effective allowlist = configured source registry (user-extensible)
        self._known_regulators = list(known_regulators or _DEFAULT_REGULATORS)

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}

        draft = state.get("compliance_report", {})

        # MANDATORY hitl_allowed guard: when this
        # graph runs inside an automated pipeline (parent GraphNode sets
        # hitl_allowed=False), an unconditional interrupt() would deadlock the
        # composition. Skip human review and auto-approve the draft — the
        # refine loop is human-gated, so with no human present the draft passes
        # through unchanged (equivalent to an "approve" with no edits).
        if not state.get("hitl_allowed", True):
            emit_trace_event("draft_review_skipped_hitl_not_allowed",
                             {"reason": "hitl_allowed=false"}, state)
            return {
                "hitl_draft": draft,
                "review_decision": "approved",
                "status": AgentStatus.SUCCESS.value,
            }

        feedback = interrupt({
            "draft": draft,
            "fetch_errors": state.get("fetch_errors", []),
            "refine_round": state.get("refine_rounds", 0),
            "reason": "compliance report draft review",
        })

        hitl_status = HitlStatus.from_feedback(feedback)
        action = extract_action(feedback)
        emit_trace_event("draft_review_feedback", {
            "action": action or "approve",
            "refine_round": state.get("refine_rounds", 0),
        }, state)

        base = {
            "hitl_draft": draft,
            "hitl_feedback": feedback,
            "hitl_status": hitl_status.value,
        }

        if hitl_status == HitlStatus.REJECTED:
            return {
                **base,
                "review_decision": "rejected",
                "status": AgentStatus.SUCCESS.value,
            }

        if hitl_status == HitlStatus.CORRECTED:
            merged = self._merge_scope(state, feedback)
            return {
                **base,
                **merged,
                "refine_rounds": state.get("refine_rounds", 0) + 1,
                "review_decision": "refine",
                "status": AgentStatus.SUCCESS.value,
            }

        # approve (default)
        return {
            **base,
            "review_decision": "approved",
            "status": AgentStatus.SUCCESS.value,
        }

    def _merge_scope(self, state: dict, feedback) -> dict:
        """Merge additional research scope from the reviewer feedback."""
        if not isinstance(feedback, dict):
            return {}
        merged: dict = {}
        extra_regulators = [
            r for r in feedback.get("additional_regulators", [])
            if r in self._known_regulators and r not in state.get("regulators", [])
        ]
        if extra_regulators:
            merged["regulators"] = list(state.get("regulators", [])) + extra_regulators
        extra_domains = [
            str(d) for d in feedback.get("additional_domains", [])
            if str(d) not in state.get("domains", [])
        ]
        if extra_domains:
            merged["domains"] = list(state.get("domains", [])) + extra_domains
        return merged
