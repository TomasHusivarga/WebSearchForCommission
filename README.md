# Website Opportunity Finder

A local research workspace for finding Slovak businesses whose websites may benefit from a redesign. It discovers candidates, checks public website evidence, captures desktop and mobile screenshots, ranks likely opportunities, generates local redesign concepts, and prepares human reviewed outreach drafts.

The app never sends email automatically. “Open in email app” saves the draft and opens the user's mail client for review. Website findings and expected benefits are evidence based suggestions, not guarantees.

## Setup

In PowerShell from this folder:

```powershell
.\setup.ps1
Copy-Item .env.example .env
```

Set `OPENAI_API_KEY` to enable optional screenshot classification. Without it, visual findings remain uncertain for human review. Set `PSI_API_KEY` to enable an on demand mobile PageSpeed measurement for a selected candidate. Both keys are optional.

Redesign concepts use the parent workspace's local Ming/WanGP installation. Generation is available when that runtime and its Ming Design checkpoint are present; images are saved under this app's `data/mockups/` folder.

## Run

```powershell
.\run.ps1
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Local candidate records, reviews, outreach drafts, and screenshots are stored under `data/`.

## Workflow

1. Discover a small batch from Slovakia's `.sk` domain inventory, OpenStreetMap business listings, archived domains, legacy fingerprints, or a manually entered URL.
2. Review each candidate's classification and source linked business details.
3. Compare the desktop and mobile screenshots. HTML checks and weighted scores are prioritization aids; they do not prove when the site was built or predict results.
4. Inspect positive signals and specific redesign options with their evidence and possible benefit.
5. Generate an optional visual concept, record a human verdict, edit a Slovak outreach draft, and track its status. Drafts can be opened in a local email app for review.

OpenStreetMap discoveries include attribution in the interface. Discovery categories and towns are defined in `config/defaults.json`.

## Architecture and data

The app uses a sequential restartable job worker, a SQLite repository, FastAPI routes, and a static HTML/CSS/JavaScript review interface. The original Website Hunter provides Slovak discovery sources, URL safety, robots handling, bounded fetching, Playwright rendering, optional visual classification, contact extraction, persistent review, and source metrics. The merged opportunity package adds the friend project's HTML facts and weighted ranking; optional PageSpeed checks are requested individually.

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
