import codecs
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import analyze_market


REFERENCE_TIME = "2026-07-30T22:04:49.568055+09:00"


def make_input_record(
    *,
    market_id="1",
    analysis_reference_time=REFERENCE_TIME,
    **overrides,
):
    record = {
        "市場ID": market_id,
        "市場": "市場A",
        "市場説明": "Resolution rule\nSecond line",
        "解決情報源": "",
        "YES価格": 0.1,
        "NO価格": 0.9,
        "出来高": 1000,
        "流動性": 500.5,
        "締切日": "2026-08-29T12:00:00Z",
        "カテゴリ": "その他",
        "締切までの日数": 30,
        "URL": "https://polymarket.com/event/example",
        "分析基準日時": analysis_reference_time,
        "選定理由": "固定条件",
    }
    record.update(overrides)
    return record


def write_analysis_input(
    data_dir: Path,
    records,
    *,
    name="analysis_input_2026-07-30_2204.json",
) -> Path:
    path = data_dir / name
    payload = (
        json.dumps(records, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    path.write_bytes(payload)
    return path


class FileContractTests(unittest.TestCase):
    def test_finds_last_filename_and_derives_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            older = data_dir / "analysis_input_2026-07-29_2359.json"
            latest = data_dir / "analysis_input_2026-07-30_2204.json"
            older.touch()
            latest.touch()
            os.utime(older, (200, 200))
            os.utime(latest, (100, 100))

            self.assertEqual(
                latest,
                analyze_market.find_latest_analysis_input(data_dir),
            )
            self.assertEqual(
                data_dir / "analysis_result_2026-07-30_2204.json",
                analyze_market.output_path_for(latest),
            )

    def test_rejects_missing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "分析入力JSON"):
                analyze_market.find_latest_analysis_input(Path(directory))

    def test_rejects_invalid_selected_filename_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            valid = data_dir / "analysis_input_2026-07-30_2204.json"
            invalid = data_dir / "analysis_input_9999-99-99_9999.json"
            valid.touch()
            invalid.touch()

            selected = analyze_market.find_latest_analysis_input(data_dir)
            self.assertEqual(invalid, selected)
            with self.assertRaisesRegex(ValueError, "ファイル名"):
                analyze_market.output_path_for(selected)


class InputValidationTests(unittest.TestCase):
    def test_rejects_non_array_top_level(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), {"市場ID": "1"})
            with self.assertRaisesRegex(ValueError, "トップレベル"):
                analyze_market.load_analysis_inputs(path)

    def test_rejects_non_object_array_element(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), ["not-object"])
            with self.assertRaisesRegex(ValueError, "オブジェクト"):
                analyze_market.load_analysis_inputs(path)

    def test_rejects_missing_required_key(self):
        record = make_input_record()
        del record["選定理由"]
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), [record])
            with self.assertRaisesRegex(ValueError, "必須キー"):
                analyze_market.load_analysis_inputs(path)

    def test_rejects_wrong_required_types_and_boolean_numbers(self):
        cases = (
            ("市場ID", 1),
            ("市場", 1),
            ("市場説明", 1),
            ("解決情報源", None),
            ("YES価格", True),
            ("NO価格", "0.9"),
            ("出来高", None),
            ("流動性", []),
            ("締切日", 1),
            ("カテゴリ", {}),
            ("締切までの日数", False),
            ("URL", 1),
            ("分析基準日時", 1),
            ("選定理由", 1),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_analysis_input(
                        Path(directory),
                        [make_input_record(**{key: value})],
                    )
                    with self.assertRaisesRegex(ValueError, key):
                        analyze_market.load_analysis_inputs(path)

    def test_rejects_blank_or_duplicate_market_id(self):
        cases = (
            [make_input_record(market_id=" ")],
            [
                make_input_record(market_id="1"),
                make_input_record(market_id="1"),
            ],
        )
        for records in cases:
            with self.subTest(records=records):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_analysis_input(Path(directory), records)
                    with self.assertRaisesRegex(ValueError, "市場ID"):
                        analyze_market.load_analysis_inputs(path)

    def test_accepts_fourteen_key_metadata_contract(self):
        record = make_input_record(
            **{
                "市場説明": "Rule, one\n\"Rule two\"",
                "解決情報源": "",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), [record])

            records = analyze_market.load_analysis_inputs(path)

        self.assertEqual("Rule, one\n\"Rule two\"", records[0]["市場説明"])
        self.assertEqual("", records[0]["解決情報源"])
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
            list(analyze_market.INPUT_KEYS),
        )

    def test_rejects_old_twelve_key_input(self):
        record = make_input_record()
        del record["市場説明"]
        del record["解決情報源"]
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), [record])
            with self.assertRaisesRegex(ValueError, "市場説明.*解決情報源"):
                analyze_market.load_analysis_inputs(path)

    def test_rejects_invalid_description_and_accepts_empty_source(self):
        invalid_descriptions = ("", "   ", "bad\rtext", "bad\x00text")
        for description in invalid_descriptions:
            with self.subTest(description=repr(description)):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_analysis_input(
                        Path(directory),
                        [make_input_record(**{"市場説明": description})],
                    )
                    with self.assertRaisesRegex(ValueError, "市場説明"):
                        analyze_market.load_analysis_inputs(path)

    def test_accepts_offset_and_z_reference_times(self):
        for reference_time in (
            "2026-07-30T22:04:49.568055+09:00",
            "2026-07-30T13:04:49.568055Z",
        ):
            with self.subTest(reference_time=reference_time):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_analysis_input(
                        Path(directory),
                        [
                            make_input_record(
                                analysis_reference_time=reference_time
                            )
                        ],
                    )
                    records = analyze_market.load_analysis_inputs(path)
                    self.assertEqual(
                        reference_time,
                        records[0]["分析基準日時"],
                    )

    def test_rejects_invalid_reference_times(self):
        for reference_time in (
            "2026-07-30T22:04:49",
            "2026-07-30",
            "2026-02-30T12:00:00+09:00",
            "2026-07-30T25:00:00+09:00",
        ):
            with self.subTest(reference_time=reference_time):
                with tempfile.TemporaryDirectory() as directory:
                    path = write_analysis_input(
                        Path(directory),
                        [
                            make_input_record(
                                analysis_reference_time=reference_time
                            )
                        ],
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "分析基準日時",
                    ):
                        analyze_market.load_analysis_inputs(path)

    def test_rejects_textually_inconsistent_reference_times(self):
        records = [
            make_input_record(
                market_id="1",
                analysis_reference_time="2026-07-30T22:04:00+09:00",
            ),
            make_input_record(
                market_id="2",
                analysis_reference_time="2026-07-30T13:04:00Z",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), records)
            with self.assertRaisesRegex(ValueError, "不統一"):
                analyze_market.load_analysis_inputs(path)

    def test_allows_unknown_keys_without_type_restriction(self):
        record = make_input_record()
        record["future_metadata"] = {
            "nested": [1, "two", None, {"enabled": True}]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = write_analysis_input(Path(directory), [record])
            records = analyze_market.load_analysis_inputs(path)
            self.assertEqual(
                record["future_metadata"],
                records[0]["future_metadata"],
            )

    def test_allows_input_schema_version_as_an_unknown_key(self):
        for input_version in ("999.0", None, {"nested": True}):
            with self.subTest(input_version=input_version):
                record = make_input_record()
                record["schema_version"] = input_version
                with tempfile.TemporaryDirectory() as directory:
                    path = write_analysis_input(Path(directory), [record])
                    records = analyze_market.load_analysis_inputs(path)
                    self.assertEqual(
                        input_version,
                        records[0]["schema_version"],
                    )

    def test_rejects_duplicate_key_at_market_level(self):
        record = make_input_record()
        text = json.dumps([record], ensure_ascii=False, indent=2)
        text = text.replace(
            '"市場ID": "1",',
            '"市場ID": "1",\n    "市場ID": "2",',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_input_2026-07-30_2204.json"
            path.write_bytes((text + "\n").encode("utf-8"))
            with self.assertRaisesRegex(ValueError, "重複"):
                analyze_market.load_analysis_inputs(path)

    def test_rejects_duplicate_key_in_nested_unknown_object(self):
        record = make_input_record()
        record["future_metadata"] = {"x": 1}
        text = json.dumps([record], ensure_ascii=False, indent=2)
        text = text.replace('"x": 1', '"x": 1,\n      "x": 2', 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_input_2026-07-30_2204.json"
            path.write_bytes((text + "\n").encode("utf-8"))
            with self.assertRaisesRegex(ValueError, "重複"):
                analyze_market.load_analysis_inputs(path)

    def test_rejects_bom_empty_invalid_json_and_non_finite_numbers(self):
        payloads = (
            b"\xef\xbb\xbf[]\n",
            b"",
            b"\xff",
            b"{",
            (
                json.dumps(
                    [make_input_record()],
                    ensure_ascii=False,
                ).replace('"YES価格": 0.1', '"YES価格": NaN')
                + "\n"
            ).encode("utf-8"),
            (
                json.dumps(
                    [make_input_record()],
                    ensure_ascii=False,
                ).replace('"YES価格": 0.1', '"YES価格": -Infinity')
                + "\n"
            ).encode("utf-8"),
            (
                json.dumps(
                    [make_input_record()],
                    ensure_ascii=False,
                ).replace('"YES価格": 0.1', '"YES価格": Infinity')
                + "\n"
            ).encode("utf-8"),
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with tempfile.TemporaryDirectory() as directory:
                    path = (
                        Path(directory)
                        / "analysis_input_2026-07-30_2204.json"
                    )
                    path.write_bytes(payload)
                    with self.assertRaises(ValueError):
                        analyze_market.load_analysis_inputs(path)


class PendingResultTests(unittest.TestCase):
    def test_builds_one_pending_result_per_input_in_order(self):
        records = [
            make_input_record(market_id="2"),
            make_input_record(market_id="1"),
        ]

        results = analyze_market.build_pending_results(records)

        self.assertEqual(["2", "1"], [item["market_id"] for item in results])
        self.assertEqual(
            [REFERENCE_TIME, REFERENCE_TIME],
            [item["analysis_reference_time"] for item in results],
        )
        self.assertEqual(
            ["pending", "pending"],
            [item["status"] for item in results],
        )

    def test_uses_only_code_schema_version_for_every_result(self):
        records = [
            {
                **make_input_record(market_id="1"),
                "schema_version": "999.0",
            },
            {
                **make_input_record(market_id="2"),
                "schema_version": None,
            },
        ]

        results = analyze_market.build_pending_results(records)

        self.assertEqual("1.0", analyze_market.SCHEMA_VERSION)
        self.assertEqual(
            {"1.0"},
            {item["schema_version"] for item in results},
        )
        self.assertNotEqual(
            records[0]["schema_version"],
            results[0]["schema_version"],
        )

    def test_empty_input_produces_empty_result_list(self):
        self.assertEqual([], analyze_market.build_pending_results([]))


class SerializationTests(unittest.TestCase):
    def test_empty_array_is_exactly_three_bytes(self):
        self.assertEqual(
            b"[]\n",
            analyze_market.serialize_analysis_results([]),
        )

    def test_serializes_fixed_key_order_utf8_and_lf(self):
        record = {
            "schema_version": "1.0",
            "market_id": '日本語 "id" \\ path\nnext',
            "analysis_reference_time": REFERENCE_TIME,
            "status": "pending",
        }

        payload = analyze_market.serialize_analysis_results([record])

        self.assertFalse(payload.startswith(codecs.BOM_UTF8))
        self.assertTrue(payload.endswith(b"\n"))
        self.assertNotIn(b"\r\n", payload)
        text = payload.decode("utf-8")
        self.assertIn(
            '"market_id": "日本語 \\"id\\" \\\\ path\\nnext"',
            text,
        )
        parsed = json.loads(text, object_pairs_hook=list)
        self.assertEqual(
            list(analyze_market.RESULT_KEYS),
            [key for key, _ in parsed[0]],
        )

    def test_preserves_result_order_and_is_byte_deterministic(self):
        records = analyze_market.build_pending_results(
            [
                make_input_record(market_id="2"),
                make_input_record(market_id="1"),
            ]
        )

        first = analyze_market.serialize_analysis_results(records)
        second = analyze_market.serialize_analysis_results(records)

        self.assertEqual(first, second)
        self.assertEqual(
            ["2", "1"],
            [item["market_id"] for item in json.loads(first)],
        )


class WorkflowTests(unittest.TestCase):
    def test_run_replaces_output_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            source = write_analysis_input(
                data_dir,
                [make_input_record()],
            )
            source_before = source.read_bytes()
            output = data_dir / "analysis_result_2026-07-30_2204.json"
            output.write_bytes(b"old")

            first_path, first_count = analyze_market.run(data_dir)
            first_bytes = first_path.read_bytes()
            second_path, second_count = analyze_market.run(data_dir)

            self.assertEqual((1, 1), (first_count, second_count))
            self.assertEqual(output, first_path)
            self.assertEqual(first_path, second_path)
            self.assertEqual(first_bytes, second_path.read_bytes())
            self.assertEqual(source_before, source.read_bytes())

    def test_invalid_input_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_analysis_input(data_dir, {"not": "array"})
            output = data_dir / "analysis_result_2026-07-30_2204.json"
            output.write_bytes(b"old")

            with self.assertRaisesRegex(ValueError, "トップレベル"):
                analyze_market.run(data_dir)

            self.assertEqual(b"old", output.read_bytes())

    def test_write_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_result_2026-07-30_2204.json"
            path.write_bytes(b"old")

            with patch.object(
                analyze_market,
                "_write_and_sync",
                side_effect=OSError("write failed"),
            ):
                with self.assertRaisesRegex(OSError, "write failed"):
                    analyze_market.atomic_write(path, b"new")

            self.assertEqual(b"old", path.read_bytes())
            self.assertEqual([], list(Path(directory).glob("*.tmp")))

    def test_replace_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis_result_2026-07-30_2204.json"
            path.write_bytes(b"old")

            with patch.object(
                analyze_market.os,
                "replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    analyze_market.atomic_write(path, b"new")

            self.assertEqual(b"old", path.read_bytes())
            self.assertEqual([], list(Path(directory).glob("*.tmp")))

    def test_main_reports_zero_results_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_analysis_input(data_dir, [])
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch.object(analyze_market, "DATA_DIR", data_dir),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = analyze_market.main()

            output = data_dir / "analysis_result_2026-07-30_2204.json"
            self.assertEqual(0, exit_code)
            self.assertEqual(b"[]\n", output.read_bytes())
            self.assertIn("分析結果0件", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())

    def test_main_reports_invalid_input_as_error(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch.object(analyze_market, "DATA_DIR", data_dir),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = analyze_market.main()

            self.assertEqual(1, exit_code)
            self.assertEqual("", stdout.getvalue())
            self.assertIn("エラー:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
