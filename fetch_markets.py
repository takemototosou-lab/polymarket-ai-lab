"""Polymarketの公開市場データを時刻付きCSVへ保存する。"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests


GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
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


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> object:
    """一時的な通信障害を再試行し、JSON応答を返す。"""
    for attempt in range(3):
        try:
            response = session.request(
                method, url, timeout=20, **kwargs
            )
            response.raise_for_status()
            return response.json()
        except (
            requests.Timeout,
            requests.ConnectionError,
            requests.HTTPError,
        ) as exc:
            response = getattr(exc, "response", None)
            status = response.status_code if response is not None else 0
            retryable = status in (0, 429) or status >= 500
            if not retryable or attempt == 2:
                raise
            sleep(float(2**attempt))
    raise RuntimeError("unreachable")


def fetch_sport_tag_ids(session: requests.Session) -> set[str]:
    """Gamma APIから全スポーツタグIDを取得する。"""
    payload = request_json(session, "GET", f"{GAMMA_BASE}/sports")
    if not isinstance(payload, list):
        raise ValueError("sports response must be a list")
    return {
        tag.strip()
        for sport in payload
        if isinstance(sport, dict)
        for tag in str(sport.get("tags", "")).split(",")
        if tag.strip()
    }


def iter_market_pages(
    session: requests.Session, now: datetime
) -> Iterator[list[dict[str, Any]]]:
    """keyset paginationで市場ページを順に返す。"""
    params: dict[str, object] = {
        "limit": 100,
        "closed": "false",
        "order": "volume_num",
        "ascending": "false",
        "volume_num_min": MIN_VOLUME,
        "liquidity_num_min": MIN_LIQUIDITY,
        "end_date_min": now.astimezone(timezone.utc).isoformat(),
        "include_tag": "true",
    }
    page_index = 0
    while True:
        try:
            payload = request_json(
                session,
                "GET",
                f"{GAMMA_BASE}/markets/keyset",
                params=params,
            )
        except requests.RequestException as exc:
            if page_index == 0:
                raise
            print(
                f"警告: 市場ページ取得を途中終了します: {exc}",
                file=sys.stderr,
            )
            return
        if not isinstance(payload, dict):
            raise ValueError("markets response must be an object")
        markets = payload.get("markets")
        if not isinstance(markets, list):
            raise ValueError("markets response must contain a list")
        yield markets
        page_index += 1
        cursor = payload.get("next_cursor")
        if not cursor:
            return
        params = {**params, "after_cursor": str(cursor)}


def _parse_midpoints(payload: object) -> dict[str, float]:
    if not isinstance(payload, dict):
        return {}
    result: dict[str, float] = {}
    for token_id, raw_price in payload.items():
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        if 0.0 <= price <= 1.0:
            result[str(token_id)] = price
    return result


def _fetch_midpoint_batch(
    session: requests.Session, token_ids: list[str]
) -> dict[str, float]:
    try:
        payload = request_json(
            session,
            "POST",
            f"{CLOB_BASE}/midpoints",
            json=[{"token_id": token_id} for token_id in token_ids],
        )
        return _parse_midpoints(payload)
    except requests.RequestException:
        if len(token_ids) == 1:
            print(
                f"警告: CLOB価格を取得できません: {token_ids[0]}",
                file=sys.stderr,
            )
            return {}
        middle = len(token_ids) // 2
        return {
            **_fetch_midpoint_batch(session, token_ids[:middle]),
            **_fetch_midpoint_batch(session, token_ids[middle:]),
        }


def fetch_midpoints(
    session: requests.Session,
    token_ids: list[str],
    batch_size: int = 50,
) -> dict[str, float]:
    """CLOB midpointをバッチ取得し、有効価格だけ返す。"""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    result: dict[str, float] = {}
    for start in range(0, len(token_ids), batch_size):
        result.update(
            _fetch_midpoint_batch(
                session, token_ids[start : start + batch_size]
            )
        )
    return result
