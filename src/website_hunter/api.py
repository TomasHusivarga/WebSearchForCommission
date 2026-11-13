import asyncio
import json
import os
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from website_hunter.classification.openai_responses import OpenAIResponsesClassifier
from website_hunter.classification.openai_website_analysis import OpenAIAnalysisError, OpenAIWebsiteAnalyzer
from website_hunter.config import settings
from website_hunter.contracts import DiscoveredDomain
from website_hunter.opportunities.analysis import add_page_speed
from website_hunter.opportunities.pagespeed import PageSpeedError, measure_mobile
from website_hunter.opportunities.mockup import MING_PYTHON, MING_SCRIPT, build_redesign_prompt, designer_available, mockup_directory
from website_hunter.discovery.commoncrawl import CommonCrawlAdapter
from website_hunter.discovery.historical import HistoricalDiscoveryAdapter
from website_hunter.discovery.legacy import LegacyFingerprintAdapter
from website_hunter.discovery.openstreetmap import OpenStreetMapAdapter
from website_hunter.discovery.sknic import SknicInventoryAdapter
from website_hunter.pipeline.worker import JobWorker
from website_hunter.pipeline.service import CandidatePipeline
from website_hunter.reporting import write_reports
from website_hunter.storage.database import initialize, table_counts
from website_hunter.storage.repository import Repository


repository = Repository()
worker = JobWorker(repository)
mockup_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    initialize()
    worker.start()
    yield
    await worker.stop()


app = FastAPI(title="Slovak Website Opportunity Finder", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=settings.app_root / "src" / "website_hunter" / "static"), name="static")


class SknicSampleRequest(BaseModel):
    limit: int = Field(default=12, ge=1, le=500)
    strategy: Literal["random", "prioritized"] = "random"
    seed: int | None = None


class TargetedRequest(BaseModel):
    town: str
    limit: int = Field(default=12, ge=1, le=250)
    categories: list[str] = Field(default_factory=list, max_length=20)


class LegacyRequest(BaseModel):
    limit: int = Field(default=12, ge=1, le=100)
    strategy: Literal["random", "prioritized"] = "random"


class HistoricalRequest(BaseModel):
    limit: int = Field(default=8, ge=1, le=30)
    strategy: Literal["random", "prioritized"] = "random"


class ManualRequest(BaseModel):
    url: str = Field(min_length=3, max_length=2048)


class ReviewRequest(BaseModel):
    candidate_id: int = Field(gt=0)
    verdict: Literal[
        "GOOD PROSPECT",
        "OUTDATED BUT NOT USEFUL",
        "NOT OUTDATED",
        "NOT A BUSINESS / IRRELEVANT",
        "UNCERTAIN",
        "DEAD / UNREACHABLE",
    ]
    note: str = Field(default="", max_length=500)


class OutreachRequest(BaseModel):
    candidate_id: int = Field(gt=0)
    status: Literal["new", "drafted", "contacted", "replied", "not_interested", "skip"]
    subject: str = Field(default="", max_length=220)
    body: str = Field(default="", max_length=6000)


class ReportRequest(BaseModel):
    candidate_ids: list[int] = Field(min_length=1, max_length=80)


def _discover(items: list[DiscoveredDomain], source: str, category: str, requested: int, config: dict[str, object]) -> dict[str, object]:
    run_id = repository.create_run(source, category, requested, config)
    counts = repository.enqueue_discoveries(items, run_id)
    repository.finish_run(run_id, counts["discovered"], "processing" if counts["queued"] else "complete")
    return {"run_id": run_id, **counts}


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    page = settings.app_root / "src" / "website_hunter" / "static" / "index.html"
    return page.read_text(encoding="utf-8")


@app.get("/api/status")
def status() -> dict[str, object]:
    return {
        "counts": table_counts(),
        "inventory": repository.inventory_stats(),
        "ai_configured": OpenAIResponsesClassifier().available,
        "pagespeed_configured": bool(os.getenv("PSI_API_KEY")),
        "mockup_configured": designer_available(),
        "reports_available": all((settings.data_dir / "reports" / name).is_file() for name in ("latest.html", "latest.csv")),
        "model": settings.openai_model,
        "towns": settings.defaults["benchmark_towns"],
        "business_categories": settings.defaults["business_categories"],
        "limits": {"batch": settings.defaults["max_batch_size"], "osm": settings.defaults["max_osm_results_per_town"]},
    }


