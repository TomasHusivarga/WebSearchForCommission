import pytest

from website_hunter.contracts import RenderResult, ScreeningResult
from website_hunter.pipeline.service import CandidatePipeline


@pytest.mark.parametrize(
    ("screening_updates", "evidence_fragment"),
    [
        ({"parked": True}, "placeholder"),
        ({"bot_challenge": True}, "bot-verification challenge"),
        ({"replacement_notice": True, "replacement_url": "https://new.example.sk/"}, "replacement website"),
        ({"site_kind": "forum"}, "discussion forum"),
    ],
)
def test_pipeline_skips_rendering_nonprospect_homepages(monkeypatch, screening_updates, evidence_fragment):
    class MemoryRepository:
        def __init__(self):
            self.fields = {}
            self.finished = None

        def update_candidate(self, candidate_id, **fields):
            self.fields.update(fields)

        def finish_job(self, job_id, status, error=None):
            self.finished = (job_id, status, error)

        def get_candidate(self, candidate_id):
            return {"sources": []}

        def find_duplicate(self, domain, candidate_id):
            return None

    class FakeScreener:
        def inspect(self, url, hint):
            values = {
                "input_url": url,
                "final_url": url,
                "status_code": 200,
                "reachable": True,
            }
            values.update(screening_updates)
            return ScreeningResult(**values)

    class NeverRenderer:
        def render(self, candidate_id, url):
            raise AssertionError("Nonprospect homepages should not be rendered")

    monkeypatch.setattr("website_hunter.pipeline.service.validate_public_url", lambda url: (url, "127.0.0.1", None, None))
    repository = MemoryRepository()
    pipeline = CandidatePipeline.__new__(CandidatePipeline)
    pipeline.repository = repository
    pipeline.screener = FakeScreener()
    pipeline.renderer = NeverRenderer()

    pipeline.process({"id": 7, "job_id": 9, "input_url": "https://candidate.example.sk/", "business_hint_json": "{}"})

    classification = repository.fields["classification_json"]
    assert classification["provider"] == "screening"
    assert evidence_fragment in classification["evidence"][0]
    assert repository.finished[0:2] == (9, "complete")


def test_pipeline_saves_fast_viktor_score_without_rendering_every_candidate(monkeypatch):
    class MemoryRepository:
        def __init__(self):
            self.fields = {}
            self.finished = None

        def update_candidate(self, candidate_id, **fields):
            self.fields.update(fields)

        def finish_job(self, job_id, status, error=None):
            self.finished = (job_id, status, error)

        def get_candidate(self, candidate_id):
            return {"sources": []}

        def find_duplicate(self, domain, candidate_id):
            return None

    class FakeScreener:
        def inspect(self, url, hint):
            return ScreeningResult(
                input_url=url,
                final_url=url,
                status_code=200,
                reachable=True,
                html="<html><head><title>Auto servis</title></head><body>Opravy vozidiel</body></html>",
                raw_html=b"<html><head><title>Auto servis</title></head><body>Opravy vozidiel</body></html>",
                analysis_headers={"content-type": "text/html"},
            )

    class NeverRenderer:
        def render(self, candidate_id, url):
            raise AssertionError("Fast scoring should not render every candidate")

    monkeypatch.setattr("website_hunter.pipeline.service.validate_public_url", lambda url: (url, "127.0.0.1", None, None))
    repository = MemoryRepository()
    pipeline = CandidatePipeline.__new__(CandidatePipeline)
    pipeline.repository = repository
    pipeline.screener = FakeScreener()
    pipeline.renderer = NeverRenderer()

    pipeline.process({"id": 7, "job_id": 9, "input_url": "https://candidate.example.sk/", "business_hint_json": "{}"})

    opportunity = repository.fields["opportunity_json"]
    assert opportunity["analysis_depth"] == "fast"
    assert opportunity["issues"]["no_viewport"] == 30
    assert "mobile_overflow" in opportunity["pending_checks"]
    assert repository.fields["stage"] == "fast_scored"
    assert repository.finished[0:2] == (9, "complete")
    assert "checked only for selected websites" in opportunity["method"]


