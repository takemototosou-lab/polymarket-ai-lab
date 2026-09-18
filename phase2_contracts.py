"""Immutable internal contracts for the offline Phase 2A foundation."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class QueryKind(str, Enum):
    OFFICIAL = "official"
    STATUS = "status"
    SUPPORT = "support"
    COUNTER = "counter"


class Phase2Error(Exception):
    """Base class for errors owned by the Phase 2 contract."""

    exit_code: int


class UrlSafetyError(Phase2Error):
    exit_code = 4


class LockConflictError(Phase2Error):
    exit_code = 5


class DependencyError(Phase2Error):
    exit_code = 6


class ResponseContractError(Phase2Error):
    exit_code = 7


class BudgetLimitError(Phase2Error):
    exit_code = 8


class ProviderAuthError(Phase2Error):
    exit_code = 9


class MimeRejectedError(Phase2Error):
    exit_code = 10


def phase2_exit_code(error: Phase2Error) -> int:
    if not isinstance(error, Phase2Error):
        raise TypeError("error must be a Phase2Error")
    return error.exit_code


@dataclass(frozen=True)
class PolicyUrl:
    original: str
    request_url: str
    hostname: str
    port: int
    path_and_query: str


@dataclass(frozen=True)
class DnsResolution:
    hostname: str
    addresses: tuple[str, ...]
    cname_chain: tuple[str, ...]


@dataclass(frozen=True)
class DnsQueryResult:
    hostname: str
    canonical_hostname: str
    addresses: tuple[str, ...]
    cname_chain: tuple[str, ...]


@runtime_checkable
class DnsQueryBackend(Protocol):
    def query(
        self, hostname: str, rdtype: str, timeout_seconds: float
    ) -> DnsQueryResult:
        """Return one immutable A or AAAA query result."""


@dataclass(frozen=True)
class ConnectionPlan:
    url: PolicyUrl
    verified_ips: tuple[str, ...]


@runtime_checkable
class TlsByteStream(Protocol):
    def peer_ip(self) -> str:
        """Return the connected numeric peer address."""

    def send_all(self, data: bytes) -> None:
        """Send every byte or fail."""

    def receive(self, max_bytes: int) -> bytes:
        """Receive at most max_bytes."""

    def close(self) -> None:
        """Close the owned stream."""


@runtime_checkable
class TlsConnector(Protocol):
    def connect(
        self, plan: ConnectionPlan, timeout_seconds: float
    ) -> TlsByteStream:
        """Connect to a pinned numeric IP using the original hostname."""


@dataclass(frozen=True)
class RedirectHop:
    request_url: str
    status_code: int
    location: str
    peer_ip: str


@dataclass(frozen=True)
class SourceCandidate:
    source_id: str
    query_kind: QueryKind
    rank: int
    url: str
    title: str
    snippet: str
    publisher_hint: str | None
    published_at_hint: str | None


@dataclass(frozen=True)
class SearchRequest:
    query_kind: QueryKind
    query: str
    max_results: int
    request_ordinal: int


class SearchProvider(Protocol):
    def search(self, request: SearchRequest) -> list[SourceCandidate]:
        """Return unvalidated source candidates for a fixed request."""


class FileStore(Protocol):
    def create_exclusive(self, path: Path, payload: bytes) -> None:
        """Create a new file without replacing an existing one."""

    def read_bytes(self, path: Path) -> bytes:
        """Read a complete file as bytes."""

    def remove(self, path: Path) -> None:
        """Remove a file owned by the current operation."""


@dataclass(frozen=True)
class ValidatedFetchResult:
    requested_url: str
    final_url: str
    redirect_chain: tuple[RedirectHop, ...]
    resolved_ips_by_hop: tuple[tuple[str, ...], ...]
    peer_ip_by_hop: tuple[str, ...]
    status_code: int
    content_type: str
    charset: str
    response_bytes: int
    decoded_chars: int
    retrieved_at: str
    decoded_html: str
