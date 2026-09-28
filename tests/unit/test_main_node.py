# FIN-C2-113 — Unit Tests: RegulationResearchReportNode (main slot, GraphNode)

import json
from unittest.mock import patch

from framework.schemas.agent_status import AgentStatus

from src.nodes.main_node import RegulationResearchReportNode


class TestRegulationResearchReportNode:
    def setup_method(self):
        self.node = RegulationResearchReportNode(config={})

    def test_extract_input_serializes_request_params(self):
        params = {"regulators": ["FSA"], "domains": ["capital"],
                  "institution_type": "mega-bank", "output_format": "brief"}
        extracted = self.node.extract_input({"request_params": params})
        assert json.loads(extracted) == params

    def test_merge_output_promotes_only_report_and_hitl_fields(self):
        sub_result = {
            "compliance_report": {"p1": []}, "citations": [{"url": "u"}],
            "status": AgentStatus.SUCCESS.value, "error_log": [],
            "hitl_draft": {"p1": []}, "hitl_feedback": {"action": "approve"}, "hitl_status": "approved",
            # inner intermediates that must NOT be promoted:
            "fetch_errors": [], "review_decision": "approved",
            "node_history": ["ParamsHydrateNode"], "trace_id": "t", "correlation_id": "c",
        }
        merged = self.node.merge_output({}, sub_result)
        assert set(merged.keys()) == {
            "compliance_report", "citations", "status", "error_log",
            "hitl_draft", "hitl_feedback", "hitl_status",
        }

    def test_upstream_error_passthrough(self):
        result = self.node.execute({"status": AgentStatus.ERROR.value})
        assert result == {}

    def test_subgraph_is_cached(self):
        """A fresh inner graph per call would lose the suspend checkpoint and
        loop the HITL interrupt forever (docs/02_design.md §HITL)."""
        assert self.node.get_subgraph() is self.node.get_subgraph()

    def test_propagate_hitl_enabled(self):
        assert RegulationResearchReportNode.propagate_hitl is True
        assert RegulationResearchReportNode.error_strategy == "propagate"

    def test_execute_method_signature(self):
        """node contract: execute(self, state) — no _invoke_impl at domain level."""
        import inspect

        sig = inspect.signature(RegulationResearchReportNode.execute)
        params = list(sig.parameters.keys())
        assert params[:2] == ["self", "state"]
        assert "_invoke_impl" not in RegulationResearchReportNode.__dict__

    def test_execute_emits_domain_audit_event_on_success(self):
        """S-4: execute() must emit at
        least one domain event at the main-slot boundary, not only rely on
        the framework's node_start/node_complete envelope."""
        sub_result = {
            "compliance_report": {"p1": [{"title": "x"}], "p2": [], "p3": []},
            "citations": [{"url": "u"}],
            "status": AgentStatus.SUCCESS.value,
            "error_log": [],
            "hitl_draft": None, "hitl_feedback": None, "hitl_status": "approved",
        }
        with patch("src.nodes.main_node.emit_trace_event") as mock_emit, \
             patch("framework.nodes.graph_node.GraphNode.execute", return_value=sub_result):
            result = self.node.execute({"request_params": {}, "status": AgentStatus.SUCCESS.value})

        assert result["status"] == AgentStatus.SUCCESS.value
        mock_emit.assert_called_once()
        event_name, payload, state = mock_emit.call_args[0]
        assert event_name == "regulation_research_report_completed"
        assert payload["p1"] == 1
        assert payload["citations"] == 1

    def test_execute_does_not_emit_on_upstream_error_passthrough(self):
        """Error passthrough returns {} immediately — no domain event, matching
        the existing pre_process/post_process error-path convention."""
        with patch("src.nodes.main_node.emit_trace_event") as mock_emit:
            result = self.node.execute({"status": AgentStatus.ERROR.value})

        assert result == {}
        mock_emit.assert_not_called()
