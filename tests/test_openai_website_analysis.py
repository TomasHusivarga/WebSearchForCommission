import json

import pytest

from website_hunter.classification import openai_website_analysis as analyzer_module
from website_hunter.classification.openai_website_analysis import OpenAIAnalysisError, OpenAIWebsiteAnalyzer


def _analysis_result():
    return {
        "summary": "The service offer is clear.",
        "visual_category": "DATED",
        "visual_evidence": ["Dense layout"],
        "mobile_assessment": "Some content is difficult to read on a phone.",
        "redesign_candidate": True,
        "sufficient_evidence": True,
        "positives": [{"point": "Service list", "evidence": "Three services are listed."}],
        "improvements": [],
        "redesign_direction": "Keep the existing color palette.",
    }


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size):
        return self.body


def test_selected_website_analysis_sends_both_screenshots_and_returns_structured_review(monkeypatch, tmp_path):
    desktop = tmp_path / "desktop.png"
    mobile = tmp_path / "mobile.png"
    desktop.write_bytes(b"desktop image")
    mobile.write_bytes(b"mobile image")
    api_response = {"output": [{"content": [{"type": "output_text", "text": json.dumps(_analysis_result())}]}]}
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(json.dumps(api_response).encode("utf-8"))

    monkeypatch.setattr(analyzer_module, "urlopen", fake_urlopen)
    analyzer = OpenAIWebsiteAnalyzer(api_key="test-key", model="test-model")

    result = analyzer.analyze(
        url="https://firma.sk/",
        title="Firma",
        visible_text="Ponuka služieb",
        business={"name": "Firma s.r.o."},
        assessment={"lead_score": 62, "neglect_score": 50, "money_score": 24, "scoring_facts": {}},
        desktop_path=str(desktop),
        mobile_path=str(mobile),
    )

    payload = json.loads(captured["request"].data)
    content = payload["input"][0]["content"]
    images = [item for item in content if item["type"] == "input_image"]
    assert captured["timeout"] == 90
    assert payload["model"] == "test-model"
    assert len(images) == 2
    assert all(item["detail"] == "low" for item in images)
    assert result["visual_category"] == "DATED"
    assert result["positives"][0]["point"] == "Service list"
    assert result["model"] == "test-model"


def test_analysis_rejects_malformed_structured_result(monkeypatch, tmp_path):
    desktop = tmp_path / "desktop.png"
    mobile = tmp_path / "mobile.png"
    desktop.write_bytes(b"desktop image")
    mobile.write_bytes(b"mobile image")
    api_response = {"output": [{"content": [{"type": "output_text", "text": "[]"}]}]}
    monkeypatch.setattr(analyzer_module, "urlopen", lambda *args, **kwargs: FakeResponse(json.dumps(api_response).encode("utf-8")))
    analyzer = OpenAIWebsiteAnalyzer(api_key="test-key", model="test-model")

    with pytest.raises(OpenAIAnalysisError, match="invalid structured result"):
        analyzer.analyze("https://firma.sk/", "Firma", "Text", {}, {}, str(desktop), str(mobile))


def test_analysis_rejects_oversized_screenshot_before_request(monkeypatch, tmp_path):
    desktop = tmp_path / "desktop.png"
    mobile = tmp_path / "mobile.png"
    desktop.write_bytes(b"x" * 8_000_001)
    mobile.write_bytes(b"mobile image")
    monkeypatch.setattr(analyzer_module, "urlopen", lambda *args, **kwargs: pytest.fail("An oversized image must not be sent"))
    analyzer = OpenAIWebsiteAnalyzer(api_key="test-key", model="test-model")

    with pytest.raises(OpenAIAnalysisError, match="8 MB analysis limit"):
        analyzer.analyze("https://firma.sk/", "Firma", "Text", {}, {}, str(desktop), str(mobile))
