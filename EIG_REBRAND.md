# EIG rebrand — scope & rollout

**Status:** COMPLETE site-wide (2026-07-27). All 191 pages on the EIG palette,
type and chrome.
**Goal:** Make georgiaeconomics.com read as an Economic Impact Group product —
a sibling of [housinganalytics.org](https://housinganalytics.org), which is the
other EIG *tool* (as opposed to the models: futureimpactmodel.com, cocsmodel.com,
economicimpact.io, economicimpact.com).

---

## The palette

The EIG logo reads as "grey and orange", but the brand darks are **warm
charcoals** and the accent is the **amber of the logo's bar**. The pre-rebrand
site was cool navy `#1a3a5c` + terracotta `#c05f3c`, which is why it did not
look related to anything else in the family.

Canonical source of truth: `gteconomist/housinganalytics` →
`src/styles/tokens.css`. Ported here into `assets/eig.css`:

| Token | Value | Role |
|---|---|---|
| `--eig-charcoal` | `#231f20` | primary brand dark (header, footer, hero) |
| `--eig-charcoal-2` | `#2e292a` | secondary dark surface (KPI strip, menus) |
| `--eig-amber` | `#f7941e` | accent — CTAs, rules, active states |
| `--eig-amber-deep` | `#c9740f` | hover, eyebrow text |
| `--eig-cream` | `#f7f6f4` | page background |
| `--eig-hair` | `#e4e2df` | card borders, warm hairlines |
| `--eig-muted` | `#6d6e71` | body/secondary text |

Type: **Geist** (sans) + **Geist Mono**, matching housinganalytics.

Recurring EIG shell patterns reused here: charcoal header with a **4px amber
bottom rule**, amber pill CTAs (`#f7941e` on `#231603`), uppercase eyebrows at
`.12em` tracking, white cards with warm hairlines and a warm-tinted shadow, and
the EIG logo lockup in the footer.

---

## What shipped (landing page)

Chosen direction: **Concept B hero on Concept A chrome.**

- **Hero** — full-bleed charcoal band. The clickable **159-county** choropleth
  is the hero image (amber ramp on charcoal, click → county profile), with a
  live-refresh chip, the headline, two CTAs and the EIG attribution lockup on
  the left. `GE.countyMap` draws it from the latest frame of `data/counties.json`;
  `GE.metroMap` remains available for a 14-metro hero via `data-ge-metromap`.
  - **Colour ramp:** Georgia county unemployment is tightly clustered (most
    counties within about a point of each other, plus a thin upper tail), so an
    evenly spaced ramp renders the state almost flat. `quantileStops()` places
    the ramp's stops at the data's own quartiles, spreading colour across where
    the counties actually are, while `zmin`/`zmax` stay the true min/max so the
    legend still reads in real percentage points.
  - The low end starts at the EIG logo's taupe rather than near-black, so the
    lowest-unemployment counties stay legible against the charcoal background.
- **KPI strip** — `data/scorecard.json` rendered as an 8-cell strip welded to
  the bottom edge of the hero. Same `GE.scorecard` renderer as before, restyled.
- **Body** — numbered section kickers (`01/02/03`), two flagship cards with an
  amber left rule, and the topic/industry grids on the EIG card system.
- **Source band** — charcoal credibility strip listing the statistical agencies.

## How the CSS is organised

Three layers, loaded in this order:

1. **`styles.css`** — the palette (`:root`) and global chrome. Rebranded in
   place; variable names kept (see the token-remap note below).
2. **`assets/app.css`** — shared components promoted out of per-page styles.
3. **`assets/eig.css`** — the EIG brand layer. Loaded on every page via
   `partials/head.html`, and **must load after** the first two so its chrome
   rules win. Holds only what a token swap cannot express.

Plus **`assets/eig-home.css`** for the landing page alone, scoped to
`body.home` and linked page-specifically **outside** the `<!-- GEN:HEAD -->`
markers so `build_site.py` will not strip it.

The shared header and footer are restyled **by CSS only** — the markup in
`partials/header.html` and `partials/footer.html` is untouched — so the nightly
generators are unaffected.

### Shared-code changes (all backwards compatible, defaults unchanged)

- `maps.js` → `drawGAChoropleth` gained optional `bgcolor`, `lineColor`,
  `fontColor`, `outlineColor`, `horizontalColorbar`, and `fill`. Every existing
  caller passes none of them and renders exactly as before.
  - `fill: true` drops `scope: 'usa'` for a plain Mercator projection. The USA
    scope forces a ~1.91:1 geo box, so a tall narrow state like Georgia gets
    fitted to the box *height* and ends up small with wide empty margins. With
    `fill`, `fitbounds: 'locations'` fills whatever box it is given — Georgia
    renders ~60% larger. Note this makes Plotly fetch `world_110m.json` instead
    of `usa_110m.json`.
- `assets/app.js` → `GE.metroMap` accepts `theme: "eig-dark"` (selected per
  element via `data-ge-theme`) and a `data-ge-fallback` opt-in that replaces the
  map with a text link if the render fails, so a CDN outage cannot leave a
  500px hole where the hero image should be.

**Verified:** `/counties/`, `/msa/`, `/labor/`, `/housing/` and `/msa/savannah/`
render **pixel-identical** before and after the shared-JS changes.

---

## Site-wide rollout (2026-07-27)

Shipped in one pass after the landing page. Approach, in order of leverage:

**1. Token remap — the whole trick.** The `:root` block in `styles.css` now
holds EIG values under the *old variable names*. Roughly 600 `var(--navy)` /
`var(--peach-deep)` / … references live in 29 pages' inline `<style>` blocks;
remapping the values rebranded every one of them at once, where renaming would
have meant editing all 191 pages for no visual gain. So `--navy` is a warm
charcoal, `--peach` is the EIG amber, `--font-display` is Geist. Each entry
carries its old value in a comment. housinganalytics does the same
(`--color-navy: #231f20`).

**2. Chrome via the shared head partial.** `assets/eig.css` is linked from
`partials/head.html`, so `build_site.py` stamps it into all 191 pages and any
regenerated page inherits it automatically — no per-page edits, no generator
template changes, nothing to forget. It carries only what a token swap cannot
express: the header becoming charcoal with a 4px amber rule, the dark dropdown
menus and search field, the footer.

**3. Landing page isolated.** Its full-bleed hero, KPI strip and section
kickers moved to `assets/eig-home.css`, scoped to `body.home` and linked
page-specifically outside the GEN markers. `main { max-width: none }` would
wreck every inner page if it leaked, so this separation is load-bearing.

**4. Chart colours remapped** (~470 literal values) from the old
navy/teal/coral onto the EIG chart palette — charcoal, amber, forest green,
brick red, slate, warm brown. Three passes were needed because the old palette
hid in three forms: hex literals, `rgb()`/`rgba()` triplets (the `/msa/`
comparator heat map was shading cells old-teal and old-coral this way), and
colours baked into `data/*.json` by the `fetch_*.py` scripts. **The scripts were
fixed too** — otherwise the next monthly refresh would have written the old
palette straight back into the data. Body copy that named colours ("bars in
teal are growing") was reworded to "green"/"red".

Note this diverges from housinganalytics, whose `tokens.css` deliberately pins
chart colours to its pre-rebrand hexes. That worked there because its original
chart palette was already earthy; here the old palette was navy/teal/coral,
which read as the previous brand.

**5. Header overflow fixed site-wide** — see below.

### What is left

- **Charts still declare their own `BRAND` objects.** 11 pages carry an inline
  `const BRAND = {…}`, now holding EIG values. Converting them to `GE.*` is the
  remaining Phase 5 WS1 work; only `/labor/` is fully converted. Once every page
  is on `GE.*`, add the lint rule that fails on a re-declared `const BRAND`
  (PHASE5_PLAN explains why that guard is invalid until then).
- The `--navy` / `--peach-*` variable names are now misnomers. Renaming is safe
  but touches every page; worth doing only alongside the `GE.*` conversion.

## Responsive bugs found and fixed during this pass

Both were **pre-existing**, unrelated to the rebrand, and both made pages
scroll sideways.

1. **Header, all ~190 pages.** The shared header switched to the hamburger
   layout at `max-width: 860px`, but the desktop row (brand + 5 nav groups +
   search) stops fitting at about 1020px — leaving a dead band from ~861px to
   ~1023px, i.e. iPad landscape and half-screen desktop windows, where the
   search box overhung the viewport. Fixed by raising the breakpoint in
   `assets/app.css` to `1024px`.
2. **Metro reports, all 14.** `.report-panel` sits in a CSS grid and contains a
   Chart.js `<canvas>`. Grid items default to `min-width: auto`, so the canvas
   forced its track to the canvas's intrinsic width and pushed the grid past the
   viewport around 981–1024px and again near 768px. Fixed with `min-width: 0` in
   the Savannah template, propagated by `generate_msa_pages.py`.

Verified afterwards: no horizontal overflow on 12 representative pages at 1440,
1200, 1024, 1023, 980, 900, 861, 860, 768 and 600px.

### Still open (pre-existing, not addressed)

Metro report pages overflow by ~28px at 375–420px and ~88px at 360px — dense
data tables that need a scroll wrapper. This pass improved it (was 43px / 103px)
but did not fix it. Every other page type is clean down to 360px.
