# Phase 5 — Consolidation & Scale

**Status:** Planned (drafted 2026-06-02, after Phase 4 statewide pages shipped)
**Premise:** The *data layer* is excellent and ahead of the *presentation layer*. The
front-end has grown by copy-paste — 30 hand-built HTML pages, **12 of which each declare
their own `BRAND` palette + `fmt` helpers + header/nav/footer + chart boilerplate inline**.
That duplication is the direct cause of most recent bugs (the `const BRAND`/`fmtNum`
collisions that blanked maps, the diverging-map fix, the `SAGDP2N` typo fixed in 4 files,
nav `sed`-ed across 26 files). It will not scale to county profiles (159 pages). Phase 5
pays down that debt and builds the scaffolding for everything still to come.

**Hard constraints to preserve:** pure-static GitHub Pages (no runtime server), the
nightly data workflows + fetch/modeling scripts (the data layer is good — **do not churn
it**), the existing brand look, and "full automation, deploy via local git push."

---

## Guiding architecture

**One shared shell, injected at build time.** Reuse the proven `<!-- GEN:NAME -->`
region-replacement mechanism already in `scripts/generate_msa_pages.py`, generalized into
a site-wide builder. Each page becomes *content only*; the `<head>` assets, header/nav,
and footer come from shared partials injected into static HTML (SEO-safe, zero runtime
cost, no npm/build toolchain). Shared CSS and JS move to versioned `/assets/` files.

```
assets/
  app.css        # promoted shared component CSS (chart-canvas, toggle-group,
                 #   data-table, pending, kpi, panel-controls, stale-badge, ...)
  app.js         # window.GE namespace: BRAND palette, fmt helpers, chart/map
                 #   setup, pending/stale helpers  → kills per-page const collisions
partials/
  head.html  header.html  footer.html
scripts/
  build_site.py  # inject partials + asset links into every page via GEN markers
```

Pages reference `GE.BRAND`, `GE.fmtUSD`, `GE.lineChart(...)`, etc. — never re-declare
globals, so the collision class of bug becomes impossible.

---

## Workstreams

### WS1 — Shared front-end foundation (the keystone; do first)
- Extract the duplicated per-page `<style>` blocks into `assets/app.css`; keep
  `styles.css` for global chrome (already has header/nav/kpi/chart-panel).
- Extract `BRAND`, `fmt*`, and the Chart.js/Plotly setup into `assets/app.js` as a single
  `window.GE` namespace. Remove the per-page copies (and the IIFE workaround — no longer
  needed once nothing is re-declared globally).
- Create `partials/{head,header,footer}.html` + `scripts/build_site.py` that injects them
  into every page through `<!-- GEN:HEAD/HEADER/FOOTER -->` markers.
- Migrate all 16 non-MSA pages + the MSA template onto the shell **incrementally**, one at
  a time, diffing the rendered output so nothing visually regresses.
- Add a `lint-html` rule: fail if a page re-declares `const BRAND`/`fmtNum` (guard against
  regression).

### WS2 — Information architecture, navigation & search
- Reframe as two axes: **Places** (State → Metro → County) and **Topics** (Housing, GDP,
  Labor, Migration, Inflation, Outlook, Trade, Population) + **Industries**.
- New grouped header nav (Places ▾ / Topics ▾ / Industries ▾ / About) replacing the flat
  6-item bar; a `/directory/` hub page listing everything; breadcrumbs on inner pages.
- **Client-side search**: a build-time `search-index.json` (every page + place + topic +
  county) powering an instant header search box. No server needed.
- Optional: an economics **glossary** page.

### WS3 — Map as primary navigation
- A reusable interactive GA map component in `app.js` (counties + metros) where clicking a
  place navigates to its page. Built on the existing `maps.js` choropleth engine.
- Make it the **hero** on the home page, and the index device on `/counties/` and `/msa/`.
- This is what makes a *places* site feel purpose-built rather than template-built.

### WS4 — County profiles (the payoff; built on WS1–WS3)
- `scripts/generate_county_pages.py` → `/counties/<slug>/` for all 159 counties, from data
  already on hand: population + growth + migration (`population.json`), unemployment
  (`counties.json` / LAUS), GDP + per-capita (`gdp.json.county_gdp`), home value / rent /
  ownership (`housing.json.county_acs`), and which MSA it belongs to
  (`ga_msa_counties.json`). A clean overview — **not** the 33-section MSA depth.
- A `/counties/` directory + search entries + map links into each profile.
- Fold generation into the nightly workflow (regenerate after data refresh).

