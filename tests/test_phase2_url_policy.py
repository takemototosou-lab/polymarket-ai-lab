import unittest

from phase2_contracts import UrlSafetyError
from phase2_url_policy import parse_policy_url, parse_redirect_url


class UrlPolicyTests(unittest.TestCase):
    def test_accepts_https_idna_and_removes_fragment(self):
        result = parse_policy_url("https://例え.テスト/a?q=1#frag")

        self.assertEqual("xn--r8jz45g.xn--zckzah", result.hostname)
        self.assertEqual("https://xn--r8jz45g.xn--zckzah/a?q=1", result.request_url)
        self.assertEqual(443, result.port)
        self.assertEqual("/a?q=1", result.path_and_query)

    def test_preserves_path_query_and_valid_percent_escapes(self):
        result = parse_policy_url("https://example.com/a/../b/%2F?q=b&a=1")

        self.assertEqual("/a/../b/%2F?q=b&a=1", result.path_and_query)
        self.assertEqual(
            "https://example.com/a/../b/%2F?q=b&a=1",
            result.request_url,
        )

    def test_accepts_public_ipv4_literal(self):
        result = parse_policy_url("https://8.8.8.8/path")
        self.assertEqual("8.8.8.8", result.hostname)
        self.assertEqual("https://8.8.8.8/path", result.request_url)

    def test_accepts_bracketed_ipv6_literal_syntax(self):
        result = parse_policy_url("https://[2606:4700:4700::1111]/path")
        self.assertEqual("2606:4700:4700::1111", result.hostname)
        self.assertEqual(
            "https://[2606:4700:4700::1111]/path",
            result.request_url,
        )

    def test_rejects_ambiguous_or_unsafe_urls(self):
        values = (
            "http://example.com/",
            "//example.com/",
            "/relative",
            "https://u:p@example.com/",
            "https://example.com:444/",
            "https:///missing-host",
            "https://example.com./",
            "https://a..example/",
            "https://example.com\\@private/",
            "https://example.com/%ZZ",
            "https://example.com/\x00",
            "https://example.com/\r",
            "https://example.com/\n",
            "https://example.com/\t",
            "https://example.com/\x7f",
            "https://example.com/\x85",
        )
        for value in values:
            with self.subTest(value=repr(value)), self.assertRaises(UrlSafetyError):
                parse_policy_url(value)

    def test_rejects_invalid_idna(self):
        for value in ("https://a_b.example/", "https://\ud800.example/"):
            with self.subTest(value=repr(value)), self.assertRaises(UrlSafetyError):
                parse_policy_url(value)

    def test_rejects_compatibility_hostname_that_changes_authority(self):
        with self.assertRaises(UrlSafetyError):
            parse_policy_url("https://ｅxample.com/")

    def test_rejects_empty_hostname_label(self):
        for value in ("https://.example.com/", "https://example..com/"):
            with self.subTest(value=value), self.assertRaises(UrlSafetyError):
                parse_policy_url(value)

    def test_rejects_invalid_percent_escape(self):
        for value in ("https://example.com/%", "https://example.com/%2"):
            with self.subTest(value=value), self.assertRaises(UrlSafetyError):
                parse_policy_url(value)

    def test_enforces_url_utf8_byte_boundary(self):
        prefix = "https://example.com/"
        accepted = prefix + "a" * (2048 - len(prefix.encode("utf-8")))

        self.assertEqual(2048, len(accepted.encode("utf-8")))
        self.assertEqual(accepted, parse_policy_url(accepted).request_url)
        with self.assertRaises(UrlSafetyError):
            parse_policy_url(accepted + "a")

    def test_enforces_label_63_byte_boundary(self):
        self.assertEqual(
            "a" * 63 + ".com",
            parse_policy_url("https://" + "a" * 63 + ".com/").hostname,
        )
        with self.assertRaises(UrlSafetyError):
            parse_policy_url("https://" + "a" * 64 + ".com/")

    def test_enforces_hostname_253_byte_boundary(self):
        accepted_host = ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 61))
        rejected_host = accepted_host + "e"

        self.assertEqual(253, len(accepted_host.encode("ascii")))
        self.assertEqual(
            accepted_host,
            parse_policy_url(f"https://{accepted_host}/").hostname,
        )
        self.assertEqual(254, len(rejected_host.encode("ascii")))
        with self.assertRaises(UrlSafetyError):
            parse_policy_url(f"https://{rejected_host}/")

    def test_relative_redirect_is_reparsed_from_start(self):
        current = parse_policy_url("https://example.com/a/b?q=1")
        result = parse_redirect_url(current, "../next?z=2#frag")

        self.assertEqual("https://example.com/next?z=2", result.request_url)

    def test_rejects_unsafe_redirect_targets(self):
        current = parse_policy_url("https://example.com/base")
        values = (
            "http://example.com/",
            "https://u:p@example.com/",
            "https://example.com:444/",
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(UrlSafetyError):
                parse_redirect_url(current, value)


if __name__ == "__main__":
    unittest.main()
