"""Pull live data for the MSA page from 4 different APIs.

Outputs: data/msa.json (replaces fixture for the fields we can fetch, preserves others).

What we fetch (all six page metrics are now live):
  1. Unemployment rate           — BLS LAUS MSA (UR, NSA latest month)
  2. Home price growth YoY (%)   — FRED FHFA HPI by MSA (quarterly, latest vs 4Q ago)
  3. Population growth YoY (%)   — Census PEP MSA estimates (annual)
  4. GDP per capita ($)          — BEA Regional MSA GDP / latest population
  5. Avg weekly wage growth (%)  — BLS QCEW MSA (private, all industries, latest qtr YoY)
  6. Building permits / 1k       — Census BPS via FRED (reporting.pull_bps), latest annual

Per-MSA per-metric fallback to the prior cached value is automatic — never blanks data.

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

# Reuse the building-permit fetcher built for the MSA reports (Census BPS via FRED).
# Same package import pattern as scripts/fetch_msa_report.py.
from reporting import pull_bps

# Atlanta MSA + the 29 county FIPS that compose it.
# Used by the home-price fetcher's stale-series fallback (see _atlanta_county_avg_hpi_yoy).
ATLANTA_CBSA = "12060"
ATLANTA_COUNTY_FIPS = [fips for fips, cbsa in COUNTY_TO_MSA.items() if cbsa == ATLANTA_CBSA]
assert len(ATLANTA_COUNTY_FIPS) == 29, f"Expected 29 Atlanta counties, got {len(ATLANTA_COUNTY_FIPS)}"

# Index: short_name -> (cbsa, full_name, population)
MSA_BY_SHORT = {short: (cbsa, full, pop) for cbsa, short, full, pop in GA_MSAS}

MSA_REPORTS_DIR = Path(__file__).parent.parent / "data" / "msa_reports"

# --------------------------------------------------------------------------- #
# Metric catalog (Phase 4 WS4 — broadened comparator)
#
# The 6 "live" metrics are pulled fresh from APIs above. The rest are rolled up
# from data/msa_reports/*.json (each metro already computes them). `polarity`
# drives heatmap colour: good_high (teal=high), good_low (teal=low), neutral
# (grey scale, no good/bad). `theme` groups columns + powers the radar's theme
# selector. `lower_is_better` is kept for the existing radar normaliser.
# --------------------------------------------------------------------------- #
def _M(key, label, unit, polarity, theme, source, fmt="num"):
    return {"key": key, "label": label, "unit": unit, "polarity": polarity,
            "lower_is_better": polarity == "good_low", "theme": theme,
            "source": source, "fmt": fmt}

# The 6 live metrics (values come from the API pulls; metadata lives here).
LIVE_METRICS = [
    _M("unemployment_rate", "Unemployment rate", "%", "good_low", "Labor", "BLS LAUS", "pct1"),
    _M("wage_growth_yoy", "Avg weekly wage growth", "% YoY", "good_high", "Labor", "BLS QCEW", "pct1"),
    _M("pop_growth_yoy", "Population growth", "% YoY", "good_high", "Growth", "Census ACS", "pct2"),
    _M("home_price_yoy", "Home-price growth", "% YoY", "good_high", "Housing", "FHFA HPI", "pct1"),
    _M("permits_per_1k", "Building permits / 1k", "/1k", "good_high", "Housing", "Census BPS", "num1"),
    _M("gdp_per_capita", "GDP per capita", "$", "good_high", "Output & income", "BEA", "usd0"),
]

# Rolled up from the metro reports. (section, *path) is the extractor; `net_rate`
# is special-cased (IRS net ÷ population).
ROLLUP_METRICS = [
    # Labor
    (_M("job_growth_yoy", "Job growth (CES)", "% YoY", "good_high", "Labor", "BLS CES", "pct1"),
        ("ces_employment", "latest_yoy")),
    # Growth
    (_M("net_migration_per_1k", "Net migration / 1k", "/1k", "good_high", "Growth", "IRS SOI", "num2"),
        ("__net_rate__",)),
    (_M("cycle_index", "Business-cycle index", "idx", "good_high", "Growth", "EIG (BLS CES+LAUS)", "num1"),
        ("business_cycle_index", "latest_value")),
    (_M("forecast_gmp_yoy", "Forecast GDP growth", "% next yr", "good_high", "Growth", "EIG forecast", "pct1"),
        ("forecast_arima", "gmp_yoy", 0)),
    # Output & income
    (_M("gmp_growth_yoy", "Real GDP growth", "% YoY", "good_high", "Output & income", "BEA CAGDP2", "pct1"),
        ("bea_gmp", "latest_yoy")),
    (_M("per_capita_income", "Per-capita income", "$", "good_high", "Output & income", "BEA CAINC1", "usd0"),
        ("bea_personal_income", "latest_per_capita_income")),
    (_M("income_growth_yoy", "Income growth", "% YoY", "good_high", "Output & income", "BEA CAINC1", "pct1"),
        ("bea_personal_income", "latest_yoy")),
    # Housing
    (_M("price_to_income", "Price-to-income", "x", "good_low", "Housing", "EIG valuation", "num1"),
        ("housing_valuation", "price_to_income_ratio")),
    (_M("valuation_pct", "Home over/under-valuation", "%", "good_low", "Housing", "EIG valuation", "pct1"),
        ("housing_valuation", "latest_valuation_pct")),
    (_M("affordability_index", "Buyer affordability", "idx", "good_high", "Housing", "EIG (NAR-style)", "num1"),
        ("housing_affordability", "latest_index")),
    (_M("rent_burden_pct", "Rent burden", "%", "good_low", "Housing", "Census ACS", "pct1"),
        ("acs_affordability", "msa_rent_burden_pct", -1)),
    (_M("pct_owner_occupied", "Home-ownership", "%", "good_high", "Housing", "Census ACS", "pct1"),
        ("acs_housing_characteristics", "derived", "pct_owner_occupied")),
    # Quality of life
    (_M("median_aqi", "Air quality (median AQI)", "AQI", "good_low", "Quality of life", "EPA AirData", "num0"),
        ("epa_air_quality", "median_aqi")),
    (_M("quality_of_life", "Quality of life", "idx", "good_high", "Quality of life", "EIG composite", "num0"),
        ("quality_of_life", "value")),
    (_M("vitality", "Economic vitality", "0-1", "good_high", "Quality of life", "EIG composite", "num2"),
        ("vitality", "value")),
    (_M("median_age", "Median age", "yrs", "neutral", "Quality of life", "Census ACS", "num1"),
        ("acs_housing_characteristics", "values", "median_age")),
    # Business
    (_M("business_formation_rate", "Business formation rate", "%", "good_high", "Business", "Census BDS", "pct1"),
        ("entrepreneurship", "entry_rate_msa")),
    (_M("cost_of_living_index", "Cost of living (US=100)", "idx", "good_low", "Business", "EIG (BEA RPP logic)", "num0"),
        ("business_costs", "cost_of_living_index")),
    (_M("business_cost_index", "Business costs (US=100)", "idx", "good_low", "Business", "EIG composite", "num0"),
        ("business_costs", "business_cost_index")),
    (_M("industrial_diversity", "Industrial diversity", "0-1", "good_high", "Business", "EIG Hachman", "num2"),
        ("industrial_diversity", "score")),
    (_M("credit_score", "Metro credit score", "0-100", "good_high", "Business", "EIG composite", "num0"),
        ("credit_score", "score")),
]

# Theme display order for the page (heatmap column groups + radar selector).
THEME_ORDER = ["Labor", "Growth", "Output & income", "Housing", "Quality of life", "Business"]


def _report_scalar(rep, path, status_ok=("live", "partial", "stale")):
    """Pull a scalar from a report by (section, *path), guarded on section_status."""
    sec = path[0]
    if rep.get("section_status", {}).get(sec) not in status_ok:
        return None
    v = rep.get("sections", {}).get(sec)
    for p in path[1:]:
        if isinstance(p, int):
            if isinstance(v, list) and -len(v) <= p < len(v):
                v = v[p]
            else:
                return None
        else:
            if isinstance(v, dict) and p in v:
                v = v[p]
            else:
                return None
    return v if isinstance(v, (int, float)) else None


def rollup_report_metrics():
    """Read all msa_reports/*.json → {short_name: {metric_key: value}} for the
    ROLLUP_METRICS catalog. Pure local read, no API keys."""
    out = {}
    if not MSA_REPORTS_DIR.exists():
        return out
    for path in sorted(MSA_REPORTS_DIR.glob("*.json")):
        try:
            rep = json.loads(path.read_text())
        except Exception:
            continue
        short = rep.get("short_name")
        if not short:
            continue
        vals = {}
        for mdef, extractor in ROLLUP_METRICS:
            key = mdef["key"]
            if extractor == ("__net_rate__",):
                net = _report_scalar(rep, ("irs_soi_migration", "net"))
                pop = rep.get("population")
                v = round(net / pop * 1000, 2) if (isinstance(net, (int, float)) and pop) else None
            else:
                v = _report_scalar(rep, extractor)
            if v is not None:
                vals[key] = v
        out[short] = vals
    return out


def build_metric_catalog():
    """The full ordered metric metadata list written to msa.json."""
    return LIVE_METRICS + [m for (m, _ex) in ROLLUP_METRICS]


def apply_rollup(existing):
    """Merge report-rollup metrics into existing['msas'] and (re)write the metric
    catalog + theme order. Live-6 values are left untouched."""
    rolled = rollup_report_metrics()
    n_filled = 0
    for msa in existing.get("msas", []):
        short = msa.get("short_name")
        m = msa.setdefault("metrics", {})
        for key, val in (rolled.get(short) or {}).items():
            m[key] = val
            n_filled += 1
    existing["metrics"] = build_metric_catalog()
    existing["theme_order"] = THEME_ORDER
    return n_filled


# ---------- HTTP helpers ----------
# FRED enforces 120 requests/minute/key (HTTP 429 over that). The county-HPI
# fallback fires one call per county, so an --all run easily bursts past the cap.
# Pace FRED calls to ~110/min proactively and back off hard on any 429 that slips
# through. Non-FRED URLs are unthrottled.
_FRED_MIN_INTERVAL = 0.55  # seconds between FRED calls (~109/min, under the 120 cap)
_last_fred_call = [0.0]


def http_get_json(url, retries=4, timeout=30):
    """Robust JSON fetch — never crashes, returns None on any failure.
    FRED (stlouisfed.org) URLs are rate-limited to stay under 120 req/min."""
    is_fred = "stlouisfed.org" in url
    last_err = None
    for attempt in range(retries):
        if is_fred:
            gap = time.monotonic() - _last_fred_call[0]
            if gap < _FRED_MIN_INTERVAL:
                time.sleep(_FRED_MIN_INTERVAL - gap)
            _last_fred_call[0] = time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                if not body.strip(): return None
                try:    return json.loads(body)
                except json.JSONDecodeError:
                    print(f"      [non-JSON] {url[:140]} → {body[:160]!r}", file=sys.stderr)
                    return None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                back = 5 * (attempt + 1)  # 5, 10, 15, 20s — let the per-minute window reset
                print(f"      [HTTP 429] FRED rate limit — backing off {back}s "
                      f"(attempt {attempt + 1}/{retries})", file=sys.stderr)
                time.sleep(back)
                continue
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


# ---------- 6. Census BPS (via FRED) — building permits per 1,000 residents ----------
def fetch_msa_permits_per_1k():
    """Latest annual building permits per 1,000 residents for each GA MSA.

    Delegates to reporting.pull_bps.fetch_bps_permits_annual (the same Census
    Building Permits Survey-via-FRED pull used by the MSA reports), which returns
    an annual block including latest_per_1k computed against the canonical MSA
    population. Returns {short_name: latest_per_1k} or None if nothing resolved.

    Per-MSA failures fall through silently — the main() update loop preserves the
    prior cached value for any MSA missing here, so the page never blanks.

    Env: FRED_API_KEY (required by pull_bps).
    """
    if not FRED_API_KEY:
        print("  [BPS/FRED] no FRED_API_KEY, skipping permits", file=sys.stderr); return None
    print(f"  [BPS/FRED] fetching permits for {len(GA_MSAS)} MSAs...")
    by_short = {}
    for cbsa, short, full, _pop in GA_MSAS:
        try:
            data = pull_bps.fetch_bps_permits_annual(cbsa, full_name=full, years_back=6)
        except Exception as e:
            print(f"    {short}: permits fetch error — {type(e).__name__}: {e}", file=sys.stderr); continue
        if not data:
            print(f"    {short}: no permit data", file=sys.stderr); continue
        per_1k = data.get("latest_per_1k")
        # Guard against null / non-positive — a 0 or None means the pull didn't
        # actually populate, and would otherwise overwrite a good fixture/cached
        # value with a misleading zero.
        if per_1k is None or per_1k <= 0:
            print(f"    {short}: permits per-1k not usable ({per_1k!r})", file=sys.stderr); continue
        by_short[short] = round(float(per_1k), 1)
        print(f"    {short}: {by_short[short]} permits/1k ({data.get('latest_year','?')})")
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

    print("\n[1/6] Unemployment (BLS LAUS MSA)")
    ur = fetch_msa_unemployment()

    print("\n[2/6] Home prices (FRED FHFA HPI)")
    hp = fetch_msa_home_price_yoy()

    print("\n[3/6] Population growth (Census PEP)")
    pop = fetch_msa_population_growth()

    print("\n[4/6] GDP per capita (BEA Regional)")
    gdp = fetch_msa_gdp_per_capita()

    print("\n[5/6] Wage growth (BLS QCEW MSA, private sector)")
    wage = fetch_msa_wage_growth_yoy()

    print("\n[6/6] Building permits per 1k (Census BPS via FRED)")
    permits = fetch_msa_permits_per_1k()

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
        if permits and short in permits: m["permits_per_1k"] = permits[short]; fetched_metrics.add("permits_per_1k")

    # Phase 4 WS4: merge the ~22 report-rollup metrics + rebuild the metric catalog.
    n_rolled = apply_rollup(existing)
    print(f"\n[WS4 rollup] merged {n_rolled} report-metric values; "
          f"catalog now {len(existing['metrics'])} metrics")

    # Recompute medians and callouts from the updated MSA data
    recompute_aggregates(existing)

    # Mark partial-live
    ALL_METRIC_KEYS = {
        "unemployment_rate", "home_price_yoy", "pop_growth_yoy",
        "gdp_per_capita", "wage_growth_yoy", "permits_per_1k",
    }
    if fetched_metrics:
        existing["_fixture"] = False
        still_fixture = sorted(ALL_METRIC_KEYS - fetched_metrics)
        note = f"Partial live data: {sorted(fetched_metrics)}."
        if still_fixture:
            note += f" Still on fixture: {still_fixture}."
        else:
            note += " All six metrics live."
        existing["_note"] = note
        base_src = ("Live: BLS LAUS (UR) + FRED FHFA HPI (home prices) + "
                    "Census ACS (population) + BEA (GDP, county-aggregated) + "
                    "BLS QCEW (wage growth, private) + Census BPS via FRED (building permits).")
        if still_fixture:
            base_src += f" Fixture: {', '.join(still_fixture)}."
        existing["source"] = base_src
    existing["fetched_at"] = TODAY.isoformat()

    with open(fixture_path, "w") as f:
        json.dump(existing, f, indent=2)

    print("\n" + "=" * 60)
    print(f"Wrote {fixture_path}")
    print(f"Metrics fetched live: {sorted(fetched_metrics) if fetched_metrics else 'NONE'}")
    print(f"Per-MSA fallback to fixture for any MSAs/metrics that failed")


def run_rollup_only():
    """--rollup: merge report metrics + rebuild catalog only; no API pulls/keys.
    Used by update-msa-reports.yml after the metro JSONs regenerate, and for
    local validation."""
    path = Path(__file__).parent.parent / "data" / "msa.json"
    if not path.exists():
        print("ERROR: data/msa.json not found", file=sys.stderr)
        return 2
    existing = json.load(open(path))
    n = apply_rollup(existing)
    recompute_aggregates(existing)
    existing["fetched_at"] = TODAY.isoformat()
    with open(path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"Wrote {path} (rollup-only): merged {n} report-metric values; "
          f"catalog now {len(existing['metrics'])} metrics across "
          f"{len(existing.get('theme_order', []))} themes")
    return 0


if __name__ == "__main__":
    if "--rollup" in sys.argv[1:]:
        raise SystemExit(run_rollup_only())
    main()