def test_selected_candidate_gets_browser_score_screenshots_and_ai_report_data(monkeypatch):
    class MemoryRepository:
        def __init__(self):
            self.fields = {}
            self.candidate = {
                "id": 7,
                "input_url": "https://candidate.example.sk/",
                "final_url": "https://candidate.example.sk/",
                "business_hint": {},
                "business": {"name": "Auto servis"},
                "sources": [],
                "contacts": [],
                "opportunity": {"lead_score": 30, "page_speed": {"performance_score": 22, "field_data": True}},
            }

        def get_candidate(self, candidate_id):
            return {**self.candidate, **{key.removesuffix("_json"): value for key, value in self.fields.items()}}

        def update_candidate(self, candidate_id, **fields):
            self.fields.update(fields)

        def find_duplicate(self, domain, candidate_id):
            return None

    class FakeScreener:
        def inspect(self, url, hint):
            return ScreeningResult(
                input_url=url,
                final_url=url,
                status_code=200,
                reachable=True,
                title="Auto servis",
                text="Auto servis a opravy vozidiel",
                html="<html><head><title>Auto servis</title><meta name='viewport' content='width=device-width'></head><body>Opravy vozidiel</body></html>",
                raw_html=b"<html><head><title>Auto servis</title><meta name='viewport' content='width=device-width'></head><body>Opravy vozidiel</body></html>",
                analysis_headers={"content-type": "text/html"},
            )

    class FakeRenderer:
        def render(self, candidate_id, url):
            return RenderResult(
                desktop_path="desktop.png",
                mobile_path="mobile.png",
                final_url=url,
                horizontal_overflow=True,
                trackers_before_consent=["google_analytics"],
            )

    class FakeAnalyzer:
        def analyze(self, **kwargs):
            assert kwargs["desktop_path"] == "desktop.png"
            assert kwargs["mobile_path"] == "mobile.png"
            return {
                "summary": "The service offer is clear.",
                "visual_category": "DATED",
                "visual_evidence": ["Dense fixed-width layout"],
                "mobile_assessment": "Some content extends past the mobile viewport.",
                "redesign_candidate": True,
                "sufficient_evidence": True,
                "positives": [{"point": "Services", "evidence": "The page lists repair services."}],
                "improvements": [],
                "redesign_direction": "Keep the familiar green colors and make the services easier to scan.",
                "model": "test-model",
            }

    monkeypatch.setattr("website_hunter.pipeline.service.validate_public_url", lambda url: (url, "127.0.0.1", None, None))
    monkeypatch.setattr("website_hunter.pipeline.service.extract_contacts", lambda html, url: [])
    monkeypatch.setattr("website_hunter.pipeline.service.contact_page_urls", lambda *args: [])
    class FakeBusiness:
        def to_dict(self):
            return {"name": "Auto servis"}

    monkeypatch.setattr("website_hunter.pipeline.service.extract_business", lambda *args: FakeBusiness())
    repository = MemoryRepository()
    pipeline = CandidatePipeline.__new__(CandidatePipeline)
    pipeline.repository = repository
    pipeline.screener = FakeScreener()
    pipeline.renderer = FakeRenderer()
    pipeline.analyzer = FakeAnalyzer()

    candidate = pipeline.analyze_candidate(7)

    opportunity = repository.fields["opportunity_json"]
    assert opportunity["analysis_depth"] == "ai"
    assert opportunity["ai_analysis"]["positives"][0]["point"] == "Services"
    assert opportunity["issues"]["mobile_overflow"] == 20
    assert opportunity["issues"]["psi_very_poor"] == 12
    assert opportunity["activity_signals"]["field_data"] == 25
    assert repository.fields["screenshot_desktop"] == "desktop.png"
    assert repository.fields["screenshot_mobile"] == "mobile.png"
    assert repository.fields["stage"] == "ai_analyzed"
    assert candidate["classification"]["category"] == "DATED"


