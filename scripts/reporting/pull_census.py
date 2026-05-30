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
import csv
import io
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Optional, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ga_msas import COUNTY_TO_MSA  # noqa: E402

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
    # Labor-force participation (population 16+), table B23025
    "B23025_001E": "lf_universe_16plus",
    "B23025_002E": "lf_in_labor_force",
    # Young-adult share (25-34), sex-by-age table B01001
    "B01001_011E": "age_m_25_29",
    "B01001_012E": "age_m_30_34",
    "B01001_035E": "age_f_25_29",
    "B01001_036E": "age_f_30_34",
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
        if values.get("lf_universe_16plus"):
            derived["labor_force_participation_pct"] = round(
                100 * (values.get("lf_in_labor_force") or 0) / values["lf_universe_16plus"], 2
            )
        if values.get("total_population"):
            young = sum((values.get(k) or 0) for k in [
                "age_m_25_29", "age_m_30_34", "age_f_25_29", "age_f_30_34"
            ])
            derived["young_adult_25_34_pct"] = round(100 * young / values["total_population"], 2)

        return {
            "source": f"Census ACS 5-year, {y} vintage (covers {y-4}-{y})",
            "year": y,
            "vintage_window": f"{y-4}-{y}",
            "values": values,
            "derived": derived,
        }

    return None


# ----------------------------- Population & Housing Characteristics -----------------------------
# Self-contained ACS pull (kept separate from the main demographics call to stay under the
# Census ~50-var/request cap) + Census Gazetteer land area for density.
HOUSING_CHAR_VARS = {
    "B01002_001E": "median_age",
    "B01003_001E": "population",
    "B25001_001E": "total_housing_units",
    "B25003_002E": "owner_occupied",
    "B25003_003E": "renter_occupied",
    "B25002_003E": "vacant_housing_units",
    "B25024_001E": "struct_total",
    "B25024_002E": "struct_1unit_detached",
    "B25024_004E": "struct_2",
    "B25024_005E": "struct_3_4",
    "B25024_006E": "struct_5_9",
    "B25024_007E": "struct_10_19",
    "B25024_008E": "struct_20_49",
    "B25024_009E": "struct_50plus",
    "B25035_001E": "median_year_built",
}
_MULTIFAMILY_KEYS = ["struct_2", "struct_3_4", "struct_5_9", "struct_10_19", "struct_20_49", "struct_50plus"]


def _gazetteer_land_area_sqmi(cbsa: str) -> Optional[float]:
    """Total land area (sq mi) for the MSA = sum of ALAND_SQMI over its counties, from the
    Census Gazetteer counties file. Land area is effectively static, so any recent vintage
    works; we probe the two most recent. Returns None on failure (sandbox proxy blocks
    www2.census.gov — validates only in a real Actions run)."""
    counties = {fips for fips, c in COUNTY_TO_MSA.items() if c == cbsa}
    if not counties:
        return None
    yr = date.today().year
    for gy in (yr - 1, yr - 2, yr - 3):
        url = (f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
               f"{gy}_Gazetteer/{gy}_Gaz_counties_national.txt")
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; EIG-MSA-reports/1.0)",
                "Accept": "text/plain,*/*",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("latin-1", errors="replace")
        except Exception:
            continue
        reader = csv.reader(io.StringIO(raw), delimiter="\t")
        try:
            header = [h.strip() for h in next(reader)]
            gi = header.index("GEOID")
            ai = header.index("ALAND_SQMI")
        except (StopIteration, ValueError):
            continue
        total, hits = 0.0, 0
        for row in reader:
            if len(row) <= max(gi, ai):
                continue
            if row[gi].strip() in counties:
                try:
                    total += float(row[ai].strip())
                    hits += 1
                except ValueError:
                    pass
        if hits:
            return round(total)
    return None


