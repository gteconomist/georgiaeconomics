# Metro Economic Profile — Data Status Tracker

**Reference model:** Moody's Analytics *Précis® Metro* report.
**Pilot page:** `/msa/savannah/` (CBSA 42340). This tracker is the source of truth for what is real, what is modeled, what is still demo, and what cannot be obtained at the MSA level.

**Last updated:** 2026-05-30
**Data layer:** 25 live / 1 failed of 26 sections (only `census_bps_permits` failing). All 7 modeling modules live. Remaining work is wiring buildable demo sections, not data plumbing.

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
| Economic Drivers (top-2 LQ industries) | QCEW location quotients | DEMO — **buildable** |
| Employment Growth Rank (2-yr / 5-yr) | national ranking | DEMO — rank not computed live |
| Relative Costs (Living / Business) | `business_costs.py` | **MODEL** — *displays next refresh* |
| Vitality Index | `vitality.py` | **MODEL** (live) |
| Quality of Life | `quality_of_life.py` | **MODEL** (live) |

> Note: the `strip-note` caption still says "all 5 / other 3 cells Demo" — update its wording once `business_costs` lands so it doesn't contradict the live Relative Costs cell.

### Scorecard sidebar
| Element | Source | Status |
|---|---|---|
| Strengths / Weaknesses / Upside / Downside | hand-written prose | DEMO (numbers cited are mostly live) |
| EIG Credit Score | `credit_score.py` | **MODEL** — AA / 84 / Positive; *displays next refresh* |

### Analysis narrative (Labor, Sector Mix, Trade, Housing, Demographics, Inequality, Synthesis)
DEMO prose, tagged "Partial." Template is final; paragraphs are hand-written, not auto-generated. Housing paragraph rewritten to match the live valuation model.

### Headline indicators table ("long indicators table")
| Part | Source | Status |
|---|---|---|
| Historical columns 2019–2024 | annual BLS/BEA/Census/FHFA series | **LIVE** |
| 2025 column | live where the year is complete | **LIVE** (partial) |
| Forecast 2026F–2030F | `forecast_arima.py` | **MODEL** |
| Median-income / net-migration / permits history rows | — | DEMO (illustrative) |

### Economic Health Check
| Element | Source | Status |
|---|---|---|
| Recent-quarters trajectory table (employment, avg weekly wage, establishments, unemployment, labor force) | QCEW MSA totals + LAUS (`health_check`) | **LIVE** — *fix pending CI validation*. Rebuilt quarterly; dropped participation + weekly hours (no MSA source). |
| Business Cycle Index chart | `business_cycle_index.py` | **MODEL** (live) |

### Employment
| Element | Source | Status |
|---|---|---|
| Industry Employment (YoY by sector) | QCEW | **LIVE** (confirmed 2026-05-30) |
| Current Employment Trends table | BLS CES by supersector | **LIVE** |
| Diffusion Index | needs 3-digit NAICS QCEW over time | DEMO — **buildable** |
| Relative Employment Performance | BLS CES (rebased) | **LIVE** |
| Relative Employment Forecast (arrows) | `forecast_arima.py` | **MODEL** |
| House Price Index chart | FHFA via FRED | **LIVE** |

### Housing
| Element | Source | Status |
|---|---|---|
| Rental Affordability | Census ACS (`acs_affordability`) | **LIVE** |
| House Price Trends (valuation) | `housing_valuation.py` | **MODEL** (live) |
| Housing Affordability index | Freddie PMMS (public) + Realtor (scrape) + ACS | DEMO — **partly buildable** |

### Industrial Structure
| Element | Source | Status |
|---|---|---|
| Top Employers | — | DEMO / **NO MSA SOURCE** (no public API; Précis uses proprietary D&B-type data). Best alternative = Tavily hints, non-authoritative. |
| Industrial Diversity score | QCEW (Hachman/HHI) | DEMO — **buildable** |
| Entrepreneurship | Census Business Formation Statistics (MSA-level exists) | DEMO — **buildable** |
| Productivity | BEA GMP ÷ CES employment | **LIVE / MODEL** |
| Exports (by product / destination) | ITA | **LIVE** |

### Comparative Employment & Income
QCEW shares + average annual wages vs GA/US. **LIVE** (confirmed 2026-05-30); reads one quarter behind the headline total by design (agglvl-44 sector lag). Manufacturing is a **single row** until a 3-digit pull enables the durable/nondurable split (shared need with Diffusion Index).

