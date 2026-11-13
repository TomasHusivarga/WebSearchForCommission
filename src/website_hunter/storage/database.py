import sqlite3
from pathlib import Path

from website_hunter.config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    domain TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    source_url TEXT NOT NULL,
    priority_key INTEGER NOT NULL DEFAULT 0,
    sampled_at TEXT,
    screened_at TEXT,
    history_checked_at TEXT
);
CREATE TABLE IF NOT EXISTS discovery_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL,
    requested INTEGER NOT NULL DEFAULT 0,
    discovered INTEGER NOT NULL DEFAULT 0,
    configuration_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY,
    normalized_name TEXT NOT NULL,
    primary_domain TEXT NOT NULL,
    identity_json TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(normalized_name, primary_domain)
);
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY,
    canonical_domain TEXT NOT NULL UNIQUE,
    input_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_processed_at TEXT,
    final_url TEXT,
    business_id INTEGER REFERENCES businesses(id),
    status_code INTEGER,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    screening_json TEXT NOT NULL DEFAULT '{}',
    render_json TEXT NOT NULL DEFAULT '{}',
    classification_json TEXT NOT NULL DEFAULT '{}',
    business_json TEXT NOT NULL DEFAULT '{}',
    opportunity_json TEXT NOT NULL DEFAULT '{}',
    outreach_json TEXT NOT NULL DEFAULT '{}',
    mockup_json TEXT NOT NULL DEFAULT '{}',
    contacts_json TEXT NOT NULL DEFAULT '[]',
    history_json TEXT NOT NULL DEFAULT '{}',
    business_hint_json TEXT NOT NULL DEFAULT '{}',
    legacy_signals_json TEXT NOT NULL DEFAULT '[]',
    priority_reasons_json TEXT NOT NULL DEFAULT '[]',
    screenshot_desktop TEXT,
    screenshot_mobile TEXT,
    duplicate_of INTEGER REFERENCES candidates(id),
    processing_ms INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    FOREIGN KEY (duplicate_of) REFERENCES candidates(id)
);
CREATE TABLE IF NOT EXISTS candidate_sources (
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    run_id INTEGER REFERENCES discovery_runs(id),
    source_url TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY(candidate_id, source, run_id)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL UNIQUE REFERENCES candidates(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    classification TEXT NOT NULL,
    source_url TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    UNIQUE(candidate_id, kind, value)
);
CREATE TABLE IF NOT EXISTS reviews (
    candidate_id INTEGER PRIMARY KEY REFERENCES candidates(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_ready ON jobs(status, available_at, id);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_candidate_sources_run ON candidate_sources(run_id, candidate_id);
CREATE INDEX IF NOT EXISTS idx_contacts_candidate ON contacts(candidate_id);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or settings.database_path
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 20000")
    return connection


def initialize(path: Path | None = None) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(inventory)")}
        if "history_checked_at" not in columns:
            connection.execute("ALTER TABLE inventory ADD COLUMN history_checked_at TEXT")
        candidate_columns = {row[1] for row in connection.execute("PRAGMA table_info(candidates)")}
        for name in ("opportunity_json", "outreach_json", "mockup_json"):
            if name not in candidate_columns:
                connection.execute(f"ALTER TABLE candidates ADD COLUMN {name} TEXT NOT NULL DEFAULT '{{}}'")
        connection.execute(
            "UPDATE jobs SET status='queued', updated_at=datetime('now') WHERE status='running'"
        )


def table_counts(path: Path | None = None) -> dict[str, int]:
    with connect(path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("inventory", "candidates", "businesses", "contacts", "jobs", "reviews", "discovery_runs")
        }
