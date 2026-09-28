"""AgentCore Platform v1.0"""

# Inner graph for FIN-C2-113 — RegulationResearchReportWorkflow.
# Instantiated (and cached) by RegulationResearchReportNode.get_subgraph().
#
# Topology:
#   START → params_hydrate → live_source_fetch → result_normalize
#         → institution_filter → deadline_score → compliance_impact_classify
#         → priority_report_synthesize → report_draft_review → {route}
#                                             ├─ refine → live_source_fetch (loop)
#                                             └─ approve/reject/cap/error → END

from __future__ import annotations

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus

from src.nodes.compliance_impact_classify_node import ComplianceImpactClassifyNode
from src.nodes.deadline_score_node import DeadlineScoreNode
from src.nodes.institution_filter_node import InstitutionFilterNode
from src.nodes.live_source_fetch_node import LiveSourceFetchNode
from src.nodes.params_hydrate_node import ParamsHydrateNode
from src.nodes.priority_report_synthesize_node import PriorityReportSynthesizeNode
from src.nodes.report_draft_review_node import ReportDraftReviewNode
from src.nodes.result_normalize_node import ResultNormalizeNode
from src.schemas.workflow_state import WorkflowState
from src.services.regulator_source_service import RegulatorSourceService


class RegulationResearchReportWorkflow(BaseGraph):
    """Multi-step regulation research → prioritized compliance report."""

    @property
    def name(self) -> str:
        return "RegulationResearchReportWorkflow"

    @property
    def state_schema(self) -> type:
        return WorkflowState

    def _validate_config(self) -> None:
        # llm is injected by the outer GraphNode config; absence is surfaced as a
        # node-level error at runtime (testable), not a compile-time crash.
        pass

    def register_nodes(self) -> None:
        llm = self.config.get("llm")
        source_service = RegulatorSourceService(
            sources=self.config.get("sources"),
            timeout=self.config.get("fetch_timeout_seconds", 20),
        )
        self._nodes["params_hydrate"] = ParamsHydrateNode()
        self._nodes["live_source_fetch"] = LiveSourceFetchNode(service=source_service)
        self._nodes["result_normalize"] = ResultNormalizeNode(llm=llm)
        self._nodes["institution_filter"] = InstitutionFilterNode()
        self._nodes["deadline_score"] = DeadlineScoreNode()
        self._nodes["compliance_impact_classify"] = ComplianceImpactClassifyNode(llm=llm)
        self._nodes["priority_report_synthesize"] = PriorityReportSynthesizeNode(llm=llm)
        self._nodes["report_draft_review"] = ReportDraftReviewNode(
            known_regulators=source_service.known_regulators(),
        )

    def add_edges(self) -> None:
        self._sg.add_edge(START, "params_hydrate")
        self._sg.add_edge("params_hydrate", "live_source_fetch")
        self._sg.add_edge("live_source_fetch", "result_normalize")
        self._sg.add_edge("result_normalize", "institution_filter")
        self._sg.add_edge("institution_filter", "deadline_score")
        self._sg.add_edge("deadline_score", "compliance_impact_classify")
        self._sg.add_edge("compliance_impact_classify", "priority_report_synthesize")
        self._sg.add_edge("priority_report_synthesize", "report_draft_review")
        self._sg.add_conditional_edges("report_draft_review", self.route)

    def route(self, state: WorkflowState) -> str:
        """Refine loop router — MUST annotate WorkflowState, not AgentState
        (AgentState annotation makes LangGraph drop the custom fields and
        every branch falls through to the default — docs/02_design.md)."""
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        if state.get("review_decision") == "refine":
            return "live_source_fetch"
        return END

    def get_output(self, state: WorkflowState) -> dict:
        """Designed together with RegulationResearchReportNode.merge_output()."""
        return {
            "compliance_report": state.get("compliance_report", {}),
            "citations": state.get("citations", []),
            "fetch_errors": state.get("fetch_errors", []),
            "review_decision": state.get("review_decision", ""),
            "status": state.get("status"),
            "error_log": state.get("error_log", []),
            "hitl_draft": state.get("hitl_draft"),
            "hitl_feedback": state.get("hitl_feedback"),
            "hitl_status": state.get("hitl_status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
