"""Completely offline fake fetch and response validation foundations."""

import codecs
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from phase2_contracts import (
    ConnectionPlan,
    DependencyError,
    DnsResolution,
    MimeRejectedError,
    PolicyUrl,
    RedirectHop,
    ResponseContractError,
    UrlSafetyError,
    ValidatedFetchResult,
)
from phase2_network_policy import FakeDnsResolver, build_connection_plan, validate_peer_ip
from phase2_url_policy import parse_redirect_url


FIXED_REQUEST_HEADERS = (
    ("Accept", "text/html, application/xhtml+xml"),
    ("Accept-Encoding", "identity"),
    (
        "User-Agent",
        "polymarket-ai-lab-safe-fetch/1.0 "
        "(+https://github.com/takemototosou-lab/polymarket-ai-lab)",
    ),
    ("Connection", "close"),
)

REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
RETRYABLE_HTTP_STATUSES = frozenset((429, 502, 503, 504))
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


@dataclass(frozen=True)
class FetchLimits:
    max_redirects: int = 3
    max_response_bytes: int = 2_097_152
    max_decoded_chars: int = 20_000
    max_header_bytes: int = 32_768
    max_header_count: int = 64
    max_single_header_bytes: int = 4_096

    def __post_init__(self) -> None:
        limits = (
            ("max_redirects", self.max_redirects, 3, True),
            ("max_response_bytes", self.max_response_bytes, 4_194_304, False),
            ("max_decoded_chars", self.max_decoded_chars, 20_000, False),
            ("max_header_bytes", self.max_header_bytes, 65_536, False),
            ("max_header_count", self.max_header_count, 100, False),
            (
                "max_single_header_bytes",
                self.max_single_header_bytes,
                8_192,
                False,
            ),
        )
        for name, value, absolute_max, zero_allowed in limits:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            minimum = 0 if zero_allowed else 1
            if not minimum <= value <= absolute_max:
                raise ValueError(f"{name} is outside its hard limit")


@dataclass(frozen=True)
class FakeHttpResponse:
    status_code: int
    headers: tuple[tuple[str, str], ...]
    body_chunks: tuple[bytes, ...]
    peer_ip: str | None


@dataclass(frozen=True)
class FetchRequestRecord:
    method: str
    request_url: str
    hostname: str
    port: int
    verified_ips: tuple[str, ...]
    headers: tuple[tuple[str, str], ...]
    response_status: int


@dataclass(frozen=True)
class RawFetchTrace:
    requested_url: PolicyUrl
    final_url: PolicyUrl
    redirect_chain: tuple[RedirectHop, ...]
    resolutions: tuple[DnsResolution, ...]
    peer_ips: tuple[str, ...]
    final_response: FakeHttpResponse


class RetryableHttpStatus(DependencyError):
    """A fake HTTP status which may be retried by the separate retry policy."""

    def __init__(self, status_code: int, retry_after: str | None = None) -> None:
        super().__init__(f"retryable fake HTTP status: {status_code}")
        self.status_code = status_code
        self.retry_after = retry_after


def _copy_response(response: FakeHttpResponse) -> FakeHttpResponse:
    try:
        headers = tuple((pair[0], pair[1]) for pair in response.headers)
        chunks = tuple(bytes(chunk) for chunk in response.body_chunks)
    except (TypeError, ValueError, IndexError) as error:
        raise DependencyError("fake response has invalid collections") from error
    return FakeHttpResponse(
        status_code=response.status_code,
        headers=headers,
        body_chunks=chunks,
        peer_ip=response.peer_ip,
    )


