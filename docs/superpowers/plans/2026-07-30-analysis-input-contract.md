# Analysis Input Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 最新の候補CSVを、固定キー順・固定数値表現・固定バイト形式のAI分析用JSONへ原子的に変換する。

**Architecture:** `prepare_analysis_input.py` を既存収集機・候補選別機から独立した標準ライブラリのみのCLIとして追加する。入力検証と値変換、決定的シリアライズ、原子的保存を別関数に分け、`run()` と `main()` がそれらを接続する。

**Tech Stack:** Python 3.10以上、標準ライブラリ（`csv`, `datetime`, `decimal`, `json`, `os`, `pathlib`, `re`, `tempfile`, `unittest`）

## Global Constraints

- 正本は `docs/superpowers/specs/2026-07-30-analysis-input-contract-design.md` とする。
- `fetch_markets.py`、`select_candidates.py`、市場CSV、候補CSV、`requirements.txt` は変更しない。
- AI API、ニュース検索、確率予測、売買、認証、外部通信を追加しない。
- 依存ライブラリを追加しない。
- 入力はファイル名昇順で最後の `data/candidates_*.csv` とする。
- 候補0件は正確な `b"[]\n"` を正常出力し、終了コード0とする。
- 数値は有限な `Decimal` として解析し、丸め・指数表記・末尾ゼロなしのJSON numberへ変換する。
- JSONはUTF-8 BOMなし、LF改行、2スペースインデント、末尾LFありとする。
- 入力行順と `JSON_KEYS` のキー順を維持する。
- 入力不正と保存失敗では既存JSONを作成または変更しない。
- 正式JSONは同一ディレクトリの一時ファイルから `os.replace()` で原子的に置換する。

---

### Task 1: 固定JSON number表現

**Files:**
- Create: `prepare_analysis_input.py`
- Create: `tests/test_prepare_analysis_input.py`

**Interfaces:**
- Produces: `canonical_json_number(raw_value: str, field: str, row_number: int) -> str`
- Produces: `NUMERIC_KEYS: frozenset[str]`

- [ ] **Step 1: テスト共通fixtureと有効なDecimal表記の失敗テストを書く**

```python
import codecs
import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import prepare_analysis_input

FETCHED_AT = "2026-07-30T22:04:49.568055+09:00"

def make_candidate_row(
    *,
    market_id="1",
    market="市場?",
    fetched_at=FETCHED_AT,
    **overrides,
):
    row = {
        "取得日時": fetched_at,
        "市場ID": market_id,
        "市場": market,
        "YES価格": "0.10",
        "NO価格": "0.90",
        "出来高": "1000.00",
        "流動性": "500.50",
        "締切日": "2026-08-29T12:00:00Z",
        "URL": "https://polymarket.com/event/example",
        "カテゴリ": "その他",
        "締切までの日数": "30.00",
        "選定理由": "固定条件",
    }
    row.update(overrides)
    return row

def write_candidate_csv(
    data_dir: Path,
    rows: list[dict[str, str]],
    *,
    name="candidates_2026-07-30_2204.csv",
    fieldnames=None,
) -> Path:
    path = data_dir / name
    fields = (
        list(prepare_analysis_input.INPUT_FIELDS)
        if fieldnames is None
        else fieldnames
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            lineterminator="\r\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
    return path

def make_analysis_record(*, market_id="1") -> dict[str, str]:
    return {
        "市場ID": market_id,
        "市場": "市場?",
        "YES価格": "0.1",
        "NO価格": "0.9",
        "出来高": "1000",
        "流動性": "500.5",
        "締切日": "2026-08-29T12:00:00Z",
        "カテゴリ": "その他",
        "締切までの日数": "30",
        "URL": "https://polymarket.com/event/example",
        "分析基準日時": FETCHED_AT,
        "選定理由": "固定条件",
    }

class CanonicalNumberTests(unittest.TestCase):
    def test_normalizes_finite_decimal_without_rounding(self):
        cases = {
            "0.10": "0.1",
            "+01.20": "1.2",
            "42.00": "42",
            "-0.00": "0",
            "1E+3": "1000",
            "1e-3": "0.001",
            "-001.2300": "-1.23",
        }
        for raw_value, expected in cases.items():
            with self.subTest(raw_value=raw_value):
                self.assertEqual(
                    expected,
                    prepare_analysis_input.canonical_json_number(
                        raw_value, "YES価格", 2
                    ),
                )
```

