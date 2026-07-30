# Candidate Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 最新の市場スナップショットから固定条件と分散規則で最大10件を選び、同じ入力からバイト単位で同じ候補CSVを生成する。

**Architecture:** `select_candidates.py` を既存収集機から独立した標準ライブラリのみのCLIとして追加する。入力検証、正規化・分類、適格判定・安定選択、CSV入出力を純粋関数に分け、`run()` と `main()` がそれらを接続する。

**Tech Stack:** Python 3.10以上、標準ライブラリ（`csv`, `datetime`, `decimal`, `pathlib`, `re`, `urllib.parse`, `unittest`）

## Global Constraints

- `fetch_markets.py` と元の `data/markets_*.csv` は変更しない。
- AI、外部ニュース検索、外部API通信、売買、認証処理を追加しない。
- 依存ライブラリを追加しない。
- YES価格0.10〜0.90、締切7〜90日の条件は緩和しない。
- 締切までの日数は各行の `取得日時` 基準で計算する。
- 候補0件はヘッダーのみを出力し、`候補0件` を表示して終了コード0とする。
- 入力不正時は候補CSVを作成または上書きせず、終了コード1とする。
- 出力はUTF-8 BOM付き、固定列順、固定改行でバイト単位の決定性を保つ。
- キーワードとカテゴリ優先順位は `CATEGORY_RULES` の一箇所に定義する。
- 詳細仕様は `docs/superpowers/specs/2026-07-30-candidate-selector-design.md` を正本とする。

---

### Task 1: 入力値、URL、カテゴリの決定的な正規化

**Files:**
- Create: `select_candidates.py`
- Create: `tests/test_select_candidates.py`

**Interfaces:**
- Produces: `parse_iso_datetime(value: str) -> datetime`
- Produces: `normalize_theme_url(value: str, market_id: str) -> str`
- Produces: `categorize_market(question: str, normalized_theme: str) -> str`
- Produces: `CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...]`

- [ ] **Step 1: URL正規化とフォールバックの失敗テストを書く**

```python
class NormalizationTests(unittest.TestCase):
    def test_normalizes_theme_url(self):
        self.assertEqual(
            "url:https://polymarket.com/Event/Alpha",
            select_candidates.normalize_theme_url(
                "HTTPS://PolyMarket.COM/Event/Alpha///?x=1#part", "42"
            ),
        )

    def test_invalid_or_missing_url_falls_back_to_market_id(self):
        for value in ("", "relative/path", "ftp://example.com/a", "https://x:bad/a"):
            with self.subTest(value=value):
                self.assertEqual(
                    "market:42",
                    select_candidates.normalize_theme_url(value, "42"),
                )
```

- [ ] **Step 2: カテゴリ優先順位と単語境界の失敗テストを書く**

```python
class CategoryTests(unittest.TestCase):
    def test_uses_first_matching_category_rule(self):
        self.assertEqual(
            "政治",
            select_candidates.categorize_market(
                "Will the presidential election affect Bitcoin?",
                "url:https://polymarket.com/event/example",
            ),
        )

    def test_does_not_match_ai_inside_another_word(self):
        self.assertEqual(
            "その他",
            select_candidates.categorize_market(
                "Will it be said again?",
                "url:https://polymarket.com/event/example",
            ),
        )
```

- [ ] **Step 3: 対象テストを実行し、モジュール未作成で失敗することを確認する**

Run: `python -m unittest tests.test_select_candidates.NormalizationTests tests.test_select_candidates.CategoryTests -v`

Expected: `ModuleNotFoundError: No module named 'select_candidates'`

- [ ] **Step 4: 最小実装を追加する**

```python
CATEGORY_RULES = (
    ("政治", ("election", "president", "presidential", "nominee", "nomination",
             "congress", "senate", "governor", "prime minister", "parliament", "vote")),
    ("国際情勢", ("war", "invasion", "invade", "ceasefire", "iran", "israel",
                 "ukraine", "russia", "china", "taiwan", "nato", "greenland")),
    ("暗号資産", ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "token")),
    ("経済・金融", ("federal reserve", "fed", "interest rate", "inflation",
                  "recession", "gdp", "unemployment", "stock", "s&p", "nasdaq",
                  "market cap")),
    ("テクノロジー", ("artificial intelligence", "openai", "spacex", "tesla",
                    "iphone", "apple", "google", "microsoft", "ai")),
    ("エンタメ", ("album", "movie", "film", "box office", "gta", "game",
                 "oscar", "grammy", "music")),
    ("科学・健康", ("nasa", "alien", "vaccine", "disease", "covid", "health",
                  "drug", "medicine")),
)

def normalize_theme_url(value: str, market_id: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("invalid market URL")
        host = parsed.hostname.casefold()
        if ":" in host:
            host = f"[{host}]"
        netloc = host if parsed.port is None else f"{host}:{parsed.port}"
        normalized = urlunsplit(
            (parsed.scheme.casefold(), netloc, parsed.path.rstrip("/"), "", "")
        )
        return f"url:{normalized}"
    except (TypeError, ValueError):
        return f"market:{market_id}"

def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in keyword.split()]
    return re.compile(r"(?<![0-9a-z])" + r"\s+".join(parts) + r"(?![0-9a-z])")

def categorize_market(question: str, normalized_theme: str) -> str:
    target = f"{question} {normalized_theme}".casefold()
    for category, keywords in CATEGORY_RULES:
        if any(_keyword_pattern(keyword).search(target) for keyword in keywords):
            return category
    return "その他"
```

