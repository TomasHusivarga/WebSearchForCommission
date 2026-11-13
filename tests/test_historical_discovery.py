from website_hunter.discovery import commoncrawl
from website_hunter.discovery.commoncrawl import CommonCrawlAdapter, _select_spread_indexes
from website_hunter.discovery.historical import HistoricalDiscoveryAdapter


def test_spreads_history_lookups_across_oldest_and_newest_indexes():
    indexes = [
        {"id": f"CC-MAIN-{year}-01", "cdx-api": f"https://index.commoncrawl.org/CC-MAIN-{year}-01-index"}
        for year in range(2015, 2025)
    ]
    selected = _select_spread_indexes(indexes, 2)
    assert [item["id"] for item in selected] == ["CC-MAIN-2015-01", "CC-MAIN-2024-01"]


def test_historical_discovery_requires_old_capture_and_persists_checked_inventory():
    class MemoryRepository:
        def __init__(self):
            self.checked = []

        def sample_unchecked_history(self, limit, strategy):
            return ["old.sk", "recent.sk"][:limit]

        def mark_history_checked(self, domains):
            self.checked.extend(domains)

    class FakeArchive:
        def history(self, domain, index_count):
            timestamp = "20150101000000" if domain == "old.sk" else "20240101000000"
            return {
                "domain": domain,
                "oldest_sampled_capture": timestamp,
                "capture_count_sampled": 1,
                "captures": [{"timestamp": timestamp, "url": f"https://{domain}/", "index": "CC-MAIN-test"}],
            }

    repository = MemoryRepository()
    adapter = HistoricalDiscoveryAdapter(repository, FakeArchive())
    matches, checked_count, errors, checked_domains = adapter.discover(2)
    assert checked_count == 2
    assert errors == 0
    assert checked_domains == ["old.sk", "recent.sk"]
    assert repository.checked == ["old.sk", "recent.sk"]
    assert len(matches) == 1
    assert matches[0].url == "https://old.sk"
    assert matches[0].business_hint["historical_evidence"]["oldest_sampled_capture"] == "20150101000000"


def test_commoncrawl_history_returns_sampled_captures(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code=200, payload=None, content=""):
            self.status_code = status_code
            self.payload = payload
            self.text = content

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    indexes = [
        {"id": "CC-MAIN-2015-01", "cdx-api": "https://index.commoncrawl.org/2015-index"},
        {"id": "CC-MAIN-2024-01", "cdx-api": "https://index.commoncrawl.org/2024-index"},
    ]
    responses = [
        FakeResponse(content='{"timestamp":"20150601000000","url":"old.sk/","status":"200"}\n'),
        FakeResponse(content='{"timestamp":"20240101000000","url":"old.sk/","status":"200"}\n'),
    ]
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url == commoncrawl.settings.defaults["commoncrawl_info_url"]:
            return FakeResponse(payload=indexes)
        return responses.pop(0)

    monkeypatch.setattr(commoncrawl.httpx, "get", fake_get)
    adapter = CommonCrawlAdapter()
    monkeypatch.setattr(adapter._throttle, "wait", lambda host: None)

    result = adapter.history("old.sk", index_count=2)

    assert len(calls) == 3
    assert result["oldest_sampled_capture"] == "20150601000000"
    assert result["capture_count_sampled"] == 2
    assert result["captures"][0]["url"] == "old.sk/"
