import codecs
import json
import re
from datetime import datetime
from decimal import Decimal
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
            raise ValueError(f"{element_number}番目の市場IDが空です")
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
