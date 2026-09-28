"""AgentCore Platform v1.0"""

# Inner workflow step 3 — InstitutionFilter.
# Narrows normalized records down to those relevant to the requested
# institution type. Deterministic rule-based filter (no LLM).

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# Institution-type relevance: which regulators' output primarily binds which
# institution type in this template's scope. "unspecified" keeps everything.
_RELEVANT_ISSUERS: dict[str, set] = {
    "mega-bank": {"FSA", "BOJ", "Basel", "SEC", "APRA"},   # global footprint — all
    "regional-bank": {"FSA", "BOJ", "Basel"},
    "securities": {"FSA", "SEC"},
    "insurance": {"FSA", "APRA"},
}


class InstitutionFilterNode(FunctionNode):
    """Filter normalized records to the requested institution type."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}

        institution_type = state.get("institution_type", "unspecified")
        records = state.get("normalized_records", [])
        relevant = _RELEVANT_ISSUERS.get(institution_type)
        filtered = records if relevant is None else [r for r in records if r.get("issuer") in relevant]

        emit_trace_event("records_filtered", {
            "institution_type": institution_type,
            "before": len(records),
            "after": len(filtered),
        }, state)

        return {
            "filtered_records": filtered,
            "status": AgentStatus.SUCCESS.value,
        }
