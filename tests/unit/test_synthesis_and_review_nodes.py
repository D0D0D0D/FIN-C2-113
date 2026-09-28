# FIN-C2-113 — Unit Tests: ComplianceImpactClassify / PriorityReportSynthesize / ReportDraftReview

import json

import pytest
from framework.schemas.agent_status import AgentStatus
from framework.schemas.hitl_status import HitlStatus
from langgraph.errors import GraphInterrupt

from src.nodes.compliance_impact_classify_node import ComplianceImpactClassifyNode
from src.nodes.priority_report_synthesize_node import PriorityReportSynthesizeNode
from src.nodes.report_draft_review_node import ReportDraftReviewNode
from tests.conftest import FakeLLM


def _base_state(**overrides):
    state = {"node_history": [], "error_log": [], "status": AgentStatus.SUCCESS.value}
    state.update(overrides)
    return state


class TestComplianceImpactClassifyNode:
    def test_classifies_each_record(self):
        node = ComplianceImpactClassifyNode(llm=FakeLLM())
        records = [{"issuer": "FSA", "title": "a"}, {"issuer": "BOJ", "title": "b"}]
        result = node.execute(_base_state(institution_type="mega-bank", scored_records=records))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert len(result["classified_records"]) == 2
        assert result["classified_records"][0]["action_type"] == "review-required"

    def test_empty_records_short_circuit(self):
        result = ComplianceImpactClassifyNode(llm=None).execute(_base_state(scored_records=[]))
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["classified_records"] == []

    def test_count_mismatch_errors(self):
        class MismatchLLM(FakeLLM):
            def complete(self, messages):
                return self._resp(json.dumps([{"impact_areas": [], "action_type": "monitor", "impact_note": ""}]))
        node = ComplianceImpactClassifyNode(llm=MismatchLLM())
        records = [{"title": "a"}, {"title": "b"}]
        result = node.execute(_base_state(scored_records=records))
        assert result["status"] == AgentStatus.ERROR.value


class TestPriorityReportSynthesizeNode:
    def test_synthesizes_report_with_citations(self):
        node = PriorityReportSynthesizeNode(llm=FakeLLM())
        state = _base_state(
            institution_type="mega-bank", output_format="brief", refine_rounds=0,
            classified_records=[{"issuer": "FSA", "title": "t", "url": "https://example.test/FSA"}],
            fetched_documents=[{"regulator": "FSA", "fetched_at": "2026-07-02T00:00:00Z"}],
            fetch_errors=[],
        )
        result = node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["compliance_report"]["p1"]
        assert result["citations"][0]["fetched_at"] == "2026-07-02T00:00:00Z"

    def test_refine_round_integrates_previous_draft(self):
        llm = FakeLLM()
        node = PriorityReportSynthesizeNode(llm=llm)
        state = _base_state(
            institution_type="mega-bank", output_format="brief", refine_rounds=1,
            compliance_report={"p1": [{"title": "old"}]},
            hitl_feedback={"action": "correct", "instructions": "BOJも"},
            classified_records=[], fetched_documents=[], fetch_errors=[],
        )
        result = node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["compliance_report"]["institution_impact_summary"] == "integrated=True"

    def test_unparseable_output_errors(self):
        node = PriorityReportSynthesizeNode(llm=FakeLLM(broken="synthesize"))
        result = node.execute(_base_state(refine_rounds=0, classified_records=[], fetched_documents=[], fetch_errors=[]))
        assert result["status"] == AgentStatus.ERROR.value


class TestReportDraftReviewNode:
    """interrupt() suspends via GraphInterrupt outside a runtime; feedback paths are
    exercised through monkeypatched interrupt (full resume paths → integration tests)."""

    def test_graph_interrupt_bubbles_through_call(self, monkeypatch):
        """GraphInterrupt must NOT be swallowed by __call__'s error boundary (PB-7 unit face)."""
        import src.nodes.report_draft_review_node as mod

        def raising_interrupt(payload):
            raise GraphInterrupt(())

        monkeypatch.setattr(mod, "interrupt", raising_interrupt)
        node = ReportDraftReviewNode()
        state = _base_state(compliance_report={"p1": []}, refine_rounds=0,
                            caller_trust_level="VERIFIED_EXTERNAL", correlation_id="t")
        with pytest.raises(GraphInterrupt):
            node(state)  # through __call__ — the S-4 error boundary must re-raise it

    def test_upstream_error_skips_interrupt(self):
        result = ReportDraftReviewNode().execute(_base_state(status=AgentStatus.ERROR.value))
        assert result == {}

    def _run_with_feedback(self, monkeypatch, state, feedback):
        import src.nodes.report_draft_review_node as mod
        monkeypatch.setattr(mod, "interrupt", lambda payload: feedback)
        return ReportDraftReviewNode().execute(state)

    def test_approve_confirms_report(self, monkeypatch):
        state = _base_state(compliance_report={"p1": [1]}, refine_rounds=0)
        result = self._run_with_feedback(monkeypatch, state, {"action": "approve"})
        assert result["review_decision"] == "approved"
        assert result["hitl_status"] == HitlStatus.APPROVED.value
        assert result["hitl_draft"] == {"p1": [1]}

    def test_reject_terminates_without_deliverable(self, monkeypatch):
        state = _base_state(compliance_report={"p1": []}, refine_rounds=0)
        result = self._run_with_feedback(monkeypatch, state, "reject")
        assert result["review_decision"] == "rejected"
        assert result["hitl_status"] == HitlStatus.REJECTED.value

    def test_correct_merges_scope_and_loops(self, monkeypatch):
        state = _base_state(compliance_report={"p1": []}, refine_rounds=0,
                            regulators=["FSA"], domains=["capital"])
        result = self._run_with_feedback(monkeypatch, state, {
            "action": "correct", "additional_regulators": ["BOJ", "FSA", "BOGUS"],
            "additional_domains": ["AI MRM"], "instructions": "BOJとAI MRMも",
        })
        assert result["review_decision"] == "refine"
        assert result["refine_rounds"] == 1
        assert result["regulators"] == ["FSA", "BOJ"]  # dedup + unknown regulator dropped
        assert result["domains"] == ["capital", "AI MRM"]

    def test_refine_loop_is_unbounded_human_gated(self, monkeypatch):
        """No template-side round cap: the loop is bounded by the human reviewer
        (each round requires an explicit "correct") — 2026-07-02 review decision."""
        state = _base_state(compliance_report={"p1": [1]}, refine_rounds=99, regulators=["FSA"])
        result = self._run_with_feedback(monkeypatch, state, {"action": "correct"})
        assert result["review_decision"] == "refine"
        assert result["refine_rounds"] == 100

    def test_does_not_write_hitl_count(self, monkeypatch):
        """hitl_count is backbone-owned — the template must never write it."""
        state = _base_state(compliance_report={"p1": []}, refine_rounds=0)
        for feedback in ({"action": "approve"}, "reject", {"action": "correct"}):
            result = self._run_with_feedback(monkeypatch, state, feedback)
            assert "hitl_count" not in result
