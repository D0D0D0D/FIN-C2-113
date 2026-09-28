# PB-1: BaseNode → AuditLogger boundary.
# Every domain node's execute() emits at least one domain-specific trace event
# via shared.utils.audit_logger.emit_trace_event — with no silent failures —
# and the framework's node_start/node_complete envelope fires around it.

from framework.schemas.agent_status import AgentStatus

from tests.conftest import FakeLLM, RecordingFetcher, external_ctx, make_agent, make_request

# domain events each execute() must emit (docs/02_design.md §Shared Components)
_EXPECTED_DOMAIN_EVENTS = {
    "request_parsed",            # InputParseNode
    "params_hydrated",           # ParamsHydrateNode
    "regulation_fetch_completed",  # LiveSourceFetchNode
    "records_normalized",        # ResultNormalizeNode
    "records_filtered",          # InstitutionFilterNode
    "deadline_scored",           # DeadlineScoreNode
    "impact_classified",         # ComplianceImpactClassifyNode
    "report_synthesized",        # PriorityReportSynthesizeNode
    "draft_review_feedback",     # ReportDraftReviewNode (after resume)
    "report_validated",          # ResponseValidateNode
    "regulation_research_report_completed",  # RegulationResearchReportNode (main slot)
}


class TestPB1AuditEvents:
    def test_every_node_emits_domain_event_through_full_cycle(self, monkeypatch):
        events: list[str] = []

        import shared.utils.audit_logger as audit_logger
        real_emit = audit_logger.emit_trace_event

        def recording_emit(event_type, payload, state):
            events.append(event_type)
            return real_emit(event_type, payload, state)

        # patch at source module; node modules (and framework base_node) imported
        # the symbol directly, so patch those references too
        monkeypatch.setattr(audit_logger, "emit_trace_event", recording_emit)
        import framework.nodes.base_node as base_node
        monkeypatch.setattr(base_node, "emit_trace_event", recording_emit)
        import src.nodes.compliance_impact_classify_node as n5
        import src.nodes.deadline_score_node as n4
        import src.nodes.institution_filter_node as n3
        import src.nodes.live_source_fetch_node as n1
        import src.nodes.main_node as nmain
        import src.nodes.params_hydrate_node as n0
        import src.nodes.post_process_node as n8
        import src.nodes.pre_process_node as npre
        import src.nodes.priority_report_synthesize_node as n6
        import src.nodes.report_draft_review_node as n7
        import src.nodes.result_normalize_node as n2
        for mod in (n0, n1, n2, n3, n4, n5, n6, n7, n8, npre, nmain):
            monkeypatch.setattr(mod, "emit_trace_event", recording_emit)

        agent = make_agent(FakeLLM(), RecordingFetcher())
        out = agent.invoke(make_request(), ctx=external_ctx())
        final = agent.resume(thread_id=out["thread_id"], feedback={"action": "approve"})
        assert final["status"] == AgentStatus.SUCCESS.value

        missing = _EXPECTED_DOMAIN_EVENTS - set(events)
        assert not missing, f"domain events not emitted: {missing}"

        # framework envelope fired too (emitted by __call__, not duplicated in execute)
        assert "node_start" in events
        assert "node_complete" in events
