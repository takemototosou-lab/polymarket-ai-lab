# Polymarket Market Snapshot Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 認証不要のPolymarket公開APIから、条件を満たす非スポーツのYes/No市場を最大100件取得し、UTF-8 BOM付き時刻別CSVへ保存する。

**Architecture:** `fetch_markets.py`を実行入口兼ライブラリとして構成し、HTTP再試行、市場正規化・絞り込み、CLOB価格取得、CSV保存を副作用の小さい関数へ分ける。単体テストでは通信と待機を注入し、実API確認は完成後にコマンド実行してCSVを独立検査する。

**Tech Stack:** Python 3.13、requests 2.x、標準ライブラリ（csv、datetime、json、pathlib、unittest）

## Global Constraints

- 公開Gamma APIと公開CLOB読み取りAPIのみを使用し、認証情報を扱わない。
- 累計出来高10,000ドル以上、流動性5,000ドル以上とする。
- 現在取引可能で、将来の締切日を持つYes/No二択市場だけを対象とする。
- スポーツ市場をタグとスポーツ固有項目の両方で除外する。
- CLOB midpointがYes/No両方で0以上1以下の市場だけを保存する。
- 最大100件を累計出来高降順で保存する。
- CSV列は`取得日時,市場ID,市場,YES価格,NO価格,出来高,流動性,締切日,URL`の順とする。
- CSVはUTF-8 BOM付きで保存し、同名ファイルを上書きしない。
- 個別市場の欠損・不正値・一部通信失敗で、処理可能な他市場まで停止させない。
- 外部依存は`requests`だけとし、不要なライブラリを追加しない。

---

### Task 1: 市場の解析・絞り込み

**Files:**
- Create: `tests/test_fetch_markets.py`
- Create: `fetch_markets.py`

**Interfaces:**
- Consumes: Gamma APIの市場辞書、`set[str]`のスポーツタグID、timezone-awareな現在日時
- Produces: `parse_json_list(value: object) -> list[str]`、`normalize_market(market: dict, sport_tag_ids: set[str], now: datetime) -> dict | None`

- [ ] **Step 1: 失敗する解析・絞り込みテストを書く**

```python
class NormalizeMarketTests(unittest.TestCase):
    def test_accepts_complete_non_sports_yes_no_market(self):
        market = make_market()
        candidate = fetch_markets.normalize_market(
            market, {"1", "82"}, datetime(2026, 7, 30, tzinfo=timezone.utc)
        )
        self.assertEqual("123", candidate["market_id"])
        self.assertEqual(("yes-token", "no-token"), candidate["token_ids"])

    def test_rejects_sports_tag(self):
        market = make_market(tags=[{"id": "82"}])
        self.assertIsNone(fetch_markets.normalize_market(
            market, {"82"}, datetime(2026, 7, 30, tzinfo=timezone.utc)
        ))

    def test_rejects_sports_specific_field(self):
        market = make_market(sportsMarketType="moneyline")
        self.assertIsNone(fetch_markets.normalize_market(
            market, set(), datetime(2026, 7, 30, tzinfo=timezone.utc)
        ))

    def test_rejects_missing_or_invalid_market_without_raising(self):
        self.assertIsNone(fetch_markets.normalize_market(
            {"id": "broken"}, set(), datetime(2026, 7, 30, tzinfo=timezone.utc)
        ))
```

`make_market()`は、`id="123"`、`question="日本語を含む市場?"`、`active=True`、`closed=False`、`acceptingOrders=True`、`endDate="2026-12-31T00:00:00Z"`、`volumeNum=20000`、`liquidityNum=8000`、`outcomes='["Yes","No"]'`、`clobTokenIds='["yes-token","no-token"]'`、空の`tags`、`events=[{"slug":"sample-event","tags":[]}]`を既定値とし、キーワード引数で上書きする。

- [ ] **Step 2: テストが期待どおり失敗することを確認する**

Run: `python -m unittest tests.test_fetch_markets.NormalizeMarketTests -v`

Expected: `ModuleNotFoundError: No module named 'fetch_markets'`

- [ ] **Step 3: 最小実装を書く**

