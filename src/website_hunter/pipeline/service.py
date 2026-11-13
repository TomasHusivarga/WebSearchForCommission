import json
import time

from website_hunter.browser.renderer import BrowserRenderer
from website_hunter.classification.openai_responses import OpenAIResponsesClassifier
from website_hunter.config import settings
from website_hunter.contracts import ContactRecord
from website_hunter.opportunities.analysis import build_assessment
from website_hunter.screening.business import extract_business
from website_hunter.screening.contacts import contact_page_urls, extract_contacts
from website_hunter.screening.service import SiteScreener
from website_hunter.security.fetch import SafeFetcher
from website_hunter.security.urls import UnsafeTarget, canonical_host, validate_public_url
from website_hunter.storage.repository import Repository, now_iso


class CandidatePipeline:
    def __init__(self, repository: Repository | None = None):
        self.repository = repository or Repository()
        self.fetcher = SafeFetcher()
        self.screener = SiteScreener(self.fetcher)
        self.renderer = BrowserRenderer()
        self.classifier = OpenAIResponsesClassifier()

    def process(self, job: dict[str, object]) -> None:
        started = time.monotonic()
        candidate_id = int(job["id"])
        try:
            validate_public_url(str(job["input_url"]))
            hint = json.loads(str(job.get("business_hint_json") or "{}"))
            screen = self.screener.inspect(str(job["input_url"]), hint)
            screen_data = screen.to_dict()
            stage_fields = {
                "stage": "screened",
                "final_url": screen.final_url,
                "status_code": screen.status_code,
                "title": screen.title,
                "description": screen.description,
                "screening_json": screen_data,
                "legacy_signals_json": screen.legacy_signals,
            }
            if hint.get("historical_evidence"):
                stage_fields["history_json"] = hint["historical_evidence"]
            self.repository.update_candidate(candidate_id, **stage_fields)
            if screen.robots_allowed is False:
                self.repository.update_candidate(candidate_id, status="blocked", stage="robots_disallowed", last_error=screen.failure_reason)
                self._finish(job, "blocked", screen.failure_reason, started)
                return
            if (
                not screen.reachable
                or screen.parked
                or screen.replacement_notice
                or screen.site_kind == "forum"
                or screen.status_code is None
                or screen.status_code >= 400
            ):
                if screen.parked:
                    evidence = "The homepage is a parked domain or hosting placeholder."
                elif screen.replacement_notice:
                    destination = f" Destination: {screen.replacement_url}." if screen.replacement_url else ""
                    evidence = f"The homepage announces a replacement website.{destination}"
                elif screen.site_kind == "forum":
                    evidence = "The homepage is a discussion forum or community, not a business website prospect."
                else:
                    evidence = "Automated inspection did not produce a usable business website."
                classification = {
                    "category": "UNCERTAIN",
                    "evidence": [evidence],
                    "mobile_experience": "Not assessed.",
                    "redesign_candidate": None,
                    "sufficient_evidence": False,
                    "provider": "screening",
                }
                self.repository.update_candidate(candidate_id, classification_json=classification, status="complete", stage="needs_human_review")
                self._finish(job, "complete", screen.failure_reason, started)
                return

            final_url = screen.final_url or str(job["input_url"])
            self.repository.update_candidate(candidate_id, stage="rendering")
            rendered = self.renderer.render(candidate_id, final_url)
            self.repository.update_candidate(
                candidate_id,
                stage="rendered",
                final_url=rendered.final_url or final_url,
                render_json=rendered.to_dict(),
                screenshot_desktop=rendered.desktop_path,
                screenshot_mobile=rendered.mobile_path,
            )
            observations = {
                "legacy_signals": screen.legacy_signals,
                "mobile_horizontal_overflow": rendered.horizontal_overflow,
                "broken_images": rendered.broken_images,
                "browser_failed_load": rendered.failed_load,
                "business_signals": screen.business_signals,
                "robots_allowed": screen.robots_allowed,
            }
            classification = self.classifier.classify(rendered.desktop_path, rendered.mobile_path, observations)
            if rendered.uncertain and classification.category != "UNCERTAIN":
                classification.category = "UNCERTAIN"
                classification.sufficient_evidence = False
                classification.evidence.append("Browser rendering was incomplete, so the automated result was downgraded for human review.")
            self.repository.update_candidate(candidate_id, stage="classified", classification_json=classification.to_dict())

            if classification.category != "MODERN_OK":
                self.repository.update_candidate(candidate_id, stage="enriching")
                contacts = extract_contacts(screen.html, final_url)
                pages = contact_page_urls(screen.html, final_url, int(settings.defaults["max_contact_pages"]))
                for page_url in pages:
                    if self.screener.page_allowed(page_url) is False:
                        continue
                    try:
                        page_response = self.fetcher.get(page_url)
                    except Exception:
                        continue
                    if "html" in page_response.headers.get("content-type", "").lower() and page_response.status_code < 400:
                        page_html = page_response.body.decode("utf-8", errors="replace")
                        contacts.extend(extract_contacts(page_html, page_response.url))
                self._include_osm_contacts(contacts, hint)
                unique_contacts = _unique_contacts(contacts)
                business_hint = dict(hint)
                business_hint["source_url"] = next((item.get("source_url") for item in self.repository.get_candidate(candidate_id)["sources"] if item.get("source_url")), None)
                business = extract_business(screen.html, final_url, business_hint)
                self.repository.update_candidate(candidate_id, business_json=business.to_dict(), contacts_json=[item.to_dict() for item in unique_contacts])

            business_data = business.to_dict() if classification.category != "MODERN_OK" else {}
            contact_data = [item.to_dict() for item in unique_contacts] if classification.category != "MODERN_OK" else []
            opportunity = build_assessment(
                screen.raw_html or screen.html,
                screen.analysis_headers,
                rendered.final_url or final_url,
                rendered.to_dict(),
                business_data,
                contact_data,
                hint,
            )
            self.repository.update_candidate(candidate_id, opportunity_json=opportunity)

            final_host = canonical_host(rendered.final_url or final_url)
            duplicate_id = self.repository.find_duplicate(final_host, candidate_id)
            reasons = _priority_reasons(screen, rendered.to_dict(), classification.to_dict(), business.to_dict() if classification.category != "MODERN_OK" else {}, unique_contacts if classification.category != "MODERN_OK" else [])
            self.repository.update_candidate(
                candidate_id,
                status="complete",
                stage="complete",
                last_processed_at=now_iso(),
                duplicate_of=duplicate_id,
                priority_reasons_json=reasons,
                processing_ms=int((time.monotonic() - started) * 1000),
                last_error=classification.error,
            )
            self._finish(job, "complete", classification.error, started)
        except UnsafeTarget as error:
            self.repository.update_candidate(candidate_id, status="blocked", stage="blocked", last_error=str(error))
            self._finish(job, "blocked", str(error), started)
        except Exception as error:
            self.repository.update_candidate(candidate_id, status="failed", stage="failed", last_error=str(error)[:500])
            self._finish(job, "failed", str(error)[:500], started)

    def _finish(self, job: dict[str, object], status: str, error: str | None, started: float) -> None:
        self.repository.update_candidate(
            int(job["id"]),
            processing_ms=int((time.monotonic() - started) * 1000),
            last_processed_at=now_iso(),
        )
        self.repository.finish_job(int(job["job_id"]), status, error)

    @staticmethod
    def _include_osm_contacts(contacts: list[ContactRecord], hint: dict[str, object]) -> None:
        source_url = str(hint.get("source_url") or "https://www.openstreetmap.org/")
        for key, kind in (("email", "email"), ("phone", "phone")):
            value = str(hint.get(key) or "").strip()
            if value:
                classification = "general_business_contact" if kind == "email" else "public_phone"
                contacts.append(ContactRecord(value, kind, classification, source_url, now_iso(), "OpenStreetMap business listing"))


def _unique_contacts(contacts: list[ContactRecord]) -> list[ContactRecord]:
    unique: dict[tuple[str, str], ContactRecord] = {}
    for contact in contacts:
        key = (contact.kind, contact.value.strip().lower())
        unique.setdefault(key, contact)
    return list(unique.values())


def _priority_reasons(screen, render: dict[str, object], classification: dict[str, object], business: dict[str, object], contacts: list[ContactRecord]) -> list[str]:
    reasons = []
    category = classification.get("category")
    if category == "VINTAGE":
        reasons.append("Visual classifier marked the current design VINTAGE")
    elif category == "DATED":
        reasons.append("Visual classifier marked the current design DATED")
    if screen.legacy_signals:
        reasons.append(f"{len(screen.legacy_signals)} observable legacy HTML signal(s)")
    if render.get("horizontal_overflow"):
        reasons.append("Rendered mobile page scrolls horizontally")
    if business.get("name"):
        reasons.append("Business name was identified from the site or discovery source")
    if any(item.kind == "email" for item in contacts):
        reasons.append("A public email address was found")
    if category == "UNCERTAIN":
        reasons.append("Classification or browser evidence needs human review")
    return reasons
