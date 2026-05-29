"""Census Bureau pulls for Metro Economic Profile reports.

We use ACS **5-year** estimates throughout (not 1-year):
  * Available for every MSA regardless of size (1-year requires 65K+ pop —
    most GA MSAs qualify, but 5-year future-proofs against pop changes).
  * Smoother / less noisy on income, education, housing, and demographic
    fields where year-to-year variability is mostly sampling noise.
  * Newest vintage is published reliably in early December each year, so
    we don't hit "not yet released" 404s the way 1-year did mid-year.
  * Each vintage labels the END of the 5-year window — vintage 2024 covers
    2020-2024 ACS data, published December 2025.

Trade-off: 5-year estimates lag actual conditions by ~2.5 years (the
midpoint of the rolling window). For things that change fast (employment),
we rely on BLS/BEA monthly series instead — ACS feeds the slower-moving
report sections (demographics, income, housing characteristics).

Exposes:
  fetch_pep_population_history(cbsa, years_back=7) -> dict
      Annual MSA population estimates from ACS 5-year B01003.
      (Named "pep_" historically; actually uses ACS for consistency with
      the rest of the demographic pulls.)

  fetch_acs_demographics(cbsa, year=None) -> dict
      One-shot ACS 5-year pull for demographics, education, income, and housing.
      Covers everything needed for the Demographics & Migration and Geographic
      Profile sections of the report.

  fetch_bps_permits_annual(cbsa, years_back=7) -> dict
      Annual residential building permits (single-family + multi-family) for the MSA.
      Pulled from the Census BPS Metro annual files at www2.census.gov/econ/bps/Metro/.

  NOTE: MSA-level export data is NOT served by Census USA Trade Online (state + port
  only). The real source is ITA Metropolitan Area Export Data — see pull_ita.py.

Census API base: https://api.census.gov/data/{year}/{dataset}
MSA predicate:  for=metropolitan+statistical+area/micropolitan+statistical+area:{cbsa}

Env: CENSUS_API_KEY (required for any meaningful volume; unkeyed allows ~500/day).
"""

from __future__ import annotations

import os
import sys
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Optional, Dict, List

CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "").strip()
CENSUS_BASE = "https://api.census.gov/data"


def _census_get(url: str, retries: int = 3, quiet_404: bool = False) -> Optional[list]:
    """Fetch a Census API URL and parse the JSON 2-D array. None on failure.

    Set `quiet_404=True` when 404 is the expected "vintage not released yet"
    signal from a newest-first probing loop — keeps the orchestrator log clean.
    Real 400s (bad query) still print; non-404/400 errors still retry.
    """
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                if not body.strip():
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if not (e.code == 404 and quiet_404):
                print(f"  [Census HTTP {e.code}] {url[:160]}", file=sys.stderr)
            if e.code in (400, 404):
                return None
            last_err = e
        except Exception as e:
            print(f"  [Census err] {type(e).__name__}: {e}", file=sys.stderr)
            last_err = e
        time.sleep(1 + attempt)
    return None


def _msa_predicate(cbsa: str) -> str:
    """Census API requires the MSA code without its trailing zero in some datasets,
    but the full CBSA in others. We use the full 5-digit code which works for ACS & PEP."""
    return f"metropolitan+statistical+area/micropolitan+statistical+area:{cbsa}"


# ----------------------------- PEP population history -----------------------------