def fetch_housing_characteristics(cbsa: str, year: Optional[int] = None) -> Optional[dict]:
    """ACS 5-year housing-structure + age/tenure pull for the Population & Housing
    Characteristics table, plus Gazetteer land area and derived density.

    Returns {"year", "vintage_window", "values", "derived", "source"} or None.
    """
    if not CENSUS_API_KEY:
        print("  [Census ACS5 housing] no API key", file=sys.stderr)
        return None

    this_year = date.today().year
    candidate_years = [year] if year else list(range(this_year - 1, this_year - 5, -1))
    vars_str = ",".join(HOUSING_CHAR_VARS.keys())

    for y in candidate_years:
        url = (
            f"{CENSUS_BASE}/{y}/acs/acs5"
            f"?get={vars_str}"
            f"&for={urllib.parse.quote(_msa_predicate(cbsa), safe=':/+')}"
            f"&key={CENSUS_API_KEY}"
        )
        data = _census_get(url, quiet_404=(y == this_year - 1))
        if not data or len(data) < 2:
            continue

        header, row = data[0], data[1]
        values: Dict[str, Optional[float]] = {}
        for i, code in enumerate(header):
            if code not in HOUSING_CHAR_VARS:
                continue
            key = HOUSING_CHAR_VARS[code]
            raw = row[i]
            try:
                values[key] = float(raw) if raw not in (None, "-", "", "null") else None
            except (ValueError, TypeError):
                values[key] = None

        derived: Dict[str, Optional[float]] = {}
        tot_units = values.get("total_housing_units") or 0
        if tot_units:
            for dk, sk in [("pct_owner_occupied", "owner_occupied"),
                           ("pct_renter_occupied", "renter_occupied"),
                           ("pct_vacant", "vacant_housing_units")]:
                v = values.get(sk)
                if v is not None:
                    derived[dk] = round(100 * v / tot_units, 1)
        struct_tot = values.get("struct_total") or 0
        if struct_tot:
            d1 = values.get("struct_1unit_detached")
            if d1 is not None:
                derived["pct_1unit_detached"] = round(100 * d1 / struct_tot, 1)
            mf = sum((values.get(k) or 0) for k in _MULTIFAMILY_KEYS)
            derived["pct_multifamily"] = round(100 * mf / struct_tot, 1)

        land = _gazetteer_land_area_sqmi(cbsa)
        pop = values.get("population")
        if land:
            derived["land_area_sqmi"] = land
            if pop:
                derived["population_density"] = round(pop / land)

        return {
            "source": f"Census ACS 5-year, {y} vintage (B25024 structure, B25035 year built) "
                      f"+ Census Gazetteer land area",
            "year": y,
            "vintage_window": f"{y-4}-{y}",
            "values": values,
            "derived": derived,
        }

    return None


# ----------------------------- Entrepreneurship (Business Dynamics Statistics) -----------------------------
# MSA-level business formation is NOT in any Census API (the BFS API endpoint, eits/bfs, is
# US-only). Business Dynamics Statistics (timeseries/bds) IS available at MSA level and
# carries ESTABS_ENTRY_RATE = "rate of establishments born during the last 12 months" — a
# clean startup-rate measure. We index the MSA rate to the US rate (US=100). BDS lags ~2yr.
BDS_BASE = "https://api.census.gov/data/timeseries/bds"


def _bds_entry_rate(geo_for: str, y: int) -> Optional[float]:
    """ESTABS_ENTRY_RATE for a geography/year. Unspecified BDS dimensions (firm/estab age,
    size, sector) aggregate to their totals, so this returns the overall metro rate.
    Takes the first parseable positive value. Returns None if unavailable."""
    url = f"{BDS_BASE}?get=ESTABS_ENTRY_RATE&for={geo_for}&time={y}&key={CENSUS_API_KEY}"
    data = _census_get(url, quiet_404=True)
    if not data or len(data) < 2:
        return None
    try:
        idx = data[0].index("ESTABS_ENTRY_RATE")
    except ValueError:
        return None
    for row in data[1:]:
        try:
            v = float(row[idx])
        except (ValueError, TypeError, IndexError):
            continue
        if v > 0:
            return v
    return None


