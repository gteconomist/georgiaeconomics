"""Pull GA population & migration data from U.S. Census Bureau public CSVs.

Output: data/population.json

Data sources by section:
  • state trend (2020-2025)            — Census PEP NST-EST{VINTAGE}-ALLDATA.csv (V2025 then V2024 fallback)
  • components of change (state)       — Same NST-EST file. BIRTHS / DEATHS / NATURALCHG /
                                         INTERNATIONALMIG / DOMESTICMIG / NETMIG per year.
  • county totals + growth (159)       — Census PEP co-est{VINTAGE}-alldata.csv (V2025 then V2024 fallback)
  • peer-state comparison (GA + 6)     — Same NST-EST file, filtered to GA/FL/NC/SC/TN/TX/AL.
  • age structure                      — Census PEP sc-est{VINTAGE}-agesex-civ.csv (state by single year of age)
  • race / ethnicity composition       — Census PEP sc-est{VINTAGE}-alldata6.csv (state by race + Hispanic origin)

Graceful degradation:
  Each section is wrapped in try/except. On failure the prior section value
  is preserved AND `_meta.<section>.last_updated` is NOT bumped. The page
  renders a "stale" badge for any section > 6 months out of date.

Environment:
  No API key required — all Census PEP datasets are public CSV downloads.
"""

import csv
import io
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date

# ---------------------------------------------------------------------------
# Env / constants
# ---------------------------------------------------------------------------
TODAY = date.today()
TODAY_ISO = TODAY.isoformat()

# Census PEP file vintages we'll try, newest first. The fetcher falls back to
# the older vintage if the newer one 404s (e.g., V2025 county data hadn't
# released yet at the time of authoring).
STATE_VINTAGES   = [2025, 2024]
COUNTY_VINTAGES  = [2025, 2024]
CHAR_VINTAGES    = [2024]  # asrh / characteristics — typically 12+ mo lag

OUT_PATH = Path(__file__).parent.parent / "data" / "population.json"

# GA + peers we want on the comparison chart
PEER_STATES = {
    "13": ("GA", "Georgia"),
    "12": ("FL", "Florida"),
    "37": ("NC", "North Carolina"),
    "45": ("SC", "South Carolina"),
    "47": ("TN", "Tennessee"),
    "48": ("TX", "Texas"),
    "01": ("AL", "Alabama"),
}
GA_STATE_FIPS = "13"


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
def http_get(url, timeout=60, retries=3, headers=None):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {
                "User-Agent": "georgiaeconomics.com fetch_population.py"
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # 404 is informative — caller decides whether to try a fallback URL.
                raise
            last_err = e
            time.sleep(1 + attempt)
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"GET {url} failed after {retries} retries: {last_err}")


def fetch_first_available(url_candidates, label="dataset"):
    """Try each URL in order. Return (body_bytes, url_used). Skip 404s, raise on
    network errors after exhausting the list."""
    last_err = None
    for url in url_candidates:
        try:
            data = http_get(url)
            print(f"      [{label}] OK ← {url}")
            return data, url
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"      [{label}] 404 ← {url} (trying next)", file=sys.stderr)
                last_err = e
                continue
            raise
        except RuntimeError as e:
            last_err = e
            print(f"      [{label}] network error ← {url}: {e}", file=sys.stderr)
            continue
    raise RuntimeError(f"All {label} candidates failed; last error: {last_err}")


def parse_csv_bytes(raw_bytes):
    """Decode + parse a CSV into a list of dicts."""
    # Census CSVs sometimes contain non-UTF-8 chars (e.g., latin-1 county names).
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw_bytes.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _int(v):
    if v is None: return None
    s = str(v).strip().replace(",", "")
    if s in ("", ".", "X"): return None
    try:
        return int(float(s))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 1) State totals + components of change (GA only) + peer-state comparison
# ---------------------------------------------------------------------------
def nst_est_urls(vintage):
    """Two URL forms to try — V2024 used uppercase, V2025 may too."""
    return [
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{vintage}/state/totals/NST-EST{vintage}-ALLDATA.csv",
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{vintage}/state/totals/nst-est{vintage}-alldata.csv",
    ]


def fetch_state_data():
    """Fetch the NST-EST state totals + components CSV for the newest available
    vintage. Returns (rows, vintage_year, source_url)."""
    candidates = []
    for v in STATE_VINTAGES:
        candidates.extend(nst_est_urls(v))
    raw, used = fetch_first_available(candidates, label="NST-EST")
    vintage_year = None
    for v in STATE_VINTAGES:
        if str(v) in used:
            vintage_year = v
            break
    rows = parse_csv_bytes(raw)
    print(f"      [NST-EST{vintage_year}] parsed {len(rows)} rows")
    return rows, vintage_year, used


