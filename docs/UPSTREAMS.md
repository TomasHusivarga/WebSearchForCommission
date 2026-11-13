# Source notes

This app combines selected parts of two projects:

| Project | Reused parts | Kept out of the merge |
|---|---|---|
| Local `website-hunter` prototype | Slovak discovery sources, bounded safe fetching, browser rendering, optional visual classification, business/contact extraction, SQLite review queue, source metrics, and UI | Local `.venv`, SQLite data, screenshots, caches, and `.env` |
| `ViktorRasev/sc` at `4989dacd16ed5127516cfeb2835efe524462ef6d` | HTML fact extraction and public contact parsing in `leadfinder/signals.py`; signal families and weighted issue/activity/fit ranking from its specification; PageSpeed response interpretation and outreach status concepts | Czech-only discovery configuration, parallel fetching and rendering pipeline, duplicate database, generated report UI, and project-specific runtime data |

The friend project's extraction lives in `src/website_hunter/opportunities/signals.py` and runs inside Website Hunter's existing safe pipeline. Its Viktor scoring weights and lead-score formula are adapted for Slovak business labels and split into a fast HTML pass followed by optional selected-site browser and AI analysis. PageSpeed remains an on-demand per-candidate action; no API key is bundled.

The target repository was empty when integration began. This file records the friend source commit used for the merge.
