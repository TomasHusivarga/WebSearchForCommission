import re
import urllib.robotparser
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from website_hunter.config import settings
from website_hunter.contracts import ScreeningResult
from website_hunter.security.fetch import FetchResponse, SafeFetcher
from website_hunter.security.urls import canonical_host
from website_hunter.screening.html_signals import (
    detect_business_signals,
    detect_forum_site,
    detect_legacy_signals,
    detect_parked_page,
    detect_replacement_url,
    has_replacement_notice,
)


class SiteScreener:
    def __init__(self, fetcher: SafeFetcher | None = None):
        self.fetcher = fetcher or SafeFetcher()
        self._robots: dict[str, tuple[urllib.robotparser.RobotFileParser | None, bool | None]] = {}

    def page_allowed(self, url: str) -> bool | None:
        return self._robots_allowed(url)

    def inspect(self, url: str, hint: dict[str, object] | None = None) -> ScreeningResult:
        robots_allowed = self._robots_allowed(url)
        if robots_allowed is False:
            return ScreeningResult(url, url, None, False, robots_allowed=False, failure_reason="Robots.txt disallows this page.", discovery_hint=hint or {})
        first = self._try_fetch(url)
        if first is None or first.status_code >= 500:
            alternate = self._http_alternate(url)
            if alternate:
                second = self._try_fetch(alternate)
                if second and (first is None or second.status_code < first.status_code):
                    first = second
        if first is None:
            return ScreeningResult(url, None, None, False, robots_allowed=robots_allowed, failure_reason="The website could not be fetched safely.", discovery_hint=hint or {})
        content_type = first.headers.get("content-type", "")
        if "html" not in content_type.lower() and first.status_code < 400:
            return ScreeningResult(url, first.url, first.status_code, True, content_type=content_type, robots_allowed=robots_allowed, failure_reason="The response was not an HTML page.", discovery_hint=hint or {})
        html = first.body.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        description_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        description = (description_tag.get("content") or "").strip() if description_tag else ""
        text = soup.get_text(" ", strip=True)[:30000]
        links = []
        for anchor in soup.find_all("a", href=True)[:500]:
            href = anchor["href"].strip()
            label = anchor.get_text(" ", strip=True)
            if href.lower().startswith(("mailto:", "tel:")):
                links.append({"href": href[:500], "label": label[:180]})
            elif href and not href.lower().startswith(("javascript:", "data:")):
                links.append({"href": href[:500], "label": label[:180]})
        legacy = detect_legacy_signals(html, soup)
        replacement_notice = has_replacement_notice(title, text)
        replacement_url = detect_replacement_url(links, first.url, text) if replacement_notice else None
        redirected_to_other_site = canonical_host(url) != canonical_host(first.url)
        if redirected_to_other_site:
            replacement_notice = True
            replacement_url = first.url
        return ScreeningResult(
            input_url=url,
            final_url=first.url,
            status_code=first.status_code,
            reachable=first.status_code < 500,
            title=title[:240],
            description=description[:600],
            content_type=content_type,
            text=text,
            links=links,
            legacy_signals=legacy,
            business_signals=detect_business_signals(title, text, soup),
            parked=detect_parked_page(title, text),
            replacement_notice=replacement_notice,
            replacement_url=replacement_url,
            site_kind="forum" if detect_forum_site(title, text, html, links) else None,
            javascript_likely=len(text) < 120 and len(soup.find_all("script")) >= 4,
            robots_allowed=robots_allowed,
            failure_reason="HTML response was truncated at the configured size limit." if first.truncated else None,
            discovery_hint=hint or {},
            html=html,
            raw_html=first.body,
            analysis_headers={key: first.headers.get(key, "") for key in ("content-type", "x-powered-by")},
        )

    def _try_fetch(self, url: str) -> FetchResponse | None:
        try:
            return self.fetcher.get(url)
        except Exception:
            return None

    def _robots_allowed(self, url: str) -> bool | None:
        host = canonical_host(url)
        if host in self._robots:
            parser, reachable = self._robots[host]
            return parser.can_fetch("SlovakWebsiteHunter/0.1", url) if parser and reachable else reachable
        parts = urlsplit(url)
        scheme = parts.scheme or "https"
        robots_url = f"{scheme}://{parts.netloc}/robots.txt"
        try:
            response = self.fetcher.get(robots_url, max_redirects=2)
        except Exception:
            self._robots[host] = (None, None)
            return None
        if response.status_code == 404:
            self._robots[host] = (None, True)
            return True
        if response.status_code != 200:
            self._robots[host] = (None, None)
            return None
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
        self._robots[host] = (parser, True)
        return parser.can_fetch("SlovakWebsiteHunter/0.1", url)

    @staticmethod
    def _http_alternate(url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme != "https":
            return None
        return f"http://{parts.netloc}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
