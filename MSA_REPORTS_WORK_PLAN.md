# Georgia MSA Metro Economic Profile — Work Plan

**A product of Economic Impact Group, LLC**

**Goal:** Build a full "Metro Economic Profile" report for each of the 14 Georgia MSAs, served as a live HTML page at `/msa/<slug>/` and downloadable as a PDF via the browser's print dialog. The example for Savannah is live at `/msa/savannah/`.

Every section maps to a free, public source (BLS / BEA / Census / FHFA / FRED / IRS SOI / ITA). Where a section needs a composite or modelled measure, Economic Impact Group publishes its own methodology rather than relying on any third-party proprietary index.

---

## STATUS AT A GLANCE (as of 2026-05-26)

| Phase | Status | Notes |
|---|---|---|
| **0 — Example page** | ✅ Shipped | `/msa/savannah/` live, full Précis-style layout, brand-correct |
| **1 — Data pipeline & template** | ✅ Shipped & verified in production | First production run (2026-05-26) completed cleanly: **10 of 13 sections returned `live`** with current data. The 3 failures (BPS / ITA / IRS SOI) are documented Phase 1.5 fixes — see section 4. |
| **1.5 — Polish & loose ends** | ⏳ Backlog | See "Phase 1.5 follow-ups" below — small, do whenever |
| **2 — Composite metrics + forecasts** | 🚧 Next session | This is the big remaining build. Brief below in section 8. |
| **3 — LLM narrative pipeline** | ⏳ Planned | Anthropic + Tavily, generates analysis prose from JSON |
| **4 — Geographic profile maps** | ⏳ Planned | Plotly choropleth, block-group level |
| **5 — Roll out to other 13 MSAs** | ⏳ Planned | Template refactor; mostly mechanical |
| **6 — Stored-PDF artifact** | ⏳ Optional | Playwright renders nightly; print-to-PDF already works |

---

## 1. Section-by-section data mapping

