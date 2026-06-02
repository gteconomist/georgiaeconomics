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
    # BEA does NOT accept percent-encoded values — urlencoding the commas in a
    # multi-year `Year=2013,2014,...` list breaks the request with APIErrorCode
    # 101 ("Unknown error"). Build the query string with RAW values, matching the
    # proven scripts/reporting/pull_bea.py path (the county CAGDP2 call works that way).
    qs = "&".join(f"{k}={v}" for k, v in p.items())
    url = BEA_URL + "?" + qs
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                j = json.loads(r.read().decode("utf-8"))
            results = (j.get("BEAAPI") or {}).get("Results") or {}
            if isinstance(results, list):
                results = results[0] if results else {}
            tn = str(params.get("TableName") or "")
            err = results.get("Error") if isinstance(results, dict) else None
            if err:
                if isinstance(err, list):
                    err = err[0] if err else {}
                desc = (err.get("APIErrorDescription") or err.get("ErrorDescription") or "") \
                    if isinstance(err, dict) else str(err)
                code = str(err.get("APIErrorCode") or "") if isinstance(err, dict) else ""
                low = str(desc).lower()
                # APIErrorCode 101 ("Unknown error") is BEA's response when there is simply
                # no data for the requested params (e.g. a year not published yet) — expected
                # while probing, so don't treat it as a hard error or print noise.
                expected = (code == "101"
                            or any(s in low for s in ("not available", "no data", "invalid year")))
                # TEMP DIAG: always surface state-level (SAGDP*) errors in full while we
                # stabilise the statewide GDP pulls.
                if tn.startswith("SAGDP") or not expected:
                    print(f"  [gdp/BEA error] {tn} "
                          f"geo={params.get('GeoFips')} lc={params.get('LineCode')} "
                          f"yr={params.get('Year')}: {err}", file=sys.stderr)
                return None
            # TEMP DIAG: dump the raw BEA response for state-level GetData calls.
            if tn.startswith("SAGDP") and str(params.get("method")) == "GetData":
                rkeys = list(results.keys()) if isinstance(results, dict) else type(results).__name__
                print(f"  [gdp/BEA diag] {tn} geo={params.get('GeoFips')} yr={params.get('Year')} "
                      f"Resultkeys={rkeys} raw={str(j)[:400]}", file=sys.stderr)
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
# BEA Regional GetData wants ONE GeoFips per request (or the "STATE" keyword for
# all states); comma-joined GeoFips lists and LineCode="ALL" are NOT supported and
# return nothing. These helpers mirror the proven scripts/fetch_film.py pattern.

def _bea_series(table: str, geofips: str, years: Optional[List[int]], line_code: str = "1") -> Dict[int, float]:
    """{year: value_millions} for one table / single GeoFips / linecode.
    Pass years=None to request Year='ALL' (all published years) — preferred for
    timeseries, since requesting a not-yet-published year makes BEA 101 the whole call."""
    res = bea_get({
        "method": "GetData", "DataSetName": "Regional", "TableName": table,
        "LineCode": line_code, "GeoFips": geofips,
        "Year": "ALL" if years is None else ",".join(str(y) for y in years),
    })
    out: Dict[int, float] = {}
    for row in _rows(res):
        try:
            yr = int(row.get("TimePeriod"))
        except (TypeError, ValueError):
            continue
        v = _val(row)
        if v is not None:
            out[yr] = v
    return out


def _bea_all_states(table: str, line_code: str, year: int) -> Dict[str, float]:
    """{state_fips: value_millions} for all states in one year (GeoFips='STATE')."""
    res = bea_get({
        "method": "GetData", "DataSetName": "Regional", "TableName": table,
        "LineCode": line_code, "GeoFips": "STATE", "Year": str(year),
    })
    out: Dict[str, float] = {}
    for row in _rows(res):
        fips = (row.get("GeoFips") or "").strip()
        v = _val(row)
        if fips and v is not None:
            out[fips] = v
    return out


def _bea_linecodes(table: str) -> List[dict]:
    """All LineCode {Key, Desc} for a table (GetParameterValuesFiltered)."""
    res = bea_get({
        "method": "GetParameterValuesFiltered", "DataSetName": "Regional",
        "TargetParameter": "LineCode", "TableName": table,
    })
    vals = (res or {}).get("ParamValue", []) if isinstance(res, dict) else []
    if isinstance(vals, dict):
        vals = [vals]
    return vals or []