- [ ] **Step 5: 対象テストを再実行して成功を確認する**

Run: `python -m unittest tests.test_select_candidates.NormalizationTests tests.test_select_candidates.CategoryTests -v`

Expected: 全テスト `ok`

- [ ] **Step 6: Task 1をコミットする**

```powershell
git add select_candidates.py tests/test_select_candidates.py
git commit -m "feat: add candidate normalization rules"
```

---

### Task 2: 取得日時基準の適格判定と安定した分散選択

**Files:**
- Modify: `select_candidates.py`
- Modify: `tests/test_select_candidates.py`

**Interfaces:**
- Produces: `prepare_candidates(rows: list[dict[str, str]], fetched_at: datetime) -> list[Candidate]`
- Produces: `select_candidates(candidates: list[Candidate], limit: int = 10) -> list[Candidate]`
- `Candidate` は元行、`Decimal` 数値、テーマ、カテゴリ、日数、元行番号、選定理由を保持するdataclass

- [ ] **Step 1: 価格・期限の境界値と取得日時基準の失敗テストを書く**

```python
class EligibilityTests(unittest.TestCase):
    def test_includes_exact_price_and_deadline_boundaries(self):
        fetched_at = datetime.fromisoformat("2026-07-30T12:00:00+09:00")
        rows = [
            make_row(market_id="1", yes="0.10", deadline="2026-08-06T12:00:00+09:00"),
            make_row(market_id="2", yes="0.90", deadline="2026-10-28T12:00:00+09:00"),
            make_row(market_id="3", yes="0.0999", deadline="2026-08-06T12:00:00+09:00"),
            make_row(market_id="4", yes="0.5", deadline="2026-10-28T12:00:01+09:00"),
        ]
        result = select_candidates.prepare_candidates(rows, fetched_at)
        self.assertEqual(["1", "2"], [item.market_id for item in result])
        self.assertEqual(["7.00", "90.00"], [item.days_text for item in result])

    def test_deadline_uses_csv_acquisition_time_not_execution_time(self):
        fetched_at = datetime.fromisoformat("2020-01-01T00:00:00+00:00")
        rows = [make_row(deadline="2020-01-08T00:00:00+00:00")]
        self.assertEqual(1, len(select_candidates.prepare_candidates(rows, fetched_at)))
```

- [ ] **Step 2: 安定ソート、重複排除、3段階分散の失敗テストを書く**

```python
class SelectionTests(unittest.TestCase):
    def test_stable_sort_deduplicates_market_id(self):
        rows = [
            make_row(market_id="2", volume="100", question="Zulu"),
            make_row(market_id="1", volume="100", question="Beta"),
            make_row(market_id="1", volume="100", question="Alpha"),
        ]
        prepared = select_candidates.prepare_candidates(rows, FETCHED_AT)
        selected = select_candidates.select_candidates(prepared)
        self.assertEqual(["1", "2"], [item.market_id for item in selected])
        self.assertEqual("Alpha", selected[0].source["市場"])

    def test_diversifies_category_then_theme_in_three_passes(self):
        rows = make_diversification_rows()
        selected = select_candidates.select_candidates(
            select_candidates.prepare_candidates(rows, FETCHED_AT), limit=6
        )
        self.assertEqual(6, len(selected))
        self.assertEqual(
            [
                "価格・期限条件を満たし、出来高上位かつカテゴリ・テーマ分散",
                "価格・期限条件を満たし、テーマ分散を維持して補完",
                "価格・期限条件を満たし、出来高順で補完",
            ],
            list(dict.fromkeys(item.reason for item in selected)),
        )
```

- [ ] **Step 3: 対象テストを実行して未実装による失敗を確認する**

Run: `python -m unittest tests.test_select_candidates.EligibilityTests tests.test_select_candidates.SelectionTests -v`

Expected: `AttributeError` または `ImportError` で失敗

- [ ] **Step 4: `Candidate`、適格判定、完全ソートキー、重複排除を実装する**

