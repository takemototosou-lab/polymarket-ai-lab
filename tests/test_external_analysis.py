import importlib.util
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import external_analysis


REFERENCE_TIME = "2026-07-30T22:04:49.568055+09:00"


def make_input(market_id="1", **overrides):
    record = {
        "市場ID": market_id,
        "市場": f"市場{market_id}",
        "市場説明": "Resolution rule\nSecond line",
        "解決情報源": "",
        "YES価格": 0.1234567890123456789,
        "NO価格": 0.8765432109876543211,
        "出来高": 1000,
        "流動性": 500.5,
        "締切日": "2026-08-29T12:00:00Z",
        "カテゴリ": "その他",
        "締切までの日数": 30,
        "URL": f"https://polymarket.com/event/{market_id}",
        "分析基準日時": REFERENCE_TIME,
        "選定理由": "固定条件",
    }
    record.update(overrides)
    return record


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_snapshot(data_dir, records, *, suffix="2026-07-30_2204"):
    input_path = data_dir / f"analysis_input_{suffix}.json"
    result_path = data_dir / f"analysis_result_{suffix}.json"
    write_json(input_path, records)
    write_json(
        result_path,
        [
            {
                "schema_version": "2.0",
                "market_id": record["市場ID"],
                "analysis_reference_time": record["分析基準日時"],
                "status": "pending",
            }
            for record in records
        ],
    )
    return input_path, result_path


class ModuleContractTests(unittest.TestCase):
    def test_external_analysis_module_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("external_analysis"))


class ConfigTests(unittest.TestCase):
    def test_reads_only_the_four_phase_one_configuration_keys(self):
        class ObservedEnvironment(dict):
            def __init__(self):
                super().__init__({"OPENAI_API_KEY": "must-not-be-read"})
                self.requested = []

            def get(self, key, default=None):
                self.requested.append(key)
                if key.endswith("API_KEY"):
                    raise AssertionError("APIキーを参照しました")
                return super().get(key, default)

        env = ObservedEnvironment()
        external_analysis.load_config(env)

        self.assertEqual(
            [
                "POLYMARKET_AI_PROVIDER",
                "POLYMARKET_AI_DRY_RUN",
                "POLYMARKET_MAX_MARKETS_PER_RUN",
                "POLYMARKET_AI_REASONING_EFFORT",
            ],
            env.requested,
        )

    def test_defaults_are_fake_dry_run_one_market_and_low_reasoning(self):
        config = external_analysis.load_config({})

        self.assertEqual(
            ("fake", True, 1, "low"),
            (
                config.provider,
                config.dry_run,
                config.max_markets_per_run,
                config.reasoning_effort,
            ),
        )

    def test_accepts_all_explicit_valid_values(self):
        for effort in ("low", "medium", "high"):
            with self.subTest(effort=effort):
                config = external_analysis.load_config(
                    {
                        "POLYMARKET_AI_PROVIDER": "fake",
                        "POLYMARKET_AI_DRY_RUN": "true",
                        "POLYMARKET_MAX_MARKETS_PER_RUN": "10",
                        "POLYMARKET_AI_REASONING_EFFORT": effort,
                    }
                )
                self.assertEqual(10, config.max_markets_per_run)
                self.assertEqual(effort, config.reasoning_effort)

    def test_openai_and_false_are_phase_not_available(self):
        cases = (
            {"POLYMARKET_AI_PROVIDER": "openai"},
            {"POLYMARKET_AI_DRY_RUN": "false"},
        )
        for env in cases:
            with self.subTest(env=env):
                with self.assertRaises(
                    external_analysis.PhaseNotAvailableError
                ):
                    external_analysis.load_config(env)

    def test_rejects_invalid_provider_boolean_and_reasoning(self):
        cases = (
            {"POLYMARKET_AI_PROVIDER": "unknown"},
            {"POLYMARKET_AI_PROVIDER": "Fake"},
            {"POLYMARKET_AI_PROVIDER": " fake"},
            {"POLYMARKET_AI_DRY_RUN": "TRUE"},
            {"POLYMARKET_AI_DRY_RUN": "1"},
            {"POLYMARKET_AI_DRY_RUN": ""},
            {"POLYMARKET_AI_REASONING_EFFORT": "unknown"},
            {"POLYMARKET_AI_REASONING_EFFORT": " Low"},
        )
        for env in cases:
            with self.subTest(env=env):
                with self.assertRaises(external_analysis.ConfigurationError):
                    external_analysis.load_config(env)

    def test_accepts_one_and_rejects_noncanonical_market_limits(self):
        self.assertEqual(
            1,
            external_analysis.load_config(
                {"POLYMARKET_MAX_MARKETS_PER_RUN": "1"}
            ).max_markets_per_run,
        )
        for value in (
            "0",
            "11",
            "-1",
            "+1",
            "01",
            "1.0",
            "1e0",
            "",
            " 1",
            "1 ",
        ):
            with self.subTest(value=value):
                with self.assertRaises(external_analysis.ConfigurationError):
                    external_analysis.load_config(
                        {"POLYMARKET_MAX_MARKETS_PER_RUN": value}
                    )