class FakeHttpTransport:
    """Return only pre-registered responses and never access a network."""

    def __init__(
        self,
        responses: Mapping[str, Sequence[FakeHttpResponse]],
    ) -> None:
        copied = {
            request_url: tuple(_copy_response(response) for response in values)
            for request_url, values in responses.items()
        }
        self._responses = MappingProxyType(copied)
        self._next_indexes = {request_url: 0 for request_url in copied}
        self._calls: list[FetchRequestRecord] = []

    @property
    def calls(self) -> tuple[FetchRequestRecord, ...]:
        return tuple(self._calls)

    def get(
        self,
        connection_plan: ConnectionPlan,
        *,
        headers: tuple[tuple[str, str], ...],
    ) -> FakeHttpResponse:
        if tuple(headers) != FIXED_REQUEST_HEADERS:
            raise DependencyError("fake transport accepts only fixed GET headers")

        request_url = connection_plan.url.request_url
        values = self._responses.get(request_url)
        index = self._next_indexes.get(request_url, 0)
        if values is None or index >= len(values):
            raise DependencyError("fake HTTP response is absent")
        response = values[index]
        self._next_indexes[request_url] = index + 1

        if not response.peer_ip:
            raise UrlSafetyError("peer IP is unavailable")
        validate_peer_ip(connection_plan, response.peer_ip)
        self._calls.append(
            FetchRequestRecord(
                method="GET",
                request_url=request_url,
                hostname=connection_plan.url.hostname,
                port=connection_plan.url.port,
                verified_ips=connection_plan.verified_ips,
                headers=FIXED_REQUEST_HEADERS,
                response_status=response.status_code,
            )
        )
        return response


def _location_from(response: FakeHttpResponse) -> str:
    locations = tuple(
        value
        for name, value in response.headers
        if isinstance(name, str) and name.lower() == "location"
    )
    if not locations or any(not isinstance(value, str) or not value for value in locations):
        raise UrlSafetyError("redirect Location is missing")
    if len(set(locations)) != 1:
        raise UrlSafetyError("redirect Locations conflict")
    return locations[0]


def _enforce_redirect_body_limit(response: FakeHttpResponse, limit: int) -> None:
    total = 0
    for chunk in response.body_chunks:
        total += len(chunk)
        if total > limit:
            raise ResponseContractError("redirect body exceeds the byte limit")


def follow_redirects(
    initial_plan: ConnectionPlan,
    resolver: FakeDnsResolver,
    transport: FakeHttpTransport,
    limits: FetchLimits,
) -> RawFetchTrace:
    """Follow fake redirects while reapplying URL, DNS, and peer policy."""

    current_plan = initial_plan
    requested_url = initial_plan.url
    redirect_chain: list[RedirectHop] = []
    resolutions = [
        DnsResolution(
            initial_plan.url.hostname,
            initial_plan.verified_ips,
            (),
        )
    ]
    peer_ips: list[str] = []
    visited = {initial_plan.url.request_url}

    while True:
        response = transport.get(current_plan, headers=FIXED_REQUEST_HEADERS)
        peer_ip = validate_peer_ip(current_plan, response.peer_ip)
        peer_ips.append(peer_ip)
        if response.status_code not in REDIRECT_STATUSES:
            return RawFetchTrace(
                requested_url=requested_url,
                final_url=current_plan.url,
                redirect_chain=tuple(redirect_chain),
                resolutions=tuple(resolutions),
                peer_ips=tuple(peer_ips),
                final_response=response,
            )

        _enforce_redirect_body_limit(response, limits.max_response_bytes)
        if len(redirect_chain) >= limits.max_redirects:
            raise UrlSafetyError("redirect limit exceeded")
        location = _location_from(response)
        next_url = parse_redirect_url(current_plan.url, location)
        if next_url.request_url in visited:
            raise UrlSafetyError("redirect loop detected")
        resolution = resolver.resolve(next_url.hostname)
        next_plan = build_connection_plan(next_url, resolution)
        redirect_chain.append(
            RedirectHop(
                request_url=current_plan.url.request_url,
                status_code=response.status_code,
                location=location,
                peer_ip=peer_ip,
            )
        )
        resolutions.append(
            DnsResolution(
                hostname=resolution.hostname,
                addresses=next_plan.verified_ips,
                cname_chain=resolution.cname_chain,
            )
        )
        visited.add(next_url.request_url)
        current_plan = next_plan


