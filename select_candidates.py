"""市場スナップショットからAI分析候補を決定的に選別する。"""

from __future__ import annotations

import re
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
