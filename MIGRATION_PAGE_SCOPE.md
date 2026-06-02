# Migration Page — Build Scope

**Route:** `/migration/` (net-new — no dir yet; sibling to `/population/`)
**Status:** Scoped 2026-06-02 · Phase 4, WS2, item #1 (first "unbury the MSA depth" win)
**One-line:** A statewide Georgia migration page centered on *flows* — where people move
to and from Georgia (state-to-state), a 159-county net-migration map, the domestic /
international / natural split over time, which metros attract movers, and a non-metro
aggregate. The distinctive thing nobody else surfaces for GA.

---

## Why this is the next build

Lowest data cost yet. Almost everything already exists:

- **County layer (all 159):** `data/population.json` carries `dom_mig_total`,
  `intl_mig_total`, `natural_total`, and `pop_latest` for **every county** — a full
  net-migration choropleth with **zero new pulls**.
- **Statewide trend:** `population.json.state` has `dom_mig` / `intl_mig` / `net_mig`
  (and `natural`) history (Census PEP) — zero new pulls.
- **Metro roll-up:** every MSA report has `irs_soi_migration` (total in/out/net + top
  origin/destination) and `census_net_migration` (domestic/intl/net timeseries), both
  `live` — roll-up ready.

The **only genuinely new code** is a state-level aggregation of the IRS SOI flows (GA's
inflows by origin state, outflows by destination state) — and even that **reuses the
existing CSV download + parser** in `scripts/reporting/pull_irs_soi.py`, which already
pulls and parses the county-to-county files and carries a state-FIPS → name map.

---

## Division of labor with the Population page

