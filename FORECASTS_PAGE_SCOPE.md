# Forecast & Business-Cycle Hub — Build Scope

**Route:** `/outlook/` (net-new — no dir yet)
**Status:** Scoped 2026-06-02 · Phase 4, WS2, item #2
**One-line:** A statewide Georgia "where are we in the cycle / where are we headed" page:
a coincident business-cycle index, a 5-year forecast of the headline indicators, and
metro roll-ups of both — all reusing the modeling code the metro reports already run.

---

## Why this one is distinctive (and cheap)

We already compute, per metro, a **Stock-Watson coincident business-cycle index**
(`business_cycle_index`, live, monthly, rebased 100 = 2019-01 with peak/trough) and a
**5-year damped-Holt forecast** (`forecast_arima`, live, 2026–2030 across GMP, employment,
unemployment, personal income, population, HPI, permits). Nobody else publishes either for
Georgia.

The key realization: both modeling helpers expose `compute(cbsa, output_so_far)` and read
their inputs from a `sections` dict. So we can produce a **statewide** GA cycle index and
forecast by feeding **Georgia actuals we already have** into the *same* functions — no new
methodology, no new external API pulls:

| Helper input section | Statewide source (already in repo) |
|---|---|
| `ces_employment` {months, values} | `labor.json.total_payrolls_k` |
| `laus_unemployment` {months, values} | `labor.json.unemployment_rate` |
| `bea_gmp` {years, gmp_billions_usd} | `gdp.json.ga_gdp` (nominal_bn) |
| `census_pep` {years, population} | `population.json.state` |
| `fhfa_hpi` {quarters, values} | `housing.json.ga_hpi` |
| `bea_personal_income`, `census_bps_permits` | optional (skip gracefully if absent) |

So: **GA cycle index** needs only `labor.json` (always present → live immediately). The
**GA forecast** also reads `gdp.json` / `housing.json` (produced earlier in the same
nightly workflow) — employment/unemployment/population forecasts work from day one; the
GMP/HPI forecast fills in once those JSONs populate. The metro roll-ups (cycle + forecast)
are zero-pull reads from the 14 reports.

---

## ⚠️ Forecast disclaimer (must be prominent on the page)

The 5-year figures are **model extrapolations** (damped-Holt / ARIMA-family), computed
mechanically from historical actuals — **not official forecasts, predictions, or
investment advice**, and they don't incorporate policy, shocks, or expert judgment. The
page must carry a clear, visible disclaimer near the forecast (not buried in the footer),
and frame the numbers as "if recent trends persist." Lead with the **business-cycle index**
(a description of where the economy *is*, less speculative) before the forecast.

---

## Data sources

| Section | Source | New pull? |
|---|---|---|
| GA business-cycle index | `business_cycle_index.compute("GA", ga_sections)` from `labor.json` | No (reuse helper) |
| GA 5-year forecast | `forecast_arima.compute("GA", ga_sections)` from labor/gdp/population/housing | No (reuse helper) |
| Metro cycle roll-up | `business_cycle_index` section in the 14 reports (latest, peak, % from peak) | No (roll-up) |
| Metro forecast roll-up | `forecast_arima` section in the 14 reports (2026–2030 GMP/employment) | No (roll-up) |

No new external API calls anywhere — this is pure reuse + assembly.

---

## Page layout (`/outlook/index.html`)

Topic-page template + reuse the Housing/GDP/Migration patterns (KPI strip, pending/stale
scaffolding, sortable tables).

