# State GDP Page — Build Scope

**Route:** `/gdp/` (the empty `gdp/` dir already exists)
**Status:** Scoped 2026-06-02 · Phase 4, WS1, item #2
**One-line:** A statewide Georgia GDP page: real & nominal output, per-capita GDP,
sector composition, a Southeast-peer comparison, a 159-county output map, and a metro
roll-up. Flips the home-page "State GDP — Coming soon" card to "Live now."

---

## Why this is next

Same low-data-cost profile as Housing. Per-metro GDP (`bea_gmp`) and personal income
(`bea_personal_income`) are already `live` in all 14 MSA reports, and we have a **proven
statewide BEA SAGDP client** in `scripts/fetch_film.py` (it pulls GDP-by-state SAGDP2N,
all-states comparison, and a 12-year GA timeseries today). So the statewide layer is a
reuse, the metro layer is a roll-up, and the county layer reuses the CAGDP2 pull that
`reporting/pull_bea.py` already makes. The only genuinely new work is wiring SAGDP at the
state total + by-industry level and assembling the page.

---

## Data sources

### Statewide (new pulls — reuse the fetch_film.py BEA SAGDP pattern)

| Series | BEA table | Drives |
|---|---|---|
| GA real GDP, annual | **SAGDP9N** (real, chained $) | Headline growth trend (real is the right basis for growth) |
| GA nominal GDP, annual | **SAGDP2N** (current $) | Output level ("Georgia is an $Xtn economy") |
| GA per-capita real GDP | **SAGDP1** (or SAGDP9N ÷ PEP pop) | Per-capita KPI + trend |
| GA GDP by industry | **SAGDP2N**/**SAGDP9N** by LineCode (NAICS sectors) | Sector composition ("what makes up GA output") |
| SE peer states | same tables, **GeoFips=FL,NC,SC,TN,AL** (+ US) | Peer comparison (matches the Population page peer set) |

SAGDP returns every state in one call, so the peer comparison is free. Use real (SAGDP9N)
for growth/peers and nominal (SAGDP2N) for levels — label each clearly.

### Metro roll-up (already live in all 14 reports)

`bea_gmp`: `years[]`, `gmp_billions_usd[]`, `yoy_pct[]`, `gdp_per_capita[]`, `latest_*`.
`bea_personal_income`: `personal_income_billions_usd[]`, `per_capita_income[]`, `latest_*`.
Drives: metro GDP comparison, GDP-per-capita ranking, each metro's **share of state GDP**.

### County layer — full state coverage (all 159 counties)

**BEA CAGDP2** county GDP (LineCode 1, all-industry total) for every county →
choropleth (total GDP and GDP per capita). `reporting/pull_bea.py` already requests
CAGDP2 at `GeoFips=COUNTY` and aggregates to MSAs — `fetch_gdp.py` grabs the raw county
rows instead of summing. Plus a **Non-Metro Georgia** aggregate (state minus the 73 metro
counties), exactly as the Housing page does, so the 86 rural counties are represented.

### Sector diversity (optional, refresh)

`industrial_diversity` (Hachman index) is currently `stale`; the GA sector shares in
`qcew_industry_shares.ga` are `live` and can drive a "how diversified is GA's economy"
note alongside the sector-composition chart.

---

## Page layout (`/gdp/index.html`)

Mirror the topic-page template (header nav, hero, KPI strip, chart sections, methodology,
staleness badges). Reuse the Housing page's choropleth + pending-state patterns verbatim.

1. **KPI strip** — GA nominal GDP level, GA real GDP growth (latest YoY), GA per-capita
   GDP, GA rank among states (or among SE peers), largest sector, non-metro county count.
2. **Real GDP over time** — GA real GDP line + US line; toggle real/nominal. Long window.
3. **Southeast peer comparison** — GA vs FL/NC/SC/TN/AL on real GDP growth and on
   per-capita GDP (grouped bars), with the US reference.
4. **Sector composition** — GA GDP by industry (SAGDP2N line codes): horizontal bar or
   treemap of sector shares; optional Hachman diversity callout.
5. **County output map** — all 159 counties shaded by GDP per capita (default) / total
   GDP toggle. Census/BEA county layer; same `maps.js` choropleth as Housing.
6. **Non-Metro Georgia** — aggregate output + per-capita callout (state minus metros).
7. **Metro comparison** — metros ranked by GDP, by GDP per capita, and by share of state
   output (the 14 metros sum to ~82% of population; show their GDP share too).
8. **Methodology** — SAGDP vs CAGDP2, real vs nominal, ACS/PEP per-capita denominator,
   SE-peer set, staleness behavior.

Charts: shared Chart.js via jsDelivr; county choropleth via Plotly/`maps.js` (`'$'` unit
for dollars, `valueFormatter` for $bn). Reuse the Housing page's pending/stale scaffolding
for the SAGDP and county sections so the page renders before the first CI run.

---

## Data wiring — `scripts/fetch_gdp.py` → `data/gdp.json`

1. **Roll-up reader** — read all 14 `msa_reports/*.json`; extract `bea_gmp` +
   `bea_personal_income` per metro; compute metro rankings + share of state GDP.
2. **Statewide SAGDP** — reuse the `fetch_film.py` BEA client (or import a shared helper):
   GA + US + 5 peers, real (SAGDP9N) and nominal (SAGDP2N) totals, per-capita, and GA
   by-industry line codes.
3. **County CAGDP2** — one BEA call at `GeoFips=COUNTY` (LineCode 1) for all GA counties;
   keep total GDP + derive per-capita using PEP/ACS population already in the repo.
4. **Non-Metro aggregate** — sum the 86 non-metro counties (reuse the `_metro_ga_fips()`
   helper pattern from `fetch_housing.py`).
5. Emit `data/gdp.json` with `_meta` (per-section `last_updated` + staleness), `kpis`,
   `ga_gdp` (real/nominal/per-capita series), `peers`, `sectors`, `metros[]`, `counties`,
   `non_metro`, `source_summary`, `fetched_at`. Same graceful-degradation rules as
   `fetch_housing.py` (preserve prior on failure, don't bump timestamps, badge if stale).
6. `--rollup` flag for local validation without BEA keys (metro layer only), mirroring
   `fetch_housing.py`.

`data/gdp.json` shape (sketch):

```json
{
  "_meta": { "ga_gdp": {...}, "county_gdp": {...}, ... },
  "fetched_at": "...", "latest_label": "2024",
  "kpis": { "ga_nominal_gdp_bn": ..., "ga_real_gdp_yoy": ..., "ga_gdp_per_capita": ...,
            "ga_rank": ..., "largest_sector": "...", "non_metro_county_count": 86 },
  "ga_gdp": { "years": [...], "real_bn": [...], "nominal_bn": [...],
              "us_real_bn": [...], "per_capita": [...], "real_yoy": [...] },
  "peers": [ {"state": "Georgia", "real_yoy": ..., "per_capita": ...}, {"state":"Florida",...} ],
  "sectors": [ {"name": "Manufacturing", "gdp_bn": ..., "share_pct": ...}, ... ],
  "metros": [ {"short_name":"Atlanta","gmp_bn":...,"gdp_per_capita":...,"share_of_state_pct":...,
               "pi_bn":...,"per_capita_income":...}, ... ],
  "counties": { "13121": {"gdp_bn": ..., "gdp_per_capita": ...}, ... },
  "non_metro": { "county_count": 86, "gdp_bn": ..., "gdp_per_capita_wt": ... }
}
```

---

## Automation

Fold into `update-msa-reports.yml` after the metro reports regenerate (the pattern just
adopted for Housing) — add a "Build statewide GDP roll-up" step running
`python3 scripts/fetch_gdp.py` with `BEA_API_KEY` (+ existing keys) in env, and add
`data/gdp.json` to the commit `git add` line. BEA SAGDP/CAGDP2 are annual, so a monthly
cadence is plenty; folding in keeps it reading fresh metro JSONs.

---

## Build checklist

- [ ] `scripts/fetch_gdp.py` — roll-up reader + SAGDP statewide/peers/sectors + CAGDP2 county + non-metro
- [ ] Factor the `fetch_film.py` BEA SAGDP client into a small shared helper (or import) to avoid duplication
- [ ] `data/gdp.json` — generated, with `_meta` staleness scaffolding
- [ ] `gdp/index.html` — 8 sections per layout, topic-page template, reuse Housing's choropleth + pending patterns
- [ ] Add `data/gdp.json` to the `update-msa-reports.yml` commit + a fetch step
- [ ] Flip the home-page "State GDP" card from "Coming soon" → "Live now" (it links to `/gdp/`)
- [ ] Nav already updated globally for Housing — add `/gdp/`? (decision below)
- [ ] Update `REPORT_STATUS.md` / `REPORT_STATUS_MATRIX.md`

## Acceptance criteria

- `/gdp/` renders real & nominal GDP, per-capita, SE-peer comparison, sector composition,
  a 159-county map, a non-metro aggregate, and a 14-metro roll-up.
- Full state coverage: statewide SAGDP (100%) + all-159-county CAGDP2 + non-metro aggregate.
- No fixture/demo leakage — every series traces to a `live` section or a real BEA pull;
  key-gated sections show a pending/stale state, not fabricated numbers.
- Home-page "State GDP" card reads "Live now" → `/gdp/`.
- Folds into the nightly workflow and commits `data/gdp.json`; passes `lint-html.yml`.

## Open questions for you

1. **Top nav vs. home-grid only.** The header nav is now Home / Counties / Metros / Labor /
   Housing / About. Adding GDP makes 7 items — fine, or keep GDP to the home-grid like
   Inflation/Population/Trade and leave nav as-is? (Leaning: keep nav as-is; link from the
   home grid, to avoid nav bloat.)
2. **Real vs nominal default.** Lead the headline trend with **real** GDP (better for
   growth) and offer a nominal toggle — agree? (Leaning: yes.)
3. **County GDP denominator.** Use BEA's own county GDP-per-capita if exposed, or derive
   per-capita from CAGDP2 ÷ PEP population we already pull? (Leaning: derive, for a
   consistent population base across pages.)
