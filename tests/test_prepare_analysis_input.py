import unittest

import prepare_analysis_input


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


if __name__ == "__main__":
    unittest.main()
