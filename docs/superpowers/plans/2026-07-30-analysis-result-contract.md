# AI Analysis Result Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 最新の分析入力JSONを検証し、入力順を維持した全件 `pending` の分析結果JSONを決定的かつ原子的に生成する。

**Architecture:** `analyze_market.py` を、入力探索、厳格なJSON検証、結果生成、固定バイトシリアライズ、原子的保存、CLIの小さな関数へ分割する。初期版はローカルJSONからローカルJSONへの契約固定処理だけを担い、AI、検索、予測、売買、認証、外部通信を一切行わない。

**Tech Stack:** Python 3.10以上、標準ライブラリ（`codecs`, `datetime`, `decimal`, `json`, `os`, `pathlib`, `re`, `sys`, `tempfile`, `typing`, `unittest`）

## Global Constraints

- 正本は `docs/superpowers/specs/2026-07-30-analysis-result-contract-design.md` とする。
- `fetch_markets.py`、`select_candidates.py`、`prepare_analysis_input.py`、既存テスト、既存生成データ、`requirements.txt` は変更しない。
- 新規実装ファイルは `analyze_market.py`、新規テストは `tests/test_analyze_market.py` とする。
- 依存ライブラリを追加せず、Python標準ライブラリだけを使用する。
- 入力は `data/analysis_input_*.json` のファイル名昇順で最後の通常ファイルとする。
- 選択後のファイル名は `analysis_input_YYYY-MM-DD_HHMM.json` へ完全一致し、実在日時でなければならない。
- 入力トップレベルは配列、各要素は必須12キーを持つオブジェクトとする。
- 未知キーは許容するが、入力の `schema_version` を出力へ継承しない。
- JSON内のすべてのオブジェクト階層で重複キーを拒否する。
- `分析基準日時` は `+09:00` などの明示的オフセットと `Z` を受理し、入力表記を保持する。
- タイムゾーンなし、日付だけ、不正な暦日、不正な時刻を拒否する。
- 入力順と1対1対応を維持し、全市場を `pending` として出力する。
- `SCHEMA_VERSION = "1.0"` とし、全要素へ同じコード定数を設定する。
- 出力キー順は `schema_version`, `market_id`, `analysis_reference_time`, `status` とする。
- 出力はUTF-8 BOMなし、2スペースインデント、LF改行、末尾LFとする。
- 入力0件は正確な `b"[]\n"` とし、終了コード0とする。
- 同じ入力の正式出力はバイト単位で一致させる。
- 入力不正と保存失敗では既存結果JSONを変更しない。
- 元の入力JSONを変更しない。
- AI、Web検索、ニュース検索、確率予測、売買、認証、外部通信を追加しない。

---

### Task 1: 入出力ファイル名契約

**Files:**
- Create: `analyze_market.py`
- Create: `tests/test_analyze_market.py`

**Interfaces:**
- Produces: `INPUT_KEYS: tuple[str, ...]`
- Produces: `RESULT_KEYS: tuple[str, ...]`
- Produces: `SCHEMA_VERSION: str`
- Produces: `find_latest_analysis_input(data_dir: Path) -> Path`
- Produces: `output_path_for(input_path: Path) -> Path`

- [ ] **Step 1: テスト用fixtureとファイル選択の失敗テストを書く**

```python
import json
import os
import tempfile
import unittest
from pathlib import Path

import analyze_market


REFERENCE_TIME = "2026-07-30T22:04:49.568055+09:00"


def make_input_record(
    *,
    market_id="1",
    analysis_reference_time=REFERENCE_TIME,
    **overrides,
):
    record = {
        "市場ID": market_id,
        "市場": "市場A",
        "YES価格": 0.1,
        "NO価格": 0.9,
        "出来高": 1000,
        "流動性": 500.5,
        "締切日": "2026-08-29T12:00:00Z",
        "カテゴリ": "その他",
        "締切までの日数": 30,
        "URL": "https://polymarket.com/event/example",
        "分析基準日時": analysis_reference_time,
        "選定理由": "固定条件",
    }
    record.update(overrides)
    return record


def write_analysis_input(
    data_dir: Path,
    records,
    *,
    name="analysis_input_2026-07-30_2204.json",
) -> Path:
    path = data_dir / name
    payload = (
        json.dumps(records, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    path.write_bytes(payload)
    return path


class FileContractTests(unittest.TestCase):
    def test_finds_last_filename_and_derives_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            older = data_dir / "analysis_input_2026-07-29_2359.json"
            latest = data_dir / "analysis_input_2026-07-30_2204.json"
            older.touch()
            latest.touch()
            os.utime(older, (200, 200))
            os.utime(latest, (100, 100))

            self.assertEqual(
                latest,
                analyze_market.find_latest_analysis_input(data_dir),
            )
            self.assertEqual(
                data_dir / "analysis_result_2026-07-30_2204.json",
                analyze_market.output_path_for(latest),
            )

    def test_rejects_missing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "分析入力JSON"):
                analyze_market.find_latest_analysis_input(Path(directory))

    def test_rejects_invalid_selected_filename_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            valid = data_dir / "analysis_input_2026-07-30_2204.json"
            invalid = data_dir / "analysis_input_9999-99-99_9999.json"
            valid.touch()
            invalid.touch()

            selected = analyze_market.find_latest_analysis_input(data_dir)
            self.assertEqual(invalid, selected)
            with self.assertRaisesRegex(ValueError, "ファイル名"):
                analyze_market.output_path_for(selected)
```

