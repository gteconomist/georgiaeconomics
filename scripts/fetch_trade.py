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
    """Robust JSON fetch — never crashes, returns None on any failure."""
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                if not body.strip():
                    print(f"      [HTTP empty body] {url[:140]}", file=sys.stderr)
                    return None
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    snippet = body[:200].replace("\n", " ")
                    print(f"      [HTTP non-JSON] {url[:140]} → body starts: {snippet!r}", file=sys.stderr)
                    return None
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")[:300]
            except Exception:
                body = "(no body)"
            print(f"      [HTTP {e.code}] {url[:140]} → {body}", file=sys.stderr)
            if e.code == 400:
                return None
            last_err = e
        except urllib.error.URLError as e:
            last_err = e
        except Exception as e:
            print(f"      [HTTP unexpected error] {url[:140]} — {type(e).__name__}: {e}", file=sys.stderr)
            return None
        time.sleep(1 + attempt)
    print(f"      [HTTP FAIL] {url[:140]} — {last_err}", file=sys.stderr)
    return None


# ---------- Census aggregate-row filter ----------
# Census USA Trade Online returns regional/grouping rows alongside actual destination
# countries (e.g., "Total For All Countries", "European Union", "Asia"). These would
# inflate rankings and double-count, so we filter them out by name.
_CENSUS_AGGREGATE_NAMES = {
    "WORLD TOTAL", "WORLD", "TOTAL", "TOTAL FOR ALL COUNTRIES",
    "TOTAL TRADE", "TRADE TOTAL",
    "AFRICA", "ASIA", "EUROPE", "OCEANIA", "AMERICAS",
    "NORTH AMERICA", "SOUTH AMERICA", "CENTRAL AMERICA",
    "SOUTH/CENTRAL AMERICA", "ASIA AND OCEANIA", "ASIA NES",
    "EUROPEAN UNION", "EUROPEAN UNION-27", "EU-27", "EU 27",
    "ASEAN", "OPEC", "USMCA", "NAFTA", "CAFTA-DR", "CAFTA",
    "FREE TRADE AREAS", "FREE TRADE AGREEMENT COUNTRIES",
    "TPP", "TPP COUNTRIES", "G7", "G20",
}

def _is_aggregate(name):
    n = name.upper().strip()
    if not n: return True
    if n in _CENSUS_AGGREGATE_NAMES: return True
    # Defensive catch-all: any "country" with TOTAL or WORLD in the name
    if "TOTAL" in n or "WORLD" in n: return True
    return False



# ---------- Census USA Trade Online — GA exports by country ----------
def fetch_ga_exports_by_country_annual(year):
    """Sum monthly GA exports per country, return dict country_name -> total $.

    Strategy: try multiple Census API query shapes, since the right combination
    of get-fields, predicates, and COMM_LVL has been finicky. Logs what each
    attempt returns so we can debug from Actions output.
    """
    base = "https://api.census.gov/data/timeseries/intltrade/exports/statehs"

    # Try several query strategies in order. Each is tagged with a name for logging.
    # First successful query (returns >5 real countries after filter) wins.
    strategies = [
        # STATE=13 (FIPS) variants — Census docs example queries use FIPS
        ("FIPS+HS6+monthly", lambda y: _fetch_monthly_loop(base, y, "HS6", state="13")),
        ("FIPS+HS4+monthly", lambda y: _fetch_monthly_loop(base, y, "HS4", state="13")),
        ("FIPS+HS6+range",   lambda y: _fetch_range(base, y, "HS6",   state="13")),
        ("FIPS+HS2+range",   lambda y: _fetch_range(base, y, "HS2",   state="13")),
        # STATE=GA variants
        ("GA+HS6+monthly",   lambda y: _fetch_monthly_loop(base, y, "HS6", state="GA")),
        ("GA+HS6+range",     lambda y: _fetch_range(base, y, "HS6",   state="GA")),
        # No COMM_LVL filter — Census may default to per-country aggregation if get= has CTY_CODE
        ("FIPS+noCOMM+range",lambda y: _fetch_range(base, y, None,    state="13")),
        ("GA+noCOMM+range",  lambda y: _fetch_range(base, y, None,    state="GA")),
    ]

    for name, fn in strategies:
        print(f"      [Census strategy: {name}]", file=sys.stderr)
        result = fn(year)
        n_real = sum(1 for k in result if not _is_aggregate(k))
        print(f"        → got {len(result)} country rows ({n_real} real after filter)", file=sys.stderr)
        if n_real >= 5:
            return {k: v for k, v in result.items() if not _is_aggregate(k)}

    print(f"      All Census strategies returned <5 real countries for {year}", file=sys.stderr)
    return {}


