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

## Sequencing & progress

1. ✅ **Housing page** (WS1) — DONE. `HOUSING_PAGE_SCOPE.md`; `/housing/`, `scripts/fetch_housing.py`. Statewide GA HPI + 159-county ACS map + non-metro aggregate.
2. ✅ **State GDP page** (WS1) — DONE. `STATE_GDP_PAGE_SCOPE.md`; `/gdp/`, `scripts/fetch_gdp.py`. SAGDP statewide + CAGDP2 county + SE peers + sectors + non-metro.
3. ✅ **Migration page** (WS2) — DONE. `MIGRATION_PAGE_SCOPE.md`; `/migration/`, `scripts/fetch_migration.py` (+ `fetch_state_flows()` in `pull_irs_soi.py`). IRS SOI state flows + 159-county net-migration map + metro attraction.
4. ✅ **Forecasts/Outlook hub** (WS2) — DONE. `FORECASTS_PAGE_SCOPE.md`; `/outlook/`, `scripts/fetch_forecasts.py`. Reuses `business_cycle_index` + `forecast_arima` helpers on GA actuals; cycle index + 5-yr forecast + metro roll-ups. Carries a model-projection disclaimer.
5. ✅ Thicken **Labor + Trade** (WS3) — DONE 2026-06-03. `LABOR_PAGE_SCOPE.md` /
   `TRADE_PAGE_SCOPE.md`.
   - **Labor**: `fetch_labor_state.py` gained a `--rollup` mode (keyless local read) →
     `metro_labor` (14-metro UR + nonfarm + YoY + sector breadth) + `sector_diffusion`
     (statewide employment breadth). `/labor/` adds a 159-county UR choropleth (reuses
     `data/counties.json` via `drawGATimeChoropleth`), a click-to-sort metro comparison
     table, and a diffusion callout. Rollup folded into `update-msa-reports.yml`; full
     monthly pull stays in `update-labor.yml`.
   - **Trade**: `fetch_trade.py` gained `build_exports_annual()` (6-yr total-export trend +
     CAGR) and `build_exports_by_commodity()` (HS2 chapter breakdown), both Census
     USA Trade Online. `/trade/` adds the multi-year trend + commodity bars. Runs in the
     existing `update-trade.yml` (no topology change; ITA MSA exports still out of scope).
6. ◻ **Consumer** stub (WS1, needs new pipelines: GA DoR sales tax, Georgia Power demand) + connective tissue (WS4). ← resume here.

All four new pages fold their roll-up into `update-msa-reports.yml` (after the metro
reports regenerate) and commit `data/{housing,gdp,migration,outlook}.json`. The home-grid
cards are flipped to "Live now"; GDP/Migration/Outlook are home-grid-only (not top nav).
All `update-*.yml` workflows were hardened with `fetch-depth: 0` + rebase-and-retry push
to survive concurrent-push races.

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

**Done (2026-06-02 session):** Housing, State GDP, Migration, Outlook — all four pages
built, wired, and shipped; all `update-*.yml` workflows hardened.

**Done (2026-06-03 session):** WS3 — Labor + Trade thickened (see sequencing item #5).
Labor: 159-county UR map (reuses `data/counties.json`), 14-metro comparison table, sector
diffusion; `fetch_labor_state.py --rollup` folded into `update-msa-reports.yml`. Trade:
multi-year export trend + HS2 commodity breakdown via Census, in `update-trade.yml`. Both
pages lint clean, JS syntax-checked, roll-ups validated offline. On push, `update-labor.yml`
and `update-trade.yml` fire (path filters match the touched scripts) and populate the live
Census/BLS sections within minutes; the metro_labor + sector_diffusion blocks are already
committed with real values from the local `--rollup`.

**Next: WS1 Consumer stub + WS4 connective tissue.**
- **Consumer** stub — needs NEW pipelines: GA Dept. of Revenue sales-tax collections,
  Georgia Power residential demand, retail proxies. No existing pipeline — the only Phase 4
  item that needs genuinely new data. Flips the last "Coming soon" home card.
- **WS4 connective tissue** — full cross-metro comparator (extend `/msa/` radar to all 33
  metrics), email/RSS refresh alerts. (Scorecard + site search already shipped in Phase 5.)

**Trade follow-up (deferred, not blocking):** Port of Savannah TEU, Brunswick autos, and ATL
Hartsfield cargo are still calibrated fixtures — a Tavily/GPA-press-release scraper is the
remaining live-wiring task for `/trade/`.

Reuse the established playbook: scope doc → `scripts/fetch_<topic>.py` (with `--rollup`
local validation) → `/<topic>/index.html` (reuse Housing/GDP choropleth + pending/stale
patterns) → fold into `update-msa-reports.yml` → flip/add home card → verify lint + JSON
keys → hand over the rebase-first push block.
