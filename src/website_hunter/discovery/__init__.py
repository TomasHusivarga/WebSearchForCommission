from typing import Protocol

from website_hunter.contracts import DiscoveredDomain


class DiscoveryAdapter(Protocol):
    source: str

    def discover(self, **options: object) -> list[DiscoveredDomain]: ...
