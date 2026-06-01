# Metro Economic Profile — Data Status Tracker

**Reference model:** Moody's Analytics *Précis® Metro* report.
**Pilot page:** `/msa/savannah/` (CBSA 42340). This tracker is the source of truth for what is real, what is modeled, what is still demo, and what cannot be obtained at the MSA level.

**All 14 metros now live (Phase 3).** For a per-metro × per-section status grid across every Georgia MSA, see the auto-generated **[`REPORT_STATUS_MATRIX.md`](REPORT_STATUS_MATRIX.md)** (rebuild with `python3 scripts/build_report_status_matrix.py` after a data refresh). This curated file remains the narrative source-of-truth for the Savannah pilot + methodology.

**Last updated:** 2026-06-01
**Data layer: 30 live / 0 failed of 30 confirmed; +1 new section (`qcew_3digit`) wired, live-pending next dispatch.** Every section with an automatable source is live. Phase-2 close-out (2026-05-30) wired three previously-DEMO page items: **Economic Inequality** (live ACS Gini B19083 + poverty B17001 — page wiring only), **Economic Drivers strip cell** (top-2 industries by QCEW location quotient — page wiring only, no new fetcher), and **Diffusion Index** (new `qcew_3digit` fetcher; confirmed live via dispatch, 2025 Q2, values 42–53 across 32–34 subsectors). The **manufacturing durable/nondurable split** from the same fetcher renders only where MSA 3-digit coverage ≥80%; for Savannah it's suppressed (NAICS 336 / Gulfstream → 13% coverage) so the Manufacturing row stays single — caught in verification, see defect below. **Production cleanup (2026-05-30, later):** removed the Employment Growth Rank strip cell (no ranking source; strip now 4 cells); reworked Top Employers → "Major Employers" representative list (no headcounts); replaced the "LAYOUT PREVIEW" banner with a data-status legend; corrected the static header population (418k); **built the Housing Affordability index** (`housing_affordability.py` — the last buildable DEMO item). Remaining non-buildable: Top Employers headcounts (proprietary), MSA crime (FBI table ended 2019), monthly high-frequency series, avg weekly hours. **Savannah county definition reconciled:** the MSA is correctly **3-county** (Bryan/Chatham/Effingham); Bulloch/Statesboro is a separate micropolitan area joined only in the broader CSA — page header corrected from the erroneous "4-county".

---

## Legend

| Tag | Meaning |
|-----|---------|
| **LIVE** | Real data fetched from a public API on each refresh. |
| **MODEL** | An EIG calculation/estimate computed from live data (not a published figure). Carries an "EIG composite" pill. |
| **DEMO** | Hardcoded placeholder. Not yet wired to data. |
| **NO MSA SOURCE** | Cannot be obtained at the MSA level (no public/automatable source). |

**Buildable** = a DEMO item for which a source we already use (or can add) exists; just not wired yet.

---

## ⚠️ How "live" actually reaches the page (read this first)

Two-stage pipeline, and the stages refresh on different clocks:

1. **Data layer** — `scripts/fetch_msa_report.py` writes `data/msa_reports/savannah.json`. This only regenerates when the **nightly workflow** (`update-msa-reports.yml`, 09:00 UTC) or a manual dispatch runs. Code that is committed but hasn't had a refresh run yet is **not yet in the JSON**.
2. **Page layer** — `loadLiveData()` reads the JSON and flips each section's pill from "Demo" to "Live". A section only flips if its JSON payload is actually populated.

**Consequence:** a section can be "deployed in code" but still show **Demo** on the live site until the next refresh run. After any merge that adds/changes a fetcher or model, trigger a dispatch to make it real.

**Critical gotcha (the source of recent confusion):** a fetcher can report `status="live"` while its payload is **empty** — the page then correctly stays on Demo because there's nothing to render. Always verify the *payload*, not just the status. See the QCEW defect below.

---

## Section-by-section status

### Header & live-indicator strip
| Element | Source | Status |
|---|---|---|
| Population, CBSA, county list, as-of | static facts | LIVE (static) |
| "Latest data pulled from public APIs" strip (employment, unemployment, HPI, population, median + per-capita income, GMP) | BLS CES/LAUS, FHFA, Census, BEA | **LIVE** |

