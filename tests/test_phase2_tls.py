import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase2_contracts import ConnectionPlan, DependencyError, UrlSafetyError
from phase2_url_policy import parse_policy_url


class FakeTlsSocket:
    def __init__(self, peer="8.8.8.8", sends=None):
        self.peer = peer
        self.closed = False
        self.sends = list(sends or ())
        self.sent = bytearray()

    def getpeername(self):
        return (self.peer, 443)

    def send(self, data):
        count = self.sends.pop(0) if self.sends else len(data)
        self.sent.extend(data[:count])
        return count

    def recv(self, maximum):
        return b"x" * min(maximum, 2)

    def close(self):
        self.closed = True


class FakeRawSocket:
    def __init__(self, outcome=None, clock=None, duration=0):
        self.outcome = outcome
        self.clock = clock
        self.duration = duration
        self.connected = None
        self.timeout = None
        self.timeouts = []
        self.closed = False

    def settimeout(self, value):
        self.timeout = value
        self.timeouts.append(value)

    def connect(self, address):
        self.connected = address
        if self.clock is not None:
            self.clock.advance(self.duration)
        if isinstance(self.outcome, Exception):
            raise self.outcome

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, tls_socket):
        self.tls_socket = tls_socket
        self.server_hostname = None

    def wrap_socket(self, raw, server_hostname):
        self.server_hostname = server_hostname
        return self.tls_socket


class FakeMonotonicClock:
    def __init__(self, start=0):
        self.current = float(start)

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current += float(seconds)


class TlsContextTests(unittest.TestCase):
    def test_context_is_strict_and_ignores_sslkeylogfile(self):
        from phase2_tls import create_phase2_ssl_context

        with tempfile.TemporaryDirectory() as directory:
            keylog = Path(directory) / "keys.log"
            with patch.dict(os.environ, {"SSLKEYLOGFILE": str(keylog)}):
                context = create_phase2_ssl_context()
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        self.assertTrue(context.check_hostname)
        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        required = ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN
        self.assertEqual(required, context.verify_flags & required)
        self.assertIsNone(context.keylog_filename)
        self.assertGreaterEqual(context.security_level, 2)
        self.assertTrue(context.get_ca_certs())
        self.assertFalse(keylog.exists())

    def test_context_ignores_sslkeylogfile_in_isolated_process(self):
        script = """
import json
from phase2_tls import create_phase2_ssl_context
context = create_phase2_ssl_context()
print(json.dumps({"keylog_filename": context.keylog_filename}))
"""
        with tempfile.TemporaryDirectory() as directory:
            keylog = Path(directory) / "keys.log"
            environment = os.environ.copy()
            environment["SSLKEYLOGFILE"] = str(keylog)
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual({"keylog_filename": None}, json.loads(result.stdout))
            self.assertFalse(keylog.exists())


