import sqlite3

from website_hunter.storage.database import SCHEMA, connect, initialize
from website_hunter.storage.repository import Repository


def test_database_upgrade_adds_opportunity_and_outreach_fields(tmp_path):
    path = tmp_path / "hunter.sqlite3"
    legacy_schema = SCHEMA.replace("    opportunity_json TEXT NOT NULL DEFAULT '{}',\n", "").replace(
        "    outreach_json TEXT NOT NULL DEFAULT '{}',\n", ""
    ).replace("    mockup_json TEXT NOT NULL DEFAULT '{}',\n", "")
    with sqlite3.connect(path) as connection:
        connection.executescript(legacy_schema)

    initialize(path)
    initialize(path)

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(candidates)")}
    assert {"opportunity_json", "outreach_json", "mockup_json"} <= columns


def test_opportunity_and_outreach_records_round_trip(tmp_path):
    path = tmp_path / "hunter.sqlite3"
    initialize(path)
    with connect(path) as connection:
        connection.execute(
            "INSERT INTO candidates(canonical_domain,input_url,created_at,first_seen,last_seen) VALUES(?,?,?,?,?)",
            ("example.sk", "https://example.sk/", "2026-01-01", "2026-01-01", "2026-01-01"),
        )

    repository = Repository(path)
    repository.update_candidate(
        1,
        opportunity_json={"opportunity_score": 42},
        outreach_json={"status": "drafted", "subject": "Subject", "body": "Body"},
        mockup_json={"status": "ready", "path": "mockups/example.png"},
    )
    candidate = repository.get_candidate(1)

    assert candidate["opportunity"]["opportunity_score"] == 42
    assert candidate["outreach"] == {"status": "drafted", "subject": "Subject", "body": "Body"}
    assert candidate["mockup"]["status"] == "ready"
