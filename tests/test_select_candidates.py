import unittest

import select_candidates


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


if __name__ == "__main__":
    unittest.main()