| # | Report section | Public-data source | Cadence | Composite needed? | Phase 1 status |
|---|---|---|---|---|---|
| 1 | Header: CBSA, counties, pop | OMB delineation + Census PEP | Annual | No | ✅ Live |
| 2 | Economic drivers (top 2 LQ industries) | BLS QCEW location quotients | Quarterly | No | Phase 2 — compute from `qcew_industry_shares` |
| 3 | Employment-growth rank (2-yr & 5-yr) | BLS CES MSA employment | Monthly | No — rank vs. 387 metros | Phase 2 — needs national MSA scan |
| 4 | Relative costs (living / business) | BLS RPP | Annual | Partial | Phase 2 |
| 5 | Vitality index | LFPR + earnings + young-adult + migration | Quarterly | **EIG composite** | Phase 2 |
| 6 | Quality-of-life composite | ACS + EPA + FBI UCR + commute | Annual | **EIG composite** | Phase 2 |
| 7 | Business cycle status (chart) | BLS CES + LAUS | Monthly | **EIG composite** (Stock-Watson) | Phase 2 |
| 8 | Strengths / weaknesses bullets | Derived from data + LLM | On data refresh | Hybrid LLM | Phase 3 |
| 9 | Forecast risks (upside / downside) | Derived + Tavily news scrape | Weekly | Hybrid LLM | Phase 3 |
| 10 | EIG credit score | EMMA bond filings + EIG composite | Quarterly | **EIG composite** | Phase 2 |
| 11 | Recent-performance prose | LLM from latest data | Monthly | Hybrid LLM | Phase 3 |
| 12 | Headline indicators table (history) | CES + LAUS + GMP + PI + PEP + BPS + HPI | Quarterly | Historical: ✅ Live; forecast: Phase 2 ARIMA | Partial ✅ |
| 13 | Economic health check (6-mo grid) | CES + LAUS + BPS monthly | Monthly | No | ✅ Data live; table swap in Phase 1.5 |
| 14 | Business-cycle index chart | Composite | Monthly | **EIG composite** | Phase 2 |
| 15 | Industry employment % YoY | BLS QCEW super-sector | Quarterly | No | ✅ Live |
| 16 | Current employment trends table | BLS CES super-sector | Monthly | No | Partial — currently CES totals only |
| 17 | Diffusion index | BLS CES 3-digit NAICS | Monthly | No — compute share | Phase 1.5 |
| 18 | Relative employment performance (chart) | BLS CES rebased | Monthly | No | Phase 1.5 |
| 19 | Relative employment forecast (arrows) | EIG VAR vs. prior vintage | Quarterly | **EIG composite** | Phase 2 |
| 20 | House price index | FHFA HPI quarterly | Quarterly | No | ✅ Live |
| 21 | Rental affordability | ACS B25064 ÷ B19013 | Annual | No | Phase 1.5 — derive from existing ACS pull |
| 22 | Over/undervalued housing | HPI residual model | Quarterly | **EIG composite** | Phase 2 |
| 23 | Housing affordability (PITI/income) | Realtor.com + Freddie + ACS | Monthly | No | Phase 1.5 |
| 24 | Top employers list | Local development authorities | Annual | Hand-maintained | Phase 1.5 — annual hand-update |
| 25 | Industrial diversity (Herfindahl) | BLS QCEW employment shares | Annual | No — compute | Phase 1.5 — derive from `qcew_industry_shares` |
| 26 | Entrepreneurship (broad-based startup) | Census BFS | Monthly | No | Phase 1.5 |
| 27 | Exports (by product & destination) | ITA Metropolitan Area Export Data (api.trade.gov via api.data.gov key) | Annual | No | ✅ Built; verifying endpoint in CI |
| 28 | Public sector employment (Fed/State/Local) | BLS QCEW ownership splits | Quarterly | No | Phase 1.5 — extend QCEW fetcher |
| 29 | Productivity ($ output / worker) | BEA GMP ÷ BLS employment | Annual | No — compute | ✅ Live (both inputs) |
| 30 | Business costs (U.S.=100) | BLS RPP + Tax Foundation + commercial rent | Annual | **EIG composite** | Phase 2 |
| 31 | High-tech employment | BLS QCEW NAICS 5112+5182+5415+5417 | Quarterly | No | Phase 1.5 — extend QCEW fetcher |
| 32 | Leading industries by wage tier | BLS QCEW LQ + avg wages | Quarterly | No | Phase 1.5 — extend QCEW fetcher |
| 33 | Block groups by income | Census ACS 5-yr, B19013 | Annual | No | Phase 1.5 |
| 34 | Gini / inequality | Census ACS B19083 | Annual | No | ✅ In ACS pull (`gini_coefficient`) |
| 35 | Migration flows (top 10 in/out) | IRS SOI, CBSA-aggregated | Annual | No | ✅ Built; verifying ZIP access in CI |
| 36 | Per-capita income (chart) | BEA Regional CA1 | Annual | No | ✅ Live |
| 37 | Households by income (histogram) | Census ACS B19001 | Annual | No | Phase 1.5 |
| 38 | Commuter flows in/out | Census LEHD LODES | Annual | No | Phase 1.5 |
| 39 | Net migration (annual stacked bar) | Census PEP components-of-change | Annual | No | Phase 1.5 |
| 40 | Generational breakdown | Census ACS B01001 | Annual | No | Phase 1.5 — derive from age table |
| 41 | Educational attainment | Census ACS B15003 | Annual | No | ✅ In ACS pull |
| 42 | Population by age (5-yr bins) | Census ACS B01001 | Annual | No | Phase 1.5 |
| 43 | Geographic profile maps | TIGER/Line + ACS, Plotly choropleth | Annual | No | Phase 4 |
| 44 | Population & housing characteristics | Census ACS DP04 + DP02 + DP05 | Annual | No | Partial ✅ |

**Bottom line:** of the 44 sections, 35 are direct pulls, 7 need simple math, and 7 require an EIG composite (vitality, QoL, business cycle index, valuation, forecast, business costs, credit score). Every section maps to a free public source — zero third-party subscription dependency.

---

## 2. Architecture (as built)

