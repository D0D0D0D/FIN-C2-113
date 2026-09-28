"""AgentCore Platform v1.0"""

# LLM injection seam for FIN-C2-113.
#
# Nodes never construct an LLM themselves — they receive a BaseLLM via the
# graph config ("llm" key). Tests inject a fake BaseLLM; production builds
# a real client here from config (provider/model) + the agent's SecretProvider.
# API keys flow through the SecretProvider (ctx.secrets model) — never os.environ.

from __future__ import annotations

import json
import re

from framework.secrets.base import SecretProvider
from shared.services.llm.base_llm import BaseLLM


def build_llm(config: dict, secrets: SecretProvider) -> BaseLLM:
    """Build the production LLM client from config + the agent's secret provider.

    Provider SDK classes (langchain_anthropic etc.) are imported lazily so
    optional dependencies are only required in deployments that construct a
    real client. Each wheel client takes a config dict and REQUIRES the API
    key inside it (clients never read os.environ — Phase 3b secret model).
    """
    provider = (config or {}).get("llm_provider", "anthropic")
    model = (config or {}).get("llm_model", "claude-sonnet-5")
    if provider == "anthropic":
        from shared.services.llm.anthropic_client import AnthropicClient
        return AnthropicClient({"model": model, "api_key": secrets.require("ANTHROPIC_API_KEY")})
    if provider == "openai":
        from shared.services.llm.openai_client import OpenAIClient
        return OpenAIClient({"model": model, "api_key": secrets.require("OPENAI_API_KEY")})
    if provider == "bedrock":
        from shared.services.llm.bedrock_client import BedrockClient
        return BedrockClient({
            "model": model,
            "aws_access_key_id": secrets.require("AWS_ACCESS_KEY_ID"),
            "aws_secret_access_key": secrets.require("AWS_SECRET_ACCESS_KEY"),
            "aws_region": secrets.require("AWS_REGION"),
        })
    raise ValueError(f"unknown llm_provider: {provider!r}")


def parse_json_content(content: str):
    """Extract a JSON object/array from an LLM completion text.

    Tolerates markdown code fences and leading/trailing prose.
    Raises ValueError when no JSON can be recovered — callers surface this
    as status="error" (never silently swallow malformed synthesis output).
    """
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fall back to the first {...} or [...] block
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("LLM response contained no parseable JSON")
