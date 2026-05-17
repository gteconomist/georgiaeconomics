"""Pull GA film industry data from BLS QCEW, BEA Regional, and Tavily.

Output: data/film.json (replaces fixture).

Data sources by section:
  • employment trend (12 yrs)        — BLS QCEW annual CSV slices, NAICS 5121, GA (FIPS 13000)
  • state comparison (latest year)   — BEA Regional SAGDP2N, NAICS 512 (Motion picture & sound recording), all states
  • production spend trend           — BEA Regional SAGDP2N, NAICS 512, GA only, 12-yr timeseries
                                       (industry GDP — comparable, not the DECD "production spend" $5.5B figure)
  • production spend HEADLINE only   — Tavily search → DECD annual press release (latest year)
  • tax credits issued (latest year) — Tavily search → GA Department of Audits & Accounts report
  • notable productions              — Tavily search → GA Film Office announcements
  • major studios                    — STATIC (rarely changes); preserved from existing JSON

Graceful degradation:
  Each section fetcher returns (value, ok). If ok=False, we preserve the
  prior value from the existing JSON and DO NOT bump that section's
  _meta.last_updated. The page renders a "stale" badge when a section is
  > 6 months out of date.

Environment:
  BLS_API_KEY   — not needed for QCEW CSV slices (public), but kept for symmetry
  BEA_API_KEY   — required for BEA Regional API (free; apps.bea.gov/API/signup)
  TAVILY_API_KEY — required for DECD / DOAA / Film Office scraping (tavily.com)
"""

import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date

# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------
BEA_API_KEY    = os.environ.get("BEA_API_KEY",    "").strip()
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()

TODAY = date.today()
TODAY_ISO = TODAY.isoformat()

# 12-year window
END_YEAR   = TODAY.year - 1  # Most recent complete calendar year (QCEW annuals lag ~6-9 months)
START_YEAR = END_YEAR - 11

OUT_PATH = Path(__file__).parent.parent / "data" / "film.json"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def http_get(url, timeout=30, retries=3, headers=None):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            # Don't retry on 4xx — server response is authoritative
            if 400 <= e.code < 500:
                raise RuntimeError(f"GET {url} → HTTP {e.code}") from e
            last_err = e
            time.sleep(1 + attempt)
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"GET {url} failed after {retries} retries: {last_err}")


def http_post_json(url, payload, headers=None, timeout=30, retries=3):
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            last_err = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"POST {url} failed after {retries} retries: {last_err}")


# ---------------------------------------------------------------------------
# BLS QCEW — annual NAICS 5121 employment for GA (FIPS 13000)
# ---------------------------------------------------------------------------
# QCEW publishes one CSV per (year, quarter) per area. For annual averages,
# the "qtr" param in the URL is the literal letter 'a'.
QCEW_AREA_URL = "https://data.bls.gov/cew/data/api/{year}/a/area/13000.csv"
GA_AREA_FIPS = "13000"
NAICS_5121 = "5121"

def fetch_qcew_employment_naics5121(start_year, end_year):
    """For each year in [start, end], download GA's QCEW annual CSV, filter to
    NAICS 5121, return list of (year, annual_avg_emplvl_in_thousands).
    Skips years that 404 (current year typically isn't published until summer).
    Aborts the whole range if we get a network/proxy failure on the FIRST year —
    no point burning ~3 minutes on retries when the host can't reach data.bls.gov.
    """
    results = []
    fatal_network_error = False
    for yr in range(start_year, end_year + 1):
        if fatal_network_error:
            break
        url = QCEW_AREA_URL.format(year=yr)
        try:
            raw = http_get(url, timeout=15, retries=2)
        except RuntimeError as e:
            err_str = str(e)
            # 404 just means that year isn't published yet — keep going.
            if "404" in err_str:
                print(f"      [QCEW {yr}] not yet published. Skipping.", file=sys.stderr)
                continue
            # Other failures (403, proxy, DNS) on the first year are usually fatal —
            # don't waste time on the remaining 11 years.
            print(f"      [QCEW {yr}] fetch failed ({e}). Aborting QCEW.", file=sys.stderr)
            if not results:
                fatal_network_error = True
            continue
        text = raw.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        # Prefer own_code=0 (Total covered, all ownership) which is the standard agg level.
        # Fall back to own_code=5 (Private) which dominates motion picture.
        best_row = None
        for row in reader:
            if row.get("industry_code", "").strip() != NAICS_5121:
                continue
            own = row.get("own_code", "").strip()
            if own not in ("0", "5"):
                continue
            # Prefer own_code=0
            if best_row is None or own == "0":
                best_row = row
                if own == "0":
                    break
        if best_row is None:
            print(f"      [QCEW {yr}] NAICS 5121 row not found in GA CSV. Skipping.", file=sys.stderr)
            continue
        try:
            emp_lvl = float(best_row.get("annual_avg_emplvl", "0"))
        except ValueError:
            print(f"      [QCEW {yr}] could not parse annual_avg_emplvl. Skipping.", file=sys.stderr)
            continue
        emp_k = emp_lvl / 1000.0
        results.append((yr, round(emp_k, 1)))
        print(f"      [QCEW {yr}] NAICS 5121 employment = {emp_lvl:,.0f} ({emp_k:.1f}K) "
              f"[own_code={best_row.get('own_code')}]")
    return results


