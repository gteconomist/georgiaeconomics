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
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import date

CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "").strip()
BLS_API_KEY    = os.environ.get("BLS_API_KEY",    "").strip()
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()

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
_EXPORTS_BY_YEAR_CACHE = {}

def fetch_ga_exports_by_country_annual(year):
    """Sum monthly GA exports per country, return dict country_name -> total $.

    Strategy: try multiple Census API query shapes, since the right combination
    of get-fields, predicates, and COMM_LVL has been finicky. Logs what each
    attempt returns so we can debug from Actions output. Results are cached per
    year so the country table and the multi-year trend don't double-pull a year.
    """
    if year in _EXPORTS_BY_YEAR_CACHE:
        return _EXPORTS_BY_YEAR_CACHE[year]
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
            clean = {k: v for k, v in result.items() if not _is_aggregate(k)}
            _EXPORTS_BY_YEAR_CACHE[year] = clean
            return clean

    print(f"      All Census strategies returned <5 real countries for {year}", file=sys.stderr)
    _EXPORTS_BY_YEAR_CACHE[year] = {}
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


# ---------- Census USA Trade Online — multi-year total & commodity breakdown ----------
from datetime import datetime

def _now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

# HS 2-digit chapter → readable name (the chapters that matter for GA exports;
# anything else falls back to the description Census returns, then "HS <code>").
HS2_NAMES = {
    "02": "Meat", "03": "Fish & seafood", "08": "Edible fruit & nuts",
    "10": "Cereals", "12": "Oil seeds & oleaginous fruits",
    "16": "Prepared meat & fish", "17": "Sugars & confectionery",
    "23": "Food-industry residues (animal feed)",
    "24": "Tobacco", "27": "Mineral fuels & oils",
    "28": "Inorganic chemicals", "29": "Organic chemicals",
    "30": "Pharmaceuticals", "31": "Fertilizers", "32": "Tanning & dyeing extracts",
    "33": "Essential oils & cosmetics", "38": "Misc. chemical products",
    "39": "Plastics", "40": "Rubber", "44": "Wood",
    "47": "Wood pulp", "48": "Paper & paperboard",
    "52": "Cotton", "55": "Man-made staple fibers",
    "72": "Iron & steel", "73": "Iron & steel articles",
    "74": "Copper", "76": "Aluminum",
    "84": "Machinery & mechanical appliances", "85": "Electrical machinery & equipment",
    "87": "Vehicles", "88": "Aircraft & spacecraft", "90": "Optical & medical instruments",
    "94": "Furniture & bedding", "95": "Toys, games & sports equipment",
}

def _hs2_name(code, desc=None):
    code = (code or "").zfill(2)
    if code in HS2_NAMES:
        return HS2_NAMES[code]
    if desc:
        d = desc.strip().title()
        return d[:48] if d else f"HS {code}"
    return f"HS {code}"


def _census_annual_total(year):
    """Total GA goods exports for a calendar year ($), summed over all countries.

    Reuses the proven per-country pull and sums the real-country values (the same
    figures the destinations table is built from), so the annual trend ties out
    exactly to the country table for overlapping years. Returns float or None.
    """
    by_country = fetch_ga_exports_by_country_annual(year)
    if not by_country:
        return None
    total = sum(v for v in by_country.values() if isinstance(v, (int, float)))
    return total if total > 0 else None


def build_exports_annual(latest_year, years_back=6):
    """Annual GA total goods exports for the last `years_back` complete years.

    Returns dict with years[], total_musd[], latest figures, YoY, and CAGR. None
    if fewer than 2 years are obtainable.
    """
    print(f"\n[Census] Building {years_back}-year export trend ending {latest_year}...")
    years, totals = [], []
    for y in range(latest_year - years_back + 1, latest_year + 1):
        t = _census_annual_total(y)
        if t is None:
            print(f"  → {y}: no data", file=sys.stderr)
            continue
        years.append(y)
        totals.append(round(t / 1e6, 0))   # $ → $M
        print(f"  → {y}: ${totals[-1]:,.0f}M", file=sys.stderr)

    if len(years) < 2:
        return None

    latest_total = totals[-1]
    prev_total = totals[-2]
    yoy = round((latest_total - prev_total) / prev_total * 100, 1) if prev_total else None
    n = len(totals) - 1
    cagr = round(((totals[-1] / totals[0]) ** (1.0 / n) - 1) * 100, 1) if totals[0] and n else None
    return {
        "years": years,
        "total_musd": totals,
        "latest_year": years[-1],
        "latest_total_musd": latest_total,
        "yoy_pct": yoy,
        "cagr_pct": cagr,
    }


