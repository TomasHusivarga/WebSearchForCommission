import socket

import pytest

from website_hunter.security import fetch as fetch_module
from website_hunter.security.fetch import SafeFetcher
from website_hunter.security.urls import UnsafeTarget, normalize_url, validate_public_url


def test_normalizes_domain_without_losing_path_or_query():
    assert normalize_url(" WWW.Example.SK/catalog?a=1#section ") == "https://example.sk/catalog?a=1"


@pytest.mark.parametrize("url", ["ftp://example.sk", "http://user:pass@example.sk", "https://example.sk:8443"])
def test_rejects_unsupported_target_shapes(url):
    with pytest.raises(UnsafeTarget):
        normalize_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://10.0.0.2", "http://[::1]/", "http://localhost"])
def test_blocks_private_and_local_targets(url):
    with pytest.raises(UnsafeTarget):
        validate_public_url(url)


def test_blocks_dns_names_that_resolve_to_private_addresses(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.2.5", 443))])
    with pytest.raises(UnsafeTarget, match="Private or non-public"):
        validate_public_url("https://internal.example")


def test_redirect_to_private_address_is_rejected(monkeypatch):
    real_validate = fetch_module.validate_public_url

    def validate(url):
        if "example.sk" in url:
            return "https://example.sk/", "93.184.216.34", 443, "/"
        return real_validate(url)

    class RedirectResponse:
        status = 302

        def getheaders(self):
            return [("Location", "http://127.0.0.1/admin")]

    class FakeConnection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return RedirectResponse()

        def close(self):
            pass

    monkeypatch.setattr(fetch_module, "validate_public_url", validate)
    monkeypatch.setattr(fetch_module, "_PinnedHTTPSConnection", FakeConnection)
    with pytest.raises(UnsafeTarget):
        SafeFetcher(timeout=0.1).get("https://example.sk/")
