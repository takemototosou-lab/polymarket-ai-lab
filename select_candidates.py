"""市場スナップショットからAI分析候補を決定的に選別する。"""

from __future__ import annotations

import csv
import io
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit, urlunsplit


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
)
OUTPUT_FIELDS = INPUT_FIELDS + (
    "カテゴリ",
    "締切までの日数",
    "選定理由",
)
NUMERIC_FIELDS = ("YES価格", "NO価格", "出来高", "流動性")
DATA_DIR = Path(__file__).resolve().parent / "data"

CATEGORY_RULES = (
    (
        "政治",
        (
            "election",
            "president",
            "presidential",
            "nominee",
            "nomination",
            "congress",
            "senate",
            "governor",
            "prime minister",
            "parliament",
            "vote",
        ),
    ),
    (
        "国際情勢",
        (
            "war",
            "invasion",
            "invade",
            "ceasefire",
            "iran",
            "israel",
            "ukraine",
            "russia",
            "china",
            "taiwan",
            "nato",
            "greenland",
        ),
    ),
    (
        "暗号資産",
        (
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "crypto",
            "solana",
            "token",
        ),
    ),
    (
        "経済・金融",
        (
            "federal reserve",
            "fed",
            "interest rate",
            "inflation",
            "recession",
            "gdp",
            "unemployment",
            "stock",
            "s&p",
            "nasdaq",
            "market cap",
        ),
    ),
    (
        "テクノロジー",
        (
            "artificial intelligence",
            "openai",
            "spacex",
            "tesla",
            "iphone",
            "apple",
            "google",
            "microsoft",
            "ai",
        ),
    ),
    (
        "エンタメ",
        (
            "album",
            "movie",
            "film",
            "box office",
            "gta",
            "game",
            "oscar",
            "grammy",
            "music",
        ),
    ),
    (
        "科学・健康",
        (
            "nasa",
            "alien",
            "vaccine",
            "disease",
            "covid",
            "health",
            "drug",
            "medicine",
        ),
    ),
    ("その他", ()),
)

MIN_YES_PRICE = Decimal("0.10")
MAX_YES_PRICE = Decimal("0.90")
MIN_DEADLINE_SECONDS = Decimal(7 * 86_400)
MAX_DEADLINE_SECONDS = Decimal(90 * 86_400)
MAX_MARKET_DESCRIPTION_CHARS = 262_144
MAX_RESOLUTION_SOURCE_CHARS = 32_768


@dataclass
class Candidate:
    """選別に必要な正規化済み市場データ。"""

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


def normalize_theme_url(value: str, market_id: str) -> str:
    """URLをテーマ識別子へ正規化し、不正時は市場IDへフォールバックする。"""
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.casefold()
        if (
            scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("invalid market URL")
        host = parsed.hostname.casefold()
        if ":" in host:
            host = f"[{host}]"
        port = parsed.port
        netloc = host if port is None else f"{host}:{port}"
        normalized = urlunsplit(
            (scheme, netloc, parsed.path.rstrip("/"), "", "")
        )
        return f"url:{normalized}"
    except (TypeError, ValueError):
        return f"market:{market_id}"


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in keyword.split()]
    return re.compile(
        r"(?<![0-9a-z])" + r"\s+".join(parts) + r"(?![0-9a-z])"
    )


def categorize_market(question: str, normalized_theme: str) -> str:
    """固定優先順位の最初に一致したカテゴリを返す。"""
    target = f"{question} {normalized_theme}".casefold()
    for category, keywords in CATEGORY_RULES:
        if not keywords or any(
            _keyword_pattern(keyword).search(target)
            for keyword in keywords
        ):
            return category
    raise RuntimeError("カテゴリ規則に既定値がありません")


def parse_iso_datetime(value: str) -> datetime:
    """タイムゾーン付きISO 8601日時を解析する。"""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("日時にはタイムゾーンが必要です")
    return parsed


def _timedelta_seconds(value: timedelta) -> Decimal:
    return (
        Decimal(value.days * 86_400 + value.seconds)
        + Decimal(value.microseconds) / Decimal(1_000_000)
    )


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


def _valid_metadata_text(
    value: object,
    *,
    allow_empty: bool,
    max_chars: int,
) -> bool:
    if not isinstance(value, str):
        return False
    if value != value.replace("\r\n", "\n").replace("\r", "\n").strip():
        return False
    if not value and not allow_empty:
        return False
    if "\x00" in value or any(
        "\ud800" <= character <= "\udfff" for character in value
    ):
        return False
    return len(value) <= max_chars


def prepare_candidates(
    rows: list[dict[str, str]],
    fetched_at: datetime,
) -> list[Candidate]:
    """適格行を正規化し、完全ソート後に市場IDで重複排除する。"""
    candidates: list[Candidate] = []
    for row_number, row in enumerate(rows, start=2):
        if not _valid_metadata_text(
            row.get("市場説明"),
            allow_empty=False,
            max_chars=MAX_MARKET_DESCRIPTION_CHARS,
        ) or not _valid_metadata_text(
            row.get("解決情報源"),
            allow_empty=True,
            max_chars=MAX_RESOLUTION_SOURCE_CHARS,
        ):
            continue
        yes_price = Decimal(row["YES価格"])
        deadline = parse_iso_datetime(row["締切日"])
        deadline_seconds = _timedelta_seconds(deadline - fetched_at)
        if (
            yes_price < MIN_YES_PRICE
            or yes_price > MAX_YES_PRICE
            or deadline_seconds < MIN_DEADLINE_SECONDS
            or deadline_seconds > MAX_DEADLINE_SECONDS
        ):
            continue
        market_id = row["市場ID"]
        theme = normalize_theme_url(row["URL"], market_id)
        days = deadline_seconds / Decimal(86_400)
        candidates.append(
            Candidate(
                source=row,
                row_number=row_number,
                market_id=market_id,
                volume=Decimal(row["出来高"]),
                liquidity=Decimal(row["流動性"]),
                yes_price=yes_price,
                no_price=Decimal(row["NO価格"]),
                theme=theme,
                category=categorize_market(row["市場"], theme),
                days=days,
                days_text=f"{days.quantize(Decimal('0.01')):.2f}",
            )
        )

    result: list[Candidate] = []
    seen_market_ids: set[str] = set()
    for item in sorted(candidates, key=_sort_key):
        if item.market_id in seen_market_ids:
            continue
        seen_market_ids.add(item.market_id)
        result.append(item)
    return result


