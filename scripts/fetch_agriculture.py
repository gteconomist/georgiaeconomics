"""Pull GA agriculture data from USDA NASS Quick Stats API.

Outputs: data/agriculture.json (replaces fixture).

What we fetch:
  1. State-level annual PRODUCTION trends for 4 commodities (10 yrs)
  2. National production for the same commodities (latest year) → GA share %
  3. County-level production (latest year, all GA counties)

What we leave on fixture:
  - cash_receipts_breakdown
  - notable_productions, major_studios

Env: NASS_API_KEY (free at https://quickstats.nass.usda.gov/api)

NASS conventions used here:
  - Broilers live under commodity=CHICKENS, class=BROILERS (NOT a separate "BROILERS" commodity)
  - Cotton has classes UPLAND and PIMA; we use UPLAND (~99% of US cotton)
  - reference_period_desc=YEAR filters out monthly/quarterly reports
  - Suppressed values come back as "(D)" / "(Z)" — filter those out
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
    print("ERROR: NASS_API_KEY env var not set.", file=sys.stderr)
    sys.exit(2)

NASS_URL = "https://quickstats.nass.usda.gov/api/api_GET/"

TODAY = date.today()
END_YEAR = TODAY.year
START_YEAR = END_YEAR - 10

sys.path.insert(0, str(Path(__file__).parent))
from _ga_counties import GA_COUNTIES


# ---------- HTTP helper ----------
def nass_query(filters, retries=2):
    """GET against NASS. Returns list of records, or [] on 400 (no data / bad filter combo).

    400s are NEVER raised — they're logged to stderr so the run keeps going.
    Only network failures raise after retries.
    """
    q = {"key": NASS_API_KEY, "format": "JSON", **filters}
    url = NASS_URL + "?" + urllib.parse.urlencode(q)
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                return payload.get("data", [])
        except urllib.error.HTTPError as e:
            if e.code == 400:
                # Soft fail. Could be no matching records OR a bad filter combo.
                try:
                    body_text = e.read().decode("utf-8")[:300]
                except Exception:
                    body_text = "(no body)"
                print(f"      [NASS 400] {filters} → {body_text}", file=sys.stderr)
                return []
            last_err = e
            time.sleep(1 + attempt)
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(1 + attempt)
    print(f"      [NASS NETWORK FAIL] {filters} — {last_err}", file=sys.stderr)
    return []


def parse_value(v):
    if v is None: return None
    s = str(v).strip()
    if s in ("(D)", "(Z)", "(NA)", "(X)", ""): return None
    try:    return float(s.replace(",", ""))
    except ValueError: return None


# ---------- Commodity definitions ----------
# Each entry: (key, base_filters, page_unit_label, divisor_to_page_unit)
# base_filters define how to pin down the right metric from NASS using multiple params
# (more robust than depending on a single short_desc string).
COMMODITIES = [
    ("broilers", {
        "commodity_desc": "CHICKENS",
        "class_desc": "BROILERS",
        "statisticcat_desc": "PRODUCTION",
        "unit_desc": "LB",
    }, "B lbs", 1e9),

    ("peanuts", {
        "commodity_desc": "PEANUTS",
        "statisticcat_desc": "PRODUCTION",
        "unit_desc": "LB",
    }, "B lbs", 1e9),

    ("pecans", {
        "commodity_desc": "PECANS",
        "statisticcat_desc": "PRODUCTION",
        "unit_desc": "LB",
    }, "M lbs", 1e6),

    ("cotton", {
        "commodity_desc": "COTTON",
        "class_desc": "UPLAND",
        "statisticcat_desc": "PRODUCTION",
        "unit_desc": "BALES",
    }, "K bales", 1e3),
]


# ---------- Annual state-level production ----------
def fetch_state_production_annual(base_filters):
    """Try SURVEY (annual) first; fall back to no source filter (which catches
    Census-of-Agriculture-only commodities like broilers at state level)."""
    base = dict(base_filters,
                agg_level_desc="STATE",
                state_alpha="GA",
                year__GE=str(START_YEAR),
                year__LE=str(END_YEAR),
                reference_period_desc="YEAR")
    by_year = {}
    # Pass 1: Survey only — annual
    rows = nass_query(dict(base, source_desc="SURVEY"))
    for r in rows:
        v = parse_value(r.get("Value"))
        y = r.get("year")
        if v is None or y is None: continue
        try: y = int(y)
        except ValueError: continue
        by_year[y] = max(by_year.get(y, 0), v)
    # Pass 2: anything (catches Census of Ag-only series, e.g. state broilers in LB)
    rows = nass_query(base)
    for r in rows:
        v = parse_value(r.get("Value"))
        y = r.get("year")
        if v is None or y is None: continue
        try: y = int(y)
        except ValueError: continue
        # Don't overwrite Survey value with Census value if Survey already has it
        by_year.setdefault(y, v)
    return sorted(by_year.items())


def fetch_national_production_year(base_filters, year):
    base = dict(base_filters,
                agg_level_desc="NATIONAL",
                year=str(year),
                reference_period_desc="YEAR")
    # Try Survey first, then anything
    for f in (dict(base, source_desc="SURVEY"), base):
        rows = nass_query(f)
        vals = [parse_value(r.get("Value")) for r in rows]
        vals = [v for v in vals if v is not None]
        if vals:
            return max(vals)
    return None


# ---------- County-level production ----------
def fetch_county_production(base_filters, year):
    """Returns list of {fips, label, value} for all 159 GA counties, value as % of state total.
    Tries Survey first, falls back to any source (catches Census-of-Ag-only county data)."""
    base = dict(base_filters,
                agg_level_desc="COUNTY",
                state_alpha="GA",
                year=str(year),
                reference_period_desc="YEAR")
    rows = []
    for f in (dict(base, source_desc="SURVEY"), base):
        r = nass_query(f)
        if r:
            rows = r
            break
    by_county_code = {}
    for r in rows:
        cc = r.get("county_code")
        v  = parse_value(r.get("Value"))
        if not cc or v is None: continue
        by_county_code[cc] = max(by_county_code.get(cc, 0), v)

    # Build full 159-county list
    pts = []
    for fips, name in GA_COUNTIES:
        cc = fips[2:]
        v  = by_county_code.get(cc, 0.0)
        pts.append({"fips": fips, "label": name, "value": round(v, 2)})

    # Normalize to share-of-state-total %
    total = sum(p["value"] for p in pts)
    if total > 0:
        for p in pts:
            p["value"] = round(p["value"] / total * 100, 2)
    return pts


# ---------- Main ----------
def main():
    fixture_path = Path(__file__).parent.parent / "data" / "agriculture.json"
    if fixture_path.exists():
        with open(fixture_path) as f:
            existing = json.load(f)
    else:
        existing = {}

    print(f"Fetching NASS data — {START_YEAR} .. {END_YEAR}")

    state_series      = {}   # key → [(year, raw_value), ...]
    failed            = []   # commodities with no data

    # 1. State-level annual production trends
    print("\n[1/3] State-level annual production:")
    for key, base_filters, _, _ in COMMODITIES:
        print(f"  → {key} ({base_filters.get('commodity_desc')})")
        series = fetch_state_production_annual(base_filters)
        if not series:
            print(f"      WARN: no state-level data for {key}")
            failed.append(key)
            continue
        state_series[key] = series
        print(f"      OK: {len(series)} years, latest {series[-1][0]}={series[-1][1]:,.0f}")

    if not state_series:
        print("\nFATAL: no state-level data for any commodity. Keeping fixture.", file=sys.stderr)
        sys.exit(3)

    # UNION of years (not intersection) — let each commodity show its own coverage.
    # Some commodities (e.g. state-level broilers in LB) only appear in Census of Ag (every 5 yrs);
    # others have annual surveys. We don't want broilers' sparseness to collapse the others' series.
    year_sets = [{y for y, _ in s} for s in state_series.values()]
    year_union = sorted(set().union(*year_sets)) if year_sets else []
    if len(year_union) > 10:
        year_union = year_union[-10:]
    common_years = year_union  # rename for downstream code
    latest_year = max(year_union) if year_union else END_YEAR - 1
    print(f"\n  Year coverage across {len(state_series)} commodities (union): {year_union}")
    print(f"  Latest year: {latest_year}")
    for key, series in state_series.items():
        years_for_key = sorted({y for y, _ in series})
        print(f"    {key}: {len(years_for_key)} years available — {years_for_key}")

    # 2. National production for the latest year (share calc)
    print(f"\n[2/3] National production for share-of-US calc ({latest_year}):")
    rankings_updates = {}
    for key, base_filters, page_unit, divisor in COMMODITIES:
        if key not in state_series: continue
        ga_raw = dict(state_series[key]).get(latest_year)
        if ga_raw is None: continue
        us_raw = fetch_national_production_year(base_filters, latest_year)
        if us_raw is None:
            print(f"  → {key}: no national data ({latest_year})")
            continue
        ga_in_pu = ga_raw / divisor
        us_in_pu = us_raw / divisor
        share    = round(ga_in_pu / us_in_pu * 100, 1) if us_in_pu else None
        rankings_updates[key] = {
            "ga_value_in_page_units": round(ga_in_pu, 3),
            "us_value_in_page_units": round(us_in_pu, 3),
            "ga_share_pct": share,
            "page_unit": page_unit,
        }
        print(f"  → {key}: GA={ga_in_pu:,.2f} {page_unit}, US={us_in_pu:,.1f} {page_unit}, share={share}%")

    # 3. County-level production
    print(f"\n[3/3] County production for {latest_year}:")
    county_production = {}
    for key, base_filters, _, _ in COMMODITIES:
        print(f"  → {key}")
        pts = fetch_county_production(base_filters, latest_year)
        nz  = sum(1 for p in pts if p["value"] > 0)
        if nz == 0:
            print(f"      no county data — keeping fixture for {key}")
            cp_existing = (existing.get("county_production") or {}).get(key)
            if cp_existing:
                county_production[key] = cp_existing
            continue
        print(f"      {nz}/159 counties with data")
        county_production[key] = {
            "metric_label": f"{key.title()} — share of GA production",
            "unit": "%",
            "points": pts,
        }

    # ---------- Assemble output ----------
    out = dict(existing) if existing else {}
    out["_fixture"]   = False
    out["_note"]      = (
        "Live data: NASS Quick Stats for state production trends + share-of-US + county production. "
        "Cash receipts breakdown still on fixture (separate follow-up — NASS economics taxonomy is more complex). "
        f"Commodities with NO live data this run: {failed}" if failed else
        "Live data: NASS Quick Stats for state production trends + share-of-US + county production. "
        "Cash receipts breakdown still on fixture (separate follow-up — NASS economics taxonomy is more complex)."
    )
    out["source"]     = "USDA NASS Quick Stats API"
    out["fetched_at"] = TODAY.isoformat()
    out["latest_year"]= latest_year

    # Trends — preserve existing values for any commodity that failed
    existing_trends = (existing.get("trends") or {})
    def trend_for(key, divisor):
        if key in failed:
            return existing_trends.get({"broilers":"broilers_lbs_b","peanuts":"peanuts_lbs_b",
                                        "pecans":"pecans_lbs_m","cotton":"cotton_bales_k"}[key], [])
        by_year = dict(state_series[key])
        return [round(by_year[y] / divisor, 3) if y in by_year else None for y in common_years]

    # Set years to common_years if we got live data, else preserve existing
    out["trends"] = {
        "years":           common_years if state_series else existing_trends.get("years", []),
        "broilers_lbs_b":  trend_for("broilers", 1e9),
        "peanuts_lbs_b":   trend_for("peanuts",  1e9),
        "pecans_lbs_m":    trend_for("pecans",   1e6),
        "cotton_bales_k":  trend_for("cotton",   1e3),
        "cash_receipts_b": existing_trends.get("cash_receipts_b", []),  # preserve fixture
    }

    # KPIs
    def kpi_for(key, divisor):
        if key in failed: return (existing.get("kpis", {}) or {}).get({"broilers":"broilers_lbs_b","peanuts":"peanuts_lbs_b","pecans":"pecans_lbs_m","cotton":"cotton_bales_k"}[key])
        by_year = dict(state_series[key])
        v = by_year.get(latest_year)
        return round(v / divisor, 3) if v is not None else None

    out["kpis"] = {
        "broilers_lbs_b":        kpi_for("broilers", 1e9),
        "peanuts_lbs_b":         kpi_for("peanuts",  1e9),
        "pecans_lbs_m":          kpi_for("pecans",   1e6),
        "cotton_bales_k":        kpi_for("cotton",   1e3),
        "total_cash_receipts_b": (existing.get("kpis", {}) or {}).get("total_cash_receipts_b"),
        "n1_commodities":        (existing.get("kpis", {}) or {}).get("n1_commodities", 4),
    }

    # Patch rankings table — update share % and value for the commodities we got live data for
    rankings = list(existing.get("rankings", []))
    name_to_key = {
        "Broilers (chickens for meat)": "broilers",
        "Peanuts": "peanuts",
        "Pecans":  "pecans",
        "Cotton":  "cotton",
    }
    for r in rankings:
        k = name_to_key.get(r.get("commodity"))
        if k and k in rankings_updates:
            u = rankings_updates[k]
            r["ga_share_pct"] = u["ga_share_pct"]
            v = u["ga_value_in_page_units"]
            unit = u["page_unit"]
            r["ga_value"] = f"{v:.1f} {unit}" if v is not None else r.get("ga_value")
    out["rankings"] = rankings

    # County production — already merged with fixture fallbacks above
    out["county_production"] = county_production or existing.get("county_production", {})

    with open(fixture_path, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\nWrote {fixture_path}")
    print(f"  Commodities with live state data: {sorted(state_series.keys())}")
    if failed:
        print(f"  Commodities still on fixture:    {failed}")
    print(f"  Latest year: {latest_year}")
    print(f"  KPIs: {out['kpis']}")


if __name__ == "__main__":
    main()