class SnapshotContractTests(unittest.TestCase):
    def test_wraps_strict_input_validation_failures(self):
        invalid_record_sets = (
            [make_input("1"), make_input("1")],
            [make_input("1", **{"市場説明": ""})],
            [make_input("1", **{"分析基準日時": "2026-07-30T22:04:49"})],
            [make_input("1", **{"YES価格": float("nan")})],
        )
        for records in invalid_record_sets:
            with self.subTest(records=records):
                with tempfile.TemporaryDirectory() as directory:
                    data_dir = Path(directory)
                    write_snapshot(data_dir, records)
                    with self.assertRaises(external_analysis.ContractError):
                        external_analysis.load_phase1_snapshot(data_dir)

    def test_rejects_input_bom_duplicate_key_and_invalid_json(self):
        payloads = (
            b"\xef\xbb\xbf[]\n",
            (
                '[{"市場ID":"1","市場ID":"1"}]\n'.encode("utf-8")
            ),
            b"not-json\n",
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with tempfile.TemporaryDirectory() as directory:
                    data_dir = Path(directory)
                    input_path, _ = write_snapshot(data_dir, [])
                    input_path.write_bytes(payload)
                    with self.assertRaises(external_analysis.ContractError):
                        external_analysis.load_phase1_snapshot(data_dir)

    def test_rejects_result_bom_invalid_json_and_count_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _, result_path = write_snapshot(data_dir, [make_input()])
            for payload in (b"\xef\xbb\xbf[]\n", b"not-json\n", b"[]\n"):
                with self.subTest(payload=payload):
                    result_path.write_bytes(payload)
                    with self.assertRaises(external_analysis.ContractError):
                        external_analysis.load_phase1_snapshot(data_dir)

    def test_selects_latest_input_and_same_suffix_result(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_snapshot(data_dir, [make_input("old")], suffix="2026-07-30_2100")
            expected_input, expected_result = write_snapshot(
                data_dir, [make_input("new")], suffix="2026-07-30_2204"
            )

            input_path, result_path, records = (
                external_analysis.load_phase1_snapshot(data_dir)
            )

            self.assertEqual(expected_input, input_path)
            self.assertEqual(expected_result, result_path)
            self.assertEqual("new", records[0]["市場ID"])

    def test_missing_corresponding_result_is_rejected_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_snapshot(data_dir, [make_input("old")], suffix="2026-07-30_2100")
            write_json(
                data_dir / "analysis_input_2026-07-30_2204.json",
                [make_input("new")],
            )

            with self.assertRaises(external_analysis.ContractError):
                external_analysis.load_phase1_snapshot(data_dir)

    def test_latest_invalid_filename_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_snapshot(data_dir, [make_input("old")])
            write_json(data_dir / "analysis_input_invalid.json", [])

            with self.assertRaises(external_analysis.ContractError):
                external_analysis.load_phase1_snapshot(data_dir)

    def test_rejects_legacy_nonpending_unknown_keys_and_mismatch(self):
        cases = (
            {"schema_version": "1.0"},
            {"status": "completed"},
            {"status": "error"},
            {"extra": "value"},
            {"market_id": "different"},
            {"analysis_reference_time": "2026-07-31T00:00:00Z"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with tempfile.TemporaryDirectory() as directory:
                    data_dir = Path(directory)
                    _, result_path = write_snapshot(data_dir, [make_input()])
                    result = {
                        "schema_version": "2.0",
                        "market_id": "1",
                        "analysis_reference_time": REFERENCE_TIME,
                        "status": "pending",
                    }
                    result.update(overrides)
                    write_json(result_path, [result])
                    with self.assertRaises(external_analysis.ContractError):
                        external_analysis.load_phase1_snapshot(data_dir)

    def test_rejects_result_key_order_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _, result_path = write_snapshot(data_dir, [make_input()])
            result_path.write_text(
                '[{"market_id":"1","schema_version":"2.0",'
                '"analysis_reference_time":"' + REFERENCE_TIME + '",'
                '"status":"pending"}]\n',
                encoding="utf-8",
            )
            with self.assertRaises(external_analysis.ContractError):
                external_analysis.load_phase1_snapshot(data_dir)

            result_path.write_text(
                '[{"schema_version":"2.0","market_id":"1",'
                '"market_id":"1","analysis_reference_time":"'
                + REFERENCE_TIME
                + '","status":"pending"}]\n',
                encoding="utf-8",
            )
            with self.assertRaises(external_analysis.ContractError):
                external_analysis.load_phase1_snapshot(data_dir)

    def test_reuses_strict_input_contract_and_accepts_empty_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_snapshot(data_dir, [])
            _, _, records = external_analysis.load_phase1_snapshot(data_dir)
            self.assertEqual([], records)

            write_json(
                data_dir / "analysis_input_2026-07-30_2204.json",
                [{"市場ID": "old-format"}],
            )
            with self.assertRaises(external_analysis.ContractError):
                external_analysis.load_phase1_snapshot(data_dir)


class RequestAndProviderTests(unittest.TestCase):
    def test_selects_input_prefix_without_sorting(self):
        records = [make_input("3"), make_input("1"), make_input("2")]
        self.assertEqual(
            ["3"],
            [item["市場ID"] for item in external_analysis.select_markets(records, 1)],
        )
        self.assertEqual(
            ["3", "1", "2"],
            [item["市場ID"] for item in external_analysis.select_markets(records, 10)],
        )
        self.assertEqual([], external_analysis.select_markets([], 10))

    def test_builds_immutable_request_with_all_fifteen_fields(self):
        source = make_input(
            "A",
            **{
                "YES価格": Decimal("0.1234567890123456789"),
                "NO価格": Decimal("0.8765432109876543211"),
                "出来高": Decimal("1000"),
                "流動性": Decimal("500.5"),
                "締切までの日数": Decimal("30"),
            },
        )
        request = external_analysis.build_requests([source], "medium")[0]

        self.assertEqual(
            (
                "A",
                "市場A",
                "Resolution rule\nSecond line",
                "",
                Decimal("0.1234567890123456789"),
                Decimal("0.8765432109876543211"),
                Decimal("1000"),
                Decimal("500.5"),
                "2026-08-29T12:00:00Z",
                "その他",
                Decimal("30"),
                "https://polymarket.com/event/A",
                REFERENCE_TIME,
                "固定条件",
                "medium",
            ),
            tuple(getattr(request, name) for name in request.__dataclass_fields__),
        )
        with self.assertRaises(FrozenInstanceError):
            request.market_id = "changed"
        self.assertNotIn("path", request.__dataclass_fields__)
        self.assertNotIn("api_key", request.__dataclass_fields__)

    def test_fake_provider_returns_only_fixed_acceptance(self):
        request = external_analysis.build_requests([make_input("A")], "low")[0]
        result = external_analysis.FakeAnalysisProvider().analyze(request)

        self.assertEqual("A", result.market_id)
        self.assertIs(True, result.accepted)
        self.assertEqual(("market_id", "accepted"), tuple(result.__dataclass_fields__))

    def test_formats_deterministic_dry_run_without_sensitive_text(self):
        config = external_analysis.load_config({})
        requests = external_analysis.build_requests([make_input("123")], "low")
        output = external_analysis.format_dry_run(
            Path("C:/secret/analysis_input_2026-07-30_2204.json"),
            Path("C:/secret/analysis_result_2026-07-30_2204.json"),
            config,
            pending_count=3,
            requests=requests,
        )

        self.assertEqual(
            "external analysis dry-run\n"
            "input: analysis_input_2026-07-30_2204.json\n"
            "result: analysis_result_2026-07-30_2204.json\n"
            "provider: fake\n"
            "reasoning_effort: low\n"
            "max_markets: 1\n"
            "pending_markets: 3\n"
            "selected_markets: 1\n"
            "market_ids:\n"
            "- 123\n",
            output,
        )
        self.assertNotIn("C:/secret", output)
        self.assertNotIn("Resolution rule", output)

    def test_zero_market_output_ends_after_header(self):
        config = external_analysis.load_config({})
        output = external_analysis.format_dry_run(
            Path("analysis_input_2026-07-30_2204.json"),
            Path("analysis_result_2026-07-30_2204.json"),
            config,
            pending_count=0,
            requests=[],
        )
        self.assertTrue(output.endswith("market_ids:\n"))


if __name__ == "__main__":
    unittest.main()