```python
MIN_VOLUME = 10_000.0
MIN_LIQUIDITY = 5_000.0

def parse_json_list(value: object) -> list[str]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        raise ValueError("list required")
    return [str(item) for item in parsed]

def normalize_market(
    market: dict, sport_tag_ids: set[str], now: datetime
) -> dict | None:
    try:
        outcomes = [item.casefold() for item in parse_json_list(market["outcomes"])]
        token_ids = parse_json_list(market["clobTokenIds"])
        end_date = parse_iso_datetime(market["endDate"])
        volume = float(market.get("volumeNum") or market["volume"])
        liquidity = float(market.get("liquidityNum") or market["liquidity"])
        tags = market_tags(market)
        if (
            market.get("active") is not True
            or market.get("closed") is not False
            or market.get("acceptingOrders") is not True
            or end_date <= now
            or volume < MIN_VOLUME
            or liquidity < MIN_LIQUIDITY
            or outcomes != ["yes", "no"]
            or len(token_ids) != 2
            or tags & sport_tag_ids
            or any(market.get(name) for name in SPORTS_FIELDS)
        ):
            return None
        return {
            "market_id": str(market["id"]),
            "question": str(market["question"]),
            "token_ids": (token_ids[0], token_ids[1]),
            "volume": volume,
            "liquidity": liquidity,
            "end_date": market["endDate"],
            "url": market_url(market),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
```

`parse_iso_datetime()`は末尾`Z`を`+00:00`へ変換して`datetime.fromisoformat()`で解析する。`market_tags()`は市場直下と`events[*].tags`のIDを集合化する。`SPORTS_FIELDS`は`("gameId", "gameStartTime", "sportsMarketType")`とする。`market_url()`はイベントslugを優先し、なければ市場slugを`https://polymarket.com/event/`へ連結する。

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m unittest tests.test_fetch_markets.NormalizeMarketTests -v`

Expected: 4 tests、`OK`

- [ ] **Step 5: Task 1をコミットする**

```text
git add fetch_markets.py tests/test_fetch_markets.py
git commit -m "feat: filter eligible Polymarket markets"
```

---

### Task 2: HTTP再試行・Gammaページング・CLOB価格取得

**Files:**
- Modify: `tests/test_fetch_markets.py`
- Modify: `fetch_markets.py`
- Create: `requirements.txt`

**Interfaces:**
- Consumes: `requests.Session`互換オブジェクト、URL、token ID
- Produces: `request_json(session, method: str, url: str, *, sleep: Callable, **kwargs) -> object`、`fetch_sport_tag_ids(session) -> set[str]`、`iter_market_pages(session, now: datetime) -> Iterator[list[dict]]`、`fetch_midpoints(session, token_ids: list[str], batch_size: int = 50) -> dict[str, float]`

- [ ] **Step 1: 失敗する通信境界テストを書く**

```python
class ApiClientTests(unittest.TestCase):
    def test_retries_transient_status_then_returns_json(self):
        session = FakeSession([
            FakeResponse(429, {}),
            FakeResponse(503, {}),
            FakeResponse(200, {"ok": True}),
        ])
        waits = []
        result = fetch_markets.request_json(
            session, "GET", "https://example.test", sleep=waits.append
        )
        self.assertEqual({"ok": True}, result)
        self.assertEqual([1.0, 2.0], waits)

    def test_fetches_all_sports_tag_ids(self):
        session = FakeSession([FakeResponse(200, [
            {"tags": "1,82"}, {"tags": "82,100639"}
        ])])
        self.assertEqual(
            {"1", "82", "100639"},
            fetch_markets.fetch_sport_tag_ids(session),
        )

    def test_uses_next_cursor_for_market_pages(self):
        session = FakeSession([
            FakeResponse(200, {"markets": [{"id": "1"}], "next_cursor": "abc"}),
            FakeResponse(200, {"markets": [{"id": "2"}]}),
        ])
        pages = list(fetch_markets.iter_market_pages(
            session, datetime(2026, 7, 30, tzinfo=timezone.utc)
        ))
        self.assertEqual([["1"], ["2"]], [[m["id"] for m in page] for page in pages])
        self.assertEqual("abc", session.calls[1]["params"]["after_cursor"])

    def test_maps_valid_midpoints_and_skips_invalid_values(self):
        session = FakeSession([FakeResponse(200, {
            "yes-token": "0.42", "no-token": "0.58", "bad-token": "2"
        })])
        self.assertEqual(
            {"yes-token": 0.42, "no-token": 0.58},
            fetch_markets.fetch_midpoints(
                session, ["yes-token", "no-token", "bad-token"]
            ),
        )

    def test_splits_failed_midpoint_batch_and_keeps_successful_tokens(self):
        session = FakeSession([
            FakeResponse(503, {}),
            FakeResponse(503, {}),
            FakeResponse(503, {}),
            FakeResponse(200, {"yes-token": "0.42"}),
            FakeResponse(200, {"no-token": "0.58"}),
        ])
        self.assertEqual(
            {"yes-token": 0.42, "no-token": 0.58},
            fetch_markets.fetch_midpoints(
                session, ["yes-token", "no-token"], batch_size=2
            ),
        )