def test_ai_retry_reuses_saved_browser_evidence_after_a_request_failure(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from website_hunter.classification.openai_website_analysis import OpenAIAnalysisError
    from website_hunter.config import settings as runtime_settings

    screenshot_root = tmp_path / "screenshots"
    screenshot_root.mkdir()
    desktop = screenshot_root / "desktop.png"
    mobile = screenshot_root / "mobile.png"
    desktop.write_bytes(b"desktop")
    mobile.write_bytes(b"mobile")
    monkeypatch.setattr(
        "website_hunter.pipeline.service.settings",
        SimpleNamespace(defaults=runtime_settings.defaults, screenshot_dir=screenshot_root),
    )

    class MemoryRepository:
        def __init__(self):
            self.fields = {}
            self.candidate = {
                "id": 7,
                "input_url": "https://candidate.example.sk/",
                "final_url": "https://candidate.example.sk/",
                "business_hint": {},
                "business": {"name": "Auto servis"},
                "contacts": [],
                "sources": [],
                "opportunity": {"lead_score": 30},
            }

        def get_candidate(self, candidate_id):
            decoded_fields = {key.removesuffix("_json"): value for key, value in self.fields.items()}
            return {**self.candidate, **decoded_fields}

        def update_candidate(self, candidate_id, **fields):
            self.fields.update(fields)

        def find_duplicate(self, domain, candidate_id):
            return None

    class FakeScreener:
        calls = 0

        def inspect(self, url, hint):
            self.calls += 1
            return ScreeningResult(
                input_url=url,
                final_url=url,
                status_code=200,
                reachable=True,
                title="Auto servis",
                text="Opravy vozidiel",
                html="<html><head><title>Auto servis</title></head><body>Opravy vozidiel</body></html>",
                raw_html=b"<html><head><title>Auto servis</title></head><body>Opravy vozidiel</body></html>",
            )

    class FakeRenderer:
        calls = 0

        def render(self, candidate_id, url):
            self.calls += 1
            return RenderResult(str(desktop), str(mobile), url, horizontal_overflow=True)

    class FakeBusiness:
        def to_dict(self):
            return {"name": "Auto servis"}

    class RetryAnalyzer:
        calls = 0

        def analyze(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise OpenAIAnalysisError("Temporary API failure.")
            return {
                "summary": "The service offer is clear.",
                "visual_category": "DATED",
                "visual_evidence": ["Dense layout"],
                "mobile_assessment": "Content overflows the mobile viewport.",
                "redesign_candidate": True,
                "sufficient_evidence": True,
                "positives": [],
                "improvements": [],
                "redesign_direction": "Use a more responsive layout.",
                "model": "test-model",
            }

    monkeypatch.setattr("website_hunter.pipeline.service.validate_public_url", lambda url: (url, "127.0.0.1", None, None))
    monkeypatch.setattr("website_hunter.pipeline.service.extract_contacts", lambda html, url: [])
    monkeypatch.setattr("website_hunter.pipeline.service.contact_page_urls", lambda *args: [])
    monkeypatch.setattr("website_hunter.pipeline.service.extract_business", lambda *args: FakeBusiness())
    repository = MemoryRepository()
    pipeline = CandidatePipeline.__new__(CandidatePipeline)
    pipeline.repository = repository
    pipeline.screener = FakeScreener()
    pipeline.renderer = FakeRenderer()
    pipeline.analyzer = RetryAnalyzer()

    with pytest.raises(OpenAIAnalysisError, match="Temporary API failure"):
        pipeline.analyze_candidate(7)

    assert repository.fields["stage"] == "browser_analyzed"
    assert repository.fields["screenshot_desktop"] == str(desktop)
    assert repository.fields["opportunity_json"]["analysis_depth"] == "browser"
    candidate = pipeline.analyze_candidate(7)

    assert candidate["stage"] == "ai_analyzed"
    assert pipeline.renderer.calls == 1
    assert pipeline.screener.calls == 1
    assert pipeline.analyzer.calls == 2
