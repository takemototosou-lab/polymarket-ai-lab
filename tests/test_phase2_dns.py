import unittest
from types import SimpleNamespace

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
        result = self.results.get((hostname, rdtype))
        if result is None and rdtype == "CNAME":
            result = DnsQueryResult(hostname, hostname, (), ())
        if result is None:
            raise AssertionError(f"missing fake DNS result: {hostname} {rdtype}")
        if isinstance(result, Exception):
            raise result
        return result


class FakeMonotonicClock:
    def __init__(self, start=0.0):
        self.current = float(start)

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current += float(seconds)


class TimedFakeBackend(FakeBackend):
    def __init__(self, results, clock, durations):
        super().__init__(results)
        self.clock = clock
        self.durations = durations

    def query(self, hostname, rdtype, timeout_seconds):
        self.calls.append((hostname, rdtype, timeout_seconds))
        self.clock.advance(
            self.durations.get((hostname, rdtype), self.durations.get(rdtype, 0))
        )
        result_value = self.results.get((hostname, rdtype))
        if result_value is None and rdtype == "CNAME":
            result_value = DnsQueryResult(hostname, hostname, (), ())
        if result_value is None:
            raise AssertionError(f"missing fake DNS result: {hostname} {rdtype}")
        if isinstance(result_value, Exception):
            raise result_value
        return result_value


def result(hostname, addresses=(), chain=(), canonical=None):
    return DnsQueryResult(
        hostname,
        canonical or (chain[-1] if chain else hostname),
        tuple(addresses),
        tuple(chain),
    )