- [ ] **Step 2: 対象テストを実行し、モジュール未作成による失敗を確認する**

Run: `python -m unittest tests.test_analyze_market.FileContractTests -v`

Expected: `ModuleNotFoundError: No module named 'analyze_market'`

- [ ] **Step 3: 定数、最新ファイル探索、出力名導出を最小実装する**

```python
import re
from datetime import datetime
from pathlib import Path


INPUT_KEYS = (
    "市場ID",
    "市場",
    "YES価格",
    "NO価格",
    "出来高",
    "流動性",
    "締切日",
    "カテゴリ",
    "締切までの日数",
    "URL",
    "分析基準日時",
    "選定理由",
)

RESULT_KEYS = (
    "schema_version",
    "market_id",
    "analysis_reference_time",
    "status",
)

SCHEMA_VERSION = "1.0"

ANALYSIS_INPUT_NAME = re.compile(
    r"^analysis_input_(\d{4}-\d{2}-\d{2})_(\d{4})\.json$"
)


def find_latest_analysis_input(data_dir: Path) -> Path:
    paths = sorted(
        (
            path
            for path in data_dir.glob("analysis_input_*.json")
            if path.is_file()
        ),
        key=lambda path: path.name,
    )
    if not paths:
        raise ValueError("入力となる分析入力JSONがありません")
    return paths[-1]


def output_path_for(input_path: Path) -> Path:
    match = ANALYSIS_INPUT_NAME.fullmatch(input_path.name)
    if match is None:
        raise ValueError("分析入力JSONのファイル名が不正です")
    try:
        datetime.strptime(
            f"{match.group(1)}_{match.group(2)}",
            "%Y-%m-%d_%H%M",
        )
    except ValueError:
        raise ValueError(
            "分析入力JSONのファイル名が不正です"
        ) from None
    suffix = f"{match.group(1)}_{match.group(2)}"
    return input_path.with_name(f"analysis_result_{suffix}.json")
```

- [ ] **Step 4: 対象テストと既存テストを実行する**

Run: `python -m unittest tests.test_analyze_market.FileContractTests -v`

Expected: 3件すべて `ok`

Run: `python -m unittest discover -s tests -v`

Expected: 既存53件を含む全テスト成功

- [ ] **Step 5: Task 1をコミットする**

```powershell
git add analyze_market.py tests/test_analyze_market.py
git commit -m "feat: add analysis result file contract"
```

---

### Task 2: 厳格なJSON入力検証

**Files:**
- Modify: `analyze_market.py`
- Modify: `tests/test_analyze_market.py`

**Interfaces:**
- Consumes: `INPUT_KEYS`
- Produces: `load_analysis_inputs(path: Path) -> list[dict[str, object]]`
- Produces: `_object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]`
- Produces: `_validate_reference_time(raw_value: str) -> None`

- [ ] **Step 1: トップレベル、必須キー、型の失敗テストを書く**

