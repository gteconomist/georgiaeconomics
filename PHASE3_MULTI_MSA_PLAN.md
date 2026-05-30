# Phase 3 — Multi-MSA Rollout Plan

**Goal:** turn the single hand-built Savannah report (`/msa/savannah/`) into an
automated generator that produces a Metro Economic Profile page for **all 14
Georgia MSAs** from their per-metro JSON, with minimal per-metro hand-curation.

**Status of inputs:** the data + modeling layers are already multi-MSA. The
nightly workflow runs `fetch_msa_report.py --all` and writes
`data/msa_reports/<slug>.json` for all 14 metros; 30 data sections + 9 modeling
modules populate each. **Only the report *page* is Savannah-specific today.**

**Last updated:** 2026-05-30

---

## 1. What is and isn't already portable

The Savannah page is ~2,500 lines. Roughly:

| Portion | Portable today? | Notes |
|---|---|---|
| All charts + tables (strip cells, headline table, Health Check, Industry/Comparative Employment, Diffusion, Exports, HPI, migration, demographics, housing, etc.) | **Yes** | Filled at runtime by `loadLiveData()` from the JSON. The fetchers already run for all 14 MSAs. |
| Header (name, CBSA, county list, population, state rank) | Mechanical | Derivable from `_ga_msas` + the JSON. |
| `loadLiveData()` JSON path | One-line change | Hardcodes `savannah.json`; must read the page's own slug. |
| **Analysis narrative** (7 paragraphs) | **No** | Hand-written Savannah prose. The hard part. |
| **Scorecard** (Strengths / Weaknesses / Upside / Downside) | **No** | Hand-written; generate from data rules. |
| **Major Employers list** | **No** | No public MSA API; needs a small curated per-metro file. |

So ~60% is portable as-is; the remaining ~40% is the qualitative content.

---

## 2. Architecture decision (decide first)

**Recommended: build-time static generation.** A Python generator reads a
template + each MSA's JSON + a small per-MSA content file and emits
`msa/<slug>/index.html` for all 14. It runs in `update-msa-reports.yml`
immediately after `fetch_msa_report.py --all`.

Why build-time over a client-rendered single template (`report.html?msa=...`):

- **SEO + no-JS:** prose and headings live in the static HTML, not assembled in
  the browser.
- **Static hosting:** GitHub Pages serves plain files; no routing needed.
- **Determinism:** the generated HTML is diffable in git; a bad build is visible
  in the PR/commit, not just at runtime.
- **Reuses the existing two-stage model:** fetch → JSON → (now) generate → pages,
  all in the nightly Action.

Trade-off: the generator must run on every data refresh (cheap — pure string
assembly). Client-side templating would avoid that but pushes narrative logic
into JS and weakens the above. **Go build-time.**

### Template strategy

Keep `msa/savannah/index.html` as the **canonical template and a real page**.
Wrap each swappable region in HTML-comment markers:

```
<!-- GEN:HEADER-SUB -->...<!-- /GEN:HEADER-SUB -->
<!-- GEN:AS-OF -->...<!-- /GEN:AS-OF -->
<!-- GEN:NARRATIVE -->...<!-- /GEN:NARRATIVE -->
<!-- GEN:SCORECARD -->...<!-- /GEN:SCORECARD -->
<!-- GEN:EMPLOYERS -->...<!-- /GEN:EMPLOYERS -->
```

Markers are invisible in the browser, so Savannah keeps rendering. The generator
swaps the marked regions per metro and sets a `data-msa-slug` attribute that
`loadLiveData()` reads. Savannah is generated from the same template as everyone
else — no special-casing.

---

## 3. Workstreams (dependency order)

1. **Parameterize the shell.** `<title>`, `<h1>`, header sub-line (CBSA + county
   count/names), population, state rank, as-of stamp — all from `_ga_msas` + JSON.
   *Mechanical.*

2. **Slug-aware `loadLiveData()`.** Read `document.documentElement.dataset.msaSlug`
   (default `savannah`) and fetch `/data/msa_reports/${slug}.json`. Remove the few
   hardcoded "Savannah" strings in the JS. *Small.*

3. **Narrative generator (the heart).** Replace the 7 hand-written paragraphs with
   a **deterministic, threshold-driven** generator: sentence templates filled from
   the JSON, gated by rules (e.g. `LQ > 1.5 → "defining industry"`,
   `diffusion < 50 → "cooling"`, `yoy < 0 → "contracting"`). Crucially it must
   **skip** sentences whose inputs are missing rather than fabricate — smaller
   metros will lack some sections. Keep it deterministic to preserve the page's
   "every claim is sourced to an indicator" guarantee; an optional LLM polish pass
   can smooth phrasing but must not introduce unsourced claims.