### Top indicator strip (5 grey cells)
| Cell | Source | Status |
|---|---|---|
| Economic Drivers (top-2 LQ industries) | QCEW location quotients (MSA share ÷ US share, from `qcew_industry_shares`) | **LIVE** (wired 2026-05-30; *displays next refresh*) |
| ~~Employment Growth Rank~~ | national ranking | **REMOVED 2026-05-30** — no automatable national-metro ranking; strip cut from 5 cells to 4 (Alfie's call). |
| Relative Costs (Living / Business) | `business_costs.py` | **MODEL** — *displays next refresh* |
| Vitality Index | `vitality.py` | **MODEL** (live) |
| Quality of Life | `quality_of_life.py` | **MODEL** (live) |

> Note: the `strip-note` caption is now **generated dynamically** by `replaceIndicatorStrip()` — it names whichever cells are live (Economic Drivers / Relative Costs / Vitality / Quality of Life) and flags Employment Growth Rank as the one remaining Demo. No longer a hardcoded contradiction.

### Scorecard sidebar
| Element | Source | Status |
|---|---|---|
| Strengths / Weaknesses / Upside / Downside | hand-written prose | DEMO (numbers cited are mostly live) |
| EIG Credit Score | `credit_score.py` | **MODEL** — AA / 84 / Positive; *displays next refresh* |

### Analysis narrative (Labor, Sector Mix, Trade, Housing, Demographics, Inequality, Synthesis)
Hand-written prose, **reconciled to the live JSON 2026-05-30** (all 7 paragraphs + the Strengths/Weaknesses/Upside/Downside scorecard). Replaced the demo figures that had drifted/contradicted live data — e.g. unemployment 2.9% (was 3.1%), employment ~flat +0.1% YoY (was "+2.5% broad-based"), diffusion 47 (was "upper-60s"), manufacturing share 16.7% (was 11.4%), exports $8.4B ≈24% of GMP (was $9.9B/29.6%), median age 36.9, diversity 0.86 (was 0.76), net migration +3,200 (was +5,700). Removed unsourced claims (affordability 280→118 index, durable-goods earnings, "lowest unemployment of any GA MSA" — actually 2nd behind Gainesville, "38th of 387" rank). Narrative now reads Savannah as tight-but-cooling. Prose is reviewed periodically and may lag the very latest refresh by design. **A `<span class="src-pill">Disclaimer</span>` was added to the report footer** (informational-only, data not independently validated by EIG — consult original sources).

### Headline indicators table ("long indicators table")
| Part | Source | Status |
|---|---|---|
| Historical columns 2019–2024 | annual BLS/BEA/Census/FHFA series | **LIVE** |
| 2025 column | live where the year is complete | **LIVE** (partial) |
| Forecast 2026F–2030F | `forecast_arima.py` | **MODEL** |
| Median-income / net-migration / permits history rows | ACS B19013 (`acs_affordability.msa_median_income`), PEP components (`census_net_migration`), Census BPS (`census_bps_permits`) | **LIVE** — wired 2026-05-30 into `replaceHistoricalColumns`. (Previously static Savannah demo numbers that leaked onto generated metro pages.) Years without data (e.g. pre-2020/2021) show "—"; income 2025 column carries the latest ACS vintage forward. |

### Economic Health Check
| Element | Source | Status |
|---|---|---|
| Recent-quarters trajectory table (employment, avg weekly wage, establishments, unemployment, labor force) | QCEW MSA totals + LAUS (`health_check`) | **LIVE** — *fix pending CI validation*. Rebuilt quarterly; dropped participation + weekly hours (no MSA source). |
| Business Cycle Index chart | `business_cycle_index.py` | **MODEL** (live) |

### Employment
| Element | Source | Status |
|---|---|---|
| Industry Employment (YoY by sector) | **BLS CES** by-supersector (latest-month YoY) | **LIVE** — switched from QCEW to CES 2026-05-30 (QCEW MSA sector detail is suppressed for big metros like Atlanta; CES is model-based, no suppression, reliable for all 14). |
| Current Employment Trends table | BLS CES by supersector | **LIVE** |
| Diffusion Index | **BLS CES by-supersector** — monthly share of the ~13 supersectors with higher employment than a year ago: (growing + 0.5·flat)/n | **LIVE** — switched from QCEW 3-digit to CES 2026-05-30. Atlanta's 3-digit QCEW was suppressed to only 3–4 industries (flat 100s); CES gives a reliable n=13 breadth for every metro. Coarser than 3-digit but consistent. (`qcew_3digit` still computes a 3-digit diffusion in the JSON but the page no longer uses it.) |
| Relative Employment Performance | BLS CES (rebased) | **LIVE** |
| Relative Employment Forecast (arrows) | `forecast_arima.py` | **MODEL** |
| House Price Index chart | FHFA via FRED | **LIVE** |

### Housing
| Element | Source | Status |
|---|---|---|
| Rental Affordability | Census ACS (`acs_affordability`) | **LIVE** |
| House Price Trends (valuation) | `housing_valuation.py` | **MODEL** (live) |
| Housing Affordability index | `housing_affordability.py` — NAR-style HAI: ACS income (B19013) vs. income needed for the median home (ACS B25077 × FHFA HPI; Freddie Mac PMMS 30-yr rate via FRED MORTGAGE30US) | **LIVE / MODEL** (built 2026-05-30; *displays next refresh*). MSA-only series; >100 = median HH can afford median home. |

### Industrial Structure
| Element | Source | Status |
|---|---|---|
| Top Employers → **Major Employers** | SEDA + public announcements (no headcount API) | **SHIPPED as representative list 2026-05-30** — headcount column dropped; real employers ordered by approximate size; "Representative" pill. Exact metro headcounts aren't published (Précis uses proprietary D&B data). |
| Industrial Diversity score | `industrial_diversity.py` (Hachman index from QCEW shares) | **MODEL** (confirmed live 2026-05-30) |
| Entrepreneurship | Census **BDS** establishment entry rate (`entrepreneurship`) | **LIVE** (confirmed 2026-05-30, 2022 vintage). BFS has no sub-national API (eits/bfs is US-only); BDS `ESTABS_ENTRY_RATE` indexed US=100. |
| Productivity | BEA GMP ÷ CES employment | **LIVE / MODEL** |
| Exports (by product / destination) | ITA | **LIVE** |

### Comparative Employment & Income
**MSA employment shares now come from BLS CES** (by-supersector, latest month) — reliable for every metro; GA/US shares + average annual wages still from QCEW (those national/state files are complete), and MSA wages shown where QCEW discloses them. Switched 2026-05-30 after confirming Atlanta's QCEW MSA sector detail is genuinely incomplete (~22% coverage, complete download — disclosure suppression at the metro level, NOT truncation). `fetch_qcew_industry_shares` now returns GA/US even when MSA detail is incomplete (anchors on GA's complete quarter). Economic Drivers LQ cell also uses CES MSA shares ÷ QCEW US shares. Manufacturing **durable/nondurable split** built from `qcew_3digit` (NAICS 311–339, standard durable/nondurable grouping). **Renders only where the MSA 3-digit detail is adequately covered (≥80% of the 2-digit sector total).** For Savannah it does **not** render: QCEW disclosure suppression hides NAICS 336 (Gulfstream/Hyundai), so the unsuppressed 3-digit detail covers just **13%** of the 24,773-job manufacturing sector — an honest split is not recoverable, so the Manufacturing row stays single. GA/US are 100%-covered (the fetcher still emits their splits + a `coverage_pct`/`msa_reliable` flag). See "QCEW 3-digit suppression" defect below.