`/population/` already covers totals, components of change, age pyramid, race/ethnicity,
and SE peers. To avoid duplication, `/migration/` focuses on **flows and geography**:
state-to-state movement, the county net-migration map, metro attraction, and non-metro.
The two pages cross-link. (The components-of-change trend appears on both, framed
differently — Population as "what changes the headcount," Migration as "migration vs.
natural change.")

---

## Data sources

| Section | Source | New pull? |
|---|---|---|
| State-to-state flows (marquee) | IRS SOI county files, **aggregated to GA state level** | New aggregation, **reuses** `pull_irs_soi` download/parse |
| Components trend (domestic/intl/natural) | `population.json.state` (Census PEP) | No |
| County net-migration map (159) | `population.json.counties[].dom_mig_total` (+ `pop_latest` for a per-1k rate) | No |
| Metro attraction roll-up | 14 reports: `irs_soi_migration` + `census_net_migration` | No (roll-up) |
| Non-Metro Georgia aggregate | sum `dom_mig_total` over the 86 non-metro counties | No |

IRS SOI is tax-return-based (~1.5-year lag) and is what we already parse, so it's the
default for origin/destination. (Census ACS state-to-state flows are an alternative if we
ever want a second view — noted, not in v1.)

---

## Page layout (`/migration/index.html`)

Topic-page template + reuse the Housing/GDP choropleth and pending-state patterns.

1. **KPI strip** — net domestic migration (latest year), net international migration, top
   origin state, top destination state, net SOI exchange (in − out), non-metro county count.
2. **Where Georgians move to & from** *(marquee)* — two ranked bars: top origin states
   (inflow) and top destination states (outflow), from the state-level SOI aggregation,
   with net-by-state highlighted (gaining vs losing exchanges, e.g. GA vs Florida).
3. **Migration over time** — stacked/line: domestic vs international vs natural change,
   plus net migration, from PEP state history.
4. **County net-migration map (159)** — choropleth, toggle net domestic migration (count)
   / net migration per 1,000 residents. Diverging color scale (gaining = teal, losing =
   coral). Covers the 86 non-metro counties too.
5. **Which metros attract movers** — metro roll-up table/bars: IRS SOI net + PEP net
   migration per metro; who's gaining the most.
6. **Non-Metro Georgia** — aggregate net domestic migration + population for the 86
   counties outside the MSAs.
7. **Methodology** — IRS SOI vs PEP, the ~1.5-yr SOI lag, return-count caveat, staleness.

Use `maps.js` `drawGAChoropleth` with `colorscale: 'diverging'` for the net-migration map
(positive vs negative flows), `valueFormatter` for signed counts / per-1k.

---

## Data wiring — `scripts/fetch_migration.py` → `data/migration.json`

1. **Local reads (no key):** `population.json` for the state components trend, the 159-county
   layer, and the non-metro aggregate; roll up the 14 `msa_reports/*.json`
   (`irs_soi_migration` + `census_net_migration`).
2. **State-to-state flows (new):** add `fetch_state_flows()` to `pull_irs_soi.py` (or a
   thin wrapper in `fetch_migration.py`) that reuses `_discover_latest_year`, `_fetch_csv`,
   and `_parse_migration_csv`, then aggregates: inflow rows with destination state FIPS 13
   summed by **origin state**; outflow rows with origin state FIPS 13 summed by
   **destination state**; compute net by state. Emit top origin/destination states + the
   GA totals (in/out/net).
3. Emit `data/migration.json` with `_meta` (per-section staleness), `kpis`, `state_flows`,
   `components` (PEP trend), `counties` (net-mig + rate, all 159), `metros[]`, `non_metro`,
   `source_summary`, `fetched_at`. Same graceful-degradation rules as `fetch_housing.py` /
   `fetch_gdp.py`; `--rollup` flag for local validation (everything but the live SOI
   state-flow pull, which is the only network-dependent piece).

Note: the county layer + components trend come from `population.json`, which the
`update-population.yml` workflow refreshes — so most of this page is "live" the moment it
deploys, even before the SOI state-flow pull runs.

`data/migration.json` shape (sketch):

```json
{
  "_meta": { "state_flows": {...}, "components": {...}, "counties": {...}, ... },
  "fetched_at": "...", "latest_label": "2022→2023",
  "kpis": { "net_domestic": ..., "net_international": ..., "top_origin_state": "Florida",
            "top_dest_state": "Florida", "net_soi": ..., "non_metro_county_count": 86 },
  "state_flows": { "year_pair_label": "2022→2023", "total_in": ..., "total_out": ..., "net": ...,
                   "top_in": [{"state":"Florida","n_returns":...}, ...],
                   "top_out": [{"state":"Florida","n_returns":...}, ...],
                   "net_by_state": [{"state":"New York","net":...}, ...] },
  "components": { "years": [...], "domestic": [...], "international": [...], "natural": [...], "net": [...] },
  "counties": { "13001": {"net_domestic": 536, "per_1k": ..., "name": "Appling"}, ... },
  "metros": [ {"short_name":"Atlanta","soi_net":...,"pep_net":...,"total_in":...,"total_out":...}, ... ],
  "non_metro": { "county_count": 86, "net_domestic": ..., "population": ... }
}
```

---

## Automation

Fold into `update-msa-reports.yml` after the metro reports + housing/GDP roll-ups (it
reads `population.json` + the metro JSONs, both refreshed earlier in the run). Add a
"Build migration roll-up" step running `python3 scripts/fetch_migration.py` and add
`data/migration.json` to the commit. SOI is annual; monthly cadence is plenty. (If we'd
rather keep it close to its inputs, it could instead live in `update-population.yml` —
decision below.)

---

## Build checklist

- [ ] `fetch_state_flows()` in `pull_irs_soi.py` (state-level GA aggregation, reusing existing download/parse)
- [ ] `scripts/fetch_migration.py` — population.json reads + metro roll-up + SOI state flows + non-metro
- [ ] `data/migration.json` — generated with `_meta` staleness
- [ ] `migration/index.html` — 7 sections, topic-page template, diverging choropleth, pending patterns
- [ ] Add a **Migration** card to the home-page topic grid (net-new; no stub to flip)
- [ ] Fold a fetch step + `data/migration.json` into the chosen workflow
- [ ] Cross-link Population ⇄ Migration
- [ ] Update `REPORT_STATUS.md` / `REPORT_STATUS_MATRIX.md`

## Acceptance criteria

- `/migration/` renders state-to-state flows, the components trend, a 159-county
  net-migration map, the metro attraction roll-up, and a non-metro aggregate.
- Full state coverage: PEP statewide + all-159-county layer + non-metro aggregate.
- No fixture/demo leakage; the SOI state-flow section shows a pending/stale state if its
  pull fails, never fabricated flows.
- New home-grid Migration card links to `/migration/`; passes `lint-html.yml`.

## Open questions for you

1. **Workflow home.** Fold the fetch into `update-msa-reports.yml` (consistent with
   Housing/GDP) or `update-population.yml` (closer to its `population.json` input)?
   (Leaning: `update-msa-reports.yml`, for consistency.)
2. **County map default metric.** Net domestic migration **count** or **per-1,000-resident
   rate**? (Leaning: per-1k as default — fairer across big/small counties — with a count
   toggle.)
3. **Flow detail depth.** Top ~10 origin/destination **states** in v1 (clean, national
   story), or also drill to top out-of-state **metros**? (Leaning: states in v1; metros a
   fast follow.)