def _validate_header_pairs(
    headers: tuple[tuple[str, str], ...],
    limits: FetchLimits,
) -> tuple[tuple[str, str], ...]:
    if len(headers) > limits.max_header_count:
        raise ResponseContractError("response header count exceeds the limit")

    validated = []
    total_bytes = 0
    for pair in headers:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ResponseContractError("response header pair is invalid")
        name, value = pair
        if not isinstance(name, str) or not isinstance(value, str):
            raise ResponseContractError("response header must contain strings")
        if not _HEADER_NAME.fullmatch(name):
            raise ResponseContractError("response header name is invalid")
        if any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in value):
            raise ResponseContractError("response header value contains a control")
        try:
            pair_bytes = len(name.encode("ascii")) + len(value.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ResponseContractError("response header name is not ASCII") from error
        if pair_bytes > limits.max_single_header_bytes:
            raise ResponseContractError("single response header exceeds the limit")
        total_bytes += pair_bytes
        if total_bytes > limits.max_header_bytes:
            raise ResponseContractError("response headers exceed the byte limit")
        validated.append((name.lower(), value))
    return tuple(validated)


def _header_values(headers: tuple[tuple[str, str], ...], name: str) -> tuple[str, ...]:
    return tuple(value for header_name, value in headers if header_name == name)


def _validate_framing(
    headers: tuple[tuple[str, str], ...],
    limits: FetchLimits,
) -> int | None:
    content_lengths = _header_values(headers, "content-length")
    transfer_encodings = _header_values(headers, "transfer-encoding")
    if len(content_lengths) > 1:
        raise ResponseContractError("multiple Content-Length headers are forbidden")
    if len(transfer_encodings) > 1:
        raise ResponseContractError("multiple Transfer-Encoding headers are forbidden")
    if content_lengths and transfer_encodings:
        raise ResponseContractError("Content-Length and Transfer-Encoding conflict")
    if transfer_encodings and transfer_encodings[0].strip().lower() != "chunked":
        raise ResponseContractError("unsupported Transfer-Encoding")
    if not content_lengths:
        return None
    raw_length = content_lengths[0].strip()
    if not raw_length or not raw_length.isascii() or not raw_length.isdecimal():
        raise ResponseContractError("Content-Length is invalid")
    length = int(raw_length)
    if length > limits.max_response_bytes:
        raise ResponseContractError("Content-Length exceeds the byte limit")
    return length


def _validate_content_encoding(headers: tuple[tuple[str, str], ...]) -> None:
    values = _header_values(headers, "content-encoding")
    if not values:
        return
    if len(values) != 1 or values[0].strip().lower() != "identity":
        raise MimeRejectedError("compressed or unknown Content-Encoding is forbidden")


def _parse_content_type(
    headers: tuple[tuple[str, str], ...],
) -> tuple[str, str, bool]:
    values = _header_values(headers, "content-type")
    if not values:
        raise MimeRejectedError("Content-Type is required")
    if len(values) != 1:
        raise ResponseContractError("multiple Content-Type headers are forbidden")

    sections = tuple(section.strip() for section in values[0].split(";"))
    mime = sections[0].lower()
    if mime not in ("text/html", "application/xhtml+xml"):
        raise MimeRejectedError("response MIME is not HTML or XHTML")

    charsets = []
    for parameter in sections[1:]:
        if not parameter or "=" not in parameter:
            raise ResponseContractError("Content-Type parameter is invalid")
        name, value = parameter.split("=", 1)
        if name.strip().lower() == "charset":
            charset = value.strip().strip('"')
            if not charset:
                raise ResponseContractError("charset is empty")
            charsets.append(charset)
    if len(charsets) > 1:
        raise ResponseContractError("multiple charset parameters are forbidden")
    declared = bool(charsets)
    raw_charset = charsets[0] if charsets else "utf-8"
    try:
        charset = codecs.lookup(raw_charset).name
    except LookupError as error:
        raise ResponseContractError("charset is unknown") from error
    return mime, charset, declared


def _read_body(
    response: FakeHttpResponse,
    limits: FetchLimits,
    declared_length: int | None,
) -> bytes:
    body = bytearray()
    for chunk in response.body_chunks:
        if not isinstance(chunk, bytes):
            raise ResponseContractError("response chunk is not bytes")
        body.extend(chunk)
        if len(body) > limits.max_response_bytes:
            raise ResponseContractError("response body exceeds the byte limit")
    if declared_length is not None and declared_length != len(body):
        raise ResponseContractError("Content-Length does not match the body")
    if not body:
        raise ResponseContractError("response body is empty")
    if b"\x00" in body:
        raise ResponseContractError("response body contains NUL")
    return bytes(body)


def _decode_body(
    body: bytes,
    charset: str,
    charset_was_declared: bool,
    limits: FetchLimits,
) -> tuple[str, str]:
    if body.startswith(codecs.BOM_UTF8):
        if charset_was_declared and charset != "utf-8":
            raise ResponseContractError("UTF-8 BOM conflicts with charset")
        body = body[len(codecs.BOM_UTF8) :]
        charset = "utf-8"
    try:
        decoded = body.decode(charset, errors="strict")
    except (LookupError, UnicodeError) as error:
        raise ResponseContractError("response body cannot be strictly decoded") from error
    if not decoded:
        raise ResponseContractError("decoded response body is empty")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in decoded):
        raise ResponseContractError("decoded response contains a surrogate")
    if any(
        (ord(character) < 0x20 and character not in "\r\n\t")
        or 0x7F <= ord(character) <= 0x9F
        for character in decoded
    ):
        raise ResponseContractError("decoded response contains a forbidden control")
    if len(decoded) > limits.max_decoded_chars:
        raise ResponseContractError("decoded response exceeds the character limit")
    return decoded, charset


