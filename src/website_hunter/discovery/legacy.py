from website_hunter.contracts import DiscoveredDomain
from website_hunter.screening.service import SiteScreener
from website_hunter.storage.repository import Repository


class LegacyFingerprintAdapter:
    source = "legacy_fingerprint"

    def __init__(self, repository: Repository | None = None, screener: SiteScreener | None = None):
        self.repository = repository or Repository()
        self.screener = screener or SiteScreener()

    def discover(self, limit: int, strategy: str = "random") -> tuple[list[DiscoveredDomain], int, list[str]]:
        domains = self.repository.sample_unscreened_inventory(limit, strategy)
        matches: list[DiscoveredDomain] = []
        for domain in domains:
            screen = self.screener.inspect(f"https://{domain}")
            if screen.reachable and screen.status_code and screen.status_code < 400 and screen.legacy_signals:
                matches.append(
                    DiscoveredDomain(
                        url=screen.final_url or f"https://{domain}",
                        source=self.source,
                        source_url=screen.final_url,
                        business_hint={
                            "name": screen.title or None,
                            "legacy_signals": screen.legacy_signals,
                            "screening_status": screen.status_code,
                        },
                    )
                )
        self.repository.mark_inventory_screened(domains)
        return matches, len(domains), domains