- [ ] **Step 2: 空・不正・非有限数の失敗テストを書く**

```python
    def test_rejects_invalid_or_non_finite_decimal(self):
        for raw_value in ("", "not-number", "NaN", "sNaN", "Infinity", "-Infinity"):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(ValueError, "YES価格"):
                    prepare_analysis_input.canonical_json_number(
                        raw_value, "YES価格", 7
                    )
```

- [ ] **Step 3: 対象テストを実行し、モジュール未作成で失敗することを確認する**

Run: `python -m unittest tests.test_prepare_analysis_input.CanonicalNumberTests -v`

Expected: `ModuleNotFoundError: No module named 'prepare_analysis_input'`

- [ ] **Step 4: 最小実装を追加する**

```python
from decimal import Decimal, InvalidOperation

NUMERIC_KEYS = frozenset(
    ("YES価格", "NO価格", "出来高", "流動性", "締切までの日数")
)

def canonical_json_number(
    raw_value: str,
    field: str,
    row_number: int,
) -> str:
    try:
        value = Decimal(raw_value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{row_number}行目の{field}が不正です") from exc
    if not value.is_finite():
        raise ValueError(f"{row_number}行目の{field}が不正です")
    if value.is_zero():
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
```

- [ ] **Step 5: 対象テストと既存テストを実行する**

Run: `python -m unittest tests.test_prepare_analysis_input.CanonicalNumberTests -v`

Expected: 全テスト `ok`

Run: `python -m unittest discover -s tests -v`

Expected: 既存36件を含む全テスト成功

- [ ] **Step 6: Task 1をコミットする**

```powershell
git add prepare_analysis_input.py tests/test_prepare_analysis_input.py
git commit -m "feat: add canonical JSON number formatting"
```

---

### Task 2: 最新候補CSVの選択と入力契約検証

**Files:**
- Modify: `prepare_analysis_input.py`
- Modify: `tests/test_prepare_analysis_input.py`

**Interfaces:**
- Produces: `INPUT_FIELDS: tuple[str, ...]`
- Produces: `JSON_KEYS: tuple[str, ...]`
- Produces: `find_latest_candidate_csv(data_dir: Path) -> Path`
- Produces: `output_path_for(input_path: Path) -> Path`
- Produces: `read_analysis_records(path: Path) -> list[dict[str, str]]`

- [ ] **Step 1: 最新ファイル選択と厳密な出力名の失敗テストを書く**

```python
class InputContractTests(unittest.TestCase):
    def test_finds_last_candidate_filename_and_derives_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            older = data_dir / "candidates_2026-07-29_2359.csv"
            latest = data_dir / "candidates_2026-07-30_2204.csv"
            older.touch()
            latest.touch()
            self.assertEqual(
                latest,
                prepare_analysis_input.find_latest_candidate_csv(data_dir),
            )
            self.assertEqual(
                data_dir / "analysis_input_2026-07-30_2204.json",
                prepare_analysis_input.output_path_for(latest),
            )

    def test_rejects_invalid_selected_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates_2026-99-99_9999.csv"
            path.touch()
            with self.assertRaisesRegex(ValueError, "ファイル名"):
                prepare_analysis_input.output_path_for(path)
```

- [ ] **Step 2: 必須列・取得日時・市場IDの失敗テストを書く**

```python
    def test_rejects_missing_required_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_candidate_csv(
                Path(directory),
                [make_candidate_row()],
                fieldnames=[
                    field
                    for field in prepare_analysis_input.INPUT_FIELDS
                    if field != "選定理由"
                ],
            )
            with self.assertRaisesRegex(ValueError, "必須列"):
                prepare_analysis_input.read_analysis_records(path)

    def test_rejects_inconsistent_acquisition_timestamps(self):
        rows = [
            make_candidate_row(market_id="1", fetched_at="2026-07-30T22:04:00+09:00"),
            make_candidate_row(market_id="2", fetched_at="2026-07-30T13:04:00+00:00"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = write_candidate_csv(Path(directory), rows)
            with self.assertRaisesRegex(ValueError, "取得日時が不統一"):
                prepare_analysis_input.read_analysis_records(path)

    def test_rejects_invalid_or_timezone_naive_acquisition_time(self):
        for fetched_at in ("not-a-date", "2026-07-30T22:04:49"):
            with self.subTest(fetched_at=fetched_at):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_candidate_csv(
                        Path(directory),
                        [make_candidate_row(fetched_at=fetched_at)],
                    )
                    with self.assertRaisesRegex(ValueError, "取得日時"):
                        prepare_analysis_input.read_analysis_records(path)
```

