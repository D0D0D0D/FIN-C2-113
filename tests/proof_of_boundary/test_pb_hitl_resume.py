# PB-7: HITL interrupt propagation & resume-to-success.
#
# Verifies:
#  (a) interrupt() inside the inner ReportDraftReview node raises GraphInterrupt
#      that propagates through BaseNode.__call__() (not swallowed by the error
#      boundary) and surfaces as status="awaiting_human" — NOT "error".
#  (b) resume() reaches SUCCESS — this catches the fresh-subgraph-per-call bug
#      where a new checkpointer loses the suspend checkpoint and the graph
#      re-interrupts forever (suspend-only assertions cannot detect it).

from framework.schemas.agent_status import AgentStatus

from tests.conftest import FakeLLM, RecordingFetcher, external_ctx, make_agent, make_request


class TestPB7HitlInterruptPropagation:
    def test_interrupt_surfaces_as_awaiting_human_not_error(self):
        agent = make_agent(FakeLLM(), RecordingFetcher())
        out = agent.invoke(make_request(), ctx=external_ctx())
        assert out["status"] == AgentStatus.AWAITING_HUMAN.value
        assert out["status"] != AgentStatus.ERROR.value
        assert not out.get("error_log")

    def test_resume_reaches_success_not_reinterrupt_loop(self):
        agent = make_agent(FakeLLM(), RecordingFetcher())
        out = agent.invoke(make_request(), ctx=external_ctx())
        final = agent.resume(thread_id=out["thread_id"], feedback={"action": "approve"})
        # a lost suspend checkpoint would re-run from scratch and return
        # awaiting_human again — resume MUST terminate at SUCCESS
        assert final["status"] == AgentStatus.SUCCESS.value
        assert final["output"]

    def test_multi_round_interrupt_then_success(self):
        agent = make_agent(FakeLLM(), RecordingFetcher())
        out = agent.invoke(make_request(regulators=("FSA",)), ctx=external_ctx())
        out = agent.resume(thread_id=out["thread_id"],
                           feedback={"action": "correct", "additional_regulators": ["BOJ"]})
        assert out["status"] == AgentStatus.AWAITING_HUMAN.value  # second round suspend
        final = agent.resume(thread_id=out["thread_id"], feedback={"action": "approve"})
        assert final["status"] == AgentStatus.SUCCESS.value
