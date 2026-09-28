"""AgentCore Platform v1.0"""

# Inner workflow step 1 — LiveSourceFetch.
# On-demand live fetch of regulator publications (no RAG KB is kept).
# Per-regulator failures are recorded as data (fetch_errors) by the service —
# only "all requested sources failed" is a workflow error (docs/02_design.md).

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.regulator_source_service import RegulatorSourceService


class LiveSourceFetchNode(FunctionNode):
    """Fetch current publications from the requested regulators."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, service: RegulatorSourceService | None = None):
        super().__init__()
        self._service = service or RegulatorSourceService()

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}

        regulators = state.get("regulators", [])
        already_fetched = {d["regulator"] for d in state.get("fetched_documents", [])}
        # refine rounds fetch only the not-yet-covered scope; round 1 fetches
        # everything. Previously-failed regulators are NOT in already_fetched,
        # so they are retried automatically on each round.
        to_fetch = [r for r in regulators if r not in already_fetched]

        documents, fetch_errors = self._service.fetch_publications(to_fetch)
        all_documents = list(state.get("fetched_documents", [])) + documents
        # fetch_errors must reflect CURRENT coverage gaps: drop stale entries
        # for regulators that have since succeeded or were retried this round.
        fetched_regs = {d["regulator"] for d in all_documents}
        retried_regs = {e["regulator"] for e in fetch_errors}
        all_errors = [
            e for e in state.get("fetch_errors", [])
            if e["regulator"] not in fetched_regs and e["regulator"] not in retried_regs
        ] + fetch_errors

        emit_trace_event("regulation_fetch_completed", {
            "requested": to_fetch,
            "fetched": [d["regulator"] for d in documents],
            "failed": [e["regulator"] for e in fetch_errors],
        }, state)

        if not all_documents:
            return {
                "fetched_documents": [],
                "fetch_errors": all_errors,
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "LiveSourceFetchNode: all requested regulator sources failed — "
                    + "; ".join(f"{e['regulator']}: {e['reason']}" for e in all_errors)
                ],
            }

        return {
            "fetched_documents": all_documents,
            "fetch_errors": all_errors,
            "status": AgentStatus.SUCCESS.value,
        }
