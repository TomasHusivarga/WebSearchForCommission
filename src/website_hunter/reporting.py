import base64
import csv
import html
import io
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from website_hunter.config import settings


CSV_COLUMNS = (
    "rank", "business", "domain", "website", "lead_score", "neglect_score", "money_score",
    "neglect_signals", "money_signals", "ai_summary", "positive_points", "improvements",
    "redesign_direction", "emails", "phones", "analyzed_at",
)


def write_reports(candidates: list[dict[str, object]], data_dir: Path | None = None) -> tuple[Path, Path, int]:
    selected = [candidate for candidate in candidates if (candidate.get("opportunity") or {}).get("ai_analysis")]
    if not selected:
        raise ValueError("Analyze at least one selected website before generating reports.")
    root = (data_dir or settings.data_dir).resolve()
    report_dir = (root / "reports").resolve()
    if not report_dir.is_relative_to(root):
        raise ValueError("Report files must stay inside the project data folder.")
    report_dir.mkdir(parents=True, exist_ok=True)
    html_path = report_dir / "latest.html"
    csv_path = report_dir / "latest.csv"
    html_path.write_text(render_html(selected, root), encoding="utf-8")
    csv_path.write_bytes(render_csv(selected))
    return html_path, csv_path, len(selected)


def render_csv(candidates: list[dict[str, object]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(CSV_COLUMNS)
    ordered = sorted(candidates, key=lambda candidate: _lead_score(candidate), reverse=True)
    for rank, candidate in enumerate(ordered, 1):
        opportunity = candidate.get("opportunity") or {}
        analysis = opportunity.get("ai_analysis") or {}
        contacts = candidate.get("contacts") or []
        writer.writerow([
            rank,
            _csv_safe((candidate.get("business") or {}).get("name") or candidate.get("title") or ""),
            _csv_safe(candidate.get("canonical_domain", "")),
            _csv_safe(candidate.get("final_url", "")),
            _lead_score(candidate),
            opportunity.get("neglect_score", 0),
            opportunity.get("money_score", opportunity.get("activity_score", 0)),
            _csv_safe(_flatten_signals(opportunity.get("issues_detail") or [])),
            _csv_safe(_flatten_signals(opportunity.get("strengths") or [])),
            _csv_safe(analysis.get("summary", "")),
            _csv_safe(_flatten_points(analysis.get("positives") or [], "point")),
            _csv_safe(_flatten_points(analysis.get("improvements") or [], "recommendation")),
            _csv_safe(analysis.get("redesign_direction", "")),
            _csv_safe(", ".join(item.get("value", "") for item in contacts if item.get("kind") == "email")),
            _csv_safe(", ".join(item.get("value", "") for item in contacts if item.get("kind") == "phone")),
            _csv_safe(analysis.get("analyzed_at", "")),
        ])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def render_html(candidates: list[dict[str, object]], data_root: Path | None = None) -> str:
    root = (data_root or settings.data_dir).resolve()
    ordered = sorted(candidates, key=lambda candidate: _lead_score(candidate), reverse=True)
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    rows = []
    cards = []
    for rank, candidate in enumerate(ordered, 1):
        opportunity = candidate.get("opportunity") or {}
        analysis = opportunity.get("ai_analysis") or {}
        business = candidate.get("business") or {}
        contacts = candidate.get("contacts") or []
        url = _safe_url(candidate.get("final_url") or candidate.get("input_url") or "")
        domain = html.escape(str(candidate.get("canonical_domain") or ""))
        name = html.escape(str(business.get("name") or candidate.get("title") or domain or "Unnamed business"))
        lead = _lead_score(candidate)
        neglect = int(opportunity.get("neglect_score") or 0)
        money = int(opportunity.get("money_score", opportunity.get("activity_score", 0)) or 0)
        rows.append(
            f"<tr><td>{rank}</td><td><a href=\"{html.escape(url, quote=True)}\" rel=\"noopener noreferrer\">{name}</a><small>{domain}</small></td>"
            f"<td>{lead}</td><td>{neglect}</td><td>{money}</td></tr>"
        )
        positives = _list_items(analysis.get("positives") or [], "point", "evidence")
        improvements = _improvement_items(analysis.get("improvements") or [])
        images = _screenshot_html(candidate, root)
        email = ", ".join(item.get("value", "") for item in contacts if item.get("kind") == "email")
        phone = ", ".join(item.get("value", "") for item in contacts if item.get("kind") == "phone")
        issues = _signals_html(opportunity.get("issues_detail") or [])
        activity = _signals_html(opportunity.get("strengths") or [])
        cards.append(f"""
<article class="lead-card">
  <header class="lead-heading"><div><p class="eyebrow">LEAD {rank} · {domain}</p><h2><a href="{html.escape(url, quote=True)}" rel="noopener noreferrer">{name}</a></h2></div>
    <div class="scores"><span class="score lead">Lead {lead}</span><span class="score neglect">Neglect {neglect}</span><span class="score money">Money {money}</span></div>
  </header>
  <p class="summary">{html.escape(str(analysis.get("summary") or ""))}</p>
  <div class="analysis-grid"><section><h3>Existing positives</h3><ul>{positives}</ul></section>
    <section><h3>Improvement options</h3><ul>{improvements}</ul></section></div>
  <details><summary>Observed score evidence</summary><div class="evidence-grid"><section><h3>Website neglect</h3><ul>{issues}</ul></section>
    <section><h3>Money signals</h3><ul>{activity}</ul></section></div></details>
  <p class="direction"><strong>Redesign direction:</strong> {html.escape(str(analysis.get("redesign_direction") or ""))}</p>
  <div class="contact-row">{f'<span>Email: {html.escape(email)}</span>' if email else ''}{f'<span>Phone: {html.escape(phone)}</span>' if phone else ''}</div>
  {images}
  <p class="stamp">Analyzed {html.escape(str(analysis.get("analyzed_at") or ""))} · Visual category {html.escape(str(analysis.get("visual_category") or "UNCERTAIN"))}</p>
</article>""")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Website opportunity report</title><style>{_REPORT_CSS}</style></head><body>
<main><header class="report-header"><p class="eyebrow">WEBSITE OPPORTUNITY FINDER</p><h1>Selected website analyses</h1><p>Generated {html.escape(generated)} · {len(ordered)} AI-analyzed lead(s)</p></header>
<section class="score-table"><h2>Lead score table</h2><div class="table-scroll"><table><thead><tr><th>#</th><th>Business</th><th>Lead</th><th>Neglect</th><th>Money</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
{''.join(cards)}<footer>Scores are evidence-based prioritization signals. They do not prove advertising spend or guarantee business results.</footer></main></body></html>"""


def _screenshot_html(candidate: dict[str, object], root: Path) -> str:
    images = []
    for key, label in (("screenshot_mobile", "Mobile screenshot"), ("screenshot_desktop", "Desktop screenshot")):
        source = _image_data(candidate.get(key), root)
        if source:
            images.append(f'<figure><img src="{source}" loading="lazy" alt="{label}"><figcaption>{label}</figcaption></figure>')
    return f'<div class="screenshots">{"".join(images)}</div>' if images else '<p class="muted">Screenshots were not available.</p>'


def _image_data(value: object, root: Path) -> str | None:
    if not value:
        return None
    path = Path(str(value)).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 8_000_000:
        return None
    media_type = mimetypes.guess_type(path.name)[0]
    if media_type not in {"image/png", "image/jpeg", "image/webp"}:
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _signals_html(items: list[dict[str, object]]) -> str:
    if not items:
        return "<li>No recorded signals.</li>"
    return "".join(
        f"<li><strong>{html.escape(str(item.get('label') or item.get('key') or 'Signal'))}</strong> "
        f"<span>{html.escape(str(item.get('evidence') or ''))}</span> "
        f"<b>+{int(item.get('weight') or 0)}</b></li>"
        for item in items
    )


def _list_items(items: list[dict[str, object]], title_key: str, detail_key: str) -> str:
    if not items:
        return "<li>No positive points were recorded.</li>"
    return "".join(
        f"<li><strong>{html.escape(str(item.get(title_key) or ''))}</strong> "
        f"<span>{html.escape(str(item.get(detail_key) or ''))}</span></li>"
        for item in items
    )


def _improvement_items(items: list[dict[str, object]]) -> str:
    if not items:
        return "<li>No additional improvement options were recorded.</li>"
    return "".join(
        f"<li><strong>{html.escape(str(item.get('issue') or ''))}</strong> "
        f"<span>{html.escape(str(item.get('evidence') or ''))}</span> "
        f"<span><b>Option:</b> {html.escape(str(item.get('recommendation') or ''))}</span> "
        f"<span><b>Possible benefit:</b> {html.escape(str(item.get('possible_benefit') or ''))}</span></li>"
        for item in items
    )


def _flatten_signals(items: list[dict[str, object]]) -> str:
    return ", ".join(f"{item.get('label') or item.get('key', '')} (+{item.get('weight', 0)})" for item in items)


def _flatten_points(items: list[dict[str, object]], key: str) -> str:
    return " | ".join(str(item.get(key) or "") for item in items)


def _lead_score(candidate: dict[str, object]) -> int:
    opportunity = candidate.get("opportunity") or {}
    return int(opportunity.get("lead_score", opportunity.get("opportunity_score", 0)) or 0)


def _csv_safe(value: object) -> object:
    if not isinstance(value, str):
        return value
    return "'" + value if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r")) else value


def _safe_url(value: object) -> str:
    url = str(value or "")
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "#"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return "#"
    return url


_REPORT_CSS = """
:root{color-scheme:light;--bg:#f2f5f2;--card:#fff;--ink:#1d2b25;--muted:#65746c;--line:#dce4dd;--forest:#174c3f;--forest-soft:#e6f0ea;--lead:#244c3e;--neglect:#9a4a32;--money:#366f4b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}main{max-width:1180px;margin:0 auto;padding:28px 18px 56px}h1,h2,h3,p{margin-top:0}a{color:var(--forest);text-decoration-thickness:1px;text-underline-offset:3px}.report-header{padding:20px 0 24px}.report-header h1{font-size:clamp(28px,4vw,42px);margin:4px 0}.report-header p:last-child{color:var(--muted);margin:0}.eyebrow{font-size:11px;font-weight:750;letter-spacing:.12em;color:var(--forest);margin:0 0 6px}.score-table,.lead-card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 8px 28px rgba(27,52,38,.045)}.score-table{padding:16px 18px;margin-bottom:18px}.score-table h2{font-size:17px;margin-bottom:8px}.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:560px}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}td small{display:block;color:var(--muted)}.lead-card{padding:20px;margin:0 0 18px}.lead-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.lead-heading h2{font-size:22px;margin:2px 0 8px;overflow-wrap:anywhere}.scores{display:flex;flex-wrap:wrap;gap:7px}.score{border-radius:999px;background:var(--forest-soft);padding:5px 10px;font-weight:750;font-size:12px;white-space:nowrap}.score.lead{color:var(--lead)}.score.neglect{color:var(--neglect);background:#f8ebe7}.score.money{color:var(--money);background:#e9f3e9}.summary{font-size:16px;max-width:80ch;margin:8px 0 16px}.analysis-grid,.evidence-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.analysis-grid h3,.evidence-grid h3{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:0 0 8px}.analysis-grid ul,.evidence-grid ul{list-style:none;padding:0;margin:0}.analysis-grid li,.evidence-grid li{display:grid;gap:4px;padding:9px 0;border-bottom:1px solid var(--line);font-size:13px}.analysis-grid li strong{font-size:14px}.analysis-grid li span,.evidence-grid li span{color:var(--muted)}.evidence-grid b{color:var(--forest)}details{margin-top:14px}summary{cursor:pointer;color:var(--forest);font-weight:700}.direction{padding:12px 14px;border-radius:9px;background:var(--forest-soft);font-size:13px}.contact-row{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--muted);font-size:13px}.screenshots{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;margin-top:16px;align-items:start}.screenshots figure{margin:0}.screenshots img{display:block;max-width:100%;max-height:780px;object-fit:contain;object-position:top;border:1px solid var(--line);border-radius:9px;background:#f7f9f7}.screenshots figcaption,.stamp,.muted{color:var(--muted);font-size:12px;margin-top:6px}.stamp{border-top:1px solid var(--line);padding-top:10px;margin-bottom:0}footer{color:var(--muted);font-size:12px;padding:10px 0}@media(max-width:720px){main{padding:18px 12px 40px}.analysis-grid,.evidence-grid,.screenshots{grid-template-columns:minmax(0,1fr)}.lead-heading{align-items:flex-start}.lead-card{padding:15px}.scores{width:100%}}
"""