```

`FakeResponse.raise_for_status()`は400以上で`requests.HTTPError(response=self)`を送出し、`FakeSession.request()`は呼び出し情報を`calls`へ保存して順にresponseを返す。

- [ ] **Step 2: テストが未定義関数で失敗することを確認する**

Run: `python -m unittest tests.test_fetch_markets.ApiClientTests -v`

Expected: `AttributeError`で`request_json`が未定義

- [ ] **Step 3: 最小通信実装と依存定義を書く**

```python
def request_json(session, method, url, *, sleep=time.sleep, **kwargs):
    for attempt in range(3):
        try:
            response = session.request(method, url, timeout=20, **kwargs)
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            response = getattr(exc, "response", None)
            status = response.status_code if response is not None else 0
            retryable = status in (0, 429) or status >= 500
            if not retryable or attempt == 2:
                raise
            sleep(float(2**attempt))

def fetch_sport_tag_ids(session) -> set[str]:
    payload = request_json(session, "GET", f"{GAMMA_BASE}/sports")
    return {
        tag.strip()
        for sport in payload
        for tag in str(sport.get("tags", "")).split(",")
        if tag.strip()
    }

def iter_market_pages(session, now):
    params = {
        "limit": 100,
        "closed": "false",
        "order": "volumeNum",
        "ascending": "false",
        "volume_num_min": MIN_VOLUME,
        "liquidity_num_min": MIN_LIQUIDITY,
        "end_date_min": now.astimezone(timezone.utc).isoformat(),
        "include_tag": "true",
    }
    page_index = 0
    while True:
        try:
            payload = request_json(
                session, "GET", f"{GAMMA_BASE}/markets/keyset", params=params
            )
        except requests.RequestException as exc:
            if page_index == 0:
                raise
            print(f"警告: 市場ページ取得を途中終了します: {exc}", file=sys.stderr)
            return
        yield payload["markets"]
        page_index += 1
        cursor = payload.get("next_cursor")
        if not cursor:
            return
        params = {**params, "after_cursor": cursor}

def _parse_midpoints(payload):
    result = {}
    for token_id, raw_price in payload.items():
        try:
            price = float(raw_price)
            if 0.0 <= price <= 1.0:
                result[str(token_id)] = price
        except (TypeError, ValueError):
            continue
    return result

def _fetch_midpoint_batch(session, token_ids):
    try:
        payload = request_json(
            session,
            "POST",
            f"{CLOB_BASE}/midpoints",
            json=[{"token_id": token_id} for token_id in token_ids],
        )
        return _parse_midpoints(payload)
    except requests.RequestException:
        if len(token_ids) == 1:
            print(f"警告: CLOB価格を取得できません: {token_ids[0]}", file=sys.stderr)
            return {}
        middle = len(token_ids) // 2
        return {
            **_fetch_midpoint_batch(session, token_ids[:middle]),
            **_fetch_midpoint_batch(session, token_ids[middle:]),
        }

def fetch_midpoints(session, token_ids, batch_size=50):
    result = {}
    for start in range(0, len(token_ids), batch_size):
        result.update(_fetch_midpoint_batch(
            session, token_ids[start:start + batch_size]
        ))
    return result