```python
class InputValidationTests(unittest.TestCase):
    def test_rejects_non_array_top_level(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), {"市場ID": "1"})
            with self.assertRaisesRegex(ValueError, "トップレベル"):
                analyze_market.load_analysis_inputs(path)

    def test_rejects_non_object_array_element(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), ["not-object"])
            with self.assertRaisesRegex(ValueError, "オブジェクト"):
                analyze_market.load_analysis_inputs(path)

    def test_rejects_missing_required_key(self):
        record = make_input_record()
        del record["選定理由"]
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), [record])
            with self.assertRaisesRegex(ValueError, "必須キー"):
                analyze_market.load_analysis_inputs(path)

    def test_rejects_wrong_required_types_and_boolean_numbers(self):
        cases = (
            ("市場ID", 1),
            ("市場", 1),
            ("YES価格", True),
            ("NO価格", "0.9"),
            ("出来高", None),
            ("流動性", []),
            ("締切日", 1),
            ("カテゴリ", {}),
            ("締切までの日数", False),
            ("URL", 1),
            ("分析基準日時", 1),
            ("選定理由", 1),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_analysis_input(
                        Path(directory),
                        [make_input_record(**{key: value})],
                    )
                    with self.assertRaisesRegex(ValueError, key):
                        analyze_market.load_analysis_inputs(path)
```

- [ ] **Step 2: 市場ID、日時、未知キーの失敗・成功テストを書く**

```python
    def test_rejects_blank_or_duplicate_market_id(self):
        cases = (
            [make_input_record(market_id=" ")],
            [
                make_input_record(market_id="1"),
                make_input_record(market_id="1"),
            ],
        )
        for records in cases:
            with self.subTest(records=records):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_analysis_input(Path(directory), records)
                    with self.assertRaisesRegex(ValueError, "市場ID"):
                        analyze_market.load_analysis_inputs(path)

    def test_accepts_offset_and_z_reference_times(self):
        for reference_time in (
            "2026-07-30T22:04:49.568055+09:00",
            "2026-07-30T13:04:49.568055Z",
        ):
            with self.subTest(reference_time=reference_time):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_analysis_input(
                        Path(directory),
                        [
                            make_input_record(
                                analysis_reference_time=reference_time
                            )
                        ],
                    )
                    records = analyze_market.load_analysis_inputs(path)
                    self.assertEqual(
                        reference_time,
                        records[0]["分析基準日時"],
                    )

    def test_rejects_invalid_reference_times(self):
        for reference_time in (
            "2026-07-30T22:04:49",
            "2026-07-30",
            "2026-02-30T12:00:00+09:00",
            "2026-07-30T25:00:00+09:00",
        ):
            with self.subTest(reference_time=reference_time):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_analysis_input(
                        Path(directory),
                        [
                            make_input_record(
                                analysis_reference_time=reference_time
                            )
                        ],
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "分析基準日時",
                    ):
                        analyze_market.load_analysis_inputs(path)

    def test_rejects_textually_inconsistent_reference_times(self):
        records = [
            make_input_record(
                market_id="1",
                analysis_reference_time="2026-07-30T22:04:00+09:00",
            ),
            make_input_record(
                market_id="2",
                analysis_reference_time="2026-07-30T13:04:00Z",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), records)
            with self.assertRaisesRegex(ValueError, "不統一"):
                analyze_market.load_analysis_inputs(path)

    def test_allows_unknown_keys_without_type_restriction(self):
        record = make_input_record()
        record["future_metadata"] = {
            "nested": [1, "two", None, {"enabled": True}]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), [record])
            records = analyze_market.load_analysis_inputs(path)
            self.assertEqual(record["future_metadata"], records[0]["future_metadata"])

    def test_allows_input_schema_version_as_an_unknown_key(self):
        for input_version in ("999.0", None, {"nested": True}):
            with self.subTest(input_version=input_version):
                record = make_input_record()
                record["schema_version"] = input_version
                with tempfile.TemporaryDirectory() as directory:
                    path = write_analysis_input(Path(directory), [record])
                    records = analyze_market.load_analysis_inputs(path)
                    self.assertEqual(
                        input_version,
                        records[0]["schema_version"],
                    )
```

- [ ] **Step 3: 全階層の重複キー、BOM、不正JSON、非有限数の失敗テストを書く**

