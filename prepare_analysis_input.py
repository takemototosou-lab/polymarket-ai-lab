import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


INPUT_FIELDS = (
    "取得日時",
    "市場ID",
    "市場",
    "YES価格",
    "NO価格",
    "出来高",
    "流動性",
    "締切日",
    "URL",
    "カテゴリ",
    "締切までの日数",
    "選定理由",
)

JSON_KEYS = (
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

NUMERIC_KEYS = frozenset(
    (
        "YES価格",
        "NO価格",
        "出来高",
        "流動性",
        "締切までの日数",
    )
)

CANDIDATE_NAME = re.compile(
    r"^candidates_(\d{4}-\d{2}-\d{2})_(\d{4})\.csv$"
)

SOURCE_KEY_BY_JSON_KEY = {
    **{key: key for key in JSON_KEYS},
    "分析基準日時": "取得日時",
}


def canonical_json_number(raw_value: str, *, field: str, row_number: int) -> str:
    try:
        value = Decimal(raw_value)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{row_number}行目の{field}が不正です") from None

    if not value.is_finite():
        raise ValueError(f"{row_number}行目の{field}が不正です")

    if value.is_zero():
        return "0"

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


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
    except ValueError:
        raise ValueError("候補CSVのファイル名が不正です") from None

    suffix = f"{match.group(1)}_{match.group(2)}"
    return input_path.with_name(f"analysis_input_{suffix}.json")


def _validate_fetched_at(raw_value: str) -> None:
    try:
        value = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise ValueError("取得日時が不正です") from None

    if value.tzinfo is None:
        raise ValueError("取得日時にはタイムゾーンが必要です")


def read_analysis_records(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", errors="strict", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [field for field in INPUT_FIELDS if field not in fieldnames]
        if missing:
            raise ValueError(f"必須列が不足しています: {', '.join(missing)}")
        rows = list(reader)

    if not rows:
        return []

    fetched_values = {row["取得日時"] for row in rows}
    if len(fetched_values) != 1:
        raise ValueError("CSV内の取得日時が不統一です")
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
                canonical_json_number(
                    raw_value,
                    field=source_key,
                    row_number=row_number,
                )
                if key in NUMERIC_KEYS
                else raw_value
            )
        records.append(record)

    return records