def _parse_census_response(rows, by_country):
    """Add rows to by_country dict. Filters aggregates by CTY_CODE
    (real Census country codes are >= 1000; aggregates like EU/OECD/CAFTA start with 0).
    """
    if not rows or len(rows) < 2:
        return
    header = rows[0]
    if "CTY_NAME" not in header or "ALL_VAL_MO" not in header:
        print(f"        ⚠ unexpected header: {header}", file=sys.stderr)
        return
    cty_name_idx = header.index("CTY_NAME")
    val_idx      = header.index("ALL_VAL_MO")
    cty_code_idx = header.index("CTY_CODE") if "CTY_CODE" in header else None

    # Log first 3 data rows for diagnosis
    for r in rows[1:4]:
        print(f"        sample row: {r[:5]}", file=sys.stderr)

    for r in rows[1:]:
        if len(r) <= max(cty_name_idx, val_idx): continue
        cty_name = r[cty_name_idx]
        cty_code = r[cty_code_idx] if cty_code_idx is not None and len(r) > cty_code_idx else None
        try:    v = float(r[val_idx])
        except (TypeError, ValueError): continue
        if not cty_name: continue

        # Primary filter: CTY_CODE-based (most reliable).
        # Census aggregates use CTY_CODE 0001-0999; real countries are 1000+.
        if cty_code:
            try:
                if int(cty_code) < 1000:
                    continue
            except (TypeError, ValueError):
                pass  # malformed code, fall through to name filter

        # Backup filter: name-based (in case CTY_CODE is missing or malformed)
        if _is_aggregate(cty_name):
            continue

        by_country[cty_name] = by_country.get(cty_name, 0.0) + v


def _fetch_range(base, year, comm_lvl, state="GA"):
    """One query for the whole year with a time range."""
    by_country = {}
    params = {
        "get":   "CTY_CODE,CTY_NAME,ALL_VAL_MO",
        "STATE": state,
        "time":  f"from {year}-01 to {year}-12",
        "key":   CENSUS_API_KEY,
    }
    if comm_lvl:
        params["COMM_LVL"] = comm_lvl
    url = base + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    rows = http_get_json(url, timeout=120)
    _parse_census_response(rows, by_country)
    return by_country


def _fetch_monthly_loop(base, year, comm_lvl, state="GA"):
    """Loop month-by-month, summing into by_country."""
    by_country = {}
    for month in range(1, 13):
        params = {
            "get":   "CTY_CODE,CTY_NAME,ALL_VAL_MO",
            "STATE": state,
            "time":  f"{year}-{month:02d}",
            "key":   CENSUS_API_KEY,
        }
        if comm_lvl:
            params["COMM_LVL"] = comm_lvl
        url = base + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        rows = http_get_json(url, timeout=60)
        if rows:
            _parse_census_response(rows, by_country)
        # Bail early if first month returns nothing — saves 11 wasted calls
        if month == 1 and not by_country:
            return by_country
    return by_country


def normalize_country_name(name):
    """Trim Census formatting; keep ISO-friendly case where reasonable."""
    if not name: return ""
    n = name.strip()
    ALIASES = {
        "UNITED KINGDOM":          "United Kingdom",
        "UNITED STATES":           "United States",
        "KOREA, SOUTH":            "South Korea",
        "KOREA, NORTH":            "North Korea",
        "TAIWAN":                  "Taiwan",
        "VIETNAM":                 "Vietnam",
        "RUSSIA":                  "Russia",
        "RUSSIAN FEDERATION":      "Russia",
        "DOMINICAN REPUBLIC":      "Dominican Republic",
        "UNITED ARAB EMIRATES":    "United Arab Emirates",
        "SAUDI ARABIA":            "Saudi Arabia",
        "HONG KONG":               "Hong Kong",
        "ST VINCENT AND THE GRENADINES": "St Vincent & the Grenadines",
    }
    upper = n.upper()
    if upper in ALIASES: return ALIASES[upper]

    # For multi-word names: title-case each word but preserve common short prefixes
    SMALL_WORDS = {"and", "of", "the", "in", "on", "at"}
    words = n.split()
    out_words = []
    for i, w in enumerate(words):
        wl = w.lower()
        if i > 0 and wl in SMALL_WORDS:
            out_words.append(wl)
        else:
            out_words.append(w.capitalize())
    return " ".join(out_words)


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
