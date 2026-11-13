import json
import re
from typing import Any

from bs4 import BeautifulSoup

from website_hunter.contracts import BusinessIdentity


ICO_PATTERN = re.compile(r"(?i)(?:i[cč]o\s*[:.]?\s*)(\d{8})")
ADDRESS_PATTERN = re.compile(r"\b\d{3}\s?\d{2}\s+[A-ZÁ-Ž][A-Za-zÁ-ž .'-]{2,40}")


def extract_business(html: str, page_url: str, hint: dict[str, Any] | None = None) -> BusinessIdentity:
    hint = hint or {}
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    jsonld = _jsonld_objects(soup)
    organization = next((item for item in jsonld if any(key in item.get("@type", []) if isinstance(item.get("@type"), list) else key == item.get("@type") for key in ("Organization", "LocalBusiness", "Store", "ProfessionalService"))), {})
    title = soup.find("meta", property="og:site_name") or soup.find("title")
    title_value = title.get("content", "") if title and title.name == "meta" else title.get_text(" ", strip=True) if title else ""
    h1 = soup.find("h1")
    structured_address = organization.get("address") if isinstance(organization.get("address"), dict) else {}
    address = ", ".join(filter(None, [
        str(structured_address.get("streetAddress", "")),
        str(structured_address.get("postalCode", "")),
        str(structured_address.get("addressLocality", "")),
    ])) or hint.get("address")
    ico_match = ICO_PATTERN.search(text)
    location_match = ADDRESS_PATTERN.search(text)
    name = hint.get("name") or organization.get("name") or title_value or (h1.get_text(" ", strip=True) if h1 else None)
    description = organization.get("description") or (soup.find("meta", attrs={"name": re.compile("description", re.I)}) or {}).get("content")
    industry = hint.get("industry") or organization.get("additionalType")
    services = []
    if description:
        services.append(str(description).strip()[:280])
    sources = [page_url]
    if hint.get("source_url"):
        sources.append(str(hint["source_url"]))
    fields = [key for key in ("name", "industry", "location", "address", "phone", "email") if hint.get(key)]
    return BusinessIdentity(
        name=str(name).strip()[:180] if name else None,
        industry=str(industry).replace("_", " ")[:100] if industry else None,
        services=services,
        location=str(hint.get("location") or (location_match.group(0).strip() if location_match else "")) or None,
        ico=ico_match.group(1) if ico_match else None,
        address=str(address).strip() if address else None,
        sources=sources,
        verified_fields=[],
        inferred_fields=["industry"] if industry and not hint.get("industry") else [],
    )


def _jsonld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for tag in soup.find_all("script", type="application/ld+json")[:10]:
        try:
            payload = json.loads(tag.string or tag.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        objects = payload if isinstance(payload, list) else payload.get("@graph", [payload]) if isinstance(payload, dict) else []
        records.extend(item for item in objects if isinstance(item, dict))
    return records
