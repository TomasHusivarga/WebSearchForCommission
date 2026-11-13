import json
import time
from pathlib import Path

from website_hunter.browser.renderer import BrowserRenderer
from website_hunter.classification.openai_website_analysis import OpenAIWebsiteAnalyzer
from website_hunter.config import settings
from website_hunter.contracts import ContactRecord, ScreeningResult
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
        self.analyzer = OpenAIWebsiteAnalyzer()

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
                or screen.bot_challenge
                or screen.replacement_notice
                or screen.site_kind == "forum"
                or screen.status_code is None
                or screen.status_code >= 400
            ):
                if screen.parked:
                    evidence = "The homepage is a parked domain or hosting placeholder."
                elif screen.bot_challenge:
                    evidence = "The homepage returned a bot-verification challenge instead of the business website."
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
                self.repository.update_candidate(candidate_id, classification_json=classification, status="complete", stage="excluded")
                self._finish(job, "complete", screen.failure_reason, started)
                return

            final_url = screen.final_url or str(job["input_url"])
            contacts = extract_contacts(screen.html, final_url)
            self._include_osm_contacts(contacts, hint)
            unique_contacts = _unique_contacts(contacts)
            business_hint = self._business_hint(candidate_id, hint)
            business = extract_business(screen.html, final_url, business_hint)
            business_data = business.to_dict()
            contact_data = [item.to_dict() for item in unique_contacts]
            opportunity = build_assessment(
                screen.raw_html or screen.html,
                screen.analysis_headers,
                final_url,
                {},
                business_data,
                contact_data,
                hint,
            )
            opportunity["analysis_depth"] = "fast"
            opportunity["pending_checks"] = ["mobile_overflow", "trackers_before_consent", "pagespeed"]
            classification = {
                "category": "UNCERTAIN",
                "evidence": ["Fast scoring is complete. Screenshots and detailed AI analysis run only for selected websites."],
                "mobile_experience": "Not checked yet.",
                "redesign_candidate": None,
                "sufficient_evidence": False,
                "provider": "not_run",
            }

            final_host = canonical_host(final_url)
            duplicate_id = self.repository.find_duplicate(final_host, candidate_id)
            reasons = _priority_reasons(screen, {}, classification, business_data, unique_contacts)
            self.repository.update_candidate(
                candidate_id,
                status="complete",
                stage="fast_scored",
                final_url=final_url,
                screening_json=screen_data,
                business_json=business_data,
                contacts_json=contact_data,
                opportunity_json=opportunity,
                classification_json=classification,
                last_processed_at=now_iso(),
                duplicate_of=duplicate_id,
                priority_reasons_json=reasons,
                processing_ms=int((time.monotonic() - started) * 1000),
                last_error=None,
            )
            self._finish(job, "complete", None, started)
        except UnsafeTarget as error:
            self.repository.update_candidate(candidate_id, status="blocked", stage="blocked", last_error=str(error))
            self._finish(job, "blocked", str(error), started)
        except Exception as error:
            self.repository.update_candidate(candidate_id, status="failed", stage="failed", last_error=str(error)[:500])
            self._finish(job, "failed", str(error)[:500], started)

    def analyze_candidate(self, candidate_id: int) -> dict[str, object]:
        started = time.monotonic()
        candidate = self.repository.get_candidate(candidate_id)
        if not candidate:
            raise KeyError("Candidate not found.")
        current_opportunity = candidate.get("opportunity") or {}
        if not current_opportunity:
            raise ValueError("Run the fast score before requesting detailed analysis.")
        if current_opportunity.get("ai_analysis"):
            return candidate

        if (
            current_opportunity.get("analysis_depth") == "browser"
            and candidate.get("screenshot_desktop")
            and candidate.get("screenshot_mobile")
        ):
            try:
                screen = ScreeningResult(**(candidate.get("screening") or {}))
            except TypeError as error:
                raise ValueError("Saved browser evidence is incomplete. Run the selected-site analysis again.") from error
            desktop_path = self._saved_screenshot(candidate, "screenshot_desktop")
            mobile_path = self._saved_screenshot(candidate, "screenshot_mobile")
            return self._run_detailed_analysis(
                candidate_id,
                candidate,
                screen,
                candidate.get("render") or {},
                current_opportunity,
                candidate.get("business") or {},
                candidate.get("contacts") or [],
                desktop_path,
                mobile_path,
                started,
            )

        input_url = str(candidate.get("input_url") or candidate.get("final_url") or "")
        validate_public_url(input_url)
        hint = candidate.get("business_hint") or {}
        screen = self.screener.inspect(input_url, hint)
        if (
            not screen.reachable or screen.robots_allowed is False or screen.parked or screen.bot_challenge
            or screen.replacement_notice or screen.site_kind == "forum" or not screen.final_url
            or screen.status_code is None or screen.status_code >= 400
        ):
            raise ValueError("The selected website is no longer available for analysis.")

        final_url = screen.final_url
        rendered = self.renderer.render(candidate_id, final_url)
        final_url = rendered.final_url or final_url
        validate_public_url(final_url)
        if canonical_host(final_url) != canonical_host(screen.final_url):
            raise ValueError("The selected website changed to another domain during browser rendering.")
        business_hint = self._business_hint(candidate_id, hint)
        business = extract_business(screen.html, final_url, business_hint).to_dict()
        contacts = extract_contacts(screen.html, final_url)
        for page_url in contact_page_urls(screen.html, final_url, int(settings.defaults["max_contact_pages"])):
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
        contact_data = [item.to_dict() for item in _unique_contacts(contacts)]
        previous_page_speed = current_opportunity.get("page_speed") or {}
        page_speed = previous_page_speed if previous_page_speed.get("status") == "measured" or previous_page_speed.get("performance_score") is not None else None
        opportunity = build_assessment(
            screen.raw_html or screen.html,
            screen.analysis_headers,
            final_url,
            rendered.to_dict(),
            business,
            contact_data,
            hint,
            page_speed,
        )
        render_data = rendered.to_dict()
        opportunity["analysis_depth"] = "browser"
        opportunity["pending_checks"] = ["ai_review"]
        if not page_speed or (page_speed.get("status") != "measured" and page_speed.get("performance_score") is None):
            opportunity["pending_checks"].append("pagespeed")
        classification = {
            "category": "UNCERTAIN",
            "evidence": ["Desktop and mobile screenshots are ready. Detailed AI review is pending."],
            "mobile_experience": "Browser rendering complete; detailed review pending.",
            "redesign_candidate": None,
            "sufficient_evidence": False,
            "provider": "not_run",
        }
        reasons = _priority_reasons(screen, render_data, classification, business, [ContactRecord(**item) for item in contact_data])
        self.repository.update_candidate(
            candidate_id,
            status="complete",
            stage="browser_analyzed",
            final_url=final_url,
            status_code=screen.status_code,
            title=screen.title[:240],
            description=screen.description[:600],
            screening_json=screen.to_dict(),
            legacy_signals_json=screen.legacy_signals,
            render_json=render_data,
            screenshot_desktop=rendered.desktop_path,
            screenshot_mobile=rendered.mobile_path,
            classification_json=classification,
            business_json=business,
            contacts_json=contact_data,
            opportunity_json=opportunity,
            priority_reasons_json=reasons,
            processing_ms=int((time.monotonic() - started) * 1000),
            last_processed_at=now_iso(),
            last_error=None,
        )
        prepared_candidate = self.repository.get_candidate(candidate_id)
        if not prepared_candidate:
            raise KeyError("Candidate not found after browser analysis.")
        return self._run_detailed_analysis(
            candidate_id,
            prepared_candidate,
            screen,
            render_data,
            opportunity,
            business,
            contact_data,
            str(rendered.desktop_path or ""),
            str(rendered.mobile_path or ""),
            started,
        )

    def _run_detailed_analysis(
        self,
        candidate_id: int,
        candidate: dict[str, object],
        screen: ScreeningResult,
        render_data: dict[str, object],
        opportunity: dict[str, object],
        business: dict[str, object],
        contact_data: list[dict[str, object]],
        desktop_path: str,
        mobile_path: str,
        started: float,
    ) -> dict[str, object]:
        final_url = str(candidate.get("final_url") or screen.final_url or "")
        analysis = self.analyzer.analyze(
            url=final_url,
            title=screen.title,
            visible_text=screen.text,
            business=business,
            assessment=opportunity,
            desktop_path=desktop_path,
            mobile_path=mobile_path,
        )
        analysis["analyzed_at"] = now_iso()
        opportunity = dict(opportunity)
        opportunity["analysis_depth"] = "ai"
        page_speed = opportunity.get("page_speed") or {}
        opportunity["pending_checks"] = [] if page_speed.get("status") == "measured" or page_speed.get("performance_score") is not None else ["pagespeed"]
        opportunity["ai_analysis"] = analysis
        classification = {
            "category": analysis["visual_category"],
            "evidence": analysis["visual_evidence"],
            "mobile_experience": analysis["mobile_assessment"],
            "redesign_candidate": analysis["redesign_candidate"],
            "sufficient_evidence": analysis["sufficient_evidence"],
            "provider": f"openai:{analysis['model']}",
        }
        reasons = _priority_reasons(
            screen,
            render_data,
            classification,
            business,
            [ContactRecord(**item) for item in contact_data],
        )
        self.repository.update_candidate(
            candidate_id,
            status="complete",
            stage="ai_analyzed",
            final_url=final_url,
            status_code=screen.status_code,
            title=screen.title[:240],
            description=screen.description[:600],
            screening_json=screen.to_dict(),
            legacy_signals_json=screen.legacy_signals,
            render_json=render_data,
            screenshot_desktop=desktop_path,
            screenshot_mobile=mobile_path,
            classification_json=classification,
            business_json=business,
            contacts_json=contact_data,
            opportunity_json=opportunity,
            priority_reasons_json=reasons,
            processing_ms=int((time.monotonic() - started) * 1000),
            last_processed_at=now_iso(),
            last_error=None,
        )
        final_host = canonical_host(final_url)
        self.repository.update_candidate(candidate_id, duplicate_of=self.repository.find_duplicate(final_host, candidate_id))
        result = self.repository.get_candidate(candidate_id)
        if not result:
            raise KeyError("Candidate not found after analysis.")
        return result

    @staticmethod
    def _saved_screenshot(candidate: dict[str, object], field: str) -> str:
        value = candidate.get(field)
        if not value:
            raise ValueError("Saved browser screenshots are incomplete. Run the selected-site analysis again.")
        path = Path(str(value)).resolve()
        screenshot_root = settings.screenshot_dir.resolve()
        if not path.is_relative_to(screenshot_root) or not path.is_file():
            raise ValueError("Saved browser screenshots are unavailable in this workspace. Run the selected-site analysis again.")
        return str(path)

    def _business_hint(self, candidate_id: int, hint: dict[str, object]) -> dict[str, object]:
        business_hint = dict(hint)
        candidate = self.repository.get_candidate(candidate_id)
        source_url = next((item.get("source_url") for item in (candidate or {}).get("sources", []) if item.get("source_url")), None)
        if source_url and not business_hint.get("source_url"):
            business_hint["source_url"] = source_url
        return business_hint

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