class PinnedTlsConnectorTests(unittest.TestCase):
    def _plan(self, *addresses):
        return ConnectionPlan(parse_policy_url("https://example.com/"), addresses)

    def test_connects_numeric_ip_and_uses_original_hostname_for_sni(self):
        from phase2_tls import PinnedTlsConnector

        raw = FakeRawSocket()
        tls = FakeTlsSocket()
        context = FakeContext(tls)
        families = []
        connector = PinnedTlsConnector(
            socket_factory=lambda family: families.append(family) or raw,
            context_factory=lambda: context,
        )
        stream = connector.connect(self._plan("8.8.8.8"), 4.0)
        self.assertEqual(("8.8.8.8", 443), raw.connected)
        self.assertEqual("example.com", context.server_hostname)
        self.assertEqual([socket.AF_INET], families)
        self.assertEqual("8.8.8.8", stream.peer_ip())

    def test_fails_over_at_most_four_ips_before_tls(self):
        from phase2_tls import PinnedTlsConnector

        class UnexpectedTlsContext:
            def wrap_socket(self, raw, server_hostname):
                raise AssertionError("TLS must not start")

        raw_sockets = [FakeRawSocket(OSError("down")) for _ in range(4)]
        calls = []
        connector = PinnedTlsConnector(
            socket_factory=lambda family: calls.append(family) or raw_sockets.pop(0),
            context_factory=UnexpectedTlsContext,
        )
        with self.assertRaises(DependencyError):
            connector.connect(
                self._plan("8.8.8.8", "1.1.1.1", "9.9.9.9", "8.8.4.4", "1.0.0.1"),
                5,
            )
        self.assertEqual(4, len(calls))

    def test_connect_failover_uses_remaining_shared_deadline(self):
        from phase2_tls import PinnedTlsConnector

        clock = FakeMonotonicClock(10)
        first = FakeRawSocket(OSError("down"), clock, 2)
        second = FakeRawSocket(clock=clock)
        sockets = [first, second]
        connector = PinnedTlsConnector(
            socket_factory=lambda family: sockets.pop(0),
            context_factory=lambda: FakeContext(FakeTlsSocket(peer="1.1.1.1")),
            clock=clock,
        )
        connector.connect(self._plan("8.8.8.8", "1.1.1.1"), 5)
        self.assertEqual([5], first.timeouts)
        self.assertEqual([3, 3], second.timeouts)

    def test_late_failover_gets_only_one_remaining_second(self):
        from phase2_tls import PinnedTlsConnector

        clock = FakeMonotonicClock()
        first = FakeRawSocket(OSError("down"), clock, 4)
        second = FakeRawSocket(clock=clock)
        sockets = [first, second]
        connector = PinnedTlsConnector(
            socket_factory=lambda family: sockets.pop(0),
            context_factory=lambda: FakeContext(FakeTlsSocket(peer="1.1.1.1")),
            clock=clock,
        )
        connector.connect(self._plan("8.8.8.8", "1.1.1.1"), 5)
        self.assertEqual([1, 1], second.timeouts)

    def test_exhausted_deadline_does_not_try_the_next_ip(self):
        from phase2_tls import PinnedTlsConnector

        clock = FakeMonotonicClock()
        first = FakeRawSocket(OSError("down"), clock, 5)
        calls = []
        connector = PinnedTlsConnector(
            socket_factory=lambda family: calls.append(family) or first,
            context_factory=lambda: FakeContext(FakeTlsSocket()),
            clock=clock,
        )
        with self.assertRaises(DependencyError):
            connector.connect(self._plan("8.8.8.8", "1.1.1.1"), 5)
        self.assertEqual(1, len(calls))

    def test_four_ip_failover_never_restarts_the_timeout(self):
        from phase2_tls import PinnedTlsConnector

        clock = FakeMonotonicClock()
        created = [FakeRawSocket(OSError("down"), clock, 1) for _ in range(4)]
        available = list(created)
        connector = PinnedTlsConnector(
            socket_factory=lambda family: available.pop(0),
            context_factory=lambda: FakeContext(FakeTlsSocket()),
            clock=clock,
        )
        with self.assertRaises(DependencyError):
            connector.connect(
                self._plan("8.8.8.8", "1.1.1.1", "9.9.9.9", "8.8.4.4"),
                5,
            )
        self.assertEqual([], available)
        self.assertEqual([[5], [4], [3], [2]], [raw.timeouts for raw in created])
        self.assertEqual(4, clock.current)

    def test_tls_handshake_uses_remaining_connect_deadline(self):
        from phase2_tls import PinnedTlsConnector

        clock = FakeMonotonicClock()
        raw = FakeRawSocket(clock=clock, duration=2)

        class TimedContext:
            def wrap_socket(self, value, server_hostname):
                self.timeout_at_handshake = value.timeout
                clock.advance(1)
                return FakeTlsSocket()

        context = TimedContext()
        connector = PinnedTlsConnector(
            socket_factory=lambda family: raw,
            context_factory=lambda: context,
            clock=clock,
        )
        connector.connect(self._plan("8.8.8.8"), 5)
        self.assertEqual([5, 3], raw.timeouts)
        self.assertEqual(3, context.timeout_at_handshake)

    def test_tls_stream_is_closed_when_handshake_exhausts_deadline(self):
        from phase2_tls import PinnedTlsConnector

        clock = FakeMonotonicClock()
        raw = FakeRawSocket(clock=clock, duration=2)
        tls = FakeTlsSocket()

        class ExhaustingContext:
            def wrap_socket(self, value, server_hostname):
                clock.advance(3)
                return tls

        connector = PinnedTlsConnector(
            socket_factory=lambda family: raw,
            context_factory=ExhaustingContext,
            clock=clock,
        )
        with self.assertRaises(DependencyError):
            connector.connect(self._plan("8.8.8.8"), 5)
        self.assertTrue(tls.closed)

    def test_certificate_failure_stops_without_failover(self):
        from phase2_tls import PinnedTlsConnector

        calls = []

        class CertificateFailureContext:
            def wrap_socket(self, raw, server_hostname):
                raise ssl.SSLCertVerificationError("bad certificate")

        connector = PinnedTlsConnector(
            socket_factory=lambda family: calls.append(family) or FakeRawSocket(),
            context_factory=CertificateFailureContext,
        )
        with self.assertRaises(UrlSafetyError):
            connector.connect(self._plan("8.8.8.8", "1.1.1.1"), 5)
        self.assertEqual(1, len(calls))

    def test_rejects_unpinned_peer_without_failover(self):
        from phase2_tls import PinnedTlsConnector

        tls = FakeTlsSocket(peer="1.1.1.1")
        connector = PinnedTlsConnector(
            socket_factory=lambda family: FakeRawSocket(),
            context_factory=lambda: FakeContext(tls),
        )
        with self.assertRaises(UrlSafetyError):
            connector.connect(self._plan("8.8.8.8", "9.9.9.9"), 5)
        self.assertTrue(tls.closed)

    def test_stream_detects_zero_progress_and_bounds_receive(self):
        from phase2_tls import PinnedTlsConnector

        tls = FakeTlsSocket(sends=[1, 0])
        connector = PinnedTlsConnector(
            socket_factory=lambda family: FakeRawSocket(),
            context_factory=lambda: FakeContext(tls),
        )
        stream = connector.connect(self._plan("8.8.8.8"), 5)
        with self.assertRaises(DependencyError):
            stream.send_all(b"abc")
        for invalid in (0, -1, 65537, True):
            with self.assertRaises(ValueError):
                stream.receive(invalid)
        stream.close()
        stream.close()
        self.assertTrue(tls.closed)


if __name__ == "__main__":
    unittest.main()