def _fetch_commodity_year(year, top_n=10):
    """GA exports by HS2 chapter for a calendar year, summed over months & countries.

    Census timeseries aggregates over dimensions not present in `get=`, so by
    requesting E_COMMODITY (not CTY_CODE) at COMM_LVL=HS2 we get one row per
    chapter already summed across destinations. Loops months and sums ALL_VAL_MO.
    Returns {hs2: {"name", "value"}} or {} on failure.
    """
    base = "https://api.census.gov/data/timeseries/intltrade/exports/statehs"
    by_chapter = {}   # hs2 -> {"name", "value"}
    got_any = False
    for month in range(1, 13):
        params = {
            "get":      "E_COMMODITY,E_COMMODITY_LDESC,ALL_VAL_MO",
            "STATE":    "13",
            "COMM_LVL": "HS2",
            "time":     f"{year}-{month:02d}",
            "key":      CENSUS_API_KEY,
        }
        url = base + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        rows = http_get_json(url, timeout=90)
        if not rows or len(rows) < 2:
            if month == 1 and not by_chapter:
                # try GA-state-abbrev variant once before bailing
                params["STATE"] = "GA"
                url = base + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
                rows = http_get_json(url, timeout=90)
                if not rows or len(rows) < 2:
                    return {}
            else:
                continue
        header = rows[0]
        if "E_COMMODITY" not in header or "ALL_VAL_MO" not in header:
            print(f"        ⚠ unexpected commodity header: {header}", file=sys.stderr)
            continue
        ci = header.index("E_COMMODITY")
        vi = header.index("ALL_VAL_MO")
        di = header.index("E_COMMODITY_LDESC") if "E_COMMODITY_LDESC" in header else None
        for r in rows[1:]:
            if len(r) <= max(ci, vi):
                continue
            code = (r[ci] or "").strip()
            if not code or code in ("-", "TOTAL"):
                continue
            try:
                v = float(r[vi])
            except (TypeError, ValueError):
                continue
            desc = r[di] if di is not None and len(r) > di else None
            ent = by_chapter.setdefault(code, {"name": _hs2_name(code, desc), "value": 0.0})
            ent["value"] += v
            got_any = True
    return by_chapter if got_any else {}


def build_exports_by_commodity(year, prev_year=None, top_n=10):
    """Top HS2 chapters for `year` with share and (optional) YoY. None on failure."""
    print(f"\n[Census] Building HS2 commodity breakdown for {year}...")
    cur = _fetch_commodity_year(year)
    if not cur:
        print(f"  → no commodity data for {year}", file=sys.stderr)
        return None
    prev = _fetch_commodity_year(prev_year) if prev_year else {}

    total = sum(c["value"] for c in cur.values())
    if total <= 0:
        return None

    ranked = sorted(cur.items(), key=lambda kv: -kv[1]["value"])
    chapters = []
    for code, ent in ranked[:top_n]:
        pv = prev.get(code, {}).get("value") if prev else None
        yoy = round((ent["value"] - pv) / pv * 100, 1) if pv else None
        chapters.append({
            "hs2": code,
            "name": ent["name"],
            "value_musd": round(ent["value"] / 1e6, 0),
            "share_pct": round(ent["value"] / total * 100, 1),
            "yoy_pct": yoy,
        })
    other = total - sum(c["value"] for code, c in ranked[:top_n])
    return {
        "year": year,
        "chapters": chapters,
        "other_musd": round(max(other, 0) / 1e6, 0),
        "total_musd": round(total / 1e6, 0),
    }


# ---------- BLS CES — ATL MSA T&W employment ----------
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# BLS CES MSA series ID format (20 chars total):
#   SM[U/S] + state_FIPS(2) + area_code(5) + industry_code(8) + datatype(2)
# Atlanta-Sandy Springs-Alpharetta MSA = BLS area code 12060.
# We try several supersector codes in fallback order — BLS does NOT publish every
# supersector at every MSA, especially the more granular "Transportation, Warehousing,
# & Utilities" (43). The combined "Trade, Transportation, & Utilities" (40) is broader
# but reliably published.
ATL_SERIES_TRIES = [
    # Format: (label, series_id) — tried in order, first one with data wins
    ("T&W+Util (43) SA",  "SMS13120604300000001"),
    ("T&W+Util (43) NSA", "SMU13120604300000001"),
    ("Trade+T+U (40) SA", "SMS13120604000000001"),
    ("Trade+T+U (40) NSA","SMU13120604000000001"),
]

