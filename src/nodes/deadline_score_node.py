"""AgentCore Platform v1.0"""

# Inner workflow step 4 — DeadlineScore.
# Deterministic urgency scoring from response deadlines. Feeds the P1/P2/P3
# priority decision: deadline within P1 window → p1 candidate, within P2 window
# → p2 candidate, otherwise / no deadline → p3 (FYI).

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_P1_WINDOW_DAYS = 30
_P2_WINDOW_DAYS = 90


def _parse_date(value) -> date | None:
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y年%m月%d日"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


class DeadlineScoreNode(FunctionNode):
    """Score records by deadline urgency (deterministic, testable)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, today: date | None = None):
        super().__init__()
        self._today = today  # injection seam for deterministic tests

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}

        today = self._today or date.today()
        scored: list[dict] = []
        for rec in state.get("filtered_records", []):
            deadline = _parse_date(rec.get("deadline"))
            if deadline is None:
                urgency, days_left = "p3", None
            else:
                days_left = (deadline - today).days
                if days_left <= _P1_WINDOW_DAYS:
                    urgency = "p1"
                elif days_left <= _P2_WINDOW_DAYS:
                    urgency = "p2"
                else:
                    urgency = "p3"
            scored.append({**rec, "deadline_urgency": urgency, "days_to_deadline": days_left})

        emit_trace_event("deadline_scored", {
            "p1": sum(1 for r in scored if r["deadline_urgency"] == "p1"),
            "p2": sum(1 for r in scored if r["deadline_urgency"] == "p2"),
            "p3": sum(1 for r in scored if r["deadline_urgency"] == "p3"),
        }, state)

        return {
            "scored_records": scored,
            "status": AgentStatus.SUCCESS.value,
        }
