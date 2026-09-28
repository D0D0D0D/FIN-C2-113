# FIN-C2-113 — Unit Tests: ResponseValidateNode (post_process, S-3)

import pytest
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.post_process_node import ResponseValidateNode


def _report(citation="https://example.test/FSA"):
    return {
        "p1": [{"title": "AI MRM改正", "issuer": "FSA", "deadline": "2026-07-20",
                "impact_note": "対応必要.", "next_action": "原文確認", "citation_url": citation}],
        "p2": [], "p3": [],
        "institution_impact_summary": "サマリ",
        "recommended_next_steps": ["確認"],
        "coverage_note": "FSA covered",
    }


def _base_state(**overrides):
    state = {"node_history": [], "error_log": [], "status": AgentStatus.SUCCESS.value,
             "citations": [], "compliance_report": _report()}
    state.update(overrides)
    return state


class TestResponseValidateNode:
    def setup_method(self):
        self.node = ResponseValidateNode()

    def test_formats_confirmed_report(self):
        result = self.node.execute(_base_state())
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "コンプライアンス影響レポート" in result["result"]
        assert "https://example.test/FSA" in result["result"]
        assert "法的助言ではありません" in result["result"]

    def test_citation_mandatory_gate(self):
        state = _base_state(compliance_report=_report(citation=""))
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.ERROR.value
        assert "citation-mandatory" in result["error_log"][0]

    def test_missing_report_errors(self):
        result = self.node.execute(_base_state(compliance_report={}))
        assert result["status"] == AgentStatus.ERROR.value

    def test_upstream_error_passthrough(self):
        result = self.node.execute(_base_state(status=AgentStatus.ERROR.value))
        assert result == {}

    def test_s3_blocks_confidential_marker(self):
        with pytest.raises(RuntimeError, match="confidential/unpublished"):
            self.node._extra_security_gate_output({"result": "本文に社外秘の記載"})

    def test_s3_strips_definitive_legal_advice(self):
        gated = self.node._extra_security_gate_output({"result": "この対応は法的に問題ありません。"})
        assert "法的に問題ありません" not in gated["result"]
        assert "[要専門家確認]" in gated["result"]

    def test_trust_level_declared(self):
        assert ResponseValidateNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_s3_lists_are_config_driven(self):
        """S-3 pattern data ships in config/config.yaml — users tune it without
        code changes; built-ins apply only when config omits the keys."""
        node = ResponseValidateNode(config={
            "s3_confidential_markers": ["カスタム秘匿マーカー"],
            "s3_legal_advice_phrases": ["独自の断定表現"],
        })
        # custom marker blocks; built-in one no longer applies
        with pytest.raises(RuntimeError):
            node._extra_security_gate_output({"result": "本文にカスタム秘匿マーカーあり"})
        passed = node._extra_security_gate_output({"result": "社外秘は今回はマーカー外"})
        assert "社外秘" in passed["result"]
        # custom phrase replaced
        gated = node._extra_security_gate_output({"result": "これは独自の断定表現です"})
        assert "独自の断定表現" not in gated["result"]
