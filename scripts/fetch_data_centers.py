"""Pull GA data-center industry data from BLS QCEW, BEA Regional, Census CBP, and Tavily.

Output: data/data_centers.json (replaces fixture once a live section lands).

Data sources by section:
  • employment trend (12 yrs)        — BLS QCEW annual CSV slices, NAICS 518210
                                       (Data Processing, Hosting, and Related Services), GA (area 13000)
  • wages trend (12 yrs)             — BLS QCEW avg weekly wage, same NAICS, plus all-private GA
  • industry GDP trend (12 yrs)      — BEA Regional SAGDP2, "Information" sector LineCode, GA
                                       (NOTE: broader than data centers — page calls this out)
  • state comparison (latest year)   — BEA Regional SAGDP2, Information sector, all states
  • establishment counts             — Census County Business Patterns (CBP), NAICS 518210, GA
                                       (state-level, lags ~18 months)
  • facilities + counties (static)   — Costar export at data/seeds/costar_data_centers_ga_*.xlsx
                                       Parsed JSON snapshot at data/seeds/costar_data_centers_ga_parsed.json
                                       The fetcher preserves these UNLESS a fresh Costar export is detected.
  • policy / legislation             — Tavily search → legis.ga.gov, gov.georgia.gov, AJC
                                       Active bills list; never silently overwrites the policy timeline.
  • DECD press releases (new sites)  — Tavily search → gov.georgia.gov + decd.georgia.gov + AJC
                                       Surfaced as a _pending_announcements list; advisory only.
  • EPD water permits                — Tavily search → epd.georgia.gov for facility water permits.
                                       Populates water.permitted_* fields; modeled remainder recomputes.
  • PSC load forecast / IRP          — Tavily search → psc.ga.gov Georgia Power IRP filings
                                       Surfaces latest forecast headline; load curves stay seeded.

Graceful degradation:
  Each section fetcher catches exceptions and preserves the prior value from the
  existing JSON, leaving _meta.<section>.last_updated unchanged. The page renders
  "as of MMM YYYY" badges + a top staleness banner when any section is > 6 months stale.

Costar freshness:
  The Costar seed is manual (Alfie re-exports periodically). The fetcher reads the
  parsed JSON and surfaces an "as of [date]" badge on the facilities/counties sections,
  flipping orange when > 9 months old. The fetcher does NOT modify the Costar facilities
  list.

Environment:
  BLS_API_KEY     — not needed for QCEW CSV slices (public), kept for symmetry
  BEA_API_KEY     — required for BEA Regional API (free; apps.bea.gov/API/signup)
  CENSUS_API_KEY  — optional for CBP; rate limits are very loose without it
  TAVILY_API_KEY  — required for policy/DECD/EPD/PSC scraping (tavily.com)
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

ROOT = Path(__file__).parent.parent
OUT_PATH = ROOT / "data" / "data_centers.json"
SEED_DIR = ROOT / "data" / "seeds"
SEED_JSON = SEED_DIR / "costar_data_centers_ga_parsed.json"

# NAICS code: Data Processing, Hosting, and Related Services
# This is the closest BLS/Census NAICS line to "data center industry" — broader
# than physical data centers (includes pure SaaS/cloud ops), but it's the only
# clean public series available. Page subtitle calls out the caveat.
NAICS_DC = "518210"

GA_AREA_FIPS  = "13000"


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
# BLS QCEW — annual NAICS 518210 employment + wages for GA
# ---------------------------------------------------------------------------
QCEW_AREA_URL = "https://data.bls.gov/cew/data/api/{year}/a/area/13000.csv"


def fetch_qcew_dc_employment_and_wages(start_year, end_year):
    """For each year in [start, end], pull GA's QCEW annual CSV, extract NAICS 518210
    employment and avg weekly wage. Also captures all-private (NAICS 10) avg wage for
    the wage-comparison chart. Returns
    [(year, emp_thousands, weekly_wage_dc, weekly_wage_allprivate)].
    """
    results = []
    fatal_network_error = False
    for yr in range(start_year, end_year + 1):
        if fatal_network_error:
            break
        url = QCEW_AREA_URL.format(year=yr)
        try:
            raw = http_get(url, timeout=20, retries=2)
        except RuntimeError as e:
            if "404" in str(e):
                print(f"      [QCEW {yr}] not yet published. Skipping.", file=sys.stderr)
                continue
            print(f"      [QCEW {yr}] fetch failed ({e}). Aborting QCEW.", file=sys.stderr)
            if not results:
                fatal_network_error = True
            continue

        text = raw.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))

        dc_row = None
        ap_row = None  # all-private (NAICS 10)
        for row in reader:
            ic  = row.get("industry_code", "").strip()
            own = row.get("own_code", "").strip()
            if ic == NAICS_DC and own in ("0", "5"):
                # Prefer own_code=5 (private) for 518210 — it's nearly all private
                if dc_row is None or own == "5":
                    dc_row = row
            elif ic == "10" and own == "5":
                ap_row = row

        if not dc_row:
            print(f"      [QCEW {yr}] NAICS {NAICS_DC} not found. Skipping.", file=sys.stderr)
            continue

        try:
            emp = float(dc_row.get("annual_avg_emplvl", "0"))
            # Annual area CSV uses `annual_avg_wkly_wage`, NOT `avg_wkly_wage`
            # (the latter is a quarterly-file field). Earlier draft had the wrong
            # name and silently returned 0 for every year — chart rendered flat.
            wkw_dc = float(dc_row.get("annual_avg_wkly_wage", "0"))
        except (TypeError, ValueError):
            continue

        wkw_ap = None
        if ap_row:
            try:
                wkw_ap = float(ap_row.get("annual_avg_wkly_wage", "0"))
            except (TypeError, ValueError):
                pass

        emp_k = round(emp / 1000.0, 2)
        results.append((yr, emp_k, int(round(wkw_dc)), int(round(wkw_ap)) if wkw_ap else None))
        print(f"      [QCEW {yr}] NAICS 518210: emp={emp:,.0f} ({emp_k:.2f}K), wkly wage=${int(round(wkw_dc)):,}, "
              f"all-private wkly wage=${int(round(wkw_ap)) if wkw_ap else 'NA'}")
    return results


# ---------------------------------------------------------------------------
# BEA Regional API — SAGDP2, Information sector
# ---------------------------------------------------------------------------
BEA_URL = "https://apps.bea.gov/api/data"

STATE_FIPS = [
    ("01000","AL","Alabama"),("02000","AK","Alaska"),("04000","AZ","Arizona"),("05000","AR","Arkansas"),
    ("06000","CA","California"),("08000","CO","Colorado"),("09000","CT","Connecticut"),("10000","DE","Delaware"),
    ("11000","DC","DC"),("12000","FL","Florida"),("13000","GA","Georgia"),("15000","HI","Hawaii"),
    ("16000","ID","Idaho"),("17000","IL","Illinois"),("18000","IN","Indiana"),("19000","IA","Iowa"),
    ("20000","KS","Kansas"),("21000","KY","Kentucky"),("22000","LA","Louisiana"),("23000","ME","Maine"),
    ("24000","MD","Maryland"),("25000","MA","Massachusetts"),("26000","MI","Michigan"),("27000","MN","Minnesota"),
    ("28000","MS","Mississippi"),("29000","MO","Missouri"),("30000","MT","Montana"),("31000","NE","Nebraska"),
    ("32000","NV","Nevada"),("33000","NH","New Hampshire"),("34000","NJ","New Jersey"),("35000","NM","New Mexico"),
    ("36000","NY","New York"),("37000","NC","North Carolina"),("38000","ND","North Dakota"),("39000","OH","Ohio"),
    ("40000","OK","Oklahoma"),("41000","OR","Oregon"),("42000","PA","Pennsylvania"),("44000","RI","Rhode Island"),
    ("45000","SC","South Carolina"),("46000","SD","South Dakota"),("47000","TN","Tennessee"),("48000","TX","Texas"),
    ("49000","UT","Utah"),("50000","VT","Vermont"),("51000","VA","Virginia"),("53000","WA","Washington"),
    ("54000","WV","West Virginia"),("55000","WI","Wisconsin"),("56000","WY","Wyoming"),
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


def bea_find_information_linecode():
    """Find SAGDP2 LineCode whose description is 'Information' (the broader sector
    that contains NAICS 518210). BEA reshuffles codes occasionally, so look up
    dynamically rather than hardcoding.
    """
    res = bea_get({
        "method": "GetParameterValuesFiltered",
        "datasetname": "Regional",
        "TargetParameter": "LineCode",
        "TableName": "SAGDP2",
    })
    values = res.get("ParamValue", []) if isinstance(res, dict) else []
    if not isinstance(values, list):
        values = [values]

    candidates = []
    for v in values:
        raw = (v.get("Desc") or "").strip()
        # SAGDP2 descriptions read "[SAGDP2] Gross domestic product (GDP) by
        # state: Information (51)" — the sector name is after the last ": ".
        desc = raw.split(": ", 1)[-1].strip().lower() if ": " in raw else raw.lower()
        key  = str(v.get("Key", "")).strip()
        if not key:
            continue
        bare = desc.split(" (")[0].strip()   # drop trailing NAICS code, e.g. "(51)"
        # Prefer the bare "Information" sector header over any sub-lines.
        if bare == "information":
            candidates.append((0, key, desc))
        elif desc.startswith("information") or ("information" in desc and "data" in desc):
            candidates.append((1, key, desc))

    if not candidates:
        raise RuntimeError("Could not find an 'Information' LineCode in SAGDP2")

    candidates.sort()
    rank, key, desc = candidates[0]
    print(f"      [BEA] LineCode for Information sector: {key} → \"{desc}\"")
    return key


def bea_fetch_sagdp2n_series(line_code, geo_fips, years):
    res = bea_get({
        "method": "GetData",
        "datasetname": "Regional",
        "TableName": "SAGDP2",
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
            yr  = int(row.get("TimePeriod"))
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
        "TableName": "SAGDP2",
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
# Census CBP — establishment counts for NAICS 518210 in GA
# ---------------------------------------------------------------------------
CENSUS_CBP_URL = "https://api.census.gov/data/{year}/cbp"


def fetch_cbp_establishments(year):
    params = {
        "get": "ESTAB,NAICS2017_LABEL,NAICS2017",
        "for": "state:13",
        "NAICS2017": NAICS_DC,
    }
    if CENSUS_API_KEY:
        params["key"] = CENSUS_API_KEY
    url = CENSUS_CBP_URL.format(year=year) + "?" + urllib.parse.urlencode(params)
    raw = http_get(url, timeout=20, retries=2)
    rows = json.loads(raw.decode("utf-8"))
    if len(rows) < 2:
        return None
    try:
        estab = int(rows[1][0])
    except (TypeError, ValueError, IndexError):
        return None
    print(f"      [CBP {year}] NAICS {NAICS_DC} establishments = {estab:,}")
    return estab


def fetch_cbp_establishments_latest():
    last_err = None
    for yr in (END_YEAR, END_YEAR - 1, END_YEAR - 2):
        try:
            estab = fetch_cbp_establishments(yr)
            if estab is not None:
                return yr, estab
        except RuntimeError as e:
            last_err = e
            print(f"      [CBP] year {yr} unavailable — trying older.", file=sys.stderr)
            continue
    if last_err:
        print(f"      [CBP] all candidate years failed (last: {last_err})", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Tavily — policy, DECD, EPD, PSC searches
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
RE_MW        = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:MW|megawatts?)", re.IGNORECASE)
RE_GWH       = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:GWh|gigawatt[- ]?hours?)", re.IGNORECASE)
RE_GPD       = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:gallons?\s*per\s*day|gpd|mgd)", re.IGNORECASE)
RE_BILL_NUM  = re.compile(r"\b(HB|SB|HR|SR)\s*([0-9]{1,4})\b", re.IGNORECASE)


def _harvest_dollars_m(text):
    if not text:
        return None
    m = RE_DOLLARS_B.search(text)
    if m:
        try:
            return round(float(m.group(1)) * 1000, 1)
        except ValueError:
            return None
    m = RE_DOLLARS_M.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def fetch_decd_new_announcements():
    """Find new GA data-center announcements since the last Costar export date.
    Returns a list of advisory _pending_announcements records — page renders these
    in a peach call-out box. Never modifies the canonical facilities list.
    """
    out = []
    queries = [
        f"Georgia new data center announcement {END_YEAR} {END_YEAR + 1} megawatts site selection",
        f"Georgia data center groundbreaking {END_YEAR + 1} county hyperscale build",
    ]
    seen = set()
    for q in queries:
        resp = tavily_search(
            q,
            include_domains=["gov.georgia.gov", "decd.georgia.gov", "georgia.org",
                             "ajc.com", "saportareport.com", "datacenterdynamics.com",
                             "datacenterfrontier.com"],
            include_answer="advanced",
            max_results=5,
            time_range="month",
        )
        answer = resp.get("answer", "") or ""
        results = resp.get("results") or []
        for r in results:
            url = r.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            title = r.get("title", "")[:140]
            content_snip = (r.get("content") or "")[:240]
            blob = (answer + " " + title + " " + content_snip)
            mw_m = RE_MW.search(blob)
            inv_m = _harvest_dollars_m(blob)
            mw_val = None
            if mw_m:
                try:
                    mw_val = float(mw_m.group(1).replace(",", ""))
                except ValueError:
                    pass
            out.append({
                "title": title,
                "url": url,
                "source_domain": url.split("/")[2] if "//" in url else "",
                "snippet": content_snip,
                "mw_hint": mw_val,
                "investment_m_hint": inv_m,
            })
            if len(out) >= 8:
                break
        if len(out) >= 8:
            break
    print(f"      [DECD] {len(out)} pending announcement candidates surfaced.")
    return out


def fetch_epd_water_permits():
    """Find GA EPD water-withdrawal permits associated with data centers.
    Returns (permitted_facility_count, permitted_mgd, sources).
    """
    queries = [
        "Georgia data center water withdrawal permit EPD permitted gallons",
        f"Georgia EPD industrial water permit data center {END_YEAR}",
    ]
    permits_seen = set()
    total_mgd = 0.0
    sources = []
    for q in queries:
        resp = tavily_search(
            q,
            include_domains=["epd.georgia.gov", "georgia.gov", "ajc.com",
                             "saportareport.com"],
            include_answer="advanced",
            max_results=5,
            time_range="year",
        )
        answer = resp.get("answer", "") or ""
        results = resp.get("results") or []
        for r in results:
            url = r.get("url", "")
            if not url or url in permits_seen:
                continue
            blob = (answer + " " + (r.get("content") or ""))[:1200]
            m = RE_GPD.search(blob)
            if not m:
                continue
            try:
                val = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            unit_blob = m.group(0).lower()
            # Convert "gallons per day" → million gallons per day if needed
            if "mgd" in unit_blob:
                mgd = val
            elif val > 100_000:  # treat as GPD if large
                mgd = val / 1_000_000
            else:
                mgd = val
            if not (0.01 <= mgd <= 100):  # sanity bound per facility
                continue
            permits_seen.add(url)
            total_mgd += mgd
            sources.append({"url": url, "mgd": round(mgd, 3),
                            "snippet": (r.get("content") or "")[:160]})
    print(f"      [EPD] {len(permits_seen)} permits / {round(total_mgd,2)} MGD total.")
    return len(permits_seen), round(total_mgd, 2), sources


def fetch_psc_load_forecast_headline():
    """Tavily → latest Georgia Power IRP / load-forecast headline.
    Returns a short string + source url, attached as _meta.load_forecast.headline.
    """
    queries = [
        f"Georgia Power IRP {END_YEAR} {END_YEAR + 1} load forecast data center gigawatts",
        f"Georgia Public Service Commission Georgia Power load growth data center {END_YEAR}",
    ]
    for q in queries:
        resp = tavily_search(
            q,
            include_domains=["psc.ga.gov", "georgia.gov", "georgiapower.com",
                             "ajc.com", "saportareport.com", "energynewsnetwork.org",
                             "utilitydive.com"],
            include_answer="advanced",
            max_results=5,
            time_range="year",
        )
        answer = (resp.get("answer", "") or "").strip()
        if not answer:
            continue
        # Want something mentioning load growth in GWh or % terms
        if RE_GWH.search(answer) or "load" in answer.lower():
            url = (resp.get("results") or [{}])[0].get("url")
            print(f"      [PSC] headline captured ({len(answer)} chars).")
            return {"text": answer[:600], "source_url": url}
    print(f"      [PSC] no IRP-headline candidate captured.", file=sys.stderr)
    return None


def fetch_active_policy_bills(existing_bills):
    """Tavily → active GA legislative bills on data centers (tax, water, grid).
    Returns updated active_bills list; preserves existing status if Tavily silent.
    """
    queries = [
        f"Georgia legislature data center bill {END_YEAR + 1} sales tax exemption HB SB",
        f"Georgia data center water grid cost bill {END_YEAR + 1} legislation session",
    ]
    bills_found = {}
    for q in queries:
        resp = tavily_search(
            q,
            include_domains=["legis.ga.gov", "ajc.com", "saportareport.com",
                             "gpb.org", "georgiarecorder.com"],
            include_answer="advanced",
            max_results=5,
            time_range="month",
        )
        for r in (resp.get("results") or []):
            title = (r.get("title", "") or "")[:200]
            content = (r.get("content", "") or "")[:600]
            blob = title + " " + content
            m = RE_BILL_NUM.search(blob)
            if not m:
                continue
            key = f"{m.group(1).upper()} {m.group(2)}"
            if key in bills_found:
                continue
            bills_found[key] = {
                "bill":    key,
                "title":   title[:120],
                "status":  "See source",
                "session": f"{END_YEAR}-{END_YEAR + 1}",
                "summary": content[:240],
                "url":     r.get("url"),
            }
    # Merge with existing: keep existing structured entries, append new ones not already named
    out = list(existing_bills)
    existing_keys = {b.get("bill") for b in existing_bills}
    for k, v in bills_found.items():
        if k in existing_keys:
            continue
        out.append(v)
    print(f"      [bills] {len(bills_found)} candidates from Tavily; {len(out)} total after merge.")
    return out


# ---------------------------------------------------------------------------
# Costar seed handling — read parsed JSON, surface freshness
# ---------------------------------------------------------------------------
def load_costar_seed():
    """Read the parsed Costar JSON. Returns (facilities, counties_meta) or
    (None, None) if seed missing/unparseable."""
    if not SEED_JSON.exists():
        print(f"      [Costar] {SEED_JSON} missing — preserving existing facilities.", file=sys.stderr)
        return None, None
    try:
        with open(SEED_JSON) as f:
            seed = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"      [Costar] parse failed ({e}) — preserving existing.", file=sys.stderr)
        return None, None
    return seed.get("facilities", []), {
        "exported_at":  seed.get("_parsed_at"),
        "n_records":    seed.get("_count"),
        "source_file":  seed.get("_source"),
    }


# ---------------------------------------------------------------------------
# Main — orchestrate all sections with graceful degradation
# ---------------------------------------------------------------------------
def main():
    if OUT_PATH.exists():
        with open(OUT_PATH) as f:
            existing = json.load(f)
    else:
        existing = {}

    meta = dict(existing.get("_meta", {}))
    for section in ("employment", "industry_gdp", "wages", "state_comparison",
                    "establishments", "mw_peer_states", "facilities", "counties",
                    "load_forecast", "water", "policy"):
        meta.setdefault(section, {"last_updated": None, "source": None})

    out = dict(existing)
    out["fetched_at"] = TODAY_ISO
    out.setdefault("trends", {})
    out.setdefault("kpis", {})

    # ----- 1) BLS QCEW employment + wages (NAICS 518210, GA) -----
    print(f"\n[1/7] BLS QCEW data-center employment & wages — GA {START_YEAR}-{END_YEAR}:")
    try:
        series = fetch_qcew_dc_employment_and_wages(START_YEAR, END_YEAR)
        if series:
            years      = [y for y, _, _, _ in series]
            emp_values = [e for _, e, _, _ in series]
            wkw_dc     = [w for _, _, w, _ in series]
            wkw_ap     = [w for _, _, _, w in series]
            out["trends"]["employment_k_years"]       = years
            out["trends"]["employment_k"]             = emp_values
            out["trends"]["wages_weekly"]             = wkw_dc
            out["trends"]["wages_weekly_allprivate"]  = wkw_ap
            latest_emp = emp_values[-1]
            prior_emp  = emp_values[-2] if len(emp_values) >= 2 else None
            out["kpis"]["employment_latest_k"] = latest_emp
            out["kpis"]["employment_yoy_pct"] = round((latest_emp - prior_emp) / prior_emp * 100, 1) if prior_emp else None
            out["kpis"]["avg_weekly_wage"]    = wkw_dc[-1]
            if wkw_ap[-1]:
                out["kpis"]["wage_vs_private_ratio"] = round(wkw_dc[-1] / wkw_ap[-1], 2)
            meta["employment"] = {
                "last_updated": TODAY_ISO,
                "source": "BLS QCEW annual averages, NAICS 518210 (Data Processing, Hosting), GA (area 13000)",
                "coverage_years": [years[0], years[-1]],
            }
            meta["wages"] = {
                "last_updated": TODAY_ISO,
                "source": "BLS QCEW avg weekly wage, NAICS 518210 vs all-private GA",
            }
            print(f"      OK: {len(years)} years; latest {years[-1]} = {latest_emp}K jobs, ${wkw_dc[-1]:,}/wk wage")
        else:
            print("      WARN: BLS QCEW returned no data — preserving existing.", file=sys.stderr)
    except Exception as e:
        print(f"      ERROR: BLS QCEW fetch failed ({e}) — preserving existing.", file=sys.stderr)

    # ----- 2 & 3) BEA SAGDP2: Information sector — GA series + state comparison -----
    if not BEA_API_KEY:
        print(f"\n[2-3/7] BEA Regional — SKIPPED (no BEA_API_KEY)", file=sys.stderr)
    else:
        try:
            line_code = bea_find_information_linecode()
        except Exception as e:
            print(f"      ERROR: could not look up BEA Information LineCode ({e})", file=sys.stderr)
            line_code = None

        if line_code:
            print(f"\n[2/7] BEA SAGDP2 Information-sector GDP — GA, {START_YEAR}-{END_YEAR}:")
            try:
                ga_years = list(range(START_YEAR, END_YEAR + 1))
                ga_series = bea_fetch_sagdp2n_series(line_code, GA_AREA_FIPS, ga_years)
                if ga_series:
                    years = [y for y, _ in ga_series]
                    gdp_b = [round(v / 1000.0, 3) for _, v in ga_series]
                    out["trends"]["years"] = years
                    out["trends"]["gdp_b"] = gdp_b
                    latest_b = gdp_b[-1]
                    prior_b  = gdp_b[-2] if len(gdp_b) >= 2 else None
                    out["kpis"]["industry_gdp_latest_b"] = latest_b
                    out["kpis"]["industry_gdp_yoy_pct"]  = round((latest_b - prior_b) / prior_b * 100, 1) if prior_b else None
                    meta["industry_gdp"] = {
                        "last_updated": TODAY_ISO,
                        "source": "BEA Regional SAGDP2 — Information sector, GA",
                        "metric_note": "Information sector value-added GDP (NAICS 51 — broader than data centers; includes telecom, publishing, software). Page calls this out.",
                        "coverage_years": [years[0], years[-1]],
                        "line_code": line_code,
                    }
                    print(f"      OK: {len(years)} years; latest {years[-1]} = ${latest_b}B GDP")
            except Exception as e:
                print(f"      ERROR: BEA GA timeseries fetch failed ({e})", file=sys.stderr)

            print(f"\n[3/7] BEA SAGDP2 Information-sector GDP — all states, latest year:")
            try:
                state_rows = bea_fetch_state_comparison(line_code, END_YEAR)
                if not state_rows and END_YEAR > 2000:
                    print(f"      no data for {END_YEAR}; falling back to {END_YEAR - 1}", file=sys.stderr)
                    state_rows = bea_fetch_state_comparison(line_code, END_YEAR - 1)
                if state_rows:
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
                    ga_rank = next((i + 1 for i, x in enumerate(state_rows) if x["abbr"] == "GA"), None)
                    if ga_rank:
                        out["kpis"]["national_rank_information"] = ga_rank
                    meta["state_comparison"] = {
                        "last_updated": TODAY_ISO,
                        "source": "BEA Regional SAGDP2 — Information sector GDP by state",
                        "metric_label": "Information-sector GDP (value-added, $B) — broader than data centers",
                        "year": END_YEAR if state_rows else END_YEAR - 1,
                    }
                    print(f"      OK: {len(state_comparison)} states; GA = #{out['kpis'].get('national_rank_information', '?')}")
            except Exception as e:
                print(f"      ERROR: BEA state comparison failed ({e})", file=sys.stderr)

    # ----- 4) Census CBP — establishment counts -----
    print(f"\n[4/7] Census CBP — establishments NAICS 518210, GA:")
    try:
        cbp = fetch_cbp_establishments_latest()
        if cbp:
            cbp_year, estab = cbp
            out["kpis"]["establishments_latest"] = estab
            meta["establishments"] = {
                "last_updated": TODAY_ISO,
                "source": f"Census CBP {cbp_year} — NAICS 518210, GA",
                "year": cbp_year,
            }
            print(f"      OK: {cbp_year}: {estab:,} establishments")
    except Exception as e:
        print(f"      ERROR: Census CBP failed ({e}) — preserving existing.", file=sys.stderr)

    # ----- 5) Costar facilities + counties (manual seed) -----
    print(f"\n[5/7] Costar facilities + counties — read parsed seed:")
    fac, fac_meta = load_costar_seed()
    if fac is not None:
        out["facilities"] = fac
        # Recompute county aggregates from the seed so they stay in sync
        try:
            counties_list = _recompute_counties(fac)
            out["counties"] = counties_list
            out["kpis"]["n_facilities_existing"] = sum(1 for f in fac if f.get("status") == "operating")
            out["kpis"]["n_facilities_pipeline"] = sum(1 for f in fac if f.get("status") in ("under-construction", "announced"))
            out["kpis"]["n_facilities_total"]    = len(fac)
            mw_existing = round(sum(f.get("capacity_total_utility_kw") or 0 for f in fac if f.get("status") == "operating") / 1000, 1)
            mw_pipeline = round(sum(f.get("capacity_total_utility_kw") or 0 for f in fac if f.get("status") in ("under-construction", "announced")) / 1000, 1)
            out["kpis"]["operating_mw"] = mw_existing
            out["kpis"]["pipeline_mw"]  = mw_pipeline
            if counties_list:
                out["kpis"]["top_county_existing"] = counties_list[0]["name"]
                out["kpis"]["top_county_pipeline"] = max(counties_list, key=lambda c: c["mw_pipeline"])["name"]
            meta["facilities"] = {
                "last_updated": fac_meta.get("exported_at"),
                "source": "Georgia Economics internal facility database (manual refresh)",
                "exported_at": fac_meta.get("exported_at"),
                "n_records": fac_meta.get("n_records"),
                "source_file": fac_meta.get("source_file"),
            }
            meta["counties"] = {
                "last_updated": fac_meta.get("exported_at"),
                "source": "Derived from internal facility database",
            }
            print(f"      OK: {len(fac)} facilities, {len(counties_list)} counties, "
                  f"{mw_existing} MW existing / {mw_pipeline} MW pipeline")
        except Exception as e:
            print(f"      ERROR: county aggregation failed ({e}) — preserving existing.", file=sys.stderr)
    else:
        print(f"      Costar seed not found — preserving prior facilities.", file=sys.stderr)

    # ----- 6) Tavily — DECD new announcements + EPD water permits + PSC headline + policy bills -----
    if not TAVILY_API_KEY:
        print(f"\n[6/7] Tavily — SKIPPED (no TAVILY_API_KEY)", file=sys.stderr)
    else:
        print(f"\n[6/7] Tavily — DECD / EPD / PSC / policy:")

        try:
            pending = fetch_decd_new_announcements()
            if pending:
                out["pending_announcements"] = pending
                meta["pending_announcements"] = {
                    "last_updated": TODAY_ISO,
                    "source": "Tavily → DECD / gov.georgia.gov / AJC / DCD / DCF; advisory hints",
                    "count": len(pending),
                }
        except Exception as e:
            print(f"      DECD scrape error: {e}", file=sys.stderr)

        try:
            n_permits, permitted_mgd, src = fetch_epd_water_permits()
            # Modeled remainder uses the operating MW that ISN'T already counted in permits.
            # Conservative: assume permits cover ~half the operating MW, model the rest.
            mw_op = out["kpis"].get("operating_mw") or 0
            modeled_mgd = round(max(0.0, mw_op - n_permits * 20) * 0.5 * 8760 * 1000 * 0.5 / 365 / 1_000_000, 2)
            out["water"] = {
                "permitted_facility_count": n_permits,
                "permitted_mgd": permitted_mgd,
                "modeled_remainder_mgd": modeled_mgd,
                "total_estimated_mgd": round(permitted_mgd + modeled_mgd, 2),
                "permit_sources": src,
                "methodology_note": (
                    "Measured: GA EPD water-withdrawal permits (Tavily-extracted). "
                    "Modeled remainder: operating MW not associated with permits, at 50% utilization × "
                    "industry-standard 0.5 gallons cooling water per kWh. Estimate, not measurement."
                ),
            }
            meta["water"] = {
                "last_updated": TODAY_ISO,
                "source": "GA EPD water-withdrawal permits + MW-based modeled remainder",
            }
            print(f"      OK: water = {permitted_mgd} MGD measured + {modeled_mgd} MGD modeled")
        except Exception as e:
            print(f"      water error: {e}", file=sys.stderr)

        try:
            psc = fetch_psc_load_forecast_headline()
            if psc:
                out.setdefault("load_forecast", {})
                out["load_forecast"]["headline"] = psc
                meta["load_forecast"] = {
                    "last_updated": TODAY_ISO,
                    "source": "GA PSC docket filings (Tavily) + Georgia Power 2023 IRP + 2025 update (seeded curve)",
                    "psc_headline_source": psc.get("source_url"),
                }
        except Exception as e:
            print(f"      PSC error: {e}", file=sys.stderr)

        try:
            existing_bills = (out.get("policy") or {}).get("active_bills", [])
            updated_bills = fetch_active_policy_bills(existing_bills)
            if updated_bills:
                out.setdefault("policy", {})
                out["policy"]["active_bills"] = updated_bills
                meta["policy"] = {
                    "last_updated": TODAY_ISO,
                    "source": "GA legis.ga.gov + AJC + Georgia Recorder (Tavily); exemption timeline curated.",
                }
        except Exception as e:
            print(f"      bills error: {e}", file=sys.stderr)

    # ----- 7) MW peer states — preserved (manual curation, not API-driven) -----
    # Keep existing seed unchanged; just update GA's own row from latest KPIs.
    print(f"\n[7/7] MW peer states — refresh GA row from latest KPIs:")
    try:
        peer = list(out.get("mw_peer_states", []))
        for p in peer:
            if p.get("abbr") == "GA":
                p["mw_existing"] = out["kpis"].get("operating_mw", p.get("mw_existing"))
                p["mw_pipeline"] = out["kpis"].get("pipeline_mw", p.get("mw_pipeline"))
        peer.sort(key=lambda r: -r["mw_existing"])
        for i, p in enumerate(peer, 1):
            p["rank"] = i
        out["mw_peer_states"] = peer
        # National rank by MW = GA's index in this sorted peer list
        for i, p in enumerate(peer, 1):
            if p.get("abbr") == "GA":
                out["kpis"]["national_rank_mw"] = i
                break
        print(f"      OK: GA mw_existing={out['kpis'].get('operating_mw')}, "
              f"rank in peer set = #{out['kpis'].get('national_rank_mw')}")
    except Exception as e:
        print(f"      peer-state refresh error: {e}", file=sys.stderr)

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
        json.dump(out, f, indent=2, default=str)
    print(f"\nWrote {OUT_PATH}")
    print(f"  Live sections this run: {live_sections}")
    print(f"  Latest year: {out['latest_year']}")
    nonnull_kpis = {k: v for k, v in out["kpis"].items() if v is not None}
    print(f"  KPIs: {json.dumps(nonnull_kpis, indent=2, default=str)}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
COUNTY_FIPS = {
    "Fulton":"13121","Douglas":"13097","Bartow":"13015","Wilkes":"13317","Rockdale":"13247",
    "Newton":"13217","Gwinnett":"13135","Fayette":"13113","Walton":"13297","Cobb":"13067",
    "Morgan":"13211","Whitfield":"13313","Clayton":"13063","Coweta":"13077","Lamar":"13171",
    "Richmond":"13245","Floyd":"13115","Forsyth":"13117","Henry":"13151","DeKalb":"13089",
    "Cherokee":"13057","Paulding":"13223","Spalding":"13255","Carroll":"13045","Hall":"13139",
}


def _recompute_counties(facilities):
    """Aggregate facilities by county. Returns sorted list of dicts."""
    from collections import defaultdict
    agg = defaultdict(lambda: {
        "existing": 0, "under_construction": 0, "announced": 0, "deferred": 0,
        "kw_existing": 0, "kw_pipeline": 0, "sf_existing": 0, "sf_pipeline": 0,
    })
    for f in facilities:
        c = f.get("county")
        if not c:
            continue
        s = f.get("status")
        kw = f.get("capacity_total_utility_kw") or 0
        sf = f.get("rba_sf") or 0
        if s == "operating":
            agg[c]["existing"] += 1
            agg[c]["kw_existing"] += kw
            agg[c]["sf_existing"] += sf
        elif s == "under-construction":
            agg[c]["under_construction"] += 1
            agg[c]["kw_pipeline"] += kw
            agg[c]["sf_pipeline"] += sf
        elif s == "announced":
            agg[c]["announced"] += 1
            agg[c]["kw_pipeline"] += kw
            agg[c]["sf_pipeline"] += sf
        elif s == "deferred":
            agg[c]["deferred"] += 1

    out = []
    for c, st in agg.items():
        total = st["existing"] + st["under_construction"] + st["announced"] + st["deferred"]
        out.append({
            "name": c,
            "fips": COUNTY_FIPS.get(c, ""),
            "total": total,
            "existing": st["existing"],
            "under_construction": st["under_construction"],
            "announced": st["announced"],
            "deferred": st["deferred"],
            "mw_existing": round(st["kw_existing"] / 1000, 1),
            "mw_pipeline": round(st["kw_pipeline"] / 1000, 1),
            "sf_existing": st["sf_existing"],
            "sf_pipeline": st["sf_pipeline"],
        })
    out.sort(key=lambda c: -c["total"])
    return out


if __name__ == "__main__":
    main()
