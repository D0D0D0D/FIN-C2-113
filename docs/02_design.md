# Template Design Specification — FIN-C2-113 FinRegulationResearchComplianceReportAgent

## Position in AgentCore Architecture

- **Agent Class**: Graph
- **L1 Base**: AgentBaseGraph（Cat 2 — 外側 5 ノード backbone。main スロットに `GraphNode` を置き、内側ドメインワークフローをカプセル化する）
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution)

## Architecture Overview

外側は L1 固定 5 ノード backbone。ドメインの複雑さ（内側 8 ステップ: ステップ 0 ParamsHydrate 〜 ステップ 7 ReportDraftReview[HITL]）は main スロットの
`RegulationResearchReportNode`（`GraphNode` 継承）が内包する内側グラフ
`RegulationResearchReportWorkflow`（`src/graph/domain_workflow_graph.py`）に閉じる。

### Node Configuration

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema_version / session_id / trust_level 設定 | — | 初期化済み State | InitializeNode (default) |
| pre_process | **InputParse**: 依頼文から規制ドメイン・対象当局・機関種別・出力形式を解釈し `request_params` を構築。入力バリデーション（S-2 extra hook: 入力サイズ上限・不正当局名の拒否） | `user_input` | `request_params`, `validated_input`, `status` | FunctionNode → `InputParseNode` |
| main | **RegulationResearchReportNode (GraphNode)**: 内側ワークフロー（下記）を実行し、優先度付きコンプラレポートを生成。内側 `ReportDraftReview` の HITL interrupt（ドラフト提示 → 承認 / 追加調査）を伝播。上流 `status="error"` はパススルー | `request_params` | `compliance_report`, `citations`, `status`, `hitl_draft`/`hitl_feedback`/`hitl_status`（伝播） | GraphNode → 内側 `RegulationResearchReportWorkflow` |
| post_process | **ResponseValidate**: S-3 extra hook で未公表・機密資料パターンの混入遮断・引用（出典 URL）必須検証・断定的法的助言表現の除去。`result` へ整形 | `compliance_report`, `citations` | `result`, `status` | FunctionNode → `ResponseValidateNode` |
| finalize | 出力射影（`result` → `output`） | `result` | `output` | FinalizeNode (default) |

### 内側ワークフロー（RegulationResearchReportWorkflow — src/graph/domain_workflow_graph.py）

| 論理ステップ | Node | 責務 |
|---|---|---|
| 0 | ParamsHydrate | `extract_input()` が直列化した JSON をパースし、内側 State の構造化パラメータへ復元（復元失敗は `status="error"`） |
| 1 | LiveSourceFetch | 対象当局（FSA / BOJ / Basel / SEC / APRA）のキュレーション済みソースレジストリから公表物をオンデマンドでライブ取得（RAG KB は保持しない）。当局単位の取得失敗は `fetch_errors` にデータとして記録し継続。**refine ループ 2 周目以降は追加スコープ分のみ取得**（取得済み文書は内側 State に保持） |
| 2 | ResultNormalize | 機関ごとに異なる公表物を共通スキーマ（発行者・発行日・対応期限・対象領域・原文 URL）へ正規化 |
| 3 | InstitutionFilter | 機関種別（メガバンク / 地銀 / 証券 / 保険）に該当する規制レコードへ絞り込み |
| 4 | DeadlineScore | 対応期限からの残日数で緊急度スコアリング（P1 / P2 / P3 の一次判定材料） |
| 5 | ComplianceImpactClassify | LLM により機関種別へのコンプライアンス影響を分類（影響領域タグ・要対応アクション種別） |
| 6 | PriorityReportSynthesize | LLM により P1（期限付き即時対応）/ P2（30日以内レビュー）/ P3（FYI）+ 機関別影響 + 推奨次アクション + 引用を合成。refine 周回では前回ドラフトと追加調査結果を**統合した**レポートを再合成 |
| 7 | ReportDraftReview（**HITL・D6**） | ドラフトレポートを `interrupt({draft, fetch_errors, reason})` で人間に提示。フィードバックが承認 → END へ。**追加調査依頼**（例:「APPI 関連も調べて」）→ 追加ドメイン/当局を内側 State のパラメータへマージし **LiveSourceFetch へ分岐で戻る**（refine ループ）。`hitl_draft` に直前ドラフトを保持（resume 時の LLM 再実行防止） |