@app.get("/api/candidates")
def candidates(limit: int = 80, reviewed: bool | None = None, sort: Literal["prospects", "antiquity", "recent"] = "prospects") -> list[dict[str, object]]:
    return repository.list_candidates(limit=limit, reviewed=reviewed, sort=sort)


@app.get("/api/jobs")
def jobs() -> list[dict[str, object]]:
    return repository.jobs()


@app.get("/api/runs")
def runs() -> list[dict[str, object]]:
    return repository.runs()


@app.get("/api/metrics")
def metrics() -> list[dict[str, object]]:
    return repository.metrics()


@app.post("/api/discover/sknic/import")
async def import_sknic() -> dict[str, object]:
    run_id = repository.create_run("sknic_inventory", "inventory_import", 0, {"source_url": settings.defaults["sknic_url"]})
    try:
        domains, source_url = await asyncio.to_thread(SknicInventoryAdapter().import_domains)
        imported = 0
        for offset in range(0, len(domains), 5000):
            imported += repository.add_inventory(domains[offset : offset + 5000], source_url)
        repository.finish_run(run_id, len(domains))
        return {"run_id": run_id, "inventory_domains": repository.inventory_count(), "imported_or_refreshed": imported, "source_url": source_url}
    except Exception as error:
        repository.finish_run(run_id, 0, "failed", force=True)
        raise HTTPException(502, f"SK-NIC import failed: {str(error)[:240]}") from error


@app.post("/api/discover/sknic/sample")
def sample_sknic(request: SknicSampleRequest) -> dict[str, object]:
    if not repository.inventory_count():
        raise HTTPException(409, "Import the current SK-NIC inventory first.")
    try:
        domains = repository.sample_inventory(request.limit, request.strategy, request.seed)
        repository.mark_sampled(domains)
        source = f"sknic_{request.strategy}_sample"
        items = [DiscoveredDomain(f"https://{domain}", source, str(settings.defaults["sknic_url"])) for domain in domains]
        return _discover(items, source, "sknic_sample", request.limit, {"strategy": request.strategy, "seed": request.seed})
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/discover/targeted")
async def discover_targeted(request: TargetedRequest) -> dict[str, object]:
    adapter = OpenStreetMapAdapter()
    run_id = repository.create_run(adapter.source, "targeted_industry_town", request.limit, {"town": request.town, "categories": request.categories or list(settings.defaults["business_categories"])})
    try:
        items = await asyncio.to_thread(adapter.discover, request.town, request.limit, request.categories or None)
        counts = repository.enqueue_discoveries(items, run_id)
        repository.finish_run(run_id, counts["discovered"], "processing" if counts["queued"] else "complete")
        return {"run_id": run_id, **counts}
    except ValueError as error:
        repository.finish_run(run_id, 0, "failed", force=True)
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        repository.finish_run(run_id, 0, "failed", force=True)
        raise HTTPException(502, f"Targeted discovery failed: {str(error)[:240]}") from error


@app.post("/api/discover/legacy")
async def discover_legacy(request: LegacyRequest) -> dict[str, object]:
    if not repository.inventory_count():
        raise HTTPException(409, "Import the current SK-NIC inventory before running legacy screening.")
    adapter = LegacyFingerprintAdapter(repository)
    run_id = repository.create_run(adapter.source, "legacy_fingerprint_screen", request.limit, {"strategy": request.strategy})
    try:
        items, screened_count, domains = await asyncio.to_thread(adapter.discover, request.limit, request.strategy)
        repository.update_run_configuration(run_id, {"strategy": request.strategy, "domains_screened": screened_count})
        counts = repository.enqueue_discoveries(items, run_id)
        repository.finish_run(run_id, counts["discovered"], "processing" if counts["queued"] else "complete")
        return {"run_id": run_id, **counts, "domains_screened": screened_count}
    except Exception as error:
        repository.finish_run(run_id, 0, "failed", force=True)
        raise HTTPException(502, f"Legacy fingerprint screening failed: {str(error)[:240]}") from error


