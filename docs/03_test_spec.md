# Test Specification — FIN-C2-113 FinRegulationResearchComplianceReportAgent

## Test Strategy
- Coverage target: 主要ノード・サービス・E2E フロー全経路（unit 5 ファイル + integration 1 ファイル + PB 5 ファイル）
- Test types: Unit / Integration / Proof-of-Boundary
- LLM・外部 HTTP は全テストで fake 注入（`tests/conftest.py` の `FakeLLM` / `RecordingFetcher`）— ネットワーク・実 LLM 非依存

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass（外側 `State` / 内側 `WorkflowState` とも）— `tests/proof_of_boundary/test_state_safety.py` | ✅ PASS |
| TC-02 | SecurityViolationError fires on invalid input | 空入力 / 不正当局名 / サイズ超過 → status=error — `test_input_parse_node.py` | ✅ PASS |
| TC-03 | No JWT/Credential in State | CI の資格情報スキャン: 0 violations | ✅ PASS (CI) |
| TC-04 | InvocationContext via configurable only | State に InvocationContext 非格納（PB-2/PB-5 で検査） | ✅ PASS |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start`/`node_complete`/`node_error` は execute 内に無し（PB-1 で framework 発火のみ確認） | ✅ 0 duplicates |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode` subclass) | `@final` — 全 FunctionNode は `_extra_security_gate_input()` のみ | ✅ 0 overrides |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode` subclass) | `@final` — 全 FunctionNode は `_extra_security_gate_output()` のみ | ✅ 0 overrides |
| TC-08 | `required_trust_level` enforced | ANONYMOUS caller → S-1 拒否（`test_graph_e2e.py::test_anonymous_caller_denied_by_s1`）。PB-6 の `test_s1_denial_refuses_execution_before_execute` が `execute()` が呼ばれなかったことを証明する | ✅ PASS |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial | InputParseNode: 入力サイズ上限（20,000 chars）で拒否 — `test_s2_input_size_ceiling` | ✅ PASS |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial | ResponseValidateNode: 機密マーカー遮断 + 断定的法的助言除去 — `test_s3_blocks_confidential_marker` / `test_s3_strips_definitive_legal_advice` | ✅ PASS |
| TC-11 | S-4: at least one domain `emit_trace_event()` inside each `execute()` | 全 11 ノードのドメインイベントを PB-1 で網羅検証（main slot `RegulationResearchReportNode` を含む） | ✅ ≥1 per node |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → AuditLogger | `test_pb_audit_events.py` — invoke→suspend→resume の全周期で全 11 ドメインイベント + framework envelope 発火 | No silent failures | ✅ PASS |
| PB-2 | State serialization | `test_state_safety.py` — 出力 State が primitives のみ | No Pydantic/dataclass | ✅ PASS |
| PB-3 | L1 → External service | `RegulatorSourceService` 経由の当局公表物ライブ取得（テストは fake fetcher・実接続は STG invoke で確認） | Data retrieved | ✅ PASS (fake) / STG で実確認 |
| PB-4 | Import isolation | `test_import_isolation.py` — Level 0 import 0 件 | AST scan: 0 violations | ✅ PASS |
| PB-5 | Checkpoint safety | `test_pb_checkpoint_safety.py` — 外側・内側の**全 checkpoint** に credential 形状・非 primitive なし（HITL suspend 含む） | Inspection pass | ✅ PASS |
| PB-6 | Invoke execution order | `test_pb_invoke_order.py` — S-1 → node_start → S-2 → execute → S-3 → node_complete | Order verified | ✅ PASS |
| PB-7 | HITL interrupt propagation | `test_pb_hitl_resume.py` — ①interrupt が `GraphInterrupt` として error boundary に握り潰されず status=awaiting_human ②**resume → SUCCESS 到達**（subgraph キャッシュ不備の再 interrupt 無限ループを検出）③多段 interrupt → approve → SUCCESS | GraphInterrupt reaches engine; resume terminates | ✅ PASS |

## Business Logic Tests

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | JSON 依頼の解釈 | regulators/domains/institution_type/output_format 指定 JSON | request_params に忠実に反映 | ✅ PASS |
| BL-02 | 自然文依頼のフォールバック解釈 | 「金融庁とバーゼルの…メガバンク向けに詳細で」 | FSA+Basel / mega-bank / detailed | ✅ PASS |
| BL-03 | 部分的失敗の継続（データ化） | BOJ のみ取得失敗 | status=success 継続・fetch_errors に BOJ 記録・レビュー到達 | ✅ PASS |
| BL-04 | 全当局取得失敗 | 全ホスト failure | status=error | ✅ PASS |
| BL-05 | 期限スコアリング境界 | 期限 18 日後 / 75 日後 / >90 日 / なし / 不正形式 | p1 / p2 / p3 / p3 / p3 | ✅ PASS |
| BL-06 | 機関種別フィルタ | regional-bank × FSA/SEC/APRA レコード | FSA のみ残存（unspecified は全件） | ✅ PASS |
| BL-07 | 影響分類の件数整合 | レコード 2 件 vs 分類 1 件 | status=error（件数不一致検出） | ✅ PASS |
| BL-08 | 引用必須検証（S-3） | citation_url 空の P1 エントリ | status=error（citation-mandatory） | ✅ PASS |
| BL-09 | HITL: approve | ドラフト提示 → `{"action":"approve"}` | SUCCESS・整形レポート・5 ノード履歴 | ✅ PASS |
| BL-10 | HITL: reject | `{"action":"reject"}` | 成果物なしで終了（rejected は finalize 直行） | ✅ PASS |
| BL-11 | HITL: refine ループ | `{"action":"correct","additional_regulators":["BOJ"]}` → approve | 増分フェッチ（BOJ のみ）・前回ドラフト統合再合成・2 回目 interrupt → SUCCESS | ✅ PASS |
| BL-12 | refine ループの人間ゲート性（上限なし） | correct 連続 4 回 → approve | 毎回再 interrupt（refine_round 逓増）・approve で SUCCESS（2026-07-02 レビュー決定: テンプレ側上限は設けない） | ✅ PASS |
| BL-13 | hitl_count 非書込 | 全 feedback 経路 | テンプレは hitl_count を書かない（backbone 専用） | ✅ PASS |
| BL-14 | ソースレジストリ拡張 | config sources で FSA 上書き + ESMA 追加 | デフォルトへマージ（コード変更不要） | ✅ PASS |
| BL-15 | 拡張当局の依頼受理（allowlist は config 由来） | known_regulators に ESMA を含む構成で ESMA 依頼 | 受理・request_params に反映（レビュー指摘 F-01 対応） | ✅ PASS |
| BL-16 | fetch_errors の自己修復 | round 1 で BOJ 失敗 → retry 成功 / 再失敗 | 成功時は stale エラー除去・再失敗時は最新 1 件のみ（レビュー指摘 F-02 対応） | ✅ PASS |
| BL-17 | LLM ビルダー | 未知 provider / JSON パース各形態 | ValueError / fence・prose 混在の JSON 復元（レビュー指摘 F-03 対応・API キーは SecretProvider 経由） | ✅ PASS |
| BL-18 | S-3 パターンデータの config 化 | config で遮断マーカー・除去フレーズを差し替え | config 提供リストが有効化（コード変更なしで調整可・2026-07-02 レビュー決定） | ✅ PASS |

## Test Execution Summary
- Execution date: 2026-07-02
- Total tests: 79
- Pass: 79 / Fail: 0 / Skip: 0
- 実行環境: Python 3.11.13 / agenticstar-agentcore==1.0.0 / pytest 8.1.1（ローカル venv・fake LLM/fetcher）
- lint: `ruff check src/ tests/` — All checks passed
