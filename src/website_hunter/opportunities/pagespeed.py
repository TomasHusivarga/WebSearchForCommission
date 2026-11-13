from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx


API_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


class PageSpeedError(Exception):
    pass


def parse_pagespeed(data: dict[str, Any]) -> dict[str, Any]:
    lighthouse = data.get("lighthouseResult") or {}
    raw_score = ((lighthouse.get("categories") or {}).get("performance") or {}).get("score")
    audits = lighthouse.get("audits") or {}
    lcp = (audits.get("largest-contentful-paint") or {}).get("numericValue")
    cls = (audits.get("cumulative-layout-shift") or {}).get("numericValue")
    field_data = any(
        isinstance(metrics, dict) and bool(metrics)
        for metrics in ((data.get(key) or {}).get("metrics") for key in ("loadingExperience", "originLoadingExperience"))
    )
    return {
        "performance_score": int(raw_score * 100 + 0.5) if isinstance(raw_score, (int, float)) else None,
        "lcp_ms": int(round(lcp)) if isinstance(lcp, (int, float)) else None,
        "cls": float(cls) if isinstance(cls, (int, float)) else None,
        "field_data": field_data,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "status": "measured",
    }


async def measure_mobile(url: str, api_key: str) -> dict[str, Any]:
    params = {"url": url, "strategy": "mobile", "category": "performance", "key": api_key}
    try:
        async with httpx.AsyncClient(timeout=75, follow_redirects=False) as client:
            response = await client.get(API_URL, params=params)
    except httpx.HTTPError as error:
        raise PageSpeedError("PageSpeed could not be reached. Try again later.") from error
    if response.status_code != 200:
        raise PageSpeedError(f"PageSpeed returned HTTP {response.status_code}.")
    try:
        return parse_pagespeed(response.json())
    except (ValueError, TypeError) as error:
        raise PageSpeedError("PageSpeed returned an unreadable result.") from error
