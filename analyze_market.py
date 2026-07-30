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