```python
    def test_rejects_duplicate_key_at_market_level(self):
        record = make_input_record()
        text = json.dumps([record], ensure_ascii=False, indent=2)
        text = text.replace(
            '"市場ID": "1",',
            '"市場ID": "1",\n    "市場ID": "2",',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_input_2026-07-30_2204.json"
            path.write_bytes((text + "\n").encode("utf-8"))
            with self.assertRaisesRegex(ValueError, "重複"):
                analyze_market.load_analysis_inputs(path)

    def test_rejects_duplicate_key_in_nested_unknown_object(self):
        record = make_input_record()
        record["future_metadata"] = {"x": 1}
        text = json.dumps([record], ensure_ascii=False, indent=2)
        text = text.replace('"x": 1', '"x": 1,\n      "x": 2', 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_input_2026-07-30_2204.json"
            path.write_bytes((text + "\n").encode("utf-8"))
            with self.assertRaisesRegex(ValueError, "重複"):
                analyze_market.load_analysis_inputs(path)

    def test_rejects_bom_empty_invalid_json_and_non_finite_numbers(self):
        payloads = (
            b"\xef\xbb\xbf[]\n",
            b"",
            b"\xff",
            b"{",
            (
                json.dumps(
                    [make_input_record()],
                    ensure_ascii=False,
                ).replace('"YES価格": 0.1', '"YES価格": NaN')
                + "\n"
            ).encode("utf-8"),
            (
                json.dumps(
                    [make_input_record()],
                    ensure_ascii=False,
                ).replace('"YES価格": 0.1', '"YES価格": -Infinity')
                + "\n"
            ).encode("utf-8"),
            (
                json.dumps(
                    [make_input_record()],
                    ensure_ascii=False,
                ).replace('"YES価格": 0.1', '"YES価格": Infinity')
                + "\n"
            ).encode("utf-8"),
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with tempfile.TemporaryDirectory() as directory:
                    path = (
                        Path(directory)
                        / "analysis_input_2026-07-30_2204.json"
                    )
                    path.write_bytes(payload)
                    with self.assertRaises(ValueError):
                        analyze_market.load_analysis_inputs(path)
```

- [ ] **Step 4: 対象テストを実行して未実装による失敗を確認する**

Run: `python -m unittest tests.test_analyze_market.InputValidationTests -v`

Expected: `AttributeError` で失敗

- [ ] **Step 5: 全階層重複検出と厳格JSON読み込みを実装する**

```python
import codecs
import json
from decimal import Decimal


STRING_INPUT_KEYS = frozenset(
    (
        "市場ID",
        "市場",
        "締切日",
        "カテゴリ",
        "URL",
        "分析基準日時",
        "選定理由",
    )
)

NUMBER_INPUT_KEYS = frozenset(
    (
        "YES価格",
        "NO価格",
        "出来高",
        "流動性",
        "締切までの日数",
    )
)


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSONキーが重複しています: {key}")
        result[key] = value
    return result


def _reject_non_finite_constant(raw_value: str) -> None:
    raise ValueError(f"非有限のJSON numberです: {raw_value}")


def _validate_reference_time(raw_value: str) -> None:
    normalized = (
        f"{raw_value[:-1]}+00:00"
        if raw_value.endswith("Z")
        else raw_value
    )
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError("分析基準日時が不正です") from None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("分析基準日時にはタイムゾーンが必要です")


def load_analysis_inputs(path: Path) -> list[dict[str, object]]:
    payload = path.read_bytes()
    if payload.startswith(codecs.BOM_UTF8):
        raise ValueError("分析入力JSONにUTF-8 BOMは使用できません")
    try:
        text = payload.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_reject_non_finite_constant,
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except UnicodeError:
        raise ValueError("分析入力JSONがUTF-8ではありません") from None
    except json.JSONDecodeError:
        raise ValueError("分析入力JSONが不正です") from None

    if not isinstance(parsed, list):
        raise ValueError("JSONトップレベルは配列である必要があります")

    records: list[dict[str, object]] = []
    market_ids: set[str] = set()
    reference_time: str | None = None
    for element_number, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"{element_number}番目の要素がオブジェクトではありません"
            )
        missing = [key for key in INPUT_KEYS if key not in item]
        if missing:
            raise ValueError(
                f"{element_number}番目の要素に必須キーが不足しています: "
                f"{', '.join(missing)}"
            )
        for key in STRING_INPUT_KEYS:
            if not isinstance(item[key], str):
                raise ValueError(
                    f"{element_number}番目の{key}の型が不正です"
                )
        for key in NUMBER_INPUT_KEYS:
            value = item[key]
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(
                    f"{element_number}番目の{key}の型が不正です"
                )

        market_id = item["市場ID"]
        if not market_id.strip():
            raise ValueError(
                f"{element_number}番目の市場IDが空です"
            )
        if market_id in market_ids:
            raise ValueError(f"市場IDが重複しています: {market_id}")
        market_ids.add(market_id)

        current_reference = item["分析基準日時"]
        _validate_reference_time(current_reference)
        if reference_time is None:
            reference_time = current_reference
        elif current_reference != reference_time:
            raise ValueError("分析基準日時が不統一です")

        records.append(item)

    return records
```

