"""Build the statewide Georgia Housing page dataset -> data/housing.json

Phase 4, WS1 #1. This is primarily a ROLL-UP of housing data already pulled
`live` across all 14 MSA reports (data/msa_reports/*.json), plus a thin
statewide layer and an all-159-county ACS layer so the ~18% of Georgians in
the 86 non-metro counties are represented (not just the 73 metro counties).

Coverage design (see HOUSING_PAGE_SCOPE.md / PHASE4_PLAN.md):
  • Statewide GA HPI .... FRED GASTHPI (all-transactions) + USSTHPI for the
                          headline trend — covers 100% of the state.
  • Metro roll-up ....... 6 housing sections from each msa_reports/*.json
                          (fhfa_hpi, census_bps_permits, acs_affordability,
                          housing_affordability, housing_valuation,
                          acs_housing_characteristics).
  • County layer ........ ACS 5-year for all 159 counties (median home value,
                          median gross rent, % owner-occupied) -> choropleth.
  • Non-Metro Georgia ... population-weighted aggregate of the 86 counties not
                          in any of the 14 MSAs, so rural GA is visible.
  • County permits ...... best-effort per-county permits from the public Census
                          BPS county annual file (no key; runs in CI). Degrades
                          to absent if the file/format is unreachable.

Data sources by section drive these page blocks:
  ga_hpi              -> Home prices over time (GA + US lines)
  metros[]            -> Metro comparison, affordability, valuation, stock
  counties            -> 159-county choropleth (home value / ownership / rent)
  non_metro           -> "Non-Metro Georgia" aggregate callout
  statewide_medians   -> KPI strip

Graceful degradation (house convention):
  Each section is wrapped in try/except. On failure we PRESERVE the prior
  value from the existing data/housing.json and do NOT bump
  _meta.<section>.last_updated, so the page renders a "stale" badge when a
  section is > STALE_MONTHS old. A section that has never succeeded is omitted.

Env:
  FRED_API_KEY   — required for ga_hpi (GASTHPI/USSTHPI via FRED).
  CENSUS_API_KEY — required for the county ACS layer.
  Both are repo secrets already used by the MSA-report and permits workflows.

Usage:
  python scripts/fetch_housing.py            # full run (needs keys + network)
  python scripts/fetch_housing.py --rollup   # metro roll-up only, no keys
                                             #   (local validation path)
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
OUT = DATA / "housing.json"

sys.path.insert(0, str(ROOT / "scripts"))
from _ga_counties import GA_COUNTIES  # noqa: E402  (159 (fips, name) tuples)

# Reuse the FRED helpers from the existing FHFA puller (rate-limit + 429 backoff baked in).
sys.path.insert(0, str(ROOT / "scripts" / "reporting"))
try:
    from pull_fhfa import _fred_observations, _yoy_pct_quarterly  # type: ignore
except Exception:  # pragma: no cover - fallback if import path shifts
    _fred_observations = None
    _yoy_pct_quarterly = None

STALE_MONTHS = 6
CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "").strip()
CENSUS_BASE = "https://api.census.gov/data"

# The six housing sections each MSA report carries.
HOUSING_SECTIONS = [
    "fhfa_hpi",
    "census_bps_permits",
    "acs_affordability",
    "housing_affordability",
    "housing_valuation",
    "acs_housing_characteristics",
]

ALL_GA_FIPS = [f for f, _ in GA_COUNTIES]
GA_NAME = {f: n for f, n in GA_COUNTIES}


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


def _metro_ga_fips() -> set:
    """GA county FIPS that fall inside one of the 14 MSAs (excludes SC/AL counties)."""
    m = json.loads((DATA / "ga_msa_counties.json").read_text())["msas"]
    out = set()
    for info in m.values():
        for c in info["counties"]:
            if c.startswith("13"):
                out.add(c)
    return out


def _safe(d: dict, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


# --------------------------------------------------------------------------- #
# 1. Metro roll-up  (pure local read — always works, no keys)
# --------------------------------------------------------------------------- #
def rollup_metros() -> List[dict]:
    """Read all 14 msa_reports/*.json and extract a compact housing block each."""
    metros: List[dict] = []
    for path in sorted(MSA_REPORTS.glob("*.json")):
        rep = json.loads(path.read_text())
        sections = rep.get("sections", {})
        status = rep.get("section_status", {})
        block: Dict[str, Any] = {
            "slug": path.stem,
            "short_name": rep.get("short_name"),
            "full_name": rep.get("full_name"),
            "cbsa": rep.get("cbsa"),
            "population": rep.get("population"),
        }
        for sec in HOUSING_SECTIONS:
            if status.get(sec) in ("live", "partial", "stale") and sections.get(sec):
                block[sec] = sections[sec]
        metros.append(block)
    return metros


def statewide_medians(metros: List[dict]) -> dict:
    """Cross-metro medians + rankings for the KPI strip."""
    def col(getter):
        vals = []
        for m in metros:
            v = getter(m)
            if isinstance(v, (int, float)):
                vals.append(v)
        return vals

    hpi_yoy = col(lambda m: _safe(m, "fhfa_hpi", "latest_yoy"))
    per1k = col(lambda m: _safe(m, "census_bps_permits", "latest_per_1k"))
    afford = col(lambda m: _safe(m, "housing_affordability", "latest_index"))
    own = col(lambda m: _safe(m, "acs_housing_characteristics", "derived", "pct_owner_occupied"))
    p2i = col(lambda m: _safe(m, "housing_valuation", "price_to_income_ratio"))

    out = {}
    if hpi_yoy:
        out["median_home_price_yoy"] = round(median(hpi_yoy), 2)
    if per1k:
        out["median_permits_per_1k"] = round(median(per1k), 2)
    if afford:
        out["median_affordability_index"] = round(median(afford), 1)
    if own:
        out["median_pct_owner_occupied"] = round(median(own), 1)
    if p2i:
        out["median_price_to_income"] = round(median(p2i), 2)
    return out


def valuation_scatter(metros: List[dict]) -> List[dict]:
    out = []
    for m in metros:
        v = m.get("housing_valuation") or {}
        if v.get("price_to_income_ratio") is not None and v.get("price_to_rent_ratio") is not None:
            out.append({
                "metro": m.get("short_name"),
                "price_to_income": v["price_to_income_ratio"],
                "price_to_rent": v["price_to_rent_ratio"],
                "valuation_pct": v.get("latest_valuation_pct"),
            })
    return out


# --------------------------------------------------------------------------- #
# 2. Statewide GA HPI  (FRED — needs FRED_API_KEY)
# --------------------------------------------------------------------------- #
def fetch_ga_hpi(years_back: int = 12) -> Optional[dict]:
    """Georgia + US all-transactions HPI, quarterly, via FRED."""
    if _fred_observations is None:
        print("  [housing] pull_fhfa helpers unavailable; skipping ga_hpi", file=sys.stderr)
        return None
    start = f"{date.today().year - years_back}-01-01"
    ga = _fred_observations("GASTHPI", start)   # GA all-transactions HPI
    us = _fred_observations("USSTHPI", start)   # US all-transactions HPI
    if not ga:
        return None

    def _series(obs):
        qs, vals = [], []
        for o in obs or []:
            v = o.get("value")
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                vals.append(None)
            d = o.get("date", "")
            # FRED dates are quarter-start months 01/04/07/10 -> Qn label
            try:
                mo = int(d[5:7]); q = (mo - 1) // 3 + 1
                qs.append(f"{d[:4]}-Q{q}")
            except Exception:
                qs.append(d)
        return qs, vals

    qs, ga_vals = _series(ga)
    _, us_vals = _series(us)
    yoy = _yoy_pct_quarterly(ga_vals) if _yoy_pct_quarterly else []
    latest_value = next((v for v in reversed(ga_vals) if v is not None), None)
    latest_yoy = next((v for v in reversed(yoy) if v is not None), None)
    return {
        "series_id": "GASTHPI",
        "us_series_id": "USSTHPI",
        "index_type": "all-transactions",
        "quarters": qs,
        "values": ga_vals,
        "us_values": us_vals,
        "yoy_pct": yoy,
        "latest_quarter": qs[-1] if qs else None,
        "latest_value": latest_value,
        "latest_yoy": round(latest_yoy, 2) if latest_yoy is not None else None,
        "source": "FHFA House Price Index (all-transactions), GA + US, via FRED",
    }


# --------------------------------------------------------------------------- #
# 3. County ACS layer  (all 159 counties — needs CENSUS_API_KEY)
# --------------------------------------------------------------------------- #
ACS_VARS = {
    "B25077_001E": "median_home_value",
    "B25064_001E": "median_gross_rent",
    "B19013_001E": "median_household_income",
    "B25003_001E": "occupied_units",
    "B25003_002E": "owner_occupied",
    "B01003_001E": "population",
}


def _latest_acs_vintage() -> int:
    # 5-year vintage labels the END year; published ~Dec. Use last fully-released.
    y = date.today().year
    return y - 2 if date.today().month >= 1 else y - 3


def _census_get(url: str, retries: int = 3) -> Optional[list]:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            print(f"  [census HTTP {e.code}] attempt {attempt+1}", file=sys.stderr)
        except Exception as e:
            print(f"  [census err] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
        time.sleep(1.5 * (attempt + 1))
    return None


def fetch_county_acs(year: Optional[int] = None) -> Optional[dict]:
    """One ACS 5-year call for all 159 GA counties."""
    if not CENSUS_API_KEY:
        print("  [housing] no CENSUS_API_KEY; skipping county ACS layer", file=sys.stderr)
        return None
    y = year or _latest_acs_vintage()
    get_vars = ",".join(["NAME"] + list(ACS_VARS.keys()))
    url = (f"{CENSUS_BASE}/{y}/acs/acs5?get={get_vars}"
           f"&for=county:*&in=state:13&key={CENSUS_API_KEY}")
    rows = _census_get(url)
    if not rows or len(rows) < 2:
        return None
    header = rows[0]
    idx = {h: i for i, h in enumerate(header)}
    counties = {}
    for row in rows[1:]:
        st, co = row[idx["state"]], row[idx["county"]]
        fips = f"{st}{co}"
        rec = {}
        for var, label in ACS_VARS.items():
            try:
                val = float(row[idx[var]])
                rec[label] = val if val >= 0 else None  # ACS uses negatives as null flags
            except (TypeError, ValueError, KeyError):
                rec[label] = None
        oo, occ = rec.get("owner_occupied"), rec.get("occupied_units")
        rec["pct_owner_occupied"] = round(100 * oo / occ, 1) if oo and occ else None
        rec["name"] = GA_NAME.get(fips, row[idx["NAME"]].split(",")[0])
        counties[fips] = rec
    return {
        "year": y,
        "vintage_window": f"{y-4}-{y}",
        "variables": list(ACS_VARS.values()),
        "counties": counties,
        "source": f"Census ACS 5-year {y} (B25077 value, B25064 rent, B19013 income, B25003 tenure)",
    }


def non_metro_aggregate(county_acs: dict) -> Optional[dict]:
    """Population-weighted housing aggregate for the 86 non-metro GA counties."""
    if not county_acs:
        return None
    metro = _metro_ga_fips()
    counties = county_acs["counties"]
    nm = {f: r for f, r in counties.items() if f not in metro}
    if not nm:
        return None

    def wmedian_proxy(field):
        # population-weighted mean as a robust aggregate proxy for county medians
        num = den = 0.0
        for r in nm.values():
            v, pop = r.get(field), r.get("population")
            if v is not None and pop:
                num += v * pop
                den += pop
        return round(num / den, 0) if den else None

    pop_total = sum(r.get("population") or 0 for r in nm.values())
    oo = sum(r.get("owner_occupied") or 0 for r in nm.values())
    occ = sum(r.get("occupied_units") or 0 for r in nm.values())
    return {
        "county_count": len(nm),
        "population": pop_total,
        "median_home_value_wt": wmedian_proxy("median_home_value"),
        "median_gross_rent_wt": wmedian_proxy("median_gross_rent"),
        "median_household_income_wt": wmedian_proxy("median_household_income"),
        "pct_owner_occupied": round(100 * oo / occ, 1) if occ else None,
        "note": ("Population-weighted aggregate of the 86 GA counties outside the 14 MSAs. "
                 "Weighted means are used as a robust proxy for a regional median."),
        "source": county_acs["source"],
    }


# --------------------------------------------------------------------------- #
# 4. County permits  (best-effort, public BPS county file — no key)
# --------------------------------------------------------------------------- #
def fetch_county_permits(year: Optional[int] = None) -> Optional[dict]:
    """Per-county residential permits from the public Census BPS county annual file.

    Best-effort: the www2.census.gov host is not always reachable from every
    runner. On any failure this returns None and the page falls back to the
    statewide/metro permit views. Runs cleanly in GitHub Actions.
    """
    y = year or (date.today().year - 1)
    url = f"https://www2.census.gov/econ/bps/County/co{y}a.txt"
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            raw = r.read().decode("latin-1")
    except Exception as e:
        print(f"  [housing] county permits unavailable ({type(e).__name__}); "
              f"falling back to statewide/metro", file=sys.stderr)
        return None
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    # BPS county file: 2 header rows, then CSV. Columns 0=date,1=state FIPS,
    # 2=county FIPS, ... name ... then 1-unit Bldgs/Units/Value at fixed offsets.
    counties = {}
    for ln in lines[2:]:
        parts = [p.strip().strip('"') for p in ln.split(",")]
        if len(parts) < 12:
            continue
        try:
            st, co = parts[1].zfill(2), parts[2].zfill(3)
        except Exception:
            continue
        if st != "13":
            continue
        fips = f"{st}{co}"
        # 1-unit units is typically the 3rd numeric block; multi = sum of others.
        nums = []
        for p in parts[6:]:
            try:
                nums.append(int(float(p)))
            except ValueError:
                nums.append(None)
        sf = nums[1] if len(nums) > 1 and nums[1] is not None else None
        mf = None
        try:
            mf = sum(n for n in [nums[4], nums[7], nums[10]] if isinstance(n, int))
        except Exception:
            mf = None
        if sf is None and mf is None:
            continue
        counties[fips] = {"single_family": sf, "multi_family": mf}
    if not counties:
        return None
    return {
        "year": y,
        "counties": counties,
        "note": "Best-effort parse of the public Census BPS county annual file.",
        "source": f"Census Building Permits Survey, county annual file co{y}a.txt",
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
    out: Dict[str, Any] = {"fetched_at": _now_iso(), "schema": "housing/v1"}

    # 1) metro roll-up — always runs
    metros = rollup_metros()
    out["metros"] = metros
    out["statewide_medians"] = statewide_medians(metros)
    out["valuation_scatter"] = valuation_scatter(metros)
    meta["metro_rollup"] = {"last_updated": _now_iso(), "n_metros": len(metros)}

    def section(name, fn, *args):
        """Run a key/network-gated section with graceful degradation."""
        if rollup_only:
            if name in prior:
                out[name] = prior[name]
                meta[name] = prior_meta.get(name, {})
            return
        try:
            val = fn(*args)
        except Exception as e:
            print(f"  [housing] {name} raised {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            val = None
        if val:
            out[name] = val
            meta[name] = {"last_updated": _now_iso()}
        elif name in prior:           # preserve prior, keep its (older) timestamp
            out[name] = prior[name]
            meta[name] = prior_meta.get(name, {"last_updated": None})

    # 2-4) statewide + county layers
    section("ga_hpi", fetch_ga_hpi)
    section("county_acs", fetch_county_acs)
    section("county_permits", fetch_county_permits)

    # non-metro aggregate derives from whatever county_acs we ended up with
    ca = out.get("county_acs")
    if ca:
        try:
            nm = non_metro_aggregate(ca)
            if nm:
                out["non_metro"] = nm
                meta["non_metro"] = {"last_updated": _now_iso()}
        except Exception as e:
            print(f"  [housing] non_metro raised {type(e).__name__}", file=sys.stderr)
            if "non_metro" in prior:
                out["non_metro"] = prior["non_metro"]
                meta["non_metro"] = prior_meta.get("non_metro", {})

    # KPI strip
    sm = out["statewide_medians"]
    out["kpis"] = {
        "ga_home_price_yoy": _safe(out, "ga_hpi", "latest_yoy"),
        "ga_hpi_latest": _safe(out, "ga_hpi", "latest_value"),
        "median_affordability_index": sm.get("median_affordability_index"),
        "median_permits_per_1k": sm.get("median_permits_per_1k"),
        "median_pct_owner_occupied": sm.get("median_pct_owner_occupied"),
        "non_metro_county_count": _safe(out, "non_metro", "county_count"),
    }

    # staleness flags + labels
    for k, v in meta.items():
        v["stale"] = _is_stale(v)
    out["_meta"] = meta
    out["latest_label"] = (_safe(out, "ga_hpi", "latest_quarter")
                           or date.today().strftime("%Y-%m"))
    out["coverage_note"] = ("14 MSAs cover 73 of Georgia's 159 counties (~82% of "
                            "population). The county ACS layer and the Non-Metro Georgia "
                            "aggregate cover the remaining 86 counties.")
    out["source_summary"] = {
        "ga_hpi": "FHFA HPI via FRED (GASTHPI/USSTHPI)",
        "metros": "Roll-up of data/msa_reports/*.json housing sections",
        "county_acs": "Census ACS 5-year (all 159 counties)",
        "county_permits": "Census Building Permits Survey (county annual file)",
    }
    return out


def main(argv: List[str]) -> int:
    rollup_only = "--rollup" in argv
    out = build(rollup_only=rollup_only)
    OUT.write_text(json.dumps(out, indent=1))
    live = [k for k, v in out["_meta"].items() if not v.get("stale")]
    stale = [k for k, v in out["_meta"].items() if v.get("stale")]
    print(f"Wrote {OUT.relative_to(ROOT)} — {len(out['metros'])} metros; "
          f"live sections: {live}; stale/absent: {stale}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