def fetch_entrepreneurship(cbsa: str, year: Optional[int] = None) -> Optional[dict]:
    """Broad-based startup rate: BDS establishment entry rate for the MSA vs the U.S.,
    indexed to US=100. Returns {"year","index_us_100","entry_rate_msa","entry_rate_us",
    "source"} or None. Pure stdlib.
    """
    if not CENSUS_API_KEY:
        print("  [BDS] no Census API key", file=sys.stderr)
        return None

    this_year = date.today().year
    # BDS is annual with a ~2-year lag; probe back from there.
    candidate_years = [year] if year else list(range(this_year - 2, this_year - 8, -1))
    msa_geo = urllib.parse.quote(_msa_predicate(cbsa), safe=':/+')

    for y in candidate_years:
        r_msa = _bds_entry_rate(msa_geo, y)
        r_us = _bds_entry_rate("us:1", y)
        if not r_msa or not r_us:
            continue
        return {
            "year": y,
            "index_us_100": round(100 * r_msa / r_us),
            "entry_rate_msa": round(r_msa, 2),
            "entry_rate_us": round(r_us, 2),
            "source": (f"Census Business Dynamics Statistics (establishment entry rate, {y}); "
                       f"metro rate indexed to US=100"),
        }
    return None


# ----------------------------- Block groups by income (B19013) -----------------------------
# Distribution of the MSA's census block groups by median household income. Block-group
# geographies can't be wildcarded across states, so we query per county (state+county).
_BG_INCOME_BINS = [
    (0, 25000, "0-25k"), (25000, 50000, "25-50k"), (50000, 75000, "50-75k"),
    (75000, 100000, "75-100k"), (100000, 125000, "100-125k"), (125000, 150000, "125-150k"),
    (150000, 200000, "150-200k"), (200000, 10**12, "200k+"),
]


def fetch_block_groups_by_income(cbsa: str, year: Optional[int] = None) -> Optional[dict]:
    """Share of the MSA's block groups falling in each median-HH-income band (B19013).

    Returns {"year","vintage_window","bins":[...],"pct":[...],"n_block_groups":N,"source"}.
    The US comparison series is left illustrative on the page (computing it would require
    pulling all ~240k US block groups). Suppressed median income (-666666666) is skipped.
    Returns None if no county resolves. Pure stdlib.
    """
    if not CENSUS_API_KEY:
        print("  [Census ACS5 block-groups] no API key", file=sys.stderr)
        return None
    counties = {fips for fips, c in COUNTY_TO_MSA.items() if c == cbsa}
    if not counties:
        return None

    this_year = date.today().year
    candidate_years = [year] if year else list(range(this_year - 1, this_year - 5, -1))
    labels = [b[2] for b in _BG_INCOME_BINS]

    for y in candidate_years:
        counts = [0] * len(_BG_INCOME_BINS)
        total = 0
        any_county = False
        for fips in sorted(counties):
            st, co = fips[:2], fips[2:]
            url = (
                f"{CENSUS_BASE}/{y}/acs/acs5?get=B19013_001E"
                f"&for=block%20group:*&in=state:{st}%20county:{co}"
                f"&key={CENSUS_API_KEY}"
            )
            data = _census_get(url, quiet_404=(y == this_year - 1))
            if not data or len(data) < 2:
                continue
            any_county = True
            try:
                idx = data[0].index("B19013_001E")
            except ValueError:
                continue
            for row in data[1:]:
                try:
                    inc = float(row[idx])
                except (ValueError, TypeError, IndexError):
                    continue
                if inc < 0:  # -666666666 = suppressed / not computable
                    continue
                for bi, (lo, hi, _) in enumerate(_BG_INCOME_BINS):
                    if lo <= inc < hi:
                        counts[bi] += 1
                        total += 1
                        break
        if any_county and total > 0:
            return {
                "year": y,
                "vintage_window": f"{y-4}-{y}",
                "bins": labels,
                "pct": [round(100 * c / total, 1) for c in counts],
                "n_block_groups": total,
                "source": f"Census ACS 5-year {y} block-group median HH income (B19013), MSA counties",
            }
    return None


