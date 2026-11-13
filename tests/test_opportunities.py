import json
from pathlib import Path

from website_hunter.opportunities.analysis import add_page_speed, build_assessment, score_facts
from website_hunter.opportunities.pagespeed import parse_pagespeed
from website_hunter.opportunities.mockup import build_redesign_prompt
from website_hunter.opportunities.signals import copyright_year, decode_cfemail, extract_facts, jquery_version


FIXTURES = Path(__file__).parent / "fixtures"
LISTS = {
    "consent_words": ["cookie", "consent", "onetrust", "usercentrics", "iubenda", "complianz", "didomi", "termly"],
    "booking_words": ["reservio.com", "reservanto.sk", "reservanto.com", "bookio.com", "previo.sk", "calendly.com", "medevio.sk"],
    "parked_words": ["doména je na predaj", "domena je na predaj", "domain is for sale", "this domain is parked", "buy this domain"],
}


def _facts(filename, headers=None, osm_emails=()):
    raw = (FIXTURES / filename).read_bytes()
    return extract_facts(raw, None, headers or {}, LISTS, "example.sk", "https://example.sk/", 2026,
                         osm_emails=osm_emails)


def test_detects_mobile_and_legacy_facts_from_old_page():
    facts = _facts("old_site.html", {"x-powered-by": "PHP/5.6.40"})

    assert facts["has_viewport"] is False
    assert facts["doctype"] == "legacy"
    assert facts["jquery_version"] == [1, 8, 3]
    assert facts["ua"] is True
    assert facts["php_version"] == [5, 6]
    assert facts["has_meta_description"] is False


def test_modern_page_produces_positive_activity_signals():
    facts = _facts("modern_site.html", osm_emails=["Recepcia@example.sk"])
    scores = score_facts(facts, {"https_ok": True, "kind": "own"}, current_year=2026)

    assert scores["neglect_score"] == 0
    assert scores["activity_score"] == 85
    assert scores["opportunity_score"] == 0
    assert {"google_ads", "sklik", "analytics", "booking", "social_links"} == set(scores["activity_signals"])


def test_tracker_requests_are_only_a_preconsent_issue_when_a_consent_tool_is_detected():
    modern = _facts("modern_site.html")
    measured = score_facts(modern, {"trackers_before_consent": ["google_analytics"]}, current_year=2026)
    old = _facts("old_site.html")
    unmeasured_consent = score_facts(old, {"trackers_before_consent": ["google_analytics"]}, current_year=2026)

    assert "trackers_before_consent" in measured["issues"]
    assert "trackers_before_consent" not in unmeasured_consent["issues"]


def test_old_page_score_explains_issues_and_mobile_redesign_value():
    assessment = build_assessment(
        (FIXTURES / "old_site.html").read_bytes(),
        {"content-type": "text/html; charset=windows-1250", "x-powered-by": "PHP/5.6.40"},
        "http://example.sk/",
        {"horizontal_overflow": True},
        {"name": "Example s.r.o.", "industry": "Dental clinic", "location": "Nitra"},
        [{"kind": "email", "value": "info@example.sk"}],
    )

    keys = {item["key"] for item in assessment["issues_detail"]}
    assert {"no_viewport", "mobile_overflow", "no_https", "no_meta_description"} <= keys
    assert assessment["opportunity_score"] > 0
    assert any(item["key"] == "identified_business" for item in assessment["strengths"])
    assert any(item["key"] == "public_email" for item in assessment["strengths"])
    assert all(item["recommendation"] and item["potential_benefit"] for item in assessment["issues_detail"])


def test_pagespeed_evidence_updates_scores_without_replacing_other_findings():
    facts = _facts("old_site.html")
    assessment = build_assessment((FIXTURES / "old_site.html").read_bytes(), {}, "https://example.sk/", {}, {})
    measured = add_page_speed(assessment, {"performance_score": 22, "lcp_ms": 4100, "cls": 0.2, "field_data": True, "status": "measured"})

    assert measured["page_speed"]["performance_score"] == 22
    assert {"psi_very_poor"} <= set(measured["issues"])
    assert {"field_data"} <= set(measured["activity_signals"])
    assert set(measured["scoring_facts"]) == set(assessment["scoring_facts"])
    assert facts["has_viewport"] is False


def test_parses_pagespeed_sample_and_empty_response():
    data = json.loads((FIXTURES / "psi_sample.json").read_text(encoding="utf-8"))

    result = parse_pagespeed(data)
    assert result["performance_score"] == 42
    assert result["lcp_ms"] == 5234
    assert result["cls"] == 0.213
    assert result["field_data"] is True
    assert parse_pagespeed({})["performance_score"] is None
    assert parse_pagespeed({})["field_data"] is False


def test_legacy_helpers_reject_unrelated_versions_and_decode_contacts():
    assert jquery_version("/wp-includes/js/jquery/jquery.min.js?ver=3.7.1") == [3, 7, 1]
    assert jquery_version("/js/jquery-ui-1.12.1.min.js") is None
    assert copyright_year("© 2019 Firma", 2026) == 2019
    assert decode_cfemail("5a33343c351a3f223b372a363f743920") == "info@example.cz"
    assert decode_cfemail("zz") is None


def test_redesign_prompt_uses_observed_evidence_without_inventing_business_claims():
    prompt = build_redesign_prompt({
        "title": "Clinic homepage",
        "canonical_domain": "example.sk",
        "business": {"name": "Klinika Nová", "industry": "Dental clinic", "services": ["check-up"]},
        "opportunity": {
            "issues_detail": [{"label": "No mobile viewport", "recommendation": "Add a responsive mobile layout."}],
            "strengths": [{"label": "Public email contact found", "evidence": "A contact address is listed."}],
        },
    })

    assert "Klinika Nová" in prompt
    assert "No mobile viewport" in prompt
    assert "Public email contact found" in prompt
    assert "Do not invent services, prices, customer reviews, awards" in prompt