1. **KPI strip** — GA cycle index (latest) + Δ vs its peak, cycle direction
   (expanding / slowing / contracting from the last few months' slope), forecast real
   GMP growth (2030), forecast employment growth (2030), # metros above their pre-pandemic
   peak, latest data month.
2. **Georgia business-cycle index** *(marquee)* — monthly line 2019→now, rebased 100,
   peak/trough markers, the COVID trough visible; annotate latest vs peak. (1σ = 10 pts.)
3. **5-year outlook** *(with disclaimer banner)* — actuals→forecast for the headline
   indicators (GMP, employment, unemployment, population, HPI). Small-multiples or a
   metric switcher; actuals solid, forecast dashed. Prominent "model extrapolation" note.
4. **Where each metro is in its cycle** — table/bars: each metro's latest cycle index and
   % below/above its own peak → who's still recovering, who's rolling over. Roll-up.
5. **Metro growth outlook** — metros ranked by forecast 2026–2030 GMP (or employment)
   growth. Roll-up.
6. **Methodology + disclaimer** — Stock-Watson coincident index (2-input PCA on CES + LAUS),
   damped-Holt forecast, statewide inputs, "not official forecasts / illustrative only."

No county choropleth here (cycle/forecast aren't county-level); coverage is statewide +
14 metros. (Note in methodology that this page is metro/state-level, not county.)

---

## Data wiring — `scripts/fetch_forecasts.py` → `data/outlook.json`

1. **Build GA `sections` dict** from `labor.json` (transform the `[[month,val],…]` pairs
   to `{months, values}`), `gdp.json.ga_gdp`, `population.json.state`, `housing.json.ga_hpi`.
2. **Statewide compute (reuse helpers):**
   `sys.path` in `scripts/` + `scripts/modeling/`; call
   `business_cycle_index.compute("GA", {"sections": ga_sections})` and
   `forecast_arima.compute("GA", {"sections": ga_sections})`. Wrap each in try/except.
3. **Metro roll-up:** read the 14 `msa_reports/*.json`; pull `business_cycle_index`
   (latest_value, peak_value, % from peak) and `forecast_arima` (2026–2030 gmp / employment
   + the implied CAGR) per metro.
4. Emit `data/outlook.json` with `_meta` (per-section staleness), `kpis`, `ga_cycle`,
   `ga_forecast`, `metros[]`, `disclaimer`, `source_summary`, `fetched_at`. Same
   graceful-degradation rules as the other Phase-4 fetchers; `--rollup` flag for local
   validation (metro roll-up + GA cycle from labor.json; GA forecast where inputs exist).

`data/outlook.json` shape (sketch):

```json
{
  "_meta": { "ga_cycle": {...}, "ga_forecast": {...}, "metro_rollup": {...} },
  "fetched_at": "...", "latest_label": "2026-03",
  "disclaimer": "Model extrapolation from historical actuals — not an official forecast...",
  "kpis": { "cycle_latest": 113.9, "cycle_vs_peak": -1.3, "cycle_direction": "slowing",
            "fc_gmp_growth_2030": ..., "fc_emp_growth_2030": ..., "metros_above_peak": 9 },
  "ga_cycle": { "months": [...], "values": [...], "latest_value": ..., "peak_*": ..., "trough_*": ... },
  "ga_forecast": { "years": [2026..2030], "gmp": [...], "gmp_yoy": [...], "total_employment": [...],
                   "unemployment": [...], "population": [...], "hpi_yoy": [...], "method": "..." },
  "metros": [ {"short_name":"Atlanta","cycle_latest":...,"pct_from_peak":...,
               "fc_gmp_cagr":...,"fc_emp_cagr":...}, ... ]
}
```

---

## Automation

Fold into `update-msa-reports.yml` **after** the housing + GDP steps (so `gdp.json` /
`housing.json` exist for the forecast inputs) — add a "Build outlook (cycle + forecast)"
step running `python3 scripts/fetch_forecasts.py` and add `data/outlook.json` to the
commit. No API keys needed. (It also reads `labor.json` / `population.json`, refreshed by
their own workflows; it reads whatever is current — acceptable, both are monthly.)

---

## Build checklist

- [ ] `scripts/fetch_forecasts.py` — GA sections builder + helper reuse + metro roll-up
- [ ] `data/outlook.json` — generated with `_meta` staleness
- [ ] `outlook/index.html` — 5 sections + prominent forecast disclaimer; reuse patterns
- [ ] Add an **Outlook / Forecast** card to the home grid (net-new)
- [ ] Fold a fetch step + `data/outlook.json` into `update-msa-reports.yml` (after housing/GDP)
- [ ] Update `REPORT_STATUS.md` / `REPORT_STATUS_MATRIX.md`

## Acceptance criteria

- `/outlook/` renders the GA cycle index, the 5-year forecast (with a visible disclaimer),
  a metro cycle roll-up, and a metro growth-outlook roll-up.
- Statewide cycle + forecast are computed by the *existing* helpers from GA actuals — no
  new external pulls; methodology matches the metro reports.
- No fixture/demo leakage; sections with missing inputs show a pending/stale state.
- New home-grid card links to `/outlook/`; passes `lint-html.yml`.

## Open questions for you

1. **Route / title.** `/outlook/` titled "Economic Outlook" (covers cycle + forecast), or
   `/forecasts/` titled "Forecasts"? (Leaning: `/outlook/` — broader, fits the cycle index
   which isn't a forecast.)
2. **Lead section.** Lead with the **business-cycle index** (current state, less
   speculative), forecast second behind the disclaimer? (Leaning: yes.)
3. **Forecast headline metric.** Rank the metro growth-outlook by **GMP** or **employment**
   growth? (Leaning: GMP, with an employment toggle.)
