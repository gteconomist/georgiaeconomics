# Georgia MSA Metro Economic Profile — Work Plan

**A product of Economic Impact Group, LLC**

**Goal:** Build a full "Metro Economic Profile" report for each of the 14 Georgia MSAs, served as a live HTML page at `/msa/<slug>/` and downloadable as a PDF via the browser's print dialog. The example for Savannah is live at `/msa/savannah/`.

Every section maps to a free, public source (BLS / BEA / Census / FHFA / IRS SOI / Census USA Trade Online / FRED / GPA). Where a section needs a composite or modelled measure, Economic Impact Group will publish its own methodology rather than rely on any third-party proprietary index.

---

## 1. Section-by-section data mapping

| # | Report section | Public-data source | Cadence | Composite needed? |
|---|---|---|---|---|
| 1 | Header: CBSA code, counties, pop | OMB delineation file + Census PEP | Annual | No |
| 2 | Economic drivers (top 2 LQ industries) | BLS QCEW location quotients | Quarterly | No |
| 3 | Employment-growth rank (2-yr & 5-yr) | BLS CES MSA employment | Monthly | No — compute rank vs. 387 metros |
| 4 | Relative costs of living / business | BLS Regional Price Parities (RPP) | Annual | Partial — business cost is a weighted RPP |
| 5 | Vitality index | BLS LAUS LFPR + employment + earnings | Quarterly | **Proxy** — z-score composite |
| 6 | Quality-of-life composite | Census ACS + EPA + FBI UCR + commute | Annual | **EIG composite** — published weighting |
| 7 | Business cycle status (chart) | BLS CES + LAUS | Monthly | **Proxy** — Stock-Watson coincident-index |
| 8 | Strengths / weaknesses bullets | Derived from data + LLM | On data refresh | Hybrid — LLM with templated guardrails |
| 9 | Forecast risks (upside / downside) | Derived + Tavily news scrape | Weekly | Hybrid — LLM, surfaced from local news + national |
| 10 | EIG credit score | Public county/MSA bond filings (EMMA) + EIG composite | As-issued / quarterly | **EIG composite** |
| 11 | Recent-performance prose | Composed by LLM from latest data | Monthly | Hybrid |
| 12 | Headline indicators table (history + forecast) | BEA GMP, BLS CES & LAUS, BEA personal income, Census PEP, Census BPS, FHFA HPI | Quarterly | Forecast: our own state-space VAR |
| 13 | Economic health check (6-month grid) | BLS CES + LAUS + BPS + GPA monthly | Monthly | No |
| 14 | Business-cycle index chart | Composite of indicators in row 13 | Monthly | **Proxy** |
| 15 | Industry employment % change | BLS CES super-sector | Monthly | No |
| 16 | Current employment trends (3-mo MA table) | BLS CES super-sector | Monthly | No |
| 17 | Diffusion index | BLS CES 3-digit NAICS | Monthly | No — compute share with positive 6-mo change |
| 18 | Relative employment performance (chart) | BLS CES rebased to Jan 2015 | Monthly | No |
| 19 | Relative employment forecast (arrows) | Our VAR forecast vs. prior vintage | Quarterly | **Proxy** — methodology our own |
| 20 | House price index | FHFA HPI (MSA, purchase-only) | Quarterly | No |
| 21 | Rental affordability | Census ACS B25064 ÷ B19013 | Annual | No |
| 22 | Over/undervalued housing | HPI residual vs. local fundamentals (income, rents, rates) | Quarterly | **Proxy** — VAR residual |
| 23 | Housing affordability (PITI/income) | Realtor.com listings + Freddie PMMS + ACS income | Monthly | No |
| 24 | Top employers list | Local development authority filings + LinkedIn + state job-creation grant data | Annual | Manual cache, validated yearly |
| 25 | Industrial diversity (Herfindahl) | BLS QCEW employment shares | Annual | No — compute directly |
| 26 | Entrepreneurship (broad-based startup rate) | Census Business Formation Statistics (BFS) | Monthly | No |
| 27 | Exports (by product & destination) | Census USA Trade Online (api.census.gov/data/timeseries/intltrade, MSA-level) | Annual | No |
| 28 | Public sector employment (Fed/State/Local) | BLS QCEW ownership splits | Quarterly | No |
| 29 | Productivity ($ output / worker) | BEA GMP ÷ BLS employment | Annual | No |
| 30 | Business costs (U.S.=100) | BLS RPP + commercial rent (CoStar OSM scrape) + state tax (Tax Foundation) | Annual | Partial proxy |
| 31 | High-tech employment | BLS QCEW NAICS 5112+5182+5415+5417 | Quarterly | No |
| 32 | Leading industries by wage tier | BLS QCEW LQ + avg wages | Quarterly | No |
| 33 | Block groups by income | Census ACS 5-year, B19013 by block group | Annual | No |
| 34 | Gini / inequality | Census ACS B19083 + table B25086 ratio | Annual | No |
| 35 | Migration flows (top 10 in/out) | IRS SOI migration (county-to-county, CBSA-aggregated) | Annual | No |
| 36 | Per-capita income (chart) | BEA Regional CA1 | Annual | No |
| 37 | Households by income (histogram) | Census ACS B19001 | Annual | No |
| 38 | Commuter flows in/out | Census LEHD LODES | Annual | No |
| 39 | Net migration (annual stacked bar) | Census PEP components-of-change | Annual | No |
| 40 | Generational breakdown | Census ACS B01001 single-year-of-age | Annual | No |
| 41 | Educational attainment | Census ACS B15003 | Annual | No |
| 42 | Population by age (5-yr bins) | Census ACS B01001 | Annual | No |
| 43 | Geographic profile maps (density, income, commute) | TIGER/Line + ACS, rendered with Plotly choropleth | Annual | No |
| 44 | Population & housing characteristics table | Census ACS DP04 + DP02 + DP05 | Annual | No |