def fetch_bls_atl_tw_employment(months=80):
    """Returns sorted list of [YYYY-MM, employment_thousands] for ATL MSA transportation
    sector. Tries multiple series IDs in fallback order so we use the most-specific
    one BLS actually publishes for ATL."""
    end_year = TODAY.year
    start_year = end_year - 7

    series_ids = [s[1] for s in ATL_SERIES_TRIES]
    payload = {
        "seriesid": series_ids,
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
    chosen = None; chosen_label = None
    for label, sid in ATL_SERIES_TRIES:
        s = by_id.get(sid)
        if s and s.get("data"):
            chosen = s; chosen_label = label
            print(f"  → BLS T&W: using {label} ({sid}) — {len(s['data'])} obs", file=sys.stderr)
            break
        else:
            print(f"  → BLS T&W: {label} ({sid}) returned no data", file=sys.stderr)

    if not chosen:
        print(f"  → No BLS data for ANY ATL T&W series tried", file=sys.stderr)
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


# ---------- Georgia Ports Authority — latest monthly throughput (best-effort) ----------
# GPA publishes the latest month's Port of Savannah TEUs and Brunswick auto/RoRo
# units in press releases ("...534,037 TEUs in August, up 9 percent..."). We pull
# the latest figure via Tavily and update ONLY the most recent point + the headline
# KPIs, preserving the calibrated historical series. Everything degrades to the
# prior value, so a miss can never blank the page. No Tavily key → skipped entirely.
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}

RE_TEU   = re.compile(r"(\d{3},\d{3}|\d{6})\s*(?:twenty-foot|TEU)", re.IGNORECASE)
RE_AUTOS = re.compile(r"([\d,]{4,7})\s*(?:units|vehicles|autos)", re.IGNORECASE)
RE_MONTH = re.compile(r"\b(January|February|March|April|May|June|July|August|"
                      r"September|October|November|December)\b", re.IGNORECASE)
RE_YOY   = re.compile(r"(up|down|rose|fell|fall\w*|increas\w*|decreas\w*|declin\w*|grew|grow\w*|"
                      r"gain\w*|drop\w*|jump\w*)\s*(?:by|of|to)?\s*(\d{1,2}(?:\.\d)?)\s*percent",
                      re.IGNORECASE)


def _tavily_search(query, *, max_results=6, time_range="month"):
    if not TAVILY_API_KEY:
        return {}
    payload = {
        "query": query, "search_depth": "advanced", "max_results": max_results,
        "include_answer": "advanced", "include_domains": ["gaports.com"],
    }
    if time_range:
        payload["time_range"] = time_range
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        TAVILY_SEARCH_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TAVILY_API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"      [Tavily] search failed: {type(e).__name__}: {e}", file=sys.stderr)
        return {}


def _tavily_texts(resp):
    """Flatten a Tavily response into (text, published_date) snippets to scan."""
    out = []
    if resp.get("answer"):
        out.append((resp["answer"], None))
    for r in resp.get("results", []) or []:
        txt = " ".join(filter(None, [r.get("title"), r.get("content")]))
        if txt:
            out.append((txt, r.get("published_date")))
    return out


def _ym_from(month_idx, published_date):
    """Resolve a Month name + a result's publish date to 'YYYY-MM'.
    The figure usually refers to the month named; pick the year from the publish
    date (or today), rolling back if that would land in the future."""
    yr = TODAY.year
    if published_date and len(published_date) >= 4 and published_date[:4].isdigit():
        yr = int(published_date[:4])
    cand = date(yr, month_idx, 1)
    if cand > TODAY.replace(day=1):
        cand = date(yr - 1, month_idx, 1)
    return f"{cand.year}-{cand.month:02d}"


def _extract_latest(resp, number_re, lo_k, hi_k):
    """Scan Tavily snippets for the freshest '<number> <unit> in <Month> ... up/down X percent'.
    Returns (ym, value_k, yoy_pct) with value in thousands, or None. Range-guarded."""
    best = None  # (ym, value_k, yoy_pct)
    for text, pub in _tavily_texts(resp):
        nm = number_re.search(text)
        mm = RE_MONTH.search(text)
        if not nm or not mm:
            continue
        try:
            val = float(nm.group(1).replace(",", ""))
        except ValueError:
            continue
        val_k = round(val / 1000.0, 1)
        if not (lo_k <= val_k <= hi_k):       # implausible → skip
            continue
        ym = _ym_from(_MONTHS[mm.group(1).lower()], pub)
        yoy = None
        ym_match = RE_YOY.search(text)
        if ym_match:
            sign = -1 if re.match(r"(down|fell|fall|decreas|declin|drop)", ym_match.group(1), re.I) else 1
            yoy = round(sign * float(ym_match.group(2)), 1)
        if best is None or ym > best[0]:
            best = (ym, val_k, yoy)
    return best