```
georgiaeconomics/
├── data/
│   └── msa_reports/                       # one JSON per MSA
│       └── savannah.json                  # ✅ exists; 8+ sections live
├── msa/
│   ├── index.html                         # ✅ comparison table + report CTA
│   └── savannah/
│       └── index.html                     # ✅ template; reads /data/msa_reports/savannah.json
├── scripts/
│   ├── _ga_msas.py                        # canonical MSA list (existed before Phase 1)
│   ├── fetch_msa_report.py                # ✅ orchestrator — 13 section runners
│   ├── reporting/
│   │   ├── __init__.py
│   │   ├── pull_bls.py                    # ✅ CES, LAUS, QCEW (shares + YoY changes)
│   │   ├── pull_fhfa.py                   # ✅ HPI quarterly (via FRED)
│   │   ├── pull_census.py                 # ✅ PEP, ACS demographics, BPS permits
│   │   ├── pull_bea.py                    # ✅ GMP, Personal Income
│   │   ├── pull_irs_soi.py                # ✅ County-to-county migration
│   │   └── pull_ita.py                    # ✅ MSA exports (via api.trade.gov)
│   ├── modeling/                          # 🚧 Phase 2 — DOES NOT EXIST YET
│   └── narrative/                         # 🚧 Phase 3 — DOES NOT EXIST YET
└── .github/workflows/
    └── update-msa-reports.yml             # ✅ nightly cron + manual dispatch
```

### Data flow (working as of end of Phase 1)

1. **Nightly cron at 09:00 UTC**: `update-msa-reports.yml` runs `python3 scripts/fetch_msa_report.py --all`
2. Orchestrator calls each registered section runner, in order, for each of 14 MSAs.
3. Each runner returns `(data_dict_or_None, status_string)`. Status: `live`, `stale`, `failed`, `pending`, or `seed`.
4. **Never-blank guarantee**: if a runner returns `failed` and the prior JSON had a value for that section, we KEEP the prior value and mark it `stale`. Only re-populated on a successful run.
5. Orchestrator writes `/data/msa_reports/<slug>.json` and commits any changes back to `main`.
6. The Savannah HTML page's `loadLiveData()` fetches the JSON on every page load, and:
   - Populates teal **Live Indicators** strip cards at the top
   - Overwrites cells in the long indicators table (tagged `data-live-cell="<id>"`) and highlights them teal
   - Rebuilds the Industry Employment Chart.js chart with QCEW YoY data
   - Replaces the Comparative Employment & Income table with QCEW shares + wages
   - Swaps the Migration Flows tables with IRS SOI county-aggregated data
   - Swaps the Exports tables with ITA MSA exports
   - Flips section status pills from `Demo` to `Live`

### Status pill semantics

| Pill | Meaning | Rendered as |
|---|---|---|
| `Live` | Fetched fresh this run from public API | Teal pill |
| `Seed` | Hand-curated value, not yet from live API | Mustard pill |
| `Cached` (status `stale`) | Prior good value, current run failed | Mustard pill |
| `Demo` | Illustrative placeholder, pipeline pending | Coral pill |
| `EIG composite` | Modelled / computed by EIG | Mustard "proxy" pill |
| `Partial` | Some fields live, others pending | Mustard pill |

---

## 3. Phase 1 — what got built

### Fetchers (6 modules, 13 section runners)

| Module | Functions | Sections it powers | Status |
|---|---|---|---|
| `pull_bls.py` | `fetch_ces_employment_history`, `fetch_ces_supersector_history`, `fetch_laus_unemployment_history`, `fetch_qcew_industry_shares`, `fetch_qcew_yoy_changes` | 12 (history), 13 (health check), 15 (industry %YoY), 16 (current trends), 18 (rel employment), 32 (leading industries) | ✅ Working in CI |
| `pull_fhfa.py` | `fetch_hpi_quarterly_history` | 20 (HPI chart), historical HPI row in indicators table | ✅ Working in CI |
| `pull_census.py` | `fetch_pep_population_history`, `fetch_acs_demographics`, `fetch_bps_permits_annual` | 1 (pop), 12 (table), 21 (rental aff), 33-34 (block group / Gini), 37 (HH income), 40-42 (age/edu), 44 (housing chars) | ✅ Working in CI (BPS timeout fix this commit) |
| `pull_bea.py` | `fetch_gmp_history`, `fetch_personal_income_history` | 12 (GMP, PI rows in table), 29 (productivity), 36 (per-cap income) | ✅ Working in CI |
| `pull_irs_soi.py` | `fetch_migration_flows` | 35 (migration flows) | ✅ Built; first CI verification pending |
| `pull_ita.py` | `fetch_msa_exports` | 27 (exports panel) | ✅ Built; defensive against 3 candidate endpoints — first CI run will reveal which works |

