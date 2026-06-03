"""Generate /counties/<slug>/ profile pages for all 159 Georgia counties (Phase 5, WS4).

A clean per-county overview — population & migration, unemployment (with a
12-month trend), GDP, and housing — built entirely from JSON the site already
maintains:
  data/population.json   counties[]  (pop, growth, components of change)
  data/counties.json     frames[]    (monthly LAUS unemployment, 12 mo)
  data/gdp.json          county_gdp.counties{fips}  (GDP $bn + per-capita)
  data/housing.json      county_acs.counties{fips}  (home value/rent/income/ownership)
                         county_permits.counties{fips} (SF/MF permits)
  data/ga_msa_counties.json  msas{}  (which metro a county belongs to)

Values are baked into static HTML at build time (deterministic — unchanged data
produces no git diff), so the pages need no client-side data fetch; only the
12-month unemployment sparkline runs a tiny Chart.js init. Shared chrome
(head/header/footer) is injected afterwards by scripts/build_site.py via the
GEN markers, exactly like the MSA report pages.

Also:
  * fills the <!-- GEN:COUNTY_INDEX --> region on /counties/ with an A–Z list, and
  * writes data/county_index.json (fips -> slug/name/metro) for the clickable
    county map navigation in assets/app.js.

Usage:
  python3 scripts/generate_county_pages.py            # all 159
  python3 scripts/generate_county_pages.py 13121      # one FIPS
  python3 scripts/generate_county_pages.py fulton     # one slug
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COUNTIES_DIR = ROOT / "counties"


def slugify(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower().replace("&", "and"))).strip("-")


# ---------- assemble per-county records from the shared JSON ----------

def load_records() -> tuple[list[dict], dict]:
    pop = json.loads((DATA / "population.json").read_text())
    cou = json.loads((DATA / "counties.json").read_text())
    gdp = json.loads((DATA / "gdp.json").read_text())
    hou = json.loads((DATA / "housing.json").read_text())
    msa = json.loads((DATA / "ga_msa_counties.json").read_text())

    gdp_c = gdp["county_gdp"]["counties"]
    acs_c = hou["county_acs"]["counties"]
    perm_c = hou["county_permits"]["counties"]

    # fips -> metro {slug,name}
    fips_metro = {}
    for cbsa, m in msa["msas"].items():
        s = slugify(m["short_name"])
        for f in m.get("counties", []):
            fips_metro[f] = {"cbsa": cbsa, "slug": s, "name": m["short_name"], "full": m["full_name"]}

    # unemployment: latest value + 12-month series, per fips
    frames = cou["frames"]
    ur_latest, ur_series = {}, {}
    for fr in frames:
        for p in fr["points"]:
            ur_series.setdefault(p["fips"], []).append({"date": fr["date"], "value": p["value"]})
    for f, ser in ur_series.items():
        ur_latest[f] = ser[-1]["value"]
    # rank by latest UR (1 = lowest = best)
    ranked = sorted([f for f in ur_latest if isinstance(ur_latest[f], (int, float))], key=lambda f: ur_latest[f])
    ur_rank = {f: i + 1 for i, f in enumerate(ranked)}
    n_ranked = len(ranked)

    recs = []
    for c in pop["counties"]:
        f = c["fips"]
        metro = fips_metro.get(f)
        recs.append({
            "fips": f,
            "name": c["county"],
            "slug": slugify(c["county"]),
            "metro": metro,
            "pop_latest": c.get("pop_latest"),
            "pop_base": c.get("pop_base"),
            "growth_pct": c.get("growth_pct"),
            "growth_abs": c.get("growth_abs"),
            "dom_mig": c.get("dom_mig_total"),
            "intl_mig": c.get("intl_mig_total"),
            "natural": c.get("natural_total"),
            "ur_latest": ur_latest.get(f),
            "ur_series": ur_series.get(f, []),
            "ur_rank": ur_rank.get(f),
            "gdp_bn": (gdp_c.get(f) or {}).get("gdp_bn"),
            "gdp_pc": (gdp_c.get(f) or {}).get("gdp_per_capita"),
            "home_value": (acs_c.get(f) or {}).get("median_home_value"),
            "rent": (acs_c.get(f) or {}).get("median_gross_rent"),
            "income": (acs_c.get(f) or {}).get("median_household_income"),
            "ownership": (acs_c.get(f) or {}).get("pct_owner_occupied"),
            "permit_sf": (perm_c.get(f) or {}).get("single_family"),
            "permit_mf": (perm_c.get(f) or {}).get("multi_family"),
        })
    meta = {
        "ur_label": cou.get("latest_label"),
        "ur_state_avg": (cou.get("kpis") or {}).get("statewide_avg_unemp"),
        "n_ranked": n_ranked,
        "pop_year": pop.get("latest_year"),
        "gdp_year": gdp["county_gdp"].get("year"),
        "acs_year": hou["county_acs"].get("year"),
        "permit_year": hou["county_permits"].get("year"),
    }
    return recs, meta


# ---------- formatting helpers (mirror GE.fmt; null-safe) ----------

def _num(v):  return "—" if v is None else f"{round(v):,}"
def _usd(v):  return "—" if v is None else f"${round(v):,}"
def _pct(v):  return "—" if v is None else f"{v:.1f}%"
def _sg(v):   return "—" if v is None else (f"+{v:,}" if v >= 0 else f"{v:,}")
def _bn(v):   return "—" if v is None else f"${v:.2f}bn"


def _ord(n):
    if n is None: return "—"
    s = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{s}"


# ---------- page template ----------

def render_page(r: dict, meta: dict) -> str:
    name = r["name"]
    metro = r["metro"]
    if metro:
        place_line = f'In the <a href="/msa/{metro["slug"]}/">{metro["name"]} metro</a> &middot; Georgia'
        metro_card = f'<a href="/msa/{metro["slug"]}/">{metro["name"]} metro &rarr;</a>'
    else:
        place_line = "Non-metro Georgia"
        metro_card = "Non-metro Georgia"

    # unemployment vs state average
    ur, avg = r["ur_latest"], meta["ur_state_avg"]
    if ur is not None and avg is not None:
        d = ur - avg
        cls = "up" if d > 0.05 else ("down" if d < -0.05 else "flat")
        ur_delta = f'<div class="delta {cls}">{d:+.1f} pp vs state ({_pct(avg)})</div>'
    else:
        ur_delta = '<div class="delta">vs state avg</div>'
    rank_line = (f'{_ord(r["ur_rank"])}-lowest of {meta["n_ranked"]} counties'
                 if r["ur_rank"] else "")

    # .delta.down is teal (good), .delta.up is coral — population growth is "good",
    # so positive growth uses the "down" (teal) class.
    growth_cls = "down" if (r["growth_pct"] or 0) >= 0 else "up"
    base_year = (meta["pop_year"] - 5) if meta.get("pop_year") else ""  # PEP 2020 base → latest

    labels = [p["date"] for p in r["ur_series"]]
    values = [p["value"] for p in r["ur_series"]]
    series_json = json.dumps({"labels": labels, "values": values}, separators=(",", ":"))

    # population components (only render the row if we have any)
    comp_cards = ""
    if any(r[k] is not None for k in ("dom_mig", "intl_mig", "natural")):
        comp_cards = f"""
      <div class="stat-grid">
        <div class="stat-card"><div class="label">Domestic migration</div><div class="value">{_sg(r['dom_mig'])}</div></div>
        <div class="stat-card"><div class="label">International migration</div><div class="value">{_sg(r['intl_mig'])}</div></div>
        <div class="stat-card"><div class="label">Natural change</div><div class="value">{_sg(r['natural'])}</div></div>
        <div class="stat-card"><div class="label">Net population change</div><div class="value">{_sg(r['growth_abs'])}</div></div>
      </div>"""

    permits = "—"
    if r["permit_sf"] is not None or r["permit_mf"] is not None:
        permits = f"{(r['permit_sf'] or 0) + (r['permit_mf'] or 0):,} ({_num(r['permit_sf'])} SF / {_num(r['permit_mf'])} MF)"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{name} County, Georgia — Population, Jobs, GDP & Housing | Georgia Economics</title>
<meta name="description" content="{name} County, Georgia at a glance: population {_num(r['pop_latest'])}, unemployment {_pct(r['ur_latest'])}, GDP per capita {_usd(r['gdp_pc'])}, median home value {_usd(r['home_value'])}. Live data from BLS, BEA, and Census.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- GEN:HEAD -->
<!-- /GEN:HEAD -->
</head>
<body>

<!-- GEN:HEADER -->
<!-- /GEN:HEADER -->

<main>
  <section class="page-header">
    <h1>{name} County</h1>
    <p class="subtitle">{place_line}</p>
    <div class="latest">Unemployment as of <strong>{meta['ur_label']}</strong> &middot; population vintage <strong>{meta['pop_year']}</strong> &middot; GDP <strong>{meta['gdp_year']}</strong> &middot; ACS <strong>{meta['acs_year']}</strong></div>
  </section>

  <div class="kpi-grid">
    <div class="kpi"><div class="label">Population</div><div class="value">{_num(r['pop_latest'])}</div><div class="delta {growth_cls}">{_pct(r['growth_pct'])} since {base_year} ({_sg(r['growth_abs'])})</div></div>
    <div class="kpi"><div class="label">Unemployment</div><div class="value">{_pct(r['ur_latest'])}</div>{ur_delta}</div>
    <div class="kpi"><div class="label">GDP / capita</div><div class="value">{_usd(r['gdp_pc'])}</div><div class="delta">Total {_bn(r['gdp_bn'])}</div></div>
    <div class="kpi"><div class="label">Median home value</div><div class="value">{_usd(r['home_value'])}</div><div class="delta">Rent {_usd(r['rent'])}/mo</div></div>
    <div class="kpi"><div class="label">Median household income</div><div class="value">{_usd(r['income'])}</div><div class="delta">Homeownership {_pct(r['ownership'])}</div></div>
    <div class="kpi"><div class="label">Metro area</div><div class="value" style="font-size:16px; line-height:1.25;">{metro_card}</div><div class="delta">{rank_line}</div></div>
  </div>

  <div class="chart-panel" style="margin-top: 22px;">
    <h2>Unemployment — last 12 months</h2>
    <p class="sub">Monthly local-area unemployment rate (BLS LAUS), {labels[0] if labels else ''} – {labels[-1] if labels else ''}.</p>
    <div class="chart-canvas-med"><canvas id="ur-trend"></canvas></div>
  </div>

  <div class="chart-panel" style="margin-top: 22px;">
    <h2>Population &amp; migration</h2>
    <p class="sub">Components of change behind {name} County's {_pct(r['growth_pct'])} growth (Census PEP, {meta['pop_year']}).</p>
    {comp_cards or '<p class="source-note">County component-of-change detail not available.</p>'}
  </div>

  <div class="chart-panel" style="margin-top: 22px;">
    <h2>Housing</h2>
    <p class="sub">Median values from Census ACS ({meta['acs_year']}); permits from the Building Permits Survey ({meta['permit_year']}).</p>
    <div class="data-table-wrap">
      <table class="data-table">
        <tbody>
          <tr><td class="row-name">Median home value</td><td class="numeric">{_usd(r['home_value'])}</td></tr>
          <tr><td class="row-name">Median gross rent</td><td class="numeric">{_usd(r['rent'])}/mo</td></tr>
          <tr><td class="row-name">Median household income</td><td class="numeric">{_usd(r['income'])}</td></tr>
          <tr><td class="row-name">Homeownership rate</td><td class="numeric">{_pct(r['ownership'])}</td></tr>
          <tr><td class="row-name">Building permits ({meta['permit_year']})</td><td class="numeric">{permits}</td></tr>
        </tbody>
      </table>
    </div>
    <p class="source-note">A county-level overview. For deep metro analysis, see the {name} County metro report linked above. Statewide context: <a href="/population/">Population</a> &middot; <a href="/labor/">Labor</a> &middot; <a href="/gdp/">GDP</a> &middot; <a href="/housing/">Housing</a>.</p>
  </div>
</main>

<!-- GEN:FOOTER -->
<!-- /GEN:FOOTER -->

<script>
document.addEventListener('DOMContentLoaded', function () {{
  var S = {series_json};
  var el = document.getElementById('ur-trend');
  if (!el || !window.Chart || !S.labels.length) return;
  new Chart(el.getContext('2d'), {{
    type: 'line',
    data: {{ labels: S.labels, datasets: [{{
      label: 'Unemployment %', data: S.values,
      borderColor: GE.BRAND.coral, backgroundColor: 'rgba(212,98,74,0.10)',
      borderWidth: 2, fill: true, tension: 0.3, pointRadius: 2,
    }}] }},
    options: {{ responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: GE.axes({{ xTicks: 12 }}) }},
  }});
}});
</script>
</body>
</html>
"""


