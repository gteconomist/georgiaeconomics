# Housing Page — Build Scope

**Route:** `/housing/` (the empty `housing/` dir already exists)
**Status:** Scoped 2026-06-02 · Phase 4, WS1, item #1
**One-line:** A statewide Georgia housing page assembled from data already pulled `live`
across all 14 MSA reports, plus a thin statewide layer. Flips the home-page "Coming soon"
Housing card to "Live now."

---

## Why this one first

Almost zero new data. Every metro report already carries six `live` housing sections, and
`data/msa.json` already rolls up `home_price_yoy` and `permits_per_1k` with statewide
medians. The reporting/modeling helpers exist (`reporting/pull_fhfa.py`,
`reporting/pull_bps.py`, `modeling/housing_valuation.py`, `modeling/housing_affordability.py`).
This is assembly + presentation, not a new pipeline. It also closes the Housing
Affordability item deferred on the Savannah report in Phase 2.

---

## Data sources (what's already live, by section)

Per-metro, present in all 14 `data/msa_reports/*.json` (verified on `atlanta.json`):

| Section | Key fields | Drives |
|---|---|---|
| `fhfa_hpi` | `quarters[]`, `values[]`, `yoy_pct[]`, `latest_value`, `latest_yoy` | Home-price trend + YoY |
| `census_bps_permits` | `single_family[]`, `multi_family[]`, `permits_per_1k[]`, by year | Supply / construction |
| `acs_affordability` | `affordability_index[]`, `msa_rent_burden_pct[]`, `msa_median_income[]` | Rent burden vs US |
| `housing_affordability` | `affordability_index[]` (NAR-style), `anchor.median_home_value`, `assumptions` | Buyer affordability |
| `housing_valuation` | `valuation_pct[]`, `hpi_index[]`, `fair_value_index[]`, `price_to_income_ratio`, `price_to_rent_ratio` | Over/under-valuation |
| `acs_housing_characteristics` | `pct_owner_occupied`, `pct_renter_occupied`, `pct_vacant`, `pct_1unit_detached`, `pct_multifamily`, `total_housing_units` | Housing stock mix |

New statewide layer (small):

- **GA statewide FHFA HPI** — FRED `GASTHPI` (all-transactions) or state purchase-only,
  quarterly. Extends the existing `pull_fhfa` pattern (currently MSA-only via
  `ATNHPIUS{cbsa}Q`). Gives a "Georgia overall" trend line + US comparison.
- **GA statewide building permits** — already available via `pull_bps` (Census BPS via
  FRED) at state level; sum/rollup for a statewide permits trend.
- **County permits / county HPI** — optional choropleth reusing `counties.json` plumbing.

No section requires a brand-new external source. `GASTHPI` is the only new series ID.

---

## Page layout (`/housing/index.html`)

Mirror the existing topic-page template (header nav, hero, KPI strip, chart sections,
methodology footer, staleness badges). Sections top to bottom:

1. **KPI strip** — statewide median home-price YoY, latest GA HPI level, statewide
   permits/1k, median affordability index, owner-occupancy %, median price-to-income.
   (All derivable from the rollup; mirror `msa.json` `statewide_medians` style.)
2. **Home prices over time** — GA HPI line + US line, with a metro selector to overlay
   any MSA's `fhfa_hpi`. Quarterly, ~10-yr window.
3. **Metro home-price comparison** — bar/sorted ranking of all 14 metros by `latest_yoy`
   (reuse `msa.json` `home_price_yoy`), GA median reference line.
4. **Affordability** — dual view: NAR-style buyer affordability (`housing_affordability`)
   and rent burden vs US (`acs_affordability`). Metro-switchable.
5. **Valuation / fair-value gap** — `housing_valuation` HPI vs fair-value index, plus a
   metro scatter of `price_to_income_ratio` vs `price_to_rent_ratio` (which metros look
   stretched). Label as EIG model, carry the existing method note.
6. **Supply & construction** — `census_bps_permits` single- vs multi-family stacked bars,
   statewide + metro; permits/1k trend.
7. **Housing stock mix** — `acs_housing_characteristics`: owner/renter/vacant split,
   single-family-detached vs multifamily, by metro (small-multiples or sortable table).
8. **Methodology** — sources (FHFA via FRED, Census ACS 5-yr, Census BPS, EIG models),
   ACS 5-year note (per `feedback_acs_5year_preferred`), staleness behavior.

Charts: shared Chart.js via jsDelivr (existing pattern); county choropleth via the
existing Plotly/`maps.js` plumbing if we add the county layer.

---

## Data wiring — `scripts/fetch_housing.py` → `data/housing.json`

The fetch script is mostly a **roll-up reader**, not a fresh puller:

1. Read all 14 `data/msa_reports/*.json`; extract the six housing sections per metro into
   a compact per-metro housing block (don't duplicate full history we don't render —
   keep HPI quarterly history + the latest-year arrays we chart).
2. Compute `statewide_medians` and rankings across metros (mirror `msa.json`).
3. Pull the **one new series** — GA statewide HPI (`GASTHPI`) via the `pull_fhfa` FRED
   helper — plus statewide permits via `pull_bps`.
4. Emit `data/housing.json` with: `_meta` (per-section `last_updated` + staleness),
   `kpis`, `ga_hpi`, `metros[]` (per-metro housing block), `statewide_medians`,
   `valuation_scatter`, `source_summary`, `fetched_at`.
5. Graceful degradation per the house convention (preserve prior values, don't bump
   `last_updated`, badge if >6 months stale).

`data/housing.json` shape (sketch):

```json
{
  "_meta": { "fhfa": {"last_updated": "..."}, "permits": {...}, ... },
  "fetched_at": "...", "latest_label": "...",
  "kpis": { "ga_home_price_yoy": ..., "median_affordability_index": ..., "permits_per_1k": ..., "pct_owner_occupied": ... },
  "ga_hpi": { "quarters": [...], "values": [...], "us_values": [...], "yoy_pct": [...] },
  "statewide_medians": { "median_home_price_yoy": ..., "median_permits_per_1k": ..., ... },
  "metros": [ { "short_name": "Atlanta", "cbsa": "12060",
                "fhfa_hpi": {...}, "permits": {...}, "affordability": {...},
                "valuation": {...}, "stock": {...} }, ... ],
  "valuation_scatter": [ {"metro": "...", "price_to_income": ..., "price_to_rent": ...}, ... ]
}
```

---

## Automation — `.github/workflows/update-housing.yml`

- Cron monthly (pick a day not already congested; the MSA-reports workflow regenerates
  the source JSONs nightly, so housing just needs to re-roll after those).
- Secrets: `FRED_API_KEY` (already in repo). Respect the 120/min FRED limit / 429 backoff
  already built into `pull_fhfa` (per `reference_fred_rate_limit`).
- Steps: run `fetch_housing.py`, commit `data/housing.json` if changed.
- **Ordering option:** fold the housing roll-up into `update-msa-reports.yml` after the
  per-metro reports regenerate, so it never reads stale metro JSONs. Decide at build time.

---

## Build checklist

- [ ] `scripts/fetch_housing.py` — roll-up reader + GA `GASTHPI`/statewide permits pull
- [ ] `data/housing.json` — generated, with `_meta` staleness scaffolding
- [ ] `housing/index.html` — 8 sections per layout, topic-page template + nav entry
- [ ] Add "Housing" to header nav across pages (currently nav = Home/Counties/Metros/Labor/About)
- [ ] Flip the home-page card from "Coming soon" → "Live now" with a real link
- [ ] `.github/workflows/update-housing.yml` (or fold into `update-msa-reports.yml`)
- [ ] Update `REPORT_STATUS.md` / `REPORT_STATUS_MATRIX.md`
- [ ] Mark the Savannah housing-affordability deferral resolved

## Acceptance criteria

- `/housing/` renders 8 live sections with real data for all 14 metros + a statewide view.
- No "Demo"/fixture leakage — every charted series traces to a `live` MSA section or the
  GA HPI/permits pull; sections with no data show a staleness/"no data" state, not fakes.
- Home-page Housing card reads "Live now" and links to `/housing/`.
- Workflow runs green and commits `data/housing.json` on a monthly cadence.
- Page passes the existing `lint-html.yml` check.

## Coverage decision (LOCKED 2026-06-02)

The 14 MSAs cover **73 of 159 GA counties** (~82% of population); **86 counties are
non-metro**. v1 ships **full state coverage**: statewide GA HPI headline + 14-metro
roll-up + an **all-159-county ACS layer** (median home value, ownership, rent burden →
choropleth) + a **"Non-Metro Georgia" aggregate** (population-weighted over the 86
counties). Implemented in `scripts/fetch_housing.py`.

Build note: the county **choropleth is ACS-driven** (one reliable keyed Census call).
Per-county **permits** are a **best-effort** add from the public Census BPS county annual
file (no key; runs in CI) with graceful degradation to statewide/metro permits if the
file is unreachable — so the page is never blocked on it.

## Remaining open questions

1. **Workflow:** standalone `update-housing.yml`, or fold the roll-up into the nightly
   `update-msa-reports.yml` so it always reads fresh metro JSONs? (Leaning: fold in.)
2. **Rentals:** ACS gives rent burden + median gross rent. Want a market-rent series
   (e.g., a Zillow ZORI-style source) in v1, or defer that to Consumer/a later pass?
