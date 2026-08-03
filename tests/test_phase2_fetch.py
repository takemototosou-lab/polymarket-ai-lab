import os
import socket
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from phase2_contracts import DependencyError, DnsResolution, UrlSafetyError
from phase2_fetch import (
    FIXED_REQUEST_HEADERS,
    FakeHttpResponse,
    FakeHttpTransport,
    FetchLimits,
    follow_redirects,
)
from phase2_network_policy import FakeDnsResolver, build_connection_plan
from phase2_url_policy import parse_policy_url


def response(status=200, *, headers=(), chunks=(b"",), peer_ip="8.8.8.8"):
    return FakeHttpResponse(status, tuple(headers), tuple(chunks), peer_ip)


def resolver(*hosts, private_hosts=()):
    records = {
        host: DnsResolution(
            host,
            (("10.0.0.1",) if host in private_hosts else ("8.8.8.8",)),
            (),
        )
        for host in hosts
    }
    return FakeDnsResolver(records)


def plan(host="a.example"):
    policy = parse_policy_url(f"https://{host}/")
    return build_connection_plan(
        policy,
        DnsResolution(host, ("8.8.8.8",), ()),
    )


def redirect(location, *, peer_ip="8.8.8.8", body=b""):
    return response(
        302,
        headers=(("Location", location),),
        chunks=(body,),
        peer_ip=peer_ip,
    )


