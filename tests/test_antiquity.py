from website_hunter.screening.antiquity import assess_antiquity


def test_visual_classification_leads_the_antiquity_band():
    vintage = assess_antiquity(
        {"category": "VINTAGE", "evidence": ["Fixed-width page and image navigation"]},
        [],
        {},
    )
    dated = assess_antiquity({"category": "DATED", "evidence": ["Tiny typography"]}, [], {})
    modern = assess_antiquity({"category": "MODERN_OK", "evidence": []}, [], {})

    assert vintage.label == "Strong vintage appearance"
    assert vintage.sort_key > dated.sort_key > modern.sort_key
    assert "Fixed-width page and image navigation" in vintage.reasons


def test_uncertain_sites_rank_by_observed_legacy_evidence_not_business_data():
    strong_legacy = assess_antiquity(
        {"category": "UNCERTAIN", "evidence": []},
        [
            {"name": "legacy_resolution_notice", "evidence": "Optimized for 800x600"},
            {"name": "frontpage_reference", "evidence": "Microsoft FrontPage"},
        ],
        {"horizontal_overflow": True},
    )
    structural_only = assess_antiquity(
        {"category": "UNCERTAIN", "evidence": []},
        [{"name": "missing_viewport_meta", "evidence": "No viewport meta tag found"}],
        {},
    )
    no_signals = assess_antiquity({"category": "UNCERTAIN", "evidence": []}, [], {})

    assert strong_legacy.label == "Strong legacy-site clues"
    assert strong_legacy.sort_key > structural_only.sort_key > no_signals.sort_key
    assert any("HTML evidence only" in reason for reason in strong_legacy.reasons)
    assert any("horizontal overflow" in reason for reason in strong_legacy.reasons)


def test_modern_visual_classification_is_not_overridden_by_legacy_markup():
    modern = assess_antiquity(
        {"category": "MODERN_OK", "evidence": ["Responsive contemporary layout"]},
        [{"name": "missing_viewport_meta", "evidence": "No viewport meta tag found"}],
        {},
    )
    assert modern.label == "Visually modern"
    assert modern.sort_key < assess_antiquity({"category": "UNCERTAIN"}, [], {}).sort_key


def test_candidates_sort_by_antiquity_before_the_api_limit_and_recent_is_available(tmp_path):
    from website_hunter.contracts import DiscoveredDomain
    from website_hunter.storage.database import connect, initialize
    from website_hunter.storage.repository import Repository

    path = tmp_path / "hunter.sqlite3"
    initialize(path)
    repository = Repository(path)
    run_id = repository.create_run("manual", "manual_url", 3)
    repository.enqueue_discoveries(
        [
            DiscoveredDomain("https://modern.sk", "manual"),
            DiscoveredDomain("https://vintage.sk", "manual"),
            DiscoveredDomain("https://signals.sk", "manual"),
        ],
        run_id,
    )
    candidates = {candidate["canonical_domain"]: candidate for candidate in repository.list_candidates(limit=3, sort="recent")}
    repository.update_candidate(
        candidates["modern.sk"]["id"],
        classification_json={"category": "MODERN_OK", "evidence": ["Modern layout"]},
    )
    repository.update_candidate(
        candidates["vintage.sk"]["id"],
        classification_json={"category": "VINTAGE", "evidence": ["Image-based navigation"]},
    )
    repository.update_candidate(
        candidates["signals.sk"]["id"],
        classification_json={"category": "UNCERTAIN", "evidence": []},
        legacy_signals_json=[{"name": "frontpage_reference", "evidence": "FrontPage"}],
    )
    with connect(path) as connection:
        connection.execute("UPDATE candidates SET last_seen='2026-10-15T00:00:03+00:00' WHERE canonical_domain='signals.sk'")
        connection.execute("UPDATE candidates SET last_seen='2026-10-15T00:00:02+00:00' WHERE canonical_domain='vintage.sk'")
        connection.execute("UPDATE candidates SET last_seen='2026-10-15T00:00:01+00:00' WHERE canonical_domain='modern.sk'")

    top_two = repository.list_candidates(limit=2)
    newest_first = repository.list_candidates(limit=3, sort="recent")

    assert [candidate["canonical_domain"] for candidate in top_two] == ["vintage.sk", "signals.sk"]
    assert top_two[0]["antiquity"]["label"] == "Strong vintage appearance"
    assert [candidate["canonical_domain"] for candidate in newest_first] == ["signals.sk", "vintage.sk", "modern.sk"]


