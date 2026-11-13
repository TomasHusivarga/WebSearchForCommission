from __future__ import annotations

from datetime import date
import re
from typing import Any
from urllib.parse import urlsplit

from website_hunter.opportunities.signals import extract_facts


NEGLECT_WEIGHTS = {
    "no_viewport": 30,
    "mobile_overflow": 20,
    "no_https": 15,
    "diy_subdomain": 15,
    "old_doctype": 10,
    "nested_tables": 10,
    "legacy_tags": 10,
    "old_cms": 10,
    "old_copyright": 8,
    "ua_only": 8,
    "psi_poor": 8,
    "psi_very_poor": 12,
    "legacy_charset": 6,
    "old_jquery": 6,
    "old_php": 6,
    "no_consent_tool": 6,
    "trackers_before_consent": 6,
    "no_html5_sections": 5,
    "no_meta_description": 4,
    "no_title": 3,
    "no_og": 2,
    "no_structured_data": 2,
}

ACTIVITY_WEIGHTS = {
    "google_ads": 30,
    "sklik": 30,
    "field_data": 25,
    "fb_pixel": 15,
    "analytics": 10,
    "booking": 10,
    "social_links": 5,
}

ISSUE_DETAILS = {
    "no_viewport": ("No mobile viewport", "Add a responsive mobile layout.", "Visitors can read and use the site more easily on phones."),
    "mobile_overflow": ("Mobile page overflows horizontally", "Rework the affected mobile layout and content widths.", "Visitors can reach content without sideways scrolling."),
    "no_https": ("HTTPS was not observed", "Configure HTTPS and redirect the HTTP version.", "A secure connection can improve visitor confidence and browser compatibility."),
    "diy_subdomain": ("Website uses a hosted subdomain", "Consider a branded domain connected to the existing site.", "A branded address is easier to remember and use in marketing."),
    "old_doctype": ("Legacy or missing HTML doctype", "Update the document structure to current HTML standards.", "A current structure is easier to maintain across browsers."),
    "nested_tables": ("Nested tables used for layout", "Replace layout tables with responsive page structure.", "Content can adapt more reliably to small screens and assistive technology."),
    "legacy_tags": ("Legacy presentation markup detected", "Replace obsolete markup with maintainable CSS and semantic HTML.", "The layout becomes easier to update and adapt."),
    "old_cms": ("Old CMS version declared", "Verify the installed CMS version and plan a supported upgrade.", "Maintenance and security support can be reviewed against the actual installation."),
    "old_copyright": ("Copyright year may be stale", "Check whether the footer and business details are current.", "Current details help visitors trust that the business is active."),
    "ua_only": ("Legacy Universal Analytics tag detected", "Review analytics and replace retired tracking where needed.", "Current measurement can make campaign results more useful."),
    "psi_very_poor": ("PageSpeed mobile performance is very low", "Review the largest render-blocking assets and page payload.", "A faster page can reduce waiting on mobile connections."),
    "psi_poor": ("PageSpeed mobile performance needs work", "Review image weight, scripts, and render-blocking resources.", "A faster page can make key actions easier to reach."),
    "legacy_charset": ("Legacy character encoding declared", "Migrate the page to UTF-8 and verify Slovak text rendering.", "Diacritics and text handling become more reliable."),
    "old_jquery": ("Old jQuery version referenced", "Check plugin compatibility and update or remove the old library.", "Fewer outdated dependencies can simplify maintenance."),
    "old_php": ("Older PHP version reported in response headers", "Confirm the hosting runtime and plan a supported PHP upgrade.", "A current runtime can improve maintenance and security support."),
    "no_consent_tool": ("Tracking tags found without an identified consent tool", "Review the tracking and consent setup with the site owner.", "A deliberate setup can make analytics behavior clearer to visitors."),
    "trackers_before_consent": ("Tracking requests observed before consent", "Review when analytics and advertising tags load.", "Visitors' choices can be applied consistently to tracking."),
    "no_html5_sections": ("No semantic page sections detected", "Use clear landmarks for the page header, navigation, main content, and footer.", "People and assistive tools can navigate the page structure more easily."),
    "no_meta_description": ("Search description missing", "Add a concise page specific description for search results.", "Searchers get a clearer summary before they visit."),
    "no_title": ("Page title is missing or very short", "Add a distinct title that describes the business and page.", "Search results and browser tabs can identify the page more clearly."),
    "no_og": ("Social sharing title missing", "Add Open Graph title metadata for shared links.", "Shared links can show a more recognizable preview."),
    "no_structured_data": ("Structured business data not detected", "Review whether relevant schema.org data fits the business.", "Search engines may interpret business details more consistently."),
}