class FetchLimitTests(unittest.TestCase):
    def test_limits_are_frozen(self):
        limits = FetchLimits()
        with self.assertRaises(FrozenInstanceError):
            limits.max_redirects = 1

    def test_rejects_non_positive_bool_and_above_absolute_limits(self):
        invalid = (
            {"max_redirects": -1},
            {"max_response_bytes": 0},
            {"max_response_bytes": True},
            {"max_response_bytes": 4_194_305},
            {"max_decoded_chars": 20_001},
            {"max_header_bytes": 65_537},
            {"max_header_count": 101},
            {"max_single_header_bytes": 8_193},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                FetchLimits(**values)


class FakeTransportTests(unittest.TestCase):
    def test_returns_registered_response_with_exact_fixed_get_contract(self):
        transport = FakeHttpTransport(
            {"https://a.example/": (response(),)}
        )

        result = transport.get(plan(), headers=FIXED_REQUEST_HEADERS)

        self.assertEqual(200, result.status_code)
        self.assertEqual(1, len(transport.calls))
        call = transport.calls[0]
        self.assertEqual("GET", call.method)
        self.assertEqual("https://a.example/", call.request_url)
        self.assertEqual(FIXED_REQUEST_HEADERS, call.headers)
        names = tuple(name for name, _ in call.headers)
        self.assertEqual(
            ("Accept", "Accept-Encoding", "User-Agent", "Connection"),
            names,
        )
        self.assertNotIn("Cookie", names)
        self.assertNotIn("Authorization", names)
        self.assertNotIn("Referer", names)

    def test_rejects_unregistered_request_or_custom_headers(self):
        transport = FakeHttpTransport({})
        with self.assertRaises(DependencyError):
            transport.get(plan(), headers=FIXED_REQUEST_HEADERS)

        transport = FakeHttpTransport({"https://a.example/": (response(),)})
        with self.assertRaises(DependencyError):
            transport.get(plan(), headers=(("Cookie", "secret"),))

    def test_requires_verified_peer_ip(self):
        for peer_ip in (None, "", "1.1.1.1", "10.0.0.1"):
            transport = FakeHttpTransport(
                {"https://a.example/": (response(peer_ip=peer_ip),)}
            )
            with self.subTest(peer_ip=peer_ip), self.assertRaises(UrlSafetyError):
                transport.get(plan(), headers=FIXED_REQUEST_HEADERS)

    def test_copies_response_mapping_and_collections(self):
        headers = [["Content-Type", "text/html"]]
        chunks = [b"original"]
        responses = {
            "https://a.example/": [FakeHttpResponse(200, headers, chunks, "8.8.8.8")]
        }
        transport = FakeHttpTransport(responses)

        headers.append(["X-Late", "change"])
        chunks.append(b"changed")
        responses["https://a.example/"] = [response(status=500)]

        result = transport.get(plan(), headers=FIXED_REQUEST_HEADERS)
        self.assertEqual((("Content-Type", "text/html"),), result.headers)
        self.assertEqual((b"original",), result.body_chunks)

    def test_calls_are_deterministic_and_immutable_to_callers(self):
        transport = FakeHttpTransport(
            {"https://a.example/": (response(), response(status=204))}
        )
        transport.get(plan(), headers=FIXED_REQUEST_HEADERS)
        first_snapshot = transport.calls
        transport.get(plan(), headers=FIXED_REQUEST_HEADERS)

        self.assertIsInstance(first_snapshot, tuple)
        self.assertEqual(1, len(first_snapshot))
        self.assertEqual((200, 204), tuple(call.response_status for call in transport.calls))

    def test_does_not_use_network_or_environment(self):
        transport = FakeHttpTransport({"https://a.example/": (response(),)})
        with patch.object(socket, "getaddrinfo", side_effect=AssertionError("network")), patch.object(
            os,
            "getenv",
            side_effect=AssertionError("environment"),
        ):
            transport.get(plan(), headers=FIXED_REQUEST_HEADERS)


class RedirectTests(unittest.TestCase):
    def test_follows_one_relative_redirect_and_records_fixed_chain(self):
        transport = FakeHttpTransport(
            {
                "https://a.example/": (redirect("/next"),),
                "https://a.example/next": (response(),),
            }
        )
        trace = follow_redirects(
            plan(),
            resolver("a.example"),
            transport,
            FetchLimits(),
        )

        self.assertEqual("https://a.example/next", trace.final_url.request_url)
        self.assertEqual(1, len(trace.redirect_chain))
        self.assertEqual(
            ("https://a.example/", "https://a.example/next"),
            tuple(call.request_url for call in transport.calls),
        )
        self.assertEqual(("8.8.8.8", "8.8.8.8"), trace.peer_ips)

    def test_accepts_three_redirects_and_rejects_fourth(self):
        responses = {
            "https://a.example/": (redirect("/1"),),
            "https://a.example/1": (redirect("/2"),),
            "https://a.example/2": (redirect("/3"),),
            "https://a.example/3": (response(),),
        }
        trace = follow_redirects(
            plan(), resolver("a.example"), FakeHttpTransport(responses), FetchLimits()
        )
        self.assertEqual(3, len(trace.redirect_chain))

        responses["https://a.example/3"] = (redirect("/4"),)
        responses["https://a.example/4"] = (response(),)
        with self.assertRaises(UrlSafetyError):
            follow_redirects(
                plan(),
                resolver("a.example"),
                FakeHttpTransport(responses),
                FetchLimits(),
            )

    def test_rejects_unsafe_redirect_locations(self):
        values = (
            "http://a.example/next",
            "https://user@a.example/next",
            "https://a.example:444/next",
        )
        for location in values:
            with self.subTest(location=location), self.assertRaises(UrlSafetyError):
                follow_redirects(
                    plan(),
                    resolver("a.example"),
                    FakeHttpTransport(
                        {"https://a.example/": (redirect(location),)}
                    ),
                    FetchLimits(),
                )

    def test_rejects_private_redirect_dns_result(self):
        transport = FakeHttpTransport(
            {"https://a.example/": (redirect("https://private.example/"),)}
        )
        with self.assertRaises(UrlSafetyError):
            follow_redirects(
                plan(),
                resolver("a.example", "private.example", private_hosts=("private.example",)),
                transport,
                FetchLimits(),
            )

    def test_rejects_loop_and_missing_or_conflicting_location(self):
        values = (
            redirect("https://a.example/"),
            response(302, headers=()),
            response(302, headers=(("Location", "/a"), ("Location", "/b"))),
        )
        for value in values:
            with self.subTest(headers=value.headers), self.assertRaises(UrlSafetyError):
                follow_redirects(
                    plan(),
                    resolver("a.example"),
                    FakeHttpTransport({"https://a.example/": (value,)}),
                    FetchLimits(),
                )

    def test_revalidates_dns_and_peer_at_every_redirect_hop(self):
        transport = FakeHttpTransport(
            {
                "https://a.example/": (redirect("https://b.example/"),),
                "https://b.example/": (response(peer_ip="1.1.1.1"),),
            }
        )
        fake_resolver = FakeDnsResolver(
            {
                "b.example": DnsResolution("b.example", ("1.1.1.1",), ()),
            }
        )
        trace = follow_redirects(plan(), fake_resolver, transport, FetchLimits())
        self.assertEqual(("8.8.8.8", "1.1.1.1"), trace.peer_ips)
        self.assertEqual(
            (("8.8.8.8",), ("1.1.1.1",)),
            tuple(item.addresses for item in trace.resolutions),
        )


if __name__ == "__main__":
    unittest.main()