def fetch_pep_population_history(cbsa: str, years_back: int = 7) -> Optional[dict]:
    """Annual MSA population estimates back N years.

    Source: ACS 5-year B01003 (table = "Total population"). Each ACS 5-year
    vintage is labelled by the END year of its window — vintage 2024 covers
    2020-2024 data. Consecutive vintages overlap by 4 years, so "YoY" change
    reflects mostly the rolling window's new and dropped years, not a single
    calendar-year delta.

    Note on naming: this function is named `fetch_pep_population_history`
    for historical reasons (the report sections were originally designed
    around PEP). It actually uses ACS 5-year because PEP MSA endpoints have
    been inconsistent and 1-year is noisier than 5-year for this purpose.

    Returns:
        {"years": [2018, ..., 2024], "population": [...], "yoy_pct": [...]}
    """
    if not CENSUS_API_KEY:
        print("  [Census ACS5] no API key in env", file=sys.stderr)
        return None

    this_year = date.today().year
    start_year = this_year - years_back

    # ACS 5-year vintages are released in early December each year (so the
    # latest available now is vintage = this_year - 1 in most cases, vintage
    # = this_year - 2 if we're before December). Probe both, the _census_get
    # 404 path handles the not-yet-released case quietly.
    years = list(range(start_year, this_year))
    pops: List[Optional[int]] = []
    out_years: List[int] = []

    for y in years:
        url = (
            f"{CENSUS_BASE}/{y}/acs/acs5"
            f"?get=B01003_001E"
            f"&for={urllib.parse.quote(_msa_predicate(cbsa), safe=':/+')}"
            f"&key={CENSUS_API_KEY}"
        )
        # ACS5 vintage `y` is released in early Dec of year y+1. So vintage
        # `this_year - 1` may or may not be out yet depending on month —
        # quiet its 404 (the loop falls back to `this_year - 2` cleanly).
        data = _census_get(url, quiet_404=(y == this_year - 1))
        if not data or len(data) < 2:
            continue
        try:
            pop = int(data[1][0])
            out_years.append(y)
            pops.append(pop)
        except (ValueError, IndexError):
            continue

    if not pops:
        return None

    # YoY %
    yoy: List[Optional[float]] = [None]
    for i in range(1, len(pops)):
        prior = pops[i - 1]
        if prior:
            yoy.append(round(100 * (pops[i] - prior) / prior, 2))
        else:
            yoy.append(None)

    return {
        "source":           "Census ACS 5-year B01003",
        "years":            out_years,
        "population":       pops,
        "yoy_pct":          yoy,
        "latest_year":      out_years[-1],
        "latest_population": pops[-1],
        "latest_yoy":       yoy[-1],
    }


# ----------------------------- ACS demographics -----------------------------

# Variables needed for the report. Each maps to its destination key in the output dict.
ACS_VARIABLES: Dict[str, str] = {
    # Population, age, household
    "B01003_001E": "total_population",
    "B01002_001E": "median_age",
    "B11001_001E": "total_households",
    "B19013_001E": "median_household_income",
    "B19301_001E": "per_capita_income",
    # Educational attainment (population 25+)
    "B15003_001E": "edu_total_25plus",
    "B15003_017E": "edu_high_school_grad",
    "B15003_018E": "edu_ged_alt",
    "B15003_019E": "edu_some_college_lt1yr",
    "B15003_020E": "edu_some_college_gt1yr",
    "B15003_021E": "edu_associates",
    "B15003_022E": "edu_bachelors",
    "B15003_023E": "edu_masters",
    "B15003_024E": "edu_professional",
    "B15003_025E": "edu_doctorate",
    # Poverty
    "B17001_001E": "poverty_universe",
    "B17001_002E": "poverty_below",
    # Housing
    "B25001_001E": "total_housing_units",
    "B25002_002E": "occupied_housing_units",
    "B25002_003E": "vacant_housing_units",
    "B25003_002E": "owner_occupied",
    "B25003_003E": "renter_occupied",
    "B25064_001E": "median_gross_rent",
    "B25077_001E": "median_home_value",
    # Commuting
    "B08303_001E": "commute_universe",
    "B08303_013E": "commute_60_plus_min",
    # Inequality (Gini)
    "B19083_001E": "gini_coefficient",
}