ACTIVITY_LABELS = {
    "google_ads": "Google Ads tag detected",
    "sklik": "Sklik advertising tag detected",
    "field_data": "PageSpeed field data is available",
    "fb_pixel": "Meta Pixel tag detected",
    "analytics": "Analytics or Tag Manager detected",
    "booking": "Online booking link detected",
    "social_links": "Social profile links found",
}


def score_facts(facts: dict[str, Any], site: dict[str, Any], current_year: int | None = None) -> dict[str, Any]:
    year = current_year or date.today().year
    th = {"wordpress_min_major": 6, "drupal_min_major": 9, "joomla_min_major": 4, "jquery_min_major": 3, "php_min_major": 8,
          "old_copyright_age_years": 3, "psi_perf_poor": 50, "psi_perf_very_poor": 25}
    neglect: dict[str, int] = {}
    activity: dict[str, int] = {}

    def add(target: dict[str, int], weights: dict[str, int], key: str, condition: bool) -> None:
        if condition and weights.get(key, 0):
            target[key] = weights[key]

    tracker = any(facts.get(key) for key in ("ga4", "ua", "gtm", "google_ads", "sklik", "fb_pixel"))
    jquery = facts.get("jquery_version")
    php = facts.get("php_version")
    copyright = facts.get("copyright_year")
    performance = site.get("psi_perf")
    generators = facts.get("generators") or []
    old_cms = False
    cms_versions = (("wordpress", th["wordpress_min_major"]), ("drupal", th["drupal_min_major"]), ("joomla", th["joomla_min_major"]))
    for generator in generators:
        normalized = str(generator).lower()
        version = _major_version(generator)
        if version is not None and any(name in normalized and version < minimum for name, minimum in cms_versions):
            old_cms = True
            break

    add(neglect, NEGLECT_WEIGHTS, "no_viewport", not facts.get("has_viewport", True))
    add(neglect, NEGLECT_WEIGHTS, "mobile_overflow", site.get("mobile_overflow") is True)
    add(neglect, NEGLECT_WEIGHTS, "no_https", site.get("https_ok") is False)
    add(neglect, NEGLECT_WEIGHTS, "diy_subdomain", site.get("kind") == "diy_subdomain")
    add(neglect, NEGLECT_WEIGHTS, "old_doctype", facts.get("doctype") != "html5")
    add(neglect, NEGLECT_WEIGHTS, "nested_tables", int(facts.get("nested_tables") or 0) >= 1)
    add(neglect, NEGLECT_WEIGHTS, "legacy_tags", bool(facts.get("legacy_tags")))
    add(neglect, NEGLECT_WEIGHTS, "old_cms", old_cms)
    add(neglect, NEGLECT_WEIGHTS, "old_copyright", copyright is not None and copyright <= year - th["old_copyright_age_years"])
    add(neglect, NEGLECT_WEIGHTS, "ua_only", facts.get("ua") is True and not facts.get("ga4") and not facts.get("gtm"))
    if performance is not None:
        add(neglect, NEGLECT_WEIGHTS, "psi_very_poor", performance < th["psi_perf_very_poor"])
        if performance >= th["psi_perf_very_poor"]:
            add(neglect, NEGLECT_WEIGHTS, "psi_poor", performance < th["psi_perf_poor"])
    add(neglect, NEGLECT_WEIGHTS, "legacy_charset", str(facts.get("declared_charset") or "").lower() in {"windows-1250", "cp1250", "iso-8859-2", "iso-8859-1", "windows-1252"})
    add(neglect, NEGLECT_WEIGHTS, "old_jquery", bool(jquery) and int(jquery[0]) < th["jquery_min_major"])
    add(neglect, NEGLECT_WEIGHTS, "old_php", bool(php) and int(php[0]) < th["php_min_major"])
    add(neglect, NEGLECT_WEIGHTS, "no_consent_tool", tracker and not facts.get("has_consent_tool"))
    add(neglect, NEGLECT_WEIGHTS, "trackers_before_consent", bool(facts.get("has_consent_tool") and site.get("trackers_before_consent")))
    add(neglect, NEGLECT_WEIGHTS, "no_html5_sections", facts.get("html5_sections") == 0)
    add(neglect, NEGLECT_WEIGHTS, "no_meta_description", not facts.get("has_meta_description"))
    add(neglect, NEGLECT_WEIGHTS, "no_title", len(str(facts.get("title") or "")) < 5)
    add(neglect, NEGLECT_WEIGHTS, "no_og", not facts.get("has_og"))
    add(neglect, NEGLECT_WEIGHTS, "no_structured_data", not facts.get("has_structured_data"))
    add(activity, ACTIVITY_WEIGHTS, "google_ads", bool(facts.get("google_ads")))
    add(activity, ACTIVITY_WEIGHTS, "sklik", bool(facts.get("sklik")))
    add(activity, ACTIVITY_WEIGHTS, "field_data", site.get("psi_has_field_data") is True)
    add(activity, ACTIVITY_WEIGHTS, "fb_pixel", bool(facts.get("fb_pixel")))
    add(activity, ACTIVITY_WEIGHTS, "analytics", bool(facts.get("ga4") or facts.get("gtm")))
    add(activity, ACTIVITY_WEIGHTS, "booking", bool(facts.get("booking")))
    add(activity, ACTIVITY_WEIGHTS, "social_links", bool(facts.get("social_links")))
    neglect_score = min(100, sum(neglect.values()))
    activity_score = min(100, sum(activity.values()))
    opportunity_score = min(100, (neglect_score * (50 + activity_score) + 50) // 100)
    return {"issues": neglect, "activity_signals": activity, "neglect_score": neglect_score,
            "activity_score": activity_score, "opportunity_score": opportunity_score}


def build_assessment(raw_html: bytes | str, headers: dict[str, str], final_url: str, render: dict[str, Any],
                     business: dict[str, Any] | None = None, contacts: list[dict[str, Any]] | None = None,
                     business_hint: dict[str, Any] | None = None,
                     page_speed: dict[str, Any] | None = None) -> dict[str, Any]:
    parts = urlsplit(final_url)
    host = parts.hostname or ""
    raw = raw_html if isinstance(raw_html, bytes) else raw_html.encode("utf-8", errors="replace")
    charset_match = re.search(r"charset=([\w-]+)", headers.get("content-type", ""), re.I)
    facts = extract_facts(
        raw, charset_match.group(1) if charset_match else None, headers, _SIGNAL_LISTS, host, final_url,
        date.today().year, osm_emails=[str((business_hint or {}).get("email") or "")] if (business_hint or {}).get("email") else (),
    )
    safe_facts = {key: value for key, value in facts.items() if key not in {"emails", "ico", "text_len"}}
    site = {
        "https_ok": parts.scheme.lower() == "https",
        "kind": _site_kind(host),
        "mobile_overflow": render.get("horizontal_overflow"),
        "trackers_before_consent": (render.get("trackers_before_consent") or []) if facts.get("has_consent_tool") else [],
        "psi_perf": (page_speed or {}).get("performance_score"),
        "psi_has_field_data": bool((page_speed or {}).get("field_data")),
    }
    if not facts.get("has_consent_tool"):
        site["trackers_before_consent"] = []
    scored = score_facts(safe_facts, site)
    issues = []
    for key, weight in sorted(scored["issues"].items(), key=lambda item: (-item[1], item[0])):
        label, recommendation, potential = ISSUE_DETAILS[key]
        issues.append({"key": key, "label": label, "weight": weight, "evidence": _evidence(key, facts, site),
                       "recommendation": recommendation, "potential_benefit": potential})
    strengths = _strengths(scored["activity_signals"], facts, business or {}, contacts or [])
    name = str((business or {}).get("name") or "").strip()
    if name:
        strengths.insert(0, {"key": "identified_business", "label": "Business identified", "evidence": name})
    return {
        **scored,
        "issues_detail": issues,
        "strengths": strengths,
        "page_speed": page_speed or {"status": "not_measured"},
        "scoring_facts": safe_facts,
        "scoring_site": site,
        "method": "Rule based screening of the initial page and available browser observations. Scores are prioritization hints, not quality certifications or predicted business results. Advertising tags do not confirm current ad spend.",
    }


def add_page_speed(assessment: dict[str, Any], page_speed: dict[str, Any]) -> dict[str, Any]:
    facts = assessment.get("scoring_facts") or {}
    site = dict(assessment.get("scoring_site") or {})
    site["psi_perf"] = page_speed.get("performance_score")
    site["psi_has_field_data"] = bool(page_speed.get("field_data"))
    scored = score_facts(facts, site)
    issues = []
    for key, weight in sorted(scored["issues"].items(), key=lambda item: (-item[1], item[0])):
        label, recommendation, potential = ISSUE_DETAILS[key]
        issues.append({"key": key, "label": label, "weight": weight, "evidence": _evidence(key, facts, site),
                       "recommendation": recommendation, "potential_benefit": potential})
    strengths = [item for item in assessment.get("strengths", []) if item.get("key") != "field_data"]
    if scored["activity_signals"].get("field_data"):
        strengths.append({"key": "field_data", "label": ACTIVITY_LABELS["field_data"],
                          "evidence": _activity_evidence("field_data", facts)})
    return {**assessment, **scored, "issues_detail": issues, "strengths": strengths,
            "page_speed": page_speed, "scoring_site": site}


def _major_version(value: Any) -> int | None:
    match = re.search(r"(?:wordpress|drupal|joomla!?)[\s/]+(\d+)", str(value), re.I)
    return int(match.group(1)) if match else None


def _site_kind(host: str) -> str:
    known_suffixes = (".webnode.sk", ".webnode.page", ".webnode.com", ".wixsite.com", ".wordpress.com", ".weebly.com", ".wix.com")
    return "diy_subdomain" if host.lower().endswith(known_suffixes) else "own"


def _evidence(key: str, facts: dict[str, Any], site: dict[str, Any]) -> str:
    if key == "no_viewport": return "No viewport meta tag in the fetched HTML."
    if key == "mobile_overflow": return "The rendered mobile page exceeded the viewport width."
    if key == "no_https": return "The final page URL uses HTTP."
    if key == "diy_subdomain": return "The final website host matches a known hosted website suffix."
    if key == "old_doctype": return f"Declared doctype: {facts.get('doctype') or 'missing'}."
    if key == "nested_tables": return f"{facts.get('nested_tables', 0)} nested layout table(s) found."
    if key == "legacy_tags": return ", ".join(f"{name} ({count})" for name, count in facts.get("legacy_tags", {}).items())
    if key == "old_cms": return "; ".join(facts.get("generators", []))
    if key == "old_copyright": return f"Latest detected copyright year: {facts.get('copyright_year')}. This may be stale."
    if key == "ua_only": return "Universal Analytics ID found without GA4 or Tag Manager."
    if key.startswith("psi_"): return f"PageSpeed mobile performance score: {site.get('psi_perf')}/100."
    if key == "legacy_charset": return f"Declared character encoding: {facts.get('declared_charset')}."
    if key == "old_jquery": return f"Referenced jQuery version: {'.'.join(map(str, facts.get('jquery_version') or []))}."
    if key == "old_php": return f"Response header reports PHP {'.'.join(map(str, facts.get('php_version') or []))}."
    if key == "no_consent_tool": return "At least one analytics or advertising tag was found; no recognized consent tool was detected in the fetched page."
    if key == "trackers_before_consent": return "Detected request types before any consent interaction: " + ", ".join(site.get("trackers_before_consent") or [])
    if key == "no_html5_sections": return "No header, navigation, main, section, article, or footer elements found."
    if key == "no_meta_description": return "No non-empty meta description found."
    if key == "no_title": return "The HTML title is shorter than five characters or missing."
    if key == "no_og": return "No og:title meta tag found."
    if key == "no_structured_data": return "No JSON-LD or microdata found."
    return "Signal detected in the fetched page."


def _strengths(signals: dict[str, int], facts: dict[str, Any], business: dict[str, Any], contacts: list[dict[str, Any]]) -> list[dict[str, str]]:
    strengths = [{"key": key, "label": ACTIVITY_LABELS[key], "evidence": _activity_evidence(key, facts)} for key in signals]
    if business.get("industry"):
        strengths.append({"key": "business_category", "label": "Business category available", "evidence": str(business["industry"])})
    if business.get("location"):
        strengths.append({"key": "business_location", "label": "Business location available", "evidence": str(business["location"])})
    if business.get("services"):
        strengths.append({"key": "services", "label": "Services described", "evidence": ", ".join(map(str, business["services"][:5]))})
    if any(contact.get("kind") == "email" for contact in contacts) or facts.get("emails"):
        strengths.append({"key": "public_email", "label": "Public email contact found", "evidence": "A contact address is listed on the site or source listing."})
    if any(contact.get("kind") == "phone" for contact in contacts):
        strengths.append({"key": "public_phone", "label": "Public phone contact found", "evidence": "A phone contact is listed on the site or source listing."})
    return strengths


def _activity_evidence(key: str, facts: dict[str, Any]) -> str:
    if key == "google_ads": return "Google Ads tag or conversion marker found in the fetched HTML."
    if key == "sklik": return "Sklik tag or conversion marker found in the fetched HTML."
    if key == "field_data": return "PageSpeed returned field data for this site or its origin."
    if key == "fb_pixel": return "Meta Pixel marker found in the fetched HTML."
    if key == "analytics": return "GA4 or Google Tag Manager marker found in the fetched HTML."
    if key == "booking": return "Booking provider link or marker found: " + ", ".join(facts.get("booking") or [])
    return "Links to social profiles were found: " + ", ".join(facts.get("social_links") or [])


_SIGNAL_LISTS = {
    "consent_words": ["cookie", "consent", "onetrust", "usercentrics", "iubenda", "complianz", "didomi", "termly", "cookies", "súhlas", "suhlas"],
    "booking_words": ["reservio.com", "reservanto.sk", "reservanto.com", "bookio.com", "previo.sk", "calendly.com", "medevio.sk"],
    "parked_words": ["doména je na predaj", "domena je na predaj", "domain is for sale", "this domain is parked", "buy this domain"],
}
