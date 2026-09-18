"""Phase 2B hardened TLS context and DNS-pinned connector."""

import ipaddress
import math
import socket
import ssl
from collections.abc import Callable

from phase2_contracts import ConnectionPlan, DependencyError, UrlSafetyError
from phase2_network_policy import validate_global_ip, validate_peer_ip


MAX_CONNECT_IPS = 4
MAX_RECEIVE_BYTES = 65_536


def _validated_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("TLS timeout must be a finite positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0 or result > 15:
        raise ValueError("TLS timeout must be within 15 seconds")
    return result


def _validate_phase2_ssl_context(context: ssl.SSLContext) -> None:
    required_flags = ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN
    if (
        context.protocol != ssl.PROTOCOL_TLS_CLIENT
        or context.verify_mode != ssl.CERT_REQUIRED
        or context.check_hostname is not True
        or context.minimum_version < ssl.TLSVersion.TLSv1_2
        or context.verify_flags & required_flags != required_flags
        or context.keylog_filename is not None
        or context.security_level < 2
        or not context.get_ca_certs()
    ):
        raise UrlSafetyError("TLS context does not meet the Phase 2B policy")


def create_phase2_ssl_context() -> ssl.SSLContext:
    """Create a strict client context without consulting SSLKEYLOGFILE."""

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_flags |= (
        ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN
    )
    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    _validate_phase2_ssl_context(context)
    return context


class _OwnedTlsByteStream:
    def __init__(self, stream, peer: str) -> None:
        self._stream = stream
        self._peer = peer
        self._closed = False

    def peer_ip(self) -> str:
        return self._peer

    def send_all(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("TLS payload must be bytes")
        view = memoryview(data)
        while view:
            try:
                written = self._stream.send(view)
            except (OSError, ssl.SSLError) as error:
                raise DependencyError("TLS send failed") from error
            if written <= 0:
                raise DependencyError("TLS send made no progress")
            view = view[written:]

    def receive(self, max_bytes: int) -> bytes:
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 1 <= max_bytes <= MAX_RECEIVE_BYTES
        ):
            raise ValueError("TLS receive size is invalid")
        try:
            return self._stream.recv(max_bytes)
        except (OSError, ssl.SSLError) as error:
            raise DependencyError("TLS receive failed") from error

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._stream.close()


class PinnedTlsConnector:
    """Connect only to pinned numeric addresses and verify the actual peer."""

    def __init__(
        self,
        *,
        socket_factory: Callable[[int], object] | None = None,
        context_factory: Callable[[], object] = create_phase2_ssl_context,
    ) -> None:
        self._socket_factory = socket_factory or (
            lambda family: socket.socket(family, socket.SOCK_STREAM)
        )
        self._context_factory = context_factory

    def connect(self, plan: ConnectionPlan, timeout_seconds: float):
        timeout = _validated_timeout(timeout_seconds)
        context = self._context_factory()
        last_error = None
        for value in plan.verified_ips[:MAX_CONNECT_IPS]:
            normalized = validate_global_ip(value)
            address = ipaddress.ip_address(normalized)
            family = socket.AF_INET if address.version == 4 else socket.AF_INET6
            endpoint = (
                (normalized, plan.url.port)
                if family == socket.AF_INET
                else (normalized, plan.url.port, 0, 0)
            )
            raw = self._socket_factory(family)
            try:
                raw.settimeout(timeout)
                raw.connect(endpoint)
                tls_stream = context.wrap_socket(
                    raw, server_hostname=plan.url.hostname
                )
            except ssl.SSLCertVerificationError as error:
                raw.close()
                raise UrlSafetyError("TLS certificate validation failed") from error
            except ssl.SSLError as error:
                raw.close()
                raise UrlSafetyError("TLS security negotiation failed") from error
            except (socket.timeout, TimeoutError, ConnectionError, OSError) as error:
                raw.close()
                last_error = error
                continue

            try:
                peer = validate_peer_ip(plan, tls_stream.getpeername()[0])
            except (AttributeError, IndexError, TypeError, UrlSafetyError) as error:
                tls_stream.close()
                raise UrlSafetyError("TLS peer validation failed") from error
            return _OwnedTlsByteStream(tls_stream, peer)
        raise DependencyError("all pinned TLS connection attempts failed") from last_error