### Demographics & Migration
| Element | Source | Status |
|---|---|---|
| Block Groups by Income | ACS block-group pull | DEMO — **buildable** |
| Economic Inequality (Gini, poverty) | data is in live ACS section | DEMO — **buildable** (wiring only; national *rank* would be MODEL) |
| Per Capita Income | BEA | **LIVE** |
| Migration Flows (in/out) | IRS SOI | **LIVE** |
| Generational Breakdown | ACS age structure | **LIVE** |
| Educational Attainment | ACS B15003 | **LIVE** |
| Population by Age | ACS | **LIVE** |

### Geographic Profile
| Element | Source | Status |
|---|---|---|
| Net Migration | Census PEP components | **LIVE** |
| Population & Housing Characteristics table | ACS B25024/B25035 + tenure/age + Census Gazetteer land area (`acs_housing_characteristics`) | **LIVE** (built 2026-05-30, pending dispatch). Rank column dropped (no national source). Density = ACS pop ÷ Gazetteer land. |

---

## Modeling modules (EIG composites) — 7 of 7 built

| Module | Powers | Status |
|---|---|---|
| `business_cycle_index` | BCI chart | live |
| `forecast_arima` | forecast columns + arrows | live |
| `vitality` | Vitality strip cell | live |
| `quality_of_life` | QoL strip cell | live |
| `housing_valuation` | House Price Trends (valuation) | live |
| `business_costs` | Relative Costs cell | built — displays next refresh |
| `credit_score` | scorecard grade | built — displays next refresh (must run **last** — reads other models) |

---

## Known defects

### QCEW "false-live" (Industry Employment + Comparative Employment showed Demo)
**Three stacked bugs, all confirmed via dispatch diagnostics + the official BLS layouts:**
1. Quarterly by-area CSV has **no `annual_avg_emplvl`** (employment is `month1/2/3_emplvl`). → read `month3_emplvl` w/ fallbacks.
2. Manufacturing/retail/transportation are **hyphenated sector codes** (`31-33`, `44-45`, `48-49`); a `len()==2` filter dropped them. → match an explicit sector-code set. (Durable/nondurable manufacturing split needs a 3-digit pull; collapsed to one row for now.)
3. **Agglvl-44 ("MSA, Private, by NAICS Sector") detail lags the agglvl-40 total** — the newest published quarter carries all-zero sector employment while the total covered is populated. → step back to the most recent quarter whose sector aggregation is non-empty (`_qcew_latest_sector_quarter`).
Plus a false-live guard: return `None` when nothing aggregates, so status is honestly `failed`/stale instead of an empty "live" payload.
**Status:** ✅ RESOLVED 2026-05-30 — dispatch confirmed both `qcew_industry_shares` and `qcew_yoy_changes` live (2025 Q2, stepped back from the unpopulated Q3). Report now 25 live / 1 failed of 26. The Comparative table reads one quarter behind the headline total by design (sector-detail lag).

### `census_bps_permits` — only hard-failed section
FRED area-prefix for Savannah unresolved. Needs a keyed run to read the resolved prefix into `GEO_OVERRIDES`.

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
3. `census_bps_permits` fix (needs keyed run) — **only remaining failure (1 of 26).**
5. Wire the **buildable** DEMO items: Industrial Diversity (QCEW HHI), Entrepreneurship (Census BFS), Economic Inequality + Pop/Housing tables (ACS, already fetched), Economic Drivers strip cell (QCEW LQ).
6. 3-digit NAICS QCEW pull → Diffusion Index + manufacturing durable/nondurable split.
7. Block Groups by Income (ACS block-group); Housing Affordability (Freddie PMMS + ACS).

## Open data-accuracy item
**Savannah MSA county count:** `_ga_msas.COUNTY_TO_MSA` maps CBSA 42340 to **3 counties** (Chatham, Bryan, Effingham), but the page header and OMB's recent delineation describe a **4-county** MSA (adds Bulloch / Statesboro). Anything county-aggregated (migration totals, Gazetteer land area, density) currently reflects the 3-county definition; the ACS CBSA-level calls use whatever Census currently defines 42340 as. Reconcile `_ga_msas` to the current OMB delineation so all county-summed metrics agree. Affects: migration, land area/density, any future county rollups.

## Cannot replicate from Précis at MSA level
Authoritative **Top Employers** (proprietary), **current MSA crime** (FBI by-MSA table ended 2019), **monthly** high-frequency series, and **average weekly hours** by MSA.
