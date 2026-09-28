# FIN-C2-113 — Integration Tests: full outer-graph invoke/resume flows
# All flows run with fake LLM + fake fetcher (no network, no real LLM).

from framework.schemas.agent_status import AgentStatus

from tests.conftest import FakeLLM, RecordingFetcher, external_ctx, make_agent, make_request


class TestHappyPathWithHitl:
    def test_invoke_suspends_at_draft_review(self):
        agent = make_agent(FakeLLM(), RecordingFetcher())
        out = agent.invoke(make_request(), ctx=external_ctx())
        assert out["status"] == AgentStatus.AWAITING_HUMAN.value
        assert out["thread_id"]
        assert out["hitl_metadata"]["reason"] == "compliance report draft review"

    def test_approve_completes_with_report(self):
        agent = make_agent(FakeLLM(), RecordingFetcher())
        out = agent.invoke(make_request(), ctx=external_ctx())
        final = agent.resume(thread_id=out["thread_id"], feedback={"action": "approve"})
        assert final["status"] == AgentStatus.SUCCESS.value
        assert "コンプライアンス影響レポート" in final["output"]
        assert final["node_history"] == [
            "InitializeNode", "InputParseNode", "RegulationResearchReportNode",
            "ResponseValidateNode", "FinalizeNode",
        ]

    def test_reject_terminates_without_deliverable(self):
        """HITL-2: on reject the outer route (hitl_status=rejected) goes
        straight to finalize — ResponseValidateNode must NOT run, so the
        rejected/empty report can never trip its "no compliance_report" error."""
        agent = make_agent(FakeLLM(), RecordingFetcher())
        out = agent.invoke(make_request(), ctx=external_ctx())
        final = agent.resume(thread_id=out["thread_id"], feedback={"action": "reject"})
        assert final["status"] == AgentStatus.SUCCESS.value
        assert not final["output"]  # rejected → no formatted deliverable
        assert "ResponseValidateNode" not in final["node_history"]  # post_process skipped
        assert not final.get("error_log")  # and no error raised on the way out


class TestRefineLoop:
    def test_correct_extends_scope_and_reinterrupts(self):
        fetcher = RecordingFetcher()
        llm = FakeLLM()
        agent = make_agent(llm, fetcher)
        out = agent.invoke(make_request(regulators=("FSA",)), ctx=external_ctx())
        assert out["status"] == AgentStatus.AWAITING_HUMAN.value

        out2 = agent.resume(thread_id=out["thread_id"], feedback={
            "action": "correct", "additional_regulators": ["BOJ"], "instructions": "BOJも調べて"})
        assert out2["status"] == AgentStatus.AWAITING_HUMAN.value
        assert out2["hitl_metadata"]["refine_round"] == 1
        # incremental fetch: BOJ only on the refine round
        assert [u for u in fetcher.urls if "boj" in u] and len(fetcher.urls) == 2

        final = agent.resume(thread_id=out2["thread_id"], feedback={"action": "approve"})
        assert final["status"] == AgentStatus.SUCCESS.value
        assert llm.synth_calls == 2  # initial + integrated refine synthesis
        assert "integrated=True" in final["output"]

    def test_refine_loop_unbounded_until_human_approves(self):
        """No template-side cap: repeated "correct" keeps re-presenting drafts
        (human-gated loop — 2026-07-02 review decision)."""
        agent = make_agent(FakeLLM(), RecordingFetcher())
        out = agent.invoke(make_request(), ctx=external_ctx())
        for i in range(1, 5):  # 4 consecutive refine rounds — all must re-interrupt
            out = agent.resume(thread_id=out["thread_id"],
                               feedback={"action": "correct", "instructions": f"round {i}"})
            assert out["status"] == AgentStatus.AWAITING_HUMAN.value
            assert out["hitl_metadata"]["refine_round"] == i
        final = agent.resume(thread_id=out["thread_id"], feedback={"action": "approve"})
        assert final["status"] == AgentStatus.SUCCESS.value
        assert "コンプライアンス影響レポート" in final["output"]


class TestErrorPaths:
    def test_invalid_regulator_short_circuits_before_research(self):
        fetcher = RecordingFetcher()
        agent = make_agent(FakeLLM(), fetcher)
        out = agent.invoke('{"regulators": ["FSB"], "domains": [], '
                           '"institution_type": "mega-bank", "output_format": "brief"}',
                           ctx=external_ctx())
        assert out["status"] == AgentStatus.ERROR.value
        assert fetcher.urls == []  # research never started

    def test_all_sources_failed_is_error(self):
        agent = make_agent(FakeLLM(), RecordingFetcher(fail_hosts=("fsa.go.jp",)))
        out = agent.invoke(make_request(regulators=("FSA",)), ctx=external_ctx())
        assert out["status"] == AgentStatus.ERROR.value

    def test_partial_source_failure_still_reaches_review(self):
        agent = make_agent(FakeLLM(), RecordingFetcher(fail_hosts=("boj.or.jp",)))
        out = agent.invoke(make_request(regulators=("FSA", "BOJ")), ctx=external_ctx())
        assert out["status"] == AgentStatus.AWAITING_HUMAN.value
        assert out["hitl_metadata"]["fetch_errors"][0]["regulator"] == "BOJ"

    def test_anonymous_caller_denied_by_s1(self):
        from framework.schemas.invocation_context import InvocationContext
        from framework.schemas.trust_level import TrustLevel

        agent = make_agent(FakeLLM(), RecordingFetcher())
        ctx = InvocationContext(caller_trust_level=TrustLevel.ANONYMOUS)
        out = agent.invoke(make_request(), ctx=ctx)
        assert out["status"] == AgentStatus.ERROR.value
        assert any("S-1 trust gate denied" in e for e in out.get("error_log", []))
