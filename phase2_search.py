"""Offline-only search fixtures. URLs here are candidates, not fetch permissions."""

from dataclasses import replace
from types import MappingProxyType

from phase2_contracts import (
    DependencyError, QueryKind, ResponseContractError, SearchRequest, SourceCandidate,
)


def _text(value, minimum, maximum, *, byte_length=False, whitespace=False):
    if not isinstance(value, str) or any(
        ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF
        for c in value
    ):
        raise ResponseContractError("Invalid candidate text")
    length = len(value.encode("utf-8")) if byte_length else len(value)
    if not minimum <= length <= maximum or (whitespace and any(c.isspace() for c in value)):
        raise ResponseContractError("Invalid candidate text length or whitespace")
    return value


def _integer(value, minimum, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ResponseContractError("Invalid search integer")


def validate_search_request(request):
    if not isinstance(request, SearchRequest) or not isinstance(request.query_kind, QueryKind):
        raise ResponseContractError("Invalid search request")
    _text(request.query, 1, 500)
    if request.query != request.query.strip():
        raise ResponseContractError("Query must not have surrounding whitespace")
    _integer(request.max_results, 1, 10)
    _integer(request.request_ordinal, 0)
    return request


def validate_candidate(candidate):
    if not isinstance(candidate, SourceCandidate) or not isinstance(candidate.query_kind, QueryKind):
        raise ResponseContractError("Invalid source candidate")
    _text(candidate.source_id, 1, 64, whitespace=True)
    _integer(candidate.rank, 1, 10)
    _text(candidate.url, 1, 2048, byte_length=True)
    _text(candidate.title, 0, 1000000)
    title = _text(candidate.title.strip(), 1, 500)
    _text(candidate.snippet, 0, 1000)
    if candidate.publisher_hint is not None:
        _text(candidate.publisher_hint, 1, 200)
    if candidate.published_at_hint is not None:
        _text(candidate.published_at_hint, 1, 100)
    return replace(candidate, title=title)


def make_candidate(request, candidate, candidate_index):
    validate_search_request(request)
    candidate = validate_candidate(candidate)
    _integer(candidate_index, 1, 10)
    if candidate.query_kind != request.query_kind:
        raise ResponseContractError("Candidate query kind mismatch")
    return validate_candidate(replace(
        candidate,
        source_id=f"Q{request.request_ordinal}-R{candidate.rank}-C{candidate_index}",
    ))


class FakeSearchProvider:
    """Copied fixtures only; rank ties preserve fixture order; indices start at one."""

    def __init__(self, records):
        copied = {}
        for request, candidates in records.items():
            validate_search_request(request)
            candidates = tuple(validate_candidate(c) for c in candidates)
            if len({c.source_id for c in candidates}) != len(candidates):
                raise ResponseContractError("Duplicate candidate ID")
            if any(c.query_kind != request.query_kind for c in candidates):
                raise ResponseContractError("Candidate query kind mismatch")
            ordered = sorted(candidates, key=lambda c: c.rank)[:request.max_results]
            copied[request] = tuple(make_candidate(request, c, i) for i, c in enumerate(ordered, 1))
        self._records = MappingProxyType(copied)

    def search(self, request):
        validate_search_request(request)
        if request not in self._records:
            raise DependencyError("Unregistered fake search request")
        return list(self._records[request])
