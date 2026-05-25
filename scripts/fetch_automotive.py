"""Pull GA automotive & EV industry data from BLS QCEW, BEA Regional, Census CBP, and Tavily.

Output: data/automotive.json (replaces fixture once a live section lands).

Data sources by section:
  • employment trend (12 yrs)        — BLS QCEW annual CSV slices, NAICS 3361+3362+3363, GA (area 13000)
                                       (motor vehicle manufacturing + bodies/trailers + parts, summed)
  • state comparison (latest year)   — BEA Regional SAGDP2N, NAICS 3361MV ("Motor vehicles, bodies and
                                       trailers, and parts manufacturing"), all states
  • industry GDP trend (12 yrs)      — BEA Regional SAGDP2N, same LineCode, GA only timeseries
  • establishment counts             — Census County Business Patterns (CBP), NAICS 3361/3362/3363, GA
                                       (latest available year — typically lags ~18 months)
  • plant milestones + investment $  — Tavily search → state press releases, AJC, Reuters, etc.
                                       (best-effort; preserves seed list if Tavily returns nothing useful)
  • plants (static-ish list)         — Manually curated card list; rarely changes

Graceful degradation:
  Each section fetcher catches exceptions and preserves the prior value from the
  existing JSON, leaving _meta.<section>.last_updated unchanged. The page renders
  "as of MMM YYYY" badges + a top staleness banner when any section is > 6 months stale.

Environment:
  BLS_API_KEY     — not needed for QCEW CSV slices (public), kept for symmetry
  BEA_API_KEY     — required for BEA Regional API (free; apps.bea.gov/API/signup)
  CENSUS_API_KEY  — optional for CBP; rate limits are very loose without it
  TAVILY_API_KEY  — required for plant milestones / investment scraping (tavily.com)
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
CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "").strip()

TODAY = date.today()
TODAY_ISO = TODAY.isoformat()

# 12-year window. QCEW annuals lag ~6-9 months, so the most recent
# complete year is usually (current year - 1).
END_YEAR   = TODAY.year - 1
START_YEAR = END_YEAR - 11

OUT_PATH = Path(__file__).parent.parent / "data" / "automotive.json"

# NAICS codes for the auto manufacturing complex:
#   3361 = Motor Vehicle Manufacturing (final assembly)
#   3362 = Motor Vehicle Body & Trailer Manufacturing
#   3363 = Motor Vehicle Parts Manufacturing
# The page sums these to get the broad "auto manufacturing" employment number.
NAICS_AUTO = ("3361", "3362", "3363")


# ---------------------------------------------------------------------------
# HTTP helpers (copied from fetch_film.py for symmetry)
# ---------------------------------------------------------------------------
def http_get(url, timeout=30, retries=3, headers=None):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
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
# BLS QCEW — annual NAICS 3361+3362+3363 employment for GA (area 13000)
# ---------------------------------------------------------------------------
QCEW_AREA_URL = "https://data.bls.gov/cew/data/api/{year}/a/area/13000.csv"
GA_AREA_FIPS = "13000"


def fetch_qcew_auto_employment(start_year, end_year):
    """For each year in [start, end], pull GA's QCEW annual CSV, sum
    annual_avg_emplvl across NAICS 3361, 3362, 3363, return
    [(year, total_emp_in_thousands)].

    Skips years that 404 (current year typically isn't published until summer).
    Aborts the remaining years if we get a non-404 failure on year #1 (proxy/DNS
    issue — no point burning ~3 minutes on retries).
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
            if "404" in err_str:
                print(f"      [QCEW {yr}] not yet published. Skipping.", file=sys.stderr)
                continue
            print(f"      [QCEW {yr}] fetch failed ({e}). Aborting QCEW.", file=sys.stderr)
            if not results:
                fatal_network_error = True
            continue

        text = raw.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))

        # We want one row per NAICS. Prefer own_code=0 (Total covered);
        # fall back to own_code=5 (Private), which dominates auto manufacturing.
        # Keyed by (naics) so we don't double-count.
        chosen = {n: None for n in NAICS_AUTO}
        for row in reader:
            ic = row.get("industry_code", "").strip()
            if ic not in NAICS_AUTO:
                continue
            own = row.get("own_code", "").strip()
            if own not in ("0", "5"):
                continue
            cur = chosen[ic]
            # Prefer own_code=0 (total) over own_code=5 (private)
            if cur is None or own == "0":
                chosen[ic] = row

        total_emp = 0.0
        per_naics = {}
        any_found = False
        for naics, row in chosen.items():
            if not row:
                continue
            try:
                v = float(row.get("annual_avg_emplvl", "0"))
            except ValueError:
                continue
            per_naics[naics] = round(v, 0)
            total_emp += v
            any_found = True

        if not any_found:
            print(f"      [QCEW {yr}] no auto NAICS rows found. Skipping.", file=sys.stderr)
            continue

        total_k = round(total_emp / 1000.0, 2)
        results.append((yr, total_k, per_naics))
        breakdown = "  ".join(f"{n}={int(per_naics.get(n,0)):,}" for n in NAICS_AUTO)
        print(f"      [QCEW {yr}] auto total = {total_emp:,.0f} ({total_k:.2f}K)  ({breakdown})")
    return results


