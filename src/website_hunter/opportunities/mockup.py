from __future__ import annotations

from pathlib import Path
from typing import Any

from website_hunter.config import settings


DESIGNER_ROOT = settings.app_root.parent.resolve()
MING_PYTHON = DESIGNER_ROOT / ".venv" / "Scripts" / "python.exe"
MING_SCRIPT = DESIGNER_ROOT / "ming" / "generate.py"


def designer_available() -> bool:
    return MING_PYTHON.is_file() and MING_SCRIPT.is_file()


def build_redesign_prompt(candidate: dict[str, Any]) -> str:
    business = candidate.get("business") or {}
    opportunity = candidate.get("opportunity") or {}
    name = str(business.get("name") or candidate.get("title") or candidate.get("canonical_domain") or "local business")
    issues = opportunity.get("issues_detail") or []
    strengths = opportunity.get("strengths") or []
    issue_lines = [f"- {item['label']}: {item['recommendation']}" for item in issues[:5] if item.get("label") and item.get("recommendation")]
    strength_lines = [f"- {item['label']}: {item['evidence']}" for item in strengths[:5] if item.get("label") and item.get("evidence")]
    industry = str(business.get("industry") or "")
    location = str(business.get("location") or "")
    context = ", ".join(value for value in (industry, location) if value)
    return "\n".join([
        f"Create a polished desktop homepage redesign concept for the Slovak business {name!r}.",
        "Use the supplied screenshot as the source reference. Preserve recognizable identity and any clearly legible existing logo. Keep all visible Slovak diacritics correct.",
        f"Known business context from the inspected sources: {context or 'not available'}. Do not invent services, prices, customer reviews, awards, addresses, contact details, or performance claims.",
        "Create a modern, responsive-ready web layout with clear navigation, readable hierarchy, strong contrast, and an obvious route to contact or book only where the source supports it.",
        "Use the current site's real content where it is legible. Do not add fake logos, fake testimonials, placeholder copy, browser chrome, a device frame, or text outside the webpage.",
        "Potential improvement evidence:",
        *(issue_lines or ["- No specific issue was recorded; improve clarity and mobile usability based on the supplied screenshot."]),
        "Existing positive signals:",
        *(strength_lines or ["- Preserve the parts of the current brand and content that are clear in the screenshot."]),
        "Render one complete homepage concept as a clean website screenshot, with an editorial but practical visual style suited to a real small business.",
    ])


def mockup_directory() -> Path:
    path = (settings.app_root / "data" / "mockups").resolve()
    if not path.is_relative_to(settings.app_root.resolve()):
        raise ValueError("Mockup output must stay inside the Website Opportunity Finder project.")
    return path
