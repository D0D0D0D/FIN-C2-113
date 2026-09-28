"""AgentCore Platform v1.0"""

# post_process slot — ResponseValidate (S-3) + output formatting.
# Formats the confirmed compliance report into the final deliverable and runs
# the domain S-3 gate: unpublished/confidential-material block, citation-mandatory
# verification, and definitive-legal-advice removal (docs/02_design.md).

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

# S-3 pattern data (code-level FALLBACK). The operative lists ship in
# config/config.yaml (s3_confidential_markers / s3_legal_advice_phrases)
# so template users can tune them without code changes — see
# docs/0A_release_note.md. These built-ins apply only when config omits the keys.
_CONFIDENTIAL_MARKERS = (
    "社外秘", "部外秘", "取扱注意", "未公表", "非公開資料", "draft - not for publication",
    "confidential", "internal use only",
)
_LEGAL_ADVICE_PHRASES = ("法的に問題ありません", "違法ではありません", "適法です", "法的リスクはありません")

_PRIORITY_LABELS = {"p1": "P1 — 即時対応（期限あり）", "p2": "P2 — 30日以内レビュー", "p3": "P3 — 参考情報"}


class ResponseValidateNode(FunctionNode):
    """Validate and format the final compliance report."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, config: dict | None = None):
        super().__init__()
        config = config or {}
        self._confidential_markers = list(config.get("s3_confidential_markers") or _CONFIDENTIAL_MARKERS)
        self._legal_advice_phrases = list(config.get("s3_legal_advice_phrases") or _LEGAL_ADVICE_PHRASES)

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}

        report = state.get("compliance_report", {})
        if not report:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ResponseValidateNode: no compliance_report to validate"],
            }

        uncited = self._uncited_entries(report)
        if uncited:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "ResponseValidateNode S-3: citation-mandatory check failed — "
                    f"entries without citation_url: {uncited}"
                ],
            }

        formatted = self._format(report, state)
        emit_trace_event("report_validated", {
            "p1": len(report.get("p1", [])),
            "p2": len(report.get("p2", [])),
            "p3": len(report.get("p3", [])),
            "citations": len(state.get("citations", [])),
        }, state)

        return {
            "result": formatted,
            "status": AgentStatus.SUCCESS.value,
        }

    def _uncited_entries(self, report: dict) -> list[str]:
        uncited = []
        for priority in ("p1", "p2", "p3"):
            for entry in report.get(priority, []):
                if isinstance(entry, dict) and not entry.get("citation_url"):
                    uncited.append(f"{priority}:{entry.get('title', '(untitled)')}")
        return uncited

    def _format(self, report: dict, state: dict) -> str:
        lines = ["# コンプライアンス影響レポート", ""]
        fetch_note = report.get("coverage_note", "")
        if fetch_note:
            lines += [f"> 対象範囲: {fetch_note}", ""]
        for priority in ("p1", "p2", "p3"):
            entries = report.get(priority, [])
            lines.append(f"## {_PRIORITY_LABELS[priority]}（{len(entries)}件）")
            for e in entries:
                if not isinstance(e, dict):
                    continue
                deadline = f"（期限: {e.get('deadline')}）" if e.get("deadline") else ""
                lines.append(f"- **{e.get('title', '')}** [{e.get('issuer', '')}]{deadline}")
                if e.get("impact_note"):
                    lines.append(f"  - 影響: {e['impact_note']}")
                if e.get("next_action"):
                    lines.append(f"  - 推奨アクション: {e['next_action']}")
                lines.append(f"  - 出典: {e.get('citation_url', '')}")
            lines.append("")
        if report.get("institution_impact_summary"):
            lines += ["## 機関別影響サマリ", report["institution_impact_summary"], ""]
        steps = report.get("recommended_next_steps", [])
        if steps:
            lines += ["## 推奨次ステップ"] + [f"1. {s}" for s in steps] + [""]
        lines += ["---", "※ 本レポートは規制動向の調査結果であり、法的助言ではありません。対応判断は原文の確認と専門家への相談のうえ行ってください。"]
        return "\n".join(lines)

    def _extra_security_gate_output(self, result: dict) -> dict:
        """S-3 domain checks: confidential-material block + legal-advice phrasing removal."""
        text = result.get("result")
        if not isinstance(text, str):
            return result
        lowered = text.lower()
        for marker in self._confidential_markers:
            if marker.lower() in lowered:
                raise RuntimeError(
                    f"ResponseValidateNode S-3: confidential/unpublished material marker "
                    f"'{marker}' detected in report output — blocked"
                )
        cleaned = text
        for phrase in self._legal_advice_phrases:
            cleaned = cleaned.replace(phrase, "[要専門家確認]")
        if cleaned != text:
            result = dict(result)
            result["result"] = cleaned
        return result
