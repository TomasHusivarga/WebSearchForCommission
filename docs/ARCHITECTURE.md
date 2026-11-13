# Architecture

## Request and processing flow

`Discovery adapter -> normalized candidate and source evidence -> durable SQLite job -> robots-aware fetch -> public-address and redirect checks -> fast homepage score and business/contact extraction -> ranked candidate queue -> selected Playwright desktop/mobile render and AI analysis -> HTML/CSV report -> human review and outreach draft`

The background worker processes one queued candidate at a time. A candidate's state and evidence survive app restarts. On startup, jobs left running are returned to the queue.

## Module boundaries

| Area | Location | Responsibility |
|---|---|---|
| Discovery | `src/website_hunter/discovery/` | SK-NIC `.sk` inventory, town/category listings, legacy sampling, and Common Crawl sampling |
| URL safety and fetch | `src/website_hunter/security/` | URL normalization, public IP validation, redirect checks, bounded fetch, throttling |
| Screening | `src/website_hunter/screening/` | Website availability, legacy clues, business and contact extraction |
| Browser | `src/website_hunter/browser/` | Sandboxed Chromium renders and mobile observations |
| Classification | `src/website_hunter/classification/`, `prompts/` | Detailed AI analysis of screenshots and homepage evidence for selected candidates |
| Opportunity assessment | `src/website_hunter/opportunities/` | HTML facts, issue and activity signals, heuristic scoring, optional PageSpeed parsing, and local Ming concept generation |
| Persistence | `src/website_hunter/storage/` | SQLite schema, migrations, candidates, source records, reviews, outreach status, and score evidence |
| Pipeline | `src/website_hunter/pipeline/` | Sequential processing stages and bounded retries |
| API and UI | `src/website_hunter/api.py`, `src/website_hunter/static/` | Local discovery controls, candidate review, evidence display, and editable outreach drafts |

## Opportunity score

The assessment reports Viktor's neglect score, money-signal score, and combined lead score. The fast score uses homepage HTML and known site facts. Mobile overflow and tracker timing are added after a person selects a candidate for browser rendering; PageSpeed is measured separately on demand. Each issue includes observed evidence, a possible redesign action, and a possible visitor benefit. The benefit is a proposal, not a measured outcome.

Signal weights are defined in `src/website_hunter/opportunities/analysis.py`. Ads tags indicate a tag was present in the fetched page; they do not prove current spend. Unknown facts are left unscored.

## External services

- SK-NIC and OpenStreetMap provide discovery data.
- Common Crawl provides sampled archive history.
- OpenAI detailed analysis is optional, uses the configured API key, and runs only for selected candidates. It describes visible positives and improvement options from the screenshots and supplied evidence.
- Google PageSpeed is optional, uses `PSI_API_KEY`, and runs only after a person requests it for a candidate.
- Selected analyses can be exported to `data/reports/latest.html` and `data/reports/latest.csv`.
- The parent workspace's Ming/WanGP installation can generate a local redesign concept from the candidate's desktop screenshot. Output stays under this app's ignored `data/mockups/` directory.

No service sends outreach emails. “Open in email app” saves the draft status and opens a `mailto:` compose action on the user's device.
