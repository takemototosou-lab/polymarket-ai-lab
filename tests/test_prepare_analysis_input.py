import codecs
import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

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
        "市場説明": "Resolution rule\nSecond line",
        "解決情報源": "",
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


def make_analysis_record(*, market_id="1") -> dict[str, str]:
    return {
        "市場ID": market_id,
        "市場": "市場A",
        "市場説明": "Resolution rule\nSecond line",
        "解決情報源": "",
        "YES価格": "0.1",
        "NO価格": "0.9",
        "出来高": "1000",
        "流動性": "500.5",
        "締切日": "2026-08-29T12:00:00Z",
        "カテゴリ": "その他",
        "締切までの日数": "30",
        "URL": "https://polymarket.com/event/example",
        "分析基準日時": FETCHED_AT,
        "選定理由": "固定条件",
    }


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

    def test_propagates_multiline_resolution_metadata_to_fourteen_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_candidate_csv(
                Path(directory),
                [
                    make_candidate_row(
                        **{
                            "市場説明": "Rule, one\n\"Rule two\"",
                            "解決情報源": "",
                        }
                    )
                ],
            )

            records = prepare_analysis_input.read_analysis_records(path)
            payload = prepare_analysis_input.serialize_analysis_input(records)
            parsed = json.loads(payload)

            self.assertEqual(
                "Rule, one\n\"Rule two\"",
                parsed[0].get("市場説明"),
            )
            self.assertEqual("", parsed[0].get("解決情報源"))
            self.assertEqual(
                [
                    "市場ID",
                    "市場",
                    "市場説明",
                    "解決情報源",
                    "YES価格",
                    "NO価格",
                    "出来高",
                    "流動性",
                    "締切日",
                    "カテゴリ",
                    "締切までの日数",
                    "URL",
                    "分析基準日時",
                    "選定理由",
                ],
                list(prepare_analysis_input.JSON_KEYS),
            )

    def test_rejects_old_twelve_column_candidate_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            old_fields = [
                field
                for field in prepare_analysis_input.INPUT_FIELDS
                if field not in {"市場説明", "解決情報源"}
            ]
            path = write_candidate_csv(
                Path(directory),
                [make_candidate_row()],
                fieldnames=old_fields,
            )

            with self.assertRaisesRegex(ValueError, "市場説明.*解決情報源"):
                prepare_analysis_input.read_analysis_records(path)

    def test_rejects_invalid_description_but_accepts_empty_resolution_source(self):
        invalid_descriptions = ("", "   ", "bad\rtext", "bad\x00text")
        for description in invalid_descriptions:
            with self.subTest(description=repr(description)):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_candidate_csv(
                        Path(directory),
                        [make_candidate_row(**{"市場説明": description})],
                    )
                    with self.assertRaisesRegex(ValueError, "市場説明"):
                        prepare_analysis_input.read_analysis_records(path)


class SerializationTests(unittest.TestCase):
    def test_empty_array_is_exactly_three_bytes(self):
        self.assertEqual(
            b"[]\n",
            prepare_analysis_input.serialize_analysis_input([]),
        )

    def test_serializes_fixed_layout_without_bom(self):
        record = {
            "市場ID": "2",
            "市場": '日本語 "quote" \\ path\nnext',
            "市場説明": "Rule\nSecond line",
            "解決情報源": "",
            "YES価格": "0.1",
            "NO価格": "0.9",
            "出来高": "1000",
            "流動性": "500.5",
            "締切日": "2026-08-29T12:00:00Z",
            "カテゴリ": "その他",
            "締切までの日数": "30",
            "URL": "https://example.test/a",
            "分析基準日時": FETCHED_AT,
            "選定理由": "固定条件",
        }

        payload = prepare_analysis_input.serialize_analysis_input([record])

        self.assertFalse(payload.startswith(codecs.BOM_UTF8))
        self.assertTrue(payload.endswith(b"\n"))
        self.assertNotIn(b"\r\n", payload)
        text = payload.decode("utf-8")
        self.assertIn('"YES価格": 0.1', text)
        self.assertIn('"市場": "日本語 \\"quote\\" \\\\ path\\nnext"', text)
        parsed = json.loads(text, object_pairs_hook=list)
        self.assertEqual(
            list(prepare_analysis_input.JSON_KEYS),
            [key for key, _ in parsed[0]],
        )

    def test_preserves_record_order_and_is_byte_deterministic(self):
        records = [
            make_analysis_record(market_id="2"),
            make_analysis_record(market_id="1"),
        ]

        first = prepare_analysis_input.serialize_analysis_input(records)
        second = prepare_analysis_input.serialize_analysis_input(records)

        self.assertEqual(first, second)
        self.assertEqual(
            ["2", "1"],
            [item["市場ID"] for item in json.loads(first)],
        )


class WorkflowTests(unittest.TestCase):
    def test_run_atomically_replaces_output_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            source = write_candidate_csv(data_dir, [make_candidate_row()])
            source_before = source.read_bytes()
            output = data_dir / "analysis_input_2026-07-30_2204.json"
            output.write_bytes(b"old")

            first_path, first_count = prepare_analysis_input.run(data_dir)
            first_bytes = first_path.read_bytes()
            second_path, second_count = prepare_analysis_input.run(data_dir)

            self.assertEqual((1, 1), (first_count, second_count))
            self.assertEqual(output, first_path)
            self.assertEqual(first_path, second_path)
            self.assertEqual(first_bytes, second_path.read_bytes())
            self.assertEqual(source_before, source.read_bytes())

    def test_write_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_input_2026-07-30_2204.json"
            path.write_bytes(b"old")

            with patch.object(
                prepare_analysis_input.os,
                "write",
                side_effect=OSError("write failed"),
            ):
                with self.assertRaisesRegex(OSError, "write failed"):
                    prepare_analysis_input.atomic_write(path, b"new")

            self.assertEqual(b"old", path.read_bytes())

    def test_replace_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_input_2026-07-30_2204.json"
            path.write_bytes(b"old")

            with patch.object(
                prepare_analysis_input.os,
                "replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    prepare_analysis_input.atomic_write(path, b"new")

            self.assertEqual(b"old", path.read_bytes())
            self.assertEqual([], list(Path(directory).glob("*.tmp")))

    def test_invalid_input_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_candidate_csv(
                data_dir,
                [make_candidate_row(**{"YES価格": "NaN"})],
            )
            output = data_dir / "analysis_input_2026-07-30_2204.json"
            output.write_bytes(b"old")

            with self.assertRaisesRegex(ValueError, "YES価格"):
                prepare_analysis_input.run(data_dir)

            self.assertEqual(b"old", output.read_bytes())

    def test_main_reports_zero_records_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_candidate_csv(data_dir, [])
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch.object(prepare_analysis_input, "DATA_DIR", data_dir),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = prepare_analysis_input.main()

            output = data_dir / "analysis_input_2026-07-30_2204.json"
            self.assertEqual(0, exit_code)
            self.assertEqual(b"[]\n", output.read_bytes())
            self.assertIn("分析入力0件", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