- [ ] **Step 3: 入力順・文字列保持・追加列無視の失敗テストを書く**

```python
    def test_preserves_row_order_and_raw_strings(self):
        rows = [
            {**make_candidate_row(market_id="2", market="市場B"), "追加列": "ignored"},
            {**make_candidate_row(market_id="1", market="市場A"), "追加列": "ignored"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = write_candidate_csv(
                Path(directory),
                rows,
                fieldnames=[*prepare_analysis_input.INPUT_FIELDS, "追加列"],
            )
            records = prepare_analysis_input.read_analysis_records(path)
            self.assertEqual(["2", "1"], [record["市場ID"] for record in records])
            self.assertEqual(["市場B", "市場A"], [record["市場"] for record in records])
            self.assertNotIn("追加列", records[0])
            self.assertEqual(
                "2026-07-30T22:04:49.568055+09:00",
                records[0]["分析基準日時"],
            )
```

- [ ] **Step 4: 対象テストを実行して未実装による失敗を確認する**

Run: `python -m unittest tests.test_prepare_analysis_input.InputContractTests -v`

Expected: `AttributeError` で失敗

- [ ] **Step 5: 定数、ファイル名検証、入力変換を実装する**

```python
import csv
import re
from datetime import datetime
from pathlib import Path

INPUT_FIELDS = (
    "取得日時", "市場ID", "市場", "YES価格", "NO価格", "出来高",
    "流動性", "締切日", "URL", "カテゴリ", "締切までの日数", "選定理由",
)
JSON_KEYS = (
    "市場ID", "市場", "YES価格", "NO価格", "出来高", "流動性",
    "締切日", "カテゴリ", "締切までの日数", "URL", "分析基準日時", "選定理由",
)
CANDIDATE_NAME = re.compile(
    r"^candidates_(\d{4}-\d{2}-\d{2})_(\d{4})\.csv$"
)
SOURCE_KEY_BY_JSON_KEY = {
    **{key: key for key in JSON_KEYS},
    "分析基準日時": "取得日時",
}

def find_latest_candidate_csv(data_dir: Path) -> Path:
    paths = sorted(
        (
            path
            for path in data_dir.glob("candidates_*.csv")
            if path.is_file()
        ),
        key=lambda path: path.name,
    )
    if not paths:
        raise ValueError("入力となる候補CSVがありません")
    return paths[-1]

def output_path_for(input_path: Path) -> Path:
    match = CANDIDATE_NAME.fullmatch(input_path.name)
    if match is None:
        raise ValueError("候補CSVのファイル名が不正です")
    try:
        datetime.strptime(
            f"{match.group(1)}_{match.group(2)}",
            "%Y-%m-%d_%H%M",
        )
    except ValueError as exc:
        raise ValueError("候補CSVのファイル名が不正です") from exc
    suffix = f"{match.group(1)}_{match.group(2)}"
    return input_path.with_name(f"analysis_input_{suffix}.json")

def _validate_fetched_at(raw_value: str) -> None:
    try:
        value = datetime.fromisoformat(
            raw_value.replace("Z", "+00:00")
        )
    except (AttributeError, ValueError) as exc:
        raise ValueError("取得日時が不正です") from exc
    if value.tzinfo is None:
        raise ValueError("取得日時にはタイムゾーンが必要です")

def read_analysis_records(
    path: Path,
) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [
            field for field in INPUT_FIELDS if field not in fieldnames
        ]
        if missing:
            raise ValueError(
                f"必須列が不足しています: {', '.join(missing)}"
            )
        rows = list(reader)
    if not rows:
        return []
    fetched_values = {row["取得日時"] for row in rows}
    if len(fetched_values) != 1:
        raise ValueError("元CSV内の取得日時が不統一です")
    _validate_fetched_at(rows[0]["取得日時"])
    records: list[dict[str, str]] = []
    for row_number, row in enumerate(rows, start=2):
        if any(row[field] is None for field in INPUT_FIELDS):
            raise ValueError(f"{row_number}行目に欠損値があります")
        if not row["市場ID"].strip():
            raise ValueError(f"{row_number}行目の市場IDが空です")
        record: dict[str, str] = {}
        for key in JSON_KEYS:
            source_key = SOURCE_KEY_BY_JSON_KEY[key]
            raw_value = row[source_key]
            record[key] = (
                canonical_json_number(raw_value, source_key, row_number)
                if key in NUMERIC_KEYS
                else raw_value
            )
        records.append(record)
    return records
```