def extract_state_trend_and_components(rows, vintage_year):
    """Pull GA POPESTIMATE{YYYY} + components-of-change for each year."""
    ga = next((r for r in rows
               if r.get("STATE") in (GA_STATE_FIPS, "13", "13 ") and
                  r.get("NAME", "").strip().lower() == "georgia"), None)
    if ga is None:
        ga = next((r for r in rows if r.get("STATE") == GA_STATE_FIPS), None)
    if ga is None:
        raise RuntimeError("Could not find GA (STATE=13) row in NST-EST CSV")

    years = list(range(2020, vintage_year + 1))
    out = {
        "years": years,
        "population": [_int(ga.get(f"POPESTIMATE{y}")) for y in years],
        "births":    [_int(ga.get(f"BIRTHS{y}"))    for y in years],
        "deaths":    [_int(ga.get(f"DEATHS{y}"))    for y in years],
        "natural":   [_int(ga.get(f"NATURALCHG{y}")) for y in years],
        "intl_mig":  [_int(ga.get(f"INTERNATIONALMIG{y}")) for y in years],
        "dom_mig":   [_int(ga.get(f"DOMESTICMIG{y}")) for y in years],
        "net_mig":   [_int(ga.get(f"NETMIG{y}"))    for y in years],
    }
    return out


def extract_peer_states(rows, vintage_year):
    """Return list of {abbr, state, fips, pop_by_year, growth_pct} sorted GA first."""
    out = []
    for fips, (abbr, name) in PEER_STATES.items():
        row = next((r for r in rows if r.get("STATE") == fips), None)
        if not row:
            continue
        pop_by_year = {}
        for y in range(2020, vintage_year + 1):
            v = _int(row.get(f"POPESTIMATE{y}"))
            if v is not None:
                pop_by_year[y] = v
        if not pop_by_year:
            continue
        base = pop_by_year.get(2020)
        latest = pop_by_year.get(vintage_year) or pop_by_year[max(pop_by_year)]
        growth_pct = ((latest - base) / base * 100) if base else None
        out.append({
            "abbr": abbr, "state": name, "fips": fips,
            "pop": pop_by_year,
            "growth_pct": round(growth_pct, 2) if growth_pct is not None else None,
            "latest_pop": latest,
        })
    out.sort(key=lambda r: (r["abbr"] != "GA", -(r["growth_pct"] or -999)))
    return out


# ---------------------------------------------------------------------------
# 2) County-level — all 159 GA counties, population + growth
# ---------------------------------------------------------------------------
def co_est_urls(vintage):
    return [
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{vintage}/counties/totals/co-est{vintage}-alldata.csv",
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{vintage}/counties/totals/CO-EST{vintage}-ALLDATA.csv",
    ]


def fetch_county_data():
    candidates = []
    for v in COUNTY_VINTAGES:
        candidates.extend(co_est_urls(v))
    raw, used = fetch_first_available(candidates, label="CO-EST")
    vintage_year = None
    for v in COUNTY_VINTAGES:
        if str(v) in used:
            vintage_year = v
            break
    rows = parse_csv_bytes(raw)
    print(f"      [CO-EST{vintage_year}] parsed {len(rows)} rows")
    return rows, vintage_year, used


def extract_ga_counties(rows, vintage_year):
    """Filter to GA counties (STATE=13, COUNTY != 000) and compute growth."""
    out = []
    base_year = 2020
    for r in rows:
        if r.get("STATE") != GA_STATE_FIPS:
            continue
        county_code = r.get("COUNTY", "").strip().zfill(3)
        if county_code == "000":
            continue
        fips = GA_STATE_FIPS + county_code
        name = r.get("CTYNAME") or r.get("NAME") or "?"
        name = name.replace(" County", "").replace(" county", "").strip()
        pop_base = _int(r.get(f"POPESTIMATE{base_year}"))
        pop_latest = _int(r.get(f"POPESTIMATE{vintage_year}"))
        dom_mig = sum((_int(r.get(f"DOMESTICMIG{y}")) or 0)
                      for y in range(base_year + 1, vintage_year + 1))
        intl_mig = sum((_int(r.get(f"INTERNATIONALMIG{y}")) or 0)
                       for y in range(base_year + 1, vintage_year + 1))
        natural = sum((_int(r.get(f"NATURALCHG{y}")) or 0)
                      for y in range(base_year + 1, vintage_year + 1))
        growth_abs = (pop_latest - pop_base) if (pop_base and pop_latest) else None
        growth_pct = (growth_abs / pop_base * 100) if (pop_base and growth_abs is not None) else None
        out.append({
            "fips": fips, "county": name,
            "pop_base": pop_base, "pop_latest": pop_latest,
            "growth_abs": growth_abs,
            "growth_pct": round(growth_pct, 2) if growth_pct is not None else None,
            "dom_mig_total": dom_mig,
            "intl_mig_total": intl_mig,
            "natural_total": natural,
        })
    out.sort(key=lambda r: r["county"])
    return out


