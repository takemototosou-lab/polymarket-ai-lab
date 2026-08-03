import hashlib
import http.client
import importlib.util
import io
import socket
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import external_analysis
from tests.test_external_analysis import make_input, write_json, write_snapshot


def file_hashes(data_dir):
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in data_dir.iterdir()
        if path.is_file()
    }


class ModuleContractTests(unittest.TestCase):
    def test_cli_module_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("run_external_analysis"))


class CliTests(unittest.TestCase):
    def test_cli_writes_utf8_stdout_bytes_with_lf_only(self):
        import run_external_analysis

        class BinaryStdout:
            def __init__(self):
                self.buffer = io.BytesIO()

        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_snapshot(data_dir, [make_input("1")])
            stdout = BinaryStdout()
            with patch.object(run_external_analysis.sys, "stdout", stdout):
                code = run_external_analysis.main(data_dir=data_dir, env={})

            payload = stdout.buffer.getvalue()
            self.assertEqual(0, code)
            self.assertIn(b"external analysis dry-run\n", payload)
            self.assertNotIn(b"\r", payload)
            self.assertTrue(payload.endswith(b"- 1\n"))

    def run_main(self, data_dir, env):
        import run_external_analysis

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = run_external_analysis.main(data_dir=data_dir, env=env)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_normal_run_is_deterministic_and_preserves_all_data_files(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_snapshot(
                data_dir,
                [make_input("3"), make_input("1"), make_input("2")],
            )
            (data_dir / "unrelated.bin").write_bytes(b"unchanged")
            before = file_hashes(data_dir)

            with (
                patch.object(socket, "socket", side_effect=AssertionError("network")),
                patch.object(
                    socket,
                    "create_connection",
                    side_effect=AssertionError("network"),
                ),
                patch.object(
                    urllib.request,
                    "urlopen",
                    side_effect=AssertionError("network"),
                ),
                patch.object(
                    http.client.HTTPConnection,
                    "request",
                    side_effect=AssertionError("network"),
                ),
                patch.object(
                    external_analysis.FakeAnalysisProvider,
                    "analyze",
                    side_effect=AssertionError("provider called"),
                ),
            ):
                first = self.run_main(data_dir, {})
                second = self.run_main(data_dir, {})

            self.assertEqual(0, first[0])
            self.assertEqual(first, second)
            self.assertEqual(before, file_hashes(data_dir))
            self.assertIn("selected_markets: 1\n", first[1])
            self.assertTrue(first[1].endswith("market_ids:\n- 3\n"))
            self.assertEqual("", first[2])
            self.assertEqual(
                [],
                [path.name for path in data_dir.iterdir() if not path.is_file()],
            )
            self.assertFalse(any("tmp" in path.name for path in data_dir.iterdir()))
            self.assertFalse(any("log" in path.name for path in data_dir.iterdir()))
            self.assertFalse(any("lock" in path.name for path in data_dir.iterdir()))

    def test_max_three_and_ten_preserve_input_order(self):
        for count, limit, expected_last in ((3, "3", "- 3\n"), (11, "10", "- 10\n")):
            with self.subTest(count=count, limit=limit):
                with tempfile.TemporaryDirectory() as directory:
                    data_dir = Path(directory)
                    write_snapshot(
                        data_dir,
                        [make_input(str(index)) for index in range(1, count + 1)],
                    )
                    code, stdout, stderr = self.run_main(
                        data_dir,
                        {"POLYMARKET_MAX_MARKETS_PER_RUN": limit},
                    )
                    self.assertEqual(0, code)
                    self.assertIn(f"selected_markets: {limit}\n", stdout)
                    self.assertTrue(stdout.endswith(expected_last))
                    self.assertEqual("", stderr)

    def test_empty_snapshot_is_success(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_snapshot(data_dir, [])
            code, stdout, stderr = self.run_main(data_dir, {})
            self.assertEqual(0, code)
            self.assertTrue(stdout.endswith("market_ids:\n"))
            self.assertEqual("", stderr)

    def test_maps_configuration_contract_and_phase_errors_to_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_snapshot(data_dir, [])
            cases = (
                ({"POLYMARKET_AI_PROVIDER": "unknown"}, 1),
                ({"POLYMARKET_AI_PROVIDER": "openai"}, 3),
                ({"POLYMARKET_AI_DRY_RUN": "false"}, 3),
            )
            for env, expected in cases:
                with self.subTest(env=env):
                    code, stdout, stderr = self.run_main(data_dir, env)
                    self.assertEqual(expected, code)
                    self.assertEqual("", stdout)
                    self.assertIn("エラー:", stderr)
                    self.assertNotIn("Traceback", stderr)

    def test_contract_error_is_exit_two_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_json(data_dir / "analysis_input_2026-07-30_2204.json", [])
            code, stdout, stderr = self.run_main(data_dir, {})
            self.assertEqual(2, code)
            self.assertEqual("", stdout)
            self.assertIn("エラー:", stderr)
            self.assertNotIn("Traceback", stderr)

    def test_does_not_read_or_print_api_key_and_hides_market_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            write_snapshot(
                data_dir,
                [
                    make_input(
                        "1",
                        **{
                            "市場説明": "SECRET DESCRIPTION",
                            "解決情報源": "https://secret.example/rules",
                        },
                    )
                ],
            )
            code, stdout, stderr = self.run_main(
                data_dir,
                {"OPENAI_API_KEY": "TOP-SECRET-KEY"},
            )
            self.assertEqual(0, code)
            self.assertNotIn("TOP-SECRET-KEY", stdout + stderr)
            self.assertNotIn("SECRET DESCRIPTION", stdout)
            self.assertNotIn("secret.example", stdout)
            self.assertNotIn(str(data_dir.resolve()), stdout)


if __name__ == "__main__":
    unittest.main()