```python
@dataclass
class Candidate:
    source: dict[str, str]
    row_number: int
    market_id: str
    volume: Decimal
    liquidity: Decimal
    yes_price: Decimal
    no_price: Decimal
    theme: str
    category: str
    days: Decimal
    days_text: str
    reason: str = ""

def _sort_key(item: Candidate) -> tuple[object, ...]:
    return (
        -item.volume,
        item.market_id,
        item.theme,
        item.source["市場"],
        item.source["締切日"],
        item.yes_price,
        item.no_price,
        item.liquidity,
        item.row_number,
    )
```

`prepare_candidates()` は全行を検証済みと仮定し、7日と90日を秒単位で包含判定する。`days_text` は `Decimal("0.01")` へ丸め、常に小数点以下2桁を出す。ソート後に最初の市場IDだけを残す。

- [ ] **Step 5: 3段階選択を実装する**

```python
def select_candidates(candidates: list[Candidate], limit: int = 10) -> list[Candidate]:
    selected: list[Candidate] = []
    selected_ids: set[str] = set()
    theme_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()

    passes = (
        (2, 1, "価格・期限条件を満たし、出来高上位かつカテゴリ・テーマ分散"),
        (None, 1, "価格・期限条件を満たし、テーマ分散を維持して補完"),
        (None, None, "価格・期限条件を満たし、出来高順で補完"),
    )
    for category_limit, theme_limit, reason in passes:
        for item in candidates:
            if item.market_id in selected_ids:
                continue
            if category_limit is not None and category_counts[item.category] >= category_limit:
                continue
            if theme_limit is not None and theme_counts[item.theme] >= theme_limit:
                continue
            item.reason = reason
            selected.append(item)
            selected_ids.add(item.market_id)
            category_counts[item.category] += 1
            theme_counts[item.theme] += 1
            if len(selected) == limit:
                return selected
    return selected
```

- [ ] **Step 6: 対象テストを再実行して成功を確認する**

Run: `python -m unittest tests.test_select_candidates.EligibilityTests tests.test_select_candidates.SelectionTests -v`

Expected: 全テスト `ok`

- [ ] **Step 7: Task 2をコミットする**

```powershell
git add select_candidates.py tests/test_select_candidates.py
git commit -m "feat: select deterministic market candidates"
```

---

### Task 3: CSVワークフロー、0件正常終了、バイト単位の再現性