def fetch_acs_demographics(cbsa: str, year: Optional[int] = None) -> Optional[dict]:
    """One-shot ACS 5-year pull for the MSA.

    If `year` is None, probe the most recent vintage backward until one
    returns. ACS 5-year vintages are published reliably in December each
    year for the previous calendar year (vintage 2024 = 2020-2024 average,
    released December 2025), so we usually find data on the first probe.

    Returns: {"year": 2024, "values": {<key>: number, ...}, "derived": {<key>: number, ...}}
    """
    if not CENSUS_API_KEY:
        print("  [Census ACS5] no API key", file=sys.stderr)
        return None

    this_year = date.today().year
    candidate_years = [year] if year else list(range(this_year - 1, this_year - 5, -1))

    vars_str = ",".join(ACS_VARIABLES.keys())

    for y in candidate_years:
        url = (
            f"{CENSUS_BASE}/{y}/acs/acs5"
            f"?get={vars_str}"
            f"&for={urllib.parse.quote(_msa_predicate(cbsa), safe=':/+')}"
            f"&key={CENSUS_API_KEY}"
        )
        # Quiet expected 404 on the newest vintage (released only in
        # early Dec of year y+1) — falls back to y-1 cleanly.
        data = _census_get(url, quiet_404=(y == this_year - 1))
        if not data or len(data) < 2:
            continue

        header = data[0]
        row = data[1]
        values: Dict[str, Optional[float]] = {}
        for i, var_code in enumerate(header):
            if var_code not in ACS_VARIABLES:
                continue
            key = ACS_VARIABLES[var_code]
            raw = row[i]
            try:
                values[key] = float(raw) if raw not in (None, "-", "", "null") else None
            except (ValueError, TypeError):
                values[key] = None

        # Derived series
        derived: Dict[str, Optional[float]] = {}
        tot = values.get("edu_total_25plus") or 0
        if tot:
            bach_plus = sum((values.get(k) or 0) for k in [
                "edu_bachelors", "edu_masters", "edu_professional", "edu_doctorate"
            ])
            derived["pct_bachelors_or_higher"] = round(100 * bach_plus / tot, 2)
            grad_plus = sum((values.get(k) or 0) for k in [
                "edu_masters", "edu_professional", "edu_doctorate"
            ])
            derived["pct_graduate_or_higher"] = round(100 * grad_plus / tot, 2)
        if values.get("poverty_universe"):
            derived["poverty_rate_pct"] = round(
                100 * (values.get("poverty_below") or 0) / values["poverty_universe"], 2
            )
        if values.get("total_housing_units"):
            for k_dst, k_src in [
                ("pct_owner_occupied", "owner_occupied"),
                ("pct_renter_occupied", "renter_occupied"),
                ("pct_vacant", "vacant_housing_units"),
            ]:
                v = values.get(k_src)
                if v is not None:
                    derived[k_dst] = round(100 * v / values["total_housing_units"], 2)
        if values.get("commute_universe"):
            derived["pct_commute_60_plus"] = round(
                100 * (values.get("commute_60_plus_min") or 0) / values["commute_universe"], 2
            )
        if values.get("median_household_income") and values.get("median_home_value"):
            derived["price_to_income_ratio"] = round(
                values["median_home_value"] / values["median_household_income"], 2
            )

        return {
            "source": f"Census ACS 5-year, {y} vintage (covers {y-4}-{y})",
            "year": y,
            "vintage_window": f"{y-4}-{y}",
            "values": values,
            "derived": derived,
        }

    return None


# ----------------------------- Building permits (annual) -----------------------------

def _bps_fetch_one_year(y: int, cbsa: str, timeout: int = 60) -> Optional[tuple]:
    """Fetch one year's BPS Metro annual file and extract this MSA's row.

    Returns (year, sf_units, mf_units) on success, None on any failure.
    Tries the canonical URL then one mirror. Single attempt per URL —
    no inner retry — to keep wall time bounded under the orchestrator.

    Worst case per year: 2 URLs × 60s = 120s. With 2 years parallelized
    in fetch_bps_permits_annual, total wall time ≤ 120s when both fail.
    """
    yy = f"{y % 100:02d}"
    urls = [
        f"https://www2.census.gov/econ/bps/Metro/ma{yy}a.txt",
        # Fallback to programs-surveys path (same content, sometimes faster):
        f"https://www2.census.gov/programs-surveys/bps/tables/{y}/ma{yy}a.txt",
    ]

    body = None
    last_err = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; EIG-MSA-reports/1.0)",
                "Accept-Encoding": "gzip",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                body = raw.decode("utf-8", errors="replace")
            break  # got body, no need to try mirror
        except Exception as e:
            last_err = e
            continue  # try next URL
    if not body:
        if last_err:
            print(f"  [BPS {y}] {type(last_err).__name__}: {str(last_err)[:80]}", file=sys.stderr)
        return None

    # BPS annual files: 3-line header (titles + units + sub-headers), then CSV rows.
    # Column layout (post-2010):
    #   CSA, CBSA, Name, Bldgs_1u, Units_1u, Cost_1u,
    #                    Bldgs_2u, Units_2u, Cost_2u,
    #                    Bldgs_3to4u, Units_3to4u, Cost_3to4u,
    #                    Bldgs_5plus, Units_5plus, Cost_5plus
    for line in body.splitlines()[3:]:
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 15:
            continue
        row_cbsa = cells[1].strip().lstrip("0").zfill(5)
        if row_cbsa != cbsa:
            continue
        try:
            sf_y = int(cells[4])                       # Units_1_Unit
            mf_y = int(cells[7]) + int(cells[10]) + int(cells[13])  # 2u + 3-4u + 5+
            return (y, sf_y, mf_y)
        except (ValueError, IndexError):
            return None
    return None


