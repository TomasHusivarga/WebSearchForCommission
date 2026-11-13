import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit


class UnsafeTarget(ValueError):
    pass


def normalize_inventory_domain(value: str) -> str:
    candidate = value.strip().rstrip(".")
    try:
        host = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("The inventory row does not contain a valid domain name.") from error
    labels = host.split(".")
    if (
        len(host) > 253
        or not host.endswith(".sk")
        or len(labels) < 2
        or any(len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in labels)
    ):
        raise ValueError("The inventory row does not contain a valid .sk domain.")
    return host


def normalize_url(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("Enter a website URL or domain.")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in {"http", "https"}:
        raise UnsafeTarget("Only HTTP and HTTPS website URLs are supported.")
    if parts.username or parts.password:
        raise UnsafeTarget("URLs with embedded credentials are not supported.")
    if not parts.hostname:
        raise ValueError("Enter a valid public website URL.")
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError("The URL has an invalid port.") from error
    if port and port not in {80, 443}:
        raise UnsafeTarget("Only standard web ports 80 and 443 are allowed.")
    host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = f"[{host}]" if ":" in host else host
    if port and port != (443 if parts.scheme.lower() == "https" else 80):
        netloc = f"{netloc}:{port}"
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


def canonical_host(value: str) -> str:
    normalized = normalize_url(value)
    host = urlsplit(normalized).hostname
    if not host:
        raise ValueError("The URL has no hostname.")
    return host


def resolve_public_addresses(host: str, port: int) -> list[str]:
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        try:
            addresses = list(
                dict.fromkeys(
                    item[4][0]
                    for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                )
            )
        except OSError as error:
            raise UnsafeTarget(f"DNS lookup failed for {host}.") from error
    if not addresses:
        raise UnsafeTarget(f"DNS returned no address for {host}.")
    for address in addresses:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
        if not parsed.is_global:
            raise UnsafeTarget(f"Private or non-public network targets are blocked: {host}.")
    return addresses


def validate_public_url(value: str) -> tuple[str, str, int, str]:
    normalized = normalize_url(value)
    parts = urlsplit(normalized)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = resolve_public_addresses(parts.hostname or "", port)
    request_target = parts.path or "/"
    if parts.query:
        request_target = f"{request_target}?{parts.query}"
    return normalized, addresses[0], port, request_target
