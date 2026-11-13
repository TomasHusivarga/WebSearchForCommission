import json
import random
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any

from website_hunter.config import settings
from website_hunter.contracts import DiscoveredDomain
from website_hunter.screening.antiquity import (
    REJECTED_REVIEW_VERDICTS,
    AntiquityAssessment,
    assess_antiquity,
    prospect_priority,
)
from website_hunter.security.urls import canonical_host, normalize_inventory_domain, normalize_url
from website_hunter.storage.database import connect


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Repository:
    def __init__(self, database_path=None):
        self.database_path = database_path

    def add_inventory(self, domains: list[str], source_url: str) -> int:
        now = now_iso()
        rows = []
        for domain in domains:
            normalized = normalize_inventory_domain(domain)
            rows.append((normalized, now, now, source_url, _priority_key(normalized)))
        with connect(self.database_path) as connection:
            connection.executemany(
                """INSERT INTO inventory(domain, first_seen, last_seen, source_url, priority_key)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(domain) DO UPDATE SET last_seen=excluded.last_seen, source_url=excluded.source_url""",
                rows,
            )
            return len(rows)

    def inventory_count(self) -> int:
        with connect(self.database_path) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM inventory").fetchone()[0])

    def sample_inventory(self, limit: int, strategy: str, seed: int | None = None) -> list[str]:
        if limit < 1 or limit > int(settings.defaults["max_batch_size"]):
            raise ValueError("Sample size is outside the configured batch limit.")
        with connect(self.database_path) as connection:
            if strategy == "random":
                pool = [row[0] for row in connection.execute("SELECT domain FROM inventory ORDER BY domain").fetchall()]
                return random.Random(seed).sample(pool, min(limit, len(pool)))
            elif strategy == "prioritized":
                rows = connection.execute("SELECT domain FROM inventory ORDER BY priority_key DESC, random() LIMIT ?", (limit,)).fetchall()
            else:
                raise ValueError("Sample strategy must be random or prioritized.")
            return [row[0] for row in rows]

    def sample_unscreened_inventory(self, limit: int, strategy: str) -> list[str]:
        if limit < 1 or limit > int(settings.defaults["max_batch_size"]):
            raise ValueError("Sample size is outside the configured batch limit.")
        order = "priority_key DESC, random()" if strategy == "prioritized" else "random()"
        with connect(self.database_path) as connection:
            rows = connection.execute(f"SELECT domain FROM inventory WHERE screened_at IS NULL ORDER BY {order} LIMIT ?", (limit,)).fetchall()
            return [row[0] for row in rows]

    def sample_unchecked_history(self, limit: int, strategy: str) -> list[str]:
        if limit < 1 or limit > 30:
            raise ValueError("Historical discovery batches are limited to 30 domains.")
        order = "priority_key DESC, random()" if strategy == "prioritized" else "random()"
        with connect(self.database_path) as connection:
            rows = connection.execute(f"SELECT domain FROM inventory WHERE history_checked_at IS NULL ORDER BY {order} LIMIT ?", (limit,)).fetchall()
            return [row[0] for row in rows]

    def mark_history_checked(self, domains: list[str]) -> None:
        if not domains:
            return
        now = now_iso()
        with connect(self.database_path) as connection:
            connection.executemany("UPDATE inventory SET history_checked_at=? WHERE domain=?", [(now, domain) for domain in domains])

    def mark_inventory_screened(self, domains: list[str]) -> None:
        if not domains:
            return
        now = now_iso()
        with connect(self.database_path) as connection:
            connection.executemany("UPDATE inventory SET screened_at=? WHERE domain=?", [(now, domain) for domain in domains])

    def create_run(self, source: str, category: str, requested: int, config: dict[str, Any] | None = None) -> int:
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO discovery_runs(source, category, status, requested, configuration_json, started_at) VALUES (?, ?, 'running', ?, ?, ?)",
                (source, category, requested, _json(config or {}), now_iso()),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, discovered: int, status: str = "complete", force: bool = False) -> None:
        with connect(self.database_path) as connection:
            if force:
                connection.execute("UPDATE discovery_runs SET status=?, discovered=?, completed_at=? WHERE id=?", (status, discovered, now_iso(), run_id))
            else:
                connection.execute(
                    """UPDATE discovery_runs SET status=CASE WHEN status IN ('complete','partial','failed') THEN status ELSE ? END,
                       discovered=?, completed_at=CASE WHEN ? IN ('complete','failed') THEN ? ELSE completed_at END WHERE id=?""",
                    (status, discovered, status, now_iso(), run_id),
                )

    def update_run_configuration(self, run_id: int, config: dict[str, Any]) -> None:
        with connect(self.database_path) as connection:
            connection.execute("UPDATE discovery_runs SET configuration_json=? WHERE id=?", (_json(config), run_id))

    def enqueue_discoveries(self, items: list[DiscoveredDomain], run_id: int, force: bool = False) -> dict[str, int]:
        now = now_iso()
        counts = {"discovered": 0, "new_candidates": 0, "queued": 0, "duplicates": 0}
        with connect(self.database_path) as connection:
            for item in items:
                try:
                    url = normalize_url(item.url)
                    domain = canonical_host(url)
                except (ValueError, UnicodeError):
                    continue
                counts["discovered"] += 1
                exists = connection.execute("SELECT id, status FROM candidates WHERE canonical_domain=?", (domain,)).fetchone()
                if exists:
                    candidate_id = int(exists["id"])
                    counts["duplicates"] += 1
                    connection.execute("UPDATE candidates SET last_seen=? WHERE id=?", (now, candidate_id))
                else:
                    cursor = connection.execute(
                        """INSERT INTO candidates(canonical_domain, input_url, created_at, first_seen, last_seen, business_hint_json)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (domain, url, now, now, now, _json(item.business_hint)),
                    )
                    candidate_id = int(cursor.lastrowid)
                    counts["new_candidates"] += 1
                connection.execute(
                    """INSERT INTO candidate_sources(candidate_id, source, run_id, source_url, evidence_json, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(candidate_id, source, run_id) DO UPDATE SET
                         last_seen=excluded.last_seen, source_url=COALESCE(excluded.source_url, candidate_sources.source_url),
                         evidence_json=excluded.evidence_json""",
                    (candidate_id, item.source, run_id, item.source_url, _json(item.business_hint), now, now),
                )
                job = connection.execute("SELECT status FROM jobs WHERE candidate_id=?", (candidate_id,)).fetchone()
                if not job:
                    connection.execute(
                        "INSERT INTO jobs(candidate_id, status, attempts, available_at, created_at, updated_at) VALUES (?, 'queued', 0, ?, ?, ?)",
                        (candidate_id, time.time(), now, now),
                    )
                    connection.execute("UPDATE candidates SET status='queued', stage='queued', last_error=NULL WHERE id=?", (candidate_id,))
                    counts["queued"] += 1
                elif force and job["status"] in {"complete", "failed", "blocked"}:
                    connection.execute(
                        "UPDATE jobs SET status='queued', attempts=0, available_at=?, error=NULL, updated_at=? WHERE candidate_id=?",
                        (time.time(), now, candidate_id),
                    )
                    connection.execute("UPDATE candidates SET status='queued', stage='queued', last_error=NULL WHERE id=?", (candidate_id,))
                    counts["queued"] += 1
            connection.execute("UPDATE discovery_runs SET discovered=? WHERE id=?", (counts["discovered"], run_id))
        return counts

    def claim_job(self) -> dict[str, Any] | None:
        now = now_iso()
        with connect(self.database_path) as connection:
            job = connection.execute(
                """SELECT jobs.id AS job_id, jobs.candidate_id, jobs.attempts AS attempts, jobs.error AS job_error, candidates.* FROM jobs
                   JOIN candidates ON candidates.id=jobs.candidate_id
                   WHERE jobs.status='queued' AND jobs.available_at<=?
                   ORDER BY jobs.id LIMIT 1""",
                (time.time(),),
            ).fetchone()
            if not job:
                return None
            connection.execute("UPDATE jobs SET status='running', attempts=attempts+1, updated_at=? WHERE id=?", (now, job["job_id"]))
            connection.execute("UPDATE candidates SET status='processing', stage='screening' WHERE id=?", (job["candidate_id"],))
            return dict(job)

    def update_candidate(self, candidate_id: int, **fields: Any) -> None:
        json_fields = {
            "screening_json", "render_json", "classification_json", "business_json", "opportunity_json", "outreach_json", "mockup_json",
            "contacts_json", "history_json", "business_hint_json", "legacy_signals_json", "priority_reasons_json",
        }
        allowed = {
            "status", "stage", "last_processed_at", "final_url", "status_code", "title", "description",
            "screening_json", "render_json", "classification_json", "business_json", "opportunity_json", "outreach_json", "mockup_json", "contacts_json",
            "history_json", "business_hint_json", "legacy_signals_json", "priority_reasons_json",
            "screenshot_desktop", "screenshot_mobile", "duplicate_of", "business_id", "processing_ms", "last_error",
        }
        if not fields or not set(fields).issubset(allowed):
            raise ValueError("Candidate update contains unsupported fields.")
        with connect(self.database_path) as connection:
            business = fields.get("business_json")
            if business is not None:
                identity = json.loads(business) if isinstance(business, str) else business
                if identity.get("name"):
                    candidate = connection.execute("SELECT canonical_domain FROM candidates WHERE id=?", (candidate_id,)).fetchone()
                    normalized_name = unicodedata.normalize("NFKC", str(identity["name"])).casefold().strip()
                    normalized_name = re.sub(r"\s+", " ", normalized_name)
                    now = now_iso()
                    connection.execute(
                        """INSERT INTO businesses(normalized_name, primary_domain, identity_json, first_seen, last_seen)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(normalized_name, primary_domain) DO UPDATE SET identity_json=excluded.identity_json, last_seen=excluded.last_seen""",
                        (normalized_name, candidate["canonical_domain"], _json(identity), now, now),
                    )
                    business_row = connection.execute("SELECT id FROM businesses WHERE normalized_name=? AND primary_domain=?", (normalized_name, candidate["canonical_domain"])).fetchone()
                    fields["business_id"] = int(business_row[0])
                else:
                    fields["business_id"] = None
            contacts = fields.get("contacts_json")
            if contacts is not None:
                records = json.loads(contacts) if isinstance(contacts, str) else contacts
                connection.execute("DELETE FROM contacts WHERE candidate_id=?", (candidate_id,))
                connection.executemany(
                    """INSERT INTO contacts(candidate_id, kind, value, classification, source_url, observed_at, evidence)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (candidate_id, item["kind"], item["value"], item["classification"], item["source_url"], item["observed_at"], item.get("evidence", ""))
                        for item in records
                    ],
                )
            values = {key: _json(value) if key in json_fields and not isinstance(value, str) else value for key, value in fields.items()}
            assignments = ", ".join(f"{key}=?" for key in values)
            connection.execute(f"UPDATE candidates SET {assignments} WHERE id=?", (*values.values(), candidate_id))

    def finish_job(self, job_id: int, status: str, error: str | None = None, attempts_limit: int | None = None, retry_delay: int | None = None) -> None:
        now = now_iso()
        with connect(self.database_path) as connection:
            job = connection.execute("SELECT candidate_id, attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                return
            retry_limit = int(settings.defaults["retry_limit"]) if attempts_limit is None else attempts_limit
            delay = int(settings.defaults["retry_delay_seconds"]) if retry_delay is None else retry_delay
            if status == "failed" and int(job["attempts"]) < retry_limit:
                connection.execute(
                    "UPDATE jobs SET status='queued', available_at=?, error=?, updated_at=? WHERE id=?",
                    (time.time() + delay, error, now, job_id),
                )
                connection.execute("UPDATE candidates SET status='queued', stage='retry_wait', last_error=? WHERE id=?", (error, job["candidate_id"]))
                return
            connection.execute("UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?", (status, error, now, job_id))
            connection.execute(
                "UPDATE candidates SET status=?, stage=?, last_error=?, last_processed_at=? WHERE id=?",
                ("complete" if status == "complete" else status, "complete" if status == "complete" else status, error, now, job["candidate_id"]),
            )
            run_ids = connection.execute("SELECT DISTINCT run_id FROM candidate_sources WHERE candidate_id=? AND run_id IS NOT NULL", (job["candidate_id"],)).fetchall()
            for run in run_ids:
                run_id = int(run[0])
                pending = connection.execute(
                    """SELECT COUNT(*) FROM candidate_sources cs JOIN jobs j ON j.candidate_id=cs.candidate_id
                       WHERE cs.run_id=? AND j.status NOT IN ('complete','failed','blocked')""",
                    (run_id,),
                ).fetchone()[0]
                if pending == 0:
                    failures = connection.execute(
                        """SELECT COUNT(*) FROM candidate_sources cs JOIN jobs j ON j.candidate_id=cs.candidate_id
                           WHERE cs.run_id=? AND j.status IN ('failed','blocked')""",
                        (run_id,),
                    ).fetchone()[0]
                    connection.execute(
                        "UPDATE discovery_runs SET status=?, completed_at=? WHERE id=? AND status NOT IN ('failed','partial')",
                        ("partial" if failures else "complete", now, run_id),
                    )

    def get_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        with connect(self.database_path) as connection:
            row = connection.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            return self._candidate_dict(connection, row) if row else None

    def list_candidates(self, limit: int = 100, reviewed: bool | None = None, sort: str = "prospects") -> list[dict[str, Any]]:
        limit = max(1, min(limit, 300))
        if sort not in {"prospects", "antiquity", "recent"}:
            raise ValueError("Candidate sort must be prospects, antiquity, or recent.")
        where = ""
        if reviewed is True:
            where = "WHERE review.verdict IS NOT NULL"
        elif reviewed is False:
            where = "WHERE review.verdict IS NULL"
        with connect(self.database_path) as connection:
            rows = connection.execute(
                f"""SELECT candidates.*, review.verdict AS review_verdict, review.note AS review_note,
                          review.reviewed_at AS reviewed_at
                   FROM candidates LEFT JOIN reviews AS review ON review.candidate_id=candidates.id
                   {where}
                   ORDER BY CASE candidates.status WHEN 'complete' THEN 0 WHEN 'processing' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,
                            candidates.last_seen DESC""",
            ).fetchall()
            rows = list(rows)
            if sort == "prospects":
                rows = [row for row in rows if not self._is_confirmed_nonprospect(row)]
                rows.sort(key=self._row_prospect, reverse=True)
            elif sort == "antiquity":
                rows.sort(key=lambda row: self._row_antiquity(row).sort_key, reverse=True)
            return [self._candidate_dict(connection, row) for row in rows[:limit]]

    def review(self, candidate_id: int, verdict: str, note: str = "") -> dict[str, Any]:
        allowed = {"GOOD PROSPECT", "OUTDATED BUT NOT USEFUL", "NOT OUTDATED", "NOT A BUSINESS / IRRELEVANT", "UNCERTAIN", "DEAD / UNREACHABLE"}
        if verdict not in allowed:
            raise ValueError("Choose a supported review verdict.")
        if len(note) > 500:
            raise ValueError("Review notes are limited to 500 characters.")
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO reviews(candidate_id, verdict, note, reviewed_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(candidate_id) DO UPDATE SET verdict=excluded.verdict, note=excluded.note, reviewed_at=excluded.reviewed_at""",
                (candidate_id, verdict, note.strip(), now_iso()),
            )
        candidate = self.get_candidate(candidate_id)
        if not candidate:
            raise KeyError("Candidate not found.")
        return candidate

    def metrics(self) -> list[dict[str, Any]]:
        with connect(self.database_path) as connection:
            sources = connection.execute(
                """SELECT source FROM candidate_sources
                   UNION
                   SELECT source FROM discovery_runs WHERE category<>'inventory_import'
                   ORDER BY source"""
            ).fetchall()
            results = []
            for source_row in sources:
                source = source_row[0]
                rows = connection.execute(
                    """SELECT DISTINCT c.id, c.status, c.processing_ms, c.last_processed_at, c.status_code,
                              c.duplicate_of, c.screening_json, c.render_json, c.classification_json,
                              c.business_json, c.contacts_json,
                              r.verdict
                       FROM candidates c
                       JOIN candidate_sources cs ON cs.candidate_id=c.id
                       LEFT JOIN reviews r ON r.candidate_id=c.id
                       WHERE cs.source=?""",
                    (source,),
                ).fetchall()
                unique_candidates = len(rows)
                run_stats = connection.execute(
                    "SELECT COALESCE(SUM(discovered), 0), COUNT(*) FROM discovery_runs WHERE source=?",
                    (source,),
                ).fetchone()
                discovered = int(run_stats[0]) if run_stats[1] else unique_candidates
                inspected = 0
                businesses = outdated = good = emails = failed = duplicates = reviewed = 0
                effort_ms = 0
                for row in rows:
                    screening = _loads(row["screening_json"], {})
                    render = _loads(row["render_json"], {})
                    classification = _loads(row["classification_json"], {})
                    business = _loads(row["business_json"], {})
                    contacts = _loads(row["contacts_json"], [])
                    businesses += bool(business.get("name"))
                    outdated += classification.get("category") in {"VINTAGE", "DATED"}
                    good += row["verdict"] == "GOOD PROSPECT"
                    reviewed += bool(row["verdict"])
                    emails += any(item.get("kind") == "email" and item.get("classification") in {"general_business_contact", "named_or_external_business_contact"} for item in contacts)
                    inspected += bool(render.get("desktop_path") and render.get("mobile_path"))
                    completed_unreachable = row["status"] == "complete" and (
                        screening.get("reachable") is False or int(row["status_code"] or 0) >= 400
                    )
                    failed += row["status"] in {"failed", "blocked"} or completed_unreachable
                    duplicates += bool(row["duplicate_of"])
                    effort_ms += int(row["processing_ms"] or 0)
                run_configs = connection.execute(
                    "SELECT configuration_json FROM discovery_runs WHERE source=?",
                    (source,),
                ).fetchall()
                pre_screened = sum(int(_loads(run[0], {}).get("domains_screened", _loads(run[0], {}).get("domains_checked", 0))) for run in run_configs)
                results.append({
                    "source": source,
                    "domains_discovered": discovered,
                    "unique_candidates": unique_candidates,
                    "domains_screened_before_discovery": pre_screened,
                    "successfully_inspected": inspected,
                    "successfully_screened": sum(1 for row in rows if _loads(row["screening_json"], {}).get("reachable")),
                    "business_websites": int(businesses),
                    "vintage_or_dated": int(outdated),
                    "good_prospects": int(good),
                    "with_usable_email": int(emails),
                    "duplicates": int(duplicates + max(0, discovered - unique_candidates)),
                    "failed_or_unreachable": int(failed),
                    "reviewed": reviewed,
                    "acceptance_rate": round(good / reviewed, 3) if reviewed else None,
                    "qualified_per_inspected_domain": round(good / inspected, 4) if inspected else None,
                    "processing_minutes": round(effort_ms / 60000, 2),
                    "qualified_per_processing_hour": round(good / (effort_ms / 3600000), 2) if effort_ms else None,
                })
            return results

    def runs(self, limit: int = 30) -> list[dict[str, Any]]:
        with connect(self.database_path) as connection:
            rows = connection.execute("SELECT * FROM discovery_runs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
            results = []
            for row in rows:
                item = dict(row)
                item["configuration"] = _loads(item.pop("configuration_json"), {})
                results.append(item)
            return results

    def jobs(self, limit: int = 40) -> list[dict[str, Any]]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """SELECT jobs.id, jobs.status, jobs.attempts, jobs.error, jobs.updated_at,
                          candidates.canonical_domain, candidates.stage
                   FROM jobs JOIN candidates ON candidates.id=jobs.candidate_id
                   ORDER BY jobs.id DESC LIMIT ?""",
                (max(1, min(limit, 100)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def retry(self, candidate_id: int) -> bool:
        now = now_iso()
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status='queued', attempts=0, available_at=?, error=NULL, updated_at=? WHERE candidate_id=? AND status IN ('failed','blocked')",
                (time.time(), now, candidate_id),
            )
            if cursor.rowcount:
                connection.execute("UPDATE candidates SET status='queued', stage='queued', last_error=NULL WHERE id=?", (candidate_id,))
            return bool(cursor.rowcount)

    def inventory_stats(self) -> dict[str, int]:
        with connect(self.database_path) as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM inventory").fetchone()[0])
            sampled = int(connection.execute("SELECT COUNT(*) FROM inventory WHERE sampled_at IS NOT NULL").fetchone()[0])
            return {"domains": total, "sampled": sampled}

    def find_duplicate(self, domain: str, candidate_id: int) -> int | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT id FROM candidates WHERE canonical_domain=? AND id<>? LIMIT 1",
                (domain, candidate_id),
            ).fetchone()
            return int(row[0]) if row else None

    def mark_sampled(self, domains: list[str]) -> None:
        if not domains:
            return
        now = now_iso()
        with connect(self.database_path) as connection:
            connection.executemany("UPDATE inventory SET sampled_at=? WHERE domain=?", [(now, domain) for domain in domains])

    def _candidate_dict(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        candidate = dict(row)
        for field in ("screening", "render", "classification", "business", "opportunity", "outreach", "mockup", "contacts", "history", "business_hint", "legacy_signals", "priority_reasons"):
            column = f"{field}_json"
            if column in candidate:
                candidate[field] = _loads(candidate.pop(column), [] if field in {"contacts", "legacy_signals", "priority_reasons"} else {})
        assessment = assess_antiquity(candidate.get("classification"), candidate.get("legacy_signals"), candidate.get("render"))
        candidate["antiquity"] = {"label": assessment.label, "reasons": assessment.reasons}
        if "review_verdict" in candidate:
            candidate["review"] = {"verdict": candidate.pop("review_verdict"), "note": candidate.pop("review_note"), "reviewed_at": candidate.pop("reviewed_at")}
        else:
            review = connection.execute("SELECT verdict, note, reviewed_at FROM reviews WHERE candidate_id=?", (row["id"],)).fetchone()
            candidate["review"] = dict(review) if review else None
        sources = connection.execute(
            "SELECT source, source_url, evidence_json, first_seen, last_seen FROM candidate_sources WHERE candidate_id=? ORDER BY source",
            (row["id"],),
        ).fetchall()
        candidate["sources"] = [
            {"source": source["source"], "source_url": source["source_url"], "evidence": _loads(source["evidence_json"], {}), "first_seen": source["first_seen"], "last_seen": source["last_seen"]}
            for source in sources
        ]
        return candidate

    @staticmethod
    def _row_antiquity(row: sqlite3.Row) -> AntiquityAssessment:
        return assess_antiquity(
            _loads(row["classification_json"], {}),
            _loads(row["legacy_signals_json"], []),
            _loads(row["render_json"], {}),
        )

    @classmethod
    def _row_prospect(cls, row: sqlite3.Row) -> tuple[Any, ...]:
        classification = _loads(row["classification_json"], {})
        legacy_signals = _loads(row["legacy_signals_json"], [])
        render = _loads(row["render_json"], {})
        screening = _loads(row["screening_json"], {})
        opportunity = _loads(row["opportunity_json"], {})
        priority = prospect_priority(
            classification,
            legacy_signals,
            render,
            screening,
            row["review_verdict"],
            row["status"],
            row["status_code"],
            row["final_url"],
        )
        assessment = cls._row_antiquity(row)
        lead_score = int(opportunity.get("lead_score", opportunity.get("opportunity_score", 0)) or 0)
        return (lead_score, priority, assessment.sort_key, str(row["last_seen"] or ""))

    @staticmethod
    def _is_confirmed_nonprospect(row: sqlite3.Row) -> bool:
        classification = _loads(row["classification_json"], {})
        screening = _loads(row["screening_json"], {})
        review = str(row["review_verdict"] or "").upper()
        status = str(row["status"] or "")

        if review in REJECTED_REVIEW_VERDICTS:
            return True
        if str(classification.get("category") or "").upper() == "MODERN_OK":
            return True
        if (
            screening.get("parked")
            or screening.get("bot_challenge")
            or screening.get("replacement_notice")
            or screening.get("replacement_url")
            or screening.get("site_kind") == "forum"
        ):
            return True
        if status in {"failed", "blocked"}:
            return True
        if status not in {"queued", "processing"} and (
            row["status_code"] is None
            or row["status_code"] >= 400
            or screening.get("reachable") is False
            or not row["final_url"]
        ):
            return True
        return False


def _loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _priority_key(domain: str) -> int:
    label = domain[:-3].split(".")[0]
    vowels = sum(char in "aeiouy" for char in label)
    letters = [char for char in label if char.isalpha()]
    digit_count = sum(char.isdigit() for char in label)
    score = 0
    if 5 <= len(label) <= 18:
        score += 2
    if letters and vowels:
        score += 2
    if letters and 0.22 <= vowels / len(letters) <= 0.58:
        score += 2
    if digit_count == 0:
        score += 1
    if "-" in label:
        score += 1
    if re.search(r"[bcdfghjklmnpqrstvwxz]{6}", label):
        score -= 3
    return score
