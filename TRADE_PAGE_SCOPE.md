# Trade Page — Thickening Scope

**Route:** `/trade/` (already live — ports fixtures + top-10 export countries + lead indicator)
**Status:** Scoped 2026-06-03 · Phase 4, WS3 (thicken thin statewide pages)
**One-line:** Move the export story beyond a single-year top-country snapshot — add a
multi-year total-exports trend and an HS-chapter commodity breakdown, both from Census USA
Trade Online (the same endpoint already wired for the country table).

---

## Why this is cheap

`scripts/fetch_trade.py` already talks to Census USA Trade Online
(`api.census.gov/data/timeseries/intltrade/exports/statehs`) and has a battle-tested,
multi-strategy query loop (`fetch_ga_exports_by_country_annual`) plus aggregate-row
filtering. The two additions reuse that same plumbing with different group-by fields:

1. **Multi-year total exports** — sum GA's exports across **all** countries for each of the
   last ~6 years → an annual trend line (GA total goods exports, $B). Already computed
   per-year as a by-product of the country pull; we just keep the annual totals.
2. **Commodity breakdown** — query with `COMM_LVL=HS2` (the 2-digit HS *chapter* level) and
   `CTY_CODE=-` (all destinations) to get exports by product chapter for the latest year,
   mapped to readable chapter names (aircraft & vehicles, machinery, agricultural products,
   etc.). Top ~10 chapters + an "all other" remainder, with YoY.

The ITA *MSA* exports endpoint stays out of scope — it's still partially blocked
(`reference_ita_exports_endpoint_dead`). This is **state-level** Census data, which works.

---

## Data sources

| Source | Query | Drives |
|---|---|---|
| Census USA Trade Online — `exports/statehs` | existing per-country loop, annual totals retained for last 6 yrs | Multi-year total-exports trend |
| Census USA Trade Online — `exports/statehs`, `COMM_LVL=HS2`, all countries | new query | Top export commodities (HS chapters) |
| (unchanged) per-country annual | existing | Existing top-10 destinations table |

Ports (Savannah TEU, Brunswick autos, ATL cargo) remain on calibrated fixtures — no public
API; out of scope for this thickening (still a Tavily/GPA-press-release task for later).

---

## New `data/trade.json` keys

```jsonc
"exports_annual": {                      // multi-year total trend
  "years":  [2020, 2021, 2022, 2023, 2024, 2025],
  "total_musd": [38120, 42890, 49510, 47220, 44980, 45110],
  "latest_year": 2025, "latest_total_musd": 45110,
  "yoy_pct": 0.3, "cagr_pct": 3.4
},
"exports_by_commodity": {                // HS-chapter breakdown, latest year
  "year": 2025,
  "chapters": [
    { "hs2": "88", "name": "Aircraft & spacecraft", "value_musd": 8120, "share_pct": 12.1, "yoy_pct": 4.2 },
    { "hs2": "87", "name": "Vehicles", "value_musd": 6890, "share_pct": 10.3, "yoy_pct": -2.1 }
    // … top ~10 chapters
  ],
  "other_musd": 14230, "total_musd": 67010
},
"_meta": {
  "exports_annual":       { "last_updated": "…Z", "stale": false },
  "exports_by_commodity": { "last_updated": "…Z", "stale": false }
}
```

Existing keys (`savannah_teu_k`, `brunswick_autos_k`, `atl_cargo_kt`, `atl_tw_employment_k`,
`ga_exports_latest`, `ga_exports_top10_total_musd`, `kpis`) are untouched.

A small HS2→name lookup (the ~20 chapters that matter for GA: 84 machinery, 87 vehicles,
88 aircraft, 85 electrical, 39 plastics, 48 paper, 02/10/12 ag, 29/30 chemicals/pharma,
52 cotton, 44 wood, etc.) lives in the fetch script; unknown chapters fall back to
`"HS {code}"`.

---

## Page additions (`trade/index.html`)

- **Section: "Georgia goods exports — the long view"** — a line/bar of annual total exports
  ($B) over the last 6 years, with a CAGR + latest-YoY callout. Replaces the implicit
  single-year framing of the destinations table with a trend.
- **Section: "What Georgia ships — exports by commodity"** — a horizontal bar of the top HS
  chapters (share of total) for the latest year, with YoY coloring (teal up / coral down),
  plus an "all other" bar. A short note that destinations and commodities come from the same
  Census state-export series.
- Two new KPIs (or repurpose the strip): total goods exports ($B, latest year) and its YoY.
- Pending/stale guards: both sections hide behind a `pending` note if the new keys are
  absent, so a Census hiccup can't blank the page.

---

## Wiring & degradation

- `fetch_trade.py` gains `build_exports_annual()` and `build_exports_by_commodity()`, both
  reusing `http_get_json` + `_parse_census_response`-style filtering. Each is wrapped so a
  failure preserves the prior value and leaves the rest of `trade.json` live.
- Runs in the existing `update-trade.yml` (already has `CENSUS_API_KEY`) — **no workflow
  topology change needed** (unlike Labor, Trade has no metro-report dependency). Optionally
  add a `--commodities-only`/offline guard for local testing, but the default run covers it.

---

## Acceptance

- `/trade/` shows the multi-year export trend and the commodity breakdown, both live from
  Census.
- `data/trade.json` carries `exports_annual` + `exports_by_commodity` with `_meta`
  timestamps; existing sections unchanged.
- The page degrades gracefully (pending notes) when the new sections are missing; HTML lints
  clean.
