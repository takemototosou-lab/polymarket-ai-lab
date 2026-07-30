import json
import os
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
