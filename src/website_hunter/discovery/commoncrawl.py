import json
from datetime import datetime

import httpx

from website_hunter.config import settings
from website_hunter.security.urls import canonical_host
from website_hunter.security.fetch import HostThrottle


class CommonCrawlAdapter:
    source = "commoncrawl_history"

    def __init__(self):
        self._indexes: list[dict[str, str]] | None = None
        self._throttle = HostThrottle(float(settings.defaults["commoncrawl_request_delay_seconds"]))

    def history(self, domain: str, index_count: int = 4) -> dict[str, object]:
        host = canonical_host(domain)
        if self._indexes is None:
            self._throttle.wait("index.commoncrawl.org")
            info = httpx.get(str(settings.defaults["commoncrawl_info_url"]), timeout=15, headers={"User-Agent": "SlovakWebsiteHunter/0.1"})
            info.raise_for_status()
            self._indexes = info.json()
        if not isinstance(self._indexes, list) or not self._indexes:
            return {"domain": host, "captures": [], "status": "no_indexes"}
        picks = _select_spread_indexes(self._indexes, index_count)
        captures: list[dict[str, object]] = []
        for item in picks:
            index_id = str(item["id"])
            url = str(item.get("cdx-api") or f"{settings.defaults['commoncrawl_index_url']}/{index_id}-index")
            try:
                self._throttle.wait("index.commoncrawl.org")
                response = httpx.get(
                    url,
                    params={"url": host, "matchType": "domain", "output": "json", "filter": "status:200", "fl": "timestamp,url,status"},
                    timeout=12,
                    headers={"User-Agent": "SlovakWebsiteHunter/0.1"},
                )
                if response.status_code == 200:
                    for line in response.text.splitlines()[:30]:
                        try:
                            item_data = json.loads(line)
                            captures.append({"timestamp": item_data.get("timestamp"), "url": item_data.get("url"), "status": item_data.get("status"), "index": index_id})
                        except ValueError:
                            continue
                elif response.status_code not in {404, 400}:
                    captures.append({"index": index_id, "error": f"HTTP {response.status_code}"})
            except httpx.HTTPError as error:
                captures.append({"index": index_id, "error": str(error)[:180]})
        timestamps = [str(item.get("timestamp")) for item in captures if item.get("timestamp") and str(item.get("timestamp")).isdigit()]
        oldest = min(timestamps) if timestamps else None
        return {
            "domain": host,
            "checked_at": datetime.now().astimezone().isoformat(),
            "capture_count_sampled": sum(1 for item in captures if item.get("timestamp")),
            "oldest_sampled_capture": oldest,
            "captures": captures,
            "note": "Sampled public Common Crawl indexes. Missing captures do not prove that the domain is new.",
        }


def _select_spread_indexes(indexes: list[dict[str, str]], count: int = 4) -> list[dict[str, str]]:
    valid = [item for item in indexes if item.get("id") and item.get("cdx-api")]
    count = max(1, count)
    if len(valid) <= count:
        return valid
    valid.sort(key=lambda item: str(item["id"]))
    if count == 1:
        return [valid[-1]]
    return [valid[round(position * (len(valid) - 1) / (count - 1))] for position in range(count)]
