import json
from pathlib import Path

from website_hunter.opportunities.analysis import ACTIVITY_WEIGHTS, NEGLECT_WEIGHTS, add_page_speed, build_assessment, score_facts
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


def test_viktor_rubric_weights_and_lead_formula():
    assert NEGLECT_WEIGHTS == {
        "no_viewport": 30, "mobile_overflow": 20, "no_https": 15, "diy_subdomain": 15,
        "old_doctype": 10, "nested_tables": 10, "legacy_tags": 10, "old_cms": 10,
        "old_copyright": 8, "ua_only": 8, "psi_poor": 8, "psi_very_poor": 12,
        "legacy_charset": 6, "old_jquery": 6, "old_php": 6, "no_consent_tool": 6,
        "trackers_before_consent": 6, "no_html5_sections": 5, "no_meta_description": 4,
        "no_title": 3, "no_og": 2, "no_structured_data": 2,
    }
    assert ACTIVITY_WEIGHTS == {
        "google_ads": 30, "sklik": 30, "field_data": 25, "fb_pixel": 15,
        "analytics": 10, "booking": 10, "social_links": 5,
    }

    facts = {
        "has_viewport": True,
        "doctype": "html5",
        "nested_tables": 0,
        "legacy_tags": {},
        "generators": [],
        "copyright_year": 2026,
        "ua": False,
        "ga4": False,
        "gtm": False,
        "has_consent_tool": True,
        "html5_sections": 1,
        "has_meta_description": True,
        "title": "Auto servis",
        "has_og": True,
        "has_structured_data": True,
        "google_ads": True,
    }
    facts["has_viewport"] = False
    scores = score_facts(facts, {"https_ok": True, "kind": "own"}, current_year=2026)

    assert scores["neglect_score"] == 30
    assert scores["activity_score"] == 30
    assert scores["opportunity_score"] == 24


def test_estranky_host_counts_as_a_hosted_builder_subdomain():
    assessment = build_assessment(
        b"<html><head><title>Remeselnik</title><meta name='viewport' content='width=device-width'></head></html>",
        {},
        "https://remeselnik.estranky.sk/",
        {},
    )

    assert assessment["scoring_site"]["kind"] == "diy_subdomain"
    assert assessment["issues"]["diy_subdomain"] == 15


def test_detects_flash_embeds_by_mime_type_as_legacy_markup():
    facts = extract_facts(
        b"<html><head><title>Old page</title></head><body><object type='application/x-shockwave-flash'></object></body></html>",
        None,
        {},
        LISTS,
        "example.sk",
        "https://example.sk/",
        2026,
    )
    scores = score_facts(facts, {"https_ok": True, "kind": "own"}, current_year=2026)

    assert facts["legacy_tags"]["flash"] == 1
    assert scores["issues"]["legacy_tags"] == 10


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
