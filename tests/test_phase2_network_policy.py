import os
import socket
import unittest
from unittest.mock import patch

from phase2_contracts import DependencyError, DnsResolution, UrlSafetyError
from phase2_network_policy import (
    FakeDnsResolver,
    build_connection_plan,
    is_global_unicast,
    validate_global_ip,
    validate_peer_ip,
)
from phase2_url_policy import parse_policy_url


def policy_url(hostname="example.com"):
    return parse_policy_url(f"https://{hostname}/")


class GlobalIpTests(unittest.TestCase):
    def test_accepts_and_normalizes_public_ipv4_and_ipv6(self):
        self.assertEqual("8.8.8.8", validate_global_ip("8.8.8.8"))
        self.assertEqual(
            "2606:4700:4700::1111",
            validate_global_ip("2606:4700:4700:0:0:0:0:1111"),
        )
        self.assertTrue(is_global_unicast("8.8.8.8"))
        self.assertTrue(is_global_unicast("2606:4700:4700::1111"))

    def test_rejects_non_global_ipv4_ranges(self):
        values = (
            "0.0.0.0",
            "10.0.0.1",
            "100.64.0.1",
            "127.0.0.1",
            "169.254.169.254",
            "172.16.0.1",
            "192.0.2.1",
            "192.168.0.1",
            "198.18.0.1",
            "198.51.100.1",
            "203.0.113.1",
            "224.0.0.1",
            "240.0.0.1",
            "255.255.255.255",
        )
        for value in values:
            with self.subTest(value=value):
                self.assertFalse(is_global_unicast(value))
                with self.assertRaises(UrlSafetyError):
                    validate_global_ip(value)

    def test_rejects_non_global_ipv6_ranges(self):
        values = ("::", "::1", "fe80::1", "fc00::1", "ff02::1", "2001:db8::1")
        for value in values:
            with self.subTest(value=value):
                self.assertFalse(is_global_unicast(value))
                with self.assertRaises(UrlSafetyError):
                    validate_global_ip(value)

    def test_rechecks_ipv4_mapped_ipv6_target(self):
        self.assertTrue(is_global_unicast("::ffff:8.8.8.8"))
        self.assertEqual("::ffff:8.8.8.8", validate_global_ip("::ffff:8.8.8.8"))
        for value in ("::ffff:127.0.0.1", "::ffff:10.0.0.1"):
            with self.subTest(value=value):
                self.assertFalse(is_global_unicast(value))

    def test_rejects_invalid_ip_syntax(self):
        for value in ("", "not-an-ip", "999.1.1.1", None):
            with self.subTest(value=value):
                self.assertFalse(is_global_unicast(value))
                with self.assertRaises(UrlSafetyError):
                    validate_global_ip(value)


