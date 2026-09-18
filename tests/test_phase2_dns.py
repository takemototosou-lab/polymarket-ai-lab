import unittest

import dns.exception
import dns.nameserver
import dns.resolver

from phase2_contracts import DependencyError, DnsQueryResult, UrlSafetyError


class FakeBackend:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def query(self, hostname, rdtype, timeout_seconds):
        self.calls.append((hostname, rdtype, timeout_seconds))
        result = self.results[(hostname, rdtype)]
        if isinstance(result, Exception):
            raise result
        return result


def result(hostname, addresses=(), chain=(), canonical=None):
    return DnsQueryResult(
        hostname,
        canonical or (chain[-1] if chain else hostname),
        tuple(addresses),
        tuple(chain),
    )


class DnsOrchestrationTests(unittest.TestCase):
    def test_queries_a_then_aaaa_and_preserves_first_seen_order(self):
        from phase2_dns import resolve_phase2_dns

        backend = FakeBackend(
            {
                ("example.com", "A"): result(
                    "example.com", ("8.8.8.8", "1.1.1.1")
                ),
                ("example.com", "AAAA"): result(
                    "example.com", ("2606:4700:4700::1111", "8.8.8.8")
                ),
            }
        )
        resolved = resolve_phase2_dns("example.com", backend, 5.0)
        self.assertEqual(
            ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"),
            resolved.addresses,
        )
        self.assertEqual(
            [("example.com", "A", 5.0), ("example.com", "AAAA", 5.0)],
            backend.calls,
        )

    def test_accepts_one_empty_family_but_rejects_both_empty(self):
        from phase2_dns import resolve_phase2_dns

        backend = FakeBackend(
            {
                ("example.com", "A"): result("example.com"),
                ("example.com", "AAAA"): result(
                    "example.com", ("2606:4700:4700::1111",)
                ),
            }
        )
        self.assertEqual(
            ("2606:4700:4700::1111",),
            resolve_phase2_dns("example.com", backend, 5).addresses,
        )
        empty = FakeBackend(
            {
                ("example.com", "A"): result("example.com"),
                ("example.com", "AAAA"): result("example.com"),
            }
        )
        with self.assertRaises(UrlSafetyError):
            resolve_phase2_dns("example.com", empty, 5)

    def test_rejects_partial_temporary_failure_conflicting_chain_and_unsafe_ip(self):
        from phase2_dns import resolve_phase2_dns

        temporary = FakeBackend(
            {
                ("example.com", "A"): result("example.com", ("8.8.8.8",)),
                ("example.com", "AAAA"): DependencyError("temporary"),
            }
        )
        with self.assertRaises(DependencyError):
            resolve_phase2_dns("example.com", temporary, 5)

        conflicting = FakeBackend(
            {
                ("example.com", "A"): result(
                    "example.com", ("8.8.8.8",), ("a.example",)
                ),
                ("example.com", "AAAA"): result(
                    "example.com", (), ("b.example",)
                ),
            }
        )
        with self.assertRaises(UrlSafetyError):
            resolve_phase2_dns("example.com", conflicting, 5)

        unsafe = FakeBackend(
            {
                ("example.com", "A"): result(
                    "example.com", ("8.8.8.8", "127.0.0.1")
                ),
                ("example.com", "AAAA"): result("example.com"),
            }
        )
        with self.assertRaises(UrlSafetyError):
            resolve_phase2_dns("example.com", unsafe, 5)

    def test_enforces_cname_ip_and_timeout_limits(self):
        from phase2_dns import resolve_phase2_dns

        for count, succeeds in ((8, True), (9, False)):
            chain = tuple(f"c{i}.example" for i in range(count))
            backend = FakeBackend(
                {
                    ("example.com", "A"): result(
                        "example.com", ("8.8.8.8",), chain
                    ),
                    ("example.com", "AAAA"): result("example.com", (), chain),
                }
            )
            if succeeds:
                resolve_phase2_dns("example.com", backend, 5)
            else:
                with self.assertRaises(UrlSafetyError):
                    resolve_phase2_dns("example.com", backend, 5)

        addresses = tuple(f"8.8.8.{index}" for index in range(1, 18))
        too_many = FakeBackend(
            {
                ("example.com", "A"): result("example.com", addresses),
                ("example.com", "AAAA"): result("example.com"),
            }
        )
        with self.assertRaises(UrlSafetyError):
            resolve_phase2_dns("example.com", too_many, 5)
        for invalid in (0, -1, float("inf"), float("nan"), True):
            with self.assertRaises(ValueError):
                resolve_phase2_dns("example.com", too_many, invalid)


class FakeResolver:
    def __init__(self, nameservers):
        self.nameservers = nameservers
        self.calls = []

    def resolve(self, hostname, rdtype, **kwargs):
        self.calls.append((hostname, rdtype, kwargs))
        raise dns.resolver.NoAnswer


class DnspythonBackendTests(unittest.TestCase):
    def test_accepts_do53_configuration_and_uses_public_resolve_options(self):
        from phase2_dns import DnspythonQueryBackend

        resolver = FakeResolver(["192.168.1.1"])
        backend = DnspythonQueryBackend(resolver)
        answer = backend.query("example.com", "A", 5.0)
        self.assertEqual((), answer.addresses)
        self.assertEqual(
            [("example.com", "A", {"search": False, "lifetime": 5.0})],
            resolver.calls,
        )

    def test_rejects_non_do53_nameserver_before_query(self):
        from phase2_dns import DnspythonQueryBackend

        resolver = FakeResolver(
            [dns.nameserver.DoHNameserver("https://dns.example/dns-query")]
        )
        with self.assertRaises(UrlSafetyError):
            DnspythonQueryBackend(resolver)
        self.assertEqual([], resolver.calls)

    def test_maps_temporary_and_permanent_dns_failures(self):
        from phase2_dns import DnspythonQueryBackend

        class RaisingResolver(FakeResolver):
            def __init__(self, error):
                super().__init__(["192.168.1.1"])
                self.error = error

            def resolve(self, *args, **kwargs):
                raise self.error

        with self.assertRaises(DependencyError):
            DnspythonQueryBackend(RaisingResolver(dns.exception.Timeout())).query(
                "example.com", "A", 5
            )
        with self.assertRaises(UrlSafetyError):
            DnspythonQueryBackend(RaisingResolver(dns.resolver.NXDOMAIN())).query(
                "example.com", "A", 5
            )


if __name__ == "__main__":
    unittest.main()