- [ ] **Step 6: 対象テストと全テストを再実行する**

Run: `python -m unittest tests.test_prepare_analysis_input.InputContractTests -v`

Expected: 全テスト `ok`

Run: `python -m unittest discover -s tests -v`

Expected: 全テスト成功

- [ ] **Step 7: Task 2をコミットする**

```powershell
git add prepare_analysis_input.py tests/test_prepare_analysis_input.py
git commit -m "feat: validate analysis input contract"
```

---

### Task 3: 固定バイトJSONシリアライズ

**Files:**
- Modify: `prepare_analysis_input.py`
- Modify: `tests/test_prepare_analysis_input.py`

**Interfaces:**
- Consumes: `JSON_KEYS`, `NUMERIC_KEYS`, `read_analysis_records()`
- Produces: `serialize_analysis_input(records: list[dict[str, str]]) -> bytes`

- [ ] **Step 1: 空配列の正確なバイト列の失敗テストを書く**

```python
class SerializationTests(unittest.TestCase):
    def test_empty_array_is_exactly_three_bytes(self):
        self.assertEqual(
            b"[]\n",
            prepare_analysis_input.serialize_analysis_input([]),
        )
```

- [ ] **Step 2: キー順・数値・UTF-8・エスケープ・改行の失敗テストを書く**

```python
    def test_serializes_fixed_layout_without_bom(self):
        record = {
            "市場ID": "2",
            "市場": "日本語 \"quote\" \\ path\nnext",
            "YES価格": "0.1",
            "NO価格": "0.9",
            "出来高": "1000",
            "流動性": "500.5",
            "締切日": "2026-08-29T12:00:00Z",
            "カテゴリ": "その他",
            "締切までの日数": "30",
            "URL": "https://example.test/a",
            "分析基準日時": "2026-07-30T22:04:49.568055+09:00",
            "選定理由": "固定条件",
        }
        payload = prepare_analysis_input.serialize_analysis_input([record])
        self.assertFalse(payload.startswith(codecs.BOM_UTF8))
        self.assertTrue(payload.endswith(b"\n"))
        self.assertNotIn(b"\r\n", payload)
        text = payload.decode("utf-8")
        self.assertIn('"YES価格": 0.1', text)
        self.assertIn('"市場": "日本語 \\"quote\\" \\\\ path\\nnext"', text)
        parsed = json.loads(text, object_pairs_hook=list)
        self.assertEqual(
            list(prepare_analysis_input.JSON_KEYS),
            [key for key, _ in parsed[0]],
        )
```

- [ ] **Step 3: 入力順維持とバイト決定性の失敗テストを書く**

```python
    def test_preserves_record_order_and_is_byte_deterministic(self):
        records = [
            make_analysis_record(market_id="2"),
            make_analysis_record(market_id="1"),
        ]
        first = prepare_analysis_input.serialize_analysis_input(records)
        second = prepare_analysis_input.serialize_analysis_input(records)
        self.assertEqual(first, second)
        self.assertEqual(
            ["2", "1"],
            [item["市場ID"] for item in json.loads(first)],
        )
```

- [ ] **Step 4: 対象テストを実行して未実装による失敗を確認する**

Run: `python -m unittest tests.test_prepare_analysis_input.SerializationTests -v`

Expected: `AttributeError` で失敗

- [ ] **Step 5: 固定シリアライザを実装する**

