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


if __name__ == "__main__":
    unittest.main()