4. **Rule-based scorecard.** Strengths/Weaknesses/Upside/Downside from the same
   data rules (top-LQ sectors → strengths; contracting sectors + low-paid
   over-weight sectors → weaknesses; expanding sectors / strong migration /
   on-trend valuation → upside; sub-50 diffusion / shrinking white-collar /
   export concentration → downside).

5. **Per-MSA qualitative data.** A small curated `data/msa_content/<slug>.yml`
   holding the major-employers list (no public API) and any metro-specific notes.
   One-time effort ×14. Everything else derives from data.

6. **Cross-MSA unlocks** (the upside of having all 14 live):
   - **GA-relative rankings** — reinstate a "Rank among GA metros" cell
     (unemployment, job growth, etc.). This is exactly the metric removed as a
     *national* stat but now computable across the 14.
   - **`/msa/` landing index** — all 14 metros with a comparison table and/or a
     choropleth (reuse `maps.js`).
   - Peer comparison callouts in the narrative.

7. **Generation pipeline.** `scripts/generate_msa_pages.py --all` invoked in
   `update-msa-reports.yml` after the fetch; diff so only changed pages commit.

8. **QA at scale.** Validate all 14. Extend `REPORT_STATUS.md` into a per-metro
   status matrix (which sections are live/failed/suppressed per metro).

---

## 4. Risks & edge cases

- **Cross-state MSAs** — Augusta (GA-SC), Columbus (GA-AL): county lists and
  labels span states; already handled in `_ga_msas.COUNTY_TO_MSA`, but the header
  copy must say "GA-SC" etc.
- **Small-metro data sparsity** — Hinesville (78k), Rome (99k): heavier QCEW
  3-digit suppression (the mfg-split coverage guard already handles this), and
  some may lack BEA GMP, ITA exports, or affordability inputs. Graceful
  degradation (skip, don't fabricate) is mandatory, especially in the narrative.
- **Narrative quality at scale** — auto-generated prose risks sounding robotic or
  over-claiming. Mitigate with conservative templates + a human review pass on the
  first full build; consider an LLM polish step behind a "no new facts" guard.
- **Major-employers curation** — 13 more lists to compile by hand (SEDA-equivalent
  sources per metro).
- **County names** — `_ga_msas` maps FIPS only; the header county *names* need a
  FIPS→name lookup (small addition) if we want to list them for small metros.

---

## 5. Milestones

1. **M1 — Atlanta proof-of-concept. ✅ DONE 2026-05-30.** `loadLiveData()` is now
   slug-aware (`data-msa-slug`); the Savannah page carries GEN markers
   (`NARRATIVE` / `SCORECARD` / `EMPLOYERS`) and is the canonical template;
   `scripts/generate_msa_pages.py` emits `msa/<slug>/index.html`. `msa/atlanta/`
   generates with correct shell (CBSA 12060, 29-county, live pop 6.26M, rank 1),
   reads `atlanta.json`, all series labels swapped to "Atlanta", qualitative
   regions stubbed. JS validates. **Pipeline proven.** Known gaps deferred to M2:
   the footer methodology block still names Savannah-specific school districts;
   `atlanta.json` needs a full `--all` refresh to carry the newest sections
   (qcew_3digit / housing_affordability / industrial_diversity).
2. **M2 — Narrative + scorecard generator.** Deterministic templates → Atlanta
   reads end-to-end with no hand-written prose.
3. **M3 — Full pipeline.** Generator in the nightly workflow → all 14 pages build
   and commit automatically.
4. **M4 — Cross-MSA features.** GA rankings, `/msa/` landing index, comparison view.
5. **M5 — QA + polish.** Per-metro status matrix, degradation hardening, curated
   employer lists, narrative review pass.

**Biggest single decision:** M3 narrative generation — pure deterministic
templates vs. LLM-assisted. Recommendation: deterministic core, optional guarded
LLM polish.

---

## 6. Files Phase 3 will add/touch

- `scripts/generate_msa_pages.py` — the generator (new).
- `msa/savannah/index.html` — becomes the marked template (GEN markers + slug attr).
- `data/msa_content/<slug>.yml` — per-metro curated content (new, ×14).
- `scripts/_ga_msas.py` — add FIPS→county-name map (optional, for header).
- `.github/workflows/update-msa-reports.yml` — add the generate step.
- `msa/index.html` — upgrade the landing page to list all 14 + comparison.
- `REPORT_STATUS.md` — extend to a 14-metro matrix.
