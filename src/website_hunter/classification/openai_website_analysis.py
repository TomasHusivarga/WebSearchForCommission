import base64
import json
import mimetypes
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from website_hunter.config import settings
from website_hunter.classification.openai_responses import _extract_output_text
from website_hunter.prompts.website_analysis_v1 import WEBSITE_ANALYSIS_PROMPT


SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "visual_category": {"type": "string", "enum": ["VINTAGE", "DATED", "MODERN_POOR", "MODERN_OK", "UNCERTAIN"]},
        "visual_evidence": {"type": "array", "items": {"type": "string"}},
        "mobile_assessment": {"type": "string"},
        "redesign_candidate": {"type": ["boolean", "null"]},
        "sufficient_evidence": {"type": "boolean"},
        "positives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"point": {"type": "string"}, "evidence": {"type": "string"}},
                "required": ["point", "evidence"],
                "additionalProperties": False,
            },
        },
        "improvements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "evidence": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "possible_benefit": {"type": "string"},
                },
                "required": ["issue", "evidence", "recommendation", "possible_benefit"],
                "additionalProperties": False,
            },
        },
        "redesign_direction": {"type": "string"},
    },
    "required": [
        "summary", "visual_category", "visual_evidence", "mobile_assessment", "redesign_candidate",
        "sufficient_evidence", "positives", "improvements", "redesign_direction",
    ],
    "additionalProperties": False,
}


class OpenAIAnalysisError(Exception):
    pass


class OpenAIWebsiteAnalyzer:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.openai_model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def analyze(
        self,
        url: str,
        title: str,
        visible_text: str,
        business: dict[str, object],
        assessment: dict[str, object],
        desktop_path: str | None,
        mobile_path: str | None,
    ) -> dict[str, object]:
        if not self.api_key:
            raise OpenAIAnalysisError("Set OPENAI_API_KEY in .env to analyze selected websites with AI.")
        if not desktop_path or not mobile_path:
            raise OpenAIAnalysisError("Desktop and mobile screenshots are required for detailed analysis.")

        facts = assessment.get("scoring_facts") or {}
        signals = {
            "lead_score": assessment.get("lead_score", assessment.get("opportunity_score")),
            "neglect_score": assessment.get("neglect_score"),
            "money_score": assessment.get("money_score", assessment.get("activity_score")),
            "observed_issues": assessment.get("issues_detail", []),
            "positive_signals": assessment.get("strengths", []),
            "page_speed": assessment.get("page_speed", {}),
        }
        context = {
            "website": url,
            "page_title": title,
            "business_context": {key: business.get(key) for key in ("name", "industry", "location", "services") if business.get(key)},
            "score_evidence": signals,
            "homepage_text": visible_text[:10000],
            "deterministic_html_facts": facts,
        }
        content = [{"type": "input_text", "text": f"{WEBSITE_ANALYSIS_PROMPT}\n\nWebsite evidence:\n{json.dumps(context, ensure_ascii=False)}"}]
        for path in (desktop_path, mobile_path):
            image_path = Path(path)
            if not image_path.is_file():
                raise OpenAIAnalysisError("A selected website screenshot is no longer available.")
            if image_path.stat().st_size > 8_000_000:
                raise OpenAIAnalysisError("A selected website screenshot exceeds the 8 MB analysis limit.")
            media_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
            try:
                encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            except OSError as error:
                raise OpenAIAnalysisError("A selected website screenshot could not be read.") from error
            content.append({"type": "input_image", "image_url": f"data:{media_type};base64,{encoded}", "detail": "low"})

        payload = {
            "model": self.model,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": "website_analysis", "strict": True, "schema": SCHEMA}},
            "max_output_tokens": 1500,
        }
        request = Request(
            str(settings.defaults["openai_endpoint"]),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=90) as response:
                result = json.loads(response.read(1_000_000))
            analysis = json.loads(_extract_output_text(result))
        except HTTPError as error:
            raise OpenAIAnalysisError(f"AI analysis returned HTTP {error.code}.") from error
        except (URLError, TimeoutError) as error:
            raise OpenAIAnalysisError("AI analysis could not reach the configured endpoint.") from error
        except (ValueError, TypeError, OSError) as error:
            raise OpenAIAnalysisError("AI analysis returned an unreadable result.") from error

        if not isinstance(analysis, dict):
            raise OpenAIAnalysisError("AI analysis returned an invalid structured result.")
        if analysis.get("visual_category") not in {"VINTAGE", "DATED", "MODERN_POOR", "MODERN_OK", "UNCERTAIN"}:
            raise OpenAIAnalysisError("AI analysis returned an unsupported visual category.")
        if not all(isinstance(analysis.get(key), list) for key in ("visual_evidence", "positives", "improvements")):
            raise OpenAIAnalysisError("AI analysis returned incomplete structured evidence.")
        analysis["summary"] = str(analysis.get("summary", ""))[:1200]
        analysis["visual_evidence"] = [str(value)[:300] for value in analysis.get("visual_evidence", [])[:8]]
        analysis["mobile_assessment"] = str(analysis.get("mobile_assessment", ""))[:700]
        analysis["positives"] = [
            {"point": str(item.get("point", ""))[:240], "evidence": str(item.get("evidence", ""))[:400]}
            for item in analysis.get("positives", [])[:8]
            if isinstance(item, dict)
        ]
        analysis["improvements"] = [
            {
                "issue": str(item.get("issue", ""))[:240],
                "evidence": str(item.get("evidence", ""))[:400],
                "recommendation": str(item.get("recommendation", ""))[:500],
                "possible_benefit": str(item.get("possible_benefit", ""))[:400],
            }
            for item in analysis.get("improvements", [])[:8]
            if isinstance(item, dict)
        ]
        analysis["redesign_direction"] = str(analysis.get("redesign_direction", ""))[:1200]
        analysis["model"] = self.model
        return analysis
