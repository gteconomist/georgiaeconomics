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

## ✅ WS2 + WS3 SHIPPED (2026-06-03)

**WS2 — IA, nav & search.** `partials/header.html` is now a grouped nav: **Places**
(Counties / Metros / Population), **Topics** (Labor / Housing / GDP / Migration / Inflation /
Trade / Outlook), **Industries** (Agriculture / Automotive / Data Centers / Film), plus
Directory + About. Dropdowns: CSS hover on desktop, JS click/Escape/outside-click + a mobile
stacked layout — all in `app.js` (`initNav`) and `app.css`. `markActiveNav` now lights the
matching link **and** its parent group. **Breadcrumbs** auto-inject at the top of `<main>` on
every inner page from a path→registry in `app.js` (`initBreadcrumbs` + `PAGES`), no per-page
markup; `/msa/<slug>/` → Home › Places › Metros › <Metro>. New **`/directory/`** hub lists
every place/topic/industry + all 14 metros. **Client-side search**: `scripts/build_search_index.py`
→ `data/search-index.json` (190 items: pages + 14 metros + 159 counties), header search box
with keyboard nav in `app.js` (`initSearch`). Wired into `update-msa-reports.yml`
(rebuild + commit `data/search-index.json`).

**WS3 — map as navigation.** `app.js` adds `GE.metroMap(elId)` (renders a clickable metro
choropleth on `[data-ge-metromap]`, shaded by metro unemployment via `gaMaps.drawGAChoropleth`)
and `GE.attachMetroNav(elId)` (click a county → its metro report). `autoWireMaps` renders the
home **hero map** (`index.html` now loads Plotly + maps.js page-specifically) and auto-attaches
metro-nav to the standing `#msa-choropleth` on `/msa/`. Slugs come from `GE.slugify` (verified
to match all 14 `msa/<slug>/` dirs).

**Validated:** app.js parses + full `GE` API present; 14/14 metro slugs resolve; **0 dead
links** across the 190-item search index (31 unique URLs all exist); 31/31 pages pass the Node
load-order sim. Lint (app.js-not-deferred, charts.js URL) green; app.css braces balanced.

**Deliberate interim:** the 159 **county** search entries point at their **metro** report (or
`/counties/`) and `/counties/` map clicks are NOT yet metro-wired — both become county-profile
links in WS4. The `/counties/` heat map stays the county index until then.

## ✅ WS4 SHIPPED (2026-06-03)

**159 county profiles.** `scripts/generate_county_pages.py` bakes a clean overview for every
county into `/counties/<slug>/index.html` from existing JSON — population + components of change
(`population.json`), 12-month unemployment trend + rank (`counties.json`), GDP + per-capita
(`gdp.json`), home value/rent/income/ownership + permits (`housing.json`), and MSA membership
(`ga_msa_counties.json`). Values are baked at build time (deterministic; the only client JS is a
Chart.js sparkline). Shared chrome via the same GEN markers + `build_site.py`.

**Discovery wired:** search-index county rows now point to `/counties/<slug>/` (190 items, 0 dead);
the `/counties/` heat map is click-navigable to profiles (`GE.attachCountyNav` auto-wires
`#ga-county-map` via `data/county_index.json`); `/counties/` got an A–Z list (GEN:COUNTY_INDEX,
filled by the generator); breadcrumbs handle `/counties/<slug>/` → Home › Places › Counties › X.

**CI:** `update-msa-reports.yml` now runs `generate_county_pages.py` AFTER the gdp+housing roll-ups
(so values are fresh), then `build_site.py` (stamp) + `build_search_index.py`; commits `counties/`
+ `data/county_index.json`. NOTE: population.json/counties.json refresh in their own workflows, so
county pages pick those up on the next MSA run — consider adding the same 3-step tail to
update-population.yml / update-labor.yml if you want same-day refresh.

**Validated:** 190/190 load-order sim, 0 dead links, generator idempotent, single-county mode works.

## ✅ WS5 SHIPPED (2026-06-03) — PHASE 5 COMPLETE

**"Economy at a glance" scorecard.** `scripts/build_scorecard.py` rolls one headline KPI from each
topic JSON into `data/scorecard.json` (7 cards: Unemployment, Population, GDP/capita, Home prices,
Inflation, Net migration, Business cycle — each formatted at build time with a good/bad/neutral
trend). `GE.scorecard` (assets/app.js) renders it into `[data-ge-scorecard]`; the home page shows it
as a card strip under the hero ("Georgia economy at a glance" → links to each topic + /directory/).
CI: `build_scorecard.py` runs after the topic roll-ups in update-msa-reports.yml; commits
`data/scorecard.json`.

**Unified cards + type (site-wide, via shared CSS — no per-page edits).** `.kpi`, `.chart-panel`,
and `.map-panel` now share the 10px radius of the newer card system (`.topic-card`/`.dir-card`/
`.score-card`/`.county-az`), so every page's cards read consistently. Done centrally in styles.css/
app.css to avoid touching page HTML.

**Validated:** 190/190 load-order sim, 0 dead links, scorecard generator idempotent, lint green,
CSS braces balanced.

---

## Phase 5 status: COMPLETE (WS1–WS5 all shipped 2026-06-02 → 2026-06-03)

WS1 shared-shell chrome · WS2 grouped nav + breadcrumbs + /directory/ + client-side search ·
WS3 clickable metro nav-map (+ later: black GA outline, no neighbors, locked zoom) · WS4 159
county profiles · WS5 scorecard + unified cards. Possible follow-ons (not Phase 5):
same-day scorecard/county refresh by adding the generate→stamp→index→scorecard tail to
update-population.yml / update-labor.yml; optional glossary page (WS2 stretch); allow zoom-in on
maps while still capping zoom-out at the GA frame.