### Demographics & Migration
| Element | Source | Status |
|---|---|---|
| Block Groups by Income | ACS B19013 block-group pull (`acs_block_group_income`) | **LIVE** (confirmed 2026-05-30) — Savannah series live; US comparison left illustrative (national block-group distribution not pulled). |
| Economic Inequality (Gini, poverty) | ACS B19083 (Gini) + B17001 (poverty) + B19013 block-group low-income share | **LIVE** (wired 2026-05-30; *displays next refresh*). National-rank column dropped (no automatable MSA ranking); narrative de-ranked to match. |
| Per Capita Income | BEA | **LIVE** |
| Migration Flows (in/out) | IRS SOI | **LIVE** |
| Generational Breakdown | ACS age structure | **LIVE** |
| Educational Attainment | ACS B15003 | **LIVE** |
| Population by Age | ACS | **LIVE** |

### Geographic Profile
| Element | Source | Status |
|---|---|---|
| Net Migration | Census PEP components | **LIVE** — generalized to all 14 MSAs 2026-05-30 (was Savannah-only; cross-state Augusta/Columbus counties supported); national CSV cached so an `--all` run downloads it once, not per metro. |
| Population & Housing Characteristics table | ACS B25024/B25035 + tenure/age + Census Gazetteer land area (`acs_housing_characteristics`) | **LIVE** (confirmed 2026-05-30). Rank column dropped (no national source). Density = ACS pop ÷ Gazetteer land. |

