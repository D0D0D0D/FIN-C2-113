"""AgentCore Platform v1.0"""

# Inner workflow step 2 — ResultNormalize.
# Uses the LLM to extract publication records from the raw fetched pages into a
# common schema: issuer / title / published / deadline / topic / url / summary.

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.services.llm.base_llm import BaseLLM
from shared.utils.audit_logger import emit_trace_event

from src.services.llm_client import parse_json_content

_PROMPT = """You are a financial-regulation analyst. From the raw regulator page below,
extract the individual regulatory publications relevant to these research domains: {domains}.

Return ONLY a JSON array. Each element:
{{"issuer": "<regulator code>", "title": "...", "published": "YYYY-MM-DD or null",
  "deadline": "YYYY-MM-DD or null", "topic": "...", "url": "...", "summary": "1-2 sentences"}}

Include only entries actually present in the page content. If nothing is relevant, return [].

Regulator: {regulator} ({source_name})
Page URL: {url}
Page content:
{content}
"""


class ResultNormalizeNode(FunctionNode):
    """Normalize heterogeneous regulator publications into common-schema records."""

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
                "error_log": ["ResultNormalizeNode: no LLM configured (inject via graph config 'llm')"],
            }

        domains = ", ".join(state.get("domains", [])) or "all regulatory topics"
        records: list[dict] = []
        for doc in state.get("fetched_documents", []):
            prompt = _PROMPT.format(
                domains=domains,
                regulator=doc["regulator"],
                source_name=doc.get("source_name", doc["regulator"]),
                url=doc["url"],
                content=doc.get("content", "")[:30_000],
            )
            response = self._llm.complete([{"role": "user", "content": prompt}])
            try:
                extracted = parse_json_content(response.get("content", ""))
            except ValueError as e:
                return {
                    "status": AgentStatus.ERROR.value,
                    "error_log": [f"ResultNormalizeNode: LLM output unparseable for {doc['regulator']} — {e}"],
                }
            for rec in extracted if isinstance(extracted, list) else []:
                if isinstance(rec, dict):
                    rec.setdefault("issuer", doc["regulator"])
                    rec.setdefault("url", doc["url"])
                    records.append(rec)

        emit_trace_event("records_normalized", {"record_count": len(records)}, state)
        return {
            "normalized_records": records,
            "status": AgentStatus.SUCCESS.value,
        }
