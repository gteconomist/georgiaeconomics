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
from _ga_msas import GA_MSAS

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
        by_short[short] = yoy
        print(f"    {short}: HPI YoY {yoy:+.1f}% ({vals[-5][0]} → {vals[-1][0]})")
    return by_short or None


# ---------- 3. Census ACS 1-year — MSA population growth YoY ----------
# Switched from PEP (which 404'd at the URL level on every vintage we tried) to ACS 1-year.
# ACS 1-year estimates are published annually for MSAs with >65K population — covers all 14 GA MSAs.
# Variable B01003_001E = Total population.
def fetch_msa_population_growth():
    if not CENSUS_API_KEY:
        print("  [Census] no key, skipping population", file=sys.stderr); return None
    cbsa_to_short = {cbsa: short for cbsa, short, _, _ in GA_MSAS}

    def fetch_acs_year(year):
        """Returns dict {cbsa: population} for given ACS year."""
        url = (f"https://api.census.gov/data/{year}/acs/acs1"
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

    # Try recent year pairs. ACS 1-year data typically lags by ~1 year.
    # In May 2026 we expect ACS 2024 just-released; ACS 2023 definitely available.
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


# ---------- 4. BEA MSA GDP per capita ----------
def fetch_msa_gdp_per_capita():
    """Try GeoFips=MSA (all MSAs in one call) first, fall back to per-MSA queries.

    BEA Regional API: TableName=CAGDP2 (or CAGDP1) — GDP by Metropolitan Area
    LineCode varies by table — for CAGDP2 LineCode=1 is "All industry total" (real GDP).
    """
    if not BEA_API_KEY:
        print("  [BEA] no key, skipping GDP", file=sys.stderr); return None
    cbsa_to_short = {cbsa: short for cbsa, short, _, _ in GA_MSAS}
    cbsa_to_pop   = {cbsa: pop for cbsa, _, _, pop in GA_MSAS}

    def query_bea(year, geofips, table="CAGDP2", linecode="1"):
        url = (f"https://apps.bea.gov/api/data/?UserID={BEA_API_KEY}"
               f"&method=GetData&DataSetName=Regional&TableName={table}"
               f"&LineCode={linecode}&GeoFips={geofips}&Year={year}&ResultFormat=JSON")
        return http_get_json(url, timeout=45)

    def parse_rows(resp):
        if not resp: return []
        results = (resp.get("BEAAPI", {}) or {}).get("Results", {})
        if isinstance(results, list): results = results[0] if results else {}
        if not results: return []
        if "Error" in results:
            err = (results.get("Error") or {})
            if isinstance(err, list): err = err[0] if err else {}
            print(f"    BEA error: {err.get('APIErrorDescription', err)}", file=sys.stderr)
            return []
        return results.get("Data", []) or []

    # Strategy A: get all MSAs in one call (faster but less reliable for batched IDs)
    # Strategy B: query each MSA individually
    for year in (TODAY.year - 2, TODAY.year - 3):
        for table, linecode in (("CAGDP2", "1"), ("CAGDP1", "1")):
            print(f"  [BEA] year={year} table={table} (all-MSA strategy)...")
            resp  = query_bea(year, "MSA", table=table, linecode=linecode)
            rows  = parse_rows(resp)
            print(f"    got {len(rows)} rows total")

            by_short = {}
            for row in rows:
                cbsa = (row.get("GeoFips", "") or "").strip()
                short = cbsa_to_short.get(cbsa)
                if not short: continue
                try: gdp_millions = float((row.get("DataValue") or "0").replace(",", ""))
                except (TypeError, ValueError): continue
                pop = cbsa_to_pop.get(cbsa, 0)
                if pop <= 0: continue
                gdp_per_cap = round(gdp_millions * 1_000_000 / pop, 0)
                by_short[short] = int(gdp_per_cap)
                print(f"    {short}: GDP/cap ${int(gdp_per_cap):,} ({year}, GDP=${gdp_millions:,.0f}M)")

            if len(by_short) >= 5:
                return by_short

        # Strategy B: per-MSA queries for this year
        print(f"  [BEA] year={year} per-MSA strategy...")
        by_short = {}
        for cbsa, short, _, pop in GA_MSAS:
            resp = query_bea(year, cbsa, table="CAGDP2", linecode="1")
            rows = parse_rows(resp)
            if not rows: continue
            for row in rows:
                try: gdp_millions = float((row.get("DataValue") or "0").replace(",", ""))
                except (TypeError, ValueError): continue
                if pop <= 0: continue
                gdp_per_cap = round(gdp_millions * 1_000_000 / pop, 0)
                by_short[short] = int(gdp_per_cap)
                print(f"    {short}: GDP/cap ${int(gdp_per_cap):,}")
                break

        if len(by_short) >= 5:
            return by_short

    return None


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

    print("\n[1/4] Unemployment (BLS LAUS MSA)")
    ur = fetch_msa_unemployment()

    print("\n[2/4] Home prices (FRED FHFA HPI)")
    hp = fetch_msa_home_price_yoy()

    print("\n[3/4] Population growth (Census PEP)")
    pop = fetch_msa_population_growth()

    print("\n[4/4] GDP per capita (BEA Regional)")
    gdp = fetch_msa_gdp_per_capita()

    # Update per-MSA metrics — fall back to fixture for missing values
    fetched_metrics = set()
    for msa in existing.get("msas", []):
        short = msa["short_name"]
        m = msa.setdefault("metrics", {})
        if ur  and short in ur:  m["unemployment_rate"] = ur[short];      fetched_metrics.add("unemployment_rate")
        if hp  and short in hp:  m["home_price_yoy"]    = hp[short];      fetched_metrics.add("home_price_yoy")
        if pop and short in pop: m["pop_growth_yoy"]    = pop[short];     fetched_metrics.add("pop_growth_yoy")
        if gdp and short in gdp: m["gdp_per_capita"]    = gdp[short];     fetched_metrics.add("gdp_per_capita")

    # Recompute medians and callouts from the updated MSA data
    recompute_aggregates(existing)

    # Mark partial-live
    if fetched_metrics:
        existing["_fixture"] = False
        existing["_note"] = (
            f"Partial live data: {sorted(fetched_metrics)}. "
            f"Wage growth & permits-per-1k still on fixture (next iteration)."
        )
        existing["source"] = "Live: BLS LAUS (UR) + FRED FHFA HPI (home prices) + Census PEP (population) + BEA (GDP). Fixture: wage_growth_yoy, permits_per_1k."
    existing["fetched_at"] = TODAY.isoformat()

    with open(fixture_path, "w") as f:
        json.dump(existing, f, indent=2)

    print("\n" + "=" * 60)
    print(f"Wrote {fixture_path}")
    print(f"Metrics fetched live: {sorted(fetched_metrics) if fetched_metrics else 'NONE'}")
    print(f"Per-MSA fallback to fixture for any MSAs/metrics that failed")


if __name__ == "__main__":
    main()