**Bottom line:** of the 44 sections, 35 are direct pulls from public APIs, 7 are computed metrics needing simple math, and 7 require a documented Economic Impact Group composite (vitality, QoL, business cycle index, valuation model, forecast vintage, business costs, credit score). Every section maps to a free, public source — no third-party subscription dependency.

---

## 2. Architecture

```
georgiaeconomics/
├── data/
│   └── msa_reports/                  # one JSON per MSA, ~50 KB each
│       ├── savannah.json
│       ├── atlanta.json
│       └── ...
├── msa/
│   ├── index.html                    # comparison table + report CTA
│   ├── savannah/index.html           # report (template, reads /data/msa_reports/savannah.json)
│   ├── atlanta/index.html
│   └── ...
├── scripts/
│   ├── _ga_msas.py                   # canonical MSA list (exists)
│   ├── fetch_msa_report.py           # main orchestrator — pulls all 44 sections
│   ├── reporting/
│   │   ├── pull_bls.py
│   │   ├── pull_bea.py
│   │   ├── pull_census.py
│   │   ├── pull_fhfa.py
│   │   ├── pull_irs_soi.py
│   │   ├── pull_gpa.py               # Savannah port stats
│   │   └── pull_trade.py             # Census USA Trade Online + ITA
│   ├── modeling/
│   │   ├── business_cycle_index.py   # Stock-Watson coincident-index
│   │   ├── housing_valuation.py      # VAR residual model
│   │   ├── vitality.py
│   │   └── forecast_var.py           # state-space forecast through 2030
│   └── narrative/
│       └── generate_prose.py         # LLM-generated analysis prose with templated guardrails
└── .github/workflows/
    ├── update-msa-reports.yml        # nightly cron — runs fetch_msa_report.py for all 14
    └── update-msa-narrative.yml      # weekly — re-runs LLM prose with latest news (Tavily)
```

**Two-stage refresh model:**

1. **Data layer (nightly):** `fetch_msa_report.py <cbsa>` pulls every section's raw data, computes derived metrics, runs the forecast model, and writes `/data/msa_reports/<slug>.json`. Each section is fault-tolerant: a failed pull falls back to the previous run's value with a `stale: true` flag, surfaced to the user via the same `.as-of.stale` pattern the inflation page already uses.

2. **Page layer:** the per-MSA HTML is a *single template* that reads its JSON at load time. Charts re-render from the JSON. Means we build **one** template, not 14. The Savannah example I just shipped will be refactored to read from JSON in Phase 2; right now it has inline data so you can see the look.

**LLM narrative pipeline:**

- The 4 prose sections (Recent Performance / Manufacturing / Logistics / Outlook) and the 8 bullet lists (strengths, weaknesses, upside, downside) are generated by Claude via the Anthropic API.
- Prompt is templated: it receives a JSON of the most recent 24 months of indicators plus a Tavily news scrape filtered to the MSA's major employers and industries.
- Output is constrained to a strict schema and re-validated. If the LLM call fails or returns malformed JSON, we fall back to the previous run's text (`stale_narrative: true`).
- Cadence: weekly, not nightly — narrative doesn't need same-day freshness and this keeps API spend predictable.

---

## 3. Print / PDF

The example page already includes `@media print` CSS so the browser's "Save as PDF" produces a clean, multi-page document with chart backgrounds preserved. This covers 95% of users' "give me a PDF" need at zero hosting cost.

