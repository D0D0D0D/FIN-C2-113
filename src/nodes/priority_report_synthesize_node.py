"""AgentCore Platform v1.0"""

# Inner workflow step 6 — PriorityReportSynthesize.
# LLM synthesis of the prioritized compliance impact report (P1/P2/P3 +
# per-institution impact + recommended next actions + citations).
# On refine rounds, the previous draft is integrated with the new findings.

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.services.llm.base_llm import BaseLLM
from shared.utils.audit_logger import emit_trace_event

from src.services.llm_client import parse_json_content

_PROMPT = """You are drafting a compliance impact report for a Japanese financial
institution (institution type: {institution_type}; output format: {output_format}).
Write the report body in Japanese.

Return ONLY a JSON object:
{{"p1": [{{"title", "issuer", "deadline", "impact_note", "next_action", "citation_url"}}, ...],
  "p2": [... same shape ...],
  "p3": [... same shape ...],
  "institution_impact_summary": "...",
  "recommended_next_steps": ["...", ...],
  "coverage_note": "which regulators were covered / missing"}}

Rules:
- p1 = immediate action with a deadline (deadline_urgency "p1" or action_type "immediate-response")
- p2 = review within 30 days; p3 = FYI
- EVERY p1/p2/p3 entry MUST carry citation_url taken from the record's url — no uncited claims.
- Do not state definitive legal conclusions; describe impact and recommended action only.
- Mention missing regulators from fetch_errors in coverage_note (do not hide gaps).

Classified records:
{records}

Fetch errors (missing coverage):
{fetch_errors}

{refine_block}
"""

_REFINE_BLOCK = """This is refine round {round}. Integrate the previous draft with the new
records above into ONE consolidated report (do not lose prior findings):
Previous draft:
{previous_draft}

Reviewer instruction: {instructions}
"""


class PriorityReportSynthesizeNode(FunctionNode):
    """Synthesize the prioritized, citation-backed compliance report draft."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm: BaseLLM | None = None):
        super().__init__()
        self._llm = llm

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}
        if self._llm is None:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PriorityReportSynthesizeNode: no LLM configured (inject via graph config 'llm')"],
            }

        refine_rounds = state.get("refine_rounds", 0)
        refine_block = ""
        if refine_rounds > 0 and state.get("compliance_report"):
            feedback = state.get("hitl_feedback") or {}
            instructions = feedback.get("instructions", "") if isinstance(feedback, dict) else str(feedback)
            refine_block = _REFINE_BLOCK.format(
                round=refine_rounds,
                previous_draft=json.dumps(state["compliance_report"], ensure_ascii=False, default=str),
                instructions=instructions,
            )

        prompt = _PROMPT.format(
            institution_type=state.get("institution_type", "unspecified"),
            output_format=state.get("output_format", "brief"),
            records=json.dumps(state.get("classified_records", []), ensure_ascii=False, default=str),
            fetch_errors=json.dumps(state.get("fetch_errors", []), ensure_ascii=False),
            refine_block=refine_block,
        )
        response = self._llm.complete([{"role": "user", "content": prompt}])
        try:
            report = parse_json_content(response.get("content", ""))
        except ValueError as e:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PriorityReportSynthesizeNode: LLM output unparseable — {e}"],
            }
        if not isinstance(report, dict):
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PriorityReportSynthesizeNode: LLM returned non-object report"],
            }

        citations = [
            {
                "regulator": rec.get("issuer", ""),
                "title": rec.get("title", ""),
                "url": rec.get("url", ""),
                "fetched_at": next(
                    (d.get("fetched_at", "") for d in state.get("fetched_documents", [])
                     if d.get("regulator") == rec.get("issuer")),
                    "",
                ),
            }
            for rec in state.get("classified_records", [])
        ]

        emit_trace_event("report_synthesized", {
            "refine_round": refine_rounds,
            "p1": len(report.get("p1", [])),
            "p2": len(report.get("p2", [])),
            "p3": len(report.get("p3", [])),
        }, state)

        return {
            "compliance_report": report,
            "citations": citations,
            "status": AgentStatus.SUCCESS.value,
        }
