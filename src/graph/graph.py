"""AgentCore Platform v1.0"""

# FIN-C2-113 — outer graph (Cat 2: AgentBaseGraph + GraphNode in the main slot).
#
# Backbone (fixed — framework-wired, add_edges() NOT overridden):
#   START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                           ↓ (RETRY, max 3)
#                                        pre_process
#
# Domain complexity lives inside RegulationResearchReportNode (GraphNode) →
# inner RegulationResearchReportWorkflow (src/graph/domain_workflow_graph.py).

from __future__ import annotations

from framework.graph.agent_base_graph import AgentBaseGraph

from src.nodes.main_node import RegulationResearchReportNode
from src.nodes.post_process_node import ResponseValidateNode
from src.nodes.pre_process_node import InputParseNode
from src.schemas.state import State
from src.services.regulator_source_service import RegulatorSourceService


class Graph(AgentBaseGraph):
    """FinRegulationResearchComplianceReportAgent — Cat 2 outer graph."""

    @property
    def name(self) -> str:
        return "FinRegulationResearchComplianceReportAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects initialize + finalize
        # regulator allowlist derives from the configured source registry so
        # users can add regulators via config.sources without code changes
        known_regulators = RegulatorSourceService(
            sources=self.config.get("sources"),
        ).known_regulators()
        self._nodes["pre_process"] = InputParseNode(known_regulators=known_regulators)
        self._nodes["main"] = RegulationResearchReportNode(config=self.config)
        self._nodes["post_process"] = ResponseValidateNode(config=self.config)

    def _enrich_output(self, output: dict, result: dict) -> None:
        """Surface error detail and structured citations to the API caller."""
        output["error_log"] = result.get("error_log", [])
        output["citations"] = result.get("citations", [])

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.
