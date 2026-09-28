# PB-5: Checkpoint safety.
# Every checkpoint persisted across the invoke → suspend → resume lifecycle
# must contain only msgpack-safe primitives — no credentials, no Pydantic
# models, no dataclasses, no arbitrary Python objects.

import dataclasses

from tests.conftest import FakeLLM, RecordingFetcher, external_ctx, make_agent, make_request

_PRIMITIVES = (str, int, float, bool, bytes, type(None))
_CREDENTIAL_MARKERS = ("sk-", "eyJhbGciOi", "AKIA")


def _assert_primitives_only(value, path="root", failures=None):
    failures = failures if failures is not None else []
    if isinstance(value, dict):
        for k, v in value.items():
            _assert_primitives_only(v, f"{path}.{k}", failures)
    elif isinstance(value, (list, tuple, set)):
        for i, v in enumerate(value):
            _assert_primitives_only(v, f"{path}[{i}]", failures)
    elif not isinstance(value, _PRIMITIVES):
        if dataclasses.is_dataclass(value) or hasattr(value, "model_dump") or hasattr(value, "__dict__"):
            failures.append(f"{path}: non-primitive {type(value).__name__}")
    return failures


def _iter_channel_values(agent):
    checkpointer = agent._compiled.checkpointer
    for ckpt_tuple in checkpointer.list(None):
        yield ckpt_tuple.checkpoint.get("channel_values", {})


class TestPB5CheckpointSafety:
    def _run_full_hitl_cycle(self):
        agent = make_agent(FakeLLM(), RecordingFetcher())
        out = agent.invoke(make_request(), ctx=external_ctx())
        agent.resume(thread_id=out["thread_id"], feedback={"action": "approve"})
        return agent

    def test_outer_checkpoints_are_primitive_only(self):
        agent = self._run_full_hitl_cycle()
        failures = []
        for channel_values in _iter_channel_values(agent):
            # LangGraph-internal channels (e.g. branch sentinels) excluded — we
            # verify OUR state fields, which carry the msgpack persistence risk.
            state_fields = {k: v for k, v in channel_values.items() if not k.startswith("branch:")}
            _assert_primitives_only(state_fields, failures=failures)
        assert failures == [], f"non-primitive values found in checkpoints: {failures}"

    def test_inner_checkpoints_are_primitive_only(self):
        agent = self._run_full_hitl_cycle()
        inner = agent._nodes["main"].get_subgraph()
        failures = []
        for channel_values in _iter_channel_values(inner):
            state_fields = {k: v for k, v in channel_values.items() if not k.startswith("branch:")}
            _assert_primitives_only(state_fields, failures=failures)
        assert failures == [], f"non-primitive values found in inner checkpoints: {failures}"

    def test_no_credential_shapes_in_any_checkpoint(self):
        agent = self._run_full_hitl_cycle()
        inner = agent._nodes["main"].get_subgraph()
        for graph in (agent, inner):
            for channel_values in _iter_channel_values(graph):
                blob = str(channel_values)
                for marker in _CREDENTIAL_MARKERS:
                    assert marker not in blob, f"credential-shaped value '{marker}' in checkpoint"
