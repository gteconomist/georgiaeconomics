# EIG rebrand — scope & rollout

**Status:** Landing page shipped 2026-07-27. Remaining pages pending.
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

## How the scoping works (important)

**Every rule in `assets/eig.css` is scoped to `body.eig`.** Only pages that opt
in with `class="eig"` on `<body>` change; the other ~190 pages keep the existing
Modern Editorial styling with zero risk. The stylesheet is linked from
`index.html` **outside** the `<!-- GEN:HEAD -->` markers, so `build_site.py`
does not strip it when it re-stamps shared chrome (verified: re-running
`build_site.py` leaves `index.html` byte-identical).

The shared header and footer are restyled **by CSS only** — `partials/*.html`
were not edited — so no other page's markup changed and the nightly generators
are unaffected.

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

## Rollout path for the rest of the site

1. Add `class="eig"` to `<body>` and the `eig.css` + Geist `<link>`s (outside the
   GEN markers) on each static page as it is converted.
2. For the generated pages, make the same two additions in
   `scripts/generate_county_pages.py` and `scripts/generate_msa_pages.py` — that
   converts 173 pages in one step.
3. Port each page's inline `<style>` block onto the EIG tokens. Most pages still
   declare their own `const BRAND` + `fmt` helpers (deliberate, see PHASE5_PLAN
   — chrome-only migration); charts will keep the old palette until those are
   converted to `GE.*`.
4. Once every page carries the class, drop the `body.eig` scoping from
   `eig.css`, fold it into `styles.css`/`app.css`, and delete the old palette.

## Known issue found during this pass (not yet fixed site-wide)

The shared header switches to the hamburger layout at `max-width: 860px`, but
the desktop row stops fitting at about **1020px**. Between ~861px and ~1023px
the search box overhangs the viewport and the page scrolls sideways. **This
affects every page on the site.** It is worked around under `body.eig` in
`eig.css`; the real fix is raising the `860px` breakpoint in `assets/app.css`
(line ~159) to `1024px`, which corrects all ~190 pages at once.
