"""AgentCore Platform v1.0"""

# Inner workflow step 5 — ComplianceImpactClassify.
# LLM classification of each record's compliance impact for the requested
# institution type (impact area tags + required action type).

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.services.llm.base_llm import BaseLLM
from shared.utils.audit_logger import emit_trace_event

from src.services.llm_client import parse_json_content

_PROMPT = """You are a compliance officer for a Japanese financial institution
(institution type: {institution_type}). Classify the compliance impact of each
regulatory record below.

Return ONLY a JSON array, same order and length as the input. Each element:
{{"impact_areas": ["credit-risk"|"market-risk"|"operational-risk"|"ai-mrm"|"data-privacy"|"capital"|"conduct"|"other", ...],
  "action_type": "immediate-response"|"review-required"|"monitor"|"no-action",
  "impact_note": "1 sentence in Japanese"}}

Records:
{records}
"""


class ComplianceImpactClassifyNode(FunctionNode):
    """Tag each scored record with institution-specific compliance impact."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm: BaseLLM | None = None):
        super().__init__()
        self._llm = llm

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}
        records = state.get("scored_records", [])
        if not records:
            emit_trace_event("impact_classified", {"record_count": 0}, state)
            return {"classified_records": [], "status": AgentStatus.SUCCESS.value}
        if self._llm is None:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ComplianceImpactClassifyNode: no LLM configured (inject via graph config 'llm')"],
            }

        prompt = _PROMPT.format(
            institution_type=state.get("institution_type", "unspecified"),
            records=json.dumps(records, ensure_ascii=False, default=str),
        )
        response = self._llm.complete([{"role": "user", "content": prompt}])
        try:
            classifications = parse_json_content(response.get("content", ""))
        except ValueError as e:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"ComplianceImpactClassifyNode: LLM output unparseable — {e}"],
            }
        if not isinstance(classifications, list) or len(classifications) != len(records):
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    f"ComplianceImpactClassifyNode: classification count mismatch "
                    f"(records={len(records)}, classifications={len(classifications) if isinstance(classifications, list) else 'non-list'})"
                ],
            }

        classified = [
            {**rec, **(cls if isinstance(cls, dict) else {})}
            for rec, cls in zip(records, classifications)
        ]
        emit_trace_event("impact_classified", {"record_count": len(classified)}, state)
        return {
            "classified_records": classified,
            "status": AgentStatus.SUCCESS.value,
        }
