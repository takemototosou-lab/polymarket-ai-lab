import csv
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


INPUT_FIELDS = (
    "取得日時",
    "市場ID",
    "市場",
    "市場説明",
    "解決情報源",
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
    "市場説明",
    "解決情報源",
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

DATA_DIR = Path(__file__).resolve().parent / "data"
MAX_MARKET_DESCRIPTION_CHARS = 262_144
MAX_RESOLUTION_SOURCE_CHARS = 32_768


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


def _validate_metadata_text(
    value: object,
    *,
    field: str,
    row_number: int,
    allow_empty: bool,
    max_chars: int,
) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{row_number}行目の{field}が不正です")
    if value != value.replace("\r\n", "\n").replace("\r", "\n").strip():
        raise ValueError(f"{row_number}行目の{field}が不正です")
    if not value and not allow_empty:
        raise ValueError(f"{row_number}行目の{field}が不正です")
    if (
        "\x00" in value
        or any("\ud800" <= character <= "\udfff" for character in value)
        or len(value) > max_chars
    ):
        raise ValueError(f"{row_number}行目の{field}が不正です")


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
        _validate_metadata_text(
            row["市場説明"],
            field="市場説明",
            row_number=row_number,
            allow_empty=False,
            max_chars=MAX_MARKET_DESCRIPTION_CHARS,
        )
        _validate_metadata_text(
            row["解決情報源"],
            field="解決情報源",
            row_number=row_number,
            allow_empty=True,
            max_chars=MAX_RESOLUTION_SOURCE_CHARS,
        )

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


def _json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def serialize_analysis_input(records: list[dict[str, str]]) -> bytes:
    if not records:
        return b"[]\n"

    lines = ["["]
    for record_index, record in enumerate(records):
        lines.append("  {")
        for key_index, key in enumerate(JSON_KEYS):
            value = (
                record[key] if key in NUMERIC_KEYS else _json_string(record[key])
            )
            comma = "," if key_index < len(JSON_KEYS) - 1 else ""
            lines.append(f"    {_json_string(key)}: {value}{comma}")
        object_comma = "," if record_index < len(records) - 1 else ""
        lines.append(f"  }}{object_comma}")
    lines.append("]")
    return ("\n".join(lines) + "\n").encode("utf-8")


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
