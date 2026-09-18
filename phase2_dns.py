"""Phase 2B DNS resolution using an injected or OS-configured Do53 backend."""

import ipaddress
import math
import time
from collections.abc import Callable

import dns.exception
import dns.nameserver
import dns.rdatatype
import dns.resolver

from phase2_contracts import (
    DependencyError,
    DnsQueryBackend,
    DnsQueryResult,
    DnsResolution,
    UrlSafetyError,
)
from phase2_network_policy import build_connection_plan
from phase2_url_policy import parse_policy_url


def _validated_hostname(value: str) -> str:
    if not isinstance(value, str) or any(
        character in value for character in "/?#@[]:"
    ):
        raise UrlSafetyError("DNS hostname is invalid")
    try:
        return parse_policy_url(f"https://{value}/").hostname
    except (TypeError, ValueError, UrlSafetyError) as error:
        raise UrlSafetyError("DNS hostname is invalid") from error


def _validated_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("DNS timeout must be a finite positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("DNS timeout must be a finite positive number")
    return result


def _clock_value(clock: Callable[[], float]) -> float:
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DependencyError("DNS monotonic clock returned an invalid value")
    result = float(value)
    if not math.isfinite(result):
        raise DependencyError("DNS monotonic clock returned an invalid value")
    return result


def _remaining_seconds(deadline: float, clock: Callable[[], float]) -> float:
    remaining = deadline - _clock_value(clock)
    if remaining <= 0:
        raise DependencyError("DNS attempt deadline exceeded")
    return remaining


def resolve_phase2_dns(
    hostname: str,
    backend: DnsQueryBackend,
    timeout_seconds: float,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> DnsResolution:
    """Resolve CNAME then A and AAAA under one fail-closed deadline."""

    hostname = _validated_hostname(hostname)
    timeout = _validated_timeout(timeout_seconds)
    deadline = _clock_value(clock) + timeout
    chain = []
    visited = {hostname}
    canonical = hostname
    while True:
        cname_answer = backend.query(
            canonical, "CNAME", _remaining_seconds(deadline, clock)
        )
        _remaining_seconds(deadline, clock)
        if cname_answer.hostname != canonical or cname_answer.addresses:
            raise UrlSafetyError("DNS CNAME answer does not match the query")
        answer_chain = cname_answer.cname_chain
        answer_canonical = _validated_hostname(cname_answer.canonical_hostname)
        if not answer_chain:
            if answer_canonical != canonical:
                raise UrlSafetyError("DNS canonical host lacks a CNAME chain")
            break
        for target in answer_chain:
            normalized = _validated_hostname(target)
            if normalized in visited:
                raise UrlSafetyError("DNS CNAME chain contains a loop")
            chain.append(normalized)
            if len(chain) > 8:
                raise UrlSafetyError("DNS CNAME chain exceeds the hop limit")
            visited.add(normalized)
        canonical = chain[-1]
        if answer_canonical != canonical:
            raise UrlSafetyError("DNS CNAME chain does not reach canonical host")

    answers = []
    for rdtype in ("A", "AAAA"):
        remaining = _remaining_seconds(deadline, clock)
        answers.append(backend.query(canonical, rdtype, remaining))
        _remaining_seconds(deadline, clock)
    answers = tuple(answers)
    if any(answer.hostname != canonical for answer in answers):
        raise UrlSafetyError("DNS answer hostname does not match the query")
    if any(
        answer.canonical_hostname != canonical or answer.cname_chain
        for answer in answers
    ):
        raise UrlSafetyError("DNS families have conflicting CNAME results")

    resolution = DnsResolution(
        hostname=hostname,
        addresses=answers[0].addresses + answers[1].addresses,
        cname_chain=tuple(chain),
    )
    plan = build_connection_plan(parse_policy_url(f"https://{hostname}/"), resolution)
    return DnsResolution(hostname, plan.verified_ips, tuple(chain))


class DnspythonQueryBackend:
    """Use dnspython public APIs and only OS-configured Do53 nameservers."""

    def __init__(self, resolver=None) -> None:
        self._resolver = resolver or dns.resolver.Resolver(configure=True)
        nameservers = tuple(self._resolver.nameservers)
        if not nameservers or not all(self._is_do53(value) for value in nameservers):
            raise UrlSafetyError("DNS resolver is not exclusively Do53")

    @staticmethod
    def _is_do53(value) -> bool:
        if isinstance(value, dns.nameserver.Do53Nameserver):
            return True
        if isinstance(value, str):
            try:
                ipaddress.ip_address(value)
                return True
            except ValueError:
                return False
        return False

    def query(
        self, hostname: str, rdtype: str, timeout_seconds: float
    ) -> DnsQueryResult:
        if rdtype not in {"CNAME", "A", "AAAA"}:
            raise ValueError("DNS record type must be CNAME, A, or AAAA")
        hostname = _validated_hostname(hostname)
        timeout = _validated_timeout(timeout_seconds)
        try:
            answer = self._resolver.resolve(
                hostname,
                rdtype,
                search=False,
                lifetime=timeout,
                raise_on_no_answer=False,
            )
        except dns.resolver.NoAnswer:
            return DnsQueryResult(hostname, hostname, (), ())
        except (dns.exception.Timeout, dns.resolver.NoNameservers) as error:
            raise DependencyError("temporary DNS failure") from error
        except (dns.resolver.NXDOMAIN, dns.resolver.YXDOMAIN) as error:
            raise UrlSafetyError("DNS name does not exist") from error
        except dns.exception.DNSException as error:
            raise UrlSafetyError("DNS response is invalid") from error

        answer_canonical = _validated_hostname(
            str(answer.canonical_name).rstrip(".")
        )
        response = getattr(answer, "response", None)
        chain = self._validated_cname_chain(
            hostname, getattr(response, "answer", ())
        )
        chain_canonical = chain[-1] if chain else hostname
        canonical = chain_canonical if rdtype == "CNAME" else answer_canonical
        if rdtype == "CNAME" and not chain and answer_canonical != hostname:
            raise UrlSafetyError("DNS canonical host lacks a CNAME record")
        if rdtype != "CNAME" and chain_canonical != answer_canonical:
            raise UrlSafetyError("DNS CNAME chain does not reach canonical host")
        addresses = (
            ()
            if rdtype == "CNAME"
            else tuple(
                str(getattr(rdata, "address", rdata)).rstrip(".")
                for rdata in answer
            )
        )
        return DnsQueryResult(hostname, canonical, addresses, chain)

    @staticmethod
    def _validated_cname_chain(hostname, rrsets) -> tuple[str, ...]:
        targets_by_owner = {}
        for rrset in rrsets:
            try:
                rdtype = rrset.rdtype
            except AttributeError as error:
                raise UrlSafetyError("DNS answer record is malformed") from error
            if rdtype != dns.rdatatype.CNAME:
                continue
            try:
                owner = _validated_hostname(str(rrset.name).rstrip("."))
                targets = {
                    _validated_hostname(str(rdata.target).rstrip("."))
                    for rdata in rrset
                }
            except (AttributeError, TypeError) as error:
                raise UrlSafetyError("DNS CNAME record is malformed") from error
            if len(targets) != 1:
                raise UrlSafetyError("DNS CNAME owner has conflicting targets")
            target = next(iter(targets))
            previous = targets_by_owner.get(owner)
            if previous is not None and previous != target:
                raise UrlSafetyError("DNS CNAME owner has conflicting targets")
            targets_by_owner[owner] = target

        if len(targets_by_owner) > 8:
            raise UrlSafetyError("DNS CNAME chain exceeds the hop limit")

        chain = []
        visited_names = {hostname}
        visited_owners = set()
        current = hostname
        while current in targets_by_owner:
            visited_owners.add(current)
            target = targets_by_owner[current]
            if target in visited_names:
                raise UrlSafetyError("DNS CNAME chain contains a loop")
            chain.append(target)
            if len(chain) > 8:
                raise UrlSafetyError("DNS CNAME chain exceeds the hop limit")
            visited_names.add(target)
            current = target

        if visited_owners != set(targets_by_owner):
            raise UrlSafetyError("DNS CNAME chain is disconnected")
        return tuple(chain)
