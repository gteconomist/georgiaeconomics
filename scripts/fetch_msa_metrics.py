"""Pull live data for the MSA page from 4 different APIs.

Outputs: data/msa.json (replaces fixture for the fields we can fetch, preserves others).

What we fetch this iteration:
  1. Unemployment rate           — BLS LAUS MSA (UR, NSA latest month)
  2. Home price growth YoY (%)   — FRED FHFA HPI by MSA (quarterly, latest vs 4Q ago)
  3. Population growth YoY (%)   — Census PEP MSA estimates (annual)
  4. GDP per capita ($)          — BEA Regional MSA GDP / latest population

What stays on fixture (deferred to next iteration):
  - wage_growth_yoy   (BLS QCEW is a separate API; complex)
  - permits_per_1k    (Census BPS monthly aggregation)

Each fetcher returns a dict {msa_short_name: value} or None on failure.
Per-MSA per-metric fallback to fixture is automatic — never blanks data.

Env: BLS_API_KEY, FRED_API_KEY, CENSUS_API_KEY, BEA_API_KEY
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

BLS_API_KEY    = os.environ.get("BLS_API_KEY",    "").strip()
FRED_API_KEY   = os.environ.get("FRED_API_KEY",   "").strip()
CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "").strip()
BEA_API_KEY    = os.environ.get("BEA_API_KEY",    "").strip()

TODAY = date.today()

sys.path.insert(0, str(Path(__file__).parent))
from _ga_msas import GA_MSAS, COUNTY_TO_MSA

# Atlanta MSA + the 29 county FIPS that compose it.
# Used by the home-price fetcher's stale-series fallback (see _atlanta_county_avg_hpi_yoy).
ATLANTA_CBSA = "12060"
ATLANTA_COUNTY_FIPS = [fips for fips, cbsa in COUNTY_TO_MSA.items() if cbsa == ATLANTA_CBSA]
assert len(ATLANTA_COUNTY_FIPS) == 29, f"Expected 29 Atlanta counties, got {len(ATLANTA_COUNTY_FIPS)}"

# Index: short_name -> (cbsa, full_name, population)
MSA_BY_SHORT = {short: (cbsa, full, pop) for cbsa, short, full, pop in GA_MSAS}


# ---------- HTTP helpers ----------
def http_get_json(url, retries=3, timeout=30):
    """Robust JSON fetch — never crashes, returns None on any failure."""
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                if not body.strip(): return None
                try:    return json.loads(body)
                except json.JSONDecodeError:
                    print(f"      [non-JSON] {url[:140]} → {body[:160]!r}", file=sys.stderr)
                    return None
        except urllib.error.HTTPError as e:
            try:    body = e.read().decode("utf-8")[:200]
            except: body = "?"
            print(f"      [HTTP {e.code}] {url[:140]} → {body}", file=sys.stderr)
            if e.code == 400: return None
            last_err = e
        except Exception as e:
            print(f"      [HTTP err] {url[:140]} — {type(e).__name__}: {e}", file=sys.stderr)
            last_err = e
        time.sleep(1 + attempt)
    return None


def http_post_json(url, body_obj, retries=3, timeout=30):
    body = json.dumps(body_obj).encode("utf-8")
    req  = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(1 + attempt)
    print(f"      [POST FAIL] {url[:80]} — {last_err}", file=sys.stderr)
    return None


# ---------- 1. BLS LAUS — MSA unemployment rate ----------
def laus_msa_series(cbsa):
    """BLS LAUS MSA UR series ID. Format: LAU + MT + state_fips(2) + cbsa(5) + 7 zeros + 3 (measure code).
    All GA MSAs use state prefix 13 (even for multi-state MSAs like Augusta-Richmond GA-SC)."""
    return f"LAUMT13{cbsa}00000003"   # NSA = U; for SA use LASMT but NSA is widely available

def fetch_msa_unemployment():
    if not BLS_API_KEY:
        print("  [BLS] no key, skipping unemployment", file=sys.stderr); return None
    series_ids = [laus_msa_series(cbsa) for cbsa, _, _, _ in GA_MSAS]
    payload = {
        "seriesid": series_ids,
        "startyear": str(TODAY.year - 1),
        "endyear":   str(TODAY.year),
        "registrationkey": BLS_API_KEY,
    }
    print(f"  [BLS LAUS] fetching {len(series_ids)} MSA UR series...")
    resp = http_post_json("https://api.bls.gov/publicAPI/v2/timeseries/data/", payload)
    if not resp or resp.get("status") != "REQUEST_SUCCEEDED":
        print(f"    BLS error: {resp.get('message') if resp else 'no response'}", file=sys.stderr)
        return None

    by_short = {}
    by_sid = {s["seriesID"]: s for s in resp["Results"]["series"]}
    for cbsa, short, _, _ in GA_MSAS:
        sid = laus_msa_series(cbsa)
        s = by_sid.get(sid)
        if not s or not s.get("data"):
            print(f"    {short}: no LAUS data ({sid})", file=sys.stderr); continue
        # Most recent monthly value (sort by year+period)
        obs = sorted(s["data"], key=lambda d: (d["year"], d["period"]))
        latest = next((o for o in reversed(obs) if o.get("period","").startswith("M") and o["period"] != "M13"), None)
        if not latest: continue
        try:    v = float(latest["value"])
        except (TypeError, ValueError): continue
        by_short[short] = round(v, 1)
        print(f"    {short}: UR {v}% ({latest['year']}-{latest['period']})")
    return by_short or None



def _atlanta_county_avg_hpi_yoy():
    """Aggregate the 29 Atlanta MSA county annual FHFA HPI series into an
    unweighted-average YoY. Workaround for FRED's frozen ATNHPIUS12060Q
    (last obs 2024-Q4 as of mid-2026 while peer MSA series have 2025-Q4 data).

    Each county series ID is ATNHPIUS<fips>A (annual freq, obs dated Jan 1).
    Unweighted average is within ~1pp of the official MSA YoY in 2023-2024
    backtests; we accept that error for simplicity (vs population-weighting).
    """
    yoys = []
    latest_dates = []
    prior_dates = []
    for fips in ATLANTA_COUNTY_FIPS:
        sid = f"ATNHPIUS{fips}A"
        url = (f"https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={sid}&api_key={FRED_API_KEY}&file_type=json&observation_start=2022-01-01")
        resp = http_get_json(url, timeout=15)
        if not resp: continue
        obs = resp.get("observations", []) or []
        vals = [(o["date"], float(o["value"])) for o in obs if o.get("value") not in (".", None)]
        if len(vals) < 2: continue
        latest_v = vals[-1][1]; prior_v = vals[-2][1]
        if prior_v <= 0: continue
        yoys.append((latest_v - prior_v) / prior_v * 100)
        latest_dates.append(vals[-1][0])
        prior_dates.append(vals[-2][0])
    if not yoys: return None
    avg = sum(yoys) / len(yoys)
    # Most counties share the same vintage; pick the modal latest date for the log line.
    from collections import Counter
    latest = Counter(latest_dates).most_common(1)[0][0] if latest_dates else "?"
    prior  = Counter(prior_dates).most_common(1)[0][0]  if prior_dates  else "?"
    print(f"      [Atlanta county-avg] {len(yoys)}/{len(ATLANTA_COUNTY_FIPS)} counties, "
          f"avg YoY {avg:+.2f}% ({prior} → {latest})")
    return round(avg, 1)


# ---------- 2. FRED FHFA HPI — MSA home price YoY ----------
def fred_hpi_series(cbsa):
    """FRED FHFA HPI series ID format: ATNHPIUS<5-digit CBSA>Q (NSA quarterly)."""
    return f"ATNHPIUS{cbsa}Q"

def fetch_msa_home_price_yoy():
    if not FRED_API_KEY:
        print("  [FRED] no key, skipping home prices", file=sys.stderr); return None
    by_short = {}
    print(f"  [FRED FHFA HPI] fetching {len(GA_MSAS)} MSA series...")
    for cbsa, short, _, _ in GA_MSAS:
        sid = fred_hpi_series(cbsa)
        url = (f"https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={sid}&api_key={FRED_API_KEY}&file_type=json&observation_start=2022-01-01")
        resp = http_get_json(url, timeout=20)
        if not resp:
            print(f"    {short}: no FHFA series ({sid})", file=sys.stderr); continue
        obs = resp.get("observations", [])
        vals = [(o["date"], float(o["value"])) for o in obs if o.get("value") not in (".", None)]
        if len(vals) < 5:
            print(f"    {short}: insufficient FHFA obs ({len(vals)})", file=sys.stderr); continue
        # YoY = latest vs 4 quarters earlier
        latest_v = vals[-1][1]
        prior_v  = vals[-5][1]
        yoy = round((latest_v - prior_v) / prior_v * 100, 1) if prior_v else None
        if yoy is None: continue

        # Atlanta-specific freshness override: ATNHPIUS12060Q has been frozen at
        # 2024-Q4 since Feb 2025. If the latest year is older than (current year
        # - 1), peer MSAs are publishing fresher data and Atlanta will look stale.
        # Fall back to the county-level annual FHFA series.
        if cbsa == ATLANTA_CBSA:
            latest_year = int(vals[-1][0][:4])
            if latest_year < TODAY.year - 1:
                print(f"    {short}: MSA series stale "
                      f"(latest {vals[-1][0]}, need >= {TODAY.year - 1}); falling back to county-avg...")
                agg = _atlanta_county_avg_hpi_yoy()
                if agg is not None:
                    by_short[short] = agg
                    print(f"    {short}: HPI YoY {agg:+.1f}% (county-avg fallback)")
                    continue
                else:
                    print(f"    {short}: county-avg fallback failed, using stale MSA value", file=sys.stderr)

        by_short[short] = yoy
        print(f"    {short}: HPI YoY {yoy:+.1f}% ({vals[-5][0]} → {vals[-1][0]})")
    return by_short or None

# ---------- 3. Census ACS 5-year — MSA population growth YoY ----------
# Uses ACS 5-year (smoother, available for all geographies) rather than 1-year.
# Each ACS 5-year vintage is labelled by the END year of its 5-year window:
# vintage 2024 = 2020-2024 data, released December 2025.
# Consecutive vintages overlap by 4 years, so "YoY" reflects the rolling
# window's new + dropped years rather than a clean single-year delta —
# good enough for the report's MSA comparison panel.
# Variable B01003_001E = Total population.
def fetch_msa_population_growth():
    if not CENSUS_API_KEY:
        print("  [Census] no key, skipping population", file=sys.stderr); return None
    cbsa_to_short = {cbsa: short for cbsa, short, _, _ in GA_MSAS}

    def fetch_acs_year(year):
        """Returns dict {cbsa: population} for given ACS 5-year vintage."""
        url = (f"https://api.census.gov/data/{year}/acs/acs5"
               f"?get=NAME,B01003_001E"
               f"&for=metropolitan%20statistical%20area/micropolitan%20statistical%20area:*"
               f"&key={CENSUS_API_KEY}")
        resp = http_get_json(url, timeout=30)
        if not resp or len(resp) < 2: return {}
        header = resp[0]
        try:
            pop_idx  = header.index("B01003_001E")
            cbsa_idx = header.index("metropolitan statistical area/micropolitan statistical area")
        except ValueError: return {}
        out = {}
        for row in resp[1:]:
            cbsa = row[cbsa_idx]
            try:    pop = float(row[pop_idx])
            except (TypeError, ValueError): continue
            out[cbsa] = pop
        return out

    # Try recent vintage pairs. ACS 5-year typically releases for vintage Y
    # in December of year Y+1. In May 2026, vintage 2024 should be live.
    for cur_year in (TODAY.year - 1, TODAY.year - 2, TODAY.year - 3):
        prev_year = cur_year - 1
        print(f"  [Census ACS] trying {cur_year} vs {prev_year}...")
        cur  = fetch_acs_year(cur_year)
        prev = fetch_acs_year(prev_year)
        if not cur or not prev:
            print(f"    ACS {cur_year} or {prev_year} unavailable, trying older", file=sys.stderr); continue

        by_short = {}
        for cbsa, short in cbsa_to_short.items():
            p_cur, p_prev = cur.get(cbsa), prev.get(cbsa)
            if p_cur is None or p_prev is None or p_prev <= 0:
                print(f"    {short}: no ACS pop ({cbsa})", file=sys.stderr); continue
            yoy = round((p_cur - p_prev) / p_prev * 100, 2)
            by_short[short] = yoy
            print(f"    {short}: pop growth {yoy:+.2f}% ({prev_year}: {int(p_prev):,} → {cur_year}: {int(p_cur):,})")

        if len(by_short) >= 5:
            return by_short
        print(f"    matched only {len(by_short)} MSAs for {prev_year}→{cur_year}, trying older", file=sys.stderr)

    return None


# ---------- 4. BEA county GDP, aggregated to MSA ----------
def fetch_msa_gdp_per_capita():
    """Sum county GDP (BEA CAGDP2 'All industry total', current dollar) within each MSA,
    then divide by MSA population to get $/capita.

    Why not just ask BEA for MSA GDP directly?
      BEA's Regional API does NOT expose MSA-level GDP as its own table. The only
      metropolitan tables are MAIRPD (price deflators) and MARPP (price parities).
      All CAGDP* tables are county-only — the GeoFips parameter list contains only
      US+states+counties, no CBSAs. FRED's NGMP/RGMP/PCRGMP MSA-GDP series were
      discontinued 2024-12-04 with last obs 2023. County aggregation is the only
      remaining live path.

    One BEA call returns all 3,127 US counties; we filter to the 76 counties in
    COUNTY_TO_MSA (73 GA + 2 SC border for Augusta + 1 AL border for Columbus).

    Units note: CAGDP2 LineCode=1 returns "Thousands of dollars" (current dollar
    GDP, all industry total). Multiply by 1000 to get dollars; divide by MSA pop.
    """
    if not BEA_API_KEY:
        print("  [BEA] no key, skipping GDP", file=sys.stderr); return None

    cbsa_to_short = {cbsa: short for cbsa, short, _, _ in GA_MSAS}
    cbsa_to_pop   = {cbsa: pop   for cbsa, _, _, pop  in GA_MSAS}

    def fetch_year(year):
        url = (f"https://apps.bea.gov/api/data/?UserID={BEA_API_KEY}"
               f"&method=GetData&DataSetName=Regional&TableName=CAGDP2"
               f"&LineCode=1&GeoFips=COUNTY&Year={year}&ResultFormat=JSON")
        return http_get_json(url, timeout=60)

    # Latest county GDP is typically released ~Dec. Try newest first; fall back.
    for year in (TODAY.year - 1, TODAY.year - 2, TODAY.year - 3):
        print(f"  [BEA CAGDP2] year={year}, all-county call ({len(COUNTY_TO_MSA)} GA-MSA counties expected)...")
        resp = fetch_year(year)
        if not resp:
            print(f"    no response for {year}", file=sys.stderr); continue

        results = (resp.get("BEAAPI") or {}).get("Results") or {}
        if isinstance(results, list): results = results[0] if results else {}
        if results.get("Error"):
            err = results.get("Error")
            if isinstance(err, list): err = err[0] if err else {}
            print(f"    BEA error: {err.get('APIErrorDescription', err)}", file=sys.stderr); continue
        rows = results.get("Data", []) or []
        if not rows:
            print(f"    no rows for {year}, trying older", file=sys.stderr); continue
        print(f"    BEA returned {len(rows)} county rows")

        # Sum county GDP (thousands of $) into MSAs via COUNTY_TO_MSA.
        msa_gdp_thou = {}
        msa_n_counties = {}
        for row in rows:
            fips = (row.get("GeoFips") or "").strip()
            cbsa = COUNTY_TO_MSA.get(fips)
            if not cbsa: continue
            raw = (row.get("DataValue") or "").replace(",", "")
            try: v = float(raw)
            except ValueError:
                # BEA suppresses some county values (e.g. "(D)" for disclosure).
                # We continue rather than fail the MSA — usually only 1-2 small
                # counties affected, totals stay within a percent or two.
                print(f"    skip {fips} ({row.get('GeoName')}): DataValue={raw!r}", file=sys.stderr)
                continue
            msa_gdp_thou[cbsa] = msa_gdp_thou.get(cbsa, 0.0) + v
            msa_n_counties[cbsa] = msa_n_counties.get(cbsa, 0) + 1

        # Convert to per-capita dollars.
        by_short = {}
        for cbsa, gdp_thou in msa_gdp_thou.items():
            pop = cbsa_to_pop.get(cbsa, 0)
            short = cbsa_to_short.get(cbsa)
            if pop <= 0 or not short: continue
            per_cap = int(round(gdp_thou * 1000 / pop, 0))
            by_short[short] = per_cap
            n = msa_n_counties[cbsa]
            print(f"    {short}: GDP/cap ${per_cap:,} "
                  f"(${gdp_thou * 1000 / 1e9:.1f}B / {pop:,} pop, {n} counties)")

        if len(by_short) >= 5:
            print(f"  [BEA CAGDP2] success: {len(by_short)} MSAs for year {year}")
            return by_short
        print(f"    only {len(by_short)} MSAs matched for {year}, trying older", file=sys.stderr)

    return None



# ---------- 5. BLS QCEW — MSA private-sector average weekly wage YoY ----------
def _qcew_msa_area_code(cbsa):
    """QCEW MSA area code = 'C' + first 4 digits of 5-digit CBSA.
    All Census MSA codes end in 0 so dropping the trailing zero is unambiguous."""
    return "C" + cbsa[:4]

def _qcew_fetch_quarter_avg_wkly_wage(year, qtr, area_code):
    """Fetch one QCEW CSV for (year, qtr, area_code) and return the private-sector
    all-industries average weekly wage (float) or None if not found.

    QCEW Open Data CSV layout reference:
    https://www.bls.gov/cew/about-data/downloadable-file-layouts/csv-quarterly-layout.htm
    Quarterly URL pattern:
    https://data.bls.gov/cew/data/api/{year}/{1-4}/area/{area_code}.csv
    """
    import csv as _csv, io as _io
    url = f"https://data.bls.gov/cew/data/api/{year}/{qtr}/area/{area_code}.csv"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"      [QCEW {year}-Q{qtr} {area_code}] HTTP {e.code}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"      [QCEW {year}-Q{qtr} {area_code}] {type(e).__name__}: {e}", file=sys.stderr)
        return None

    reader = _csv.DictReader(_io.StringIO(body))
    # We want the MSA-level, private-sector, all-industries, all-sizes row. Filter on:
    #   own_code='5'      Private
    #   industry_code='10' Total, all industries
    #   size_code='0'     All establishment sizes
    # PLUS reject zero/empty wages — BLS sometimes publishes the row structure for
    # a just-released quarter before populating the actual numbers, and those
    # placeholder rows would produce a bogus -100% YoY against the populated prior year.
    #
    # The QCEW agglvl_code 40-series is for MSAs. (The 70-series is county-level
    # and the 90-series is also county-level — earlier code mistakenly preferred
    # agglvl='74' which never appears in an MSA CSV.)
    MIN_PLAUSIBLE_WAGE = 100   # $/week — guard against obviously-placeholder rows
    candidates = []
    for row in reader:
        if (row.get("own_code") == "5"
            and row.get("industry_code") == "10"
            and row.get("size_code") == "0"):
            try: w = float(row.get("avg_wkly_wage") or 0)
            except ValueError: continue
            if w < MIN_PLAUSIBLE_WAGE: continue   # skip placeholder/zero rows
            candidates.append((row.get("agglvl_code", ""), w, row))
    if not candidates: return None
    # Prefer MSA-level rollups (agglvl_code in 40-49). For the
    # private/all-industries/all-sizes filter there is typically exactly one
    # MSA-level row per quarter per area.
    for agglvl, w, _row in candidates:
        if agglvl.startswith("4"):
            return w
    # No MSA-level row — return highest-wage candidate as last resort.
    return max(c[1] for c in candidates)


def fetch_msa_wage_growth_yoy():
    """Compute YoY % change in average weekly wage (private sector, all industries)
    for each of the 14 GA MSAs, using the latest available QCEW quarter.

    Strategy:
      1. Probe Atlanta with quarters from newest backward to find the latest published quarter.
      2. For each MSA, fetch that quarter + same quarter prior year, compute YoY.
    """
    # Probe order: today is May 2026 → 2025-Q4 release lands ~Jun 2026 (often not out yet),
    # 2025-Q3 ~Mar 2026 (should be available), then older fallbacks.
    probe_order = [
        (TODAY.year - 1, 4), (TODAY.year - 1, 3), (TODAY.year - 1, 2), (TODAY.year - 1, 1),
        (TODAY.year - 2, 4), (TODAY.year - 2, 3),
    ]
    print(f"  [QCEW] probing latest quarter via Atlanta (C1206)...")
    latest_y, latest_q = None, None
    for y, q in probe_order:
        v = _qcew_fetch_quarter_avg_wkly_wage(y, q, "C1206")
        # Require a POSITIVE wage. A 0 or None means the quarter is either not
        # released or BLS published placeholders without the actual numbers.
        if v is not None and v > 0:
            latest_y, latest_q = y, q
            print(f"    latest available: {y}-Q{q} (Atlanta wage probe = ${v:,.0f}/wk)")
            break
        else:
            print(f"    {y}-Q{q}: no usable data (probe returned {v!r})", file=sys.stderr)
    if latest_y is None:
        print("  [QCEW] no quarter returned data; skipping wage growth", file=sys.stderr)
        return None

    print(f"  [QCEW] fetching {len(GA_MSAS)} MSAs for {latest_y}-Q{latest_q} vs {latest_y - 1}-Q{latest_q}...")
    by_short = {}
    for cbsa, short, _, _ in GA_MSAS:
        area = _qcew_msa_area_code(cbsa)
        cur = _qcew_fetch_quarter_avg_wkly_wage(latest_y,     latest_q, area)
        prv = _qcew_fetch_quarter_avg_wkly_wage(latest_y - 1, latest_q, area)
        if cur is None or prv is None or prv <= 0:
            print(f"    {short}: missing wage data (cur={cur}, prv={prv})", file=sys.stderr); continue
        yoy = round((cur - prv) / prv * 100, 1)
        by_short[short] = yoy
        print(f"    {short}: avg wkly wage YoY {yoy:+.1f}% (${prv:,.0f} → ${cur:,.0f})")
    return by_short or None


# ---------- Aggregate recomputation ----------
def recompute_aggregates(existing):
    """Recompute statewide_medians and callouts from per-MSA metrics in place."""
    msas = existing.get("msas", [])
    metrics = existing.get("metrics", [])
    if not msas or not metrics: return

    def med(key):
        vals = sorted(m["metrics"].get(key) for m in msas if m["metrics"].get(key) is not None)
        if not vals: return None
        n = len(vals)
        return round((vals[n//2-1] + vals[n//2]) / 2, 2) if n % 2 == 0 else vals[n//2]

    existing["statewide_medians"] = {
        "median_unemployment":   med("unemployment_rate"),
        "median_wage_growth":    med("wage_growth_yoy"),
        "median_pop_growth":     med("pop_growth_yoy"),
        "median_home_price_yoy": med("home_price_yoy"),
        "median_permits_per_1k": med("permits_per_1k"),
        "median_gdp_per_capita": med("gdp_per_capita"),
    }

    def top(key, lower_better=False):
        with_key = [(m["short_name"], m["metrics"].get(key)) for m in msas if m["metrics"].get(key) is not None]
        if not with_key: return None
        sorted_ms = sorted(with_key, key=lambda x: x[1], reverse=not lower_better)
        return {"msa": sorted_ms[0][0], "value": sorted_ms[0][1]}

    existing["callouts"] = {
        "lowest_unemployment":   top("unemployment_rate", True),
        "highest_wage_growth":   top("wage_growth_yoy"),
        "fastest_growing":       top("pop_growth_yoy"),
        "hottest_housing":       top("home_price_yoy"),
        "most_building":         top("permits_per_1k"),
        "richest":               top("gdp_per_capita"),
    }


# ---------- Main ----------
def main():
    fixture_path = Path(__file__).parent.parent / "data" / "msa.json"
    if not fixture_path.exists():
        print(f"ERROR: data/msa.json not found", file=sys.stderr); sys.exit(2)
    with open(fixture_path) as f:
        existing = json.load(f)

    print("=" * 60)
    print(f"MSA metrics fetch — {TODAY.isoformat()}")
    print("=" * 60)

    print("\n[1/5] Unemployment (BLS LAUS MSA)")
    ur = fetch_msa_unemployment()

    print("\n[2/5] Home prices (FRED FHFA HPI)")
    hp = fetch_msa_home_price_yoy()

    print("\n[3/5] Population growth (Census PEP)")
    pop = fetch_msa_population_growth()

    print("\n[4/5] GDP per capita (BEA Regional)")
    gdp = fetch_msa_gdp_per_capita()

    print("\n[5/5] Wage growth (BLS QCEW MSA, private sector)")
    wage = fetch_msa_wage_growth_yoy()

    # Hygiene: any existing wage_growth_yoy outside [-30, +30]% is implausible
    # (no real MSA has had +30% or -30% annual wage growth) and is almost certainly
    # left over from a buggy prior run — strip it so the per-MSA update loop will
    # either replace it with fresh QCEW data or leave it absent (page shows '—').
    for _msa in existing.get("msas", []):
        _m = _msa.setdefault("metrics", {})
        _v = _m.get("wage_growth_yoy")
        if _v is not None and (_v < -30 or _v > 30):
            print(f"    hygiene: dropping bogus wage_growth_yoy={_v} for {_msa.get('short_name')}", file=sys.stderr)
            _m.pop("wage_growth_yoy", None)

    # Update per-MSA metrics — fall back to fixture for missing values
    fetched_metrics = set()
    for msa in existing.get("msas", []):
        short = msa["short_name"]
        m = msa.setdefault("metrics", {})
        if ur  and short in ur:  m["unemployment_rate"] = ur[short];      fetched_metrics.add("unemployment_rate")
        if hp  and short in hp:  m["home_price_yoy"]    = hp[short];      fetched_metrics.add("home_price_yoy")
        if pop and short in pop: m["pop_growth_yoy"]    = pop[short];     fetched_metrics.add("pop_growth_yoy")
        if gdp and short in gdp: m["gdp_per_capita"]    = gdp[short];     fetched_metrics.add("gdp_per_capita")
        if wage and short in wage: m["wage_growth_yoy"]  = wage[short];    fetched_metrics.add("wage_growth_yoy")

    # Recompute medians and callouts from the updated MSA data
    recompute_aggregates(existing)

    # Mark partial-live
    if fetched_metrics:
        existing["_fixture"] = False
        existing["_note"] = (
            f"Partial live data: {sorted(fetched_metrics)}. "
            f"Permits-per-1k still on fixture (next iteration)."
        )
        existing["source"] = "Live: BLS LAUS (UR) + FRED FHFA HPI (home prices) + Census ACS (population) + BEA (GDP, county-aggregated) + BLS QCEW (wage growth, private). Fixture: permits_per_1k."
    existing["fetched_at"] = TODAY.isoformat()

    with open(fixture_path, "w") as f:
        json.dump(existing, f, indent=2)

    print("\n" + "=" * 60)
    print(f"Wrote {fixture_path}")
    print(f"Metrics fetched live: {sorted(fetched_metrics) if fetched_metrics else 'NONE'}")
    print(f"Per-MSA fallback to fixture for any MSAs/metrics that failed")


if __name__ == "__main__":
    main()
