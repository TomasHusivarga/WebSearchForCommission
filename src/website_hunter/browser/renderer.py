import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from website_hunter.config import settings
from website_hunter.contracts import RenderResult
from website_hunter.security.urls import validate_public_url


class BrowserRenderer:
    def render(self, candidate_id: int, url: str) -> RenderResult:
        settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
        safe_name = hashlib.sha256(f"{candidate_id}:{url}".encode()).hexdigest()[:18]
        target_dir = settings.screenshot_dir / safe_name
        target_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        failed_load = False
        final_url = url
        mobile_overflow: bool | None = None
        broken_images = 0
        tracking_requests: set[str] = set()
        tracker_patterns = {
            name: re.compile(pattern, re.I)
            for name, pattern in settings.defaults.get("tracker_request_patterns", {}).items()
        }
        request_count = 0
        host_last_seen: dict[str, float] = {}
        desktop_path = target_dir / "desktop.jpg"
        mobile_path = target_dir / "mobile.jpg"
        normalized_url, pinned_address, _, _ = validate_public_url(url)
        url = normalized_url
        primary_host = urlsplit(url).hostname or ""
        resolver_rule = f"--host-resolver-rules=MAP {primary_host} {pinned_address}" if ":" not in pinned_address else None

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=[resolver_rule] if resolver_rule else [])
            context = browser.new_context(
                viewport=settings.defaults["desktop_viewport"],
                java_script_enabled=True,
                accept_downloads=False,
                service_workers="block",
                ignore_https_errors=False,
            )
            page = context.new_page()

            def guard(route):
                nonlocal request_count
                request_count += 1
                if request_count > int(settings.defaults["browser_request_limit"]):
                    route.abort()
                    return
                request_url = route.request.url
                for name, pattern in tracker_patterns.items():
                    if pattern.search(request_url):
                        tracking_requests.add(name)
                if route.request.resource_type in {"media", "websocket"}:
                    route.abort()
                    return
                try:
                    validate_public_url(request_url)
                except Exception:
                    route.abort()
                    return
                hostname = urlsplit(request_url).hostname or ""
                now = time.monotonic()
                delay = float(settings.defaults["browser_request_delay_seconds"]) - (now - host_last_seen.get(hostname, 0.0))
                if delay > 0:
                    time.sleep(delay)
                host_last_seen[hostname] = time.monotonic()
                route.continue_()

            page.route("**/*", guard)
            context.route_web_socket("**/*", lambda route: route.close())
            page.on("pageerror", lambda error: errors.append(str(error)[:180]) if len(errors) < 8 else None)
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=int(settings.defaults["browser_timeout_ms"]))
                if response is None or response.status >= 500:
                    failed_load = True
                final_url = page.url
                page.wait_for_timeout(500)
                page.screenshot(path=str(desktop_path), type="jpeg", quality=88, full_page=True, animations="disabled", timeout=int(settings.defaults["browser_timeout_ms"]))
                dimensions = page.evaluate("({width: document.documentElement.scrollWidth, viewport: window.innerWidth, broken: [...document.images].filter(image => !image.complete || image.naturalWidth === 0).length})")
                broken_images = int(dimensions.get("broken", 0))
                context.set_default_timeout(int(settings.defaults["browser_timeout_ms"]))
                page.set_viewport_size(settings.defaults["mobile_viewport"])
                page.wait_for_timeout(350)
                mobile_metrics = page.evaluate("({width: document.documentElement.scrollWidth, viewport: window.innerWidth, broken: [...document.images].filter(image => !image.complete || image.naturalWidth === 0).length})")
                mobile_overflow = int(mobile_metrics["width"]) > int(mobile_metrics["viewport"]) + 4
                broken_images = max(broken_images, int(mobile_metrics.get("broken", 0)))
                page.screenshot(path=str(mobile_path), type="jpeg", quality=88, full_page=True, animations="disabled", timeout=int(settings.defaults["browser_timeout_ms"]))
            except PlaywrightError as error:
                failed_load = True
                errors.append(str(error)[:220])
                if page.url and page.url != "about:blank":
                    final_url = page.url
                    for path in (desktop_path, mobile_path):
                        if not path.exists():
                            try:
                                page.screenshot(path=str(path), type="jpeg", quality=88, full_page=True, timeout=5000)
                            except PlaywrightError:
                                pass
            finally:
                context.close()
                browser.close()
        return RenderResult(
            desktop_path=str(desktop_path) if desktop_path.exists() else None,
            mobile_path=str(mobile_path) if mobile_path.exists() else None,
            final_url=final_url,
            horizontal_overflow=mobile_overflow,
            broken_images=broken_images,
            trackers_before_consent=sorted(tracking_requests),
            failed_load=failed_load,
            browser_errors=errors,
            uncertain=failed_load or not desktop_path.exists() or not mobile_path.exists(),
        )
