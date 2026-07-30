import json
import unittest
from datetime import datetime, timezone

import fetch_markets


def make_market(**overrides):
    market = {
        "id": "123",
        "question": "日本語を含む市場?",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "endDate": "2026-12-31T00:00:00Z",
        "volumeNum": 20_000,
        "liquidityNum": 8_000,
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps(["yes-token", "no-token"]),
        "slug": "sample-market",
        "tags": [],
        "events": [{"slug": "sample-event", "tags": []}],
        "gameId": None,
        "gameStartTime": None,
        "sportsMarketType": None,
    }
    market.update(overrides)
    return market


class NormalizeMarketTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 30, tzinfo=timezone.utc)

    def test_accepts_complete_non_sports_yes_no_market(self):
        candidate = fetch_markets.normalize_market(
            make_market(), {"1", "82"}, self.now
        )

        self.assertEqual("123", candidate["market_id"])
        self.assertEqual(("yes-token", "no-token"), candidate["token_ids"])
        self.assertEqual(
            "https://polymarket.com/event/sample-event", candidate["url"]
        )

    def test_rejects_sports_tag(self):
        market = make_market(tags=[{"id": "82"}])

        self.assertIsNone(
            fetch_markets.normalize_market(market, {"82"}, self.now)
        )

    def test_rejects_sports_specific_field(self):
        market = make_market(sportsMarketType="moneyline")

        self.assertIsNone(
            fetch_markets.normalize_market(market, set(), self.now)
        )

    def test_rejects_missing_or_invalid_market_without_raising(self):
        self.assertIsNone(
            fetch_markets.normalize_market({"id": "broken"}, set(), self.now)
        )

    def test_rejects_ineligible_market_conditions(self):
        invalid_markets = [
            make_market(active=False),
            make_market(closed=True),
            make_market(acceptingOrders=False),
            make_market(endDate="2026-07-01T00:00:00Z"),
            make_market(volumeNum=9_999),
            make_market(liquidityNum=4_999),
            make_market(outcomes=json.dumps(["Up", "Down"])),
            make_market(clobTokenIds=json.dumps(["only-one-token"])),
        ]

        for market in invalid_markets:
            with self.subTest(market=market):
                self.assertIsNone(
                    fetch_markets.normalize_market(market, set(), self.now)
                )


if __name__ == "__main__":
    unittest.main()
