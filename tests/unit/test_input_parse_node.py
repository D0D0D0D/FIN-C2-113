# FIN-C2-113 — Unit Tests: InputParseNode (pre_process, S-2)

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.pre_process_node import InputParseNode, _MAX_INPUT_CHARS


class TestInputParseNode:
    def setup_method(self):
        self.node = InputParseNode()

    def test_json_request_parsed(self):
        state = {
            "user_input": '{"regulators": ["FSA", "BOJ"], "domains": ["AI MRM"],'
                          ' "institution_type": "securities", "output_format": "detailed"}',
            "node_history": [], "error_log": [],
        }
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["request_params"]["regulators"] == ["FSA", "BOJ"]
        assert result["request_params"]["institution_type"] == "securities"
        assert result["request_params"]["output_format"] == "detailed"

    def test_natural_language_fallback(self):
        state = {"user_input": "金融庁とバーゼルのAI規制動向をメガバンク向けに詳細で調べて",
                 "node_history": [], "error_log": []}
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert set(result["request_params"]["regulators"]) == {"FSA", "Basel"}
        assert result["request_params"]["institution_type"] == "mega-bank"
        assert result["request_params"]["output_format"] == "detailed"

    def test_empty_input_errors(self):
        result = self.node.execute({"user_input": "", "node_history": [], "error_log": []})
        assert result["status"] == AgentStatus.ERROR.value

    def test_unknown_regulator_rejected(self):
        state = {"user_input": '{"regulators": ["FSB"], "domains": [],'
                               ' "institution_type": "mega-bank", "output_format": "brief"}',
                 "node_history": [], "error_log": []}
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.ERROR.value
        assert "FSB" in result["error_log"][0]

    def test_defaults_applied(self):
        result = self.node.execute({"user_input": "{}", "node_history": [], "error_log": []})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["request_params"]["regulators"] == ["FSA", "BOJ", "Basel", "SEC", "APRA"]
        assert result["request_params"]["institution_type"] == "unspecified"
        assert result["request_params"]["output_format"] == "brief"

    def test_s2_input_size_ceiling(self):
        state = {"user_input": "x" * (_MAX_INPUT_CHARS + 1), "node_history": [], "error_log": []}
        gated = self.node._extra_security_gate_input(state)
        assert gated["status"] == AgentStatus.ERROR.value

    def test_trust_level_declared(self):
        assert InputParseNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_config_extended_regulator_accepted(self):
        """Regulators added via config.sources are usable without code changes (F-01)."""
        node = InputParseNode(known_regulators=["FSA", "BOJ", "Basel", "SEC", "APRA", "ESMA"])
        state = {"user_input": '{"regulators": ["ESMA"], "domains": [],'
                               ' "institution_type": "mega-bank", "output_format": "brief"}',
                 "node_history": [], "error_log": []}
        result = node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["request_params"]["regulators"] == ["ESMA"]

    def test_default_regulators_follow_allowlist(self):
        node = InputParseNode(known_regulators=["FSA", "ESMA"])
        result = node.execute({"user_input": "{}", "node_history": [], "error_log": []})
        assert result["request_params"]["regulators"] == ["FSA", "ESMA"]