# ---------- county A–Z index (filled into /counties/) ----------

def render_county_index(recs: list[dict]) -> str:
    rows = []
    for r in sorted(recs, key=lambda x: x["name"]):
        metro = f' <span class="cx-metro">{r["metro"]["name"]}</span>' if r["metro"] else ""
        rows.append(f'<a href="/counties/{r["slug"]}/"><span class="cx-name">{r["name"]}</span>{metro}</a>')
    return '<div class="county-az">\n  ' + "\n  ".join(rows) + "\n</div>"


def fill_region(path: Path, name: str, body: str) -> bool:
    html = path.read_text()
    pat = re.compile(rf"(<!-- GEN:{name} -->).*?(<!-- /GEN:{name} -->)", re.S)
    if not pat.search(html):
        return False
    path.write_text(pat.sub(lambda _m: f"<!-- GEN:{name} -->\n{body}\n<!-- /GEN:{name} -->", html))
    return True


def main(argv: list[str]) -> int:
    recs, meta = load_records()
    targets = recs
    if argv:
        want = {a.lower() for a in argv}
        targets = [r for r in recs if r["fips"] in want or r["slug"] in want or r["name"].lower() in want]
        if not targets:
            print(f"no county matched {argv}", file=sys.stderr)
            return 2

    for r in targets:
        out = COUNTIES_DIR / r["slug"] / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_page(r, meta))
    print(f"generate_county_pages: wrote {len(targets)} county page(s).")

    # Only refresh the index + lookup when generating the full set.
    if not argv:
        idx = {r["fips"]: {"slug": r["slug"], "name": r["name"],
                           "metro": (r["metro"]["slug"] if r["metro"] else None)} for r in recs}
        (DATA / "county_index.json").write_text(
            json.dumps({"_note": "fips -> county profile slug/name/metro; generated by generate_county_pages.py",
                        "counties": idx}, ensure_ascii=False, indent=1) + "\n")
        if fill_region(COUNTIES_DIR / "index.html", "COUNTY_INDEX", render_county_index(recs)):
            print("  filled GEN:COUNTY_INDEX on /counties/")
        else:
            print("  (no GEN:COUNTY_INDEX marker on /counties/ — skipped A–Z list)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
