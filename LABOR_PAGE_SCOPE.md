# Labor Page — Thickening Scope

**Route:** `/labor/` (already live — statewide UR/payrolls + 10 CES supersectors)
**Status:** Scoped 2026-06-03 · Phase 4, WS3 (thicken thin statewide pages)
**One-line:** Add a 159-county unemployment choropleth, a 14-metro labor comparison, and a
sector-diffusion (employment breadth) layer — all from data already pulled `live`, no new
external sources.

---

## Why this is cheap

The statewide page already has the headline state series (BLS LAUS UR/LF + CES supersector
payrolls via `scripts/fetch_labor_state.py` → `data/labor.json`). Three additions need **no
new pipeline**:

1. **159-county unemployment map** — `data/counties.json` is *already* refreshed nightly by
   `scripts/fetch_bls_laus.py` (12 monthly LAUS frames for all 159 counties) and powers the
   `/counties/` animated choropleth. The labor page simply fetches it and reuses
   `window.gaMaps.drawGATimeChoropleth` — zero new data, zero new API cost.
2. **Metro labor comparison** — every `data/msa_reports/*.json` carries `live`
   `laus_unemployment` (metro UR) and `ces_employment` (total nonfarm + YoY). A pure local
   roll-up across the 14 reports, exactly like the Housing/GDP roll-ups.
3. **Sector diffusion** — the standard labor-economics *breadth* measure: the share of
   employment supersectors growing year-over-year. Statewide it's derived from the 10 CES
   supersectors already in `labor.json`; per-metro it's derived from each report's
   `ces_by_supersector.sectors[*].latest_yoy`.

---

## Data sources (all already live)

| Source | Where | Drives |
|---|---|---|
| `data/counties.json` (BLS LAUS, 159 counties × 12 mo) | refreshed by `fetch_bls_laus.py` in `update-labor.yml` | County UR animated choropleth + lowest/highest leaderboard |
| `laus_unemployment` per metro | `data/msa_reports/*.json` | Metro comparison: latest UR |
| `ces_employment` per metro | `data/msa_reports/*.json` | Metro comparison: total nonfarm level + YoY |
| `ces_by_supersector.sectors` per metro | `data/msa_reports/*.json` | Metro diffusion (breadth) column |
| `sectors[]` statewide | `data/labor.json` (existing CES pull) | Statewide diffusion headline |

---

## New `data/labor.json` keys

```jsonc
"metro_labor": [
  { "slug": "atlanta", "short_name": "Atlanta", "cbsa": "12060", "population": 6307261,
    "unemployment_rate": 3.3, "ur_as_of": "2026-03",
    "nonfarm_k": 3124.2, "nonfarm_yoy_pct": 0.33, "nonfarm_as_of": "2026-04",
    "diffusion_pct": 43.0, "n_sectors": 14 }      // diffusion null when n_sectors < 6
  // … 14 metros, sorted by population desc
],
"sector_diffusion": {
  "statewide_pct": 20.0,        // share of the 10 CES supersectors growing YoY
  "n_growing": 2, "n_total": 10,
  "growing":  ["Education & Health Services", "Other Services"],
  "shrinking":["Construction", "Manufacturing", …],
  "as_of": "2026-04"
},
"_meta": {
  "metro_labor":     { "last_updated": "…Z", "n_metros": 14, "stale": false },
  "sector_diffusion":{ "last_updated": "…Z", "stale": false }
}
```

The existing state series (`unemployment_rate`, `total_payrolls_k`, `labor_force_k`,
`sectors`, `kpis`) are untouched.

---

## Page additions (`labor/index.html`)

- Add Plotly + `/maps.js` to the `GEN:HEAD` block (the page is currently Chart.js-only) so
  it can draw the county choropleth, mirroring `/gdp/`.
- **Section: "Unemployment across all 159 counties"** — animated `drawGATimeChoropleth` from
  `counties.json` + a lowest/highest leaderboard (`renderLeaderboards`). A short note links
  to `/counties/` for the full county view.
- **Section: "Metro labor markets compared"** — sortable table of the 14 metros (UR, total
  nonfarm, YoY, diffusion) + a UR-vs-employment-growth scatter or a simple sorted bar.
- **Sector-diffusion callout** — a KPI + one line of plain-English interpretation
  ("2 of 10 sectors are adding jobs year-over-year — a narrow, late-cycle labor market").
- Pending/stale guards on the new sections (reuse the `pending`/`stale-badge` pattern from
  `/gdp/` so a missing roll-up degrades gracefully).

---

## Wiring & degradation

- `fetch_labor_state.py` gains a `--rollup` mode (local reads only, no BLS key): recompute
  `metro_labor` + `sector_diffusion` from `data/msa_reports/*.json` + the prior
  `data/labor.json`, preserving the live state series. Full runs do the BLS pull **and** the
  roll-up.
- Add a `python3 scripts/fetch_labor_state.py --rollup` step to `update-msa-reports.yml`
  (after the metro JSONs regenerate) so the metro comparison stays fresh against the latest
  reports — the same placement as the Housing/GDP roll-ups. `update-labor.yml` keeps the full
  monthly pull.
- Each roll-up section is wrapped in try/except and preserves the prior value (without
  bumping `last_updated`) on failure, rendering a `stale` badge when > 6 months old.

---

## Acceptance

- `/labor/` shows the county UR map, the 14-metro comparison, and the diffusion callout.
- `python scripts/fetch_labor_state.py --rollup` runs offline and refreshes only the two new
  blocks, leaving the live state series intact.
- HTML lints clean; the page degrades gracefully if `metro_labor`/`sector_diffusion` are
  absent.