# ---------------------------------------------------------------------------
# BEA Regional API — SAGDP2N (GDP by state), NAICS 512 (Motion picture & sound recording)
# ---------------------------------------------------------------------------
BEA_URL = "https://apps.bea.gov/api/data"

# Each state's GeoFips → 2-letter abbreviation + display name.
# (Limited to the 50 states; territories and BEA regions excluded for the
# state-comparison chart.)
STATE_FIPS = [
    ("01000", "AL", "Alabama"),       ("02000", "AK", "Alaska"),
    ("04000", "AZ", "Arizona"),       ("05000", "AR", "Arkansas"),
    ("06000", "CA", "California"),    ("08000", "CO", "Colorado"),
    ("09000", "CT", "Connecticut"),   ("10000", "DE", "Delaware"),
    ("11000", "DC", "DC"),            ("12000", "FL", "Florida"),
    ("13000", "GA", "Georgia"),       ("15000", "HI", "Hawaii"),
    ("16000", "ID", "Idaho"),         ("17000", "IL", "Illinois"),
    ("18000", "IN", "Indiana"),       ("19000", "IA", "Iowa"),
    ("20000", "KS", "Kansas"),        ("21000", "KY", "Kentucky"),
    ("22000", "LA", "Louisiana"),     ("23000", "ME", "Maine"),
    ("24000", "MD", "Maryland"),      ("25000", "MA", "Massachusetts"),
    ("26000", "MI", "Michigan"),      ("27000", "MN", "Minnesota"),
    ("28000", "MS", "Mississippi"),   ("29000", "MO", "Missouri"),
    ("30000", "MT", "Montana"),       ("31000", "NE", "Nebraska"),
    ("32000", "NV", "Nevada"),        ("33000", "NH", "New Hampshire"),
    ("34000", "NJ", "New Jersey"),    ("35000", "NM", "New Mexico"),
    ("36000", "NY", "New York"),      ("37000", "NC", "North Carolina"),
    ("38000", "ND", "North Dakota"),  ("39000", "OH", "Ohio"),
    ("40000", "OK", "Oklahoma"),      ("41000", "OR", "Oregon"),
    ("42000", "PA", "Pennsylvania"),  ("44000", "RI", "Rhode Island"),
    ("45000", "SC", "South Carolina"),("46000", "SD", "South Dakota"),
    ("47000", "TN", "Tennessee"),     ("48000", "TX", "Texas"),
    ("49000", "UT", "Utah"),          ("50000", "VT", "Vermont"),
    ("51000", "VA", "Virginia"),      ("53000", "WA", "Washington"),
    ("54000", "WV", "West Virginia"), ("55000", "WI", "Wisconsin"),
    ("56000", "WY", "Wyoming"),
]
FIPS_TO_ABBR = {f: a for f, a, _ in STATE_FIPS}
FIPS_TO_NAME = {f: n for f, _, n in STATE_FIPS}


def bea_get(params):
    """Make a GET request to the BEA Regional API. Returns parsed JSON or raises."""
    p = dict(params)
    p["UserID"] = BEA_API_KEY
    p["ResultFormat"] = "JSON"
    url = BEA_URL + "?" + urllib.parse.urlencode(p)
    raw = http_get(url)
    j = json.loads(raw.decode("utf-8"))
    # BEA wraps everything in BEAAPI / Results
    results = j.get("BEAAPI", {}).get("Results", {})
    if isinstance(results, dict) and results.get("Error"):
        raise RuntimeError(f"BEA error: {results['Error']}")
    return results