---

## Modeling modules (EIG composites) — 9 of 9 built

| Module | Powers | Status |
|---|---|---|
| `business_cycle_index` | BCI chart | live |
| `forecast_arima` | forecast columns + arrows | live |
| `vitality` | Vitality strip cell | live |
| `quality_of_life` | QoL strip cell | live |
| `housing_valuation` | House Price Trends (valuation) | live |
| `business_costs` | Relative Costs cell | live |
| `industrial_diversity` | Hachman index (Industrial Diversity) | live |
| `housing_affordability` | Housing Affordability chart | built 2026-05-30 — displays next refresh |
| `credit_score` | scorecard grade | live (must run **last** — reads other models) |

---

## Known defects

### QCEW "false-live" (Industry Employment + Comparative Employment showed Demo)
**Three stacked bugs, all confirmed via dispatch diagnostics + the official BLS layouts:**
1. Quarterly by-area CSV has **no `annual_avg_emplvl`** (employment is `month1/2/3_emplvl`). → read `month3_emplvl` w/ fallbacks.
2. Manufacturing/retail/transportation are **hyphenated sector codes** (`31-33`, `44-45`, `48-49`); a `len()==2` filter dropped them. → match an explicit sector-code set. (Durable/nondurable manufacturing split needs a 3-digit pull; collapsed to one row for now.)
3. **Agglvl-44 ("MSA, Private, by NAICS Sector") detail lags the agglvl-40 total** — the newest published quarter carries all-zero sector employment while the total covered is populated. → step back to the most recent quarter whose sector aggregation is non-empty (`_qcew_latest_sector_quarter`).
Plus a false-live guard: return `None` when nothing aggregates, so status is honestly `failed`/stale instead of an empty "live" payload.
**Status:** ✅ RESOLVED 2026-05-30 — dispatch confirmed both `qcew_industry_shares` and `qcew_yoy_changes` live (2025 Q2, stepped back from the unpopulated Q3). Report now 25 live / 1 failed of 26. The Comparative table reads one quarter behind the headline total by design (sector-detail lag).

### QCEW 3-digit manufacturing suppression (MSA durable/nondurable split) — ⚠️ KNOWN LIMITATION 2026-05-30
At the **MSA** level, QCEW suppresses 3-digit subsectors dominated by one or two employers (disclosure rule). In Savannah, NAICS 336 (transportation equipment — Gulfstream, Hyundai) is suppressed, so the summed 3-digit manufacturing detail is **3,305 jobs vs the published 24,773** 2-digit total — **13% coverage**. The unsuppressed 59.9/40.1 durable/nondurable ratio is therefore biased (durable understated) and is **not shown**. `fetch_qcew_3digit` computes `coverage_pct` per geography and sets `manufacturing_split.msa_reliable`; the page renders the split only when `msa_reliable` (coverage ≥80%). GA/US are 100%-covered. Net: this is a **NO-MSA-SOURCE-class** limitation for metros with a dominant manufacturer — the split will display for some GA metros but not Savannah. The **Diffusion Index is unaffected** (breadth across all unsuppressed 3-digit industries; n≈32–34). Diffusion + split *display next refresh* once `qcew_3digit` is in the JSON.

