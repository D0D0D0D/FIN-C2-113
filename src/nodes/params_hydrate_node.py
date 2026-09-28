"""AgentCore Platform v1.0"""

# Inner workflow step 0 — ParamsHydrate.
# Restores the JSON payload serialized by RegulationResearchReportNode.extract_input()
# into the structured WorkflowState fields (docs/02_design.md).

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_REQUIRED_KEYS = ("regulators", "domains", "institution_type", "output_format")


class ParamsHydrateNode(FunctionNode):
    """Parse the serialized request_params into structured inner-state fields."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}
        raw = state.get("user_input", "")
        try:
            params = json.loads(raw)
            if not isinstance(params, dict):
                raise ValueError("request payload is not a JSON object")
            missing = [k for k in _REQUIRED_KEYS if k not in params]
            if missing:
                raise ValueError(f"request payload missing keys: {missing}")
        except (json.JSONDecodeError, ValueError) as e:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"ParamsHydrateNode: cannot hydrate request params — {e}"],
            }

        emit_trace_event("params_hydrated", {
            "regulators": params["regulators"],
            "institution_type": params["institution_type"],
        }, state)

        return {
            "regulators": list(params["regulators"]),
            "domains": list(params["domains"]),
            "institution_type": str(params["institution_type"]),
            "output_format": str(params["output_format"]),
            "refine_rounds": 0,
            "fetched_documents": [],
            "fetch_errors": [],
            "status": AgentStatus.SUCCESS.value,
        }