- 内側グラフの分岐関数は **自 State schema を注釈**する（`AgentState` 注釈は LangGraph の射影でカスタム欄が落ち誤 route する — Business Travel Hotel & Plan Recommendation Agent の実機知見）。
- **入力の受け渡し（GraphNode 契約準拠）**: `RegulationResearchReportNode` は `extract_input()` をオーバーライドし、外側 State の `request_params` を `json.dumps(..., default=str)` で直列化して内側へ渡す（内側は `user_input` + ctx のみ受領のため）。内側グラフの**先頭ノード `ParamsHydrate`** が JSON をパースし、内側 State schema（`WorkflowState`）の構造化フィールド（`regulators` / `domains` / `institution_type` / `output_format`）へ復元してから論理ステップ 1 以降が実行される。復元失敗（不正 JSON・必須キー欠落）は内側で `status="error"` とする。
- **出力の受け渡し**: `merge_output()` は内側 State から `compliance_report` / `citations` / `status` **のみ**を外側 State の差分として返す（変更キーのみ返却・full state 返却禁止）。内側の中間フィールドは外側へ持ち出さない。

### Data Flow

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (retry)
                                          pre_process

main (GraphNode) 内部:
  ParamsHydrate → LiveSourceFetch → ResultNormalize → InstitutionFilter
    → DeadlineScore → ComplianceImpactClassify → PriorityReportSynthesize
    → ReportDraftReview (HITL interrupt)
         ├─ 承認 → END（compliance_report 確定）
         └─ 追加調査依頼 → パラメータ拡張 → LiveSourceFetch へ戻る（refine ループ・人間ゲート型で上限なし）
