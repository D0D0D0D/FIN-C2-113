# FIN-C2-113 — shared test fixtures.
# Wheel-era repo: framework/shared come from the installed agenticstar-agentcore
# package (no sys.modules stubs). Only repo-root path setup + domain fakes here.

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.services.llm.base_llm import BaseLLM  # noqa: E402


class FakeLLM(BaseLLM):
    """Deterministic BaseLLM double keyed on prompt markers.

    - normalize prompt → one record per fetched page (issuer inferred from URL)
    - classify prompt  → one classification per input record (count-matched)
    - synthesize prompt→ canonical P1 report; marks integration on refine rounds
    """

    def __init__(self, synth_body: dict | None = None, broken: str | None = None):
        self.synth_calls = 0
        self.calls: list[str] = []
        self._synth_body = synth_body
        self._broken = broken  # "normalize" | "classify" | "synthesize" → return garbage

    def complete(self, messages: list) -> dict:
        prompt = messages[-1]["content"]
        if "extract the individual regulatory publications" in prompt:
            self.calls.append("normalize")
            if self._broken == "normalize":
                return self._resp("not json at all")
            issuer = "BOJ" if "boj.or.jp" in prompt else ("Basel" if "bis.org" in prompt else "FSA")
            return self._resp(json.dumps([{
                "issuer": issuer, "title": f"{issuer} publication", "published": "2026-06-20",
                "deadline": "2026-07-20", "topic": "capital", "url": f"https://example.test/{issuer}",
                "summary": "summary",
            }]))
        if "Classify the compliance impact" in prompt:
            self.calls.append("classify")
            if self._broken == "classify":
                return self._resp("not json at all")
            records = json.loads(prompt.split("Records:\n", 1)[1])
            return self._resp(json.dumps([
                {"impact_areas": ["capital"], "action_type": "review-required", "impact_note": "影響あり."}
                for _ in records
            ]))
        self.calls.append("synthesize")
        self.synth_calls += 1
        if self._broken == "synthesize":
            return self._resp("not json at all")
        if self._synth_body is not None:
            return self._resp(json.dumps(self._synth_body, ensure_ascii=False))
        integrated = "Previous draft" in prompt
        return self._resp(json.dumps({
            "p1": [{"title": f"synth-{self.synth_calls}", "issuer": "FSA", "deadline": "2026-07-20",
                    "impact_note": "対応が必要.", "next_action": "原文確認",
                    "citation_url": "https://example.test/FSA"}],
            "p2": [], "p3": [],
            "institution_impact_summary": f"integrated={integrated}",
            "recommended_next_steps": ["原文確認"],
            "coverage_note": "requested regulators covered",
        }, ensure_ascii=False))

    @staticmethod
    def _resp(content: str) -> dict:
        return {"content": content, "tool_calls": [], "model": "fake-llm",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "model": "fake-llm"}}

    def stream(self, messages: list):
        yield ""

    def bind_tools(self, tools: list) -> "BaseLLM":
        return self


class RecordingFetcher:
    """Fake HTTP fetcher — records URLs; can fail per-host."""

    def __init__(self, fail_hosts: tuple = ()):
        self.urls: list[str] = []
        self._fail_hosts = fail_hosts

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        for host in self._fail_hosts:
            if host in url:
                raise RuntimeError(f"simulated fetch failure for {host}")
        return f"<html>publication page at {url}</html>"


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def fetcher():
    return RecordingFetcher()


def make_agent(llm, fetcher_fn, extra_config: dict | None = None):
    """Build a compiled outer Graph with fakes wired into the cached subgraph."""
    from langgraph.checkpoint.memory import InMemorySaver

    from src.graph.graph import Graph

    agent = Graph(config={
        "memory_enabled": True,
        "hitl": {"enabled": True, "max_hitl": 8},
        "llm": llm,
        **(extra_config or {}),
    })
    agent.compile(checkpointer=InMemorySaver())
    subgraph = agent._nodes["main"].get_subgraph()
    subgraph._nodes["live_source_fetch"]._service._fetcher = fetcher_fn
    return agent


def make_request(regulators=("FSA",), domains=("capital",), institution_type="mega-bank", output_format="brief") -> str:
    return json.dumps({
        "regulators": list(regulators),
        "domains": list(domains),
        "institution_type": institution_type,
        "output_format": output_format,
    })


def external_ctx():
    from framework.schemas.invocation_context import InvocationContext
    from framework.schemas.trust_level import TrustLevel

    return InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


@pytest.fixture
def agent_config(monkeypatch) -> dict:
    """実 LLM 1回転チェック用 config。

    入力は `tests/integration/test_graph_e2e.py::TestHappyPathWithHitl` と同じ
    `make_request()` 系列を使う。live source fetch は integration の
    `RecordingFetcher` と同じく fake HTML に固定し、外部 HTTP に出さない。
    """
    from src.services.regulator_source_service import RegulatorSourceService

    def _fake_http_fetch(_self, url: str) -> str:
        return f"<html>publication page at {url}</html>"

    monkeypatch.setattr(RegulatorSourceService, "_http_fetch", _fake_http_fetch)
    return {
        "memory_enabled": True,
        "hitl": {"enabled": True, "max_hitl": 8},
        "fetch_timeout_seconds": 20,
    }


@pytest.fixture
def case_payload() -> str:
    """`tests/integration/test_graph_e2e.py` の happy path と同じ JSON request。"""
    return make_request(regulators=("FSA",), domains=("capital",), institution_type="mega-bank", output_format="brief")


@pytest.fixture
def live_input_context() -> dict:
    """HITL draft review は実 LLM smoke では自動承認経路にする。"""
    return {"hitl_allowed": False}