def test_prospect_sort_uses_human_reviews_and_pushes_uncertain_failures_down(tmp_path):
    from website_hunter.contracts import DiscoveredDomain
    from website_hunter.storage.database import initialize
    from website_hunter.storage.repository import Repository

    path = tmp_path / "hunter.sqlite3"
    initialize(path)
    repository = Repository(path)
    run_id = repository.create_run("manual", "manual_url", 12)
    repository.enqueue_discoveries(
        [DiscoveredDomain(f"https://{domain}", "manual") for domain in (
            "modern.sk", "confirmed-modern.sk", "raw-signals.sk", "dated.sk", "good.sk", "failed.sk",
            "parked.sk", "replacement.sk", "forum.sk", "not-useful.sk", "irrelevant.sk", "dead-reviewed.sk",
        )],
        run_id,
    )
    candidates = {candidate["canonical_domain"]: candidate for candidate in repository.list_candidates(limit=12, sort="recent")}

    for domain, category, status_code, final_url, render, screening, signals in (
        ("modern.sk", "MODERN_OK", 200, "https://modern.sk/", {"desktop_path": "desktop.png", "mobile_path": "mobile.png"}, {"reachable": True}, []),
        ("confirmed-modern.sk", "MODERN_OK", 200, "https://confirmed-modern.sk/", {"desktop_path": "desktop.png", "mobile_path": "mobile.png"}, {"reachable": True}, []),
        ("raw-signals.sk", "UNCERTAIN", 200, "https://raw-signals.sk/", {"desktop_path": "desktop.png", "mobile_path": "mobile.png"}, {"reachable": True}, [{"name": "internet_explorer_text", "evidence": "Internet Explorer"}, {"name": "fixed_wide_layout", "evidence": "width:1100px"}]),
        ("dated.sk", "DATED", 200, "https://dated.sk/", {"desktop_path": "desktop.png", "mobile_path": "mobile.png"}, {"reachable": True}, []),
        ("good.sk", "UNCERTAIN", 200, "https://good.sk/", {"desktop_path": "desktop.png", "mobile_path": "mobile.png"}, {"reachable": True}, []),
        ("failed.sk", "UNCERTAIN", None, None, {}, {"reachable": False, "failure_reason": "safe fetch failed"}, []),
        ("parked.sk", "UNCERTAIN", 200, "https://parked.sk/", {}, {"reachable": True, "parked": True}, []),
        ("replacement.sk", "UNCERTAIN", 200, "https://replacement.sk/", {}, {"reachable": True, "replacement_notice": True, "replacement_url": "https://new-site.sk/"}, []),
        ("forum.sk", "UNCERTAIN", 200, "https://forum.sk/", {}, {"reachable": True, "site_kind": "forum"}, []),
        ("not-useful.sk", "UNCERTAIN", 200, "https://not-useful.sk/", {}, {"reachable": True}, []),
        ("irrelevant.sk", "UNCERTAIN", 200, "https://irrelevant.sk/", {}, {"reachable": True}, []),
        ("dead-reviewed.sk", "UNCERTAIN", 200, "https://dead-reviewed.sk/", {}, {"reachable": True}, []),
    ):
        repository.update_candidate(
            candidates[domain]["id"],
            status="complete",
            stage="complete",
            status_code=status_code,
            final_url=final_url,
            classification_json={"category": category, "evidence": []},
            render_json=render,
            screening_json=screening,
            legacy_signals_json=signals,
        )

    repository.review(candidates["modern.sk"]["id"], "NOT OUTDATED", "Visually modern")
    repository.review(candidates["not-useful.sk"]["id"], "OUTDATED BUT NOT USEFUL", "Replacement site is current")
    repository.review(candidates["irrelevant.sk"]["id"], "NOT A BUSINESS / IRRELEVANT", "Community site")
    repository.review(candidates["dead-reviewed.sk"]["id"], "DEAD / UNREACHABLE", "Domain has no current website")
    repository.review(candidates["good.sk"]["id"], "GOOD PROSPECT", "Manual fit confirmation")

    ordered = repository.list_candidates(limit=12, sort="prospects")
    all_candidates = repository.list_candidates(limit=12, sort="recent")

    assert [candidate["canonical_domain"] for candidate in ordered] == [
        "good.sk",
        "dated.sk",
        "raw-signals.sk",
    ]
    assert {candidate["canonical_domain"] for candidate in all_candidates} == {
        "modern.sk",
        "confirmed-modern.sk",
        "raw-signals.sk",
        "dated.sk",
        "good.sk",
        "failed.sk",
        "parked.sk",
        "replacement.sk",
        "forum.sk",
        "not-useful.sk",
        "irrelevant.sk",
        "dead-reviewed.sk",
    }