```

`requirements.txt`:

```text
requests>=2.32,<3
```

- [ ] **Step 4: 通信境界テストと既存テストが通ることを確認する**

Run: `python -m unittest discover -s tests -v`

Expected: 9 tests、`OK`

- [ ] **Step 5: Task 2をコミットする**

```text
git add fetch_markets.py tests/test_fetch_markets.py requirements.txt
git commit -m "feat: fetch Gamma markets and CLOB prices"
```

---

### Task 3: 行選択・CSV保存・コマンド実行

**Files:**
- Modify: `tests/test_fetch_markets.py`
- Modify: `fetch_markets.py`
- Create: `data/.gitkeep`

**Interfaces:**
- Consumes: 正規化済み候補、`dict[str, float]`のmidpoint、取得日時、出力ディレクトリ
- Produces: `select_rows(candidates: Iterable[dict], midpoints: dict[str, float], fetched_at: datetime, limit: int = 100) -> list[dict]`、`choose_output_path(data_dir: Path, fetched_at: datetime) -> Path`、`write_csv(rows: list[dict], path: Path) -> None`、`main() -> int`

- [ ] **Step 1: 失敗する選択・CSVテストを書く**

```python
class CsvOutputTests(unittest.TestCase):
    def test_deduplicates_sorts_and_requires_both_prices(self):
        fetched_at = datetime(2026, 7, 30, 20, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        rows = fetch_markets.select_rows(
            [
                make_candidate("1", 20_000, ("y1", "n1")),
                make_candidate("1", 20_000, ("y1", "n1")),
                make_candidate("2", 30_000, ("y2", "n2")),
                make_candidate("3", 40_000, ("y3", "n3")),
            ],
            {"y1": 0.4, "n1": 0.6, "y2": 0.7, "n2": 0.3, "y3": 0.2},
            fetched_at,
        )
        self.assertEqual(["2", "1"], [row["市場ID"] for row in rows])

    def test_writes_utf8_bom_and_preserves_non_ascii_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "markets.csv"
            fetch_markets.write_csv([make_csv_row("日本語 – café")], path)
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(codecs.BOM_UTF8))
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual("日本語 – café", rows[0]["市場"])

    def test_output_path_does_not_overwrite_same_minute_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            fetched_at = datetime(2026, 7, 30, 20, 0, 45, tzinfo=timezone.utc)
            first = fetch_markets.choose_output_path(data_dir, fetched_at)
            first.touch()
            second = fetch_markets.choose_output_path(data_dir, fetched_at)
            self.assertNotEqual(first, second)
            self.assertFalse(second.exists())
```

- [ ] **Step 2: テストが未定義関数で失敗することを確認する**

Run: `python -m unittest tests.test_fetch_markets.CsvOutputTests -v`

Expected: `AttributeError`で`select_rows`が未定義

- [ ] **Step 3: 最小の選択・保存・実行処理を書く**

```python
CSV_FIELDS = ("取得日時", "市場ID", "市場", "YES価格", "NO価格", "出来高", "流動性", "締切日", "URL")

def select_rows(candidates, midpoints, fetched_at, limit=100):
    rows, seen = [], set()
    for candidate in sorted(candidates, key=lambda item: item["volume"], reverse=True):
        if candidate["market_id"] in seen:
            continue
        yes_token, no_token = candidate["token_ids"]
        if yes_token not in midpoints or no_token not in midpoints:
            continue
        seen.add(candidate["market_id"])
        rows.append({
            "取得日時": fetched_at.isoformat(),
            "市場ID": candidate["market_id"],
            "市場": candidate["question"],
            "YES価格": midpoints[yes_token],
            "NO価格": midpoints[no_token],
            "出来高": candidate["volume"],
            "流動性": candidate["liquidity"],
            "締切日": candidate["end_date"],
            "URL": candidate["url"],
        })
        if len(rows) == limit:
            break
    return rows