class DnsOrchestrationTests(unittest.TestCase):
    def test_a_and_aaaa_share_one_deadline(self):
        from phase2_dns import resolve_phase2_dns

        clock = FakeMonotonicClock(10)
        backend = TimedFakeBackend(
            {
                ("example.com", "A"): result("example.com", ("8.8.8.8",)),
                ("example.com", "AAAA"): result(
                    "example.com", ("2606:4700:4700::1111",)
                ),
            },
            clock,
            {"A": 2, "AAAA": 1},
        )
        resolve_phase2_dns("example.com", backend, 5, clock=clock)
        self.assertEqual(("example.com", "CNAME", 5), backend.calls[0])
        self.assertEqual(5, backend.calls[1][2])
        self.assertEqual(3, backend.calls[2][2])

    def test_exhausted_deadline_skips_aaaa_and_rejects_partial_a(self):
        from phase2_dns import resolve_phase2_dns

        clock = FakeMonotonicClock()
        backend = TimedFakeBackend(
            {
                ("example.com", "A"): result("example.com", ("8.8.8.8",)),
                ("example.com", "AAAA"): result("example.com"),
            },
            clock,
            {"A": 5, "AAAA": 0},
        )
        with self.assertRaises(DependencyError):
            resolve_phase2_dns("example.com", backend, 5, clock=clock)
        self.assertEqual(
            [("example.com", "CNAME", 5), ("example.com", "A", 5)],
            backend.calls,
        )

    def test_cname_and_both_address_families_keep_the_same_deadline(self):
        from phase2_dns import resolve_phase2_dns

        clock = FakeMonotonicClock(20)
        chain = ("target.example",)
        backend = TimedFakeBackend(
            {
                ("example.com", "CNAME"): result(
                    "example.com", chain=chain
                ),
                ("target.example", "A"): result(
                    "target.example", ("8.8.8.8",)
                ),
                ("target.example", "AAAA"): result(
                    "target.example", ("2606:4700:4700::1111",)
                ),
            },
            clock,
            {
                ("example.com", "CNAME"): 1.25,
                ("target.example", "CNAME"): 0,
                "A": 0.75,
                "AAAA": 0,
            },
        )
        resolved = resolve_phase2_dns("example.com", backend, 5, clock=clock)
        self.assertEqual(chain, resolved.cname_chain)
        self.assertEqual(
            ["CNAME", "CNAME", "A", "AAAA"],
            [call[1] for call in backend.calls],
        )
        self.assertEqual(3.75, backend.calls[1][2])
        self.assertEqual(3.75, backend.calls[2][2])
        self.assertEqual(3, backend.calls[3][2])
        self.assertEqual(22, clock.current)

    def test_follows_one_cname_owner_at_a_time_before_address_queries(self):
        from phase2_dns import resolve_phase2_dns

        backend = FakeBackend(
            {
                ("example.com", "CNAME"): result(
                    "example.com", chain=("a.example",)
                ),
                ("a.example", "CNAME"): result(
                    "a.example", chain=("b.example",)
                ),
                ("b.example", "CNAME"): result("b.example"),
                ("b.example", "A"): result("b.example", ("8.8.8.8",)),
                ("b.example", "AAAA"): result("b.example"),
            }
        )
        resolved = resolve_phase2_dns(
            "example.com", backend, 5, clock=FakeMonotonicClock()
        )
        self.assertEqual(("a.example", "b.example"), resolved.cname_chain)
        self.assertEqual(
            [
                ("example.com", "CNAME"),
                ("a.example", "CNAME"),
                ("b.example", "CNAME"),
                ("b.example", "A"),
                ("b.example", "AAAA"),
            ],
            [(hostname, rdtype) for hostname, rdtype, _ in backend.calls],
        )

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
        resolved = resolve_phase2_dns(
            "example.com", backend, 5.0, clock=FakeMonotonicClock()
        )
        self.assertEqual(
            ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"),
            resolved.addresses,
        )
        self.assertEqual(
            [
                ("example.com", "CNAME", 5.0),
                ("example.com", "A", 5.0),
                ("example.com", "AAAA", 5.0),
            ],
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
            final_hostname = chain[-1] if chain else "example.com"
            backend = FakeBackend(
                {
                    ("example.com", "CNAME"): result(
                        "example.com", chain=chain
                    ),
                    (final_hostname, "A"): result(
                        final_hostname, ("8.8.8.8",)
                    ),
                    (final_hostname, "AAAA"): result(final_hostname),
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


class FakeName:
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return self.value


class FakeCnameRecord:
    def __init__(self, target):
        self.target = FakeName(target)


class FakeCnameRrset:
    rdtype = dns.rdatatype.CNAME

    def __init__(self, owner, *targets):
        self.name = FakeName(owner)
        self.records = tuple(FakeCnameRecord(target) for target in targets)

    def __iter__(self):
        return iter(self.records)


class FakeAnswer:
    def __init__(self, canonical, rrsets, addresses=()):
        self.canonical_name = FakeName(canonical)
        self.response = SimpleNamespace(answer=tuple(rrsets))
        self.addresses = tuple(addresses)

    def __iter__(self):
        return iter(self.addresses)


class AnswerResolver(FakeResolver):
    def __init__(self, answer):
        super().__init__(["192.168.1.1"])
        self.answer = answer

    def resolve(self, hostname, rdtype, **kwargs):
        self.calls.append((hostname, rdtype, kwargs))
        return self.answer


class DnspythonBackendTests(unittest.TestCase):
    def _query_cname(self, canonical, rrsets):
        from phase2_dns import DnspythonQueryBackend

        return DnspythonQueryBackend(
            AnswerResolver(FakeAnswer(canonical, rrsets))
        ).query("example.com", "A", 5)

    def test_tracks_single_cname_owner_to_target(self):
        answer = self._query_cname(
            "target.example.",
            (FakeCnameRrset("example.com.", "target.example."),),
        )
        self.assertEqual(("target.example",), answer.cname_chain)

    def test_accepts_eight_cname_hops_and_rejects_nine(self):
        for count, accepted in ((8, True), (9, False)):
            names = ["example.com"] + [f"c{i}.example" for i in range(count)]
            rrsets = tuple(
                FakeCnameRrset(f"{owner}.", f"{target}.")
                for owner, target in zip(names, names[1:])
            )
            if accepted:
                answer = self._query_cname(f"{names[-1]}.", rrsets)
                self.assertEqual(tuple(names[1:]), answer.cname_chain)
            else:
                with self.assertRaises(UrlSafetyError):
                    self._query_cname(f"{names[-1]}.", rrsets)

    def test_rejects_cname_loop(self):
        rrsets = (
            FakeCnameRrset("example.com.", "a.example."),
            FakeCnameRrset("a.example.", "example.com."),
        )
        with self.assertRaises(UrlSafetyError):
            self._query_cname("example.com.", rrsets)

    def test_rejects_same_owner_with_two_cname_targets(self):
        rrsets = (
            FakeCnameRrset(
                "example.com.", "a.example.", "b.example."
            ),
        )
        with self.assertRaises(UrlSafetyError):
            self._query_cname("a.example.", rrsets)

    def test_rejects_disconnected_cname_chain(self):
        rrsets = (
            FakeCnameRrset("example.com.", "target.example."),
            FakeCnameRrset("elsewhere.example.", "unused.example."),
        )
        with self.assertRaises(UrlSafetyError):
            self._query_cname("target.example.", rrsets)

    def test_rejects_malformed_cname_target(self):
        for target in ("bad target.", "target.example:443"):
            with self.subTest(target=target), self.assertRaises(UrlSafetyError):
                self._query_cname(
                    "target.example.",
                    (FakeCnameRrset("example.com.", target),),
                )

    def test_accepts_do53_configuration_and_uses_public_resolve_options(self):
        from phase2_dns import DnspythonQueryBackend

        resolver = FakeResolver(["192.168.1.1"])
        backend = DnspythonQueryBackend(resolver)
        answer = backend.query("example.com", "A", 5.0)
        self.assertEqual((), answer.addresses)
        self.assertEqual(
            [
                (
                    "example.com",
                    "A",
                    {
                        "search": False,
                        "lifetime": 5.0,
                        "raise_on_no_answer": False,
                    },
                )
            ],
            resolver.calls,
        )

    def test_preserves_cname_when_requested_family_has_no_addresses(self):
        from phase2_dns import DnspythonQueryBackend

        class Name:
            def __init__(self, value):
                self.value = value

            def __str__(self):
                return self.value

        class CnameRecord:
            target = Name("target.example.")

        class CnameRrset:
            rdtype = dns.rdatatype.CNAME
            name = Name("example.com.")

            def __iter__(self):
                return iter((CnameRecord(),))

        class EmptyAnswer:
            canonical_name = Name("target.example.")
            response = SimpleNamespace(answer=(CnameRrset(),))

            def __iter__(self):
                return iter(())

        class EmptyAnswerResolver(FakeResolver):
            def resolve(self, hostname, rdtype, **kwargs):
                self.calls.append((hostname, rdtype, kwargs))
                return EmptyAnswer()

        answer = DnspythonQueryBackend(
            EmptyAnswerResolver(["192.168.1.1"])
        ).query("example.com", "AAAA", 5)
        self.assertEqual("target.example", answer.canonical_hostname)
        self.assertEqual(("target.example",), answer.cname_chain)
        self.assertEqual((), answer.addresses)

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