def _retry_after_value(response: FakeHttpResponse) -> str | None:
    values = tuple(
        value
        for name, value in response.headers
        if isinstance(name, str) and name.lower() == "retry-after"
    )
    return values[0] if len(values) == 1 and isinstance(values[0], str) else None


def validate_response(
    trace: RawFetchTrace,
    limits: FetchLimits,
    retrieved_at: str,
) -> ValidatedFetchResult:
    """Validate one final fake response without returning partial results."""

    response = trace.final_response
    if response.status_code in RETRYABLE_HTTP_STATUSES:
        raise RetryableHttpStatus(response.status_code, _retry_after_value(response))
    if response.status_code != 200:
        raise ResponseContractError("only status 200 is a body candidate")

    headers = _validate_header_pairs(response.headers, limits)
    declared_length = _validate_framing(headers, limits)
    _validate_content_encoding(headers)
    content_type, charset, declared_charset = _parse_content_type(headers)
    body = _read_body(response, limits, declared_length)
    decoded, charset = _decode_body(body, charset, declared_charset, limits)

    return ValidatedFetchResult(
        requested_url=trace.requested_url.request_url,
        final_url=trace.final_url.request_url,
        redirect_chain=trace.redirect_chain,
        resolved_ips_by_hop=tuple(item.addresses for item in trace.resolutions),
        peer_ip_by_hop=trace.peer_ips,
        status_code=response.status_code,
        content_type=content_type,
        charset=charset,
        response_bytes=len(body),
        decoded_chars=len(decoded),
        retrieved_at=retrieved_at,
        decoded_html=decoded,
    )


def fetch_validated_html(
    initial_plan: ConnectionPlan,
    *,
    resolver: FakeDnsResolver,
    transport: FakeHttpTransport,
    limits: FetchLimits,
    now,
) -> ValidatedFetchResult:
    """Run the complete fake redirect and response-validation path."""

    trace = follow_redirects(initial_plan, resolver, transport, limits)
    return validate_response(trace, limits, now())