```python
import json

def _json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)

def serialize_analysis_input(
    records: list[dict[str, str]],
) -> bytes:
    if not records:
        return b"[]\n"
    lines = ["["]
    for record_index, record in enumerate(records):
        lines.append("  {")
        for key_index, key in enumerate(JSON_KEYS):
            value = (
                record[key]
                if key in NUMERIC_KEYS
                else _json_string(record[key])
            )
            comma = "," if key_index < len(JSON_KEYS) - 1 else ""
            lines.append(f"    {_json_string(key)}: {value}{comma}")
        object_comma = "," if record_index < len(records) - 1 else ""
        lines.append(f"  }}{object_comma}")
    lines.append("]")
    return ("\n".join(lines) + "\n").encode("utf-8")
```

- [ ] **Step 6: 対象テストと全テストを再実行する**

Run: `python -m unittest tests.test_prepare_analysis_input.SerializationTests -v`

Expected: 全テスト `ok`

Run: `python -m unittest discover -s tests -v`

Expected: 全テスト成功

- [ ] **Step 7: Task 3をコミットする**

```powershell
git add prepare_analysis_input.py tests/test_prepare_analysis_input.py
git commit -m "feat: serialize deterministic analysis JSON"
```

---

### Task 4: 原子的保存とCLIワークフロー

**Files:**
- Modify: `prepare_analysis_input.py`
- Modify: `tests/test_prepare_analysis_input.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `atomic_write(path: Path, payload: bytes) -> None`
- Produces: `run(data_dir: Path) -> tuple[Path, int]`
- Produces: `main() -> int`
- Produces: `DATA_DIR: Path`

- [ ] **Step 1: 正常置換・元CSV不変・再実行一致の失敗テストを書く**

```python
class WorkflowTests(unittest.TestCase):
    def test_run_atomically_replaces_output_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            source = write_candidate_csv(data_dir, [make_candidate_row()])
            source_before = source.read_bytes()
            output = data_dir / "analysis_input_2026-07-30_2204.json"
            output.write_bytes(b"old")
            first_path, first_count = prepare_analysis_input.run(data_dir)
            first_bytes = first_path.read_bytes()
            second_path, second_count = prepare_analysis_input.run(data_dir)
            self.assertEqual((1, 1), (first_count, second_count))
            self.assertEqual(output, first_path)
            self.assertEqual(first_path, second_path)
            self.assertEqual(first_bytes, second_path.read_bytes())
            self.assertEqual(source_before, source.read_bytes())