# ---------------------------------------------------------------------------
# BEA Regional API — SAGDP2N, "Motor vehicles, bodies and trailers, and parts mfg"
# ---------------------------------------------------------------------------
BEA_URL = "https://apps.bea.gov/api/data"

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
    p = dict(params)
    p["UserID"] = BEA_API_KEY
    p["ResultFormat"] = "JSON"
    url = BEA_URL + "?" + urllib.parse.urlencode(p)
    raw = http_get(url)
    j = json.loads(raw.decode("utf-8"))
    results = j.get("BEAAPI", {}).get("Results", {})
    if isinstance(results, dict) and results.get("Error"):
        raise RuntimeError(f"BEA error: {results['Error']}")
    return results


def bea_find_motor_vehicle_linecode():
    """Find the SAGDP2N LineCode whose description is the auto-manufacturing aggregate:
    'Motor vehicles, bodies and trailers, and parts manufacturing'
    (NAICS 3361-3363 rolled up — the only motor-vehicle line published in SAGDP2N).
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

    # Prefer exact 'Motor vehicles, bodies and trailers' phrase
    candidates = []
    for v in values:
        desc = (v.get("Desc") or "").lower()
        key = str(v.get("Key", "")).strip()
        if not key:
            continue
        if "motor vehicle" in desc and "trailer" in desc:
            candidates.append((0, key, desc))   # best
        elif "motor vehicle" in desc:
            candidates.append((1, key, desc))   # acceptable

    if not candidates:
        raise RuntimeError("Could not find a 'Motor vehicles…' LineCode in SAGDP2N")

    candidates.sort()
    rank, key, desc = candidates[0]
    print(f"      [BEA] LineCode for motor vehicles: {key} → \"{desc}\"")
    return key


def bea_fetch_sagdp2n_series(line_code, geo_fips, years):
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
        raw_val = str(row.get("DataValue", "0")).replace(",", "").strip()
        # BEA suppresses small/sensitive values with "(D)" — skip those
        if raw_val in ("(D)", "(L)", "(NA)", ""):
            continue
        try:
            val = float(raw_val)
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
# Census CBP — establishment counts for NAICS 3361/3362/3363 in GA
# ---------------------------------------------------------------------------
CENSUS_CBP_URL = "https://api.census.gov/data/{year}/cbp"


def fetch_cbp_establishments(year):
    """Census County Business Patterns: total establishments for each auto NAICS
    in Georgia (state-level), returning {naics: estab_count}. CBP lags ~18-24
    months; we walk back a few years if the most recent isn't published.
    """
    out = {}
    params_base = {
        "get": "ESTAB,NAICS2017_LABEL,NAICS2017",
        "for": "state:13",
    }
    if CENSUS_API_KEY:
        params_base["key"] = CENSUS_API_KEY

    for naics in NAICS_AUTO:
        params = dict(params_base)
        params["NAICS2017"] = naics
        url = CENSUS_CBP_URL.format(year=year) + "?" + urllib.parse.urlencode(params)
        try:
            raw = http_get(url, timeout=20, retries=2)
        except RuntimeError as e:
            if "404" in str(e):
                # That year of CBP isn't published yet — caller will retry older year
                raise
            print(f"      [CBP {year}] NAICS {naics} fetch failed: {e}", file=sys.stderr)
            continue
        try:
            rows = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            continue
        # First row is the header; second row has values
        if len(rows) < 2:
            continue
        try:
            estab = int(rows[1][0])
        except (TypeError, ValueError, IndexError):
            continue
        out[naics] = estab
        print(f"      [CBP {year}] NAICS {naics} establishments = {estab:,}")
    return out


def fetch_cbp_establishments_latest():
    """Try the most recent few years until one returns data."""
    last_err = None
    # Census CBP for {year} is usually published ~Q1 of year+2. So if we're in
    # mid-2026, the most recent year is 2024 (or 2023). Walk back to 2022.
    for yr in (END_YEAR, END_YEAR - 1, END_YEAR - 2):
        try:
            data = fetch_cbp_establishments(yr)
            if data:
                return yr, data
        except RuntimeError as e:
            last_err = e
            print(f"      [CBP] year {yr} unavailable — trying older.", file=sys.stderr)
            continue
    if last_err:
        print(f"      [CBP] all candidate years failed (last: {last_err})", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Tavily — plant milestones, EV investment scraping
# ---------------------------------------------------------------------------
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def tavily_search(query, *, max_results=5, search_depth="advanced",
                  include_answer="advanced", include_raw_content=False,
                  include_domains=None, time_range=None):
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


RE_DOLLARS_B = re.compile(r"\$\s?([0-9]+(?:\.[0-9]+)?)\s*billion", re.IGNORECASE)
RE_DOLLARS_M = re.compile(r"\$\s?([0-9]+(?:\.[0-9]+)?)\s*million", re.IGNORECASE)
RE_JOBS      = re.compile(r"([0-9][0-9,]{2,})\s*(?:jobs|workers|employees|positions)", re.IGNORECASE)


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


def _harvest_jobs(text):
    if not text:
        return None
    m = RE_JOBS.search(text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def fetch_total_ev_investment():
    """Tavily → cumulative announced EV/battery/auto investment in Georgia since 2020.
    Returns (value_b, source_url) or None. Used in the hero stat strip.

    GA officials (DECD, Gov. Kemp) regularly quote a cumulative figure ($24B+ as of
    early 2025) covering Hyundai Metaplant, SK On, Rivian (paused), and supplier
    investments. We look for that consolidated figure rather than summing piecewise.
    """
    queries = [
        f"Georgia electric vehicle investment announced cumulative billion {END_YEAR + 1} EV corridor",
        f"Georgia EV investment total announced since 2020 Hyundai SK battery",
        f"Governor Kemp Georgia EV manufacturing investment {END_YEAR} {END_YEAR + 1} billion",
    ]
    for q in queries:
        resp = tavily_search(
            q,
            include_domains=["georgia.org", "gov.georgia.gov", "ajc.com",
                             "reuters.com", "saportareport.com"],
            include_answer="advanced",
            max_results=5,
            time_range="year",
        )
        answer = resp.get("answer", "")
        val_b = _harvest_billions(answer)
        # Sanity-bound: GA's cumulative EV figure is in the $15-40B range
        if val_b and 10.0 <= val_b <= 60.0:
            url = (resp.get("results") or [{}])[0].get("url")
            print(f"      [EV $] cumulative announced = ${val_b}B  (from: \"{answer[:100]}…\")")
            return (val_b, url)
    print(f"      [EV $] no extractable cumulative figure from Tavily.", file=sys.stderr)
    return None


def fetch_plant_milestone_updates(existing_plants):
    """Look for status changes on the seed plant list. For each plant, run a
    Tavily search; if the answer text contains a status-changing keyword (opened,
    production began, paused, expansion), surface it. Returns dict
    {plant_name: {status_hint, jobs_hint, source_url}}.

    Conservative — only surfaces hints; the page renderer can decide what to do.
    Doesn't rewrite the seed list automatically.
    """
    updates = {}
    if not existing_plants:
        return updates

    for plant in existing_plants[:8]:  # cap calls
        name = plant.get("name") or ""
        city = plant.get("city") or ""
        if not name:
            continue
        q = f"{name} {city} Georgia auto plant {END_YEAR + 1} status update production"
        resp = tavily_search(
            q,
            include_domains=["georgia.org", "gov.georgia.gov", "ajc.com",
                             "reuters.com", "automotivenews.com", "saportareport.com"],
            include_answer="basic",
            max_results=4,
            time_range="year",
        )
        answer = resp.get("answer", "") or ""
        lc = answer.lower()
        hint = None
        if "production" in lc and ("began" in lc or "started" in lc or "commenced" in lc):
            hint = "operating"
        elif "opens" in lc or "opened" in lc or "ribbon cutting" in lc:
            hint = "operating"
        elif "paused" in lc or "delayed" in lc or "halt" in lc:
            hint = "paused"
        elif "broke ground" in lc or "groundbreaking" in lc or "construction" in lc:
            hint = "under-construction"
        elif "announced" in lc:
            hint = "announced"

        if hint:
            jobs = _harvest_jobs(answer)
            src = (resp.get("results") or [{}])[0].get("url")
            updates[name] = {
                "status_hint": hint,
                "jobs_hint": jobs,
                "source_url": src,
                "snippet": answer[:140],
            }
            print(f"      [plant] {name}: hint={hint}  jobs={jobs}  {(src or '')[:60]}")
    return updates


# ---------------------------------------------------------------------------
# Main merge — preserve existing values for any failed section
# ---------------------------------------------------------------------------
def main():
    if OUT_PATH.exists():
        with open(OUT_PATH) as f:
            existing = json.load(f)
    else:
        existing = {}

    meta = dict(existing.get("_meta", {}))
    for section in ("employment", "industry_gdp", "state_comparison",
                    "establishments", "ev_investment", "plants", "milestones"):
        meta.setdefault(section, {"last_updated": None, "source": None})

    out = dict(existing)
    out["fetched_at"] = TODAY_ISO
    out.setdefault("trends", {})
    out.setdefault("kpis", {})

    # ----- 1) BLS QCEW employment (NAICS 3361+3362+3363, GA) -----
    print(f"\n[1/5] BLS QCEW auto manufacturing employment — GA {START_YEAR}-{END_YEAR}:")
    try:
        emp_series = fetch_qcew_auto_employment(START_YEAR, END_YEAR)
        if emp_series:
            years           = [y for y, _, _ in emp_series]
            emp_values      = [v for _, v, _ in emp_series]
            per_naics_last  = emp_series[-1][2]
            out["trends"]["employment_k_years"] = years
            out["trends"]["employment_k"]       = emp_values
            latest_emp = emp_series[-1][1]
            prior_emp  = emp_series[-2][1] if len(emp_series) >= 2 else None
            out["kpis"]["employment_latest_k"] = latest_emp
            out["kpis"]["employment_yoy_pct"]  = round(
                (latest_emp - prior_emp) / prior_emp * 100, 1
            ) if prior_emp else None
            # NAICS breakdown for the latest year — useful for the page subtitle
            out["kpis"]["employment_breakdown"] = {
                k: int(v) for k, v in per_naics_last.items()
            }
            meta["employment"] = {
                "last_updated": TODAY_ISO,
                "source": "BLS QCEW annual averages, NAICS 3361+3362+3363 (motor vehicle mfg + bodies/trailers + parts), GA (area 13000)",
                "coverage_years": [years[0], years[-1]],
            }
            print(f"      OK: {len(years)} years; latest {years[-1]} = {latest_emp}K "
                  f"({out['kpis']['employment_yoy_pct']:+.1f}% YoY)")
        else:
            print("      WARN: BLS QCEW returned no data — preserving existing.", file=sys.stderr)
    except Exception as e:
        print(f"      ERROR: BLS QCEW fetch failed ({e}) — preserving existing.", file=sys.stderr)

    # ----- 2 & 3) BEA SAGDP2N: industry GDP timeseries + state comparison -----
    if not BEA_API_KEY:
        print(f"\n[2-3/5] BEA Regional — SKIPPED (no BEA_API_KEY)", file=sys.stderr)
    else:
        try:
            line_code = bea_find_motor_vehicle_linecode()
        except Exception as e:
            print(f"      ERROR: could not look up BEA LineCode ({e})", file=sys.stderr)
            line_code = None

        if line_code:
            # ----- 2) GA timeseries -----
            print(f"\n[2/5] BEA SAGDP2N motor-vehicle GDP — GA, {START_YEAR}-{END_YEAR}:")
            try:
                ga_years = list(range(START_YEAR, END_YEAR + 1))
                ga_series = bea_fetch_sagdp2n_series(line_code, GA_AREA_FIPS, ga_years)
                if ga_series:
                    years   = [y for y, _ in ga_series]
                    gdp_b   = [round(v / 1000.0, 3) for _, v in ga_series]
                    out["trends"]["years"]    = years
                    out["trends"]["gdp_b"]    = gdp_b
                    latest_b = gdp_b[-1]
                    prior_b  = gdp_b[-2] if len(gdp_b) >= 2 else None
                    out["kpis"]["industry_gdp_latest_b"] = latest_b
                    out["kpis"]["industry_gdp_yoy_pct"]  = round(
                        (latest_b - prior_b) / prior_b * 100, 1
                    ) if prior_b else None
                    meta["industry_gdp"] = {
                        "last_updated": TODAY_ISO,
                        "source": "BEA Regional SAGDP2N — motor vehicles, bodies and trailers, and parts manufacturing, GA",
                        "metric_note": "Industry value-added (GDP). NAICS 3361 + 3362 + 3363 rolled up.",
                        "coverage_years": [years[0], years[-1]],
                        "line_code": line_code,
                    }
                    print(f"      OK: {len(years)} years; latest {years[-1]} = ${latest_b}B GDP")
            except Exception as e:
                print(f"      ERROR: BEA GA timeseries fetch failed ({e})", file=sys.stderr)

            # ----- 3) State comparison -----
            print(f"\n[3/5] BEA SAGDP2N motor-vehicle GDP — all states, latest year:")
            try:
                state_rows = bea_fetch_state_comparison(line_code, END_YEAR)
                if not state_rows and END_YEAR > 2000:
                    print(f"      no data for {END_YEAR}; falling back to {END_YEAR - 1}", file=sys.stderr)
                    state_rows = bea_fetch_state_comparison(line_code, END_YEAR - 1)
                if state_rows:
                    # Show top 10 + always include GA if it's outside top 10
                    top = state_rows[:10]
                    has_ga = any(r["abbr"] == "GA" for r in top)
                    if not has_ga:
                        ga_row = next((r for r in state_rows if r["abbr"] == "GA"), None)
                        if ga_row:
                            top = top[:9] + [ga_row]
                    state_comparison = []
                    for i, r in enumerate(state_rows):
                        if r not in top:
                            continue
                        gdp_b = round(r["value_m_usd"] / 1000.0, 2)
                        # GA gets the peach-deep, the leader gets navy, #2 teal,
                        # everyone else neutral khaki.
                        color = ("#c46b3a" if r["abbr"] == "GA"
                                 else "#1a3a5c" if i == 0
                                 else "#3a8d8d" if i == 1
                                 else "#9b8b6a")
                        state_comparison.append({
                            "state": r["state"], "abbr": r["abbr"],
                            "gdp_b": gdp_b, "rank": i + 1, "color": color,
                        })
                    state_comparison.sort(key=lambda r: -r["gdp_b"])
                    out["state_comparison"] = state_comparison
                    ga_rank = next((i + 1 for i, x in enumerate(state_rows)
                                    if x["abbr"] == "GA"), None)
                    if ga_rank:
                        out["kpis"]["national_rank"] = ga_rank
                    meta["state_comparison"] = {
                        "last_updated": TODAY_ISO,
                        "source": "BEA Regional SAGDP2N — motor-vehicle manufacturing GDP by state",
                        "metric_label": "Industry GDP (value-added, $B)",
                        "year": END_YEAR if state_rows else END_YEAR - 1,
                    }
                    print(f"      OK: {len(state_comparison)} states in comparison; "
                          f"GA = #{out['kpis'].get('national_rank', '?')}")
            except Exception as e:
                print(f"      ERROR: BEA state comparison failed ({e})", file=sys.stderr)

    # ----- 4) Census CBP — establishment counts (state-level) -----
    print(f"\n[4/5] Census CBP — establishments by NAICS, GA:")
    try:
        cbp = fetch_cbp_establishments_latest()
        if cbp:
            cbp_year, by_naics = cbp
            out["kpis"]["establishments_latest"] = sum(by_naics.values())
            out["kpis"]["establishments_breakdown"] = by_naics
            meta["establishments"] = {
                "last_updated": TODAY_ISO,
                "source": f"Census CBP {cbp_year} — NAICS 3361/3362/3363, GA",
                "year": cbp_year,
            }
            print(f"      OK: {cbp_year}: {sum(by_naics.values()):,} total establishments  "
                  f"({by_naics})")
    except Exception as e:
        print(f"      ERROR: Census CBP failed ({e}) — preserving existing.", file=sys.stderr)

    # ----- 5) Tavily: EV cumulative investment + plant milestone hints -----
    if not TAVILY_API_KEY:
        print(f"\n[5/5] Tavily — SKIPPED (no TAVILY_API_KEY)", file=sys.stderr)
    else:
        print(f"\n[5/5] Tavily — cumulative EV $ investment + plant milestone hints:")

        try:
            ev = fetch_total_ev_investment()
            if ev:
                val_b, src = ev
                out["kpis"]["ev_investment_total_b"] = val_b
                meta["ev_investment"] = {
                    "last_updated": TODAY_ISO,
                    "source": "Tavily → DECD / Gov. office / AJC cumulative EV investment figure",
                    "value_b": val_b,
                    "source_url": src,
                }
        except Exception as e:
            print(f"      EV $ scrape error: {e}", file=sys.stderr)

        try:
            existing_plants = out.get("plants", existing.get("plants", []))
            plant_updates = fetch_plant_milestone_updates(existing_plants)
            if plant_updates:
                # Attach hint dict to each plant in-place. The page can choose
                # to display the source_url; we do NOT rewrite the seed status
                # silently — that requires human review.
                for plant in existing_plants:
                    nm = plant.get("name")
                    if nm and nm in plant_updates:
                        plant["_hint"] = plant_updates[nm]
                out["plants"] = existing_plants
                meta["plants"] = {
                    "last_updated": TODAY_ISO,
                    "source": "Tavily → AJC / Reuters / state press; hints only, not status overwrites",
                    "hint_count": len(plant_updates),
                }
        except Exception as e:
            print(f"      plant milestones error: {e}", file=sys.stderr)

    # ----- Static-ish: plants seed list + milestones list (preserve from JSON) -----
    out["plants"]     = out.get("plants",     existing.get("plants",     []))
    out["milestones"] = out.get("milestones", existing.get("milestones", []))
    # Derived KPIs — count by status
    operating = sum(1 for p in out["plants"] if (p.get("status") or "").lower() == "operating")
    announced = sum(1 for p in out["plants"] if (p.get("status") or "").lower() in ("announced", "under-construction"))
    out["kpis"]["n_plants_operating"] = operating
    out["kpis"]["n_plants_announced"] = announced
    out["kpis"]["n_plants_total"]     = len(out["plants"])

    # ----- Finalize -----
    out["_meta"]       = meta
    out["latest_year"] = (
        (out["trends"].get("years", [None])[-1])
        or (out["trends"].get("employment_k_years", [None])[-1])
        or END_YEAR
    )
    live_sections = [k for k, v in meta.items() if v.get("last_updated") == TODAY_ISO]
    out["source_summary"] = (
        f"Live sources updated {TODAY_ISO}: {', '.join(live_sections) or '(none)'}. "
        f"Stale or static: {', '.join(k for k in meta if k not in live_sections) or '(none)'}."
    )
    if live_sections:
        out["_fixture"] = False
    else:
        out.setdefault("_fixture", existing.get("_fixture", True))

    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {OUT_PATH}")
    print(f"  Live sections this run: {live_sections}")
    print(f"  Latest year: {out['latest_year']}")
    print(f"  KPIs: {json.dumps({k: v for k, v in out['kpis'].items() if v is not None}, indent=2, default=str)}")


if __name__ == "__main__":
    main()
