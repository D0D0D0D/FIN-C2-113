"""AgentCore Platform v1.0"""

# main slot — RegulationResearchReportNode (GraphNode).
# Wraps the inner RegulationResearchReportWorkflow (src/graph/domain_workflow_graph.py)
# and propagates its HITL draft-review interrupt to the outer graph.

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


class RegulationResearchReportNode(GraphNode):
    """GraphNode wrapping the regulation research → compliance report workflow.

    Contract notes (docs/02_design.md):
      - get_subgraph() CACHES the inner graph — a fresh instance per call would
        hand resume() an empty checkpointer and loop the interrupt forever.
      - extract_input() serializes request_params to JSON; the inner
        ParamsHydrate node restores the structured fields.
      - merge_output() promotes only the final report fields + HITL signals.
      - execute() passes upstream status="error" through untouched
        (pre_process → main is an unconditional edge; routing happens after main).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = True

    def __init__(self, config: dict | None = None):
        super().__init__()
        self._config = config or {}
        self._subgraph = None  # lazy, then cached — same checkpointer across invoke/resume

    def get_subgraph(self):
        if self._subgraph is None:
            from langgraph.checkpoint.memory import InMemorySaver

            from src.graph.domain_workflow_graph import RegulationResearchReportWorkflow

            subgraph = RegulationResearchReportWorkflow(config=self._inner_config())
            # 1.0.0 wheel does not auto-create a checkpointer from config —
            # interrupt() inside the inner graph requires an explicit one.
            subgraph.compile(checkpointer=InMemorySaver())
            self._subgraph = subgraph
        return self._subgraph

    def _inner_config(self) -> dict:
        """Forward the config keys the inner workflow needs."""
        keys = ("llm", "sources", "report_language", "fetch_timeout_seconds")
        forwarded = {k: self._config[k] for k in keys if k in self._config}
        # inner graph fires interrupt() → it needs hitl/memory enabled on ITS config
        forwarded["memory_enabled"] = True
        forwarded["hitl"] = dict(self._config.get("hitl") or {"enabled": True, "max_hitl": 8})
        return forwarded

    def extract_input(self, state: dict) -> str:
        return json.dumps(state.get("request_params", {}), ensure_ascii=False, default=str)

    def merge_output(self, state: dict, sub_result: dict) -> dict:
        return {
            "compliance_report": sub_result.get("compliance_report", {}),
            "citations": sub_result.get("citations", []),
            "status": sub_result.get("status"),
            "error_log": sub_result.get("error_log", []),
            # HITL signals must cross the subgraph boundary (docs/02_design.md §HITL)
            "hitl_draft": sub_result.get("hitl_draft"),
            "hitl_feedback": sub_result.get("hitl_feedback"),
            "hitl_status": sub_result.get("hitl_status"),
        }

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}  # upstream error passthrough — route() sends it to finalize
        result = super().execute(state)
        # S-4 domain audit event for the main-slot subgraph boundary. node_start/
        # node_complete/node_error are emitted by __call__ automatically —
        # this is the domain-specific event, not a duplicate of those.
        report = result.get("compliance_report") or {}
        emit_trace_event("regulation_research_report_completed", {
            "status": result.get("status"),
            "p1": len(report.get("p1", [])),
            "p2": len(report.get("p2", [])),
            "p3": len(report.get("p3", [])),
            "citations": len(result.get("citations", [])),
            "hitl_status": result.get("hitl_status"),
        }, state)
        return result