# ----------------------------- ACS age structure (B01001) -----------------------------
# B01001 is sex-by-age. Male age groups are suffixes 3..25; the female counterpart of
# male suffix s is s+24 (e.g. male 25-29 = _011E, female 25-29 = _035E). Requesting all
# of these (47 vars incl. the _001E total) is its own call to stay under the Census API's
# ~50-variable-per-request limit (the main demographics call is already near it).
_B01001_MALE_SUFFIXES = list(range(3, 26))  # 3..25 inclusive

# 16 display bins -> the MALE suffixes composing each (the female counterpart s+24 is
# summed automatically). Census splits some 5-yr spans into finer groups, so a few bins
# combine multiple suffixes (e.g. 15-19 = 15-17 + 18-19).
_AGE_BINS = [
    ("0-4", [3]), ("5-9", [4]), ("10-14", [5]), ("15-19", [6, 7]),
    ("20-24", [8, 9, 10]), ("25-29", [11]), ("30-34", [12]), ("35-39", [13]),
    ("40-44", [14]), ("45-49", [15]), ("50-54", [16]), ("55-59", [17]),
    ("60-64", [18, 19]), ("65-69", [20, 21]), ("70-74", [22]), (">74", [23, 24, 25]),
]
# Generation groups, approximate — grouped by 15-yr age bands as of the ACS vintage year
# (generation boundaries are fuzzy at 5-yr-bin granularity; this is labelled approximate).
_GENERATIONS = [
    ("Alpha",       [3, 4, 5]),
    ("Gen Z",       [6, 7, 8, 9, 10, 11]),
    ("Millennial",  [12, 13, 14]),
    ("Gen X",       [15, 16, 17]),
    ("Boomer",      [18, 19, 20, 21, 22]),
    ("Silent/Gtst", [23, 24, 25]),
]


def _b01001_var_codes() -> List[str]:
    codes = ["B01001_001E"]
    for s in _B01001_MALE_SUFFIXES:
        codes.append(f"B01001_{s:03d}E")       # male
        codes.append(f"B01001_{s + 24:03d}E")  # female counterpart
    return codes


def fetch_acs_age_structure(cbsa: str, year: Optional[int] = None) -> Optional[dict]:
    """ACS 5-year sex-by-age (B01001) -> 16 five-year bins (% of pop) + generation groups.

    Single most-recent vintage (a current snapshot, like the demographics pull).
    Returns None if no vintage resolves. Powers the Population-by-Age and Generational
    Breakdown charts.
    """
    if not CENSUS_API_KEY:
        print("  [Census ACS5 age] no API key", file=sys.stderr)
        return None
    this_year = date.today().year
    candidate_years = [year] if year else list(range(this_year - 1, this_year - 5, -1))
    vars_str = ",".join(_b01001_var_codes())

    for y in candidate_years:
        url = (
            f"{CENSUS_BASE}/{y}/acs/acs5?get={vars_str}"
            f"&for={urllib.parse.quote(_msa_predicate(cbsa), safe=':/+')}"
            f"&key={CENSUS_API_KEY}"
        )
        data = _census_get(url, quiet_404=(y == this_year - 1))
        if not data or len(data) < 2:
            continue
        header, row = data[0], data[1]
        vals: Dict[str, float] = {}
        for i, code in enumerate(header):
            if code.startswith("B01001_"):
                try:
                    vals[code] = float(row[i]) if row[i] not in (None, "", "-", "null") else 0.0
                except (ValueError, TypeError):
                    vals[code] = 0.0
        total = vals.get("B01001_001E") or 0
        if not total:
            continue

        def grp_pct(suffixes):
            s = sum((vals.get(f"B01001_{m:03d}E") or 0) + (vals.get(f"B01001_{m + 24:03d}E") or 0)
                    for m in suffixes)
            return round(100 * s / total, 2)

        return {
            "source": f"Census ACS 5-year B01001, {y} vintage (covers {y-4}-{y})",
            "year": y,
            "total_population": int(total),
            "age_bins": {lbl: grp_pct(sfx) for lbl, sfx in _AGE_BINS},
            "bin_order": [lbl for lbl, _ in _AGE_BINS],
            "generations": {lbl: grp_pct(sfx) for lbl, sfx in _GENERATIONS},
            "generation_order": [lbl for lbl, _ in _GENERATIONS],
        }
    return None