**Files:**
- Modify: `select_candidates.py`
- Modify: `tests/test_select_candidates.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `find_latest_markets_csv(data_dir: Path) -> Path`
- Produces: `read_market_csv(path: Path) -> tuple[list[dict[str, str]], datetime]`
- Produces: `candidate_output_path(data_dir: Path, fetched_at: datetime) -> Path`
- Produces: `write_candidate_csv(path: Path, selected: list[Candidate]) -> None`
- Produces: `run(data_dir: Path) -> tuple[Path, int]`
- Produces: `main() -> int`

- [ ] **Step 1: 0件・取得日時不統一・元CSV不変の失敗テストを書く**

```python
class WorkflowTests(unittest.TestCase):
    def test_zero_candidates_writes_header_only_and_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            source = write_market_csv(data_dir, [make_row(yes="0.01")])
            before = source.read_bytes()
            output, count = select_candidates.run(data_dir)
            self.assertEqual(0, count)
            self.assertTrue(output.read_bytes().startswith(codecs.BOM_UTF8))
            with output.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual([], list(csv.DictReader(handle)))
            self.assertEqual(before, source.read_bytes())

    def test_rejects_inconsistent_acquisition_timestamps_without_output(self):
        rows = [
            make_row(market_id="1", fetched_at="2026-07-30T12:00:00+09:00"),
            make_row(market_id="2", fetched_at="2026-07-30T03:00:00+00:00"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_market_csv(data_dir, rows)
            with self.assertRaisesRegex(ValueError, "取得日時が不統一"):
                select_candidates.run(data_dir)
            self.assertEqual([], list(data_dir.glob("candidates_*.csv")))
```

- [ ] **Step 2: バイト単位の再実行決定性と安定した上書きの失敗テストを書く**

```python
    def test_rerun_produces_byte_identical_output(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_market_csv(data_dir, make_tied_rows())
            first_path, _ = select_candidates.run(data_dir)
            first_bytes = first_path.read_bytes()
            second_path, _ = select_candidates.run(data_dir)
            self.assertEqual(first_path, second_path)
            self.assertEqual(first_bytes, second_path.read_bytes())
```

- [ ] **Step 3: 対象テストを実行して未実装による失敗を確認する**

Run: `python -m unittest tests.test_select_candidates.WorkflowTests -v`

Expected: `AttributeError` で失敗

- [ ] **Step 4: 入力全体の事前検証と固定CSV出力を実装する**

`read_market_csv()` は `utf-8-sig` で読み、必須列、1行以上、取得日時の生文字列一致、timezone、空市場ID、全数値、全締切日時を検証してから返す。`run()` は検証が終わるまで出力パスを開かない。

```python
OUTPUT_FIELDS = INPUT_FIELDS + ("カテゴリ", "締切までの日数", "選定理由")

def write_candidate_csv(path: Path, selected: list[Candidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=OUTPUT_FIELDS,
            lineterminator="\r\n",
        )
        writer.writeheader()
        for item in selected:
            writer.writerow({
                **{field: item.source[field] for field in INPUT_FIELDS},
                "カテゴリ": item.category,
                "締切までの日数": item.days_text,
                "選定理由": item.reason,
            })
```

- [ ] **Step 5: CLIの正常・異常終了を実装してテストする**

`main()` は `Path(__file__).resolve().parent / "data"` を `run()` へ渡す。正常0件なら `"<path> に候補0件を保存しました"`、正常1件以上なら `"<path> に候補N件を保存しました"` を標準出力へ出す。`OSError` と `ValueError` は `エラー: ...` を標準エラーへ出し、1を返す。

Run: `python -m unittest tests.test_select_candidates.WorkflowTests -v`

Expected: 全テスト `ok`

- [ ] **Step 6: 候補CSVをGit管理対象外へ追加する**

```text
data/candidates_*.csv
```

- [ ] **Step 7: 全単体テストを実行する**

Run: `python -m unittest discover -s tests -v`

Expected: 既存17件を含む全テスト成功

- [ ] **Step 8: Task 3をコミットする**

```powershell
git add select_candidates.py tests/test_select_candidates.py .gitignore
git commit -m "feat: add candidate CSV workflow"
```

---

### Task 4: 利用文書、実データ検証、最終確認

**Files:**
- Modify: `README.md`
- Modify: `plan.md`

**Interfaces:**
- Consumes: `python select_candidates.py`
- Produces: 利用方法、カテゴリ優先順位、0件正常仕様、検証結果の日本語文書

- [ ] **Step 1: READMEへ候補選別機の仕様と実行方法を追記する**

次の内容を日本語で明記する。

```text
python select_candidates.py
data/candidates_YYYY-MM-DD_HHMM.csv
```

- 価格0.10〜0.90、期限7〜90日、最大10件
- 期限は元CSV各行の取得日時基準
- 価格・期限条件は自動緩和しない
- 0件はヘッダーのみで正常終了
- カテゴリ優先順位は「政治→国際情勢→暗号資産→経済・金融→テクノロジー→エンタメ→科学・健康→その他」
- キーワードの正本は `select_candidates.py` の `CATEGORY_RULES`
- URL正規化と市場IDフォールバック
- 3段階の分散選択
- 同じ入力では同じ出力パスとバイト列

- [ ] **Step 2: `plan.md` へ完了項目と検証欄を追加する**

候補選別機、境界値、0件、取得日時不統一、URL正規化、カテゴリ優先順位、完全ソート、バイト一致の各項目をチェック済みとして記載する。実データ結果は実行後の件数とファイル名を正確に記録する。

- [ ] **Step 3: 実データで候補CSVを生成する**

Run: `python select_candidates.py`

Expected for current `markets_2026-07-30_2204.csv`: 終了コード0、`data/candidates_2026-07-30_2204.csv`、表示に `候補0件`

- [ ] **Step 4: 生成CSVと元CSV不変を検証する**

PowerShellで元CSVのSHA-256を実行前後に比較し、候補CSVの先頭3バイトが `EF BB BF`、列が12個、データ行が0件であることを確認する。同じ入力で2回実行し、候補CSVのSHA-256が一致することも確認する。

- [ ] **Step 5: 文法検査と全テストを新規実行する**

Run: `python -m py_compile fetch_markets.py select_candidates.py`

Expected: 終了コード0

Run: `python -m unittest discover -s tests -v`

Expected: 全テスト成功、失敗0件

- [ ] **Step 6: 変更範囲と既存収集機の非変更を確認する**

Run: `git diff main...HEAD --stat`

Expected: `fetch_markets.py` と `requirements.txt` が変更一覧にない

- [ ] **Step 7: Task 4をコミットする**

```powershell
git add README.md plan.md
git commit -m "docs: document candidate selection"
```

- [ ] **Step 8: 完了前の全検証を再実行する**

Run: `python -m py_compile fetch_markets.py select_candidates.py`

Run: `python -m unittest discover -s tests -v`

Run: `git status --short`

Expected: 文法検査成功、全テスト成功、追跡対象の未コミット変更なし
