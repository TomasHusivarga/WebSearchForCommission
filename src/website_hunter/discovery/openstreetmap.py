import httpx

from website_hunter.config import settings
from website_hunter.contracts import DiscoveredDomain


class OpenStreetMapAdapter:
    source = "targeted_osm"

    def discover(self, town: str, limit: int, categories: list[str] | None = None) -> list[DiscoveredDomain]:
        allowed_towns = set(settings.defaults["benchmark_towns"])
        if town not in allowed_towns:
            raise ValueError("Choose a town from the configured list.")
        if limit < 1 or limit > int(settings.defaults["max_osm_results_per_town"]):
            raise ValueError("Result limit exceeds the configured Overpass limit.")
        catalog = settings.defaults["business_categories"]
        selected = categories or list(catalog)
        invalid = set(selected) - set(catalog)
        if invalid:
            raise ValueError(f"Unknown business categories: {', '.join(sorted(invalid))}")
        tag_queries = [_tag_query(catalog[key]) for key in selected]
        if not tag_queries:
            raise ValueError("Select at least one business category.")
        escaped_town = town.replace('"', "")
        query = "\n".join(
            [
                f'[out:json][timeout:{int(settings.defaults["overpass_timeout_seconds"])}];',
                f'area["name"="{escaped_town}"]["boundary"="administrative"]["admin_level"="8"]->.town;',
                "(",
                *tag_queries,
                ");",
                f"out center tags {limit};",
            ]
        )
        response = httpx.post(
            str(settings.defaults["overpass_url"]),
            data={"data": query},
            timeout=float(settings.defaults["overpass_timeout_seconds"]) + 5,
            headers={"User-Agent": "SlovakWebsiteHunter/0.1"},
        )
        response.raise_for_status()
        payload = response.json()
        results: dict[str, DiscoveredDomain] = {}
        for element in payload.get("elements", []):
            tags = element.get("tags") or {}
            website = tags.get("website") or tags.get("contact:website")
            if not website:
                continue
            source_url = f"https://www.openstreetmap.org/{element.get('type')}/{element.get('id')}"
            results.setdefault(
                website.strip(),
                DiscoveredDomain(
                    url=website.strip(),
                    source=self.source,
                    source_url=source_url,
                    business_hint={
                        "name": tags.get("name"),
                        "industry": next((tags.get(key) for key in ("craft", "shop", "industrial", "office", "tourism", "amenity") if tags.get(key)), None),
                        "location": ", ".join(filter(None, [tags.get("addr:city"), tags.get("addr:postcode")])),
                        "address": ", ".join(filter(None, [tags.get("addr:street"), tags.get("addr:housenumber"), tags.get("addr:city")])),
                        "phone": tags.get("phone") or tags.get("contact:phone"),
                        "email": tags.get("email") or tags.get("contact:email"),
                    },
                ),
            )
        return list(results.values())[:limit]


def _tag_query(category: dict[str, str]) -> str:
    key = category["key"]
    if "value" in category:
        return f'nwr["website"]["{key}"="{category["value"]}"](area.town);'
    if "values" in category:
        return f'nwr["website"]["{key}"~"{category["values"]}"](area.town);'
    return f'nwr["website"]["{key}"](area.town);'
