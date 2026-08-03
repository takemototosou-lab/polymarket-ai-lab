"""Completely offline fake fetch and response validation foundations."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from phase2_contracts import (
    ConnectionPlan,
    DependencyError,
    DnsResolution,
    PolicyUrl,
    RedirectHop,
    ResponseContractError,
    UrlSafetyError,
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
        resolutions.append(resolution)
        visited.add(next_url.request_url)
        current_plan = next_plan