# ---------------------------------------------------------------------------
# 3) Age structure (state) — sc-est{VINTAGE}-agesex-civ.csv
# ---------------------------------------------------------------------------
def agesex_urls(vintage):
    return [
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{vintage}/state/asrh/sc-est{vintage}-agesex-civ.csv",
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{vintage}/state/asrh/sc-est{vintage}-syasex.csv",
    ]


def fetch_age_data():
    candidates = []
    for v in CHAR_VINTAGES:
        candidates.extend(agesex_urls(v))
    raw, used = fetch_first_available(candidates, label="sc-est-agesex")
    vintage_year = None
    for v in CHAR_VINTAGES:
        if str(v) in used:
            vintage_year = v
            break
    rows = parse_csv_bytes(raw)
    print(f"      [sc-est{vintage_year}] parsed {len(rows)} rows")
    return rows, vintage_year, used


def extract_age_structure(rows, vintage_year):
    """Compute GA's age distribution (median age, %<18, %65+, 10-yr bands + pyramid)."""
    candidates = [f"POPEST_CIV{vintage_year}", f"POPEST{vintage_year}_CIV",
                  f"POPESTIMATE{vintage_year}", f"POP{vintage_year}"]
    pop_col = None
    if rows:
        for c in candidates:
            if c in rows[0]:
                pop_col = c
                break
        if pop_col is None:
            for k in rows[0].keys():
                if str(vintage_year) in k and ("POP" in k.upper()):
                    pop_col = k
                    break
    if pop_col is None:
        raise RuntimeError(f"Could not find a POP column for year {vintage_year} in agesex CSV")

    ga_rows = [r for r in rows
               if r.get("STATE") == GA_STATE_FIPS
               and r.get("SEX", "0") in ("0",)]
    by_age = {}
    total_pop = None
    for r in ga_rows:
        try:
            age = int(r.get("AGE", "-1"))
        except ValueError:
            continue
        pop = _int(r.get(pop_col))
        if pop is None:
            continue
        if age == 999:
            total_pop = pop
            continue
        by_age[age] = pop

    if not by_age:
        raise RuntimeError("No age-stratified GA rows found in agesex CSV")

    if total_pop is None:
        total_pop = sum(by_age.values())

    by_age_sex = {1: {}, 2: {}}
    for r in rows:
        if r.get("STATE") != GA_STATE_FIPS: continue
        try:
            sex = int(r.get("SEX", "0"))
            age = int(r.get("AGE", "-1"))
        except ValueError:
            continue
        if sex not in (1, 2) or age == 999: continue
        pop = _int(r.get(pop_col))
        if pop is None: continue
        by_age_sex[sex][age] = pop

    def band_total(by_age_dict, lo, hi):
        return sum(v for a, v in by_age_dict.items() if lo <= a <= hi)

    bands = [
        ("0–9",   0, 9),
        ("10–19", 10, 19),
        ("20–29", 20, 29),
        ("30–39", 30, 39),
        ("40–49", 40, 49),
        ("50–59", 50, 59),
        ("60–69", 60, 69),
        ("70–79", 70, 79),
        ("80+",   80, 130),
    ]

    age_bands_total = [{"band": b, "total": band_total(by_age, lo, hi)}
                       for b, lo, hi in bands]
    age_bands_pyramid = [{
        "band": b,
        "male":   band_total(by_age_sex[1], lo, hi),
        "female": band_total(by_age_sex[2], lo, hi),
    } for b, lo, hi in bands]

    cum = 0; half = total_pop / 2.0; median_age = None
    for a in sorted(by_age):
        cum += by_age[a]
        if cum >= half:
            median_age = a
            break

    pct_under_18 = sum(v for a, v in by_age.items() if a < 18)  / total_pop * 100
    pct_18_64    = sum(v for a, v in by_age.items() if 18 <= a < 65) / total_pop * 100
    pct_65_plus  = sum(v for a, v in by_age.items() if a >= 65) / total_pop * 100

    return {
        "vintage": vintage_year,
        "total_pop": total_pop,
        "median_age": median_age,
        "pct_under_18": round(pct_under_18, 1),
        "pct_18_64":    round(pct_18_64, 1),
        "pct_65_plus":  round(pct_65_plus, 1),
        "age_bands": age_bands_total,
        "age_pyramid": age_bands_pyramid,
    }