```

### State Definition

State schema は**外側と内側で分離**する（Cat 2 GraphNode 契約 — 内側の中間データは subgraph 内にカプセル化し、外側 State / checkpoint を肥大させない）。

**外側 State（`src/schemas/state.py` — AgentState 継承 flat TypedDict）**

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `request_params` | `dict` | 解釈済み依頼パラメータ（domains / regulators / institution_type / output_format） | ✅ |
| `compliance_report` | `dict` | P1/P2/P3 構造のレポート本体（JSON-serializable・`merge_output()` が内側から昇格） | ✅ |
| `citations` | `list[dict]` | 出典（当局名・文書名・URL・取得日時・`merge_output()` が内側から昇格） | ✅ |
| `result` | `str` | 検証済み最終レポート（brief / detailed 形式） | ✅ |

**内側 State（`src/schemas/workflow_state.py` — 内側グラフ専用 flat TypedDict。外側へは持ち出さない）**

| Field | Type | Purpose |
|-------|------|---------|
| `regulators` / `domains` / `institution_type` / `output_format` | `list[str]` / `list[str]` / `str` / `str` | ParamsHydrate が復元した構造化パラメータ |
| `fetched_documents` | `list[dict]` | ライブ取得した公表物 |
| `fetch_errors` | `list[dict]` | 当局別の取得失敗記録（regulator / reason）— 部分的失敗の明示化用データ |
| `normalized_records` | `list[dict]` | 共通スキーマ正規化済みレコード |
| `filtered_records` | `list[dict]` | 機関種別フィルタ済みレコード |
| `scored_records` | `list[dict]` | 期限スコア付きレコード |
| `classified_records` | `list[dict]` | 影響分類タグ付きレコード |
| `refine_rounds` | `int` | HITL refine ループの周回数（増分フェッチ判定・監査用カウンタ。上限には使わない） |
| `compliance_report` / `citations` | `dict` / `list[dict]` | 合成結果（`merge_output()` で外側へ昇格する 2 キー） |

HITL フィールド（`hitl_draft` / `hitl_feedback` / `hitl_status` 等・schema 1.4 の 7 フィールド）はフレームワーク定義を継承し、テンプレが書くのは `hitl_draft` のみ。`merge_output()` は `compliance_report` / `citations` / `status` に加えて `hitl_draft` / `hitl_feedback` / `hitl_status` を外側へ伝播する。

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, permissions, credential handle)
- [ ] ConnectionPolicy (retry/timeout strategy) — 外部 HTTP 取得は service 層で timeout / retry を自前管理
- [x] SecurityViolationError
- [x] S-2: `_extra_security_gate_input()` — `InputParseNode` に実装。入力サイズ上限・対象当局 enum 外の値の拒否・依頼文への機密識別子混入チェック
      (**MUST NOT override `_security_gate_input()`** — `TypeError` at class definition)
- [x] S-3: `_extra_security_gate_output()` — `ResponseValidateNode` に実装。①未公表/機密資料パターンの混入遮断（content filtering）②全レポート項目の引用必須検証（出典 URL の無い規制記述を拒否）③断定的法的助言の表現除去。**遮断マーカー・除去フレーズのパターンデータは `config/config.yaml`（`s3_confidential_markers` / `s3_legal_advice_phrases`）で提供**し、テンプレ利用者がコード変更なしで調整できる（ゲートのロジック自体はコード側・2026-07-02 レビュー決定）
      (**MUST NOT override `_security_gate_output()`** — `TypeError` at class definition)
- [x] S-4: `emit_trace_event()` — 全ノードの `execute()` 内で最低 1 ドメインイベント（`regulation_fetch_completed` / `records_normalized` / `report_synthesized` / `report_validated` 等）
      (do NOT emit `node_start` / `node_complete` / `node_error` — `BaseNode.__call__()` emits these automatically)

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass → framework `@final` gate always runs automatically;
>   extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only
> - `GraphNode`（main スロット）→ deliberate no-op（外側 pre_process の S-2 / post_process の S-3 が上流・下流でゲートを担う）
> - Custom `BaseNode` subclass → 本テンプレでは使用しない

### LLM / 外部サービス

| 依存 | 実装 | 備考 |
|---|---|---|
| LLM | `shared.services.llm.BaseLLM` をコンストラクタ注入（`src/services/llm_client.py` を注入シームとする） | テストは fake BaseLLM 注入 |
| 規制ソース取得 | `src/services/regulator_source_service.py` — 当局別キュレーション済みソースレジストリ + HTTP ライブ取得。**5 当局（FSA / BOJ / Basel / SEC / APRA）の公式公表物 URL をデフォルト config に同梱**し、ユーザーは config でソースを追加・上書きできる（コード変更不要） | timeout / 失敗時は該当当局を欠損としてレポートに明示（silent skip しない）。ソース一覧と追加手順はユーザー向け README に記載する |
| Secrets | 取得系 API キーが必要な場合は `ctx.secrets` 経由（`os.environ` 禁止）。State には格納しない | `agent.yaml` の `requires.secrets` に宣言 |

### Composition Pattern

- **Pattern**: GraphNode (subgraph)
- **Composition target**: `RegulationResearchReportWorkflow`（同一リポジトリ内の内側グラフ・`src/graph/domain_workflow_graph.py`）
- **Error propagation strategy**: **propagate** — ただし「エラー」と「部分的失敗」を明確に区別する。
  - **部分的失敗（一部当局の取得失敗）は例外にしない**: 当局別 HTTP 取得は `regulator_source_service` 内で当局単位に try/except し、失敗を**例外でなくデータ**（内側 State の `fetch_errors`）として記録して継続する。トランスポート例外は service 層の外へ一切伝播しないため、propagate 戦略下でもワークフローは中断しない。レポート合成時に `fetch_errors` から「取得できなかった当局」を明示する（silent skip しない）。
  - **真の失敗（全当局取得失敗・ParamsHydrate 復元失敗・LLM 呼び出し失敗）は propagate**: 内側ノードが `status="error"` + `error_log` を設定し、`merge_output()` 経由で外側 State へ伝播 → 外側 route が finalize へ送る。
  - `GraphNode.execute` は上流 `status="error"` を必ずパススルーする（pre→main は無条件辺のため）。

### HITL（D6 パターン・2026-07-02 レビューで採用決定）

レポート受領後に「追加でこれも調べて」という追い調査依頼が想定されるため（レビュー項目 7・ユーザー判断）、**D6 backbone `interrupt()` パターン**を採用する。

- **トリガー位置**: 内側ワークフローの `ReportDraftReview` ノード（PriorityReportSynthesize の直後）。ドラフトレポート + 取得欠損（`fetch_errors`）を `interrupt()` で提示する。
- **フィードバック処理**:
  - 承認（approve）→ 内側 END へ進み `compliance_report` を確定
  - 追加調査依頼（refine: 追加ドメイン / 追加当局 / 追記指示）→ 内側 State のパラメータへマージし LiveSourceFetch へ分岐で戻る。再合成は前回ドラフトと統合
- **ループ上限なし（2026-07-02 レビュー決定）**: refine は毎周「人間が明示的に correct を送信」しないと回らない人間ゲート型のため、テンプレ側の回数上限は設けない（上限超過で勝手に確定する挙動の方がレビューアの意図に反し有害）。`refine_rounds` は増分フェッチ判定と監査用のカウンタとしてのみ保持。⚠️ `hitl_count` は backbone 専用フィールドでありテンプレは書き込まない（Cat 2 に max_hitl ブレーカーは無い — フレームワーク実機確認済み）。`hitl_draft` のみテンプレが書く（resume 時の LLM 再実行防止・schema 1.4 許可フィールド）。
- **config 要件**（`config/config.yaml`）:
  ```yaml
  memory_enabled: true   # MANDATORY — checkpointer required for pause/resume
  hitl:
    enabled: true
    max_hitl: 8
  # refine ループのテンプレ側上限は無し（人間ゲート型）
  ```
- **実装上の必須事項**（Cat 2 HITL 実機知見 — TourPlanningProcurementRoutingAgent で確立）:
  1. **明示 compile(checkpointer=)** — standalone adapter と内側グラフは、**両グラフに明示的に checkpointer を渡して compile** する（欠くと interrupt で RuntimeError）
  2. **`get_subgraph()` は内側グラフをキャッシュ**（`self._subgraph`）— 毎回新規生成すると resume が suspend checkpoint を見失い、最初から再実行 → 再 interrupt の無限ループになる
  3. **`merge_output()` に `hitl_draft` / `hitl_feedback` / `hitl_status` を含める**（HITL シグナルの外側伝播）
  4. **PB-7 テスト**: `interrupt()` が `GraphInterrupt` として `BaseNode.__call__()` を通過しエラー境界に握り潰されないこと + **resume → success 到達**まで assert（suspend 確認だけでは subgraph キャッシュ不備を検出できない）

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: framework/ and shared/ only (no agents/base/ required)

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | 固定多段パイプラインで自律ループ不要（Cat 2）。 |
| Composition pattern | main に FunctionNode（内側 8 ステップを 1 ノードに集約） | main に GraphNode（内側ワークフローへ分離） | **GraphNode** | 内側 8 ステップの責務分離・ステップ単位の監査イベント・内側ノード単体テスト容易性・HITL refine ループ（ReportDraftReview → LiveSourceFetch の分岐）の実現。Cat 2 標準構成（graph_cat2_sample.py）に準拠（2026-07-02 レビューで代替案 B/C/D と比較のうえ確定）。 |
| 規制文書の保持 | RAG KB を構築し検索 | 依頼ごとにライブ取得 | **ライブ取得** | 再フレーム（2026-06-15）で確定。流動する公開規制文書の KB 保守負債を回避し、常に最新公表物を参照する。 |
| 取得失敗時の挙動 | 即エラー終了 | 部分結果で継続 + 欠損明示 | **部分結果で継続** | コンプラ担当者には「どの当局が確認できなかったか」の明示が実務上の価値。silent skip はしない。 |
| レポート引用 | 任意 | 必須（S-3 で検証） | **必須** | 規制対応は原文確認が前提。出典の無い規制記述は S-3 で拒否する。 |
| ソースレジストリ提供形態 | コード内ハードコード | デフォルト config 同梱 + ユーザー追加・上書き可 | **config 同梱 + ユーザー拡張可** | 当局公式サイトの公表物 URL は変動が少なくデフォルト提供が実用的。変更・追加は config のみで対応（コード変更不要）。ソース一覧・追加手順はユーザー向け README（docs/0A_release_note.md）に記載（2026-07-02 レビュー確認）。 |
| HITL | なし（単発 invoke・追い調査は再 invoke） | D6 `interrupt()`（ドラフト提示 → 追加調査ループ） | **D6 採用** | レポート受領後の追加調査依頼が現実的な利用形態（2026-07-02 レビュー項目 7・ユーザー判断）。固定 backbone では追い調査ループは main 内でしか実現できず、後付けは State/graph の手戻りが大きいため初期設計で採用。実装は TourPlanningProcurementRoutingAgent 確立レシピを踏襲。 |