def bea_find_motion_picture_linecode():
    """Look up the SAGDP2N LineCode whose description matches NAICS 512
    (Motion picture and sound recording industries).
    """
    res = bea_get({
        "method": "GetParameterValuesFiltered",
        "datasetname": "Regional",
        "TargetParameter": "LineCode",
        "TableName": "SAGDP2N",
    })
    values = res.get("ParamValue", []) if isinstance(res, dict) else []
    if not isinstance(values, list):
        values = [values]
    # Find best match — prefer exact "Motion picture and sound recording industries"
    for v in values:
        desc = v.get("Desc", "")
        if "motion picture" in desc.lower() and "sound" in desc.lower():
            print(f"      [BEA] LineCode for NAICS 512: {v.get('Key')} → \"{desc}\"")
            return str(v.get("Key"))
    raise RuntimeError("Could not find LineCode for 'Motion picture and sound recording' in SAGDP2N")


def bea_fetch_sagdp2n_series(line_code, geo_fips, years):
    """Fetch SAGDP2N series for the given LineCode + GeoFips + years.
    Returns list of (year, value_millions_of_dollars).
    SAGDP2N values are reported in millions of current dollars.
    """
    res = bea_get({
        "method": "GetData",
        "datasetname": "Regional",
        "TableName": "SAGDP2N",
        "LineCode": line_code,
        "GeoFips": geo_fips,
        "Year": ",".join(str(y) for y in years),
    })
    data_rows = res.get("Data", []) if isinstance(res, dict) else []
    if isinstance(data_rows, dict):
        data_rows = [data_rows]
    out = []
    for row in data_rows:
        try:
            yr = int(row.get("TimePeriod"))
            val = float(str(row.get("DataValue", "0")).replace(",", ""))
            out.append((yr, val))
        except (TypeError, ValueError):
            continue
    out.sort()
    return out


def bea_fetch_state_comparison(line_code, year):
    """Fetch SAGDP2N for ALL states for a single year. Returns list of
    {fips, abbr, name, value_m_usd} sorted descending by value.
    """
    res = bea_get({
        "method": "GetData",
        "datasetname": "Regional",
        "TableName": "SAGDP2N",
        "LineCode": line_code,
        "GeoFips": "STATE",
        "Year": str(year),
    })
    data_rows = res.get("Data", []) if isinstance(res, dict) else []
    if isinstance(data_rows, dict):
        data_rows = [data_rows]
    rows = []
    for row in data_rows:
        fips = row.get("GeoFips", "")
        if fips not in FIPS_TO_ABBR:
            continue
        try:
            val = float(str(row.get("DataValue", "0")).replace(",", ""))
        except (TypeError, ValueError):
            continue
        rows.append({
            "fips": fips,
            "abbr": FIPS_TO_ABBR[fips],
            "state": FIPS_TO_NAME[fips],
            "value_m_usd": round(val, 1),
        })
    rows.sort(key=lambda r: -r["value_m_usd"])
    return rows