```

- [ ] **Step 2: 書き込み・置換失敗時の既存出力保護の失敗テストを書く**

```python
    def test_write_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_input_2026-07-30_2204.json"
            path.write_bytes(b"old")
            with patch.object(
                prepare_analysis_input.os,
                "write",
                side_effect=OSError("write failed"),
            ):
                with self.assertRaisesRegex(OSError, "write failed"):
                    prepare_analysis_input.atomic_write(path, b"new")
            self.assertEqual(b"old", path.read_bytes())

    def test_replace_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_input_2026-07-30_2204.json"
            path.write_bytes(b"old")
            with patch.object(
                prepare_analysis_input.os,
                "replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    prepare_analysis_input.atomic_write(path, b"new")
            self.assertEqual(b"old", path.read_bytes())
            self.assertEqual([], list(Path(directory).glob("*.tmp")))

    def test_invalid_input_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_candidate_csv(
                data_dir,
                [make_candidate_row(**{"YES価格": "NaN"})],
            )
            output = data_dir / "analysis_input_2026-07-30_2204.json"
            output.write_bytes(b"old")
            with self.assertRaisesRegex(ValueError, "YES価格"):
                prepare_analysis_input.run(data_dir)
            self.assertEqual(b"old", output.read_bytes())
```

- [ ] **Step 3: 0件正常系とCLI終了コードの失敗テストを書く**

```python
    def test_main_reports_zero_records_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_candidate_csv(data_dir, [])
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch.object(prepare_analysis_input, "DATA_DIR", data_dir),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = prepare_analysis_input.main()
            output = data_dir / "analysis_input_2026-07-30_2204.json"
            self.assertEqual(0, exit_code)
            self.assertEqual(b"[]\n", output.read_bytes())
            self.assertIn("分析入力0件", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())
```

- [ ] **Step 4: 対象テストを実行して未実装による失敗を確認する**

Run: `python -m unittest tests.test_prepare_analysis_input.WorkflowTests -v`

Expected: `AttributeError` で失敗

- [ ] **Step 5: 原子的保存を実装する**

```python
import os
import tempfile

def atomic_write(path: Path, payload: bytes) -> None:
    descriptor: int | None = None
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written == 0:
                raise OSError("一時ファイルへ書き込めません")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
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
    input_path = find_latest_candidate_csv(data_dir)
    output_path = output_path_for(input_path)
    records = read_analysis_records(input_path)
    payload = serialize_analysis_input(records)
    atomic_write(output_path, payload)
    return output_path, len(records)

def main() -> int:
    try:
        output_path, count = run(DATA_DIR)
    except (csv.Error, OSError, UnicodeError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    print(f"{output_path} に分析入力{count}件を保存しました")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: 候補JSONをGit管理対象外へ追加する**

```text
data/analysis_input_*.json
```

- [ ] **Step 8: 対象テストと全テストを再実行する**

Run: `python -m unittest tests.test_prepare_analysis_input.WorkflowTests -v`

Expected: 全テスト `ok`

Run: `python -m unittest discover -s tests -v`

Expected: 全テスト成功

- [ ] **Step 9: Task 4をコミットする**

```powershell
git add prepare_analysis_input.py tests/test_prepare_analysis_input.py .gitignore
git commit -m "feat: add atomic analysis input workflow"
```

---

### Task 5: 文書、実データ検証、最終確認

**Files:**
- Modify: `README.md`
- Modify: `plan.md`

**Interfaces:**
- Consumes: `python prepare_analysis_input.py`
- Produces: 利用方法、入力契約、数値規則、0件正常系、実測結果の日本語文書

- [ ] **Step 1: READMEへ分析入力変換機を追記する**

次を明記する。

```text
python prepare_analysis_input.py
data/analysis_input_YYYY-MM-DD_HHMM.json
```

- 最新候補CSVをファイル名順で選ぶ
- 12列契約と取得日時完全一致
- 入力行順と固定キー順
- 5数値をJSON numberへ正規化
- UTF-8 BOMなし、LF、2スペース、末尾LF
- 0件は正確な `[]\n` で正常終了
- 同一入力はバイト単位で同一
- 入力不正と保存失敗で既存JSONを保持
- AI、ニュース検索、予測を行わない

- [ ] **Step 2: `plan.md` へ完了項目と実測欄を追加する**

入力契約、数値正規化、キー順、空配列、原子的保存、バイト一致を完了項目へ追加する。実測欄には入力候補CSV、出力JSON、件数、終了コード、正確なバイト列またはSHA-256、元CSV不変、全テスト件数を実行結果どおり記載する。

- [ ] **Step 3: 現在の候補CSVでCLIを2回実行する**

Run: `python prepare_analysis_input.py`

Expected for current `data/candidates_2026-07-30_2204.csv`: 終了コード0、`data/analysis_input_2026-07-30_2204.json`、表示に `分析入力0件`

同じコマンドをもう一度実行し、2回の出力SHA-256が一致することを確認する。

- [ ] **Step 4: 実ファイルの契約を検証する**

- 出力が正確に3バイト `5B 5D 0A`
- UTF-8 BOMなし
- LFのみ
- 元候補CSVのSHA-256が実行前後で一致
- JSONを標準JSONパーサーで読み、空配列になる

- [ ] **Step 5: 文法検査と全テストを実行する**

Run: `python -m py_compile fetch_markets.py select_candidates.py prepare_analysis_input.py`

Expected: 終了コード0

Run: `python -m unittest discover -s tests -v`

Expected: 全テスト成功、失敗0件

- [ ] **Step 6: 変更範囲を確認する**

Run: `git diff main...HEAD --name-status`

Expected: `fetch_markets.py`、`select_candidates.py`、`requirements.txt`、市場CSV、候補CSVが変更一覧にない

- [ ] **Step 7: 文書をコミットする**

```powershell
git add README.md plan.md
git commit -m "docs: document analysis input contract"
```

- [ ] **Step 8: 完了前検証を新規実行する**

Run: `python -m py_compile fetch_markets.py select_candidates.py prepare_analysis_input.py`

Run: `python -m unittest discover -s tests -v`

Run: `git status --short`

Expected: 文法検査成功、全テスト成功、追跡対象の未コミット変更なし
