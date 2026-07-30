import codecs
import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def make_candidate(market_id, volume, token_ids):
    return {
        "market_id": market_id,
        "question": f"市場 {market_id}",
        "token_ids": token_ids,
        "volume": float(volume),
        "liquidity": 8_000.0,
        "end_date": "2026-12-31T00:00:00Z",
        "url": f"https://polymarket.com/event/market-{market_id}",
    }


def make_csv_row(question):
    return {
        "取得日時": "2026-07-30T20:00:00+09:00",
        "市場ID": "123",
        "市場": question,
        "YES価格": 0.42,
        "NO価格": 0.58,
        "出来高": 20_000.0,
        "流動性": 8_000.0,
        "締切日": "2026-12-31T00:00:00Z",
        "URL": "https://polymarket.com/event/sample-event",
    }


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
        self.assertEqual("volumeNum", session.calls[0]["params"]["order"])
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


class CsvOutputTests(unittest.TestCase):
    def test_deduplicates_sorts_and_requires_both_prices(self):
        fetched_at = datetime(
            2026,
            7,
            30,
            20,
            0,
            tzinfo=timezone(timedelta(hours=9), "JST"),
        )

        rows = fetch_markets.select_rows(
            [
                make_candidate("1", 20_000, ("y1", "n1")),
                make_candidate("1", 20_000, ("y1", "n1")),
                make_candidate("2", 30_000, ("y2", "n2")),
                make_candidate("3", 40_000, ("y3", "n3")),
            ],
            {
                "y1": 0.4,
                "n1": 0.6,
                "y2": 0.7,
                "n2": 0.3,
                "y3": 0.2,
            },
            fetched_at,
        )

        self.assertEqual(["2", "1"], [row["市場ID"] for row in rows])

    def test_limits_rows_to_requested_count(self):
        candidates = [
            make_candidate(str(index), 20_000 + index, (f"y{index}", f"n{index}"))
            for index in range(5)
        ]
        prices = {
            token_id: price
            for index in range(5)
            for token_id, price in (
                (f"y{index}", 0.4),
                (f"n{index}", 0.6),
            )
        }

        rows = fetch_markets.select_rows(
            candidates,
            prices,
            datetime(2026, 7, 30, tzinfo=timezone.utc),
            limit=3,
        )

        self.assertEqual(3, len(rows))
        self.assertEqual(["4", "3", "2"], [row["市場ID"] for row in rows])

    def test_writes_utf8_bom_and_preserves_non_ascii_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "markets.csv"

            fetch_markets.write_csv(
                [make_csv_row("日本語 – café")],
                path,
            )

            raw = path.read_bytes()
            self.assertTrue(raw.startswith(codecs.BOM_UTF8))
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual("日本語 – café", rows[0]["市場"])
            self.assertEqual(list(fetch_markets.CSV_FIELDS), list(rows[0]))

    def test_output_path_does_not_overwrite_same_minute_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            fetched_at = datetime(
                2026, 7, 30, 20, 0, 45, tzinfo=timezone.utc
            )

            first = fetch_markets.choose_output_path(data_dir, fetched_at)
            first.touch()
            second = fetch_markets.choose_output_path(data_dir, fetched_at)
            second.touch()
            third = fetch_markets.choose_output_path(data_dir, fetched_at)

            self.assertEqual("markets_2026-07-30_2000.csv", first.name)
            self.assertEqual("markets_2026-07-30_2000_45.csv", second.name)
            self.assertEqual("markets_2026-07-30_2000_45_1.csv", third.name)
            self.assertFalse(third.exists())


class CollectionWorkflowTests(unittest.TestCase):
    def test_collect_snapshot_writes_rows_from_public_api_payloads(self):
        second_market = make_market(
            id="456",
            question="Second market?",
            volumeNum=30_000,
            slug="second-market",
            events=[{"slug": "second-event", "tags": []}],
            clobTokenIds=json.dumps(["yes-2", "no-2"]),
        )
        session = FakeSession(
            [
                FakeResponse(200, [{"tags": "1,82"}]),
                FakeResponse(
                    200,
                    {"markets": [make_market(), second_market]},
                ),
                FakeResponse(
                    200,
                    {
                        "yes-token": "0.42",
                        "no-token": "0.58",
                        "yes-2": "0.7",
                        "no-2": "0.3",
                    },
                ),
            ]
        )
        fetched_at = datetime(2026, 7, 30, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as directory:
            path, count = fetch_markets.collect_snapshot(
                session, fetched_at, Path(directory)
            )

            self.assertEqual(2, count)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(["456", "123"], [row["市場ID"] for row in rows])

    def test_collect_snapshot_rejects_zero_eligible_markets(self):
        session = FakeSession(
            [
                FakeResponse(200, [{"tags": "1,82"}]),
                FakeResponse(
                    200,
                    {"markets": [make_market(active=False)]},
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                RuntimeError, "条件を満たす市場候補が0件"
            ):
                fetch_markets.collect_snapshot(
                    session,
                    datetime(2026, 7, 30, tzinfo=timezone.utc),
                    Path(directory),
                )

            self.assertEqual([], list(Path(directory).glob("*.csv")))


if __name__ == "__main__":
    unittest.main()
