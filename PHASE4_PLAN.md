# Phase 4 — Plan of Action

**Status:** Planned (drafted 2026-06-02, after Phase 3 multi-MSA wrap)
**Theme:** Stop adding *new topics*. Surface the depth we already pull but currently bury, finish the visible "Coming soon" stubs, and thicken the thin statewide pages.

---

## The core insight

Phase 3 left the site in a lopsided state. The **14 MSA reports are extraordinarily
rich** — each `data/msa_reports/*.json` carries **33 sections, almost all `live`**,
including data nobody else publishes at metro level: IRS SOI migration flows, EPA air
quality, business formation / entrepreneurship, an ARIMA forecast, a business-cycle
index, quality-of-life, housing valuation, business costs, industrial diversity, and a
credit-score composite.

Meanwhile the **statewide topic pages are comparatively thin** — labor is 10 sectors +
KPIs, trade is a handful of port/export numbers — and three home-page cards still read
**"Coming soon"** (Housing, State GDP, Consumer).

We are computing world-class data 14 times over and only ever showing it one metro at a
time. Phase 4 is about **rolling that depth up to the state level** and closing the
visible gaps. Most of it needs little or no new data — it's assembly and presentation.

---

## Workstreams (priority order)

### WS1 — Finish the three "Coming soon" stubs

The home page (`index.html`) advertises three cards as "Coming soon." Two of them can be
built almost entirely from data we already pull every night.

| Stub | New data needed? | Notes |
|---|---|---|
| **Housing** | **None** | Roll-up of 6 housing sections already live in all 14 MSA reports (`fhfa_hpi`, `acs_affordability`, `housing_valuation`, `housing_affordability`, `acs_housing_characteristics`, `census_bps_permits`) + a statewide GA FHFA series. Also closes the Savannah housing-affordability item deferred in Phase 2. **Scoped separately in `HOUSING_PAGE_SCOPE.md` — this is the Phase 4 starting point.** |
| **State GDP** | Minimal | `bea_gmp` + `bea_personal_income` are `live` per metro; pull GA statewide BEA SAGDP + add SE-peer comparison (FL/NC/SC/TN/AL). Output, per-capita, sector composition. |
| **Consumer** | **Yes (genuinely new)** | GA Dept. of Revenue sales-tax collections, Georgia Power residential demand, retail proxies. No existing pipeline — do this last. |

**Acceptance:** each "Coming soon" card flips to "Live now" and links to a real page that
follows the existing topic-page template (fetch script → `data/<topic>.json` → page +
`update-<topic>.yml` cron, graceful degradation + staleness badges).

### WS2 — Surface buried MSA depth as statewide views

We already compute these per metro; the data only ever appears one metro at a time. Build
statewide roll-up pages that read across all 14 `data/msa_reports/*.json`.

- **Migration page** — IRS SOI county inflow/outflow (`irs_soi_migration` section) as a
  statewide flow map: who's moving to Georgia and where within it. Distinctive; nobody
  else surfaces this for GA. Pairs with the existing Population page.
- **Forecasts / business-cycle hub** — `forecast_arima` + `business_cycle_index` are live
  per metro. A statewide forecast/leading-indicator page is genuinely unique.
- **Entrepreneurship / business formation** — from the `entrepreneurship` section.
- (Stretch) **Air quality / environment** layer from `epa_air_quality`.

### WS3 — Thicken the thin statewide pages

Use data already in hand.

- **Labor** — fold in the county LAUS layer (`fetch_bls_laus.py` + `counties.json`) and a
  metro labor comparison; add sector diffusion (already computed for MSA reports).
- **Trade** — add multi-year export trends and a commodity breakdown rather than just
  top-country; consider the import side. (Note: ITA exports endpoint partially blocked —
  see `reference_ita_exports_endpoint_dead`.)

### WS4 — Connective tissue

- **"Economy at a glance"** statewide scorecard rolling up headline KPIs across topics.
- **Site search + economics glossary.**
- **Email/RSS alert** when data refreshes (cron infra already exists).
- **Full cross-metro comparator** — extend the `/msa/` radar to compare any metros across
  all 33 metrics, not just the 6 in `msa.json`.

---

## Sequencing

1. **Housing page** (WS1) — starting point, scoped in `HOUSING_PAGE_SCOPE.md`.
2. **State GDP page** (WS1) — next-easiest stub, data largely in hand.
3. **Migration page** (WS2) — first "unbury the MSA depth" win, high distinctiveness.
4. **Forecasts hub** (WS2).
5. Thicken Labor + Trade (WS3).
6. Consumer stub (WS1, needs new pipelines) + connective tissue (WS4).

## Conventions to preserve (all Phase 4 work)

- Topic page = `scripts/fetch_<topic>.py` → `data/<topic>.json` → `/<topic>/index.html`,
  driven by `.github/workflows/update-<topic>.yml` on a monthly cron.
- Reuse `scripts/reporting/` pullers (`pull_fhfa`, `pull_bps`, `pull_bea`, `pull_census`,
  …) and `scripts/modeling/` helpers rather than re-implementing.
- Graceful degradation: wrap each section in try/except, preserve prior values on
  failure, don't bump `_meta.<section>.last_updated`, render a "stale" badge when a
  section is >6 months old.
- Full automation only — no "manually update annually" steps (per `feedback_full_automation`).
- Deploy via local git push, not the GitHub MCP (per `feedback_no_mcp_deploys`).
- Keep `REPORT_STATUS.md` / `REPORT_STATUS_MATRIX.md` current as pages ship.

---

## ▶ RESUME HERE (Phase 4)

Start with the **Housing page** per `HOUSING_PAGE_SCOPE.md`. It closes a visible
"Coming soon" gap, the underlying data is already `live` across all 14 metros, and it
reuses the proven topic-page template end to end.
