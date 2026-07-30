"""Polymarketの公開市場データを時刻付きCSVへ保存する。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import quote


MIN_VOLUME = 10_000.0
MIN_LIQUIDITY = 5_000.0
SPORTS_FIELDS = ("gameId", "gameStartTime", "sportsMarketType")


def parse_json_list(value: object) -> list[str]:
    """JSON文字列またはlistを文字列listへ変換する。"""
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list):
        raise ValueError("list required")
    return [str(item) for item in parsed]


def parse_iso_datetime(value: object) -> datetime:
    """ISO 8601日時をtimezone-awareなdatetimeとして解析する。"""
    if not isinstance(value, str) or not value:
        raise ValueError("ISO 8601 datetime required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed


def market_tags(market: dict[str, Any]) -> set[str]:
    """市場と関連イベントに付与されたタグIDを集める。"""
    tags: set[str] = set()
    tag_groups = [market.get("tags")]
    events = market.get("events")
    if isinstance(events, list):
        tag_groups.extend(
            event.get("tags")
            for event in events
            if isinstance(event, dict)
        )
    for group in tag_groups:
        if not isinstance(group, list):
            continue
        for tag in group:
            if isinstance(tag, dict) and tag.get("id") is not None:
                tags.add(str(tag["id"]))
    return tags


def market_url(market: dict[str, Any]) -> str:
    """イベントslugを優先してPolymarket URLを組み立てる。"""
    events = market.get("events")
    slug = None
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and event.get("slug"):
                slug = str(event["slug"])
                break
    if not slug:
        slug = str(market["slug"])
    return f"https://polymarket.com/event/{quote(slug, safe='-')}"


def normalize_market(
    market: dict[str, Any], sport_tag_ids: set[str], now: datetime
) -> dict[str, Any] | None:
    """保存条件を満たす市場を内部形式へ正規化する。"""
    try:
        outcomes = [
            outcome.casefold() for outcome in parse_json_list(market["outcomes"])
        ]
        token_ids = parse_json_list(market["clobTokenIds"])
        end_date = parse_iso_datetime(market["endDate"])
        volume = float(market.get("volumeNum") or market["volume"])
        liquidity = float(
            market.get("liquidityNum") or market["liquidity"]
        )
        if (
            market.get("active") is not True
            or market.get("closed") is not False
            or market.get("acceptingOrders") is not True
            or end_date <= now
            or volume < MIN_VOLUME
            or liquidity < MIN_LIQUIDITY
            or outcomes != ["yes", "no"]
            or len(token_ids) != 2
            or bool(market_tags(market) & sport_tag_ids)
            or any(market.get(field) for field in SPORTS_FIELDS)
        ):
            return None
        return {
            "market_id": str(market["id"]),
            "question": str(market["question"]),
            "token_ids": (token_ids[0], token_ids[1]),
            "volume": volume,
            "liquidity": liquidity,
            "end_date": str(market["endDate"]),
            "url": market_url(market),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
