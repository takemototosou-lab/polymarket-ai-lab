import os
import socket
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from phase2_contracts import (
    DependencyError,
    DnsResolution,
    MimeRejectedError,
    ResponseContractError,
    UrlSafetyError,
)
from phase2_fetch import (
    FIXED_REQUEST_HEADERS,
    FakeHttpResponse,
    FakeHttpTransport,
    FetchLimits,
    RawFetchTrace,
    RetryableHttpStatus,
    fetch_validated_html,
    follow_redirects,
    validate_response,
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


def raw_trace(final_response):
    policy = parse_policy_url("https://a.example/")
    return RawFetchTrace(
        requested_url=policy,
        final_url=policy,
        redirect_chain=(),
        resolutions=(DnsResolution("a.example", ("8.8.8.8",), ()),),
        peer_ips=("8.8.8.8",),
        final_response=final_response,
    )


def html_response(body=b"<html>ok</html>", *, headers=(), status=200):
    return response(
        status,
        headers=(("Content-Type", "text/html; charset=utf-8"),) + tuple(headers),
        chunks=(body,),
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

    def test_rejects_invalid_redirect_headers_or_framing(self):
        invalid = (
            response(
                302,
                headers=(("Location", "/next"), ("Bad Header", "value")),
            ),
            response(
                302,
                headers=(("Location", "/next"), ("Content-Length", "2")),
                chunks=(b"x",),
            ),
            response(
                302,
                headers=(("Location", "/next"), ("Content-Encoding", "gzip")),
            ),
        )
        for value in invalid:
            with self.subTest(headers=value.headers), self.assertRaises(
                (ResponseContractError, MimeRejectedError)
            ):
                follow_redirects(
                    plan(),
                    resolver("a.example"),
                    FakeHttpTransport({"https://a.example/": (value,)}),
                    FetchLimits(),
                )


class ResponseHeaderTests(unittest.TestCase):
    def test_accepts_headers_at_count_and_byte_boundaries(self):
        count_headers = (("X-Test", "a"),) * 63
        result = validate_response(
            raw_trace(html_response(headers=count_headers)),
            FetchLimits(max_header_count=64),
            "2026-08-03T00:00:00Z",
        )
        self.assertEqual("text/html", result.content_type)

        boundary = (("Content-Type", "text/html"), ("X", "a" * 10))
        byte_count = sum(len(name.encode()) + len(value.encode()) for name, value in boundary)
        result = validate_response(
            raw_trace(response(200, headers=boundary, chunks=(b"x",))),
            FetchLimits(max_header_bytes=byte_count, max_single_header_bytes=21),
            "2026-08-03T00:00:00Z",
        )
        self.assertEqual("x", result.decoded_html)

    def test_rejects_header_count_total_and_single_header_plus_one(self):
        count_headers = (("Content-Type", "text/html"),) + (("X-Test", "a"),) * 64
        cases = (
            (
                response(200, headers=count_headers, chunks=(b"x",)),
                FetchLimits(max_header_count=64),
            ),
            (
                response(
                    200,
                    headers=(("Content-Type", "text/html"), ("X", "a" * 10)),
                    chunks=(b"x",),
                ),
                FetchLimits(max_header_bytes=31),
            ),
            (
                response(
                    200,
                    headers=(("Content-Type", "text/html"), ("X", "a" * 11)),
                    chunks=(b"x",),
                ),
                FetchLimits(max_single_header_bytes=11),
            ),
        )
        for value, limits in cases:
            with self.subTest(limits=limits), self.assertRaises(ResponseContractError):
                validate_response(raw_trace(value), limits, "2026-08-03T00:00:00Z")

    def test_rejects_duplicate_or_invalid_content_length_and_ambiguous_framing(self):
        invalid_headers = (
            (("Content-Length", "1"), ("Content-Length", "1")),
            (("Content-Length", "1"), ("Content-Length", "2")),
            (("Content-Length", "1"), ("Transfer-Encoding", "chunked")),
            (("Content-Length", "-1"),),
            (("Content-Length", "abc"),),
            (("Transfer-Encoding", "gzip"),),
            (("Transfer-Encoding", "chunked"), ("Transfer-Encoding", "chunked")),
        )
        for headers in invalid_headers:
            value = html_response(b"x", headers=headers)
            with self.subTest(headers=headers), self.assertRaises(ResponseContractError):
                validate_response(raw_trace(value), FetchLimits(), "2026-08-03T00:00:00Z")

    def test_rejects_invalid_header_name_or_control_value(self):
        invalid = (
            (("Bad Header", "value"),),
            (("X-Test", "bad\rvalue"),),
            (("X-Test", "bad\nvalue"),),
            (("X-Test", "bad\x00value"),),
            (("X-Test", "bad\x7fvalue"),),
        )
        for headers in invalid:
            with self.subTest(headers=headers), self.assertRaises(ResponseContractError):
                validate_response(
                    raw_trace(html_response(headers=headers)),
                    FetchLimits(),
                    "2026-08-03T00:00:00Z",
                )

    def test_validates_retryable_response_headers_before_classification(self):
        value = response(
            503,
            headers=(("Retry-After", "1"), ("Bad Header", "value")),
        )
        with self.assertRaises(ResponseContractError):
            validate_response(
                raw_trace(value),
                FetchLimits(),
                "2026-08-03T00:00:00Z",
            )


class ResponseBodyTests(unittest.TestCase):
    def test_accepts_content_length_and_streaming_at_byte_limit(self):
        for headers in ((("Content-Length", "4"),), (("Transfer-Encoding", "chunked"),), ()):
            value = html_response(b"test", headers=headers)
            result = validate_response(
                raw_trace(value),
                FetchLimits(max_response_bytes=4),
                "2026-08-03T00:00:00Z",
            )
            self.assertEqual(4, result.response_bytes)

    def test_rejects_declared_or_streamed_limit_plus_one_and_length_mismatch(self):
        cases = (
            html_response(b"test", headers=(("Content-Length", "5"),)),
            html_response(b"12345"),
            response(
                200,
                headers=(("Content-Type", "text/html"), ("Transfer-Encoding", "chunked")),
                chunks=(b"12", b"345"),
            ),
            html_response(b"test", headers=(("Content-Length", "3"),)),
        )
        for value in cases:
            with self.subTest(headers=value.headers), self.assertRaises(ResponseContractError):
                validate_response(
                    raw_trace(value),
                    FetchLimits(max_response_bytes=4),
                    "2026-08-03T00:00:00Z",
                )

    def test_rejects_empty_or_nul_body(self):
        for body in (b"", b"a\x00b"):
            with self.subTest(body=body), self.assertRaises(ResponseContractError):
                validate_response(
                    raw_trace(html_response(body)),
                    FetchLimits(),
                    "2026-08-03T00:00:00Z",
                )


class MimeAndDecodeTests(unittest.TestCase):
    def test_accepts_html_xhtml_missing_encoding_and_identity(self):
        headers = (
            (("Content-Type", "text/html"),),
            (("Content-Type", "application/xhtml+xml; charset=UTF-8"),),
            (("Content-Type", "text/html"), ("Content-Encoding", "identity")),
        )
        for values in headers:
            result = validate_response(
                raw_trace(response(200, headers=values, chunks=(b"ok",))),
                FetchLimits(),
                "2026-08-03T00:00:00Z",
            )
            self.assertEqual("ok", result.decoded_html)

    def test_rejects_compression_multiple_or_unknown_content_encoding(self):
        values = ("gzip", "deflate", "br", "identity, gzip", "unknown")
        for encoding in values:
            with self.subTest(encoding=encoding), self.assertRaises(MimeRejectedError):
                validate_response(
                    raw_trace(html_response(headers=(("Content-Encoding", encoding),))),
                    FetchLimits(),
                    "2026-08-03T00:00:00Z",
                )

    def test_rejects_missing_or_non_html_content_type(self):
        values = (
            (),
            (("Content-Type", "text/plain"),),
            (("Content-Type", "application/json"),),
            (("Content-Type", "application/pdf"),),
            (("Content-Type", "application/octet-stream"),),
        )
        for headers in values:
            with self.subTest(headers=headers), self.assertRaises(MimeRejectedError):
                validate_response(
                    raw_trace(response(200, headers=headers, chunks=(b"ok",))),
                    FetchLimits(),
                    "2026-08-03T00:00:00Z",
                )

    def test_uses_utf8_by_default_and_removes_utf8_bom(self):
        for body in ("日本語".encode(), b"\xef\xbb\xbf" + "日本語".encode()):
            result = validate_response(
                raw_trace(response(200, headers=(("Content-Type", "text/html"),), chunks=(body,))),
                FetchLimits(),
                "2026-08-03T00:00:00Z",
            )
            self.assertEqual("日本語", result.decoded_html)
            self.assertEqual("utf-8", result.charset)

    def test_rejects_bom_conflict_multiple_or_unknown_charset_and_decode_error(self):
        cases = (
            response(
                200,
                headers=(("Content-Type", "text/html; charset=latin-1"),),
                chunks=(b"\xef\xbb\xbfhello",),
            ),
            response(
                200,
                headers=(("Content-Type", "text/html; charset=utf-8; charset=ascii"),),
                chunks=(b"hello",),
            ),
            response(
                200,
                headers=(("Content-Type", "text/html; charset=unknown-xyz"),),
                chunks=(b"hello",),
            ),
            response(
                200,
                headers=(("Content-Type", "text/html; charset=utf-8"),),
                chunks=(b"\xff",),
            ),
        )
        for value in cases:
            with self.subTest(headers=value.headers), self.assertRaises(ResponseContractError):
                validate_response(raw_trace(value), FetchLimits(), "2026-08-03T00:00:00Z")

    def test_rejects_decoded_surrogate_or_disallowed_controls(self):
        cases = (
            ("utf-7", b"+2AA-"),
            ("latin-1", b"a\x85b"),
            ("utf-8", b"a\x01b"),
        )
        for charset, body in cases:
            value = response(
                200,
                headers=(("Content-Type", f"text/html; charset={charset}"),),
                chunks=(body,),
            )
            with self.subTest(charset=charset), self.assertRaises(ResponseContractError):
                validate_response(raw_trace(value), FetchLimits(), "2026-08-03T00:00:00Z")

    def test_allows_html_whitespace_controls(self):
        result = validate_response(
            raw_trace(html_response(b"a\r\n\tb")),
            FetchLimits(),
            "2026-08-03T00:00:00Z",
        )
        self.assertEqual("a\r\n\tb", result.decoded_html)

    def test_accepts_twenty_thousand_chars_and_rejects_plus_one_without_truncation(self):
        result = validate_response(
            raw_trace(html_response(b"a" * 20_000)),
            FetchLimits(),
            "2026-08-03T00:00:00Z",
        )
        self.assertEqual(20_000, result.decoded_chars)
        with self.assertRaises(ResponseContractError):
            validate_response(
                raw_trace(html_response(b"a" * 20_001)),
                FetchLimits(max_response_bytes=20_001),
                "2026-08-03T00:00:00Z",
            )


class StatusAndOrchestrationTests(unittest.TestCase):
    def test_accepts_only_status_200_as_final_body(self):
        for status in (100, 201, 204, 300, 400, 401, 403, 404, 500):
            with self.subTest(status=status), self.assertRaises(ResponseContractError):
                validate_response(
                    raw_trace(html_response(status=status)),
                    FetchLimits(),
                    "2026-08-03T00:00:00Z",
                )

    def test_marks_only_retryable_http_statuses(self):
        for status in (429, 502, 503, 504):
            with self.subTest(status=status), self.assertRaises(RetryableHttpStatus) as caught:
                validate_response(
                    raw_trace(html_response(status=status)),
                    FetchLimits(),
                    "2026-08-03T00:00:00Z",
                )
            self.assertEqual(status, caught.exception.status_code)

    def test_fetch_validated_html_builds_fixed_result_without_output_or_files(self):
        transport = FakeHttpTransport(
            {
                "https://a.example/": (
                    html_response(b"<html>ok</html>"),
                )
            }
        )
        result = fetch_validated_html(
            plan(),
            resolver=resolver("a.example"),
            transport=transport,
            limits=FetchLimits(),
            now=lambda: "2026-08-03T00:00:00Z",
        )

        self.assertEqual("https://a.example/", result.requested_url)
        self.assertEqual("<html>ok</html>", result.decoded_html)
        self.assertEqual("2026-08-03T00:00:00Z", result.retrieved_at)


if __name__ == "__main__":
    unittest.main()
