"""AgentCore Platform v1.0"""

# Inner-workflow state for FIN-C2-113 (RegulationResearchReportWorkflow).
# Flat TypedDict — same ADR-005 constraints as the outer State.
# These intermediates never leave the subgraph: merge_output() promotes only
# compliance_report / citations / status (+ HITL signal fields) to the outer graph.

from __future__ import annotations

from framework.schemas.agent_state import AgentState


class WorkflowState(AgentState):
    """Inner graph state.

    Conditional-edge functions of the inner graph MUST annotate their state
    parameter with THIS class (not AgentState) — annotating AgentState makes
    LangGraph project the state down and drop the custom fields below,
    which silently mis-routes every branch (docs/02_design.md).
    """

    # ── structured request parameters restored by ParamsHydrate ──
    regulators: list
    domains: list
    institution_type: str
    output_format: str

    # ── research intermediates ──
    fetched_documents: list      # [{"regulator","title","url","content","fetched_at"}]
    fetch_errors: list           # [{"regulator","reason"}] — partial failures as DATA, never exceptions
    normalized_records: list     # common schema: issuer/published/deadline/topic/url/summary
    filtered_records: list       # institution-type filtered
    scored_records: list         # + deadline urgency score
    classified_records: list     # + impact classification tags

    # ── HITL refine loop bookkeeping (template-owned counter; hitl_count is backbone-only) ──
    refine_rounds: int
    review_decision: str         # "approved" | "rejected" | "refine"

    # ── synthesis output (promoted to outer state via merge_output) ──
    compliance_report: dict
    citations: list