# ---------------------------------------------------------------------------
# Tavily search — best-effort scraping of DECD, DOAA, GA Film Office
# ---------------------------------------------------------------------------
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def tavily_search(query, *, max_results=5, search_depth="advanced",
                  include_answer="advanced", include_raw_content=False,
                  include_domains=None, time_range=None):
    """Wrapper for Tavily Search API. Returns the parsed JSON response, or {} on error."""
    if not TAVILY_API_KEY:
        return {}
    payload = {
        "query": query,
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": include_answer,
        "include_raw_content": include_raw_content,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    if time_range:
        payload["time_range"] = time_range
    try:
        return http_post_json(
            TAVILY_SEARCH_URL, payload,
            headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
            timeout=45, retries=2,
        )
    except RuntimeError as e:
        print(f"      [Tavily] search failed: {e}", file=sys.stderr)
        return {}


# Patterns used to harvest $-billion / $-million numbers from Tavily's NLP "answer" text.
RE_DOLLARS_B = re.compile(r"\$\s?([0-9]+(?:\.[0-9]+)?)\s*billion", re.IGNORECASE)
RE_DOLLARS_M = re.compile(r"\$\s?([0-9]+(?:\.[0-9]+)?)\s*million", re.IGNORECASE)


def _harvest_billions(text):
    if not text:
        return None
    m = RE_DOLLARS_B.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m = RE_DOLLARS_M.search(text)
    if m:
        try:
            return round(float(m.group(1)) / 1000, 3)
        except ValueError:
            return None
    return None


def fetch_decd_production_spend_latest():
    """Tavily → DECD annual film industry press release for the most recent
    fiscal year's total Georgia production spend. Returns (year, spend_b) or None.

    DECD typically publishes "Georgia film and TV industry generated $X.X billion
    in [FY YYYY]" as a press release on georgia.org each summer.
    """
    queries = [
        f"Georgia Department of Economic Development film industry production spend FY{END_YEAR + 1} billion press release",
        f"Georgia film industry total spend FY{END_YEAR + 1} DECD",
        "Georgia film industry billion fiscal year DECD direct spend annual",
    ]
    for q in queries:
        resp = tavily_search(
            q,
            include_domains=["georgia.org", "gadecd.org"],
            include_answer="advanced",
            max_results=5,
            time_range="year",
        )
        answer = resp.get("answer", "")
        spend_b = _harvest_billions(answer)
        if spend_b:
            # Determine the fiscal year from the answer text if possible
            year_match = re.search(r"FY\s?(20\d{2})", answer)
            yr = int(year_match.group(1)) if year_match else END_YEAR + 1
            print(f"      [DECD] FY{yr} production spend = ${spend_b}B  (from: \"{answer[:100]}…\")")
            return (yr, spend_b)
    print(f"      [DECD] no extractable spend figure from Tavily.", file=sys.stderr)
    return None


def fetch_doaa_tax_credits_latest():
    """Tavily → GA Department of Audits & Accounts for the most recent year's
    film tax credits issued. Returns (year, credits_b) or None.
    """
    queries = [
        f"Georgia film tax credit issued {END_YEAR + 1} Department of Audits Accounts billion",
        f"Georgia film tax credit annual report {END_YEAR + 1} DOAA",
        "Georgia film tax credit total issued billion annual report",
    ]
    for q in queries:
        resp = tavily_search(
            q,
            include_domains=["audits.ga.gov", "georgia.org"],
            include_answer="advanced",
            max_results=5,
            time_range="year",
        )
        answer = resp.get("answer", "")
        credits_b = _harvest_billions(answer)
        if credits_b and credits_b < 3.0:  # sanity check — total credits are in the $1-2B range
            year_match = re.search(r"(?:FY|fiscal year|in)\s?(20\d{2})", answer)
            yr = int(year_match.group(1)) if year_match else END_YEAR + 1
            print(f"      [DOAA] FY{yr} tax credits = ${credits_b}B  (from: \"{answer[:100]}…\")")
            return (yr, credits_b)
    print(f"      [DOAA] no extractable credits figure from Tavily.", file=sys.stderr)
    return None


def fetch_film_office_productions():
    """Tavily → GA Film Office recent production announcements. Returns a list of
    dicts {title, year, studio, type, spend_m, location}. Best-effort; may return
    empty list if Tavily doesn't yield structured info.

    Conservative: we only return entries we can confidently extract.
    """
    queries = [
        f"\"filmed in Georgia\" major productions {END_YEAR + 1} Marvel Netflix",
        f"Georgia Film Office production announcements {END_YEAR} {END_YEAR + 1}",
    ]
    found = []
    seen_titles = set()
    for q in queries:
        resp = tavily_search(
            q,
            include_domains=["georgia.org", "variety.com", "deadline.com", "hollywoodreporter.com"],
            include_answer="basic",
            max_results=10,
            time_range="year",
        )
        for result in resp.get("results", []):
            title_raw = result.get("title", "")
            content   = result.get("content", "")
            # Extract " 'Movie Title' " from the source title
            m = re.search(r"['\"“]([A-Z][A-Za-z0-9 :,\-!?']+)['\"”]", title_raw)
            if not m:
                continue
            movie_title = m.group(1).strip()
            if len(movie_title) < 3 or movie_title.lower() in seen_titles:
                continue
            seen_titles.add(movie_title.lower())
            # Look for spend in the content
            spend_match = RE_DOLLARS_M.search(content)
            spend_m = round(float(spend_match.group(1))) if spend_match else None
            found.append({
                "title": movie_title,
                "year": END_YEAR + 1,
                "studio": "—",
                "type": "Film" if "film" in content.lower() else "TV Series",
                "spend_m": spend_m if spend_m else 0,
                "location": "Georgia",
                "_source_url": result.get("url"),
            })
            if len(found) >= 6:
                break
        if len(found) >= 6:
            break
    if found:
        print(f"      [Film Office] Tavily yielded {len(found)} new production candidates.")
    else:
        print(f"      [Film Office] Tavily yielded no clean production candidates — preserving existing list.", file=sys.stderr)
    return found


# ---------------------------------------------------------------------------
# Merge logic — preserve existing values for any failed section
# ---------------------------------------------------------------------------
def main():
    # Load existing JSON; we mutate it section-by-section.
    if OUT_PATH.exists():
        with open(OUT_PATH) as f:
            existing = json.load(f)
    else:
        existing = {}

    # Initialize _meta if missing — track per-section last_updated
    meta = dict(existing.get("_meta", {}))
    for section in ("production_spend", "tax_credits", "employment",
                    "state_comparison", "notable_productions", "major_studios"):
        meta.setdefault(section, {"last_updated": None, "source": None})

    out = dict(existing)
    out["fetched_at"] = TODAY_ISO
    # _fixture is only cleared if at least one live section landed this run
    # (set after all fetches complete, below).

    out.setdefault("trends", {})
    out.setdefault("kpis", {})

    # ----- 1) BLS QCEW employment (NAICS 5121 GA) -----
    print(f"\n[1/5] BLS QCEW NAICS 5121 employment — GA, {START_YEAR}-{END_YEAR}:")
    try:
        emp_series = fetch_qcew_employment_naics5121(START_YEAR, END_YEAR)
        if emp_series:
            years      = [y for y, _ in emp_series]
            emp_values = [v for _, v in emp_series]
            out["trends"]["employment_k_years"] = years
            out["trends"]["employment_k"]       = emp_values
            latest_emp = emp_series[-1][1]
            prior_emp  = emp_series[-2][1] if len(emp_series) >= 2 else None
            out["kpis"]["employment_latest_k"] = latest_emp
            out["kpis"]["employment_yoy_pct"]  = round(
                (latest_emp - prior_emp) / prior_emp * 100, 1
            ) if prior_emp else None
            meta["employment"] = {
                "last_updated": TODAY_ISO,
                "source": "BLS QCEW annual averages, NAICS 5121, GA (area_fips 13000)",
                "coverage_years": [years[0], years[-1]],
            }
            print(f"      OK: {len(years)} years; latest {years[-1]} = {latest_emp}K "
                  f"({out['kpis']['employment_yoy_pct']:+.1f}% YoY)")
        else:
            print("      WARN: BLS QCEW returned no data — preserving existing employment values.", file=sys.stderr)
    except Exception as e:
        print(f"      ERROR: BLS QCEW fetch failed ({e}) — preserving existing employment values.", file=sys.stderr)

    # ----- 2 & 3) BEA SAGDP2N: GA timeseries + state comparison -----
    if not BEA_API_KEY:
        print(f"\n[2-3/5] BEA Regional — SKIPPED (no BEA_API_KEY)", file=sys.stderr)
    else:
        try:
            line_code = bea_find_motion_picture_linecode()
        except Exception as e:
            print(f"      ERROR: could not look up BEA LineCode ({e})", file=sys.stderr)
            line_code = None

        if line_code:
            # ----- 2) GA timeseries: industry GDP (proxy for "production spend") -----
            print(f"\n[2/5] BEA SAGDP2N NAICS 512 GDP — GA, {START_YEAR}-{END_YEAR}:")
            try:
                ga_years = list(range(START_YEAR, END_YEAR + 1))
                ga_series = bea_fetch_sagdp2n_series(line_code, GA_AREA_FIPS, ga_years)
                if ga_series:
                    years      = [y for y, _ in ga_series]
                    # BEA SAGDP2N is in millions; convert to billions for the chart
                    spend_b    = [round(v / 1000.0, 3) for _, v in ga_series]
                    out["trends"]["years"]              = years
                    out["trends"]["production_spend_b"] = spend_b
                    out["trends"]["tax_credits_b"]      = (
                        out["trends"].get("tax_credits_b") or [None] * len(years)
                    )[-len(years):]
                    # Pad/truncate to match years
                    if len(out["trends"]["tax_credits_b"]) < len(years):
                        out["trends"]["tax_credits_b"] = (
                            [None] * (len(years) - len(out["trends"]["tax_credits_b"]))
                            + out["trends"]["tax_credits_b"]
                        )
                    latest_b = spend_b[-1]
                    prior_b  = spend_b[-2] if len(spend_b) >= 2 else None
                    out["kpis"]["production_spend_latest_b"] = latest_b
                    out["kpis"]["production_spend_yoy_pct"]  = round(
                        (latest_b - prior_b) / prior_b * 100, 1
                    ) if prior_b else None
                    meta["production_spend"] = {
                        "last_updated": TODAY_ISO,
                        "source": "BEA Regional SAGDP2N — NAICS 512 (Motion picture and sound recording industries), GA",
                        "metric_note": "Industry GDP (value-added). Distinct from DECD's gross production spend figure.",
                        "coverage_years": [years[0], years[-1]],
                    }
                    print(f"      OK: {len(years)} years; latest {years[-1]} = ${latest_b}B GDP")
            except Exception as e:
                print(f"      ERROR: BEA GA timeseries fetch failed ({e}) — preserving existing trend.", file=sys.stderr)

            # ----- 3) State comparison: all 50 states for the latest available year -----
            print(f"\n[3/5] BEA SAGDP2N NAICS 512 GDP — all states, latest year:")
            try:
                state_rows = bea_fetch_state_comparison(line_code, END_YEAR)
                if not state_rows and END_YEAR > 2000:
                    # Fall back one year if BEA hasn't published END_YEAR yet
                    print(f"      no data for {END_YEAR}; falling back to {END_YEAR - 1}", file=sys.stderr)
                    state_rows = bea_fetch_state_comparison(line_code, END_YEAR - 1)
                if state_rows:
                    # Convert to billions, take top 8, color GA in peach-deep
                    top = state_rows[:8]
                    has_ga = any(r["abbr"] == "GA" for r in top)
                    if not has_ga:
                        ga_row = next((r for r in state_rows if r["abbr"] == "GA"), None)
                        if ga_row:
                            top = top[:7] + [ga_row]
                    state_comparison = []
                    for i, r in enumerate(state_rows):
                        if r not in top:
                            continue
                        spend_b = round(r["value_m_usd"] / 1000.0, 2)
                        color = ("#c46b3a" if r["abbr"] == "GA"
                                 else "#1a3a5c" if i == 0
                                 else "#3a8d8d" if i == 1
                                 else "#9b8b6a")
                        state_comparison.append({
                            "state": r["state"], "abbr": r["abbr"],
                            "spend_b": spend_b, "rank": i + 1, "color": color,
                        })
                    # Sort the output list by spend desc
                    state_comparison.sort(key=lambda r: -r["spend_b"])
                    out["state_comparison"] = state_comparison
                    # National rank for GA
                    ga_rank = next((r["rank"] for r in [
                        {"abbr": x["abbr"], "rank": i + 1} for i, x in enumerate(state_rows)
                    ] if r["abbr"] == "GA"), None)
                    if ga_rank:
                        out["kpis"]["national_rank"] = ga_rank
                    meta["state_comparison"] = {
                        "last_updated": TODAY_ISO,
                        "source": "BEA Regional SAGDP2N — NAICS 512 (Motion picture & sound recording industries) GDP by state",
                        "metric_label": "Industry GDP (value-added, $B)",
                        "year": END_YEAR if state_rows else END_YEAR - 1,
                    }
                    print(f"      OK: {len(state_comparison)} states in comparison; "
                          f"GA = #{out['kpis'].get('national_rank', '?')}")
            except Exception as e:
                print(f"      ERROR: BEA state comparison failed ({e}) — preserving existing.", file=sys.stderr)

    # ----- 4) Tavily: DECD production spend + DOAA tax credits (current-year point) -----
    if not TAVILY_API_KEY:
        print(f"\n[4/5] Tavily — SKIPPED (no TAVILY_API_KEY)", file=sys.stderr)
    else:
        print(f"\n[4/5] Tavily — DECD production spend + DOAA tax credits + Film Office productions:")

        # DECD's "production spend" headline figure is a separate metric from
        # BEA's NAICS 512 industry GDP. Store it under its own keys so the page
        # can show it alongside (not instead of) the BEA timeseries.
        try:
            decd = fetch_decd_production_spend_latest()
            if decd:
                yr, spend_b = decd
                out["kpis"]["decd_production_spend_b"]   = spend_b
                out["kpis"]["decd_production_spend_fy"]  = yr
                meta["decd_headline"] = {
                    "last_updated": TODAY_ISO,
                    "source": f"Tavily → DECD FY{yr} press release",
                    "value_b": spend_b,
                    "year": yr,
                }
        except Exception as e:
            print(f"      DECD scrape error: {e}", file=sys.stderr)

        # DOAA tax credits
        try:
            doaa = fetch_doaa_tax_credits_latest()
            if doaa:
                yr, credits_b = doaa
                out["kpis"]["tax_credits_latest_b"] = credits_b
                # Append to trends.tax_credits_b at the matching year
                if "years" in out["trends"] and yr in out["trends"]["years"]:
                    idx = out["trends"]["years"].index(yr)
                    tc = list(out["trends"].get("tax_credits_b") or [None] * len(out["trends"]["years"]))
                    if len(tc) < len(out["trends"]["years"]):
                        tc = [None] * (len(out["trends"]["years"]) - len(tc)) + tc
                    tc[idx] = credits_b
                    out["trends"]["tax_credits_b"] = tc
                meta["tax_credits"] = {
                    "last_updated": TODAY_ISO,
                    "source": f"Tavily → GA DOAA FY{yr} report",
                    "latest_year": yr,
                    "latest_value_b": credits_b,
                }
        except Exception as e:
            print(f"      DOAA scrape error: {e}", file=sys.stderr)

        # Film Office productions
        try:
            new_productions = fetch_film_office_productions()
            if new_productions:
                # Merge with existing — dedupe on title, prefer new entries
                existing_prods = existing.get("notable_productions", [])
                titles_seen = {p["title"].lower() for p in new_productions}
                merged = list(new_productions) + [
                    p for p in existing_prods
                    if p.get("title", "").lower() not in titles_seen
                ]
                # Cap at 12 to keep the grid tidy
                out["notable_productions"] = merged[:12]
                out["kpis"]["n_major_productions"] = len(out["notable_productions"])
                meta["notable_productions"] = {
                    "last_updated": TODAY_ISO,
                    "source": "Tavily → GA Film Office + trade press announcements",
                    "new_this_run": len(new_productions),
                }
        except Exception as e:
            print(f"      Film Office scrape error: {e}", file=sys.stderr)

    # ----- 5) Static: major studios (preserve), annotations (preserve) -----
    print(f"\n[5/5] Static sections (preserved from existing JSON):")
    out["major_studios"] = existing.get("major_studios", [])
    out["annotations"]   = existing.get("annotations", [])
    out["kpis"]["n_major_studios"] = len(out["major_studios"])
    if "tax_credit_pct" not in out["kpis"]:
        out["kpis"]["tax_credit_pct"] = 30
    print(f"      Studios: {len(out['major_studios'])}  Annotations: {len(out['annotations'])}")

    # ----- Finalize -----
    out["_meta"]       = meta
    out["latest_year"] = (
        out["kpis"].get("decd_production_spend_fy")
        or (out["trends"].get("years", [None])[-1])
        or END_YEAR
    )
    # source_summary describes what's currently live vs stale
    live_sections = [k for k, v in meta.items() if v.get("last_updated") == TODAY_ISO]
    out["source_summary"] = (
        f"Live sources updated {TODAY_ISO}: {', '.join(live_sections)}. "
        f"Stale or static: {', '.join(k for k in meta if k not in live_sections)}."
    )
    # Clear the seed-data banner only when at least one live section landed.
    # The banner reflects "are we relying on fixtures?" — if even one section
    # is real-sourced, the page is no longer pure fixture.
    if live_sections:
        out["_fixture"] = False
    else:
        out.setdefault("_fixture", existing.get("_fixture", True))

    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {OUT_PATH}")
    print(f"  Live sections this run: {live_sections}")
    print(f"  Latest year: {out['latest_year']}")
    print(f"  KPIs: {json.dumps({k: v for k, v in out['kpis'].items() if v is not None}, indent=2)}")


if __name__ == "__main__":
    main()
