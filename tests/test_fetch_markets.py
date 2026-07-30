import json
import unittest
from datetime import datetime, timezone

import fetch_markets
import requests


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


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("Unexpected request")
        return self.responses.pop(0)


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


class ApiClientTests(unittest.TestCase):
    def test_retries_transient_status_then_returns_json(self):
        session = FakeSession(
            [
                FakeResponse(429, {}),
                FakeResponse(503, {}),
                FakeResponse(200, {"ok": True}),
            ]
        )
        waits = []

        result = fetch_markets.request_json(
            session,
            "GET",
            "https://example.test",
            sleep=waits.append,
        )

        self.assertEqual({"ok": True}, result)
        self.assertEqual([1.0, 2.0], waits)

    def test_does_not_retry_non_transient_http_error(self):
        session = FakeSession([FakeResponse(404, {})])

        with self.assertRaises(requests.HTTPError):
            fetch_markets.request_json(
                session,
                "GET",
                "https://example.test",
                sleep=lambda _: None,
            )

        self.assertEqual(1, len(session.calls))

    def test_fetches_all_sports_tag_ids(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    [
                        {"tags": "1,82"},
                        {"tags": "82,100639"},
                        {"tags": ""},
                    ],
                )
            ]
        )

        self.assertEqual(
            {"1", "82", "100639"},
            fetch_markets.fetch_sport_tag_ids(session),
        )

    def test_uses_next_cursor_for_market_pages(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {"markets": [{"id": "1"}], "next_cursor": "abc"},
                ),
                FakeResponse(200, {"markets": [{"id": "2"}]}),
            ]
        )

        pages = list(
            fetch_markets.iter_market_pages(
                session, datetime(2026, 7, 30, tzinfo=timezone.utc)
            )
        )

        self.assertEqual(
            [["1"], ["2"]],
            [[market["id"] for market in page] for page in pages],
        )
        self.assertEqual("abc", session.calls[1]["params"]["after_cursor"])

    def test_maps_valid_midpoints_and_skips_invalid_values(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "yes-token": "0.42",
                        "no-token": "0.58",
                        "bad-token": "2",
                        "text-token": "invalid",
                    },
                )
            ]
        )

        self.assertEqual(
            {"yes-token": 0.42, "no-token": 0.58},
            fetch_markets.fetch_midpoints(
                session,
                ["yes-token", "no-token", "bad-token", "text-token"],
            ),
        )

    def test_splits_failed_midpoint_batch_and_keeps_successful_tokens(self):
        session = FakeSession(
            [
                FakeResponse(400, {}),
                FakeResponse(200, {"yes-token": "0.42"}),
                FakeResponse(200, {"no-token": "0.58"}),
            ]
        )

        self.assertEqual(
            {"yes-token": 0.42, "no-token": 0.58},
            fetch_markets.fetch_midpoints(
                session, ["yes-token", "no-token"], batch_size=2
            ),
        )


if __name__ == "__main__":
    unittest.main()
