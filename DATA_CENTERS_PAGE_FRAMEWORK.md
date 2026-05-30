# Data Centers Page — Framework & Proposal

**Status:** Planning doc. Nothing built yet. Review before I scaffold.
**Proposed URL:** `/industries/data-centers/`
**Pattern reference:** mirrors `/industries/film/` and `/industries/automotive/` (live BLS + BEA + Tavily pipelines, monthly cron, `_meta` staleness badges).

**Seed-data status (updated 2026-05-30):** No longer blocked. Alfie supplied a Costar export of 138 Georgia data centers (74 existing, 19 under construction, 41 proposed, 3 final planning, 1 deferred), saved at `data/seeds/costar_data_centers_ga_2026-05-27.xlsx`. The MW-sourcing problem that previously paused this page is resolved by a layered approach: Costar as base, DECD press releases + Tavily for new announcements, EPD water permits as cross-check for private/enterprise facilities Costar misses. See §6.

---

## 1. Why this page belongs on the site

Atlanta has quietly become the #3–#4 U.S. data center market by absorption, behind Northern Virginia and Dallas. It's already a top-5 market by under-construction MW. The story has three intertwined threads that none of the existing site pages capture:

- **Capex and jobs** — multi-billion-dollar facility builds, mostly in 5–6 metro counties.
- **Power load** — Georgia Power's 2025 IRP forecasts the largest load growth in the utility's history, with data centers as the dominant driver. That's a state-economy-level story, not an industry story.
- **Policy fight** — the sales-tax exemption (HB 1192 in 2024) is live again in the 2025–26 session. Tax-base, water, and grid-cost debates are escalating.

This is exactly the kind of page economicsguru.com readers expect from us: state-relevant, data-rich, neutral framing.

---

## 2. Page structure (top → bottom)

