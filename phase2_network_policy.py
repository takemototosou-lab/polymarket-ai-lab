"""Offline IP, fake DNS, connection-plan, and peer-IP policy."""

import ipaddress
from types import MappingProxyType
from typing import Mapping

from phase2_contracts import (
    ConnectionPlan,
    DependencyError,
    DnsResolution,
    PolicyUrl,
    UrlSafetyError,
)


MAX_DNS_ADDRESSES = 16
MAX_CNAME_HOPS = 8

_DENIED_IPV4_NETWORKS = tuple(
    ipaddress.IPv4Network(value)
    for value in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)

_DENIED_IPV6_NETWORKS = tuple(
    ipaddress.IPv6Network(value)
    for value in (
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
        "2001:db8::/32",
    )
)


def _parse_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return ipaddress.ip_address(value)
    except (TypeError, ValueError) as error:
        raise UrlSafetyError("IP address syntax is invalid") from error


def _is_denied(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    denied_networks = (
        _DENIED_IPV4_NETWORKS
        if isinstance(address, ipaddress.IPv4Address)
        else _DENIED_IPV6_NETWORKS
    )
    if any(address in network for network in denied_networks):
        return True
    if (
        not address.is_global
        or address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        return True
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_denied(address.ipv4_mapped)
    return False


def is_global_unicast(value: str) -> bool:
    """Return whether one syntactically valid IP is allowed by the policy."""

    try:
        address = _parse_address(value)
    except UrlSafetyError:
        return False
    return not _is_denied(address)


def validate_global_ip(value: str) -> str:
    """Validate and return the standard-library canonical IP representation."""

    address = _parse_address(value)
    if _is_denied(address):
        raise UrlSafetyError("IP address is not global unicast")
    return str(address)


class FakeDnsResolver:
    """Resolve only immutable, pre-registered fake DNS records."""

    def __init__(self, records: Mapping[str, DnsResolution]) -> None:
        copied = {
            hostname: DnsResolution(
                hostname=resolution.hostname,
                addresses=tuple(resolution.addresses),
                cname_chain=tuple(resolution.cname_chain),
            )
            for hostname, resolution in records.items()
        }
        self._records = MappingProxyType(copied)

    def resolve(self, hostname: str) -> DnsResolution:
        try:
            return self._records[hostname]
        except KeyError as error:
            raise DependencyError("fake DNS result is absent") from error


def build_connection_plan(
    url: PolicyUrl,
    resolution: DnsResolution,
) -> ConnectionPlan:
    """Validate every fake DNS result and pin the deterministic address set."""

    if resolution.hostname != url.hostname:
        raise UrlSafetyError("DNS result hostname does not match the URL")
    if len(resolution.cname_chain) > MAX_CNAME_HOPS:
        raise UrlSafetyError("CNAME chain exceeds the hop limit")
    if not resolution.addresses:
        raise UrlSafetyError("DNS result is empty")

    verified = []
    seen = set()
    for value in resolution.addresses:
        normalized = validate_global_ip(value)
        if normalized not in seen:
            seen.add(normalized)
            verified.append(normalized)
    if len(verified) > MAX_DNS_ADDRESSES:
        raise UrlSafetyError("DNS result exceeds the address limit")

    return ConnectionPlan(url=url, verified_ips=tuple(verified))


def validate_peer_ip(plan: ConnectionPlan, peer_ip: str) -> str:
    """Revalidate a transport peer and require exact membership in the pinned set."""

    normalized = validate_global_ip(peer_ip)
    if normalized not in plan.verified_ips:
        raise UrlSafetyError("peer IP is outside the verified DNS set")
    return normalized