def fetch_ga_gdp(years_back: int = 13) -> Optional[dict]:
    # Request ALL published years (avoids 101 on the not-yet-published latest year),
    # then keep the most recent `years_back` for the chart.
    ga_real = _bea_series("SAGDP9N", GA_FIPS, None)   # real, chained $
    us_real = _bea_series("SAGDP9N", US_FIPS, None)
    ga_nom = _bea_series("SAGDP2N", GA_FIPS, None)     # nominal, current $
    if not ga_real and not ga_nom:
        return None
    yrs = sorted(set(ga_real) | set(ga_nom))[-years_back:]
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
    # SAGDP annual GDP lags ~1.5 yrs, so the latest published year is ~this_year-2.
    # Probe from there (newest first) and fall back if a year isn't out yet.
    for y_latest in (this_year - 2, this_year - 1, this_year - 3, this_year - 4):
        cur = _bea_all_states("SAGDP9N", "1", y_latest)
        if cur:
            break
    else:
        return None
    prev = _bea_all_states("SAGDP9N", "1", y_latest - 1)
    us_cur = _bea_series("SAGDP9N", US_FIPS, [y_latest])          # US not in 'STATE'
    us_prev = _bea_series("SAGDP9N", US_FIPS, [y_latest - 1])
    try:
        pcap = _per_capita_real_by_state(y_latest)
    except Exception:
        pcap = {}
    out = []
    for fips, name in PEERS + [(US_FIPS, "United States")]:
        if fips == US_FIPS:
            c, p = us_cur.get(y_latest), us_prev.get(y_latest - 1)
        else:
            c, p = cur.get(fips), prev.get(fips)
        yoy = round(100 * (c - p) / p, 2) if (c and p) else None
        out.append({
            "state": name, "fips": fips,
            "real_bn": round(c / 1000, 2) if c else None,
            "real_yoy": yoy,
            "per_capita": pcap.get(fips),
        })
    return out


def _per_capita_real_by_state(year: int) -> Dict[str, int]:
    """Per-capita real GDP for all states (SAGDP1), via the proven STATE keyword
    + a specific LineCode found by description (no LineCode='ALL')."""
    lc = None
    for v in _bea_linecodes("SAGDP1"):
        desc = (v.get("Desc") or "").lower()
        if "per capita" in desc and "real" in desc:
            lc = str(v.get("Key")); break
    if not lc:
        return {}
    raw = _bea_all_states("SAGDP1", lc, year)
    return {f: int(round(v)) for f, v in raw.items()}


# --------------------------------------------------------------------------- #
# 4. Sector composition (GA GDP by industry, SAGDP2N — per-linecode)
# --------------------------------------------------------------------------- #
# Top-level NAICS sectors we surface. We match each to its SAGDP2N LineCode by
# the start of the line description, then fetch GA's value for that linecode
# (single-GeoFips GetData — the proven pattern; LineCode='ALL' is unsupported).
_SECTOR_DESC_PREFIXES = [
    "agriculture, forestry, fishing", "mining, quarrying", "utilities", "construction",
    "manufacturing", "wholesale trade", "retail trade", "transportation and warehousing",
    "information", "finance and insurance", "real estate and rental",
    "professional, scientific, and technical", "management of companies",
    "administrative and support", "educational services", "health care and social assistance",
    "arts, entertainment, and recreation", "accommodation and food services",
    "other services", "government and government enterprises",
]


def fetch_sectors(year: Optional[int] = None) -> Optional[dict]:
    # Request ALL published years (a single not-yet-published year would 101 the
    # whole call) and use the latest year actually returned for each line code.
    yrs = [year] if year else None
    codes = _bea_linecodes("SAGDP2N")
    if not codes:
        return None
    # map each wanted top-level sector to the first matching line code
    wanted: Dict[str, tuple] = {}
    for v in codes:
        desc = (v.get("Desc") or "").strip()
        dl = desc.lower()
        for pref in _SECTOR_DESC_PREFIXES:
            if pref not in wanted and dl.startswith(pref):
                wanted[pref] = (str(v.get("Key")), desc)
                break
    if not wanted:
        return None
    sectors: List[dict] = []
    total = 0.0
    used_year = None
    for lc, desc in wanted.values():
        s = _bea_series("SAGDP2N", GA_FIPS, yrs, line_code=lc)
        if not s:
            continue
        yy = max(s)                      # latest year actually returned
        v = s[yy]
        used_year = yy if used_year is None else max(used_year, yy)
        sectors.append({"name": desc, "gdp_bn": round(v / 1000, 2), "_v": v})
        total += v
    if not sectors or total <= 0:
        return None
    for s in sectors:
        s["share_pct"] = round(100 * s.pop("_v") / total, 1)
    sectors.sort(key=lambda s: s["gdp_bn"], reverse=True)
    return {
        "year": used_year, "total_bn": round(total / 1000, 2), "sectors": sectors,
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
