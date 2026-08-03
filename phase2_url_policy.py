"""Pure URL parsing and safety policy for the offline Phase 2A foundation."""

import ipaddress
import re
import unicodedata
import urllib.parse

import idna

from phase2_contracts import PolicyUrl, UrlSafetyError


MAX_URL_BYTES = 2_048
MAX_HOSTNAME_BYTES = 253
MAX_LABEL_BYTES = 63
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def _encoded_length(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except (UnicodeEncodeError, AttributeError) as error:
        raise UrlSafetyError("URL must be a valid Unicode string") from error


def _reject_unsafe_text(value: str) -> None:
    if not isinstance(value, str):
        raise UrlSafetyError("URL must be a string")
    if not value or _encoded_length(value) > MAX_URL_BYTES:
        raise UrlSafetyError("URL exceeds the byte limit")
    if "\\" in value:
        raise UrlSafetyError("backslash is not allowed in a URL")
    if any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in value):
        raise UrlSafetyError("control characters are not allowed in a URL")
    if _INVALID_PERCENT_ESCAPE.search(value):
        raise UrlSafetyError("URL contains an invalid percent escape")


def _canonical_hostname(hostname: str, netloc: str) -> tuple[str, str]:
    if not hostname:
        raise UrlSafetyError("URL hostname is required")
    if hostname.endswith(".") or ".." in hostname:
        raise UrlSafetyError("hostname contains an empty label")

    if ":" in hostname:
        if not netloc.startswith("[") or "%" in hostname:
            raise UrlSafetyError("IPv6 literals must use brackets without a zone")
        try:
            canonical = str(ipaddress.IPv6Address(hostname))
        except ipaddress.AddressValueError as error:
            raise UrlSafetyError("invalid IPv6 literal") from error
        return canonical, f"[{canonical}]"

    try:
        address = ipaddress.IPv4Address(hostname)
    except ipaddress.AddressValueError:
        address = None
    if address is not None:
        canonical = str(address)
        return canonical, canonical
    if all(character in "0123456789." for character in hostname):
        raise UrlSafetyError("ambiguous numeric hostname")

    if hostname.startswith(".") or any(not label for label in hostname.split(".")):
        raise UrlSafetyError("hostname contains an empty label")
    try:
        remapped = idna.uts46_remap(
            hostname,
            std3_rules=True,
            transitional=False,
        )
        if remapped.casefold() != unicodedata.normalize("NFC", hostname).casefold():
            raise UrlSafetyError("compatibility hostname is ambiguous")
        canonical = idna.encode(
            hostname,
            uts46=True,
            transitional=False,
            std3_rules=True,
        ).decode("ascii")
    except (idna.IDNAError, UnicodeError) as error:
        raise UrlSafetyError("hostname is not valid UTS #46 IDNA") from error

    labels = canonical.split(".")
    if any(not label for label in labels):
        raise UrlSafetyError("hostname contains an empty label")
    if any(len(label.encode("ascii")) > MAX_LABEL_BYTES for label in labels):
        raise UrlSafetyError("hostname label exceeds the byte limit")
    if len(canonical.encode("ascii")) > MAX_HOSTNAME_BYTES:
        raise UrlSafetyError("hostname exceeds the byte limit")
    return canonical, canonical


def _has_explicit_default_port(netloc: str) -> bool:
    if netloc.startswith("["):
        closing = netloc.find("]")
        suffix = netloc[closing + 1 :]
        if not suffix:
            return False
        if suffix != ":443":
            raise UrlSafetyError("only port 443 is allowed")
        return True
    if ":" not in netloc:
        return False
    _, separator, port_text = netloc.rpartition(":")
    if not separator or port_text != "443":
        raise UrlSafetyError("only port 443 is allowed")
    return True


def parse_policy_url(raw: str) -> PolicyUrl:
    """Parse one untrusted absolute URL without performing network access."""

    _reject_unsafe_text(raw)
    try:
        parts = urllib.parse.urlsplit(raw, allow_fragments=True)
        port = parts.port
        username = parts.username
        password = parts.password
    except (TypeError, ValueError) as error:
        raise UrlSafetyError("URL cannot be parsed unambiguously") from error

    if parts.scheme.lower() != "https" or not parts.netloc:
        raise UrlSafetyError("an absolute HTTPS URL is required")
    if username is not None or password is not None or "@" in parts.netloc:
        raise UrlSafetyError("userinfo is not allowed")
    if port not in (None, 443):
        raise UrlSafetyError("only port 443 is allowed")

    explicit_port = _has_explicit_default_port(parts.netloc)
    hostname, request_host = _canonical_hostname(parts.hostname or "", parts.netloc)
    request_authority = request_host + (":443" if explicit_port else "")
    path_and_query = parts.path
    without_fragment = raw.split("#", 1)[0]
    if "?" in without_fragment:
        path_and_query += "?" + parts.query
    request_url = f"https://{request_authority}{path_and_query}"
    if _encoded_length(request_url) > MAX_URL_BYTES:
        raise UrlSafetyError("normalized URL exceeds the byte limit")

    return PolicyUrl(
        original=raw,
        request_url=request_url,
        hostname=hostname,
        port=443,
        path_and_query=path_and_query,
    )


def parse_redirect_url(current: PolicyUrl, location: str) -> PolicyUrl:
    """Resolve one redirect location and reapply the complete URL policy."""

    _reject_unsafe_text(location)
    resolved = urllib.parse.urljoin(current.request_url, location)
    return parse_policy_url(resolved)
