import http.client
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from website_hunter.config import settings
from website_hunter.security.urls import UnsafeTarget, validate_public_url


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self) -> None:
        import socket

        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        import socket

        raw = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


@dataclass
class FetchResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    truncated: bool = False


class SafeFetcher:
    def __init__(self, timeout: float | None = None, max_bytes: int | None = None):
        self.timeout = timeout or float(settings.defaults["screen_timeout_seconds"])
        self.max_bytes = max_bytes or int(settings.defaults["max_html_bytes"])
        self.throttle = HostThrottle()

    def get(self, url: str, max_redirects: int | None = None) -> FetchResponse:
        redirect_limit = max_redirects if max_redirects is not None else int(settings.defaults["max_redirects"])
        current = url
        for hop in range(redirect_limit + 1):
            normalized, address, port, target = validate_public_url(current)
            parts = urlsplit(normalized)
            self.throttle.wait(parts.hostname or "")
            connection_type = _PinnedHTTPSConnection if parts.scheme == "https" else _PinnedHTTPConnection
            connection = connection_type(parts.hostname or "", port, address, self.timeout)
            try:
                connection.request(
                    "GET",
                    target,
                    headers={
                        "User-Agent": "SlovakWebsiteHunter/0.1 (+local prototype)",
                        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                headers = {key.lower(): value for key, value in response.getheaders()}
                if response.status in {301, 302, 303, 307, 308} and headers.get("location"):
                    if hop == redirect_limit:
                        raise UnsafeTarget("The website exceeded the redirect limit.")
                    current = urljoin(normalized, headers["location"])
                    continue
                body = response.read(self.max_bytes + 1)
                truncated = len(body) > self.max_bytes
                return FetchResponse(
                    url=normalized,
                    status_code=response.status,
                    headers=headers,
                    body=body[: self.max_bytes],
                    truncated=truncated,
                )
            finally:
                connection.close()
        raise UnsafeTarget("The website could not be safely resolved.")


class HostThrottle:
    def __init__(self, minimum_delay: float | None = None):
        self.minimum_delay = minimum_delay or float(settings.defaults["host_delay_seconds"])
        self.last_seen: dict[str, float] = {}

    def wait(self, hostname: str) -> None:
        now = time.monotonic()
        elapsed = now - self.last_seen.get(hostname, 0.0)
        if elapsed < self.minimum_delay:
            time.sleep(self.minimum_delay - elapsed)
        self.last_seen[hostname] = time.monotonic()
