import unittest
from datetime import datetime

import select_candidates


FETCHED_AT = datetime.fromisoformat("2026-07-30T12:00:00+09:00")


def make_row(
    *,
    market_id="1",
    question="General question?",
    yes="0.50",
    no="0.50",
    volume="100",
    liquidity="50",
    fetched_at="2026-07-30T12:00:00+09:00",
    deadline="2026-08-29T12:00:00+09:00",
    url=None,
):
    return {
        "取得日時": fetched_at,
        "市場ID": market_id,
        "市場": question,
        "YES価格": yes,
        "NO価格": no,
        "出来高": volume,
        "流動性": liquidity,
        "締切日": deadline,
        "URL": (
            f"https://polymarket.com/event/market-{market_id}"
            if url is None
            else url
        ),
    }


class NormalizationTests(unittest.TestCase):
    def test_normalizes_theme_url(self):
        self.assertEqual(
            "url:https://polymarket.com/Event/Alpha",
            select_candidates.normalize_theme_url(
                "HTTPS://PolyMarket.COM/Event/Alpha///?x=1#part",
                "42",
            ),
        )

    def test_preserves_explicit_port(self):
        self.assertEqual(
            "url:https://polymarket.com:8443/event/alpha",
            select_candidates.normalize_theme_url(
                "https://POLYMARKET.com:8443/event/alpha/",
                "42",
            ),
        )

    def test_invalid_or_missing_url_falls_back_to_market_id(self):
        for value in (
            "",
            "relative/path",
            "ftp://example.com/a",
            "https://user@example.com/a",
            "https://x:bad/a",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    "market:42",
                    select_candidates.normalize_theme_url(value, "42"),
                )


class CategoryTests(unittest.TestCase):
    def test_uses_first_matching_category_rule(self):
        self.assertEqual(
            "政治",
            select_candidates.categorize_market(
                "Will the presidential election affect Bitcoin?",
                "url:https://polymarket.com/event/example",
            ),
        )

    def test_matches_multiword_keyword_with_flexible_whitespace(self):
        self.assertEqual(
            "経済・金融",
            select_candidates.categorize_market(
                "Will the Federal   Reserve cut rates?",
                "url:https://polymarket.com/event/example",
            ),
        )

    def test_does_not_match_ai_inside_another_word(self):
        self.assertEqual(
            "その他",
            select_candidates.categorize_market(
                "Will it be said again?",
                "url:https://polymarket.com/event/example",
            ),
        )


class EligibilityTests(unittest.TestCase):
    def test_includes_exact_price_and_deadline_boundaries(self):
        rows = [
            make_row(
                market_id="1",
                yes="0.10",
                deadline="2026-08-06T12:00:00+09:00",
            ),
            make_row(
                market_id="2",
                yes="0.90",
                deadline="2026-10-28T12:00:00+09:00",
            ),
            make_row(
                market_id="3",
                yes="0.0999",
                deadline="2026-08-06T12:00:00+09:00",
            ),
            make_row(
                market_id="4",
                yes="0.9001",
                deadline="2026-08-06T12:00:00+09:00",
            ),
            make_row(
                market_id="5",
                yes="0.50",
                deadline="2026-08-06T11:59:59+09:00",
            ),
            make_row(
                market_id="6",
                yes="0.50",
                deadline="2026-10-28T12:00:01+09:00",
            ),
        ]

        result = select_candidates.prepare_candidates(rows, FETCHED_AT)

        self.assertEqual(["1", "2"], [item.market_id for item in result])
        self.assertEqual(
            ["7.00", "90.00"],
            [item.days_text for item in result],
        )

    def test_deadline_uses_csv_acquisition_time_not_execution_time(self):
        old_fetched_at = datetime.fromisoformat(
            "2020-01-01T00:00:00+00:00"
        )
        rows = [
            make_row(
                fetched_at="2020-01-01T00:00:00+00:00",
                deadline="2020-01-08T00:00:00+00:00",
            )
        ]

        result = select_candidates.prepare_candidates(rows, old_fetched_at)

        self.assertEqual(1, len(result))
        self.assertEqual("7.00", result[0].days_text)


class SelectionTests(unittest.TestCase):
    def test_stable_sort_deduplicates_market_id(self):
        rows = [
            make_row(market_id="2", volume="100", question="Zulu"),
            make_row(market_id="1", volume="100", question="Beta"),
            make_row(market_id="1", volume="100", question="Alpha"),
        ]

        prepared = select_candidates.prepare_candidates(rows, FETCHED_AT)
        selected = select_candidates.select_candidates(prepared)

        self.assertEqual(["1", "2"], [item.market_id for item in selected])
        self.assertEqual("Alpha", selected[0].source["市場"])

    def test_diversifies_category_then_theme_in_three_passes(self):
        rows = [
            make_row(
                market_id="1",
                question="Presidential election A?",
                volume="600",
                url="https://polymarket.com/event/politics-a",
            ),
            make_row(
                market_id="2",
                question="Presidential election B?",
                volume="590",
                url="https://polymarket.com/event/politics-b",
            ),
            make_row(
                market_id="3",
                question="Presidential election C?",
                volume="580",
                url="https://polymarket.com/event/politics-c",
            ),
            make_row(
                market_id="4",
                question="Bitcoin target?",
                volume="570",
                url="https://polymarket.com/event/crypto-a",
            ),
            make_row(
                market_id="5",
                question="OpenAI release?",
                volume="560",
                url="https://polymarket.com/event/tech-a",
            ),
            make_row(
                market_id="6",
                question="Microsoft AI release?",
                volume="550",
                url="HTTPS://POLYMARKET.COM/event/tech-a/?ref=duplicate",
            ),
        ]

        selected = select_candidates.select_candidates(
            select_candidates.prepare_candidates(rows, FETCHED_AT),
            limit=6,
        )

        self.assertEqual(["1", "2", "4", "5", "3", "6"], [
            item.market_id for item in selected
        ])
        self.assertEqual(
            [
                "価格・期限条件を満たし、出来高上位かつカテゴリ・テーマ分散",
                "価格・期限条件を満たし、テーマ分散を維持して補完",
                "価格・期限条件を満たし、出来高順で補完",
            ],
            list(dict.fromkeys(item.reason for item in selected)),
        )


if __name__ == "__main__":
    unittest.main()
