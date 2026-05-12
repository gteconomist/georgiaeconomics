"""Pull GA agriculture data from USDA NASS Quick Stats API.

Outputs: data/agriculture.json (replaces fixture).

What we fetch:
  1. State-level annual PRODUCTION trends for 4 commodities (2016-latest):
       - Broilers       — short_desc='BROILERS - PRODUCTION, MEASURED IN LB'
       - Peanuts        — short_desc='PEANUTS - PRODUCTION, MEASURED IN LB'
       - Pecans         — short_desc='PECANS - PRODUCTION, MEASURED IN LB'
       - Cotton (upland)— short_desc='COTTON, UPLAND - PRODUCTION, MEASURED IN 480 LB BALES'
  2. National production for the same 4 commodities (latest year), to compute GA share
  3. County-level production for the same 4 commodities (latest year, all GA counties)

What we leave on fixture (NASS economics is messier — separate follow-up):
  - cash_receipts_breakdown
  - rankings text/value (we update the share % for the 4 commodities we fetch)
  - notable_productions, major_studios — unrelated, stay as is

Env: NASS_API_KEY (free at https://quickstats.nass.usda.gov/api)

NASS API quirks:
  - "(D)" or "(Z)" Value strings = withheld for confidentiality. Filter out.
  - Values are returned as strings with comma thousand-separators.
  - county_code is a 3-digit string; full FIPS = state_fips_code + county_code.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import date

NASS_API_KEY = os.environ.get("NASS_API_KEY", "").strip()
if not NASS_API_KEY:
    print("ERROR: NASS_API_KEY env var not set. Get one free at https://quickstats.nass.usda.gov/api", file=sys.stderr)
    sys.exit(2)

NASS_URL = "https://quickstats.nass.usda.gov/api/api_GET/"

TODAY = date.today()
END_YEAR = TODAY.year
START_YEAR = END_YEAR - 10  # 10 years of trends; NASS may not have current year yet

sys.path.insert(0, str(Path(__file__).parent))
from _ga_counties import GA_COUNTIES
GA_COUNTY_NAMES = {fips[2:]: name for fips, name in GA_COUNTIES}  # 3-digit county_code → name


def nass_query(filters, retries=3):
    """GET against NASS Quick Stats. Returns list of records."""
    q = {"key": NASS_API_KEY, "format": "JSON", **filters}
    url = NASS_URL + "?" + urllib.parse.urlencode(q)
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                return payload.get("data", [])
        except urllib.error.HTTPError as e:
            # NASS returns 400 with a JSON body when no records match — that's NOT a fatal error
            if e.code == 400:
                try:
                    body = json.loads(e.read().decode("utf-8"))
                    if "no data" in str(body).lower() or "0 record" in str(body).lower():
                        return []
                except Exception:
                    pass
            last_err = e
            time.sleep(2 ** attempt)
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"NASS query failed after {retries} retries: {last_err}\n  filters: {filters}")


def parse_value(v):
    """Convert NASS Value string to float, or None if suppressed."""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("(D)", "(Z)", "(NA)", "(X)", ""):
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


# ---------- State-level annual production ----------

def fetch_state_production_annual(short_desc):
    """Return list of (year, value_float) for GA state, sorted ascending by year.

    Uses short_desc as the precise filter — most reliable way to pin down the
    exact metric+unit combination NASS uses.
    """
    rows = nass_query({
        "short_desc": short_desc,
        "agg_level_desc": "STATE",
        "state_alpha": "GA",
        "year__GE": str(START_YEAR),
        "year__LE": str(END_YEAR),
        "domain_desc": "TOTAL",
    })
    out = []
    for r in rows:
        v = parse_value(r.get("Value"))
        y = r.get("year")
        if v is None or y is None:
            continue
        try: y = int(y)
        except ValueError: continue
        out.append((y, v))
    # NASS may have multiple records per year (e.g., monthly + annual). Keep the largest = annual.
    by_year = {}
    for y, v in out:
        by_year[y] = max(by_year.get(y, 0), v)
    return sorted(by_year.items())


# ---------- National-level annual production (for share %) ----------

def fetch_national_production_year(short_desc, year):
    """Return single float = US total production for given year, or None."""
    rows = nass_query({
        "short_desc": short_desc,
        "agg_level_desc": "NATIONAL",
        "year": str(year),
        "domain_desc": "TOTAL",
    })
    vals = [parse_value(r.get("Value")) for r in rows]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


# ---------- County-level production (heatmap data) ----------

def fetch_county_production_latest(short_desc, year):
    """Return list of {fips, label, value} for GA counties.

    Counties with suppressed (D) data get value=0 so the map still renders them.
    """
    rows = nass_query({
        "short_desc": short_desc,
        "agg_level_desc": "COUNTY",
        "state_alpha": "GA",
        "year": str(year),
        "domain_desc": "TOTAL",
    })
    by_county_code = {}
    for r in rows:
        cc = r.get("county_code")
        v  = parse_value(r.get("Value"))
        if not cc or v is None:
            continue
        # Some NASS records are duplicates by district/region — keep max
        by_county_code[cc] = max(by_county_code.get(cc, 0), v)

    # Build full 159-county list — counties not in the response get 0
    out = []
    for fips, name in GA_COUNTIES:
        cc = fips[2:]   # 3-digit county code (state prefix already stripped)
        v  = by_county_code.get(cc, 0.0)
        out.append({"fips": fips, "label": name, "value": round(v, 2)})

    # Convert to share-of-state-total (%) for the heatmap to be comparable across commodities
    total = sum(p["value"] for p in out)
    if total > 0:
        for p in out:
            p["value"] = round(p["value"] / total * 100, 2)
    return out


# ---------- Most recent year with data (NASS lags vary by commodity) ----------

def latest_year_with_state_data(short_desc):
    """Walk back from END_YEAR until we find a state-level data point."""
    rows = nass_query({
        "short_desc": short_desc,
        "agg_level_desc": "STATE",
        "state_alpha": "GA",
        "year__GE": str(END_YEAR - 3),
        "year__LE": str(END_YEAR),
        "domain_desc": "TOTAL",
    })
    years = [int(r["year"]) for r in rows if r.get("year") and parse_value(r.get("Value")) is not None]
    return max(years) if years else (END_YEAR - 1)


# ---------- Main ----------

# (display_key, NASS short_desc, page_unit, divisor_to_page_unit)
COMMODITIES = [
    # Broilers: NASS reports in $ for state and lbs for production. We want lbs (in billions).
    ("broilers", "BROILERS - PRODUCTION, MEASURED IN LB",                    "B lbs",  1e9),
    ("peanuts",  "PEANUTS - PRODUCTION, MEASURED IN LB",                     "B lbs",  1e9),
    ("pecans",   "PECANS, IN SHELL - PRODUCTION, MEASURED IN LB",            "M lbs",  1e6),
    ("cotton",   "COTTON, UPLAND - PRODUCTION, MEASURED IN 480 LB BALES",    "K bales", 1e3),
]

def main():
    # Load existing fixture so we can preserve fields we don't fetch
    fixture_path = Path(__file__).parent.parent / "data" / "agriculture.json"
    if fixture_path.exists():
        with open(fixture_path) as f:
            existing = json.load(f)
    else:
        existing = {}

    print(f"Fetching NASS data — START_YEAR={START_YEAR} END_YEAR={END_YEAR}")

    trends = {"years": []}
    kpis_latest = {}
    county_production = {}
    rankings_updates = {}  # commodity name → updated dict

    # 1. State-level annual production trends (per commodity)
    all_year_sets = []
    raw_state_series = {}
    for key, short_desc, unit, divisor in COMMODITIES:
        print(f"  → state production: {key} ({short_desc!r})")
        series = fetch_state_production_annual(short_desc)
        if not series:
            print(f"    WARN: no data returned for {key}", file=sys.stderr)
            continue
        # Convert to page units
        series_in_page_units = [(y, round(v / divisor, 3)) for y, v in series]
        raw_state_series[key] = series_in_page_units
        all_year_sets.append({y for y, _ in series_in_page_units})

    if not raw_state_series:
        print("ERROR: NASS returned no data for any commodity. Aborting.", file=sys.stderr)
        sys.exit(3)

    # Take the intersection of years where we have data for ALL commodities (so charts align)
    common_years = sorted(set.intersection(*all_year_sets))
    if len(common_years) > 10:
        common_years = common_years[-10:]
    trends["years"] = common_years

    for key, short_desc, unit, divisor in COMMODITIES:
        if key not in raw_state_series:
            trends[f"{key}_trend"] = []
            continue
        by_year = dict(raw_state_series[key])
        trends[f"{key}_trend"] = [by_year.get(y) for y in common_years]
        if common_years:
            kpis_latest[key] = by_year.get(common_years[-1])

    latest_year = common_years[-1] if common_years else END_YEAR - 1

    # 2. National production for the SAME latest year (for share % calc)
    print(f"\n  → national production for {latest_year} (share calc)")
    for key, short_desc, unit, divisor in COMMODITIES:
        ga_v = kpis_latest.get(key)
        if ga_v is None:
            continue
        us_raw = fetch_national_production_year(short_desc, latest_year)
        if us_raw is None:
            print(f"    WARN: no US national value for {key} {latest_year}", file=sys.stderr)
            continue
        us_in_page_units = us_raw / divisor
        share_pct = round(ga_v / us_in_page_units * 100, 1) if us_in_page_units else None
        rankings_updates[key] = {
            "ga_value_in_page_units": ga_v,
            "us_value_in_page_units": round(us_in_page_units, 3),
            "ga_share_pct": share_pct,
            "page_unit": unit,
        }
        print(f"    {key}: GA={ga_v} {unit}, US={us_in_page_units:.1f} {unit}, GA share={share_pct}%")

    # 3. County-level production (heatmaps)
    print(f"\n  → county production for {latest_year}")
    for key, short_desc, unit, divisor in COMMODITIES:
        print(f"    {key}...")
        # For broilers, NASS county data is rarely available (poultry confidentiality). Skip if empty.
        pts = fetch_county_production_latest(short_desc, latest_year)
        nonzero = sum(1 for p in pts if p["value"] > 0)
        print(f"      counties with data: {nonzero}/{len(pts)}")
        if nonzero == 0 and key in (existing.get("county_production") or {}):
            print(f"      no NASS county data — KEEPING fixture county_production for {key}")
            county_production[key] = existing["county_production"][key]
        else:
            county_production[key] = {
                "metric_label": f"{key.title()} — share of GA production",
                "unit": "%",
                "points": pts,
            }

    # ---------- Assemble output ----------
    # Map commodity key → human label for trends section
    LABEL_BY_KEY = {"broilers": "broilers_lbs_b", "peanuts": "peanuts_lbs_b",
                    "pecans": "pecans_lbs_m", "cotton": "cotton_bales_k"}

    out = dict(existing) if existing else {}
    out["_fixture"] = False
    out["_note"]    = "Live data: NASS Quick Stats for state production trends + share-of-US calculations + county production. Cash receipts breakdown still on fixture (separate follow-up — NASS economics taxonomy is more complex)."
    out["source"]   = "USDA NASS Quick Stats API"
    out["fetched_at"] = TODAY.isoformat()
    out["latest_year"] = latest_year
    # Update trends in the structure the page expects
    out["trends"] = {
        "years":               common_years,
        "broilers_lbs_b":      trends.get("broilers_trend", []),
        "peanuts_lbs_b":       trends.get("peanuts_trend",  []),
        "pecans_lbs_m":        trends.get("pecans_trend",   []),
        "cotton_bales_k":      trends.get("cotton_trend",   []),
        "cash_receipts_b":     (existing.get("trends", {}) or {}).get("cash_receipts_b", []),  # preserve fixture
    }
    out["kpis"] = {
        "broilers_lbs_b":          kpis_latest.get("broilers"),
        "peanuts_lbs_b":           kpis_latest.get("peanuts"),
        "pecans_lbs_m":            kpis_latest.get("pecans"),
        "cotton_bales_k":          kpis_latest.get("cotton"),
        "total_cash_receipts_b":   (existing.get("kpis", {}) or {}).get("total_cash_receipts_b"),  # preserve
        "n1_commodities":          (existing.get("kpis", {}) or {}).get("n1_commodities", 4),
    }

    # Update rankings — patch the share_pct for the 4 commodities we have live data for
    rankings = list(existing.get("rankings", []))
    name_to_key = {
        "Broilers (chickens for meat)": "broilers",
        "Peanuts": "peanuts",
        "Pecans": "pecans",
        "Cotton": "cotton",
    }
    for r in rankings:
        k = name_to_key.get(r.get("commodity"))
        if k and k in rankings_updates:
            r["ga_share_pct"] = rankings_updates[k]["ga_share_pct"]
            # Also patch ga_value text with live value
            v = rankings_updates[k]["ga_value_in_page_units"]
            unit = rankings_updates[k]["page_unit"]
            r["ga_value"] = f"{v:.1f} {unit}" if v is not None else r.get("ga_value")
    out["rankings"] = rankings

    out["county_production"] = county_production
    # cash_receipts_breakdown stays as fixture (preserved by dict copy above)

    with open(fixture_path, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\nWrote {fixture_path}")
    print(f"  Years (common across all 4): {common_years}")
    print(f"  Latest KPIs: {kpis_latest}")
    print(f"  Counties with data:")
    for k, block in county_production.items():
        nz = sum(1 for p in block['points'] if p['value'] > 0)
        print(f"    {k:10s}: {nz}/159 counties have non-zero share")


if __name__ == "__main__":
    main()
