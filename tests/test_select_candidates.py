import codecs
import csv
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import select_candidates


FETCHED_AT = datetime.fromisoformat("2026-07-30T12:00:00+09:00")


def make_row(
    *,
    market_id="1",
    question="General question?",
    description="Resolution rule\nSecond line",
    resolution_source="",
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
        "市場説明": description,
        "解決情報源": resolution_source,
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


def write_market_csv(
    data_dir,
    rows,
    name="markets_2026-07-30_1200.csv",
    fieldnames=None,
):
    path = Path(data_dir) / name
    fields = (
        list(select_candidates.INPUT_FIELDS)
        if fieldnames is None
        else fieldnames
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            lineterminator="\r\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


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

    def test_invalid_description_is_not_eligible_but_empty_source_is(self):
        rows = [
            make_row(market_id="blank", description="   "),
            make_row(
                market_id="valid",
                description="Rule",
                resolution_source="",
            ),
        ]

        result = select_candidates.prepare_candidates(rows, FETCHED_AT)

        self.assertEqual(["valid"], [item.market_id for item in result])


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

    def test_limits_output_to_ten(self):
        rows = [
            make_row(
                market_id=str(index),
                volume=str(1000 - index),
                url=f"https://polymarket.com/event/theme-{index}",
            )
            for index in range(12)
        ]

        selected = select_candidates.select_candidates(
            select_candidates.prepare_candidates(rows, FETCHED_AT)
        )

        self.assertEqual(10, len(selected))


class WorkflowTests(unittest.TestCase):
    def test_finds_latest_market_csv_by_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_market_csv(
                data_dir,
                [make_row()],
                name="markets_2026-07-29_2359.csv",
            )
            latest = write_market_csv(
                data_dir,
                [make_row()],
                name="markets_2026-07-30_1200.csv",
            )
            (data_dir / "candidates_2099-01-01_0000.csv").touch()

            self.assertEqual(
                latest,
                select_candidates.find_latest_markets_csv(data_dir),
            )

    def test_zero_candidates_writes_header_only_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            source = write_market_csv(
                data_dir,
                [make_row(yes="0.01")],
            )
            source_before = source.read_bytes()

            output, count = select_candidates.run(data_dir)

            self.assertEqual(0, count)
            self.assertTrue(output.read_bytes().startswith(codecs.BOM_UTF8))
            with output.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(
                    list(select_candidates.OUTPUT_FIELDS),
                    reader.fieldnames,
                )
                self.assertEqual([], list(reader))
            self.assertEqual(source_before, source.read_bytes())

    def test_propagates_resolution_metadata_to_fixed_fourteen_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            source = write_market_csv(
                data_dir,
                [
                    make_row(
                        description="Rule, one\n\"Rule two\"",
                        resolution_source="Official notice",
                    )
                ],
            )

            output, count = select_candidates.run(data_dir)

            self.assertEqual(1, count)
            with output.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
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
                    "カテゴリ",
                    "締切までの日数",
                    "選定理由",
                ],
                reader.fieldnames,
            )
            self.assertEqual("Rule, one\n\"Rule two\"", rows[0]["市場説明"])
            self.assertEqual("Official notice", rows[0]["解決情報源"])
            self.assertTrue(source.exists())

    def test_rejects_old_nine_column_market_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            old_fields = [
                "取得日時",
                "市場ID",
                "市場",
                "YES価格",
                "NO価格",
                "出来高",
                "流動性",
                "締切日",
                "URL",
            ]
            path = write_market_csv(
                data_dir,
                [make_row()],
                fieldnames=old_fields,
            )

            with self.assertRaisesRegex(ValueError, "市場説明.*解決情報源"):
                select_candidates.read_market_csv(path)

    def test_replace_failure_preserves_existing_candidate_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            path = data_dir / "candidates_2026-07-30_1200.csv"
            path.write_bytes(b"existing")
            candidate = select_candidates.prepare_candidates(
                [make_row()], FETCHED_AT
            )[0]

            with patch("os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    select_candidates.write_candidate_csv(path, [candidate])

            self.assertEqual(b"existing", path.read_bytes())
            self.assertEqual([], list(data_dir.glob(".*.tmp")))

    def test_rejects_inconsistent_acquisition_timestamps_without_output(self):
        rows = [
            make_row(
                market_id="1",
                fetched_at="2026-07-30T12:00:00+09:00",
            ),
            make_row(
                market_id="2",
                fetched_at="2026-07-30T03:00:00+00:00",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_market_csv(data_dir, rows)

            with self.assertRaisesRegex(ValueError, "取得日時が不統一"):
                select_candidates.run(data_dir)

            self.assertEqual(
                [],
                list(data_dir.glob("candidates_*.csv")),
            )

    def test_rejects_invalid_input_without_overwriting_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_market_csv(
                data_dir,
                [make_row(volume="not-a-number")],
            )
            existing = (
                data_dir / "candidates_2026-07-30_1200.csv"
            )
            existing.write_bytes(b"keep-this")

            with self.assertRaisesRegex(ValueError, "出来高"):
                select_candidates.run(data_dir)

            self.assertEqual(b"keep-this", existing.read_bytes())

    def test_rejects_missing_required_column(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            fields = [
                field
                for field in select_candidates.INPUT_FIELDS
                if field != "市場ID"
            ]
            write_market_csv(
                data_dir,
                [make_row()],
                fieldnames=fields,
            )

            with self.assertRaisesRegex(ValueError, "必須列"):
                select_candidates.run(data_dir)

    def test_rerun_produces_byte_identical_output(self):
        rows = [
            make_row(
                market_id="2",
                question="同順位B",
                volume="100",
            ),
            make_row(
                market_id="1",
                question="同順位A",
                volume="100",
            ),
            make_row(
                market_id="1",
                question="同順位A",
                volume="100",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_market_csv(data_dir, rows)

            first_path, first_count = select_candidates.run(data_dir)
            first_bytes = first_path.read_bytes()
            second_path, second_count = select_candidates.run(data_dir)

            self.assertEqual(2, first_count)
            self.assertEqual(first_count, second_count)
            self.assertEqual(first_path, second_path)
            self.assertEqual(first_bytes, second_path.read_bytes())

    def test_main_reports_zero_candidates_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_market_csv(data_dir, [make_row(yes="0.01")])
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch.object(select_candidates, "DATA_DIR", data_dir),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = select_candidates.main()

            self.assertEqual(0, exit_code)
            self.assertIn("候補0件", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())

    def test_main_reports_invalid_input_as_error(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_market_csv(data_dir, [make_row(fetched_at="no-date")])
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch.object(select_candidates, "DATA_DIR", data_dir),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = select_candidates.main()

            self.assertEqual(1, exit_code)
            self.assertEqual("", stdout.getvalue())
            self.assertIn("エラー:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