- [ ] **Step 6: 対象テストと既存テストを再実行する**

Run: `python -m unittest tests.test_analyze_market.InputValidationTests -v`

Expected: 全テスト `ok`

Run: `python -m unittest discover -s tests -v`

Expected: 既存53件を含む全テスト成功

- [ ] **Step 7: Task 2をコミットする**

```powershell
git add analyze_market.py tests/test_analyze_market.py
git commit -m "feat: validate analysis result inputs"
```

---

### Task 3: pending結果とバージョン境界

**Files:**
- Modify: `analyze_market.py`
- Modify: `tests/test_analyze_market.py`

**Interfaces:**
- Consumes: `SCHEMA_VERSION`, `RESULT_KEYS`, `load_analysis_inputs()`
- Produces: `build_pending_results(records: list[dict[str, object]]) -> list[dict[str, str]]`

- [ ] **Step 1: 1対1対応、順序、全件同一バージョンの失敗テストを書く**

```python
class PendingResultTests(unittest.TestCase):
    def test_builds_one_pending_result_per_input_in_order(self):
        records = [
            make_input_record(market_id="2"),
            make_input_record(market_id="1"),
        ]
        results = analyze_market.build_pending_results(records)

        self.assertEqual(["2", "1"], [item["market_id"] for item in results])
        self.assertEqual(
            [REFERENCE_TIME, REFERENCE_TIME],
            [item["analysis_reference_time"] for item in results],
        )
        self.assertEqual(
            ["pending", "pending"],
            [item["status"] for item in results],
        )

    def test_uses_only_code_schema_version_for_every_result(self):
        records = [
            {
                **make_input_record(market_id="1"),
                "schema_version": "999.0",
            },
            {
                **make_input_record(market_id="2"),
                "schema_version": None,
            },
        ]
        results = analyze_market.build_pending_results(records)

        self.assertEqual("1.0", analyze_market.SCHEMA_VERSION)
        self.assertEqual(
            {"1.0"},
            {item["schema_version"] for item in results},
        )
        self.assertNotEqual(
            records[0]["schema_version"],
            results[0]["schema_version"],
        )

    def test_empty_input_produces_empty_result_list(self):
        self.assertEqual([], analyze_market.build_pending_results([]))
```

- [ ] **Step 2: 対象テストを実行して未実装による失敗を確認する**

Run: `python -m unittest tests.test_analyze_market.PendingResultTests -v`

Expected: `AttributeError` で失敗

- [ ] **Step 3: 固定4キーのpending結果生成を実装する**

```python
def build_pending_results(
    records: list[dict[str, object]],
) -> list[dict[str, str]]:
    return [
        {
            "schema_version": SCHEMA_VERSION,
            "market_id": record["市場ID"],
            "analysis_reference_time": record["分析基準日時"],
            "status": "pending",
        }
        for record in records
    ]
```

- [ ] **Step 4: 対象テストと既存テストを再実行する**

Run: `python -m unittest tests.test_analyze_market.PendingResultTests -v`

Expected: 3件すべて `ok`

Run: `python -m unittest discover -s tests -v`

Expected: 全テスト成功

- [ ] **Step 5: Task 3をコミットする**

```powershell
git add analyze_market.py tests/test_analyze_market.py
git commit -m "feat: build versioned pending results"
```

---

### Task 4: 固定バイトJSONシリアライズ

**Files:**
- Modify: `analyze_market.py`
- Modify: `tests/test_analyze_market.py`

**Interfaces:**
- Consumes: `RESULT_KEYS`
- Produces: `serialize_analysis_results(records: list[dict[str, str]]) -> bytes`

- [ ] **Step 1: 空配列と固定レイアウトの失敗テストを書く**

