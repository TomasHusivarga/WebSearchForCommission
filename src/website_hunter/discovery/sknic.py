import random
import urllib.request
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler

from website_hunter.config import settings
from website_hunter.contracts import DiscoveredDomain
from website_hunter.security.urls import normalize_inventory_domain


class _SknicRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_host: str):
        super().__init__()
        self.allowed_host = allowed_host

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        target = urlsplit(new_url)
        if target.scheme != "https" or target.hostname != self.allowed_host:
            raise ValueError("The SK-NIC download redirected outside its official HTTPS host.")
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


class SknicInventoryAdapter:
    source = "sknic_inventory"

    def import_domains(self) -> tuple[list[str], str]:
        url = str(settings.defaults["sknic_url"])
        origin = urlsplit(url)
        if origin.scheme != "https" or origin.hostname != "sk-nic.sk":
            raise ValueError("The SK-NIC inventory URL must use the official HTTPS host.")
        max_bytes = int(settings.defaults["sknic_max_bytes"])
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "SlovakWebsiteHunter/0.1",
                "Accept": "text/plain,text/html,*/*",
            },
        )
        opener = urllib.request.build_opener(_SknicRedirectHandler(origin.hostname))
        with opener.open(request, timeout=45) as response:
            content = response.read(max_bytes + 1)
            source_url = response.geturl()
        if len(content) > max_bytes:
            raise ValueError("The SK-NIC domain list exceeded the configured size limit.")
        decoded = content.decode("iso-8859-2", errors="replace")
        domains: set[str] = set()
        for line in decoded.splitlines():
            raw = line.split(";", 1)[0].strip()
            if not raw.lower().endswith(".sk"):
                continue
            try:
                domains.add(normalize_inventory_domain(raw))
            except (ValueError, UnicodeError):
                continue
        return sorted(domains), source_url

    def sample(self, domains: list[str], limit: int, strategy: str = "random", seed: int | None = None) -> list[DiscoveredDomain]:
        if limit < 1 or limit > int(settings.defaults["max_batch_size"]):
            raise ValueError("Sample size must be within the configured batch limit.")
        pool = list(domains)
        if strategy == "random":
            random.Random(seed).shuffle(pool)
        elif strategy == "prioritized":
            pool.sort(key=lambda domain: (domain.count("-"), len(domain), domain))
        else:
            raise ValueError("Strategy must be random or prioritized.")
        return [DiscoveredDomain(url=f"https://{domain}", source=f"sknic_{strategy}_sample", source_url=str(settings.defaults["sknic_url"])) for domain in pool[:limit]]
