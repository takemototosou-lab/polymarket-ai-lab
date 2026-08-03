import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

from phase2_contracts import (
    BudgetLimitError,
    ConnectionPlan,
    DependencyError,
    DnsResolution,
    FileStore,
    LockConflictError,
    MimeRejectedError,
    Phase2Error,
    PolicyUrl,
    ProviderAuthError,
    QueryKind,
    RedirectHop,
    ResponseContractError,
    SearchProvider,
    SearchRequest,
    SourceCandidate,
    UrlSafetyError,
    ValidatedFetchResult,
    phase2_exit_code,
)


class ContractTests(unittest.TestCase):
    def test_query_kind_values_are_fixed(self):
        self.assertEqual(
            ("official", "status", "support", "counter"),
            tuple(value.value for value in QueryKind),
        )

    def test_maps_phase2_errors_to_codes_four_through_ten(self):
        cases = (
            (UrlSafetyError(), 4),
            (LockConflictError(), 5),
            (DependencyError(), 6),
            (ResponseContractError(), 7),
            (BudgetLimitError(), 8),
            (ProviderAuthError(), 9),
            (MimeRejectedError(), 10),
        )
        for error, code in cases:
            with self.subTest(error=type(error).__name__):
                self.assertIsInstance(error, Phase2Error)
                self.assertEqual(code, type(error).exit_code)
                self.assertEqual(code, phase2_exit_code(error))

    def test_rejects_non_phase2_error_in_exit_code_mapping(self):
        with self.assertRaises(TypeError):
            phase2_exit_code(Exception("outside Phase 2"))

    def test_dataclass_field_order_is_fixed(self):
        expected = {
            PolicyUrl: ("original", "request_url", "hostname", "port", "path_and_query"),
            DnsResolution: ("hostname", "addresses", "cname_chain"),
            ConnectionPlan: ("url", "verified_ips"),
            RedirectHop: ("request_url", "status_code", "location", "peer_ip"),
            SourceCandidate: (
                "source_id",
                "query_kind",
                "rank",
                "url",
                "title",
                "snippet",
                "publisher_hint",
                "published_at_hint",
            ),
            SearchRequest: ("query_kind", "query", "max_results", "request_ordinal"),
            ValidatedFetchResult: (
                "requested_url",
                "final_url",
                "redirect_chain",
                "resolved_ips_by_hop",
                "peer_ip_by_hop",
                "status_code",
                "content_type",
                "charset",
                "response_bytes",
                "decoded_chars",
                "retrieved_at",
                "decoded_html",
            ),
        }
        for contract, names in expected.items():
            with self.subTest(contract=contract.__name__):
                self.assertEqual(names, tuple(field.name for field in fields(contract)))
                self.assertTrue(contract.__dataclass_params__.frozen)

    def test_dataclasses_are_frozen(self):
        candidate = SourceCandidate(
            "C1",
            QueryKind.OFFICIAL,
            1,
            "https://example.com/",
            "title",
            "",
            None,
            None,
        )
        with self.assertRaises(FrozenInstanceError):
            candidate.rank = 2

    def test_collection_fields_are_tuples(self):
        resolution = DnsResolution("example.com", ("8.8.8.8",), ("alias.example",))
        plan = ConnectionPlan(
            PolicyUrl(
                "https://example.com/",
                "https://example.com/",
                "example.com",
                443,
                "/",
            ),
            ("8.8.8.8",),
        )
        result = ValidatedFetchResult(
            "https://example.com/",
            "https://example.com/",
            (),
            (("8.8.8.8",),),
            ("8.8.8.8",),
            200,
            "text/html",
            "utf-8",
            1,
            1,
            "2026-08-03T00:00:00Z",
            "x",
        )
        self.assertIsInstance(resolution.addresses, tuple)
        self.assertIsInstance(resolution.cname_chain, tuple)
        self.assertIsInstance(plan.verified_ips, tuple)
        self.assertIsInstance(result.redirect_chain, tuple)
        self.assertIsInstance(result.resolved_ips_by_hop, tuple)
        self.assertIsInstance(result.peer_ip_by_hop, tuple)

    def test_protocol_boundaries_exist_without_implementations(self):
        self.assertTrue(SearchProvider._is_protocol)
        self.assertTrue(FileStore._is_protocol)
        self.assertIn("search", SearchProvider.__dict__)
        self.assertEqual(
            {"create_exclusive", "read_bytes", "remove"},
            {name for name in FileStore.__dict__ if not name.startswith("_")},
        )
        self.assertIs(Path, FileStore.create_exclusive.__annotations__["path"])


if __name__ == "__main__":
    unittest.main()