# ----------------------------- ACS rental affordability history -----------------------------

def _acs_rent_income(geo_predicate: str, y: int) -> Optional[tuple]:
    """(median_gross_rent, median_hh_income) for a geo+vintage, or None."""
    url = (
        f"{CENSUS_BASE}/{y}/acs/acs5?get=B25064_001E,B19013_001E"
        f"&for={urllib.parse.quote(geo_predicate, safe=':/+')}"
        f"&key={CENSUS_API_KEY}"
    )
    data = _census_get(url, quiet_404=True)
    if not data or len(data) < 2:
        return None
    try:
        rent, inc = float(data[1][0]), float(data[1][1])
        return (rent, inc) if (rent > 0 and inc > 0) else None
    except (ValueError, TypeError, IndexError):
        return None


def fetch_acs_median_home_value(cbsa: str, year: int) -> Optional[float]:
    """ACS B25077 median value of owner-occupied housing units ($) for one vintage.
    Used as the dollar price anchor for the Housing Affordability index."""
    pred = _msa_predicate(cbsa)
    url = (
        f"{CENSUS_BASE}/{year}/acs/acs5?get=B25077_001E"
        f"&for={urllib.parse.quote(pred, safe=':/+')}"
        f"&key={CENSUS_API_KEY}"
    )
    data = _census_get(url, quiet_404=True)
    if not data or len(data) < 2:
        return None
    try:
        v = float(data[1][0])
        return v if v > 0 else None
    except (ValueError, TypeError, IndexError):
        return None


def fetch_acs_affordability_history(cbsa: str, years_back: int = 6) -> Optional[dict]:
    """Rental affordability indexed to US = 100 across recent ACS 5-year vintages.

    Burden = annual gross rent / median household income. The index is
    (US burden / MSA burden) * 100, so a value > 100 means the MSA spends a
    *smaller* share of income on rent than the US (i.e. more affordable).
    """
    if not CENSUS_API_KEY:
        print("  [Census ACS5 afford] no API key", file=sys.stderr)
        return None
    this_year = date.today().year
    years: List[int] = []
    idx_vals: List[float] = []
    msa_burden: List[float] = []
    us_burden: List[float] = []
    msa_income: List[int] = []
    for y in range(this_year - years_back, this_year):
        msa = _acs_rent_income(_msa_predicate(cbsa), y)
        us = _acs_rent_income("us:1", y)
        if not (msa and us):
            continue
        mb = (msa[0] * 12) / msa[1]
        ub = (us[0] * 12) / us[1]
        if mb <= 0:
            continue
        years.append(y)
        msa_burden.append(round(100 * mb, 1))
        us_burden.append(round(100 * ub, 1))
        idx_vals.append(round(100 * ub / mb, 1))
        msa_income.append(round(msa[1]))  # median HH income, reused by housing_affordability
    if not years:
        return None
    return {
        "source": "Census ACS 5-year B25064 (median gross rent) / B19013 (median HH income), MSA vs US",
        "years": years,
        "affordability_index": idx_vals,
        "msa_rent_burden_pct": msa_burden,
        "us_rent_burden_pct": us_burden,
        "msa_median_income": msa_income,
        "latest_index": idx_vals[-1],
    }