class ConnectionPlanTests(unittest.TestCase):
    def test_builds_immutable_plan_in_first_seen_normalized_order(self):
        resolution = DnsResolution(
            "example.com",
            ("8.8.8.8", "2606:4700:4700:0:0:0:0:1111", "8.8.8.8"),
            ("alias.example.com",),
        )

        plan = build_connection_plan(policy_url(), resolution)

        self.assertEqual(("8.8.8.8", "2606:4700:4700::1111"), plan.verified_ips)

    def test_rejects_empty_dns_result(self):
        with self.assertRaises(UrlSafetyError):
            build_connection_plan(policy_url(), DnsResolution("example.com", (), ()))

    def test_accepts_sixteen_unique_dns_addresses(self):
        addresses = tuple(f"8.8.8.{value}" for value in range(1, 17))
        plan = build_connection_plan(
            policy_url(),
            DnsResolution("example.com", addresses, ()),
        )
        self.assertEqual(addresses, plan.verified_ips)

    def test_rejects_seventeen_unique_dns_addresses(self):
        addresses = tuple(f"8.8.8.{value}" for value in range(1, 18))
        with self.assertRaises(UrlSafetyError):
            build_connection_plan(
                policy_url(),
                DnsResolution("example.com", addresses, ()),
            )

    def test_duplicate_dns_results_are_counted_after_normalization(self):
        addresses = tuple("8.8.8.8" for _ in range(17))
        plan = build_connection_plan(
            policy_url(),
            DnsResolution("example.com", addresses, ()),
        )
        self.assertEqual(("8.8.8.8",), plan.verified_ips)

    def test_rejects_entire_dns_result_when_one_address_is_private(self):
        resolution = DnsResolution("example.com", ("8.8.8.8", "10.0.0.1"), ())
        with self.assertRaises(UrlSafetyError):
            build_connection_plan(policy_url(), resolution)

    def test_accepts_eight_cname_hops_and_rejects_nine(self):
        accepted = tuple(f"alias{value}.example.com" for value in range(8))
        plan = build_connection_plan(
            policy_url(),
            DnsResolution("example.com", ("8.8.8.8",), accepted),
        )
        self.assertEqual(("8.8.8.8",), plan.verified_ips)

        rejected = accepted + ("alias8.example.com",)
        with self.assertRaises(UrlSafetyError):
            build_connection_plan(
                policy_url(),
                DnsResolution("example.com", ("8.8.8.8",), rejected),
            )

    def test_rejects_dns_hostname_mismatch(self):
        with self.assertRaises(UrlSafetyError):
            build_connection_plan(
                policy_url(),
                DnsResolution("other.example.com", ("8.8.8.8",), ()),
            )

    def test_accepts_verified_peer_and_normalizes_it(self):
        plan = build_connection_plan(
            policy_url(),
            DnsResolution("example.com", ("2606:4700:4700::1111",), ()),
        )
        self.assertEqual(
            "2606:4700:4700::1111",
            validate_peer_ip(plan, "2606:4700:4700:0:0:0:0:1111"),
        )

    def test_rejects_unverified_private_or_missing_peer(self):
        plan = build_connection_plan(
            policy_url(),
            DnsResolution("example.com", ("8.8.8.8",), ()),
        )
        for value in ("1.1.1.1", "10.0.0.1", "", None):
            with self.subTest(value=value), self.assertRaises(UrlSafetyError):
                validate_peer_ip(plan, value)


class FakeDnsResolverTests(unittest.TestCase):
    def test_returns_registered_fake_result_without_network_or_environment(self):
        expected = DnsResolution("example.com", ("8.8.8.8",), ())
        resolver = FakeDnsResolver({"example.com": expected})

        with patch.object(socket, "getaddrinfo", side_effect=AssertionError("network used")), patch.object(
            os,
            "getenv",
            side_effect=AssertionError("environment used"),
        ):
            self.assertEqual(expected, resolver.resolve("example.com"))

    def test_rejects_unregistered_or_case_changed_hostname(self):
        resolver = FakeDnsResolver(
            {"example.com": DnsResolution("example.com", ("8.8.8.8",), ())}
        )
        for hostname in ("missing.example.com", "EXAMPLE.COM"):
            with self.subTest(hostname=hostname), self.assertRaises(DependencyError):
                resolver.resolve(hostname)

    def test_copies_input_mapping_and_resolution_collections(self):
        addresses = ["8.8.8.8"]
        records = {
            "example.com": DnsResolution("example.com", addresses, []),
        }
        resolver = FakeDnsResolver(records)

        addresses.append("10.0.0.1")
        records["example.com"] = DnsResolution("example.com", ("1.1.1.1",), ())

        result = resolver.resolve("example.com")
        self.assertEqual(("8.8.8.8",), result.addresses)
        self.assertEqual((), result.cname_chain)

    def test_repeated_resolution_is_deterministic(self):
        resolver = FakeDnsResolver(
            {
                "example.com": DnsResolution(
                    "example.com",
                    ("8.8.8.8", "1.1.1.1"),
                    ("alias.example.com",),
                )
            }
        )
        self.assertEqual(
            resolver.resolve("example.com"),
            resolver.resolve("example.com"),
        )


if __name__ == "__main__":
    unittest.main()