def write_csv(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

def choose_output_path(data_dir, fetched_at):
    stem = fetched_at.strftime("markets_%Y-%m-%d_%H%M")
    path = data_dir / f"{stem}.csv"
    if not path.exists():
        return path
    with_seconds = data_dir / f"{stem}_{fetched_at:%S}.csv"
    if not with_seconds.exists():
        return with_seconds
    suffix = 1
    while (data_dir / f"{stem}_{fetched_at:%S}_{suffix}.csv").exists():
        suffix += 1
    return data_dir / f"{stem}_{fetched_at:%S}_{suffix}.csv"

def collect_candidates(session, fetched_at):
    sport_tag_ids = fetch_sport_tag_ids(session)
    candidates = []
    for page in iter_market_pages(session, fetched_at):
        for market in page:
            candidate = normalize_market(market, sport_tag_ids, fetched_at)
            if candidate is not None:
                candidates.append(candidate)
        if len(candidates) >= CANDIDATE_TARGET:
            break
    return candidates

def main():
    fetched_at = datetime.now(ZoneInfo("Asia/Tokyo"))
    session = requests.Session()
    session.headers["User-Agent"] = "polymarket-ai-lab/1.0"
    try:
        candidates = collect_candidates(session, fetched_at)
        if not candidates:
            raise RuntimeError("条件を満たす市場候補が0件です")
        token_ids = [
            token_id
            for candidate in candidates
            for token_id in candidate["token_ids"]
        ]
        rows = select_rows(
            candidates,
            fetch_midpoints(session, token_ids),
            fetched_at,
        )
        if not rows:
            raise RuntimeError("有効なCLOB価格を持つ市場が0件です")
        output_path = choose_output_path(Path("data"), fetched_at)
        write_csv(rows, output_path)
        print(f"{output_path} に {len(rows)} 件保存しました")
        if len(rows) < OUTPUT_LIMIT:
            print(f"警告: 目標{OUTPUT_LIMIT}件に達しませんでした", file=sys.stderr)
        return 0
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
```

`CANDIDATE_TARGET = 150`、`OUTPUT_LIMIT = 100`とし、価格欠損があっても100件を確保できる余裕を持たせる。途中ページ取得失敗は、最初のページなら例外を再送出し、2ページ目以降なら警告してページングを終了する。

- [ ] **Step 4: 全単体テストが通ることを確認する**

Run: `python -m unittest discover -s tests -v`

Expected: 12 tests、`OK`

- [ ] **Step 5: Task 3をコミットする**

```text
git add fetch_markets.py tests/test_fetch_markets.py data/.gitkeep
git commit -m "feat: save timestamped market snapshots"
```

---

### Task 4: 利用手順と進捗文書

**Files:**
- Create: `README.md`
- Create: `plan.md`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: 完成したコマンドと検証結果
- Produces: セットアップ・実行・仕様・制限・検証方法を記載した日本語文書

- [ ] **Step 1: READMEとplan.mdを作る**

`README.md`に以下を具体的に記載する。

```text
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python fetch_markets.py
python -m unittest discover -s tests -v
```

さらに、対象条件、CSV列、無料・認証不要の読み取りAPIのみを使うこと、売買しないこと、100件未満になる条件、429への再試行、データが取得時点のスナップショットであることを記載する。

`plan.md`は「市場データ収集」「品質検証」を完了、「AI予測」「定期収集」「履歴評価」を未着手として、今回の取得条件と確認項目を残す。

`.gitignore`:

```text
.venv/
__pycache__/
*.py[cod]
data/markets_*.csv
```

- [ ] **Step 2: 文書とファイル構成を確認する**

Run: `rg --files`

Expected: `fetch_markets.py`、`data/.gitkeep`、`requirements.txt`、`README.md`、`plan.md`、`tests/test_fetch_markets.py`、設計書、実装計画が表示される。

- [ ] **Step 3: 全単体テストを再実行する**

Run: `python -m unittest discover -s tests -v`

Expected: 全テスト`OK`

- [ ] **Step 4: Task 4をコミットする**

```text
git add README.md plan.md .gitignore
git commit -m "docs: add collector usage and roadmap"
```

---

### Task 5: 実API実行・CSV品質検証・GitHub公開

**Files:**
- Modify: `plan.md`
- Generate but do not commit: `data/markets_YYYY-MM-DD_HHMM.csv`

**Interfaces:**
- Consumes: 実API、生成CSV、ローカルGitリポジトリ
- Produces: 品質検証結果、公開GitHubリポジトリ`polymarket-ai-lab`

- [ ] **Step 1: 依存関係を導入して実API収集を実行する**

Run:

```text
python -m pip install -r requirements.txt
python fetch_markets.py
```

Expected: `data/markets_YYYY-MM-DD_HHMM.csv`が生成され、保存件数100件が表示される。100件未満の場合は不足理由を確認し、仕様矛盾または実装不備なら停止する。

- [ ] **Step 2: 生成CSVを独立検査する**

Python標準ライブラリだけの一時的な検査コマンドで、以下を確認する。

```text
行数 == 100
全YES価格・NO価格が0以上1以下
全締切日が非空
全市場IDが非空
市場IDの重複数 == 0
先頭3 byte == EF BB BF
utf-8-sigで全行を再読込可能
```

- [ ] **Step 3: plan.mdへ実測結果を反映する**

取得日時、取得件数、価格範囲、締切日欠損数、市場ID欠損数、重複数、BOM、文字コード再読込結果を記録する。実行ごとに変わるCSV本体は`.gitignore`対象のままにする。

- [ ] **Step 4: 完了検証を実行する**

Run:

```text
python -m unittest discover -s tests -v
python -m py_compile fetch_markets.py tests/test_fetch_markets.py
git status -sb
git diff --check
```

Expected: 全テスト`OK`、コンパイル成功、意図した`plan.md`以外に未コミット変更なし、whitespace errorなし。

- [ ] **Step 5: 検証結果をコミットする**

```text
git add plan.md
git commit -m "docs: record live collection verification"
```

- [ ] **Step 6: GitHubに公開する**

GitHubの認証済みユーザー配下に公開リポジトリ`polymarket-ai-lab`を作成し、`origin`へ設定して`main`をpushする。既存同名リポジトリがある場合は上書きせず停止して報告する。

- [ ] **Step 7: 公開状態を確認する**

GitHub上のリポジトリURL、既定ブランチ`main`、最新コミット、README表示を読み取り確認する。
