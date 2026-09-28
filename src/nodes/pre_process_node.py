"""AgentCore Platform v1.0"""

# pre_process slot — InputParse (docs/02_design.md §Node Configuration).
# Interprets the research request into request_params and validates it (S-2).

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_MAX_INPUT_CHARS = 20_000

# fallback only — the effective allowlist comes from the configured source
# registry (config.sources merged over defaults) so users can add regulators
# without code changes (docs/0A_release_note.md).
_DEFAULT_REGULATORS = ("FSA", "BOJ", "Basel", "SEC", "APRA")
_VALID_INSTITUTION_TYPES = ("mega-bank", "regional-bank", "securities", "insurance", "unspecified")
_VALID_OUTPUT_FORMATS = ("brief", "detailed")

# natural-language fallback: keyword → regulator
_REGULATOR_KEYWORDS = {
    "FSA": ("fsa", "金融庁"),
    "BOJ": ("boj", "日銀", "日本銀行"),
    "Basel": ("basel", "バーゼル", "bis"),
    "SEC": ("sec", "米国証券"),
    "APRA": ("apra",),
}
_INSTITUTION_KEYWORDS = {
    "mega-bank": ("メガバンク", "mega"),
    "regional-bank": ("地銀", "地方銀行", "regional"),
    "securities": ("証券", "securities"),
    "insurance": ("保険", "insurance"),
}


class InputParseNode(FunctionNode):
    """Parse the compliance-research request into structured request_params."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, known_regulators: list | None = None):
        super().__init__()
        # effective allowlist = configured source registry (user-extensible)
        self._known_regulators = list(known_regulators or _DEFAULT_REGULATORS)

    def execute(self, state: dict) -> dict:
        user_input = state.get("validated_input") or state.get("user_input", "")
        if not user_input or not user_input.strip():
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputParseNode: user_input is empty or missing"],
            }

        params = self._parse(user_input.strip())
        invalid = [r for r in params["regulators"] if r not in self._known_regulators]
        if invalid:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"InputParseNode: unknown regulator(s): {invalid} — valid: {self._known_regulators}"],
            }

        emit_trace_event("request_parsed", {
            "regulators": params["regulators"],
            "domains": params["domains"],
            "institution_type": params["institution_type"],
            "output_format": params["output_format"],
        }, state)

        return {
            "validated_input": user_input.strip(),
            "request_params": params,
            "status": AgentStatus.SUCCESS.value,
        }

    def _parse(self, text: str) -> dict:
        """Structured JSON request preferred; keyword fallback for natural language."""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict):
            regulators = payload.get("regulators") or list(self._known_regulators)
            domains = payload.get("domains") or []
            institution_type = payload.get("institution_type") or "unspecified"
            output_format = payload.get("output_format") or "brief"
        else:
            lowered = text.lower()
            regulators = [r for r, kws in _REGULATOR_KEYWORDS.items()
                          if r in self._known_regulators and any(k in lowered for k in kws)]
            if not regulators:
                regulators = list(self._known_regulators)
            institution_type = next(
                (t for t, kws in _INSTITUTION_KEYWORDS.items() if any(k in lowered for k in kws)),
                "unspecified",
            )
            domains = [text[:200]]  # free-text research topic becomes the domain query
            output_format = "detailed" if ("detailed" in lowered or "詳細" in text) else "brief"

        if institution_type not in _VALID_INSTITUTION_TYPES:
            institution_type = "unspecified"
        if output_format not in _VALID_OUTPUT_FORMATS:
            output_format = "brief"
        return {
            "regulators": [str(r) for r in regulators],
            "domains": [str(d) for d in domains],
            "institution_type": str(institution_type),
            "output_format": str(output_format),
        }

    def _extra_security_gate_input(self, state: dict) -> dict:
        """S-2 domain check: input size ceiling (docs/02_design.md §Shared Components)."""
        raw = state.get("user_input", "")
        if isinstance(raw, str) and len(raw) > _MAX_INPUT_CHARS:
            state = dict(state)
            state["status"] = AgentStatus.ERROR.value
            state["error_log"] = list(state.get("error_log", [])) + [
                f"InputParseNode S-2: input exceeds {_MAX_INPUT_CHARS} chars"
            ]
        return state