def _apply_latest(existing, series_key, kpi_latest, kpi_yoy, found, label):
    """Update a [[ym,val],...] series + KPIs with a freshly-scraped latest point."""
    if not found:
        return False
    ym, val_k, yoy = found
    series = existing.get(series_key) or []
    if series and ym <= series[-1][0]:
        # Not newer than what we already have — just refresh the headline value.
        if ym == series[-1][0]:
            series[-1][1] = val_k
    else:
        series.append([ym, val_k])
    existing[series_key] = series
    kp = existing.setdefault("kpis", {})
    kp[kpi_latest] = val_k
    if yoy is not None:
        kp[kpi_yoy] = yoy
    print(f"  ✓ {label}: {val_k}K for {ym}" + (f" ({yoy:+.1f}% YoY)" if yoy is not None else ""))
    return True


def update_ports(existing):
    """Best-effort refresh of Savannah TEU + Brunswick autos latest points from GPA."""
    if not TAVILY_API_KEY:
        print("  [ports] no TAVILY_API_KEY; keeping calibrated port series", file=sys.stderr)
        return []
    live = []
    print("\n[GPA/Tavily] Port of Savannah latest monthly TEUs...")
    sav = _extract_latest(
        _tavily_search("Port of Savannah container volume TEUs latest month"),
        RE_TEU, lo_k=200, hi_k=700)
    if _apply_latest(existing, "savannah_teu_k", "savannah_teu_latest", "savannah_teu_yoy", sav, "Savannah TEU"):
        live.append("Savannah TEU (GPA)")

    print("[GPA/Tavily] Port of Brunswick latest monthly auto units...")
    bru = _extract_latest(
        _tavily_search("Port of Brunswick Colonel's Island RoRo auto units latest month"),
        RE_AUTOS, lo_k=20, hi_k=120)
    if _apply_latest(existing, "brunswick_autos_k", "brunswick_autos_latest", "brunswick_autos_yoy", bru, "Brunswick autos"):
        live.append("Brunswick autos (GPA)")

    return live


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

    # 1b. Multi-year total-exports trend + HS2 commodity breakdown (Phase 4 WS3).
    # Each is wrapped so a Census hiccup preserves the prior value and leaves the
    # rest of trade.json live. Uses the same year that the country table resolved.
    meta = existing.get("_meta", {}) or {}
    try:
        annual = build_exports_annual(latest_export_year, years_back=6)
    except Exception as e:
        print(f"  [trade] exports_annual raised {type(e).__name__}: {e}", file=sys.stderr)
        annual = None
    if annual:
        existing["exports_annual"] = annual
        meta["exports_annual"] = {"last_updated": _now_iso()}
        print(f"  ✓ exports_annual: {annual['years'][0]}–{annual['years'][-1]}, "
              f"latest ${annual['latest_total_musd']:,.0f}M (CAGR {annual['cagr_pct']}%)")
    elif "exports_annual" not in existing:
        print(f"  ✗ exports_annual unavailable; no prior to preserve", file=sys.stderr)

    try:
        commodity = build_exports_by_commodity(latest_export_year, prev_year=latest_export_year - 1)
    except Exception as e:
        print(f"  [trade] exports_by_commodity raised {type(e).__name__}: {e}", file=sys.stderr)
        commodity = None
    if commodity:
        existing["exports_by_commodity"] = commodity
        meta["exports_by_commodity"] = {"last_updated": _now_iso()}
        top = commodity["chapters"][0] if commodity["chapters"] else None
        if top:
            print(f"  ✓ exports_by_commodity: top chapter = {top['name']} ({top['share_pct']}%)")
    elif "exports_by_commodity" not in existing:
        print(f"  ✗ exports_by_commodity unavailable; no prior to preserve", file=sys.stderr)

    existing["_meta"] = meta

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

    # 3b. Best-effort GPA latest-month port throughput (Savannah TEU + Brunswick autos)
    try:
        ports_live = update_ports(existing)
    except Exception as e:
        print(f"  [trade] update_ports raised {type(e).__name__}: {e}", file=sys.stderr)
        ports_live = []

    # 4. Mark partial-live status
    notes = []
    if top10: notes.append("Census USA Trade Online (GA exports)")
    if tw:    notes.append("BLS CES (ATL MSA T&W employment)")
    notes += ports_live
    if notes:
        existing["_fixture"] = False
        still_fixture = []
        if "Savannah TEU (GPA)" not in ports_live: still_fixture.append("Port of Savannah TEU")
        if "Brunswick autos (GPA)" not in ports_live: still_fixture.append("Brunswick autos")
        still_fixture.append("ATL Hartsfield cargo")   # no clean public source
        existing["_note"] = (
            "Live data: " + ", ".join(notes) + ". "
            + ("Calibrated estimate (latest GPA point may not have refreshed): "
               + ", ".join(still_fixture) + "." if still_fixture else "")
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