### Orchestrator (`scripts/fetch_msa_report.py`)

- **13 section runners** registered, all built (zero stubs).
- **Never-blank-on-failure**: reads prior JSON, keeps stale values when current pull fails.
- **Freshness stamp** prints inline: `[ces_employment] live (2026-04)` so each CI log immediately shows how fresh each section is.
- **CLI**: `python3 scripts/fetch_msa_report.py savannah` (single MSA), `--all` (all 14), `--sections name1,name2` (selective).
- **Status summary**: prints `live / seed / stale / pending / failed` counts at the end of each MSA.

### GitHub Action (`.github/workflows/update-msa-reports.yml`)

- Nightly cron `0 9 * * *` (09:00 UTC, post most US economic-release windows).
- Manual dispatch with `target` (CBSA or short_name) and `sections` (comma-separated) inputs.
- Env vars: `BLS_API_KEY`, `FRED_API_KEY`, `CENSUS_API_KEY`, `BEA_API_KEY`, `ITA_API_KEY`.
- Commits any changes back to `main` as `github-actions[bot]`.

### Savannah page (`msa/savannah/index.html`)

- ~80 KB single-file template (will be reused for the other 13 MSAs in Phase 5).
- `loadLiveData()` async-fetches `/data/msa_reports/savannah.json` and overlays live values onto the static fallback content.
- 14 `data-live-cell` hooks in the indicators table — each cell flips teal when JSON populates.
- 7+ live cards in the top "Live Indicators" strip when sections are populated.
- 4 chart/table swap helpers: `replaceIndustryEmploymentChart`, `replaceComparativeEmploymentTable`, `replaceMigrationTables`, `replaceExportsTables`.
- "Layout Preview" banner with Live/Partial/Demo/EIG composite legend.
- Forecast columns intentionally em-dashes with "Forecast model pending — Phase 2" annotation.
- `@media print` CSS so browser "Save as PDF" produces a clean multi-page document.
- Zero Moody's references; branded "A product of Economic Impact Group, LLC" throughout.

---

## 4. Phase 1.5 — known follow-ups (small, do whenever)

These are loose ends from Phase 1 — not blockers, but worth cleaning up in spare moments or as part of Phase 5 polish.

### Confirmed Phase 1 failures (3 of 13 sections, 2026-05-26 CI run)

These three section runners returned `failed` in the first production run. Each is a known/diagnosable issue, not a code bug:

1. **`census_bps_permits failed`** — `www2.census.gov` is unreliably slow even with the 45s/2-year fail-fast fix. None of the per-year files came back in time. Real fix options:
   - Switch source to the HUD SOCDS portal (https://socds.huduser.gov/permits/) — possibly faster
   - Download the all-years single CSV if Census publishes one (e.g. `bps-history.csv`)
   - Cache fetches outside the orchestrator and only update annually
2. **`ita_msa_exports failed`** — `pull_ita.py` tries 3 candidate URL patterns at api.trade.gov; all returned empty body (`JSONDecodeError: Expecting value: line 1 column 1 (char 0)`). Diagnosis: none of `/v3/maed/search`, `/v3/metropolitan_exports/search`, or the query-string-key variant exist on api.trade.gov today. Real fix: look up the actual current MAED endpoint at https://developer.trade.gov — the dataset definitely exists, the URL just doesn't match what I guessed. Once known, prune `CANDIDATE_ENDPOINTS` in `pull_ita.py` to that one URL.
3. **`irs_soi_migration failed`** — most likely the ZIP URL pattern `https://www.irs.gov/pub/irs-soi/{YY}_to_{YZ}_county_data.zip` no longer matches the IRS's current hosting layout. Real fix: visit https://www.irs.gov/statistics/soi-tax-stats-migration-data, find the current download URL for the latest county migration ZIP, and update `_discover_latest_year()` in `pull_irs_soi.py`.

### Other follow-ups
4. **`pull_bls.py::fetch_ces_supersector_history`** currently only returns "Total nonfarm" in CI runs — investigate why other super-sectors aren't being returned (probably a series ID format issue specific to the super-sector code position) and fix.
5. **BEA "Unknown error" cosmetic noise** — first year tried (2026) returns empty Error object; fetcher falls back to 2024 successfully. Already silenced for "not available"/"no data" messages; the "Unknown error" string slips through because BEA returns an Error key with no description. Trivially fixable by also silencing when `desc == ""`.
6. **Census ACS 2025 lag** — fetcher already falls back to 2024 correctly when 2025 returns 404. No action needed until ACS 2025 1-year drops (~Sep 2026), at which point it'll auto-pick-up.
7. **Top Employers** — annual hand-update. Owned by a human, not the pipeline. Recommend yearly audit.
8. **Additional small fetchers** to fill Phase 1.5-tagged rows in the section table:
   - Census BFS (Business Formation Statistics) for entrepreneurship — row 26
   - Census LEHD LODES for commuter flows — row 38
   - Census PEP components-of-change for net-migration domestic/foreign split — row 39
   - Realtor.com + Freddie Mac PMMS join for housing affordability index — row 23
   - QCEW high-tech NAICS subset + public-sector ownership splits — rows 28, 31
9. **Derive-from-existing-data sections** (no new fetcher needed, just JS):
   - Industrial diversity Herfindahl from `qcew_industry_shares` — row 25
   - Rental affordability from existing ACS B25064/B19013 — row 21
   - Productivity = `bea_gmp.gmp_billions_usd / ces_employment.latest_value` — row 29
   - Generational breakdown + Population by age from ACS B01001 — rows 40, 42
10. **Atlanta CBSA HPI fallback** — FRED's `ATNHPIUS12060Q` series has been frozen for years; existing `scripts/fetch_msa_metrics.py` has the county-aggregate workaround. Port that fallback into `pull_fhfa.py`.

---

## 5. Required secrets / API keys (all free)

| Secret | Used by | Where to get |
|---|---|---|
| `BLS_API_KEY` | `pull_bls.py` (CES, LAUS); QCEW doesn't need it | https://data.bls.gov/registrationEngine/ |
| `FRED_API_KEY` | `pull_fhfa.py` | https://fred.stlouisfed.org/docs/api/api_key.html |
| `CENSUS_API_KEY` | `pull_census.py` (PEP, ACS) | https://api.census.gov/data/key_signup.html |
| `BEA_API_KEY` | `pull_bea.py` (GMP, Personal Income) | https://apps.bea.gov/api/signup/ |
| `ITA_API_KEY` | `pull_ita.py` (MSA exports) | https://api.data.gov/signup/ |
| `ANTHROPIC_API_KEY` | Phase 3 narrative pipeline | https://console.anthropic.com (already in repo for the film page) |
| `TAVILY_API_KEY` | Phase 3 news scraping | https://tavily.com (already in repo for the film page) |

All five Phase-1 keys are already loaded in repo secrets. **No key needed for:** Census BPS permits (public download), IRS SOI migration (public ZIP), BLS QCEW (open data).

---

## 6. Operational notes

### Trigger a manual data refresh

**GitHub → Actions → Update MSA Reports → Run workflow**

- Leave `target` blank to refresh all 14 MSAs (slow — ~15-20 min)
- Set `target` to `savannah` (or any CBSA / short_name) to refresh just one
- Set `sections` to e.g. `ces_employment,fhfa_hpi` to refresh selected sections only

### Read the orchestrator log

Each section line looks like one of:
- `[ces_employment] pulling ... live (2026-04)` — success, latest data is from April 2026
- `[bps_permits] pulling ... failed` followed by `↳ kept prior value, status=stale` — current pull failed, but we kept the prior good value
- `[qcew_industry_shares] pending (no runner yet)` — slot exists but no fetcher built yet (should be zero of these now)

Summary line at the end: `summary: 8 live, 0 seed, 0 stale (kept prior), 0 pending, 5 failed (of 13)` — quick at-a-glance health of the run.

### Verify what's actually live on the page

Visit `/msa/savannah/` in a browser, open DevTools Console:
```js
fetch('/data/msa_reports/savannah.json').then(r => r.json()).then(d => console.log(d.section_status))
```

That dumps the live/stale/pending status of every section as seen by the page.

---

## 7. Open questions

1. **Forecast methodology** (decision needed before Phase 2): true state-space VAR (more accurate, harder to maintain) or simpler ARIMA + judgment overlay? **Recommend ARIMA + Atlanta Fed consensus blend** for the first cut.
2. **Credit-score column** (decision needed before Phase 2): (a) EIG composite only; (b) EIG composite + most recent county GO rating from EMMA as reference; (c) drop the row entirely. **Recommend (b)**.
3. **PDF distribution**: are these PDFs going to be emailed to anyone, or is "Save as PDF from browser" enough? Affects whether Phase 6 is in scope.
4. **Rollout order for the other 13 MSAs** (Phase 5): default would be by population (Atlanta → Augusta → Columbus → Macon → Athens → ...). Any priority MSAs to bump up?

---

## 8. PHASE 2 PICKUP BRIEF — Composite metrics + forecast model

**This is the next session's work.** Goal: replace the 7 em-dashed / Demo proxy metrics with real computed values, plus populate the forecast columns in the indicators table.

### What to build

```
scripts/modeling/
├── __init__.py
├── forecast_arima.py          # ARIMA per series → 2026F–2030F columns
├── business_cycle_index.py    # Stock-Watson coincident index
├── vitality.py                # Z-score composite (LFPR + earnings + young-adult + migration)
├── quality_of_life.py         # ACS + EPA + FBI UCR composite, scaled 0-300
├── housing_valuation.py       # FHFA HPI residual vs. local fundamentals
├── business_costs.py          # BLS RPP + Tax Foundation + commercial rent
└── credit_score.py            # EMMA bond filings + EIG fiscal-strength composite → letter grade
```

### Per-module design intent

| Module | Inputs (already in JSON) | Method | Output added to JSON |
|---|---|---|---|
| `forecast_arima.py` | `ces_employment.values`, `laus_unemployment.values`, `bea_gmp.gmp_billions_usd`, `census_pep.population`, `fhfa_hpi.values`, `bea_personal_income.personal_income_billions_usd` | `statsmodels.tsa.arima.model.ARIMA(p,d,q)` per series with auto-order via AIC; blend with Atlanta Fed consensus where available | `forecast: {years: [2026,...,2030], <metric>: [...], <metric>_yoy: [...]}` |
| `business_cycle_index.py` | CES employment (monthly), LAUS unemployment, LAUS LFP, average weekly hours | Stock-Watson dynamic-factor model (sklearn `FactorAnalysis` works as a simple proxy) | `business_cycle_index: {months: [...], values: [...], rebased_to: "2015-01"}` |
| `vitality.py` | LAUS LFP, BEA earnings growth, ACS young-adult share, PEP net migration | Z-score each variable vs. 387-metro distribution, average | `vitality_index: {value: 0.71, rank_of_387: 47, components: {...}}` |
| `quality_of_life.py` | ACS commute (have), EPA AQI (new fetcher), FBI UCR crime (new), NCES school spending (new) | Weighted sum, scaled 0-300 | `quality_of_life: {value: 182, rank_of_407: 236, components: {...}}` |
| `housing_valuation.py` | FHFA HPI, ACS median HH income, ACS median rent, FRED 30-year mortgage rate, PEP population | VAR: `HPI ~ income + rent + rate + pop_growth`; published value = residual | `housing_valuation: {pct_over_under: 14, history: [...]}` |
| `business_costs.py` | BLS RPP (new fetcher), Tax Foundation state-local burden (annual scrape), CoStar commercial rent (annual hand-update or proxy) | Weighted index, US=100 | `business_costs: {living: 92, business: 89, components: {...}}` |
| `credit_score.py` | EMMA bond filings (new fetcher), `bea_personal_income` per-capita, `census_pep` population growth, EIA energy mix (optional) | Map composite to 1-21 scale → letter grade (Aaa..C) | `credit_score: {grade: "Aa3", numeric: 4, components: {...}}` |

### Wiring once each module exists

For each modeling module, add a new section runner in `scripts/fetch_msa_report.py`:
```python
def run_forecast_arima(cbsa):
    # Modeling modules need the prior JSON to read inputs — pass it in
    prior = read_prior_report(out_dir, MSA_BY_CBSA[cbsa][0])
    data = forecast_arima.compute(cbsa, prior)
    return data, "live" if data else "failed"
```
Register in the `SECTIONS` list. The orchestrator's never-blank-on-failure logic handles transient failures automatically.

Then in `msa/savannah/index.html`, extend `loadLiveData()`:
- Populate the 5 forecast columns (2026F–2030F) in the indicators table from `data.sections.forecast_arima`
- Flip the Forecast model pending banner to a Live status badge
- Render the Business Cycle Index chart from `data.sections.business_cycle_index`
- Flip the credit-score box from `—` to the live grade
- Populate the 5-cell top indicator strip (drivers, rank, costs, vitality, QoL) — currently all `Demo`

### Recommended Phase 2 build order

1. **Day 1**: `forecast_arima.py` first — biggest visual impact (fills 5 forecast columns × 12 indicators = 60 currently-empty cells). Use `statsmodels` for ARIMA, blend with Atlanta Fed quarterly economic forecast for state-level sanity check.
2. **Day 2**: `business_cycle_index.py` (Stock-Watson) — converts the only proxy chart on the page from Demo to Live.
3. **Day 3**: `vitality.py` + `quality_of_life.py` — the top indicator strip flips to Live.
4. **Day 4**: `housing_valuation.py` + `business_costs.py` — finishes the housing trio + relative costs cell.
5. **Day 5**: `credit_score.py` — finishes the sidebar rating box.

Total Phase 2 estimate: ~5 days of focused work to flip every remaining Demo pill on the Savannah page to Live or EIG composite.

### Key dependencies to add to `requirements.txt` / GitHub Action

```
statsmodels>=0.14   # ARIMA, VAR
scikit-learn>=1.3   # FactorAnalysis for Stock-Watson
pandas>=2.1
numpy>=1.26
```

### Important context for tomorrow's session

- The user is **Alfie Meek**, principal of Economic Impact Group, LLC, and proprietor of the Georgia Economics site.
- The site is at `~/Documents/Claude/Projects/Georgia Economics` (local) and lives at https://www.georgiaeconomics.com.
- Per memory, **Alfie pushes from his Mac via a 3-line git block**; never use the GitHub MCP for writes.
- Per memory, **never propose manual updates** — default to APIs + Tavily-with-staleness-fallback.
- Per memory, **never blank live data** on transient errors — the orchestrator already enforces this.
- All Phase 1 fetchers are in `scripts/reporting/`. Phase 2 modules go in `scripts/modeling/` (new directory).
- The orchestrator's `SECTIONS` list is the single registration point for new section runners.
- The Savannah page's `loadLiveData()` function in `msa/savannah/index.html` is where new sections get wired into the UI.
- **Read this whole document before writing any code** so the architecture stays coherent.

### Phased rollout — full timeline

| Phase | Scope | Status | Effort |
|---|---|---|---|
| **0 — Example** | Static Savannah report + CTA on /msa/ | ✅ Done | — |
| **1 — Data pipeline** | 13 fetchers, orchestrator, GH Action, JSON wiring | ✅ Done (this session) | ~3 days realized |
| **1.5 — Polish** | Lock ITA endpoint; add 5-6 derived sections; small fetcher extensions | Backlog | ~2 days |
| **2 — Composites + forecasts** | 7 modeling modules → ARIMA, Stock-Watson, vitality, QoL, valuation, business costs, credit score | 🚧 Next | ~5 days |
| **3 — LLM narrative** | Anthropic + Tavily; weekly cadence; strict JSON validation | Planned | ~5 days |
| **4 — Maps** | Plotly choropleth at block-group level for density / income / commute | Planned | ~4 days |
| **5 — Other 13 MSAs** | Generate template instances + per-MSA QA | Planned | ~3 days |
| **6 — Stored PDF (optional)** | Playwright render + repo upload | Planned | ~2 days |

**Remaining total after Phase 1:** ~21 days of focused work to ship live, automatically-refreshing reports for all 14 Georgia MSAs.