# ---------------------------------------------------------------------------
# 4) Race / Hispanic origin — sc-est{VINTAGE}-alldata6.csv
# ---------------------------------------------------------------------------
def race_urls(vintage):
    return [
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{vintage}/state/asrh/sc-est{vintage}-alldata6.csv",
        f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{vintage}/state/asrh/sc-est{vintage}-alldata5.csv",
    ]


def fetch_race_data():
    candidates = []
    for v in CHAR_VINTAGES:
        candidates.extend(race_urls(v))
    raw, used = fetch_first_available(candidates, label="sc-est-race")
    vintage_year = None
    for v in CHAR_VINTAGES:
        if str(v) in used:
            vintage_year = v
            break
    rows = parse_csv_bytes(raw)
    has_hispanic = "alldata6" in used
    print(f"      [sc-est{vintage_year}-{'alldata6' if has_hispanic else 'alldata5'}] parsed {len(rows)} rows")
    return rows, vintage_year, used, has_hispanic


def extract_race_composition(rows, vintage_year, has_hispanic):
    """Aggregate GA population by race / Hispanic origin for latest year."""
    candidates = [f"POPESTIMATE{vintage_year}", f"POPEST{vintage_year}_CIV",
                  f"POP{vintage_year}"]
    pop_col = None
    if rows:
        for c in candidates:
            if c in rows[0]:
                pop_col = c; break
    if pop_col is None and rows:
        for k in rows[0].keys():
            if str(vintage_year) in k and "POP" in k.upper():
                pop_col = k; break
    if pop_col is None:
        raise RuntimeError(f"Could not find POP column for year {vintage_year} in race CSV")

    def get_total(origin, race):
        for r in rows:
            if r.get("STATE") != GA_STATE_FIPS: continue
            if r.get("SEX") != "0": continue
            try:
                if int(r.get("AGE", "0")) != 999: continue
            except ValueError: continue
            if has_hispanic and r.get("ORIGIN") != str(origin): continue
            if r.get("RACE") != str(race): continue
            v = _int(r.get(pop_col))
            if v is not None:
                return v
        return None

    races = [
        ("white",   1, "White (alone)"),
        ("black",   2, "Black (alone)"),
        ("aian",    3, "American Indian / Alaska Native (alone)"),
        ("asian",   4, "Asian (alone)"),
        ("nhpi",    5, "Native Hawaiian / Pacific Islander (alone)"),
        ("multi",   6, "Two or more races"),
    ]

    out = {"vintage": vintage_year, "has_hispanic": has_hispanic, "by_race": [], "by_origin": []}

    if has_hispanic:
        for key, code, label in races:
            tot = get_total(0, code)
            nh  = get_total(1, code)
            hisp = get_total(2, code)
            out["by_race"].append({
                "key": key, "label": label, "total": tot,
                "non_hispanic": nh, "hispanic": hisp,
            })
        hisp_total = sum(get_total(2, c) or 0 for _, c, _ in races)
        nh_total   = sum(get_total(1, c) or 0 for _, c, _ in races)
        out["by_origin"] = [
            {"key": "non_hispanic", "label": "Non-Hispanic / Latino", "pop": nh_total},
            {"key": "hispanic",     "label": "Hispanic / Latino",     "pop": hisp_total},
        ]
    else:
        for key, code, label in races:
            out["by_race"].append({"key": key, "label": label, "total": get_total(None, code)})

    total = sum((r["total"] or 0) for r in out["by_race"])
    if total:
        for r in out["by_race"]:
            r["pct"] = round((r["total"] or 0) / total * 100, 1) if r["total"] else None
        if has_hispanic:
            origin_total = sum(r["pop"] or 0 for r in out["by_origin"])
            for r in out["by_origin"]:
                r["pct"] = round((r["pop"] or 0) / origin_total * 100, 1) if origin_total else None

    return out


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def main():
    if OUT_PATH.exists():
        with open(OUT_PATH) as f:
            existing = json.load(f)
    else:
        existing = {}

    meta = dict(existing.get("_meta", {}))
    for section in ("state_trend", "components", "counties",
                    "peer_states", "age_structure", "race_composition"):
        meta.setdefault(section, {"last_updated": None, "source": None})

    out = dict(existing)
    out["fetched_at"] = TODAY_ISO
    out.setdefault("state", {})
    out.setdefault("counties", [])
    out.setdefault("peer_states", [])
    out.setdefault("age", {})
    out.setdefault("race", {})
    out.setdefault("kpis", {})

    state_vintage = None
    try:
        print(f"\n[1+2+4/6] Fetching NST-EST state totals + components…")
        state_rows, state_vintage, src_url = fetch_state_data()

        try:
            ga = extract_state_trend_and_components(state_rows, state_vintage)
            out["state"] = ga
            meta["state_trend"] = {
                "last_updated": TODAY_ISO,
                "source": "U.S. Census Bureau Population Estimates Program (PEP)",
                "vintage": state_vintage,
                "url": src_url,
            }
            meta["components"] = {
                "last_updated": TODAY_ISO,
                "source": "U.S. Census Bureau PEP — components of change",
                "vintage": state_vintage,
                "coverage_years": [2021, state_vintage],
            }
            latest_pop = ga["population"][-1]
            base_pop = ga["population"][0]
            out["kpis"]["population_latest"] = latest_pop
            out["kpis"]["population_base"]   = base_pop
            out["kpis"]["latest_year"]       = state_vintage
            out["kpis"]["growth_pct_5yr"]    = round((latest_pop - base_pop) / base_pop * 100, 2) if base_pop else None
            out["kpis"]["births_latest"]   = ga["births"][-1]
            out["kpis"]["deaths_latest"]   = ga["deaths"][-1]
            out["kpis"]["intl_mig_latest"] = ga["intl_mig"][-1]
            out["kpis"]["dom_mig_latest"]  = ga["dom_mig"][-1]
            out["kpis"]["net_mig_latest"]  = ga["net_mig"][-1]
            out["kpis"]["natural_latest"]  = ga["natural"][-1]
            print(f"      OK: GA pop {state_vintage} = {latest_pop:,} "
                  f"({out['kpis']['growth_pct_5yr']:+.2f}% since 2020); "
                  f"net mig = {out['kpis']['net_mig_latest']:+,}")
        except Exception as e:
            print(f"      ERROR: parsing state trend/components failed ({e}) — preserving prior.", file=sys.stderr)

        try:
            peers = extract_peer_states(state_rows, state_vintage)
            out["peer_states"] = peers
            meta["peer_states"] = {
                "last_updated": TODAY_ISO,
                "source": "U.S. Census Bureau PEP — state totals",
                "vintage": state_vintage,
                "states": [p["abbr"] for p in peers],
            }
            ga_growth = next((p["growth_pct"] for p in peers if p["abbr"] == "GA"), None)
            print(f"      OK: peer-state comparison ({len(peers)} states); "
                  f"GA 5-yr growth = {ga_growth:+.2f}%")
            ranked = sorted(peers, key=lambda p: -(p["growth_pct"] or -999))
            ga_rank = next((i + 1 for i, p in enumerate(ranked) if p["abbr"] == "GA"), None)
            out["kpis"]["peer_rank"] = ga_rank
            out["kpis"]["peer_count"] = len(peers)
        except Exception as e:
            print(f"      ERROR: peer-state extraction failed ({e}) — preserving prior.", file=sys.stderr)

    except Exception as e:
        print(f"      FATAL: NST-EST fetch failed ({e}) — preserving prior state/peer values.", file=sys.stderr)

    try:
        print(f"\n[3/6] Fetching CO-EST county totals…")
        county_rows, county_vintage, src_url = fetch_county_data()
        try:
            counties = extract_ga_counties(county_rows, county_vintage)
            out["counties"] = counties
            meta["counties"] = {
                "last_updated": TODAY_ISO,
                "source": "U.S. Census Bureau PEP — county totals",
                "vintage": county_vintage,
                "n_counties": len(counties),
                "coverage_years": [2020, county_vintage],
            }
            growing = [c for c in counties if c.get("growth_pct") is not None]
            n_growing = sum(1 for c in growing if c["growth_pct"] > 0)
            top_growth = sorted(growing, key=lambda c: -c["growth_pct"])[:10]
            bot_growth = sorted(growing, key=lambda c:  c["growth_pct"])[:10]
            top_pop = sorted([c for c in counties if c["pop_latest"]],
                             key=lambda c: -c["pop_latest"])[:10]
            out["leaderboards"] = {
                "top_growth":     top_growth,
                "bottom_growth":  bot_growth,
                "top_population": top_pop,
            }
            out["kpis"]["n_counties"]        = len(counties)
            out["kpis"]["n_growing"]         = n_growing
            out["kpis"]["n_shrinking"]       = len(growing) - n_growing
            out["kpis"]["county_vintage"]    = county_vintage
            out["kpis"]["fastest_growing"]   = top_growth[0]["county"] if top_growth else None
            out["kpis"]["fastest_growing_pct"] = top_growth[0]["growth_pct"] if top_growth else None
            out["kpis"]["fastest_shrinking"] = bot_growth[0]["county"] if bot_growth else None
            out["kpis"]["fastest_shrinking_pct"] = bot_growth[0]["growth_pct"] if bot_growth else None
            print(f"      OK: {len(counties)} GA counties; {n_growing} growing, "
                  f"{len(growing) - n_growing} shrinking since 2020")
            print(f"      Fastest grower: {out['kpis']['fastest_growing']} "
                  f"({out['kpis']['fastest_growing_pct']:+.1f}%)")
        except Exception as e:
            print(f"      ERROR: county parsing failed ({e}) — preserving prior.", file=sys.stderr)
    except Exception as e:
        print(f"      FATAL: CO-EST fetch failed ({e}) — preserving prior county values.", file=sys.stderr)

    try:
        print(f"\n[5/6] Fetching state age-sex characteristics…")
        age_rows, age_vintage, src_url = fetch_age_data()
        try:
            age = extract_age_structure(age_rows, age_vintage)
            out["age"] = age
            meta["age_structure"] = {
                "last_updated": TODAY_ISO,
                "source": "U.S. Census Bureau PEP — state by single year of age + sex",
                "vintage": age_vintage,
            }
            out["kpis"]["median_age"]   = age["median_age"]
            out["kpis"]["pct_under_18"] = age["pct_under_18"]
            out["kpis"]["pct_65_plus"]  = age["pct_65_plus"]
            print(f"      OK: median age = {age['median_age']}; "
                  f"%65+ = {age['pct_65_plus']}%; %<18 = {age['pct_under_18']}%")
        except Exception as e:
            print(f"      ERROR: age parsing failed ({e}) — preserving prior.", file=sys.stderr)
    except Exception as e:
        print(f"      FATAL: age-sex fetch failed ({e}) — preserving prior age values.", file=sys.stderr)

    try:
        print(f"\n[6/6] Fetching state race + Hispanic origin…")
        race_rows, race_vintage, src_url, has_hispanic = fetch_race_data()
        try:
            race = extract_race_composition(race_rows, race_vintage, has_hispanic)
            out["race"] = race
            meta["race_composition"] = {
                "last_updated": TODAY_ISO,
                "source": "U.S. Census Bureau PEP — state by race" + (" + Hispanic origin" if has_hispanic else ""),
                "vintage": race_vintage,
                "has_hispanic": has_hispanic,
            }
            top_race = max(race["by_race"], key=lambda r: (r.get("total") or 0))
            print(f"      OK: largest race group = {top_race['label']} "
                  f"({top_race.get('pct', '?')}%)")
        except Exception as e:
            print(f"      ERROR: race parsing failed ({e}) — preserving prior.", file=sys.stderr)
    except Exception as e:
        print(f"      FATAL: race-origin fetch failed ({e}) — preserving prior race values.", file=sys.stderr)

    out["_meta"] = meta
    out["latest_year"] = state_vintage or out.get("latest_year") or 2024

    live_sections = [k for k, v in meta.items() if v.get("last_updated") == TODAY_ISO]
    out["source_summary"] = (
        f"Live sections updated {TODAY_ISO}: {', '.join(live_sections) or 'none'}. "
        f"Stale or pending: {', '.join(k for k in meta if k not in live_sections) or 'none'}."
    )
    if live_sections:
        out["_fixture"] = False
    else:
        out.setdefault("_fixture", existing.get("_fixture", True))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {OUT_PATH}")
    print(f"  Live sections this run: {live_sections}")
    print(f"  Latest year: {out['latest_year']}")
    print(f"  KPIs: {json.dumps({k: v for k, v in out['kpis'].items() if v is not None}, indent=2)}")


if __name__ == "__main__":
    main()
