import pytest

from website_hunter.contracts import ScreeningResult
from website_hunter.pipeline.service import CandidatePipeline


@pytest.mark.parametrize(
    ("screening_updates", "evidence_fragment"),
    [
        ({"parked": True}, "placeholder"),
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