```
┌──────────────────────────────────────────────────────────────┐
│  HERO                                                        │
│  ─ Headline: "Georgia's Data Center Economy"                 │
│  ─ Subhead: NAICS 518210 + adjacent infrastructure           │
│  ─ 4 KPI cards (see §3)                                      │
│  ─ Staleness banner (orange if any section >6 mo old)        │
├──────────────────────────────────────────────────────────────┤
│  SECTION A — Economic Impact (the "industry page" half)      │
│  ─ Jobs trend chart (12 yr, BLS QCEW NAICS 518210)           │
│  ─ Industry GDP trend (12 yr, BEA SAGDP2N "Information")     │
│  ─ Average wage vs all-private (BLS QCEW)                    │
│  ─ Cumulative announced capex (Tavily, sanity-bounded)       │
│  ─ State benchmark chart (top 10 + GA pinned)                │
├──────────────────────────────────────────────────────────────┤
│  SECTION B — County-Level Geography                          │
│  ─ Plotly choropleth of GA counties, colored by establishment│
│    count (Census CBP) or operating MW (Tavily/industry data) │
│  ─ County rankings table: top 15 by MW + by jobs             │
│  ─ Atlanta-vs-rest-of-state split callout                    │
├──────────────────────────────────────────────────────────────┤
│  SECTION C — Capacity & Infrastructure                       │
│  ─ Operating MW vs Under-Construction MW vs Planned MW       │
│    (stacked bar by county or by operator)                    │
│  ─ Georgia Power load-growth chart                           │
│    (historical actual vs IRP forecast, 2015–2035)            │
│  ─ Data center share of total GA Power load (%, projected)   │
│  ─ Water-use estimate (gallons/day, with methodology note)   │
├──────────────────────────────────────────────────────────────┤
│  SECTION D — Major Facilities Scorecard                      │
│  ─ Card grid: 12–20 facilities                               │
│    Operator · County · MW (operating/announced) · Status     │
│  ─ Status colors: operating / under-construction /           │
│    announced / paused (same scheme as automotive page)       │
├──────────────────────────────────────────────────────────────┤
│  SECTION E — Policy & Incentives Tracker                     │
│  ─ Sales-tax-exemption status timeline (2018 → present)      │
│  ─ Estimated forgone state revenue (DOR data + Tavily)       │
│  ─ Active bills tracker (current session)                    │
│  ─ Major PSC decisions affecting data centers                │
├──────────────────────────────────────────────────────────────┤
│  SECTION F — Cross-state benchmark                           │
│  ─ Georgia vs NoVA, Dallas, Phoenix, Chicago, Columbus       │
│    on: MW operating, jobs, GDP, average wage                 │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. KPI cards (hero)

| Card | Number | Source | Update cadence |
|---|---|---|---|
| Data center jobs (GA) | ~8–12k est. | BLS QCEW NAICS 518210, area 13000 | Monthly |
| Industry GDP contribution | $ value-add | BEA SAGDP2N (Information sector + share allocation) | Annual |
| Operating MW | ~2,500–3,500 MW | Tavily → CBRE/JLL public summaries | Quarterly (best-effort) |
| Announced + under construction | additional MW | Tavily aggregation + press releases | Monthly |

**Honest caveat:** NAICS 518210 ("Data Processing, Hosting") is broader than physical data centers — it includes pure SaaS/cloud ops with no real estate. We should call this out the same way the film page distinguishes BEA NAICS 512 GDP from DECD's production-spend figure. The MW figure is the better single-number proxy for "how much data center is actually here."

---

## 4. Data sources, by section

| Section | Primary source | Automatable? | Cadence | Notes |
|---|---|---|---|---|
| Jobs trend | BLS QCEW (CSV, NAICS 518210, GA state + county) | ✅ Yes, already have `BLS_API_KEY` | Monthly | Same fetch pattern as film/automotive |
| GDP trend | BEA SAGDP2N | ✅ Yes, have `BEA_API_KEY` | Annual | Uses "Information" sector — caveat needed since it's broader than data centers |
| Wages | BLS QCEW | ✅ Yes | Monthly | Avg weekly wage by NAICS 518210 |
| Establishments by county | Census CBP, NAICS 518210 | ✅ Yes | Annual (lags ~18 mo) | County-level fidelity |
| Cumulative announced capex | Tavily search → press releases, DECD announcements | ⚠️ Partial — Tavily can scrape, but needs sanity bounds and human spot-check | Monthly | Sanity-bound $5B–$50B (similar to automotive EV capex) |
| Operating MW by facility | Tavily → CBRE / JLL / Cushman quarterly press summaries | ⚠️ Partial — these reports are paywalled but headlines and press releases give us the topline | Quarterly | This is the **load-bearing data risk**. See §6. |
| Georgia Power load forecast | PSC docket filings (georgia.gov) — Georgia Power IRP | ⚠️ Hard to automate cleanly | When IRP filings drop (irregular) | Tavily can pull headline numbers, but the IRP itself is hundreds of pages. May need to seed manually and refresh on filing dates. |
| Water use estimate | Modeled: MW × industry-standard gallons/MWh × 24×365 | ✅ Yes — derived from MW figure | Same cadence as MW | Methodology note required; this is an estimate, not measured |
| Tax exemption / forgone revenue | GA DOR annual tax expenditure reports | ⚠️ Annual PDF, Tavily can pull headline | Annual | DOR publishes total exemption claimed each year |
| Active legislation | Tavily → legis.ga.gov | ✅ Yes | Weekly during session, monthly otherwise | New workflow type — needs cron on the 1st |
| MW comparisons across states | Tavily → CBRE / JLL national reports | ⚠️ Partial | Quarterly | Topline numbers per market are usually in the press release |

**Status legend:**
- ✅ Yes = clean API or CSV, drop-in pattern from film/automotive
- ⚠️ Partial = Tavily scrape + sanity bounds + graceful degradation (preserve last good value)

---

## 5. Metrics worth tracking — full list

**Economic (Section A):**
- Total employment, NAICS 518210, GA (level + YoY)
- Average weekly wage, NAICS 518210, GA vs all-private
- Industry GDP contribution (or "Information sector" with caveat)
- Cumulative announced capex (running total since 2015 or 2020)
- Annual new capex announcements

**Geographic (Section B):**
- Operating facilities per county
- Operating MW per county
- Announced MW per county
- Share of Georgia data-center capacity in top-5 counties (concentration metric)

**Infrastructure (Section C):**
- Total operating MW
- Total under-construction MW
- Total announced MW (pre-construction)
- Georgia Power peak load actual vs forecast
- Data center share of Georgia Power total load (% historical + projected)
- Estimated water consumption (modeled)
- Avg MW per facility (concentration / hyperscale shift indicator)

**Policy (Section E):**
- Sales tax exemption claimed, $/year (DOR)
- Estimated forgone state revenue
- Active bills count + status
- PSC IRP-approved generation additions tied to data center load

**Comparison (Section F):**
- GA rank among U.S. data center markets (by MW, by jobs)
- 5-year MW growth rate vs peer markets

---

## 6. MW & facilities data — the layered source approach

Previously the blocker for this page. Resolved by combining four sources, each filling a specific gap:

**Layer 1 — Costar export (base inventory).** 138 records at `data/seeds/costar_data_centers_ga_2026-05-27.xlsx`. Pulled 2026-05-27. Strengths: 100% RBA (sq ft), 97% year built, county + lat/long, building status, operator names where applicable, ~31% have utility-capacity kW, ~18% have critical IT kW. Weaknesses: commercial real-estate dataset only (misses private enterprise rooms), 3–6 month lag on greenfield announcements, no automated refresh — Alfie has to re-export when Costar updates.

**Layer 2 — DECD press releases via Tavily.** Catches the 3–6 month Costar announcement lag. Tavily search against `gov.georgia.gov` + `decd.georgia.gov` + AJC, deduplicated against Costar by address/operator. Same automation pattern as the [[project_automotive_page_deployed]] plant-hint logic — surface as advisory until confirmed, never silently overwrite the Costar record.

**Layer 3 — Georgia EPD water-withdrawal permits.** One-time scrape (and periodic refresh) of EPD's permit database to find enterprise/private facilities Costar doesn't track (universities, hospitals, state agencies, banks). Bonus: gives us actual water-use data instead of modeled estimates for Section C.

**Layer 4 — Georgia PSC docket filings.** Georgia Power's IRP and certificate filings name specific large-load data center customers when not redacted. Free at psc.ga.gov. Anchors the credibility of Section C's load-growth chart.

**Refresh cadence:**
- Costar re-export: when Alfie does it (probably 1–2x/yr). Manual.
- DECD/Tavily: monthly, in the same cron as everything else
- EPD permits: quarterly Tavily run
- PSC filings: monthly Tavily run, but mostly idle (filings are irregular)

**Reconciliation logic** (worth getting right in the fetcher): when Tavily/DECD finds an announcement that isn't in Costar, it goes into a `_pending` bucket in `data/data_centers.json` with `source: "decd-press"` and a confidence flag, not directly into the canonical facility list. Human (Alfie) promotes it on the next pass. This keeps Costar's authority intact while surfacing newer info.

**Costar re-export reminder:** the data has a freshness stamp embedded in the filename (`_2026-05-27`). The fetcher checks this date and flips the `_meta.facilities` source badge to orange if it's >9 months old. That nudges Alfie to re-export without breaking anything.

---

## 7. Open questions for you

(Question 2 about a manual seed list is now resolved — Costar export at `data/seeds/costar_data_centers_ga_2026-05-27.xlsx` is the seed.)

1. **Page name / nav slot.** "Data Centers" under Industries? Or does this warrant its own top-level nav like Population and Inflation got? (My take: under Industries — it's a sector, not a cross-cutting topic.)

2. **Policy section depth.** Section E is the most editorial part of the page. Two options:
   - Light: just track exemption status + bill list + dollar amount.
   - Heavy: include a short interpretive paragraph that gets updated when the legislative status changes.
   I'd default to light — the data points speak for themselves and we avoid having to update prose.

3. **Water section.** With EPD permit data now available (Layer 3 in §6), we can probably do measured-not-modeled for the facilities that have permits, plus a modeled estimate for the rest. Want both, or just measured?

4. **Tavily budget.** This page would add ~6–10 more Tavily calls per monthly run on top of film + automotive. Want to consolidate into a single `update-industries.yml` workflow to control cost, or keep one workflow per page?

5. **Costar refresh ownership.** Costar requires manual re-export. Do you want the page to show a visible "Costar data as of [date]" badge so readers know the inventory date, or keep it implicit?

---

## 8. Implementation sketch (once approved)

Roughly the same shape as the automotive build:

- `scripts/fetch_data_centers.py` (~500 lines, one function per section)
- `data/data_centers.json` (calibrated seed + `_meta` block)
- `industries/data-centers/index.html` (replaces no existing stub — fresh page)
- `.github/workflows/update-data-centers.yml` — monthly cron, **22nd @ 18:00 UTC** (staggers from film/8th, population/12th, automotive/15th)
- Nav update in `index.html` and any other industry-landing pages
- Required secrets: `BLS_API_KEY`, `BEA_API_KEY`, `TAVILY_API_KEY` (all configured); optional `CENSUS_API_KEY`

Estimated build: one focused session. Probably 2–3 hours of work on my end once the seed list and the open questions above are settled.

---

## 9. What I would NOT do

- **No real-time load data.** Georgia Power doesn't publish hourly data center load — the IRP forecast is the cleanest available number.
- **No individual-customer power data.** PSC redacts this. Aggregated only.
- **No water data per facility.** Same redaction issue. Aggregate modeled estimate only.
- **No "is this a good thing?" framing.** Per the brief — neutral data, let readers decide.
- **No annual-manual-refresh promises.** Per [[feedback_full_automation]] — everything either has an API, a Tavily fallback, or it doesn't go on the page.
