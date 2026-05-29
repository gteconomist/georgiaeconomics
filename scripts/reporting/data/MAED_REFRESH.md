# Refreshing the ITA Metropolitan Area Exports (MAED) cache

The Metropolitan Area Export Data is **not exposed as a REST API**. The only public access path is the Tableau dashboard at `tsereports.trade.gov`, which serves an annual snapshot via a manual "Download Data" button. Building a programmatic scraper for that dashboard is real engineering and the data updates only once a year, so we ship an **annual manual refresh** instead.

This file lives next to the CSV cache for that reason — when you come back in 12 months you'll find these instructions right where you need them.

## When to refresh

**Mid-to-late November each year**, after ITA publishes the new annual data. Confirm by checking [git pull --rebase origin main
git push) — the page footer reads e.g. "Last updated: November 2025 with 2024 annual data". Refresh once that footer year ticks over.

The MSA reports gracefully degrade when the cache is missing or stale (the orchestrator's never-blank-on-failure logic renders prior-run values), so missing a month isn't a blocker.

## How to refresh (≈ 5 min)

1. Open [tsereports.trade.gov/views/MetroBulkDownload/MetropolitanAreaExports](https://tsereports.trade.gov/views/MetroBulkDownload/MetropolitanAreaExports) in any browser. Wait for the dashboard to finish loading (the spinner stops; the table populates).

2. **Clear all filters.** In the right-side panel, every filter (Dataset, Year, MSA, Destination) should be set to **All**. If any filter shows a specific value, click it and choose "(All)".

3. Click the orange **Download Data** button (also on the right side panel).

4. In the modal that appears, select **Crosstab** at the top (not Summary or Data).

5. Under "Select sheet from dashboard", pick **Metro Bulk Table** (the worksheet, not the dashboard).

6. Click **Download**. You'll get a `.csv` file (typically 30–60 MB; 168K+ rows covering 421 MSAs × 20 years × 83 destinations × multiple NAICS sectors).

7. **Rename it** to match the latest data year. If the dashboard footer says "Last updated: November 2026 with 2025 annual data", rename to `maed_2025.csv`.

8. Move it to `scripts/reporting/data/maed_{year}.csv` in this repo.

9. Commit and push:
   ```bash
   cd ~/Documents/Claude/Projects/Georgia\ Economics
   git add scripts/reporting/data/maed_2025.csv
   git commit -m "data: refresh MAED with 2025 annual exports"
   git push
   ```

`pull_ita.py` will auto-discover the newest `maed_*.csv` file in this directory. Older files can stay (useful for diff/comparison) or be deleted.

## Expected CSV shape

Header row: `Dataset,MSA Full Name,NAICS Code,NAICS Sector,Destination,2005,2006,...,{latest_year}` — annual columns are wide.

Cells contain dollar values in millions. The string `D` denotes suppressed data (Census disclosure rules); `pull_ita.py` treats `D` as `None`.

## What pull_ita.py uses

For each Georgia MSA (matched by name), it filters to the latest year column and pulls:
- **total_usd_millions** — sum across all NAICS sectors for `Dataset = "All MSAs - Exports to World"` × `Destination = "World"`
- **by_destination** — top 10 destinations from `Dataset = "All MSAs - Exports to Select Regions/Trading Groups"`
- **by_product** — top 10 NAICS-3 sectors from `Dataset = "All MSAs - Top 5 Exported Sectors (NAICS-3)"`

## If the dashboard URL or worksheet name changes

ITA has bumped their Tableau Server twice in the last 6 months. If step 1 above 404s:
- Start at [trade.gov/ita-metropolitan-export-series](https://www.trade.gov/ita-metropolitan-export-series), click "Bulk Download Tool" — that link follows redirects to the current Tableau dashboard.
- In step 5, the worksheet picker shows current sheet names — pick the one that obviously contains the data table (likely still "Metro Bulk Table" but could be renamed).
- If the column structure shifts, `pull_ita.py` will start failing at parse time; update `MAED_DATASET_NAMES` in that file to match new dataset values.

## Why we don't auto-scrape

Tracked in memory as `reference-ita-exports-endpoint-dead`. Short version: MAED has no REST API on `developer.trade.gov`; the Tableau Server bulk URL requires a stateful session+commands handshake that's ~300 lines of brittle code for data that updates once a year.
