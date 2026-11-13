from dataclasses import dataclass
from typing import Any


STRONG_LEGACY_SIGNALS = {
    "legacy_resolution_notice",
    "frontpage_reference",
    "dreamweaver_reference",
    "legacy_generator_metadata",
    "frameset_markup",
}
STRUCTURAL_LEGACY_SIGNALS = {
    "old_last_updated",
    "old_copyright",
    "table_heavy_markup",
    "presentational_font_tags",
    "obsolete_html_presentation",
}
WEAK_LEGACY_SIGNALS = {"internet_explorer_text", "image_based_navigation", "fixed_wide_layout"}
RANK_VINTAGE = 6
RANK_DATED = 5
RANK_STRONG_LEGACY = 4
RANK_STRUCTURAL_LEGACY = 3
RANK_MODERN_POOR = 2
RANK_LIMITED_MOBILE_EVIDENCE = 1
RANK_WEAK_LEGACY_HINTS = 1
RANK_UNCLEAR = 0
RANK_MODERN = -1
REJECTED_REVIEW_VERDICTS = {
    "OUTDATED BUT NOT USEFUL",
    "NOT OUTDATED",
    "NOT A BUSINESS / IRRELEVANT",
    "DEAD / UNREACHABLE",
}


@dataclass(frozen=True)
class AntiquityAssessment:
    label: str
    reasons: list[str]
    sort_key: tuple[int, int, int, int, int]


def assess_antiquity(
    classification: dict[str, Any] | None,
    legacy_signals: list[dict[str, str]] | None,
    render: dict[str, Any] | None,
) -> AntiquityAssessment:
    classification = classification or {}
    legacy_signals = legacy_signals or []
    render = render or {}
    category = str(classification.get("category") or "").upper()
    evidence = classification.get("evidence")
    visual_evidence = [str(item) for item in evidence if item] if isinstance(evidence, list) else []
    signal_names = {str(item.get("name") or "") for item in legacy_signals if isinstance(item, dict)}
    strong_count = len(signal_names & STRONG_LEGACY_SIGNALS)
    structural_count = len(signal_names & STRUCTURAL_LEGACY_SIGNALS)
    weak_count = len(signal_names & WEAK_LEGACY_SIGNALS)
    responsive_clue_count = int("missing_viewport_meta" in signal_names)
    horizontal_overflow = render.get("horizontal_overflow") is True
    rendered = bool(render.get("desktop_path") or render.get("mobile_path"))

    if category == "VINTAGE":
        band, label = RANK_VINTAGE, "Strong vintage appearance"
    elif category == "DATED":
        band, label = RANK_DATED, "Clearly dated appearance"
    elif category == "UNCERTAIN" and strong_count:
        band, label = RANK_STRONG_LEGACY, "Strong legacy-site clues"
    elif category == "UNCERTAIN" and structural_count:
        band, label = RANK_STRUCTURAL_LEGACY, "Possible legacy-site clues"
    elif category == "UNCERTAIN" and weak_count:
        band, label = RANK_WEAK_LEGACY_HINTS, "Weak HTML hints; visual age unconfirmed"
    elif category == "MODERN_POOR":
        band, label = RANK_MODERN_POOR, "Poor website experience; age unclear"
    elif category == "UNCERTAIN" and (responsive_clue_count or horizontal_overflow):
        band, label = RANK_LIMITED_MOBILE_EVIDENCE, "Limited mobile-fit evidence"
    elif category == "MODERN_OK":
        band, label = RANK_MODERN, "Visually modern"
    elif category == "UNCERTAIN":
        band, label = RANK_UNCLEAR, "Unclear appearance"
    else:
        band, label = RANK_UNCLEAR, "Not visually assessed" if not rendered else "Unclear appearance"

    reasons: list[str] = []
    if category in {"VINTAGE", "DATED", "MODERN_POOR", "MODERN_OK"}:
        reasons.append(f"Current screenshots classified as {category}.")
        reasons.extend(visual_evidence)
    elif category == "UNCERTAIN" and legacy_signals:
        reasons.append("HTML evidence only; the visual age has not been confirmed.")
    elif category == "UNCERTAIN":
        reasons.extend(visual_evidence)
        if not reasons:
            reasons.append("Visual evidence is incomplete or unavailable.")
    elif legacy_signals:
        reasons.append("HTML evidence only; the site has not been visually classified.")
    else:
        reasons.append("No visual classification or strong legacy HTML evidence is available yet.")

    for item in legacy_signals:
        if isinstance(item, dict):
            name = str(item.get("name") or "legacy signal").replace("_", " ")
            detail = str(item.get("evidence") or "").strip()
            reason = f"HTML signal: {name}"
            if detail:
                reason += f" — {detail}"
            if reason not in reasons:
                reasons.append(reason)
    if horizontal_overflow:
        reasons.append("Rendered mobile page has horizontal overflow; this is usability evidence, not proof of age.")

    sort_key = (
        band,
        strong_count,
        structural_count,
        responsive_clue_count,
        int(horizontal_overflow),
    )
    return AntiquityAssessment(label=label, reasons=reasons, sort_key=sort_key)


def prospect_priority(
    classification: dict[str, Any] | None,
    legacy_signals: list[dict[str, str]] | None,
    render: dict[str, Any] | None,
    screening: dict[str, Any] | None,
    review_verdict: str | None,
    candidate_status: str | None,
    status_code: int | None,
    final_url: str | None,
) -> int:
    classification = classification or {}
    render = render or {}
    screening = screening or {}
    category = str(classification.get("category") or "").upper()
    review = str(review_verdict or "").upper()

    if review == "GOOD PROSPECT":
        return 5
    if (
        screening.get("parked")
        or screening.get("replacement_notice")
        or screening.get("replacement_url")
        or screening.get("site_kind") == "forum"
    ):
        return -3
    if (
        candidate_status in {"failed", "blocked"}
        or status_code is None
        or status_code >= 400
        or screening.get("reachable") is False
        or not final_url
    ):
        return -5
    if review in REJECTED_REVIEW_VERDICTS:
        return -4
    if category == "VINTAGE":
        return 4
    if category == "DATED":
        return 3
    if category == "MODERN_POOR":
        return 2
    if category == "MODERN_OK":
        return -2

    assessment = assess_antiquity(classification, legacy_signals, render)
    if assessment.label == "Strong legacy-site clues":
        return 1
    if assessment.label in {"Possible legacy-site clues", "Weak HTML hints; visual age unconfirmed"}:
        return 0
    return -1
