# External Analysis Phase 1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 承認済み設計どおり、外部通信も結果更新も行わない決定的なPhase 1 dry-run CLIを追加する。

**Architecture:** `external_analysis.py`が設定、厳格な入力・2.0 pending結果照合、immutable request、provider境界、計画整形を担う。`run_external_analysis.py`は環境変数と標準入出力を扱う薄いCLIとし、既存`analyze_market.py`の入力検証を再利用する。

**Tech Stack:** Python 3.13、標準ライブラリ、既存`unittest`。依存追加なし。

## Global Constraints

- Phase 1はOpenAI、Brave Search、URL取得、DNS、SSRF、APIキー読込を行わない。
- analysis result、data内ファイル、一時ファイル、log、lock、cacheを作成・変更しない。
- `SCHEMA_VERSION = "2.0"`、pending固定4キー、analysis input固定14キーを変更しない。
- `fetch_markets.py`、`select_candidates.py`、`prepare_analysis_input.py`、設計書、依存関係、実データ、GitHub Actionsを変更しない。

---

### Task 1: 設定契約

**Files:**
- Create: `external_analysis.py`
- Test: `tests/test_external_analysis.py`

**Interfaces:**
- Produces: `ExternalAnalysisConfig`, `ConfigurationError`, `PhaseNotAvailableError`, `load_config(env: Mapping[str, str])`。

- [x] 設定既定値、厳格な許可値、境界値、段階未到達を表す失敗テストを書く。
- [x] `python -m unittest tests.test_external_analysis.ConfigTests -v`でfeature未実装による失敗を確認する。
- [x] frozen dataclassとmapping注入の最小実装を追加する。
- [x] 同じ対象テストを再実行して成功を確認する。

### Task 2: 入力・結果契約と市場選択

**Files:**
- Modify: `external_analysis.py`
- Modify: `tests/test_external_analysis.py`

**Interfaces:**
- Consumes: `analyze_market.find_latest_analysis_input`, `output_path_for`, `load_analysis_inputs`, `load_existing_pending_results`, `validate_existing_pending_results`。
- Produces: `load_phase1_snapshot(data_dir)`, `select_pending_markets(records, limit)`。

- [x] 最新入力、同suffix結果、2.0 pending限定、照合不正、0/1/3/10件選択の失敗テストを書く。
- [x] 対象テストで期待した失敗を確認する。
- [x] 既存検証を再利用し、1.0を追加拒否する最小実装を追加する。
- [x] 対象テストを再実行して成功を確認する。

### Task 3: Request・provider境界・dry-run計画

**Files:**
- Modify: `external_analysis.py`
- Modify: `tests/test_external_analysis.py`

**Interfaces:**
- Produces: frozen `AnalysisRequest`（固定15field）、`ProviderDryRunResult`、`AnalysisProvider`, `FakeAnalysisProvider`, `build_requests`, `format_dry_run`。

- [x] 全15field、Decimal、改行、空文字、immutability、fake固定応答、固定stdoutの失敗テストを書く。
- [x] 対象テストで期待した失敗を確認する。
- [x] providerを通常dry-runから呼ばない最小実装を追加する。
- [x] 対象テストを再実行して成功を確認する。

### Task 4: CLIと不変性

**Files:**
- Create: `run_external_analysis.py`
- Create: `tests/test_run_external_analysis.py`

**Interfaces:**
- Consumes: `external_analysis.run_phase1(data_dir, env)`。
- Produces: `main() -> int`、終了コード0/1/2/3、日本語stderr。

- [x] 正常、設定不正、契約不正、段階未到達、0件、stdout決定性、data SHA-256不変、ネットワーク未使用の失敗テストを書く。
- [x] `python -m unittest tests.test_run_external_analysis -v`で期待した失敗を確認する。
- [x] tracebackやファイル書込を行わない薄いCLIを追加する。
- [x] 対象テストを再実行して成功を確認する。

### Task 5: 文書と全体検証

**Files:**
- Modify: `README.md`
- Modify: `plan.md`

**Interfaces:**
- Documents: Phase 1の4環境変数、実行方法、上限、禁止範囲、検証結果。

- [x] READMEへdry-run専用の利用契約を追記する。
- [x] plan.mdへ完了範囲、検証結果、Phase 2未着手を記録する。
- [x] 新規対象テストと全テストを実行する。
- [x] Python全ファイルを`py_compile`し、`git diff --check`を実行する。
- [x] 隔離dataでdefault 1、max 3、max 10、stdout SHA-256一致、data SHA-256不変、生成物なしを検証する。
- [ ] 変更範囲を自己レビューし、commit、push、Draft PR作成後にmain未統合で停止する。