# ----------------------------- MSA net migration (PEP components CSV) -----------------------------
# Census PEP publishes county-level components-of-change as keyless CSVs. We sum the
# MSA's member counties to get domestic + international + net migration per year. Same
# file/parse pattern as scripts/fetch_population.py's county pull.
_PEP_COUNTY_VINTAGES = [2025, 2024]


def _safe_int(x) -> int:
    try:
        return int(float(x))
    except (ValueError, TypeError):
        return 0


def _download_text(url: str, timeout: int = 60) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EIG-MSA-reports/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("latin-1", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  [PEP-CC HTTP {e.code}] {url[:120]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [PEP-CC err] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
        return None


# Module-level cache: the national PEP components CSV is the SAME file for every
# MSA, so on an --all run we download + parse each vintage at most once and slice
# all 14 metros from it. Without this, a multi-MSA run re-downloaded a ~3,200-row
# national CSV per metro (or, when Savannah-gated, failed slowly on the other 13).
_PEP_CC_CACHE: Dict[int, Optional[dict]] = {}


def _load_pep_components(v: int) -> Optional[dict]:
    """Download + parse the national PEP county components-of-change CSV for vintage
    `v`, once per process. Returns {"years": [...], "by_fips": {fips5: {year: (dom,
    intl, net)}}} or None. Cached (incl. None) so an --all run hits the network once
    per vintage."""
    if v in _PEP_CC_CACHE:
        return _PEP_CC_CACHE[v]
    urls = [
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{v}/counties/totals/co-est{v}-alldata.csv",
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{v}/counties/totals/CO-EST{v}-ALLDATA.csv",
    ]
    parsed = None
    for url in urls:
        text = _download_text(url)
        if not text:
            continue
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            continue
        years = list(range(2021, v + 1))  # base year 2020 has no flow estimate
        by_fips: Dict[str, dict] = {}
        for r in rows:
            st = (r.get("STATE") or "").strip().zfill(2)
            co = (r.get("COUNTY") or "").strip().zfill(3)
            if co == "000":  # state-total row, not a county
                continue
            by_fips[st + co] = {
                y: (_safe_int(r.get(f"DOMESTICMIG{y}")),
                    _safe_int(r.get(f"INTERNATIONALMIG{y}")),
                    _safe_int(r.get(f"NETMIG{y}")))
                for y in years
            }
        if by_fips:
            parsed = {"years": years, "by_fips": by_fips}
            break
    _PEP_CC_CACHE[v] = parsed
    return parsed


def fetch_msa_net_migration(cbsa: str) -> Optional[dict]:
    """Annual domestic / international / net migration for the MSA, summed across its
    member counties from the Census PEP county components-of-change CSV (keyless).

    Works for any GA MSA via COUNTY_TO_MSA, including the cross-state counties of
    Augusta (GA-SC) and Columbus (GA-AL) since the match is on full 5-digit FIPS.
    The national CSV is downloaded once per run and cached, so an --all pass slices
    all 14 metros from a single fetch. Fail-soft: returns None on any miss.
    """
    counties = {fips for fips, c in COUNTY_TO_MSA.items() if c == cbsa}
    if not counties:
        return None
    for v in _PEP_COUNTY_VINTAGES:
        data = _load_pep_components(v)
        if not data:
            continue
        years = data["years"]
        by_fips = data["by_fips"]
        dom = {y: 0 for y in years}
        intl = {y: 0 for y in years}
        net = {y: 0 for y in years}
        matched = []
        for fips in counties:
            rec = by_fips.get(fips)
            if not rec:
                continue
            matched.append(fips)
            for y in years:
                d, i, n = rec[y]
                dom[y] += d
                intl[y] += i
                net[y] += n
        if not matched:
            print(f"  [PEP-CC {v}] no counties matched for CBSA {cbsa}", file=sys.stderr)
            continue
        return {
            "source": f"Census PEP components of change (CO-EST{v}), MSA counties",
            "years": years,
            "domestic_migration": [dom[y] for y in years],
            "international_migration": [intl[y] for y in years],
            "net_migration": [net[y] for y in years],
            "counties": sorted(matched),
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
