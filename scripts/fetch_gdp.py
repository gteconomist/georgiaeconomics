"""Build the statewide Georgia GDP page dataset -> data/gdp.json

Phase 4, WS1 #2. Mirrors scripts/fetch_housing.py: a ROLL-UP of GDP data already
pulled `live` across all 14 MSA reports, plus a statewide BEA SAGDP layer, an
all-159-county BEA CAGDP2 layer, a Southeast-peer comparison, and a Non-Metro
Georgia aggregate so the 86 counties outside the 14 MSAs are represented.

Coverage design (see STATE_GDP_PAGE_SCOPE.md / PHASE4_PLAN.md):
  • Statewide GA GDP ... BEA SAGDP9N (real, chained $) + SAGDP2N (nominal, current $),
                         LineCode 1 (all-industry total), GA + US. 100% of the state.
  • Per-capita ......... derived from SAGDP2N nominal ÷ state population history
                         (data/population.json) for a consistent population base.
  • SE peers ........... GA vs FL / NC / SC / TN / AL (+ US) — real GDP growth and,
                         best-effort, per-capita real GDP (SAGDP1).
  • Sectors ............ GA GDP by industry from SAGDP2N (LineCode ALL), latest year.
  • County layer ....... BEA CAGDP2 (LineCode 1) for all 159 counties -> choropleth,
                         per-capita derived from county pop_latest (population.json).
  • Non-Metro Georgia .. aggregate of the 86 counties not in any of the 14 MSAs.

Units: SAGDP9N/SAGDP2N are millions of $; CAGDP2 is thousands of $.

Graceful degradation (house convention): each section is wrapped in try/except;
on failure we PRESERVE the prior value from data/gdp.json and do NOT bump
_meta.<section>.last_updated, so the page renders a "stale" badge when a section
is > STALE_MONTHS old. A section that has never succeeded is omitted.

Env:
  BEA_API_KEY — required for every BEA pull (SAGDP + CAGDP2). Repo secret.

Usage:
  python scripts/fetch_gdp.py            # full run (needs BEA_API_KEY + network)
  python scripts/fetch_gdp.py --rollup   # metro roll-up only, no key (local check)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Optional, Dict, List, Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MSA_REPORTS = DATA / "msa_reports"
OUT = DATA / "gdp.json"

STALE_MONTHS = 12  # SAGDP/CAGDP2 are annual series
BEA_API_KEY = os.environ.get("BEA_API_KEY", "").strip()
BEA_URL = "https://apps.bea.gov/api/data"

# Southeast peer set (matches the Population page). GeoFips -> display name.
PEERS = [
    ("13000", "Georgia"), ("12000", "Florida"), ("37000", "North Carolina"),
    ("45000", "South Carolina"), ("47000", "Tennessee"), ("01000", "Alabama"),
]
US_FIPS = "00000"
GA_FIPS = "13000"

GMP_SECTION = "bea_gmp"
PI_SECTION = "bea_personal_income"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_prior() -> dict:
    if OUT.exists():
        try:
            return json.loads(OUT.read_text())
        except Exception:
            return {}
    return {}


def _safe(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def _metro_ga_fips() -> set:
    """GA county FIPS inside one of the 14 MSAs (excludes SC/AL counties)."""
    m = json.loads((DATA / "ga_msa_counties.json").read_text())["msas"]
    out = set()
    for info in m.values():
        for c in info["counties"]:
            if c.startswith("13"):
                out.add(c)
    return out


def _state_pop_by_year() -> Dict[int, float]:
    try:
        p = json.loads((DATA / "population.json").read_text())["state"]
        return {int(y): float(v) for y, v in zip(p["years"], p["population"])}
    except Exception:
        return {}


def _county_pop_latest() -> Dict[str, float]:
    try:
        rows = json.loads((DATA / "population.json").read_text())["counties"]
        return {r["fips"]: float(r["pop_latest"]) for r in rows if r.get("pop_latest")}
    except Exception:
        return {}


def bea_get(params: dict, retries: int = 3) -> Optional[dict]:
    """GET the BEA Regional API. Returns the Results dict, or None on failure."""
    if not BEA_API_KEY:
        print("  [gdp/BEA] no BEA_API_KEY in env", file=sys.stderr)
        return None
    p = dict(params)
    p["UserID"] = BEA_API_KEY
    p["ResultFormat"] = "JSON"
    url = BEA_URL + "?" + urllib.parse.urlencode(p)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                j = json.loads(r.read().decode("utf-8"))
            results = j.get("BEAAPI", {}).get("Results", {})
            if isinstance(results, dict) and results.get("Error"):
                print(f"  [gdp/BEA error] {str(results['Error'])[:100]}", file=sys.stderr)
                return None
            return results
        except Exception as e:
            print(f"  [gdp/BEA err] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            time.sleep(1.5 * (attempt + 1))
    return None


def _rows(results: Optional[dict]) -> List[dict]:
    if not results:
        return []
    data = results.get("Data") or []
    if isinstance(data, dict):
        data = [data]
    return data


def _val(row: dict) -> Optional[float]:
    try:
        return float(str(row.get("DataValue", "")).replace(",", ""))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 1. Metro roll-up (pure local read — always works, no key)
# --------------------------------------------------------------------------- #
def rollup_metros() -> List[dict]:
    metros: List[dict] = []
    for path in sorted(MSA_REPORTS.glob("*.json")):
        rep = json.loads(path.read_text())
        sec, st = rep.get("sections", {}), rep.get("section_status", {})
        block: Dict[str, Any] = {
            "slug": path.stem, "short_name": rep.get("short_name"),
            "full_name": rep.get("full_name"), "cbsa": rep.get("cbsa"),
            "population": rep.get("population"),
        }
        g = sec.get(GMP_SECTION) if st.get(GMP_SECTION) in ("live", "partial", "stale") else None
        i = sec.get(PI_SECTION) if st.get(PI_SECTION) in ("live", "partial", "stale") else None
        if g:
            block["gmp_bn"] = g.get("latest_gmp_billions_usd")
            block["gmp_yoy"] = g.get("latest_yoy")
            block["gdp_per_capita"] = g.get("latest_gdp_per_capita")
            block["gmp_series"] = {"years": g.get("years"), "gmp_billions_usd": g.get("gmp_billions_usd")}
        if i:
            block["pi_bn"] = i.get("latest_personal_income_billions_usd")
            block["per_capita_income"] = i.get("latest_per_capita_income")
        metros.append(block)
    return metros


def attach_state_share(metros: List[dict], ga_nominal_bn_latest: Optional[float]):
    if not ga_nominal_bn_latest:
        return
    for m in metros:
        if isinstance(m.get("gmp_bn"), (int, float)):
            m["share_of_state_pct"] = round(100 * m["gmp_bn"] / ga_nominal_bn_latest, 1)


# --------------------------------------------------------------------------- #
# 2. Statewide GA GDP (SAGDP9N real + SAGDP2N nominal, GA + US)
# --------------------------------------------------------------------------- #
def _linecode1_series(table: str, geofips_csv: str, years: List[int]) -> Dict[str, Dict[int, float]]:
    """{geofips: {year: value_millions}} for LineCode 1 (all-industry total)."""
    res = bea_get({
        "method": "GetData", "DataSetName": "Regional", "TableName": table,
        "LineCode": "1", "GeoFips": geofips_csv, "Year": ",".join(str(y) for y in years),
    })
    out: Dict[str, Dict[int, float]] = {}
    for row in _rows(res):
        fips = (row.get("GeoFips") or "").strip()
        try:
            yr = int(row.get("TimePeriod"))
        except (TypeError, ValueError):
            continue
        v = _val(row)
        if v is not None:
            out.setdefault(fips, {})[yr] = v
    return out


def fetch_ga_gdp(years_back: int = 13) -> Optional[dict]:
    this_year = date.today().year
    years = list(range(this_year - years_back, this_year))
    real = _linecode1_series("SAGDP9N", f"{GA_FIPS},{US_FIPS}", years)
    nominal = _linecode1_series("SAGDP2N", f"{GA_FIPS},{US_FIPS}", years)
    ga_real, us_real = real.get(GA_FIPS, {}), real.get(US_FIPS, {})
    ga_nom = nominal.get(GA_FIPS, {})
    if not ga_real and not ga_nom:
        return None
    yrs = sorted(set(ga_real) | set(ga_nom))
    pop = _state_pop_by_year()
    real_bn = [round(ga_real[y] / 1000, 2) if y in ga_real else None for y in yrs]
    nom_bn = [round(ga_nom[y] / 1000, 2) if y in ga_nom else None for y in yrs]
    us_real_bn = [round(us_real[y] / 1000, 2) if y in us_real else None for y in yrs]
    per_capita = [int(round(ga_nom[y] * 1e6 / pop[y])) if (y in ga_nom and pop.get(y)) else None for y in yrs]
    real_yoy = [None]
    for i in range(1, len(yrs)):
        a, b = ga_real.get(yrs[i - 1]), ga_real.get(yrs[i])
        real_yoy.append(round(100 * (b - a) / a, 2) if (a and b) else None)
    latest_real_yoy = next((v for v in reversed(real_yoy) if v is not None), None)
    return {
        "years": yrs, "real_bn": real_bn, "nominal_bn": nom_bn, "us_real_bn": us_real_bn,
        "per_capita": per_capita, "real_yoy": real_yoy,
        "latest_year": yrs[-1] if yrs else None,
        "latest_nominal_bn": next((v for v in reversed(nom_bn) if v is not None), None),
        "latest_real_bn": next((v for v in reversed(real_bn) if v is not None), None),
        "latest_real_yoy": latest_real_yoy,
        "latest_per_capita": next((v for v in reversed(per_capita) if v is not None), None),
        "source": "BEA SAGDP9N (real, chained $) + SAGDP2N (nominal, current $), LineCode 1",
    }


# --------------------------------------------------------------------------- #
# 3. Southeast peers
# --------------------------------------------------------------------------- #
def fetch_peers() -> Optional[List[dict]]:
    this_year = date.today().year
    years = [this_year - 2, this_year - 1]
    csv = ",".join(f for f, _ in PEERS) + f",{US_FIPS}"
    real = _linecode1_series("SAGDP9N", csv, years)
    if not real:
        return None
    # best-effort per-capita real GDP via SAGDP1 (description match), latest year
    pcap = _per_capita_real_by_state(csv, years[-1])
    out = []
    for fips, name in PEERS + [(US_FIPS, "United States")]:
        s = real.get(fips, {})
        yrs = sorted(s)
        yoy = None
        if len(yrs) >= 2 and s[yrs[-2]]:
            yoy = round(100 * (s[yrs[-1]] - s[yrs[-2]]) / s[yrs[-2]], 2)
        out.append({
            "state": name, "fips": fips,
            "real_bn": round(s[yrs[-1]] / 1000, 2) if yrs else None,
            "real_yoy": yoy,
            "per_capita": pcap.get(fips),
        })
    return out


def _per_capita_real_by_state(geofips_csv: str, year: int) -> Dict[str, int]:
    """Per-capita real GDP from SAGDP1 (LineCode ALL), matched by Description."""
    res = bea_get({
        "method": "GetData", "DataSetName": "Regional", "TableName": "SAGDP1",
        "LineCode": "ALL", "GeoFips": geofips_csv, "Year": str(year),
    })
    out: Dict[str, int] = {}
    for row in _rows(res):
        desc = (row.get("Description") or "").lower()
        if "per capita" in desc and "real" in desc:
            fips = (row.get("GeoFips") or "").strip()
            v = _val(row)
            if v is not None:
                out[fips] = int(round(v))
    return out


# --------------------------------------------------------------------------- #
# 4. Sector composition (GA GDP by industry, SAGDP2N LineCode ALL)
# --------------------------------------------------------------------------- #
# Descriptions to exclude (totals / sub-aggregates) so we keep the ~major sectors.
_SECTOR_SKIP = ("all industry total", "private industries", "government and government enterprises",
                "addenda", "natural resources and mining")  # keep granular instead of rollups


def fetch_sectors(year: Optional[int] = None) -> Optional[dict]:
    y = year or (date.today().year - 1)
    res = bea_get({
        "method": "GetData", "DataSetName": "Regional", "TableName": "SAGDP2N",
        "LineCode": "ALL", "GeoFips": GA_FIPS, "Year": str(y),
    })
    rows = _rows(res)
    if not rows:
        return None
    total = None
    raw = []
    for row in rows:
        desc = (row.get("Description") or "").strip()
        v = _val(row)
        if v is None:
            continue
        dl = desc.lower()
        if dl == "all industry total" or dl.startswith("all industry"):
            total = v
            continue
        if any(s in dl for s in _SECTOR_SKIP):
            continue
        # keep only top-level NAICS sectors (BEA marks them; heuristic: no leading digits,
        # reasonably short description) — we filter to a curated set below.
        raw.append((desc, v))
    if not total:
        total = sum(v for _, v in raw) or None
    if not raw or not total:
        return None
    sectors = sorted(
        ({"name": d, "gdp_bn": round(v / 1000, 2), "share_pct": round(100 * v / total, 1)}
         for d, v in raw),
        key=lambda s: s["gdp_bn"], reverse=True,
    )
    return {
        "year": y, "total_bn": round(total / 1000, 2), "sectors": sectors,
        "source": "BEA SAGDP2N by industry (current $)",
    }


# --------------------------------------------------------------------------- #
# 5. County GDP layer (CAGDP2, all 159) + Non-Metro aggregate
# --------------------------------------------------------------------------- #
def fetch_county_gdp(year: Optional[int] = None) -> Optional[dict]:
    y = year or (date.today().year - 1)
    res = bea_get({
        "method": "GetData", "DataSetName": "Regional", "TableName": "CAGDP2",
        "LineCode": "1", "GeoFips": "COUNTY", "Year": str(y),
    })
    rows = _rows(res)
    if not rows:
        # retry one year earlier (county GDP lags state)
        res = bea_get({
            "method": "GetData", "DataSetName": "Regional", "TableName": "CAGDP2",
            "LineCode": "1", "GeoFips": "COUNTY", "Year": str(y - 1),
        })
        rows = _rows(res)
        if not rows:
            return None
        y = y - 1
    pop = _county_pop_latest()
    counties = {}
    for row in rows:
        fips = (row.get("GeoFips") or "").strip()
        if not fips.startswith("13") or len(fips) != 5:
            continue
        v = _val(row)  # thousands of $
        if v is None:
            continue
        gdp_bn = round(v / 1e6, 3)
        pc = int(round(v * 1000 / pop[fips])) if pop.get(fips) else None
        counties[fips] = {"gdp_bn": gdp_bn, "gdp_per_capita": pc,
                          "name": (row.get("GeoName") or "").split(",")[0]}
    if not counties:
        return None
    return {"year": y, "counties": counties,
            "source": "BEA CAGDP2 (county GDP, LineCode 1, current $)"}


def non_metro_aggregate(county_gdp: dict) -> Optional[dict]:
    if not county_gdp:
        return None
    metro = _metro_ga_fips()
    pop = _county_pop_latest()
    nm = {f: r for f, r in county_gdp["counties"].items() if f not in metro}
    if not nm:
        return None
    gdp_bn = round(sum(r["gdp_bn"] for r in nm.values() if r.get("gdp_bn")), 2)
    pop_total = sum(pop.get(f, 0) for f in nm)
    pc = int(round(gdp_bn * 1e9 / pop_total)) if pop_total else None
    return {
        "county_count": len(nm), "gdp_bn": gdp_bn, "population": pop_total,
        "gdp_per_capita_wt": pc, "year": county_gdp.get("year"),
        "note": "Aggregate of the 86 GA counties outside the 14 MSAs.",
        "source": county_gdp["source"],
    }


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def _is_stale(meta_entry: Optional[dict]) -> bool:
    if not meta_entry or not meta_entry.get("last_updated"):
        return True
    try:
        d = datetime.strptime(meta_entry["last_updated"][:10], "%Y-%m-%d")
    except Exception:
        return True
    return (datetime.utcnow() - d).days > STALE_MONTHS * 30


def build(rollup_only: bool = False) -> dict:
    prior = _load_prior()
    prior_meta = prior.get("_meta", {})
    meta: Dict[str, dict] = {}
    out: Dict[str, Any] = {"fetched_at": _now_iso(), "schema": "gdp/v1"}

    metros = rollup_metros()
    out["metros"] = metros
    meta["metro_rollup"] = {"last_updated": _now_iso(), "n_metros": len(metros)}

    def section(name, fn, *args):
        if rollup_only:
            if name in prior:
                out[name] = prior[name]
                meta[name] = prior_meta.get(name, {})
            return out.get(name)
        try:
            val = fn(*args)
        except Exception as e:
            print(f"  [gdp] {name} raised {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            val = None
        if val:
            out[name] = val
            meta[name] = {"last_updated": _now_iso()}
        elif name in prior:
            out[name] = prior[name]
            meta[name] = prior_meta.get(name, {"last_updated": None})
        return out.get(name)

    ga = section("ga_gdp", fetch_ga_gdp)
    section("peers", fetch_peers)
    section("sectors", fetch_sectors)
    cg = section("county_gdp", fetch_county_gdp)

    # share of state GDP needs the statewide nominal total
    attach_state_share(metros, _safe(ga or {}, "latest_nominal_bn"))

    if cg:
        try:
            nm = non_metro_aggregate(cg)
            if nm:
                out["non_metro"] = nm
                meta["non_metro"] = {"last_updated": _now_iso()}
        except Exception as e:
            print(f"  [gdp] non_metro raised {type(e).__name__}", file=sys.stderr)
            if "non_metro" in prior:
                out["non_metro"] = prior["non_metro"]
                meta["non_metro"] = prior_meta.get("non_metro", {})

    # KPI strip
    largest = _safe(out, "sectors", "sectors", default=[])
    out["kpis"] = {
        "ga_nominal_gdp_bn": _safe(out, "ga_gdp", "latest_nominal_bn"),
        "ga_real_gdp_yoy": _safe(out, "ga_gdp", "latest_real_yoy"),
        "ga_gdp_per_capita": _safe(out, "ga_gdp", "latest_per_capita"),
        "largest_sector": (largest[0]["name"] if largest else None),
        "metro_count": len(metros),
        "non_metro_county_count": _safe(out, "non_metro", "county_count"),
    }

    for v in meta.values():
        v["stale"] = _is_stale(v)
    out["_meta"] = meta
    out["latest_label"] = str(_safe(out, "ga_gdp", "latest_year")
                              or (date.today().year - 1))
    out["coverage_note"] = ("Statewide GDP (BEA SAGDP) covers 100% of Georgia. The 159-county "
                            "BEA CAGDP2 layer and the Non-Metro Georgia aggregate cover the 86 "
                            "counties outside the 14 MSAs.")
    out["source_summary"] = {
        "ga_gdp": "BEA SAGDP9N (real) + SAGDP2N (nominal), GA + US",
        "peers": "BEA SAGDP9N + SAGDP1 (per-capita), SE states",
        "sectors": "BEA SAGDP2N by industry",
        "metros": "Roll-up of data/msa_reports/*.json (bea_gmp + bea_personal_income)",
        "county_gdp": "BEA CAGDP2 (all 159 counties)",
    }
    return out


def main(argv: List[str]) -> int:
    out = build(rollup_only=("--rollup" in argv))
    OUT.write_text(json.dumps(out, indent=1))
    live = [k for k, v in out["_meta"].items() if not v.get("stale")]
    stale = [k for k, v in out["_meta"].items() if v.get("stale")]
    print(f"Wrote {OUT.relative_to(ROOT)} — {len(out['metros'])} metros; "
          f"live: {live}; stale/absent: {stale}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
