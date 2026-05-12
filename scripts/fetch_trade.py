"""Pull GA trade & logistics data from public APIs.

Outputs: data/trade.json (replaces fixture for the wired sections, preserves the rest).

What we fetch live this iteration:
  1. GA exports by destination country (top 10) — Census USA Trade Online API
       Endpoint: api.census.gov/data/timeseries/intltrade/exports/statehs
       Aggregates monthly data into annual totals per country, computes YoY.
  2. ATL MSA Transportation & Warehousing employment — BLS CES API
       Series: SMU131206043000000001 (NSA, T&W, ATL MSA, all employees, K)
       Used in the lead-indicator chart bottom of the page.

What stays on fixture (next iteration with Tavily):
  - savannah_teu_k       — Port of Savannah monthly TEU
  - brunswick_autos_k    — Brunswick auto throughput
  - atl_cargo_kt         — Hartsfield-Jackson cargo

Env: CENSUS_API_KEY, BLS_API_KEY
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

CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "").strip()
BLS_API_KEY    = os.environ.get("BLS_API_KEY",    "").strip()

if not CENSUS_API_KEY:
    print("ERROR: CENSUS_API_KEY env var not set", file=sys.stderr)
    sys.exit(2)
if not BLS_API_KEY:
    print("ERROR: BLS_API_KEY env var not set", file=sys.stderr)
    sys.exit(2)

TODAY = date.today()


# ---------- HTTP helper ----------
def http_get_json(url, retries=3, timeout=30):
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")[:300]
            except Exception:
                body = "(no body)"
            print(f"      [HTTP {e.code}] {url[:120]} → {body}", file=sys.stderr)
            if e.code == 400:   # bad request — soft fail
                return None
            last_err = e
        except urllib.error.URLError as e:
            last_err = e
        time.sleep(1 + attempt)
    print(f"      [HTTP FAIL] {url[:120]} — {last_err}", file=sys.stderr)
    return None


# ---------- Census USA Trade Online — GA exports by country ----------
def fetch_ga_exports_by_country_annual(year):
    """Sum monthly GA exports across all HS codes per country, return dict country_name -> total $.

    Census API quirk: query returns one row per (HS code × country) per month.
    To get country totals, we sum across all rows where state=GA.
    Values are in $ (USD).
    """
    base = "https://api.census.gov/data/timeseries/intltrade/exports/statehs"
    by_country = {}

    # Census API monthly time format: from YYYY-MM to YYYY-MM
    # We pull the full year in one query (one HTTP call).
    params = {
        "get":   "CTY_NAME,ALL_VAL_MO",
        "STATE": "GA",
        "time":  f"from {year}-01 to {year}-12",
        "key":   CENSUS_API_KEY,
    }
    url = base + "?" + urllib.parse.urlencode(params)
    rows = http_get_json(url, timeout=60)
    if not rows or len(rows) < 2:
        print(f"      no Census export rows for GA {year}", file=sys.stderr)
        return {}

    # rows[0] is header: e.g., ["CTY_NAME", "ALL_VAL_MO", "STATE", "time"]
    header = rows[0]
    try:
        cty_idx = header.index("CTY_NAME")
        val_idx = header.index("ALL_VAL_MO")
    except ValueError:
        print(f"      Census header mismatch: {header}", file=sys.stderr)
        return {}

    for r in rows[1:]:
        if len(r) <= max(cty_idx, val_idx): continue
        cty = r[cty_idx]
        try:    v = float(r[val_idx])
        except (TypeError, ValueError): continue
        # Skip non-real countries
        if not cty or cty.upper() in {"WORLD TOTAL", "WORLD", "TOTAL"}:
            continue
        by_country[cty] = by_country.get(cty, 0.0) + v

    return by_country


def normalize_country_name(name):
    """Trim weird Census formatting; keep ISO-friendly case where reasonable."""
    if not name: return ""
    n = name.strip()
    # Common Census names → cleaner display names
    ALIASES = {
        "UNITED KINGDOM": "United Kingdom",
        "UNITED STATES": "United States",
        "KOREA, SOUTH":  "South Korea",
        "KOREA, NORTH":  "North Korea",
        "TAIWAN":        "Taiwan",
    }
    upper = n.upper()
    if upper in ALIASES: return ALIASES[upper]
    # Title-case for display (CANADA → Canada, GERMANY → Germany)
    return " ".join(w.capitalize() for w in n.split())


# Rough country → ISO 2-letter mapping for the table icon column
ISO_BY_COUNTRY = {
    "Canada":"CA","Mexico":"MX","China":"CN","Germany":"DE","Singapore":"SG",
    "Japan":"JP","South Korea":"KR","United Kingdom":"GB","Belgium":"BE",
    "Brazil":"BR","Netherlands":"NL","France":"FR","Italy":"IT","India":"IN",
    "Hong Kong":"HK","Taiwan":"TW","Switzerland":"CH","Australia":"AU",
    "United Arab Emirates":"AE","Saudi Arabia":"SA","Spain":"ES","Israel":"IL",
    "Colombia":"CO","Chile":"CL","Argentina":"AR","Vietnam":"VN","Malaysia":"MY",
    "Thailand":"TH","Indonesia":"ID","Philippines":"PH","Egypt":"EG","Turkey":"TR",
}


def build_top_exports(latest_year):
    """Returns list of dicts {rank, country, value_musd, yoy_pct, iso} for top 10."""
    print(f"\n[Census] Pulling GA exports for {latest_year} and {latest_year - 1}...")
    cur  = fetch_ga_exports_by_country_annual(latest_year)
    prev = fetch_ga_exports_by_country_annual(latest_year - 1)
    if not cur:
        print(f"  → no data for {latest_year}", file=sys.stderr)
        return None

    # Convert $ → millions of $; dedupe and sort
    rows = []
    for raw_name, val in cur.items():
        name = normalize_country_name(raw_name)
        if not name: continue
        prev_v = prev.get(raw_name)
        if prev_v is None:
            # try normalized name in prev too (some Census naming variations)
            for pn, pv in prev.items():
                if normalize_country_name(pn) == name:
                    prev_v = pv; break
        yoy = round((val - prev_v) / prev_v * 100, 1) if prev_v else None
        rows.append({
            "country": name,
            "value_musd": round(val / 1e6, 0),
            "yoy_pct":   yoy if yoy is not None else 0.0,
            "iso":       ISO_BY_COUNTRY.get(name, ""),
        })

    rows.sort(key=lambda r: -r["value_musd"])
    top10 = rows[:10]
    for i, r in enumerate(top10, 1):
        r["rank"] = i

    return top10


# ---------- BLS CES — ATL MSA T&W employment ----------
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Atlanta-Sandy Springs-Alpharetta MSA = area code 12060 in BLS CES
# Series: state(13) + area(12060) + supersector(43=Trans/Warehousing/Utilities) + industry(00000) + datatype(01=AE,K)
ATL_TW_SERIES_NSA = "SMU131206043000000001"   # NSA, all employees, thousands
ATL_TW_SERIES_SA  = "SMS131206043000000001"   # Seasonally adjusted version

def fetch_bls_atl_tw_employment(months=80):
    """Returns sorted list of [YYYY-MM, employment_thousands] for ATL MSA T&W (SA preferred)."""
    end_year = TODAY.year
    start_year = end_year - 7  # ~80 months max

    payload = {
        "seriesid": [ATL_TW_SERIES_SA, ATL_TW_SERIES_NSA],
        "startyear": str(start_year),
        "endyear":   str(end_year),
        "registrationkey": BLS_API_KEY,
    }
    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(BLS_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  → BLS request failed: {e}", file=sys.stderr)
        return None

    if data.get("status") != "REQUEST_SUCCEEDED":
        print(f"  → BLS error: {data.get('message')}", file=sys.stderr)
        return None

    by_id = {s["seriesID"]: s for s in data["Results"]["series"]}
    # Prefer SA series; fall back to NSA if SA empty
    chosen = by_id.get(ATL_TW_SERIES_SA) or by_id.get(ATL_TW_SERIES_NSA)
    if not chosen or not chosen.get("data"):
        print(f"  → No BLS data for ATL T&W series", file=sys.stderr)
        return None

    out = []
    for obs in chosen["data"]:
        if obs.get("period", "").startswith("M") and obs["period"] != "M13":
            y = obs["year"]; m = obs["period"][1:].zfill(2)
            try: v = float(obs["value"])
            except (TypeError, ValueError): continue
            out.append([f"{y}-{m}", round(v, 1)])
    out.sort()
    return out[-months:] if months else out


# ---------- Main ----------
def main():
    fixture_path = Path(__file__).parent.parent / "data" / "trade.json"
    if not fixture_path.exists():
        print(f"ERROR: data/trade.json not found", file=sys.stderr)
        sys.exit(2)
    with open(fixture_path) as f:
        existing = json.load(f)

    # 1. Census exports (latest completed year — typically last calendar year)
    # Census trade data lags a bit; we may need to walk back if current year incomplete.
    latest_export_year = TODAY.year - 1
    top10 = build_top_exports(latest_export_year)

    if not top10:
        # Try one year earlier (in case Census hasn't released latest year fully)
        print(f"  retrying with {latest_export_year - 1}...")
        latest_export_year -= 1
        top10 = build_top_exports(latest_export_year)

    if top10:
        existing["ga_exports_top10_total_musd"] = round(sum(r["value_musd"] for r in top10), 0)
        existing[f"ga_exports_{latest_export_year}"] = top10
        # Replace the existing exports table (page reads ga_exports_2025 — generalize key)
        # Page expects exactly one of ga_exports_<year> — keep both old + new for compatibility
        existing["ga_exports_latest"] = top10  # always-fresh reference
        print(f"\n  ✓ Census exports: top country = {top10[0]['country']} (${top10[0]['value_musd']:,.0f}M)")
    else:
        print(f"  ✗ Census exports failed; keeping fixture", file=sys.stderr)

    # 2. BLS ATL MSA T&W employment
    print(f"\n[BLS] Pulling ATL MSA Transportation & Warehousing employment...")
    tw = fetch_bls_atl_tw_employment(months=80)
    if tw:
        existing["atl_tw_employment_k"] = tw
        print(f"  ✓ BLS T&W: {len(tw)} months ({tw[0][0]} → {tw[-1][0]}); latest = {tw[-1][1]}K")
    else:
        print(f"  ✗ BLS T&W failed; keeping fixture", file=sys.stderr)

    # 3. KPIs — patch top export country from live data
    if top10:
        kpis = existing.get("kpis", {}) or {}
        kpis["top_export_country"] = top10[0]["country"]
        kpis["top_export_musd"]    = top10[0]["value_musd"]
        existing["kpis"] = kpis

    # 4. Mark partial-live status
    notes = []
    if top10: notes.append("Census USA Trade Online (GA exports)")
    if tw:    notes.append("BLS CES (ATL MSA T&W employment)")
    if notes:
        existing["_fixture"] = False
        existing["_note"] = (
            "Partial live data: " + ", ".join(notes) + ". "
            "Port of Savannah TEU, Brunswick autos, and ATL Hartsfield cargo still on fixture "
            "(Tavily-based scrapers in next iteration)."
        )
    existing["fetched_at"] = TODAY.isoformat()
    if top10:
        existing["latest_label"] = max(existing.get("latest_label", ""), tw[-1][0]) if tw else existing.get("latest_label")
        existing["exports_data_year"] = latest_export_year

    with open(fixture_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"\nWrote {fixture_path}")


if __name__ == "__main__":
    main()
