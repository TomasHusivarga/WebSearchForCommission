import pytest

from website_hunter.config import settings
from website_hunter.discovery import sknic
from website_hunter.discovery.sknic import SknicInventoryAdapter
from website_hunter.storage.database import initialize
from website_hunter.storage.repository import Repository


class FakeResponse:
    def __init__(self, content, url):
        self.content = content
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit):
        assert limit == int(settings.defaults["sknic_max_bytes"]) + 1
        return self.content[:limit]

    def geturl(self):
        return self.url


class FakeOpener:
    def __init__(self, response):
        self.response = response

    def open(self, request, timeout):
        assert timeout == 45
        assert request.full_url == settings.defaults["sknic_url"]
        assert request.get_header("User-agent") == "SlovakWebsiteHunter/0.1"
        assert request.get_header("Accept") == "text/plain,text/html,*/*"
        return self.response


def test_import_parses_registered_domains_and_enforces_download_limit(monkeypatch):
    original_limit = settings.defaults["sknic_max_bytes"]
    monkeypatch.setitem(settings.defaults, "sknic_max_bytes", 256)
    payload = "domena;ID reg;ID drzitela;NS;datum exspiracie\nFirma.sk;REG-123;REG-123;ns.example;11.02.2027\nwww.obchod.sk;REG-123;REG-123;ns.example;11.02.2027\nnot-a-sk-domain.com;REG-123;REG-123;ns.example;11.02.2027\n".encode("iso-8859-2")
    response = FakeResponse(payload, settings.defaults["sknic_url"])
    monkeypatch.setattr(sknic.urllib.request, "build_opener", lambda handler: FakeOpener(response))

    domains, source_url = SknicInventoryAdapter().import_domains()

    assert domains == ["firma.sk", "www.obchod.sk"]
    assert source_url == settings.defaults["sknic_url"]
    monkeypatch.setitem(settings.defaults, "sknic_max_bytes", len(payload) - 1)
    with pytest.raises(ValueError, match="size limit"):
        SknicInventoryAdapter().import_domains()
    monkeypatch.setitem(settings.defaults, "sknic_max_bytes", original_limit)


def test_sknic_download_rejects_non_official_inventory_url(monkeypatch):
    monkeypatch.setitem(settings.defaults, "sknic_url", "https://example.com/domains.txt")

    with pytest.raises(ValueError, match="official HTTPS host"):
        SknicInventoryAdapter().import_domains()


def test_inventory_storage_preserves_exact_registered_domain(tmp_path):
    path = tmp_path / "hunter.sqlite3"
    initialize(path)
    repository = Repository(path)
    repository.add_inventory(["www.sk", "firma.sk"], "https://sk-nic.sk/subory/domains.txt")

    assert set(repository.sample_inventory(2, "prioritized")) == {"www.sk", "firma.sk"}