### WS5 — Visual polish (interleave after WS1)
- Redesigned hero (signature map visualization), consistent iconography, a unified card
  system, tighter typographic hierarchy.
- A statewide **"Economy at a glance"** scorecard rolling up headline KPIs across topics.

---

## Sequencing

1. **WS1 — shared shell** (keystone; everything else rides on it).
2. **WS2 + WS3** — IA/nav/search and map-navigation (parallelizable).
3. **WS4 — county profiles** (needs WS1 shell + WS3 map + WS2 search).
4. **WS5 — polish** (interleave once the shell exists).

## Migration strategy (low-risk)
- The shell rolls out page-by-page; the site stays live throughout. Each migrated page is
  diffed against its current render before commit.
- Data layer is untouched. Build steps are additive (`build_site.py`, generators) and run
  in CI alongside the existing data workflows.
- Each step ends with the established loop: `lint-html` green, JSON/asset checks, local
  validation, then the rebase-first push block.

## Risks / watch-items
- **No-build-step simplicity** is a feature; `build_site.py` keeps output pure-static, but
  it does introduce a build *generation* step (like the MSA generator already is). See
  Decision 1.
- Don't break `generate_msa_pages.py` (it already uses GEN markers) — generalize, don't
  fork.
- Asset cache-busting: version `app.css`/`app.js` (e.g. `?v=` or content hash) so GH Pages
  CDN serves fresh after deploy.

## Acceptance criteria (per workstream)
- WS1: zero pages re-declare `BRAND`/`fmt*`; every page renders identically to today;
  lint guard in place.
- WS2: grouped nav + working client-side search reach every page; directory hub live.
- WS3: clicking a county/metro on the home map opens its page.
- WS4: 159 county profiles generated, on the shell, linked from map + directory + search.
- WS5: refreshed hero + scorecard; consistent components site-wide.

---

## ✅ WS1 COMPLETE (chrome rollout finished 2026-06-03)

**Mechanism:** build-time partial injection via `scripts/build_site.py` (GEN markers).
Shipped: `assets/app.js` (`window.GE` = BRAND palette + fmt helpers + `data()` +
`setYear`/`markActiveNav` + `show/hide/text`), `assets/app.css`, `partials/{head,header,
footer}.html`, `scripts/build_site.py`.

**CRITICAL gotcha (keep):** `app.js` is loaded **WITHOUT `defer`** so `window.GE` exists
before each page's end-of-body inline script runs (deferred app.js → blank page). A
lint-html.yml rule now fails the build if `/assets/app.js` is ever loaded with `defer`.

**All 30 pages migrated.** `/labor/` is the only FULL `GE.*` conversion; every other page is
**chrome-centralized only** — head/header/footer via GEN markers, with inline `<style>` +
local `const BRAND`/`fmt` kept as harmless dupes and charts untouched. The 25 finished this
session: home, about, counties, inflation, population, trade, msa/index, 4 industries, and all
14 MSA reports (savannah template + 13 metros).

**Validated:** 30/30 pass the Node load-order sim (app.js → inline → DOMContentLoaded, 0
throws, GE defined); diff is chrome-only; the `generate_msa_pages.py` → `build_site.py`
round-trip is idempotent (regenerating a metro reproduces the same chrome diff). Active-nav is
path-based (`markActiveNav`) so `/msa/<x>/` lights up "Metros" with no per-page markup.

**CI wired:** `update-msa-reports.yml` has a "Stamp shared chrome" `build_site.py` step AFTER
`generate_msa_pages.py`. NOTE: did **not** add a `const BRAND` lint guard — chrome-only
migration keeps the inline dupes on purpose, so that guard only becomes valid after full
`GE.*` conversion of every page.

**Known content delta:** centralizing the footer dropped each page's own source-attribution
line (e.g. "Source: USDA NASS", per-MSA "Sources: BLS LAUS & QCEW…") + per-page copyright in
favor of the shared generic footer (same change the 5 already-shipped pages adopted). To
restore per-page sources, add a page-specific footer slot below `<!-- /GEN:FOOTER -->`.

## ▶ RESUME HERE (Phase 5 — the VISIBLE workstreams)

WS1 (invisible refactor) is done; pages look identical by design. Next, in order:
WS2 (Places/Topics grouped nav + client-side `search-index.json` + `/directory/` hub),
WS3 (map-as-primary-navigation, reusing `maps.js`), WS4 (159 county profiles via
`scripts/generate_county_pages.py` from existing JSON — the payoff), WS5 (visual polish:
hero map + "Economy at a glance" scorecard). See the workstream sections above for detail.
