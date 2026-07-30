"""市場スナップショットからAI分析候補を決定的に選別する。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit


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
)

MIN_YES_PRICE = Decimal("0.10")
MAX_YES_PRICE = Decimal("0.90")
MIN_DEADLINE_SECONDS = Decimal(7 * 86_400)
MAX_DEADLINE_SECONDS = Decimal(90 * 86_400)


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
        if any(
            _keyword_pattern(keyword).search(target)
            for keyword in keywords
        ):
            return category
    return "その他"


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


def prepare_candidates(
    rows: list[dict[str, str]],
    fetched_at: datetime,
) -> list[Candidate]:
    """適格行を正規化し、完全ソート後に市場IDで重複排除する。"""
    candidates: list[Candidate] = []
    for row_number, row in enumerate(rows, start=2):
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
