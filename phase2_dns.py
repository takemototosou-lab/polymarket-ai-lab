"""Phase 2B DNS resolution using an injected or OS-configured Do53 backend."""

import ipaddress
import math

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


def resolve_phase2_dns(
    hostname: str,
    backend: DnsQueryBackend,
    timeout_seconds: float,
) -> DnsResolution:
    """Resolve A then AAAA and reject any unsafe partial DNS result."""

    hostname = _validated_hostname(hostname)
    timeout = _validated_timeout(timeout_seconds)
    answers = tuple(
        backend.query(hostname, rdtype, timeout) for rdtype in ("A", "AAAA")
    )
    if any(answer.hostname != hostname for answer in answers):
        raise UrlSafetyError("DNS answer hostname does not match the query")
    if (
        answers[0].canonical_hostname != answers[1].canonical_hostname
        or answers[0].cname_chain != answers[1].cname_chain
    ):
        raise UrlSafetyError("DNS families have conflicting CNAME results")

    chain = answers[0].cname_chain
    if len(chain) > 8 or len(set(chain)) != len(chain):
        raise UrlSafetyError("DNS CNAME chain is invalid")
    for target in chain:
        _validated_hostname(target)
    canonical = _validated_hostname(answers[0].canonical_hostname)
    if chain and chain[-1] != canonical:
        raise UrlSafetyError("DNS CNAME chain does not end at the canonical host")

    resolution = DnsResolution(
        hostname=hostname,
        addresses=answers[0].addresses + answers[1].addresses,
        cname_chain=chain,
    )
    plan = build_connection_plan(parse_policy_url(f"https://{hostname}/"), resolution)
    return DnsResolution(hostname, plan.verified_ips, chain)


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
        if rdtype not in {"A", "AAAA"}:
            raise ValueError("DNS record type must be A or AAAA")
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

        canonical = _validated_hostname(str(answer.canonical_name).rstrip("."))
        chain = []
        response = getattr(answer, "response", None)
        for rrset in getattr(response, "answer", ()):
            if rrset.rdtype == dns.rdatatype.CNAME:
                for rdata in rrset:
                    chain.append(_validated_hostname(str(rdata.target).rstrip(".")))
        addresses = tuple(
            str(getattr(rdata, "address", rdata)).rstrip(".") for rdata in answer
        )
        return DnsQueryResult(hostname, canonical, addresses, tuple(chain))
