import csv
import tempfile
import unittest
from pathlib import Path

import prepare_analysis_input


FETCHED_AT = "2026-07-30T22:04:49.568055+09:00"


def make_candidate_row(
    *,
    market_id="1",
    market="市場A",
    fetched_at=FETCHED_AT,
    **overrides,
):
    row = {
        "取得日時": fetched_at,
        "市場ID": market_id,
        "市場": market,
        "YES価格": "0.10",
        "NO価格": "0.90",
        "出来高": "1000.00",
        "流動性": "500.50",
        "締切日": "2026-08-29T12:00:00Z",
        "URL": "https://polymarket.com/event/example",
        "カテゴリ": "その他",
        "締切までの日数": "30.00",
        "選定理由": "固定条件",
    }
    row.update(overrides)
    return row


def write_candidate_csv(
    data_dir: Path,
    rows: list[dict[str, str]],
    *,
    name="candidates_2026-07-30_2204.csv",
    fieldnames=None,
) -> Path:
    path = data_dir / name
    fields = (
        list(prepare_analysis_input.INPUT_FIELDS)
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


class CanonicalNumberTests(unittest.TestCase):
    def test_normalizes_finite_decimal_without_rounding(self):
        cases = {
            "0.10": "0.1",
            "+01.20": "1.2",
            "42.00": "42",
            "-0.00": "0",
            "1E+3": "1000",
            "1e-3": "0.001",
            "-001.2300": "-1.23",
        }

        for raw_value, expected in cases.items():
            with self.subTest(raw_value=raw_value):
                actual = prepare_analysis_input.canonical_json_number(
                    raw_value,
                    field="YES価格",
                    row_number=2,
                )
                self.assertEqual(actual, expected)

    def test_rejects_invalid_or_non_finite_decimal(self):
        for raw_value in (
            "",
            "not-number",
            "NaN",
            "sNaN",
            "Infinity",
            "-Infinity",
        ):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(ValueError, "YES価格"):
                    prepare_analysis_input.canonical_json_number(
                        raw_value,
                        field="YES価格",
                        row_number=2,
                    )


class InputContractTests(unittest.TestCase):
    def test_finds_last_candidate_filename_and_derives_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            older = data_dir / "candidates_2026-07-29_2359.csv"
            latest = data_dir / "candidates_2026-07-30_2204.csv"
            older.touch()
            latest.touch()

            self.assertEqual(
                latest,
                prepare_analysis_input.find_latest_candidate_csv(data_dir),
            )
            self.assertEqual(
                data_dir / "analysis_input_2026-07-30_2204.json",
                prepare_analysis_input.output_path_for(latest),
            )

    def test_rejects_invalid_selected_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates_2026-99-99_9999.csv"
            path.touch()

            with self.assertRaisesRegex(ValueError, "ファイル名"):
                prepare_analysis_input.output_path_for(path)

    def test_rejects_missing_required_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_candidate_csv(
                Path(directory),
                [make_candidate_row()],
                fieldnames=[
                    field
                    for field in prepare_analysis_input.INPUT_FIELDS
                    if field != "選定理由"
                ],
            )

            with self.assertRaisesRegex(ValueError, "必須列"):
                prepare_analysis_input.read_analysis_records(path)

    def test_rejects_inconsistent_acquisition_timestamps(self):
        rows = [
            make_candidate_row(
                market_id="1",
                fetched_at="2026-07-30T22:04:00+09:00",
            ),
            make_candidate_row(
                market_id="2",
                fetched_at="2026-07-30T13:04:00+00:00",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = write_candidate_csv(Path(directory), rows)

            with self.assertRaisesRegex(ValueError, "取得日時が不統一"):
                prepare_analysis_input.read_analysis_records(path)

    def test_rejects_invalid_or_timezone_naive_acquisition_time(self):
        for fetched_at in ("not-a-date", "2026-07-30T22:04:49"):
            with self.subTest(fetched_at=fetched_at):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_candidate_csv(
                        Path(directory),
                        [make_candidate_row(fetched_at=fetched_at)],
                    )

                    with self.assertRaisesRegex(ValueError, "取得日時"):
                        prepare_analysis_input.read_analysis_records(path)

    def test_rejects_blank_market_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_candidate_csv(
                Path(directory),
                [make_candidate_row(market_id=" ")],
            )

            with self.assertRaisesRegex(ValueError, "市場ID"):
                prepare_analysis_input.read_analysis_records(path)

    def test_preserves_row_order_and_raw_strings(self):
        rows = [
            {
                **make_candidate_row(market_id="2", market="市場B"),
                "追加列": "ignored",
            },
            {
                **make_candidate_row(market_id="1", market="市場A"),
                "追加列": "ignored",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = write_candidate_csv(
                Path(directory),
                rows,
                fieldnames=[*prepare_analysis_input.INPUT_FIELDS, "追加列"],
            )

            records = prepare_analysis_input.read_analysis_records(path)

            self.assertEqual(["2", "1"], [record["市場ID"] for record in records])
            self.assertEqual(["市場B", "市場A"], [record["市場"] for record in records])
            self.assertNotIn("追加列", records[0])
            self.assertEqual(FETCHED_AT, records[0]["分析基準日時"])


if __name__ == "__main__":
    unittest.main()
