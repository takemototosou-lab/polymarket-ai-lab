"""Immutable internal contracts for the offline Phase 2A foundation."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


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
class ConnectionPlan:
    url: PolicyUrl
    verified_ips: tuple[str, ...]


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
