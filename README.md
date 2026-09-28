# FIN-C2-113 — FinRegulationResearchComplianceReportAgent

> **Category**: Cat 2 (domain workflow)
> **Industry**: FIN

## Overview

This agent researches financial regulation on demand and turns the result into a prioritised,
cited compliance-impact report. Given a set of regulators, subject domains and an institution
type, it fetches those regulators' current publications live, organises them across authorities,
scores deadlines, classifies impact, and produces a report split into **P1** (act now, deadline
attached), **P2** (review within 30 days) and **P3** (for information). A human reviewer sees the
draft before it is final, and can send it back for more research.

It replaces a recurring manual round: visiting several regulators' publication pages, reading what
is new, working out what it means for one kind of institution, and writing it up in priority
order. The default registry ships five authorities — the Japan Financial Services Agency, the Bank
of Japan, the Basel Committee (BIS), the U.S. Securities and Exchange Commission and the
Australian Prudential Regulation Authority — and more can be added in configuration without
touching code.

Two properties shape the design:

- **There is no regulation database.** Every request fetches the sources at that moment, so the
  report reflects what was published when it ran and nothing is served from a stale cache. The
  cost is that a regulator whose site is unreachable or has changed shape cannot be read — and
  when that happens the report names the authority as **not retrieved** rather than quietly
  omitting it.
