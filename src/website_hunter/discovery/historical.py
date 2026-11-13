from website_hunter.config import settings
from website_hunter.contracts import DiscoveredDomain
from website_hunter.discovery.commoncrawl import CommonCrawlAdapter
from website_hunter.storage.repository import Repository


class HistoricalDiscoveryAdapter:
    source = "historical_source"

    def __init__(self, repository: Repository | None = None, archive: CommonCrawlAdapter | None = None):
        self.repository = repository or Repository()
        self.archive = archive or CommonCrawlAdapter()

    def discover(self, limit: int, strategy: str = "random") -> tuple[list[DiscoveredDomain], int, int, list[str]]:
        domains = self.repository.sample_unchecked_history(limit, strategy)
        matches: list[DiscoveredDomain] = []
        uncertain_errors = 0
        checked_domains = []
        cutoff = int(settings.defaults["history_age_cutoff_year"])
        for domain in domains:
            history = self.archive.history(domain, index_count=int(settings.defaults["commoncrawl_discovery_indexes"]))
            captures = [item for item in history.get("captures", []) if item.get("timestamp")]
            had_errors = any(item.get("error") for item in history.get("captures", []))
            uncertain_errors += int(had_errors)
            oldest = history.get("oldest_sampled_capture")
            old_enough = bool(oldest and str(oldest)[:4].isdigit() and int(str(oldest)[:4]) <= cutoff)
            if old_enough:
                matches.append(
                    DiscoveredDomain(
                        url=f"https://{domain}",
                        source=self.source,
                        source_url=str(captures[0].get("url") or "https://index.commoncrawl.org/"),
                        business_hint={"historical_evidence": history},
                    )
                )
            if not had_errors or old_enough:
                checked_domains.append(domain)
        self.repository.mark_history_checked(checked_domains)
        return matches, len(domains), uncertain_errors, domains
