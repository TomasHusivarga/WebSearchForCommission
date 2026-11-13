import base64
import json
import mimetypes
from pathlib import Path
from urllib.request import Request, urlopen

from website_hunter.config import settings
from website_hunter.contracts import WebsiteClassification
from website_hunter.prompts.classification_v1 import CLASSIFICATION_PROMPT


SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["VINTAGE", "DATED", "MODERN_POOR", "MODERN_OK", "UNCERTAIN"]},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "mobile_experience": {"type": "string"},
        "redesign_candidate": {"type": ["boolean", "null"]},
        "sufficient_evidence": {"type": "boolean"},
    },
    "required": ["category", "evidence", "mobile_experience", "redesign_candidate", "sufficient_evidence"],
    "additionalProperties": False,
}


class OpenAIResponsesClassifier:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.openai_model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def classify(self, desktop_path: str | None, mobile_path: str | None, observations: dict[str, object]) -> WebsiteClassification:
        if not self.api_key:
            return WebsiteClassification("UNCERTAIN", ["Visual AI is not configured; human review is required."], "Not assessed by AI.", None, False, "manual")
        if not desktop_path or not mobile_path:
            return WebsiteClassification("UNCERTAIN", ["Desktop and mobile screenshots are not both available."], "Render evidence is incomplete.", None, False, "openai_responses", "screenshots_missing")
        content = [{"type": "input_text", "text": f"{CLASSIFICATION_PROMPT}\n\nDeterministic observations: {json.dumps(observations, ensure_ascii=False)}"}]
        for path in (desktop_path, mobile_path):
            file_path = Path(path)
            media_type = mimetypes.guess_type(file_path.name)[0] or "image/png"
            encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{media_type};base64,{encoded}", "detail": "low"})
        payload = {
            "model": self.model,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": "website_classification", "strict": True, "schema": SCHEMA}},
            "max_output_tokens": 650,
        }
        request = Request(
            str(settings.defaults["openai_endpoint"]),
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=75) as response:
                result = json.loads(response.read(1_000_000))
            output_text = _extract_output_text(result)
            classification = json.loads(output_text)
            if classification.get("category") not in {"VINTAGE", "DATED", "MODERN_POOR", "MODERN_OK", "UNCERTAIN"}:
                raise ValueError("Classifier returned an unsupported category.")
            return WebsiteClassification(
                category=classification["category"],
                evidence=[str(item)[:300] for item in classification.get("evidence", [])[:8]],
                mobile_experience=str(classification.get("mobile_experience", ""))[:500],
                redesign_candidate=classification.get("redesign_candidate"),
                sufficient_evidence=bool(classification.get("sufficient_evidence")),
                provider=f"openai:{self.model}",
            )
        except Exception as error:
            return WebsiteClassification("UNCERTAIN", ["Visual classification did not complete; keep this result for human review."], "Not assessed.", None, False, "openai_responses", str(error)[:240])


def _extract_output_text(response: dict[str, object]) -> str:
    chunks = []
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                chunks.append(str(content.get("text", "")))
    if not chunks:
        raise ValueError("OpenAI response had no output_text item.")
    return "".join(chunks)