- **Every finding must carry its source.** A statement without a citation URL is rejected by the
  output gate rather than published. The gate also blocks reports carrying confidentiality
  markers, and strips categorical legal-conclusion phrasing ("this is lawful", "there is no legal
  risk") — both lists are configuration, so an organization can extend them to its own wording.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Integration

Provide:

- an AgentCore runtime with a checkpointer. HITL requires one; the standalone server compiles with
  an explicit in-memory saver, which a real deployment must replace with a durable one — an
  in-memory saver loses every draft awaiting review on restart, and the per-thread resume lock is
  process-local, so a multi-process deployment needs a per-thread lease or compare-and-set;
- a language model. The client is built from `config["llm_provider"]` / `config["llm_model"]` and
  the agent's secret provider, and it is built **at start-up** — see *Behaviour without the
  platform*. A caller that owns process wiring may instead inject a ready `BaseLLM` at
  `config["llm"]`, which takes precedence;
- outbound HTTPS access to the regulator sites in the source registry;
- caller authentication in front of the entry adapter.

`config/agent.yaml` declares five secrets under `requires.secrets` — `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`. The manifest is
checked at compile time as a set, so **all five must be present in the secret store regardless of
which provider is selected**, even though only the selected provider's keys are read. Narrow the
list if your deployment only ever uses one provider.

The standalone adapter loads `config/config.yaml` and passes it as `Graph(config=...)`:

| Key | Default | Meaning |
|---|---|---|
| `sources.<REGULATOR>.url` | five authorities | The research registry. Adding an entry adds an authority; no code change |
| `s3_confidential_markers` | 8 patterns | Markers that cause the output gate to **block** the report |
| `s3_legal_advice_phrases` | 4 phrases | Categorical legal-conclusion wording stripped from the report |
| `llm_provider` / `llm_model` | `anthropic` / `claude-sonnet-5` | Model selection |
| `fetch_timeout_seconds` | `20` | Per-source fetch timeout |
| `max_retry` / `timeout_seconds` | `3` / `30` | Framework routing parameters |
| `memory_enabled`, `hitl.enabled`, `hitl.max_hitl` | `true`, `true`, `8` | Checkpointing and the review loop |

## Input

`POST /invoke` takes `input` and an optional `session_id`. `input` is either a JSON request object
or a plain-language request:

```json
{"regulators": ["FSA", "Basel"], "domains": ["AI MRM", "Basel IV"],
 "institution_type": "mega-bank", "output_format": "detailed"}
```

`output_format` is `brief` or `detailed`. Regulator names are validated against the configured
registry, so an authority you have not added is rejected rather than silently ignored.

Callers must be authenticated. The entry adapter uses a verified middleware trust level when
present; otherwise, if `INVOKE_AUTH_TOKEN` is set on the server environment, a matching
`Authorization: Bearer` header promotes the caller to `VERIFIED_EXTERNAL`. A caller that is
neither middleware-verified nor token-authenticated is rejected with **401**.

```bash
# 1) research request — comes back suspended, waiting for review
curl -X POST http://localhost:8000/invoke \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{
    "input": "{\"regulators\": [\"FSA\", \"Basel\"], \"domains\": [\"AI MRM\"], \"institution_type\": \"mega-bank\", \"output_format\": \"brief\"}",
    "session_id": "demo-001"
  }'
# → {"status": "awaiting_human", "thread_id": "...", "hitl_metadata": {…draft…}}
```

Review happens on a separate route. The `thread_id` from the suspend response must be echoed back,
and the same `session_id` must be supplied — the thread is ownership-checked:

```bash
# 2a) approve and finalise
curl -X POST http://localhost:8000/resume \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{"thread_id": "<thread_id>", "session_id": "demo-001",
       "feedback": {"action": "approve"}}'

# 2b) ask for more research — the merged draft comes back for review again
curl -X POST http://localhost:8000/resume \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{"thread_id": "<thread_id>", "session_id": "demo-001",
       "feedback": {"action": "correct", "additional_regulators": ["BOJ"],
                    "instructions": "…"}}'
```

The refine loop is human-gated — every round needs an explicit `correct` — and each round costs
another model call. Resume fails closed: an unknown thread is **404**, a thread owned by another
session or caller is **403**, and a thread that is not awaiting input, including one already
approved, is **409**.

## Output

The finalised report is Japanese Markdown, structured as priority sections followed by a
per-institution impact summary and recommended next steps:

```
# コンプライアンス影響レポート
## P1 — 即時対応（期限あり）
- **<publication title>** [FSA]（期限: 2026-07-20）
  - 影響: … / 推奨アクション: … / 出典: https://www.fsa.go.jp/…
## P2 — 30日以内レビュー
## P3 — 参考情報
## 機関別影響サマリ
## 推奨次ステップ
```

Every item carries a source URL; an item without one does not reach the output. Regulators that
could not be fetched are listed explicitly in the report. Values above are illustrative; real
output depends on what the authorities have published.

## Security and Limitations

Every node requires verified-external trust, so the framework's trust gate stops an insufficiently
trusted caller before any node body runs — the adapter's 401 is the first of two independent
checks. API keys are resolved through the agent's secret provider, never from the process
environment, and are never written into state. The output gate blocks a report carrying a
confidentiality marker and removes categorical legal-conclusion phrasing.

Known limits, stated plainly:

- **This is research, not legal advice.** The report organises published material and flags what
  looks time-critical. Deciding what to do requires reading the primary source and consulting
  someone qualified.
- **It is on demand, not continuous.** There is no monitoring mode and no alerting; the agent runs
  when asked.
- **Only public material.** The agent reads published pages. Unpublished or internal regulatory
  material is out of scope, and content marked confidential is blocked on the way out.
- **Fetching is best-effort.** A regulator site that is down, slow past the timeout, or
  restructured yields a "not retrieved" entry rather than a silent gap — but the report is then
  incomplete, and it says so.
- The report body is Japanese.

Output is machine-generated and assistive. It does not replace expert review, and every finding
should be checked against the authority's own publication before it is acted on.

## Release

- Version: 1.0.0
- Category: fixed multi-step domain workflow (Cat 2), document-generation pattern
- Generation mode: `llm`
- HITL: **enabled and mandatory** (`hitl.max_hitl: 8`)
- Persistence: checkpointer required; the standalone server uses an in-memory saver
- Required trust level: `VERIFIED_EXTERNAL`
- Default regulators: FSA, BOJ, Basel (BIS), SEC, APRA

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent resolves its model credential from the platform's secret provider at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Supplied by the platform's own package registry at build time. It is not published on public PyPI, so `pip install -e .` alone does not fetch it. The `anthropic` extra is required for the default provider |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run on AGENTIC STAR, and this agent genuinely refuses to start
without it. The standalone server builds its language-model client at import time, so
`uvicorn src.api.server:app` raises before the application object exists: `ModuleNotFoundError`
when the provider extra is absent, and — once it is installed — `MissingSecret` for
`ANTHROPIC_API_KEY`, naming the secret-store paths it looked in. Refusing to start is deliberate:
a compliance report produced by a half-wired agent is worse than no report. The test suite runs
without a platform connection, using a fake language model and a stubbed source fetcher, so no
test reaches a regulator site or a model endpoint.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## Documentation

`docs/02_design.md` carries the node flow, the state schema, the source registry design, the HITL
refine loop and the output-gate design. `docs/03_test_spec.md` records the test specification and
the verification results.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
