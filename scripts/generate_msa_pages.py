"""Generate per-MSA Metro Economic Profile pages from the Savannah template.

Phase 3, Milestone 1 (proof-of-concept). Takes `msa/savannah/index.html` as the
canonical template and emits `msa/<slug>/index.html` for any other Georgia MSA by:

  - parameterizing the shell (title, description, <h1>, CBSA + county count,
    population, state rank, as-of) from `_ga_msas` + the metro's JSON;
  - pointing the page at its own JSON via a `data-msa-slug` attribute that
    `loadLiveData()` reads;
  - swapping the Savannah chart-series labels for the metro's name;
  - replacing the GEN-marked qualitative regions (narrative, scorecard, major
    employers) with placeholders — these become data-generated in Milestone 2.

The charts/tables themselves need no templating: they are filled at runtime by
`loadLiveData()` from `data/msa_reports/<slug>.json`, which already exists for
all 14 MSAs.

Usage:
    python3 scripts/generate_msa_pages.py atlanta        # one metro by slug/CBSA
    python3 scripts/generate_msa_pages.py --all          # all MSAs except savannah
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
from _ga_msas import GA_MSAS, COUNTY_TO_MSA  # noqa: E402

TEMPLATE = ROOT / "msa" / "savannah" / "index.html"
JSON_DIR = ROOT / "data" / "msa_reports"
OUT_DIR = ROOT / "msa"

MONTHS = ["", "January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

# Population rank (1 = largest) across the 14 GA MSAs.
_POP_RANK = {cbsa: i + 1 for i, (cbsa, *_rest) in
             enumerate(sorted(GA_MSAS, key=lambda r: -r[3]))}
_BY_SLUG = {short.lower().replace(" ", "-"): (cbsa, short, full, pop)
            for cbsa, short, full, pop in GA_MSAS}
_BY_CBSA = {cbsa: (cbsa, short, full, pop) for cbsa, short, full, pop in GA_MSAS}


def _resolve(target: str):
    t = target.strip().lower()
    if t in _BY_SLUG:
        return _BY_SLUG[t]
    if t in _BY_CBSA:
        return _BY_CBSA[t]
    raise SystemExit(f"Unknown MSA: {target}")


def _county_count(cbsa: str) -> int:
    return sum(1 for v in COUNTY_TO_MSA.values() if v == cbsa)


def _as_of_label(slug: str) -> str:
    path = JSON_DIR / f"{slug}.json"
    if path.exists():
        try:
            d = json.loads(path.read_text())
            iso = str(d.get("as_of", ""))
            y, m = int(iso[:4]), int(iso[5:7])
            return f"As of {MONTHS[m]} {y}"
        except Exception:
            pass
    return "As of latest release"


def _live_population(slug: str, fallback: int) -> int:
    path = JSON_DIR / f"{slug}.json"
    if path.exists():
        try:
            d = json.loads(path.read_text())
            pep = (d.get("sections") or {}).get("census_pep") or {}
            if pep.get("latest_population"):
                return int(pep["latest_population"])
        except Exception:
            pass
    return fallback


def _replace_region(html: str, name: str, replacement: str) -> str:
    pattern = re.compile(rf"<!-- GEN:{name} -->.*?<!-- /GEN:{name} -->", re.S)
    if not pattern.search(html):
        print(f"  WARNING: marker GEN:{name} not found in template", file=sys.stderr)
        return html
    return pattern.sub(lambda _m: replacement, html)


def generate(target: str) -> Path:
    cbsa, short, full, pop = _resolve(target)
    slug = short.lower().replace(" ", "-")
    if slug == "savannah":
        raise SystemExit("Savannah is the canonical template; it is not regenerated.")

    state_suffix = full.rsplit(",", 1)[-1].strip()  # 'GA', 'GA-SC', 'GA-AL'
    display = f"{short}, {state_suffix}"
    rank = _POP_RANK[cbsa]
    counties = _county_count(cbsa)
    population = _live_population(slug, pop)
    as_of = _as_of_label(slug)
    month_year = as_of.replace("As of ", "")

    html = TEMPLATE.read_text()

    # --- shell: <html> attributes -------------------------------------------
    html = html.replace(
        '<html lang="en">',
        f'<html lang="en" data-msa-slug="{slug}" data-msa-name="{short}">', 1)

    # --- shell: <title> + meta description -----------------------------------
    html = re.sub(r"<title>.*?</title>",
                  f"<title>{short} MSA Economic Profile | Economic Impact Group, LLC</title>",
                  html, count=1, flags=re.S)
    html = re.sub(r'(<meta name="description" content=")[^"]*(">)',
                  rf"\g<1>{display} metro economic profile from Economic Impact Group, LLC: "
                  "business cycle, employment, housing, demographics, exports, and migration "
                  r"— built from BLS, BEA, Census, FHFA, FRED and IRS SOI data.\g<2>",
                  html, count=1)

    # --- shell: <h1> + header sub + as-of block ------------------------------
    html = html.replace("<h1>Savannah, GA</h1>", f"<h1>{display}</h1>", 1)
    html = html.replace(
        '<div class="sub">CBSA 42340 &middot; 3-county MSA: Chatham, Bryan, Effingham</div>',
        f'<div class="sub">CBSA {cbsa} &middot; {counties}-county MSA</div>', 1)
    html = html.replace("<div class=\"stamp\">As of May 2026</div>",
                        f'<div class="stamp">{as_of}</div>', 1)
    html = html.replace(
        "<div>Population: <strong>418,000</strong> &middot; State rank: 3 of 14</div>",
        f"<div>Population: <strong>{population:,}</strong> &middot; State rank: {rank} of 14</div>", 1)

    # --- metro name: swap every standalone "Savannah" -> the metro's name -----
    # Catches chart-series labels, axis sub-headers, table headers (<th>Savannah</th>),
    # source captions ("Savannah live; ...") and JS comments. Case-sensitive on the
    # capitalized word, so the lowercase JSON slug ('savannah.json', the loadLiveData
    # default) is left untouched.
    html = re.sub(r"\bSavannah\b", short, html)

    # --- qualitative regions: stub until Milestone 2 -------------------------
    html = _replace_region(html, "NARRATIVE", f"""<!-- GEN:NARRATIVE -->
    <section class="analysis">
      <h2>Analysis<span class="as-of-stamp">{month_year} &middot; Economic Impact Group, LLC</span></h2>
      <p style="font-size:13px;color:var(--ink-soft);font-style:italic;padding:8px 0;">Metro-specific written analysis for {short} is generated from this page's live indicators in a later build step (Phase&nbsp;3, Milestone&nbsp;2). The charts and tables on this page are live for {short}; this narrative is a placeholder.</p>
    </section>
    <!-- /GEN:NARRATIVE -->""")

    html = _replace_region(html, "SCORECARD", f"""<!-- GEN:SCORECARD -->
      <div class="box strengths">
        <h3>Scorecard</h3>
        <p style="font-size:12px;color:var(--ink-soft);">Strengths, weaknesses and risk factors for {short} are generated from live data in Phase&nbsp;3 (Milestone&nbsp;2).</p>
      </div>
      <!-- /GEN:SCORECARD -->""")

    html = _replace_region(html, "EMPLOYERS", f"""<!-- GEN:EMPLOYERS -->
      <div class="sub">Largest employers, ranked by approximate size</div>
      <p style="font-size:12px;color:var(--ink-soft);padding:8px 0;">A representative major-employer list for {short} is pending curation (Phase&nbsp;3).</p>
      <div class="src"><span class="src-pill partial">Pending</span></div>
      <!-- /GEN:EMPLOYERS -->""")

    out_path = OUT_DIR / slug / "index.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
    print(f"  wrote {out_path.relative_to(ROOT)}  "
          f"({short}, CBSA {cbsa}, {counties} counties, pop {population:,}, rank {rank})")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Generate per-MSA report pages from the Savannah template.")
    ap.add_argument("target", nargs="?", help="MSA slug or CBSA")
    ap.add_argument("--all", action="store_true", help="Generate all MSAs except Savannah")
    args = ap.parse_args()

    if args.all:
        for cbsa, short, *_ in GA_MSAS:
            if short.lower() == "savannah":
                continue
            generate(cbsa)
    elif args.target:
        generate(args.target)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