### `census_bps_permits` — ✅ RESOLVED 2026-05-30 (county-sum)
FRED has **no MSA-level** permit series for Savannah — only county-level (`BPPRIV013051` etc.; note the leading `0` before the 5-digit FIPS). `_county_permits_annual` sums `BPPRIV0{fips}` over the MSA's counties for the total. County 1-unit series (`BP1FH0{fips}`) don't exist, so the SF/MF split is estimated from the GA state 1-unit share (GABP1FH/GABPPRIV). Confirmed live (2025) via dispatch.

---

## Health Check rebuild — quarterly via QCEW + LAUS (feasibility)

The monthly 6-month trajectory table cannot be sourced at MSA level. **Quarterly is feasible** for most of it:

| Metric | Quarterly MSA source? |
|---|---|
| Employment level + OTY change | ✅ QCEW (`month3_emplvl`, `oty_*`) |
| Average weekly wage + OTY change | ✅ QCEW (`avg_wkly_wage`) |
| Establishment count | ✅ QCEW (`qtrly_estabs`) |
| Total quarterly wages | ✅ QCEW |
| Unemployment rate | ✅ LAUS (monthly → quarterly avg) |
| Labor force level | ✅ LAUS (monthly → quarterly avg) |
| Labor-force participation rate | ❌ no clean quarterly MSA source (needs working-age denominator; ACS annual only) |
| Average weekly hours | ❌ NO MSA SOURCE (CES hours are national/state only) |
| Container TEUs | ⚠️ Georgia Ports monthly, **Savannah-only** (not generalizable to other GA MSAs) |

**Recommendation:** rebuild as a "recent-quarters trajectory" from QCEW + LAUS; drop participation and weekly hours; treat TEUs as an optional Savannah-only extra. If we don't want a quarterly table, drop the section rather than leave it demo.

---

## Roadmap (priority order)

1. ~~QCEW fix + quarterly Health Check~~ — DONE & confirmed live (2026-05-30).
2. ~~`business_costs` + `credit_score` display~~ — DONE & confirmed live.
3. ~~`census_bps_permits` fix~~ — DONE & confirmed live (2026-05-30, county-sum).
5. ~~Wire the **buildable** DEMO items~~ — DONE: Industrial Diversity, Entrepreneurship, Pop/Housing, Block Groups, **Economic Inequality** (ACS Gini+poverty), **Economic Drivers** strip cell (QCEW LQ). All live/live-pending.
6. ~~3-digit NAICS QCEW pull → Diffusion Index + manufacturing durable/nondurable split~~ — DONE (`qcew_3digit` fetcher, 2026-05-30; live-pending next dispatch).
7. ~~Housing Affordability (Freddie PMMS + ACS)~~ — DONE (`housing_affordability.py`, 2026-05-30; live-pending next dispatch). Employment Growth Rank removed; Top Employers shipped as a representative list. **No buildable DEMO items remain.**

## Open data-accuracy item — ✅ RESOLVED 2026-05-30
**Savannah MSA county count:** confirmed against the OMB 2023 delineation (effective July 2023): CBSA 42340 is a **3-county MSA** (Bryan, Chatham, Effingham). Bulloch County / Statesboro is the **Statesboro micropolitan area (44340)** and joins Savannah only in the broader **Savannah–Hinesville–Statesboro Combined Statistical Area (CSA)** — *not* the MSA. So `_ga_msas.COUNTY_TO_MSA` (3 counties) was already correct; the page header's "4-county MSA … Bulloch" was the error and has been corrected to "3-county MSA: Chatham, Bryan, Effingham." All county-aggregated metrics (migration, land area/density) were already on the right 3-county basis. No code change to `_ga_msas` needed.

## Cannot replicate from Précis at MSA level
Authoritative **Top Employers** (proprietary), **current MSA crime** (FBI by-MSA table ended 2019), **monthly** high-frequency series, and **average weekly hours** by MSA.
