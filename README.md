# Website Opportunity Finder

A local research workspace for finding Slovak businesses whose websites may benefit from a redesign. It discovers candidates, scores public homepage evidence, captures desktop and mobile screenshots for selected leads, generates local redesign concepts, and prepares human-reviewed outreach drafts.

The app never sends email automatically. “Open in email app” saves the draft and opens the user's mail client for review. Website findings and expected benefits are evidence based suggestions, not guarantees.

## Setup

In PowerShell from this folder:

```powershell
.\setup.ps1
Copy-Item .env.example .env
```

Set `OPENAI_API_KEY` to enable detailed AI reviews for candidates you select. The app scores homepage HTML first; desktop/mobile screenshots and AI analysis run only after you select a candidate. Set `PSI_API_KEY` to enable an on-demand mobile PageSpeed measurement. Both keys are optional.

Redesign concepts use the parent workspace's local Ming/WanGP installation. Generation is available when that runtime and its Ming Design checkpoint are present; images are saved under this app's `data/mockups/` folder.

## Run

Double-click `start.bat` to start the local server in the background and open the workspace in a dedicated Edge or Chrome app window. The launcher reuses the server if it is already running. Server output is saved under `data/`.

For a terminal-based run, use:

```powershell
.\run.ps1
```

The terminal-based server is available at [http://127.0.0.1:8765](http://127.0.0.1:8765). Local candidate records, reviews, outreach drafts, and screenshots are stored under `data/`.

## Workflow

1. Discover a small batch from Slovakia's `.sk` domain inventory, OpenStreetMap business listings, archived domains, legacy fingerprints, or a manually entered URL.
2. Review the ranked Viktor score, homepage evidence, and source-linked business details. This fast pass does not render screenshots or call AI.
3. Select the strongest candidates and run detailed AI analysis. Only these sites receive desktop/mobile captures and an AI review of visible positives and redesign options.
4. Generate a styled HTML report and CSV for the analyzed selection. PageSpeed can be measured separately for individual candidates and updates their score.
5. Generate an optional local visual concept, record a human verdict, edit a Slovak outreach draft, and track its status. Drafts can be opened in a local email app for review.

OpenStreetMap discoveries include attribution in the interface. Discovery categories and towns are defined in `config/defaults.json`.

## Architecture and data

The app uses a sequential restartable job worker, a SQLite repository, FastAPI routes, and a static HTML/CSS/JavaScript review interface. Fast discovery uses Slovak sources, URL safety, robots handling, bounded fetching, homepage HTML scoring, contact extraction, persistent review, and source metrics. Playwright rendering and the detailed OpenAI review are deferred until a candidate is selected. The report generator exports analyzed selections to local HTML and CSV files. PageSpeed checks remain individually requested.

See [architecture](docs/ARCHITECTURE.md) and [source notes](docs/UPSTREAMS.md) for module boundaries and merge decisions.

The server binds to loopback. Fetches validate public addresses and redirects, cap response sizes, respect robots.txt, and browser requests are screened. These protections do not create an operating system network namespace. Candidate data is local and excluded from Git.

## Verification

Run the deterministic suite from PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The tests use local fixtures and do not contact business websites, OpenStreetMap, PageSpeed, or AI services.

## Current limits

- OpenStreetMap listings and the `.sk` inventory are incomplete and do not cover every Slovak company.
- HTML clues are not proof of visual age. A person should inspect screenshots and confirm the business and improvement points.
- Advertising tags do not establish that a business is currently paying for ads.
- PageSpeed measurements require the optional Google API key and may be limited by its quota.
- Business names and contact information are source linked but are not verified against a company register.
- Generated visual concepts may contain inaccurate text or details; review them before sharing. The app does not send messages automatically.
