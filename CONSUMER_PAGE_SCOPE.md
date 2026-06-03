# Consumer Page — Build Scope

**Route:** `/consumer/` (new — the empty `consumer/` dir already exists)
**Status:** Scoped 2026-06-03 · Phase 4, WS1 (the last "Coming soon" home stub)
**One-line:** A statewide Georgia consumer-spending page anchored on BEA state personal
consumption expenditures (PCE), with EIA Georgia residential-electricity demand as a monthly
proxy, a real-wage/purchasing-power tracker, and a best-effort sales-tax/retail proxy. Flips
the last "Coming soon" home card to "Live now."

---

## Why this is the last/hardest one

Unlike Housing/GDP/Labor/Trade, there is **no existing pipeline** and the metro reports don't
carry consumer data, so this is genuinely new wiring. But it stays inside the established
playbook — BEA / EIA / FRED / Census with existing repo secrets, graceful degradation,
monthly cron. No scraping required for the anchor sections.

---

## Data sources (keys all present in repo secrets)

| Section | Source | Key | Notes |
|---|---|---|---|
| **PCE — total + composition** | BEA Regional `SAPCE1` (total / goods / durable / nondurable / services), annual | `BEA_API_KEY` | Same GetData mechanics as `fetch_gdp.py` (`SAGDP*`). Anchor section. |
| **PCE — per-capita, SE peers** | BEA `SAPCE3` per-capita PCE, all states one year | `BEA_API_KEY` | GA vs FL/NC/SC/TN/AL + US, latest year. Mirrors GDP peer chart. |
| **PCE — by function (bonus)** | BEA `SAPCE2` (housing/utilities, health, food, transport, recreation…) via dynamic LineCode discovery | `BEA_API_KEY` | Latest-year composition. Degrades to the SAPCE1 goods/services split if unavailable. |
| **Georgia residential electricity** | EIA API v2 `electricity/retail-sales` (sales MWh + price ¢/kWh), `stateid=GA`, `sectorid=RES`, monthly | `EIA_API_KEY` | New integration. Consumer-demand + cost-of-living proxy. |
| **Purchasing power / real wages** | reuse `data/inflation.json → real_wages` (already live, BLS) | — | No new pull; ties the consumer story to inflation. |
| **Sales-tax / retail proxy (best-effort)** | FRED GA sales-tax/retail candidate series, multi-ID fallback | `FRED_API_KEY` | Heavily guarded; page is solid without it (degrades to absent). |

BEA PCE units: SAPCE1 = millions of current $; SAPCE3 = dollars (per-capita). EIA: sales in
million kWh, price in cents/kWh.

---

## `data/consumer.json` shape

```jsonc
{
  "fetched_at": "…Z", "schema": "consumer/v1", "latest_label": "2024",
  "kpis": {
    "pce_total_bn": 540.2, "pce_per_capita": 48910, "pce_yoy_pct": 4.1,
    "elec_price_cents_kwh": 14.2, "elec_price_yoy_pct": 3.0,
    "real_wage_yoy_pct": 1.2
  },
  "pce_trend":      { "years": [...], "ga_total_musd": [...], "us_total_musd": [...], "yoy_pct": [...] },
  "pce_composition":{ "year": 2024, "goods_musd": ..., "durable_musd": ..., "nondurable_musd": ..., "services_musd": ..., "total_musd": ... },
  "pce_by_function":{ "year": 2024, "functions": [{ "name": "Housing & utilities", "value_musd": ..., "share_pct": ... }, …] },
  "pce_peers":      { "year": 2024, "states": [{ "fips":"13000","name":"Georgia","per_capita": ... }, …], "us_per_capita": ... },
  "electricity":    { "months": [...], "sales_gwh": [...], "price_cents_kwh": [...], "latest_month": "…", "source": "EIA v2 electricity/retail-sales, GA residential" },
  "real_wages":     { "months": [...], "index": [...] },   // copied from inflation.json
  "sales_tax":      { "quarters": [...], "values_musd": [...], "source": "…" },   // optional
  "_meta": { "<section>": { "last_updated": "…Z", "stale": true|false } },
  "coverage_note": "…", "source_summary": { … }
}
```

Each section is wrapped in try/except; on failure it preserves the prior value (without
bumping `last_updated`) and the page renders a `stale` badge when > `STALE_MONTHS` old.
BEA PCE is annual → `STALE_MONTHS = 14`.

---

## Page (`consumer/index.html`)

Follows the GDP/Labor template (shared chrome via `GEN:` markers, `app.js` not deferred,
Chart.js; no map needed). Sections:

1. **KPI strip** — total PCE ($B), per-capita PCE ($), PCE YoY, residential electricity price
   (¢/kWh + YoY), real-wage YoY.
2. **Consumer spending over time** — GA vs US total PCE ($B) line.
3. **What Georgians spend on** — composition (goods/durable/nondurable/services) and, when
   available, the by-function breakdown (housing, health, food, transport, recreation…).
4. **Per-capita spending vs Southeast peers** — GA vs FL/NC/SC/TN/AL + US bar.
5. **Georgia Power / residential electricity** — monthly consumption (GWh) + price (¢/kWh)
   dual-axis. The most current monthly consumer signal on the page.
6. **Purchasing power** — real-wage index line (reused from inflation).
7. *(if live)* **Taxable spending proxy** — sales-tax/retail trend.

Each network section has a `pending` note + `stale-badge` guard.

---

## Wiring & connective tissue

- **New** `scripts/fetch_consumer.py` → `data/consumer.json` (with `--offline`/no-key
  graceful degradation for local testing).
- **New** `.github/workflows/update-consumer.yml` — monthly cron; env `BEA_API_KEY`,
  `EIA_API_KEY`, `FRED_API_KEY`, `CENSUS_API_KEY`; `fetch-depth: 0` + rebase-and-retry push
  (matches the other `update-*.yml`). Path-filter on `scripts/fetch_consumer.py` so the first
  push triggers a live populate.
- **Home card** — flip the `consumer` card from "Coming soon" to a live `<a … href="/consumer/">`
  with accurate copy; add **Consumer** to `partials/header.html` Topics menu (then
  `build_site.py` re-stamps every page).
- **Scorecard** — add a Consumer KPI card to `scripts/build_scorecard.py` (per-capita PCE or
  PCE YoY) and include `data/consumer.json` in the search index + the msa-reports commit list
  if appropriate.

---

## Acceptance

- `/consumer/` renders the PCE anchor, electricity, peers, and purchasing-power sections;
  flips the last home stub to "Live now."
- `python scripts/fetch_consumer.py` populates `data/consumer.json` live; runs without a key
  in a degraded mode without crashing.
- EIA puller smoke-tested against the live v2 endpoint before deploy.
- HTML lints clean; the page degrades gracefully when any section is absent.