def select_candidates(
    candidates: list[Candidate],
    limit: int = 10,
) -> list[Candidate]:
    """カテゴリ・テーマの分散規則で候補を最大limit件選ぶ。"""
    selected: list[Candidate] = []
    selected_ids: set[str] = set()
    theme_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    passes = (
        (
            2,
            1,
            "価格・期限条件を満たし、出来高上位かつカテゴリ・テーマ分散",
        ),
        (
            None,
            1,
            "価格・期限条件を満たし、テーマ分散を維持して補完",
        ),
        (
            None,
            None,
            "価格・期限条件を満たし、出来高順で補完",
        ),
    )
    for category_limit, theme_limit, reason in passes:
        for item in candidates:
            if item.market_id in selected_ids:
                continue
            if (
                category_limit is not None
                and category_counts[item.category] >= category_limit
            ):
                continue
            if (
                theme_limit is not None
                and theme_counts[item.theme] >= theme_limit
            ):
                continue
            item.reason = reason
            selected.append(item)
            selected_ids.add(item.market_id)
            category_counts[item.category] += 1
            theme_counts[item.theme] += 1
            if len(selected) == limit:
                return selected
    return selected


def find_latest_markets_csv(data_dir: Path) -> Path:
    """ファイル名順で最新の市場スナップショットを返す。"""
    paths = sorted(
        (
            path
            for path in data_dir.glob("markets_*.csv")
            if path.is_file()
        ),
        key=lambda path: path.name,
    )
    if not paths:
        raise ValueError("入力となるmarkets CSVがありません")
    return paths[-1]


def _validated_decimal(
    value: object,
    field: str,
    row_number: int,
) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(
            f"{row_number}行目の{field}が不正です"
        ) from exc
    if not parsed.is_finite():
        raise ValueError(f"{row_number}行目の{field}が不正です")
    return parsed


def _validated_datetime(
    value: object,
    field: str,
    row_number: int,
) -> datetime:
    try:
        return parse_iso_datetime(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(
            f"{row_number}行目の{field}が不正です"
        ) from exc


def read_market_csv(
    path: Path,
) -> tuple[list[dict[str, str]], datetime]:
    """市場CSV全体を検証し、行と統一取得日時を返す。"""
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
        raise ValueError("入力CSVに市場データがありません")

    fetched_values = {row["取得日時"] for row in rows}
    if len(fetched_values) != 1:
        raise ValueError("元CSV内の取得日時が不統一です")

    fetched_at = _validated_datetime(
        rows[0]["取得日時"],
        "取得日時",
        2,
    )
    for row_number, row in enumerate(rows, start=2):
        market_id = row["市場ID"]
        if not isinstance(market_id, str) or not market_id.strip():
            raise ValueError(f"{row_number}行目の市場IDが空です")
        for field in NUMERIC_FIELDS:
            _validated_decimal(row[field], field, row_number)
        _validated_datetime(row["締切日"], "締切日", row_number)
    return rows, fetched_at


def candidate_output_path(
    data_dir: Path,
    fetched_at: datetime,
) -> Path:
    """入力取得日時から決定的な候補CSVパスを返す。"""
    return data_dir / fetched_at.strftime(
        "candidates_%Y-%m-%d_%H%M.csv"
    )


def _write_and_sync(handle: BinaryIO, payload: bytes) -> None:
    written = handle.write(payload)
    if written != len(payload):
        raise OSError("一時ファイルへ全バイトを書き込めません")
    handle.flush()
    os.fsync(handle.fileno())


def write_candidate_csv(
    path: Path,
    selected: list[Candidate],
) -> None:
    """候補をUTF-8 BOM付き、固定列順、固定改行で上書き保存する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = io.StringIO(newline="")
    writer = csv.DictWriter(
        text,
        fieldnames=OUTPUT_FIELDS,
        lineterminator="\r\n",
    )
    writer.writeheader()
    for item in selected:
        writer.writerow(
            {
                **{
                    field: item.source[field]
                    for field in INPUT_FIELDS
                },
                "カテゴリ": item.category,
                "締切までの日数": item.days_text,
                "選定理由": item.reason,
            }
        )
    payload = text.getvalue().encode("utf-8-sig")

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


def run(data_dir: Path) -> tuple[Path, int]:
    """最新市場CSVを読み、候補CSVを生成する。"""
    input_path = find_latest_markets_csv(data_dir)
    rows, fetched_at = read_market_csv(input_path)
    prepared = prepare_candidates(rows, fetched_at)
    selected = select_candidates(prepared)
    output_path = candidate_output_path(data_dir, fetched_at)
    write_candidate_csv(output_path, selected)
    return output_path, len(selected)


def main() -> int:
    """コマンドライン実行入口。"""
    try:
        output_path, count = run(DATA_DIR)
    except (csv.Error, OSError, UnicodeError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    print(f"{output_path} に候補{count}件を保存しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