@app.post("/api/discover/historical")
async def discover_historical(request: HistoricalRequest) -> dict[str, object]:
    if not repository.inventory_count():
        raise HTTPException(409, "Import the SK-NIC inventory before running historical sampling.")
    adapter = HistoricalDiscoveryAdapter(repository)
    run_id = repository.create_run(adapter.source, "historical_domain_age", request.limit, {"strategy": request.strategy, "indexes": settings.defaults["commoncrawl_discovery_indexes"]})
    try:
        items, checked, errors, domains = await asyncio.to_thread(adapter.discover, request.limit, request.strategy)
        repository.update_run_configuration(run_id, {"strategy": request.strategy, "indexes": settings.defaults["commoncrawl_discovery_indexes"], "domains_checked": checked, "index_errors": errors})
        counts = repository.enqueue_discoveries(items, run_id)
        repository.finish_run(run_id, counts["discovered"], "processing" if counts["queued"] else "complete")
        return {"run_id": run_id, **counts, "domains_checked": checked, "index_errors": errors}
    except Exception as error:
        repository.finish_run(run_id, 0, "failed", force=True)
        raise HTTPException(502, f"Historical discovery failed: {str(error)[:240]}") from error


@app.post("/api/discover/manual")
def discover_manual(request: ManualRequest) -> dict[str, object]:
    item = DiscoveredDomain(request.url, "manual")
    try:
        from website_hunter.security.urls import normalize_url

        normalize_url(request.url)
        return _discover([item], "manual", "manual_url", 1, {})
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/candidates/{candidate_id}/history")
async def check_history(candidate_id: int) -> dict[str, object]:
    candidate = repository.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    result = await asyncio.to_thread(CommonCrawlAdapter().history, candidate["canonical_domain"])
    repository.update_candidate(candidate_id, history_json=result)
    return result


@app.post("/api/candidates/{candidate_id}/pagespeed")
async def check_pagespeed(candidate_id: int) -> dict[str, object]:
    api_key = os.getenv("PSI_API_KEY")
    if not api_key:
        raise HTTPException(503, "Set PSI_API_KEY in .env to measure mobile performance.")
    candidate = repository.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    url = str(candidate.get("final_url") or "")
    if not url:
        raise HTTPException(409, "The candidate has no inspected website URL yet.")
    try:
        result = await measure_mobile(url, api_key)
    except PageSpeedError as error:
        raise HTTPException(502, str(error)) from error
    assessment = add_page_speed(candidate.get("opportunity") or {}, result)
    repository.update_candidate(candidate_id, opportunity_json=assessment)
    return {"page_speed": result, "opportunity": assessment}