```python
import codecs


class SerializationTests(unittest.TestCase):
    def test_empty_array_is_exactly_three_bytes(self):
        self.assertEqual(
            b"[]\n",
            analyze_market.serialize_analysis_results([]),
        )

    def test_serializes_fixed_key_order_utf8_and_lf(self):
        record = {
            "schema_version": "1.0",
            "market_id": '日本語 "id" \\ path\nnext',
            "analysis_reference_time": REFERENCE_TIME,
            "status": "pending",
        }
        payload = analyze_market.serialize_analysis_results([record])

        self.assertFalse(payload.startswith(codecs.BOM_UTF8))
        self.assertTrue(payload.endswith(b"\n"))
        self.assertNotIn(b"\r\n", payload)
        text = payload.decode("utf-8")
        self.assertIn(
            '"market_id": "日本語 \\"id\\" \\\\ path\\nnext"',
            text,
        )
        parsed = json.loads(text, object_pairs_hook=list)
        self.assertEqual(
            list(analyze_market.RESULT_KEYS),
            [key for key, _ in parsed[0]],
        )

    def test_preserves_result_order_and_is_byte_deterministic(self):
        records = analyze_market.build_pending_results(
            [
                make_input_record(market_id="2"),
                make_input_record(market_id="1"),
            ]
        )
        first = analyze_market.serialize_analysis_results(records)
        second = analyze_market.serialize_analysis_results(records)

        self.assertEqual(first, second)
        self.assertEqual(
            ["2", "1"],
            [item["market_id"] for item in json.loads(first)],
        )
```

- [ ] **Step 2: 対象テストを実行して未実装による失敗を確認する**

Run: `python -m unittest tests.test_analyze_market.SerializationTests -v`

Expected: `AttributeError` で失敗

- [ ] **Step 3: 固定キー順の手動シリアライザを実装する**

```python
def _json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def serialize_analysis_results(
    records: list[dict[str, str]],
) -> bytes:
    if not records:
        return b"[]\n"
    lines = ["["]
    for record_index, record in enumerate(records):
        lines.append("  {")
        for key_index, key in enumerate(RESULT_KEYS):
            comma = "," if key_index < len(RESULT_KEYS) - 1 else ""
            lines.append(
                f"    {_json_string(key)}: "
                f"{_json_string(record[key])}{comma}"
            )
        object_comma = "," if record_index < len(records) - 1 else ""
        lines.append(f"  }}{object_comma}")
    lines.append("]")
    return ("\n".join(lines) + "\n").encode("utf-8")
```

- [ ] **Step 4: 対象テストと既存テストを再実行する**

Run: `python -m unittest tests.test_analyze_market.SerializationTests -v`

Expected: 3件すべて `ok`

Run: `python -m unittest discover -s tests -v`

Expected: 全テスト成功

- [ ] **Step 5: Task 4をコミットする**

```powershell
git add analyze_market.py tests/test_analyze_market.py
git commit -m "feat: serialize deterministic analysis results"
```

---

### Task 5: 原子的保存とCLI

**Files:**
- Modify: `analyze_market.py`
- Modify: `tests/test_analyze_market.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `find_latest_analysis_input()`, `output_path_for()`, `load_analysis_inputs()`, `build_pending_results()`, `serialize_analysis_results()`
- Produces: `atomic_write(path: Path, payload: bytes) -> None`
- Produces: `run(data_dir: Path) -> tuple[Path, int]`
- Produces: `main() -> int`
- Produces: `DATA_DIR: Path`

- [ ] **Step 1: 正常置換、入力不変、再実行一致の失敗テストを書く**

```python
class WorkflowTests(unittest.TestCase):
    def test_run_replaces_output_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            source = write_analysis_input(
                data_dir,
                [make_input_record()],
            )
            source_before = source.read_bytes()
            output = data_dir / "analysis_result_2026-07-30_2204.json"
            output.write_bytes(b"old")

            first_path, first_count = analyze_market.run(data_dir)
            first_bytes = first_path.read_bytes()
            second_path, second_count = analyze_market.run(data_dir)

            self.assertEqual((1, 1), (first_count, second_count))
            self.assertEqual(output, first_path)
            self.assertEqual(first_path, second_path)
            self.assertEqual(first_bytes, second_path.read_bytes())
            self.assertEqual(source_before, source.read_bytes())
