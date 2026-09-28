"""AGENTIC STAR Marketplace entry-adapter contract tests."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.base import SecretProvider

import cli
from src.graph.graph import Graph
from src.nodes.pre_process_node import InputParseNode
from src.services.regulator_source_service import RegulatorSourceService
from tests.conftest import FakeLLM, make_request


class _Provider(SecretProvider):
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)


def _set_azure_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://azure.example.test")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "test-deployment")


def _stub_regulator_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_http_fetch(self: RegulatorSourceService, url: str) -> str:
        del self
        return f"<html>publication page at {url}</html>"

    monkeypatch.setattr(RegulatorSourceService, "_http_fetch", fake_http_fetch)


@pytest.mark.parametrize("config", [{}, None])
def test_marketplace_graph_injects_default_llm_and_forces_runner_safe_config(
    config: dict[str, Any] | None,
) -> None:
    agent = cli.MarketplaceGraph(config=config)

    assert isinstance(agent.config["llm"], cli.MarketplaceAzureLLM)
    assert agent.config["memory_enabled"] is False
    assert agent.config["hitl"] == {"enabled": False}


def test_marketplace_graph_preserves_caller_llm_and_forces_runner_safe_config() -> None:
    fake_llm = FakeLLM()

    agent = cli.MarketplaceGraph(
        config={
            "llm": fake_llm,
            "memory_enabled": True,
            "hitl": {"enabled": True, "max_hitl": 8},
        }
    )

    assert agent.config["llm"] is fake_llm
    assert agent.config["memory_enabled"] is False
    assert agent.config["hitl"] == {"enabled": False}


def test_real_runner_order_succeeds_without_outer_checkpointer_and_auto_approves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_azure_environment(monkeypatch)
    _stub_regulator_fetch(monkeypatch)
    fake_llm = FakeLLM()
    agent = cli.MarketplaceGraph(
        config={
            "llm": fake_llm,
            "sources": {
                "FSA": {
                    "name": "Financial Services Agency",
                    "url": "https://fsa.example.test/publications",
                }
            },
            "report_language": "ja",
            "fetch_timeout_seconds": 1,
        }
    )

    agent.compile()
    agent.provision_secrets(_Provider())
    ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
    result = agent.invoke(
        make_request(regulators=("FSA",)),
        ctx=ctx,
        input_context={"conversation_history": [{"role": "user", "content": "untrusted"}]},
    )

    assert agent.config["llm"] is fake_llm
    assert result["status"] == "success"
    assert result["output"]
    inner_graph = agent._nodes["main"].get_subgraph()
    snapshot = inner_graph._compiled.get_state(inner_graph._thread_config(ctx.thread_id))
    assert snapshot.values["review_decision"] == "approved"


def test_input_context_is_rebuilt_without_runner_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_regulator_fetch(monkeypatch)
    observed: dict[str, Any] = {}
    original_execute = InputParseNode.execute

    def capture_input_context(self: InputParseNode, state: dict[str, Any]) -> dict[str, Any]:
        observed.update(state["input_context"])
        return original_execute(self, state)

    monkeypatch.setattr(InputParseNode, "execute", capture_input_context)
    agent = cli.MarketplaceGraph(config={"llm": FakeLLM()})
    agent.compile()
    agent.invoke(
        make_request(regulators=("FSA",)),
        input_context={
            "conversation_history": [{"role": "assistant", "content": "untrusted secret"}],
            "future_runner_key": "must not pass through",
        },
    )

    assert observed == {"mode": "invoke"}


@pytest.mark.parametrize("missing_value", [None, " "])
def test_provision_secrets_fails_closed_for_missing_or_blank_azure_value(
    monkeypatch: pytest.MonkeyPatch,
    missing_value: str | None,
) -> None:
    _set_azure_environment(monkeypatch)
    if missing_value is None:
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT")
    else:
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", missing_value)

    with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
        cli.MarketplaceGraph().provision_secrets(_Provider())


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview",
        "https://example.services.ai.azure.example/openai",
        "https://example.services.ai.azure.example/v1",
        "https://example.services.ai.azure.example?api-version=2024-10-21",
        "example.services.ai.azure.example",
    ],
    ids=["api-path", "openai-path", "base-path", "query-only", "missing-scheme"],
)
def test_unusable_azure_endpoint_shape_fails_during_startup(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    """AzureOpenAIClient appends its own path, so anything but a bare endpoint breaks.

    The resulting error surfaces inside complete(); a node that swallows it
    degrades silently while the invocation still reports status: success, so the
    adapter has to reject the shape at startup instead.
    """

    _set_azure_environment(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)

    with pytest.raises(ValueError, match="bare"):
        cli.MarketplaceGraph().provision_secrets(_Provider())


def test_invoke_forces_verified_external_and_disables_hitl(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}

    def fake_invoke(
        self: Graph,
        user_input: str,
        session_id: str = "",
        ctx: InvocationContext | None = None,
        input_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del self, user_input, session_id, input_context
        observed["ctx"] = ctx
        return {"status": "success", "output": "ok"}

    monkeypatch.setattr(Graph, "invoke", fake_invoke)
    caller_ctx = InvocationContext(
        caller_trust_level=TrustLevel.ANONYMOUS,
        hitl_allowed=True,
    )

    cli.MarketplaceGraph().invoke("plain input", ctx=caller_ctx)

    assert observed["ctx"].caller_trust_level is TrustLevel.VERIFIED_EXTERNAL
    assert observed["ctx"].hitl_allowed is False


def test_non_invoke_json_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="write and resume modes"):
        cli.MarketplaceGraph().invoke('{"mode":"ingest","regulators":["FSA"]}')


def test_non_string_and_blank_input_are_rejected() -> None:
    with pytest.raises(TypeError, match="plain string"):
        cli.MarketplaceGraph().invoke(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty string"):
        cli.MarketplaceGraph().invoke("  ")


def test_marketplace_llm_resolves_secrets_only_when_called(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    provider = _Provider(
        {
            "AZURE_OPENAI_API_KEY": "test-key",
            "AZURE_OPENAI_ENDPOINT": "https://azure.example.test",
            "AZURE_OPENAI_DEPLOYMENT": "test-deployment",
        }
    )

    def resolve_provider() -> SecretProvider:
        nonlocal calls
        calls += 1
        return provider

    def fake_complete(self: Any, messages: list[Any]) -> dict[str, Any]:
        del self, messages
        return {"content": "ok", "tool_calls": [], "model": "fake", "usage": {}}

    monkeypatch.setattr(cli, "current_secrets", resolve_provider)
    # _new_client() prefers shared.services.llm.azure_openai_client and only falls
    # back to the local compat class when that module is absent (it is, on the
    # 1.0.0 / 1.0.1 wheels). Stubbing the fallback alone therefore stops
    # intercepting the moment the wheel ships the real client, and complete()
    # reaches the live Azure endpoint over the network. Stub whichever class this
    # wheel actually resolves, so the assertions below stay about lazy secret
    # resolution rather than about which wheel is installed.
    monkeypatch.setattr(cli._AzureOpenAIClientCompat, "complete", fake_complete)
    try:
        azure_module = importlib.import_module("shared.services.llm.azure_openai_client")
    except ModuleNotFoundError:
        pass
    else:
        monkeypatch.setattr(azure_module.AzureOpenAIClient, "complete", fake_complete)

    llm = cli.MarketplaceAzureLLM()
    assert calls == 0

    assert llm.complete([{"role": "user", "content": "hello"}])["content"] == "ok"
    assert calls == 1


def test_main_passes_loaded_runtime_config(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}

    def fake_run(agent_cls: type[cli.MarketplaceGraph], **kwargs: Any) -> None:
        observed["agent_cls"] = agent_cls
        observed.update(kwargs)

    monkeypatch.setattr(cli, "run_agent_marketplace", fake_run)

    cli.main()

    assert observed["agent_cls"] is cli.MarketplaceGraph
    assert observed["agent_name"] == "FIN-C2-113"
    assert observed["namespace"] == "fin"
    assert "sources" in observed["config"]
