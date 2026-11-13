from website_hunter.contracts import DiscoveredDomain
from website_hunter.storage.database import initialize, table_counts
from website_hunter.storage.repository import Repository


def test_candidates_deduplicate_normalized_host_and_keep_multiple_sources(tmp_path):
    path = tmp_path / "hunter.sqlite3"
    initialize(path)
    repository = Repository(path)
    run_id = repository.create_run("manual", "manual_url", 2)
    result = repository.enqueue_discoveries(
        [
            DiscoveredDomain("https://www.example.sk/kontakt", "manual"),
            DiscoveredDomain("http://example.sk", "manual"),
        ],
        run_id,
    )
    assert result == {"discovered": 2, "new_candidates": 1, "queued": 1, "duplicates": 1}
    candidate = repository.list_candidates()[0]
    assert candidate["canonical_domain"] == "example.sk"
    assert len(candidate["sources"]) == 1


def test_reviews_business_contacts_and_retry_state_survive_reopen(tmp_path):
    path = tmp_path / "hunter.sqlite3"
    initialize(path)
    repository = Repository(path)
    run_id = repository.create_run("manual", "manual_url", 1)
    repository.enqueue_discoveries([DiscoveredDomain("https://firma.sk", "manual")], run_id)
    job = repository.claim_job()
    assert job and job["attempts"] == 0
    repository.finish_job(job["job_id"], "failed", "temporary network problem", attempts_limit=2, retry_delay=0)
    retried = repository.claim_job()
    assert retried and retried["attempts"] == 1
    repository.update_candidate(
        retried["id"],
        business_json={"name": "Firma s.r.o.", "industry": "servis", "sources": ["https://firma.sk"]},
        contacts_json=[{"kind": "email", "value": "info@firma.sk", "classification": "general_business_contact", "source_url": "https://firma.sk/kontakt", "observed_at": "2026-10-15T00:00:00+00:00", "evidence": "Kontakt"}],
        last_processed_at="2026-10-15T00:00:00+00:00",
        processing_ms=1250,
        classification_json={"category": "DATED", "evidence": ["fixed width"]},
    )
    repository.finish_job(retried["job_id"], "complete")
    repository.review(retried["id"], "GOOD PROSPECT", "Rebuild candidate")

    reopened = Repository(path)
    candidate = reopened.get_candidate(retried["id"])
    reopened.enqueue_discoveries([DiscoveredDomain("https://pending.sk", "manual")], run_id)
    counts = table_counts(path)
    assert candidate["review"]["verdict"] == "GOOD PROSPECT"
    assert candidate["business"]["name"] == "Firma s.r.o."
    assert candidate["contacts"][0]["source_url"] == "https://firma.sk/kontakt"
    assert counts["businesses"] == 1
    assert counts["contacts"] == 1
    assert reopened.runs()[0]["status"] == "complete"
    metric = reopened.metrics()[0]
    assert metric["good_prospects"] == 1
    assert metric["vintage_or_dated"] == 1
    assert metric["successfully_inspected"] == 0
    assert metric["qualified_per_inspected_domain"] is None
    assert metric["failed_or_unreachable"] == 0


def test_metrics_include_discovery_sources_that_produce_no_candidates(tmp_path):
    path = tmp_path / "hunter.sqlite3"
    initialize(path)
    repository = Repository(path)
    run_id = repository.create_run("historical_source", "historical_domain_age", 3)
    repository.finish_run(run_id, 0, "complete")

    metrics = repository.metrics()

    assert len(metrics) == 1
    assert metrics[0]["source"] == "historical_source"
    assert metrics[0]["domains_discovered"] == 0
    assert metrics[0]["unique_candidates"] == 0