@app.post("/api/candidates/{candidate_id}/analyze")
async def analyze_candidate(candidate_id: int) -> dict[str, object]:
    analyzer = OpenAIWebsiteAnalyzer()
    if not analyzer.available:
        raise HTTPException(503, "Set OPENAI_API_KEY in .env to analyze selected websites with AI.")
    try:
        candidate = await asyncio.to_thread(CandidatePipeline(repository).analyze_candidate, candidate_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except OpenAIAnalysisError as error:
        raise HTTPException(502, str(error)) from error
    return {"candidate": candidate}


@app.post("/api/reports/generate")
def generate_reports(request: ReportRequest) -> dict[str, object]:
    selected = []
    seen = set()
    for candidate_id in request.candidate_ids:
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        candidate = repository.get_candidate(candidate_id)
        if candidate and (candidate.get("opportunity") or {}).get("ai_analysis"):
            selected.append(candidate)
    if not selected:
        raise HTTPException(409, "Analyze at least one selected website before generating reports.")
    try:
        _, _, count = write_reports(selected, settings.data_dir)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {
        "count": count,
        "html_url": "/api/reports/latest.html",
        "csv_url": "/api/reports/latest.csv",
    }


@app.get("/api/reports/latest.html")
def latest_html_report() -> FileResponse:
    path = settings.data_dir / "reports" / "latest.html"
    if not path.is_file():
        raise HTTPException(404, "Generate a report from analyzed selected websites first.")
    return FileResponse(path, media_type="text/html; charset=utf-8")


@app.get("/api/reports/latest.csv")
def latest_csv_report() -> FileResponse:
    path = settings.data_dir / "reports" / "latest.csv"
    if not path.is_file():
        raise HTTPException(404, "Generate a report from analyzed selected websites first.")
    return FileResponse(path, media_type="text/csv; charset=utf-8", filename="website-opportunities.csv")


@app.post("/api/candidates/{candidate_id}/mockup")
async def generate_mockup(candidate_id: int) -> dict[str, object]:
    if not designer_available():
        raise HTTPException(503, "The local Ming designer runtime is not available.")
    if mockup_lock.locked():
        raise HTTPException(409, "Another redesign concept is already generating.")
    candidate = repository.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    screenshot = candidate.get("screenshot_desktop")
    if not screenshot:
        raise HTTPException(409, "Inspect the website and create a desktop screenshot before generating a concept.")
    screenshot_path = Path(str(screenshot)).resolve()
    screenshot_root = settings.screenshot_dir.resolve()
    if screenshot_root not in screenshot_path.parents or not screenshot_path.is_file():
        raise HTTPException(404, "The candidate screenshot is not available in the local workspace.")
    try:
        output_dir = mockup_directory()
    except ValueError as error:
        raise HTTPException(500, str(error)) from error
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"candidate-{candidate_id}-{timestamp}.png"
    prompt_path = output_dir / f"candidate-{candidate_id}-{timestamp}.txt"
    prompt_path.write_text(build_redesign_prompt(candidate), encoding="utf-8")
    starting = {"status": "generating", "path": None, "started_at": datetime.now(timezone.utc).isoformat()}
    repository.update_candidate(candidate_id, mockup_json=starting)
    async with mockup_lock:
        try:
            process = await asyncio.create_subprocess_exec(
                str(MING_PYTHON), str(MING_SCRIPT),
                "--prompt-file", str(prompt_path),
                "--reference", str(screenshot_path),
                "--output", str(output_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(), timeout=1800)
            except TimeoutError:
                process.kill()
                await process.communicate()
                raise HTTPException(504, "Ming image generation exceeded the 30 minute limit.")
            if process.returncode != 0 or not output_path.is_file():
                details = output.decode("utf-8", errors="replace")[-800:].strip()
                raise HTTPException(502, details or "Ming image generation failed.")
        except HTTPException as error:
            repository.update_candidate(candidate_id, mockup_json={"status": "error", "path": None, "error": str(error.detail)[:800]})
            raise
        except Exception as error:
            repository.update_candidate(candidate_id, mockup_json={"status": "error", "path": None, "error": type(error).__name__})
            raise HTTPException(502, "The local Ming designer could not generate the concept.") from error
    result = {"status": "ready", "path": str(output_path), "created_at": datetime.now(timezone.utc).isoformat()}
    repository.update_candidate(candidate_id, mockup_json=result)
    return {"mockup": result}


@app.get("/api/candidates/{candidate_id}/mockup")
def candidate_mockup(candidate_id: int) -> FileResponse:
    candidate = repository.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    mockup = candidate.get("mockup") or {}
    value = mockup.get("path")
    if mockup.get("status") != "ready" or not value:
        raise HTTPException(404, "A redesign concept is not available yet.")
    path = Path(str(value)).resolve()
    root = (settings.app_root / "data" / "mockups").resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "The redesign concept is not available.")
    return FileResponse(path, media_type="image/png")


@app.get("/api/candidates/{candidate_id}/screenshots/{kind}")
def screenshot(candidate_id: int, kind: Literal["desktop", "mobile"]) -> FileResponse:
    candidate = repository.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    value = candidate.get("screenshot_desktop" if kind == "desktop" else "screenshot_mobile")
    if not value:
        raise HTTPException(404, "Screenshot not available.")
    path = Path(value).resolve()
    root = settings.screenshot_dir.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Screenshot not available.")
    return FileResponse(path, media_type="image/png")


@app.post("/api/reviews")
def submit_review(request: ReviewRequest) -> dict[str, object]:
    try:
        return repository.review(request.candidate_id, request.verdict, request.note)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/outreach")
def save_outreach(request: OutreachRequest) -> dict[str, object]:
    candidate = repository.get_candidate(request.candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    repository.update_candidate(
        request.candidate_id,
        outreach_json={"status": request.status, "subject": request.subject.strip(), "body": request.body.strip()},
    )
    return repository.get_candidate(request.candidate_id)


@app.post("/api/candidates/{candidate_id}/retry")
def retry_candidate(candidate_id: int) -> dict[str, bool]:
    if not repository.get_candidate(candidate_id):
        raise HTTPException(404, "Candidate not found.")
    return {"queued": repository.retry(candidate_id)}
