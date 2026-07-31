import codecs
import csv
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import fetch_markets
import requests


class ShortWriteHandle:
    def __init__(self, descriptor):
        self.descriptor = descriptor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        os.close(self.descriptor)

    def write(self, payload):
        written = max(1, len(payload) // 2)
        os.write(self.descriptor, payload[:written])
        return written

    def flush(self):
        pass

    def fileno(self):
        return self.descriptor


def make_market(**overrides):
    market = {
        "id": "123",
        "question": "日本語を含む市場?",
        "description": "This market resolves according to official rules.",
        "resolutionSource": "https://example.com/source",
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
        "description": f"Rule for {market_id}\nSecond line",
        "resolution_source": "Official source",
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
        "市場説明": "Rule, one\n\"Rule two\"",
        "解決情報源": "",
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

    def test_normalizes_and_preserves_resolution_metadata(self):
        candidate = fetch_markets.normalize_market(
            make_market(
                description="  Rule one.\r\n<b>Rule two</b>.\r  ",
                resolutionSource="  Official notice\r\nArchive  ",
            ),
            set(),
            self.now,
        )

        self.assertEqual(
            "Rule one.\n<b>Rule two</b>.",
            candidate.get("description"),
        )
        self.assertEqual(
            "Official notice\nArchive",
            candidate.get("resolution_source"),
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

    def test_rejects_invalid_description_and_resolution_source(self):
        invalid_markets = [
            make_market(description=None),
            make_market(description="  \r\n  "),
            make_market(description=123),
            make_market(description="bad\x00text"),
            make_market(description="bad\ud800text"),
            make_market(
                description=(
                    "x"
                    * (fetch_markets.MAX_MARKET_DESCRIPTION_CHARS + 1)
                )
            ),
            make_market(resolutionSource=123),
            make_market(resolutionSource="bad\x00text"),
            make_market(
                resolutionSource=(
                    "x" * (fetch_markets.MAX_RESOLUTION_SOURCE_CHARS + 1)
                )
            ),
        ]

        for market in invalid_markets:
            with self.subTest(market_id=market["id"]):
                self.assertIsNone(
                    fetch_markets.normalize_market(market, set(), self.now)
                )

    def test_defaults_missing_or_null_resolution_source_to_empty(self):
        missing = make_market()
        del missing["resolutionSource"]

        for market in (missing, make_market(resolutionSource=None)):
            with self.subTest(keys=tuple(market)):
                candidate = fetch_markets.normalize_market(
                    market,
                    set(),
                    self.now,
                )
                self.assertEqual("", candidate["resolution_source"])


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

    def test_collect_candidates_reports_metadata_rejection_counts_only(self):
        secret_description = "do-not-log-description"
        secret_source = "do-not-log-source"
        session = FakeSession(
            [
                FakeResponse(200, [{"tags": "1,82"}]),
                FakeResponse(
                    200,
                    {
                        "markets": [
                            make_market(
                                id="1",
                                description=None,
                                question=secret_description,
                            ),
                            make_market(
                                id="2",
                                resolutionSource=123,
                                question=secret_source,
                            ),
                            make_market(id="3"),
                        ]
                    },
                ),
            ]
        )
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            candidates = fetch_markets.collect_candidates(
                session,
                datetime(2026, 7, 30, tzinfo=timezone.utc),
            )

        self.assertEqual(["3"], [item["market_id"] for item in candidates])
        self.assertIn("市場説明=1", stderr.getvalue())
        self.assertIn("解決情報源=1", stderr.getvalue())
        self.assertNotIn(secret_description, stderr.getvalue())
        self.assertNotIn(secret_source, stderr.getvalue())


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
            self.assertEqual("Rule, one\n\"Rule two\"", rows[0]["市場説明"])
            self.assertEqual("", rows[0]["解決情報源"])
            self.assertEqual(list(fetch_markets.CSV_FIELDS), list(rows[0]))

    def test_select_rows_propagates_resolution_metadata(self):
        candidate = make_candidate("1", 20_000, ("yes", "no"))

        rows = fetch_markets.select_rows(
            [candidate],
            {"yes": 0.4, "no": 0.6},
            datetime(2026, 7, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(candidate["description"], rows[0].get("市場説明"))
        self.assertEqual(
            candidate["resolution_source"],
            rows[0].get("解決情報源"),
        )
        self.assertEqual(
            [
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
            ],
            list(fetch_markets.CSV_FIELDS),
        )

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

    def test_link_failure_does_not_publish_partial_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "markets.csv"

            with patch("os.link", side_effect=OSError("link failed")):
                with self.assertRaisesRegex(OSError, "link failed"):
                    fetch_markets.write_csv([make_csv_row("Market?")], path)

            self.assertFalse(path.exists())
            self.assertEqual([], list(path.parent.glob(".*.tmp")))

    def test_concurrent_creation_reselects_path_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            fetched_at = datetime(
                2026, 7, 30, 20, 0, 45, tzinfo=timezone.utc
            )
            first_path = data_dir / "markets_2026-07-30_2000.csv"
            competing_payload = b"competing snapshot"
            real_link = os.link
            link_calls = 0

            def create_competing_file_then_link(source, destination):
                nonlocal link_calls
                link_calls += 1
                if link_calls == 1:
                    Path(destination).write_bytes(competing_payload)
                real_link(source, destination)

            with (
                patch(
                    "fetch_markets.collect_candidates",
                    return_value=[make_candidate("1", 20_000, ("yes", "no"))],
                ),
                patch(
                    "fetch_markets.fetch_midpoints",
                    return_value={"yes": 0.4, "no": 0.6},
                ),
                patch(
                    "fetch_markets.os.link",
                    side_effect=create_competing_file_then_link,
                ),
            ):
                output_path, count = fetch_markets.collect_snapshot(
                    object(), fetched_at, data_dir
                )

            self.assertEqual(1, count)
            self.assertEqual(competing_payload, first_path.read_bytes())
            self.assertEqual(
                "markets_2026-07-30_2000_45.csv",
                output_path.name,
            )
            self.assertTrue(output_path.read_bytes().startswith(codecs.BOM_UTF8))
            self.assertEqual([], list(data_dir.glob(".*.tmp")))

    def test_short_write_does_not_publish_or_leave_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "markets.csv"

            with patch(
                "fetch_markets.os.fdopen",
                side_effect=lambda descriptor, mode: ShortWriteHandle(descriptor),
            ):
                with self.assertRaisesRegex(OSError, "全バイト"):
                    fetch_markets.write_csv([make_csv_row("Market?")], path)

            self.assertFalse(path.exists())
            self.assertEqual([], list(path.parent.glob(".*.tmp")))

    def test_existing_snapshot_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "markets.csv"
            path.write_bytes(b"existing snapshot")

            with self.assertRaises(FileExistsError):
                fetch_markets.write_csv([make_csv_row("Market?")], path)

            self.assertEqual(b"existing snapshot", path.read_bytes())
            self.assertEqual([], list(path.parent.glob(".*.tmp")))


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