If a stored PDF artifact is later desired (for email distribution / archival), a one-shot Playwright job in CI can render each page to PDF and upload to `/data/msa_reports/pdfs/<slug>.pdf`. ~$0 marginal cost; ~5 min added to the nightly build.

---

## 4. Economic Impact Group composite metrics

| Metric | What it measures | EIG approach |
|---|---|---|
| **Business cycle index** | Coincident snapshot of local economic momentum | Stock-Watson coincident-index using BLS CES employment, LAUS unemployment & LFP, BEA real wages, BLS hours-worked. Open-source methodology, peer-reviewed. |
| **Vitality** | LFPR + earnings growth + young-adult share + net migration | Z-score average of the four variables vs. the 387-metro distribution. Documented in `/about/methodology/`. |
| **Quality of life** | Commute, crime, schools, air quality | ACS commute + EPA AQI + FBI UCR (county-aggregated) + Census-NCES school spending, scaled 0-300. |
| **Housing valuation (over/under)** | % over/under local fundamentals | VAR residual: regress FHFA HPI on local income, rents, mortgage rate, population growth; residual is the published value. |
| **Long-term risk exposure** | 5-year downside scenario | Conditional value-at-risk on the EIG forecast distribution. |
| **Business costs index** | Cost of operating relative to US | BLS RPP + Tax Foundation state-local burden + commercial rent from local broker reports. |
| **EIG credit score** | County GO-equivalent risk grade | Mapping of EMMA bond filings + EIG fiscal-strength composite onto a published 1-21 letter scale. |

Every composite metric gets a "Methodology" link on the page and a `<span class="src-pill proxy">EIG composite</span>` label in the source line, so users always know which numbers are direct pulls vs. modelled.

---

## 5. Phased rollout

| Phase | Scope | Calendar | Effort |
|---|---|---|---|
| **0 — Example (DONE)** | Static Savannah report + CTA on /msa/ | Now | ✓ Shipped |
| **1 — Data pipeline & template** | Build `fetch_msa_report.py` for the 35 direct-pull sections; refactor Savannah to read from JSON; add the GitHub Action | Weeks 1-2 | ~10 days |
| **2 — Compute the 7 proxy metrics** | Stock-Watson BCI, vitality, QoL, valuation, VAR forecast, business costs, rating lookup | Weeks 3-4 | ~8 days |
| **3 — LLM narrative pipeline** | Anthropic API integration + Tavily news + templated prompts + validation | Week 5 | ~5 days |
| **4 — Geographic profile maps** | Plotly choropleth at block-group level for density / income / commute | Week 6 | ~4 days |
| **5 — Roll out to remaining 13 MSAs** | Generate 13 more `/msa/<slug>/index.html` from the template; QA each | Week 7 | ~3 days (mostly QA) |
| **6 — Stored-PDF artifact (optional)** | Playwright render to PDF, upload to repo, link from report header | Week 8 | ~2 days |

**Total:** ~6 weeks of focused work to ship live, automatically-refreshing reports for all 14 Georgia MSAs.

---

## 6. Required secrets / API keys (all free)

Already in repo (per existing workflows): `BLS_API_KEY`, `FRED_API_KEY`, `CENSUS_API_KEY`, `BEA_API_KEY`. Need to add:

- `ANTHROPIC_API_KEY` — for narrative generation (already used elsewhere)
- `TAVILY_API_KEY` — for the news scrape feeding the narrative (already used on the film page per memory)
- `IRS_SOI` — no key needed, but pin a curl-able URL for the latest county-to-county migration ZIP (annual release)

**No API key needed for:** FHFA HPI (public CSV download), Census USA Trade Online (uses existing `CENSUS_API_KEY`), api.data.gov ITA datasets (skipped — Census USA Trade Online covers MSA-level export statistics with better granularity).

---

## 7. Open questions to nail down before Phase 1

1. **Forecast methodology:** do you want a true state-space VAR (more accurate, harder to maintain) or simpler ARIMA + judgment overlay? Recommend ARIMA + Atlanta Fed consensus blend for the first cut.
2. **Credit-score column:** credit ratings are issued at the county and bond-issuer level, not the MSA level. Options: (a) publish the EIG composite credit score only; (b) publish the EIG score *and* show the most recent county GO rating from EMMA as a reference; (c) drop the row entirely. Recommend (b) so users can sanity-check the EIG composite against issued ratings.
3. **Top-employers refresh:** SEDA-style lists need an annual hand-update. Plan to do this once a year as part of an annual data audit, or hand to a junior analyst.
4. **PDF distribution:** are you planning to email these PDFs to anyone, or is "Save as PDF from the browser" enough? Affects whether Phase 6 is in scope.