```

- [ ] **Step 2: 入力不正・書き込み・置換失敗時の保護テストを書く**

```python
from unittest.mock import patch


    def test_invalid_input_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_analysis_input(data_dir, {"not": "array"})
            output = data_dir / "analysis_result_2026-07-30_2204.json"
            output.write_bytes(b"old")

            with self.assertRaisesRegex(ValueError, "トップレベル"):
                analyze_market.run(data_dir)

            self.assertEqual(b"old", output.read_bytes())

    def test_write_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_result_2026-07-30_2204.json"
            path.write_bytes(b"old")

            with patch.object(
                analyze_market,
                "_write_and_sync",
                side_effect=OSError("write failed"),
            ):
                with self.assertRaisesRegex(OSError, "write failed"):
                    analyze_market.atomic_write(path, b"new")

            self.assertEqual(b"old", path.read_bytes())
            self.assertEqual([], list(Path(directory).glob("*.tmp")))

    def test_replace_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_result_2026-07-30_2204.json"
            path.write_bytes(b"old")

            with patch.object(
                analyze_market.os,
                "replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    analyze_market.atomic_write(path, b"new")

            self.assertEqual(b"old", path.read_bytes())
            self.assertEqual([], list(Path(directory).glob("*.tmp")))
```

- [ ] **Step 3: 0件正常系とCLIエラーの失敗テストを書く**

```python
import io
from contextlib import redirect_stderr, redirect_stdout


    def test_main_reports_zero_results_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_analysis_input(data_dir, [])
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch.object(analyze_market, "DATA_DIR", data_dir),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = analyze_market.main()

            output = data_dir / "analysis_result_2026-07-30_2204.json"
            self.assertEqual(0, exit_code)
            self.assertEqual(b"[]\n", output.read_bytes())
            self.assertIn("分析結果0件", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())

    def test_main_reports_invalid_input_as_error(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch.object(analyze_market, "DATA_DIR", data_dir),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = analyze_market.main()

            self.assertEqual(1, exit_code)
            self.assertEqual("", stdout.getvalue())
            self.assertIn("エラー:", stderr.getvalue())
```

- [ ] **Step 4: 対象テストを実行して未実装による失敗を確認する**

Run: `python -m unittest tests.test_analyze_market.WorkflowTests -v`

Expected: `AttributeError` で失敗

- [ ] **Step 5: flush・fsyncを含む原子的保存を実装する**

```python
import os
import tempfile
from typing import BinaryIO


def _write_and_sync(handle: BinaryIO, payload: bytes) -> None:
    written = handle.write(payload)
    if written != len(payload):
        raise OSError("一時ファイルへ全バイトを書き込めません")
    handle.flush()
    os.fsync(handle.fileno())


def atomic_write(path: Path, payload: bytes) -> None:
    descriptor: int | None = None
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            _write_and_sync(handle, payload)
        os.replace(temporary_path, path)
        temporary_path = None
    except BaseException:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
        raise
```

- [ ] **Step 6: ワークフローとCLIを実装する**

```python
import sys


DATA_DIR = Path(__file__).resolve().parent / "data"


def run(data_dir: Path) -> tuple[Path, int]:
    input_path = find_latest_analysis_input(data_dir)
    output_path = output_path_for(input_path)
    input_records = load_analysis_inputs(input_path)
    results = build_pending_results(input_records)
    payload = serialize_analysis_results(results)
    atomic_write(output_path, payload)
    return output_path, len(results)


def main() -> int:
    try:
        output_path, count = run(DATA_DIR)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    print(f"{output_path} に分析結果{count}件を保存しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: 分析結果JSONをGit管理対象外へ追加する**

`.gitignore` へ次の1行を追加する。

```text
data/analysis_result_*.json
```

- [ ] **Step 8: 対象テストと全テストを再実行する**

Run: `python -m unittest tests.test_analyze_market.WorkflowTests -v`

Expected: 全テスト `ok`

Run: `python -m unittest discover -s tests -v`

Expected: 全テスト成功

- [ ] **Step 9: Task 5をコミットする**

```powershell
git add .gitignore analyze_market.py tests/test_analyze_market.py
git commit -m "feat: add atomic analysis result workflow"
```

---

### Task 6: README、plan.md、実データ検証

**Files:**
- Modify: `README.md`
- Modify: `plan.md`

**Interfaces:**
- Consumes: `python analyze_market.py`
- Produces: 利用方法、入力契約、バージョン境界、0件正常系、実測結果の日本語文書

- [ ] **Step 1: READMEへ分析結果契約固定機を追記する**

次を明記する。

```text
python analyze_market.py
data/analysis_result_YYYY-MM-DD_HHMM.json
```

- 最新入力JSONをファイル名順で選ぶ
- 必須12キーと型、全階層の重複キー拒否
- 未知キー許容と入力 `schema_version` 非継承
- `+09:00` と `Z` の受理、入力日時表記の保持
- 入力順と1対1対応
- 固定4キーと全件 `pending`
- `SCHEMA_VERSION = "1.0"` と同一ファイル内の単一バージョン
- UTF-8 BOMなし、LF、2スペース、末尾LF
- 0件は正確な `[]\n`
- 空配列にはバージョン情報を保持できない
- 同一入力はバイト単位で同一
- 原子的保存と失敗時の既存出力保護
- AI、Web検索、ニュース検索、確率予測、売買、認証、外部通信を行わない

- [ ] **Step 2: `plan.md` へ完了項目と実測欄を追加する**

入力契約、全階層重複キー、日時契約、固定4キー、バージョン境界、全件 `pending`、0件正常系、原子的保存、決定性を完了項目へ追加する。

実測欄には次を実行結果どおり記載する。

- 入力JSON
- 入力件数
- 出力JSON
- 出力件数と状態
- 終了コード
- 入出力SHA-256
- 2回の出力SHA-256一致
- 0件なら正確なバイト列 `5B 5D 0A`
- 元入力不変
- 全テスト件数

- [ ] **Step 3: 現在の分析入力JSONでCLIを2回実行する**

Run: `python analyze_market.py`

Expected for current `data/analysis_input_2026-07-30_2204.json`: 終了コード0、`data/analysis_result_2026-07-30_2204.json`、表示に `分析結果0件`

同じコマンドをもう一度実行し、2回の出力SHA-256が一致することを確認する。

- [ ] **Step 4: 実ファイル契約を検証する**

- 出力が正確に3バイト `5B 5D 0A`
- UTF-8 BOMがない
- JSONとして読み込むと空配列である
- 2回の出力SHA-256が一致する
- 入力JSONの実行前後SHA-256が一致する
- `data/analysis_result_*.json` がGit管理対象外である

- [ ] **Step 5: Python構文と全テストを最終検証する**

Run: `python -m py_compile fetch_markets.py select_candidates.py prepare_analysis_input.py analyze_market.py`

Expected: 終了コード0

Run: `python -m unittest discover -s tests -v`

Expected: 全テスト成功

Run:

```powershell
$baseCommit = git merge-base main HEAD
git diff --exit-code $baseCommit -- fetch_markets.py select_candidates.py prepare_analysis_input.py requirements.txt
```

Expected: 差分なし

- [ ] **Step 6: READMEとplan.mdをコミットする**

```powershell
git add README.md plan.md
git commit -m "docs: document analysis result contract"
```

---

### Task 7: 最終差分レビューと統合判断

**Files:**
- Review: `analyze_market.py`
- Review: `tests/test_analyze_market.py`
- Review: `.gitignore`
- Review: `README.md`
- Review: `plan.md`

**Interfaces:**
- Consumes: Task 1からTask 6までの全成果物
- Produces: 実装前設計に一致する検証済み機能ブランチ

- [ ] **Step 1: 変更対象を確認する**

Run: `git status --short --branch`

Expected: 意図しない未コミット変更なし

Run:

```powershell
$baseCommit = git merge-base main HEAD
git diff --stat "$baseCommit..HEAD"
```

Expected: 設計で許可されたファイルだけが表示される

- [ ] **Step 2: 設計要件を差分へ照合する**

次をコード、テスト、README、`plan.md` で確認する。

- 必須12キーと型
- 未知キー許容
- 入力 `schema_version` 非継承
- 全階層の重複キー拒否
- `+09:00` と `Z` の受理
- タイムゾーンなし、日付だけ、不正日時の拒否
- 入力順と1対1対応
- 固定4キーと全件 `pending`
- 全要素で `SCHEMA_VERSION = "1.0"`
- 0件の正確な `[]\n`
- 固定UTF-8バイト形式
- 原子的保存と既存出力保護
- 入力JSON不変
- 外部通信なし

- [ ] **Step 3: 新鮮な最終検証を実行する**

Run: `python -m py_compile fetch_markets.py select_candidates.py prepare_analysis_input.py analyze_market.py`

Expected: 終了コード0

Run: `python -m unittest discover -s tests -v`

Expected: 失敗0件

Run:

```powershell
$baseCommit = git merge-base main HEAD
git diff --check "$baseCommit..HEAD"
```

Expected: 問題なし

- [ ] **Step 4: 完成ブランチの統合方法を利用者へ確認する**

`superpowers:finishing-a-development-branch` に従い、`main`へのローカルマージ、PR作成、ブランチ保持の3択を提示する。利用者の選択前にマージ、push、worktree削除、ブランチ削除を行わない。