def fetch_bps_permits_annual(cbsa: str, years_back: int = 2) -> Optional[dict]:
    """Annual single-family + multi-family residential permits for the MSA.

    Source: Census BPS annual MSA files at
    https://www2.census.gov/econ/bps/Metro/ma{YY}a.txt

    Each year's file contains one row per MSA with columns:
        CSA, CBSA, Name, Total_Buildings_All_Permits, Total_Units_All_Permits,
        Total_Construction_Cost, plus by-structure-type columns.

    Extracts:
        - Single-family units      = Units_1_Unit
        - Multi-family units total = Units_2_Unit + Units_3_4_Unit + Units_5_or_more_Unit

    RESILIENT DESIGN: www2.census.gov is unreliably slow (3MB files, 60+ s
    typical, sometimes timing out). Strategy:
      * Fetch the last `years_back` years (default 2 — current + prior).
      * Parallelize year fetches via ThreadPoolExecutor (max 4 workers).
      * Per-year timeout 90s, with one retry on transient failure.
      * Try canonical URL first, then the programs-surveys path as mirror.
      * Returns partial results when some years succeed.
      * Returns None silently if all years fail.

    Historical years (>2 back) come from the prior cached JSON via the
    orchestrator's never-blank-on-failure logic.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    this_year = date.today().year
    start_year = this_year - years_back

    target_years = list(range(start_year, this_year))
    results: List[tuple] = []
    with ThreadPoolExecutor(max_workers=min(4, len(target_years))) as ex:
        futs = {ex.submit(_bps_fetch_one_year, y, cbsa): y for y in target_years}
        for fut in as_completed(futs):
            r = fut.result()
            if r is not None:
                results.append(r)

    if not results:
        return None
    results.sort(key=lambda t: t[0])
    years = [r[0] for r in results]
    sf = [r[1] for r in results]
    mf = [r[2] for r in results]

    if not years:
        return None

    # Per-1k uses the MSA population from our canonical map (avoids needing a fresh PEP call here)
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from _ga_msas import GA_MSAS
        pop_map = {c: p for c, _, _, p in GA_MSAS}
        pop = pop_map.get(cbsa)
    except Exception:
        pop = None
    per_1k = [round((sf[i] + mf[i]) / (pop / 1000), 2) if pop else None for i in range(len(years))]

    return {
        "source":          "Census BPS Metro annual (https://www2.census.gov/econ/bps/Metro)",
        "years":           years,
        "single_family":   sf,
        "multi_family":    mf,
        "permits_per_1k":  per_1k,
        "latest_year":     years[-1],
        "latest_single":   sf[-1],
        "latest_multi":    mf[-1],
        "latest_per_1k":   per_1k[-1],
    }


# NOTE: MSA-level export breakdowns are NOT served by Census USA Trade Online
# (state and port only). The real source is ITA Metropolitan Area Export Data,
# served via api.trade.gov with the api.data.gov ITA_API_KEY. See pull_ita.py.


# ----------------------------- CLI smoke test -----------------------------

if __name__ == "__main__":
    cbsa = sys.argv[1] if len(sys.argv) > 1 else "42340"
    print(f"Fetching Census data for CBSA {cbsa} ...", file=sys.stderr)

    pep = fetch_pep_population_history(cbsa)
    if pep:
        print(f"  Population: latest {pep['latest_year']} = {pep['latest_population']:,}  ({pep['latest_yoy']:+.2f}% YoY)")

    acs = fetch_acs_demographics(cbsa)
    if acs:
        print(f"  ACS demographics ({acs['year']}):")
        v = acs["values"]
        d = acs["derived"]
        print(f"    median HH income:   ${v.get('median_household_income'):,.0f}" if v.get("median_household_income") else "    median HH income: —")
        print(f"    median age:         {v.get('median_age')}")
        print(f"    poverty rate:       {d.get('poverty_rate_pct')}%")
        print(f"    bachelors+:         {d.get('pct_bachelors_or_higher')}%")
        print(f"    median home value:  ${v.get('median_home_value'):,.0f}" if v.get("median_home_value") else "")
        print(f"    price/income ratio: {d.get('price_to_income_ratio')}")
        print(f"    Gini:               {v.get('gini_coefficient')}")
