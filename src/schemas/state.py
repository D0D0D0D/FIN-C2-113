"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict (see ADR-005 for the prohibited
# object kinds). LangGraph checkpoints use msgpack serialization; non-primitive
# model objects cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or such model objects.

from __future__ import annotations

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Outer graph state for FIN-C2-113.

    Inner-workflow intermediates (fetched documents, normalized records, …)
    stay encapsulated in WorkflowState (src/schemas/workflow_state.py) and
    are never promoted here — merge_output() promotes only the final report
    fields (docs/02_design.md §State Definition).

    Inherited framework fields used by this agent (declared on AgentState —
    do NOT redeclare them here: `node_history` and `error_log` carry an
    `Annotated[..., operator.add]` reducer that a plain redeclaration would
    silently drop, breaking history accumulation):
      user_input / validated_input / status / error_log / node_history /
      result / hitl_draft / hitl_feedback / hitl_status
    """

    # Parsed request parameters built by InputParseNode (pre_process):
    # {"domains": [...], "regulators": [...], "institution_type": str, "output_format": str}
    request_params: dict

    # Final prioritized report body (P1/P2/P3 structure) promoted from the inner workflow.
    compliance_report: dict

    # Citations backing the report: [{"regulator", "title", "url", "fetched_at"}, ...]
    citations: list
