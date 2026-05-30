"""BLS pulls for Metro Economic Profile reports.

Exposes:
  fetch_ces_employment_history(cbsa, years_back=7) -> dict
      Monthly nonfarm payroll employment for the MSA (seasonally adjusted).
      Output:
          {
            "series_id": "SMS13123060000000001",
            "months":   ["2019-01", "2019-02", ...],
            "values":   [183.2, 183.4, ...],          # thousands of jobs
            "yoy_pct":  [None, ..., 2.1, 2.3, ...],   # 12-month % change
            "latest_month": "2026-04",
            "latest_value": 216.8,
            "latest_yoy":   2.5
          }

  fetch_ces_supersector_history(cbsa, years_back=2) -> dict
      Monthly employment by NAICS super-sector for the MSA.
      Output: {"sectors": {<sector_label>: {"months":[...], "values":[...], "yoy_pct":[...]}}}

  fetch_laus_unemployment_history(cbsa, years_back=7) -> dict
      Monthly LAUS unemployment rate (NSA) for the MSA. Same shape as CES.

CES MSA series ID format (verified 2026 against live BLS API, 20 chars total):
    SMS + state_fips(2) + cbsa(5) + supersector(2) + industry(6) + datatype(2)
    Example (Savannah total nonfarm, SA, employment):
        SMS 13 42340 00 000000 01  -> SMS13423400000000001
    For NSA, swap SMS for SMU. We use SA where available.

LAUS MSA series ID format (verified 2026, 20 chars):
    LAUMT + state_fips(2) + cbsa(5) + 6 zeros + measure_code(2)
    Example (Savannah unemployment rate):
        LAUMT 13 42340 000000 03  -> LAUMT134234000000003
    measure codes: 03 = unemployment rate, 04 = unemployed, 05 = employed, 06 = labor force

NB: BLS public API allows 25 queries/day without a key, 500/day with a free key.
Our BLS_API_KEY env var (already in use by scripts/fetch_msa_metrics.py) lifts the limit.

Env: BLS_API_KEY (optional but strongly recommended)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import date
from typing import Dict, List, Optional

BLS_API_KEY = os.environ.get("BLS_API_KEY", "").strip()
BLS_ENDPOINT = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# BLS CES MSA super-sector codes (the 2-digit "supersector" position in the SMS series ID)
# Source: https://www.bls.gov/sae/additional-resources/list-of-published-state-and-metro-area-series.htm
CES_SUPERSECTORS: Dict[str, str] = {
    "00": "Total nonfarm",
    "05": "Total private",
    "10": "Mining and logging",
    "20": "Construction",
    "30": "Manufacturing",
    "40": "Trade, transportation, and utilities",
    "41": "Wholesale trade",
    "42": "Retail trade",
    "43": "Transportation, warehousing and utilities",
    "50": "Information",
    "55": "Financial activities",
    "60": "Professional and business services",
    "65": "Education and health services",
    "70": "Leisure and hospitality",
    "80": "Other services",
    "90": "Government",
}

# All GA MSAs use state FIPS 13 (BLS attributes multi-state MSAs to the state of
# the principal city — Augusta is attributed to GA even though it crosses into SC).
DEFAULT_STATE_FIPS = "13"


def _bls_request(series_ids: List[str], start_year: int, end_year: int) -> Optional[dict]:
    """POST to the BLS time-series API. Returns parsed JSON or None on failure."""
    payload = {
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear": str(end_year),
    }
    if BLS_API_KEY:
        payload["registrationkey"] = BLS_API_KEY

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BLS_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
    )

    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") != "REQUEST_SUCCEEDED":
                    msg = data.get("message", [""])[0] if data.get("message") else "?"
                    print(f"  [BLS API] non-success: {msg}", file=sys.stderr)
                    return None
                return data
        except urllib.error.HTTPError as e:
            print(f"  [BLS HTTP {e.code}] {e.reason}", file=sys.stderr)
            last_err = e
        except Exception as e:
            print(f"  [BLS error] {type(e).__name__}: {e}", file=sys.stderr)
            last_err = e
        time.sleep(1 + attempt)

    return None


def _ces_series_id(cbsa: str, supersector: str = "00", state_fips: str = DEFAULT_STATE_FIPS, sa: bool = True) -> str:
    """Build a CES MSA series ID.

    Format: SMS|SMU + state(2) + cbsa(5) + supersector(2) + industry(6) + datatype(2) = 20 chars.
    supersector "00" + industry "000000" = total of that super-sector.
    datatype 01 = all employees (thousands).
    """
    prefix = "SMS" if sa else "SMU"
    return f"{prefix}{state_fips}{cbsa}{supersector}00000001"


def _laus_series_id(cbsa: str, measure: str = "03", state_fips: str = DEFAULT_STATE_FIPS) -> str:
    """Build a LAUS MSA series ID.

    Format: LAUMT + state(2) + cbsa(5) + 6 zeros + measure(2) = 20 chars.
    measure 03 = unemployment rate, 04 = unemployed, 05 = employed, 06 = labor force.
    """
    return f"LAUMT{state_fips}{cbsa}000000{measure}"


def _flatten_observations(series_data: dict) -> List[dict]:
    """Sort BLS observations by period ASC and return [{period, value}, ...]."""
    obs = []
    for entry in series_data.get("data", []):
        try:
            year = int(entry["year"])
            period = entry["period"]  # M01..M12 or Q01..Q04
            if not period.startswith("M"):
                continue
            month = int(period[1:])
            value = float(entry["value"])
            obs.append({"ym": f"{year:04d}-{month:02d}", "value": value})
        except (KeyError, ValueError):
            continue
    obs.sort(key=lambda o: o["ym"])
    return obs


def _yoy_pct(values: List[Optional[float]], periods_per_year: int = 12) -> List[Optional[float]]:
    """Year-over-year % change for a monthly series."""
    out: List[Optional[float]] = [None] * len(values)
    for i, v in enumerate(values):
        if i < periods_per_year or v is None:
            continue
        prior = values[i - periods_per_year]
        if prior is None or prior == 0:
            continue
        out[i] = round(100 * (v - prior) / prior, 2)
    return out


# ----------------------------- Public API -----------------------------

def fetch_ces_employment_history(cbsa: str, years_back: int = 7) -> Optional[dict]:
    """Monthly nonfarm payroll employment, last N years."""
    series_id = _ces_series_id(cbsa)
    end_year = date.today().year
    start_year = end_year - years_back

    data = _bls_request([series_id], start_year, end_year)
    if not data or not data.get("Results", {}).get("series"):
        return None

    series = data["Results"]["series"][0]
    obs = _flatten_observations(series)
    if not obs:
        return None

    months = [o["ym"] for o in obs]
    values = [o["value"] for o in obs]
    yoy = _yoy_pct(values)

    return {
        "series_id": series_id,
        "months": months,
        "values": values,
        "yoy_pct": yoy,
        "latest_month": months[-1],
        "latest_value": values[-1],
        "latest_yoy": yoy[-1],
    }


def fetch_ces_supersector_history(cbsa: str, years_back: int = 2) -> Optional[dict]:
    """Monthly employment by super-sector, last N years.

    Strategy: For smaller MSAs, BLS only publishes seasonally-adjusted (SMS)
    series for "Total nonfarm" — most other supersectors are NSA-only (SMU).
    We request BOTH the SA and NSA variant for each supersector in a single
    BLS call (32 series), then prefer the SA series where it returned data,
    falling back to NSA otherwise. This recovers all 15-16 sectors for any
    MSA, regardless of disclosure rules.

    Returns: {"sectors": {<label>: {"months":[...], "values":[...], "yoy_pct":[...], "latest_yoy": float, "adjustment": "SA"|"NSA"}}}
    """
    series_map = {}  # series_id -> (ss_code, label, sa_flag)
    sa_series_ids = []
    nsa_series_ids = []
    for ss_code, label in CES_SUPERSECTORS.items():
        sa_sid = _ces_series_id(cbsa, supersector=ss_code, sa=True)
        nsa_sid = _ces_series_id(cbsa, supersector=ss_code, sa=False)
        series_map[sa_sid] = (ss_code, label, True)
        series_map[nsa_sid] = (ss_code, label, False)
        sa_series_ids.append(sa_sid)
        nsa_series_ids.append(nsa_sid)

    # BLS API allows up to 50 series per request — we have 32, single call is fine.
    end_year = date.today().year
    start_year = end_year - years_back

    data = _bls_request(sa_series_ids + nsa_series_ids, start_year, end_year)
    if not data or not data.get("Results", {}).get("series"):
        return None

    # First pass: collect all series with data, keyed by (ss_code, sa_flag)
    parsed: Dict[tuple, dict] = {}
    for series in data["Results"]["series"]:
        sid = series["seriesID"]
        if sid not in series_map:
            continue
        ss_code, label, sa_flag = series_map[sid]
        obs = _flatten_observations(series)
        if not obs:
            continue
        months = [o["ym"] for o in obs]
        values = [o["value"] for o in obs]
        yoy = _yoy_pct(values)
        parsed[(ss_code, sa_flag)] = {
            "supersector_code": ss_code,
            "label":           label,
            "series_id":       sid,
            "months":          months,
            "values":          values,
            "yoy_pct":         yoy,
            "latest_yoy":      yoy[-1] if yoy else None,
            "adjustment":      "SA" if sa_flag else "NSA",
        }

    # Second pass: prefer SA, fall back to NSA per supersector
    sectors: Dict[str, dict] = {}
    for ss_code, label in CES_SUPERSECTORS.items():
        rec = parsed.get((ss_code, True)) or parsed.get((ss_code, False))
        if rec is None:
            continue
        # Pop the helper field so the JSON keys match the prior schema
        rec_out = {k: v for k, v in rec.items() if k != "label"}
        sectors[label] = rec_out

    if not sectors:
        return None

    return {"sectors": sectors}


def fetch_laus_unemployment_history(cbsa: str, years_back: int = 7) -> Optional[dict]:
    """Monthly LAUS unemployment rate (%), last N years."""
    series_id = _laus_series_id(cbsa)
    end_year = date.today().year
    start_year = end_year - years_back

    data = _bls_request([series_id], start_year, end_year)
    if not data or not data.get("Results", {}).get("series"):
        return None

    series = data["Results"]["series"][0]
    obs = _flatten_observations(series)
    if not obs:
        return None

    months = [o["ym"] for o in obs]
    values = [o["value"] for o in obs]

    return {
        "series_id": series_id,
        "months": months,
        "values": values,
        "latest_month": months[-1],
        "latest_value": values[-1],
    }


# ----------------------------- QCEW: industry shares + wages -----------------------------
# QCEW Open Data API: https://data.bls.gov/cew/data/api/{year}/{quarter}/area/{area_code}.csv
# MSA area code = "C" + first 4 digits of CBSA (Census MSA codes all end in 0).
# State area code = state FIPS + 2 zeros (e.g. Georgia = "13000").
# National = "US000".

# Super-sector groupings used in the Comparative Employment & Income table.
# Codes are QCEW NAICS *sector* codes as they actually appear in the by-area CSV:
# manufacturing, retail, and transportation are emitted as HYPHENATED sector rows
# ("31-33", "44-45", "48-49"), not as bare 2-digit codes. Manufacturing cannot be
# split into durable/nondurable at the sector level — that needs a 3-digit subsector
# pull (a future enhancement, shared with the Diffusion Index).
QCEW_SUPERSECTORS = [
    ("Mining",                          ["21"]),
    ("Construction",                    ["23"]),
    ("Manufacturing",                   ["31-33"]),
    ("Transportation/Utilities",        ["22", "48-49"]),
    ("Wholesale Trade",                 ["42"]),
    ("Retail Trade",                    ["44-45"]),
    ("Information",                     ["51"]),
    ("Financial Activities",            ["52", "53"]),
    ("Prof. & Bus. Services",           ["54", "55", "56"]),
    ("Education & Health Services",     ["61", "62"]),
    ("Leisure & Hospitality",           ["71", "72"]),
    ("Other Services",                  ["81"]),
]
# Flattened set of every code we recognize — used to filter QCEW rows to the
# sector level (skips total "10", domain "101", and 3-digit subsector rows).
QCEW_SECTOR_CODES = {c for _, codes in QCEW_SUPERSECTORS for c in codes}


def _qcew_msa_area_code(cbsa: str) -> str:
    """QCEW MSA area code = 'C' + first 4 digits of 5-digit CBSA (the trailing 0 is dropped).
    Verified against the existing scripts/fetch_msa_metrics.py."""
    return "C" + cbsa[:4]


def _qcew_fetch_csv(year: int, quarter: int, area_code: str):
    """GET one QCEW CSV. Returns a list of dict rows, or None on failure."""
    import csv as _csv
    import io as _io
    url = f"https://data.bls.gov/cew/data/api/{year}/{quarter}/area/{area_code}.csv"
    try:
        with urllib.request.urlopen(url, timeout=45) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  [QCEW {year}-Q{quarter} {area_code}] HTTP {e.code}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [QCEW {year}-Q{quarter} {area_code}] {type(e).__name__}: {e}", file=sys.stderr)
        return None
    return list(_csv.DictReader(_io.StringIO(body)))


def _qcew_latest_quarter(probe_area: str = "C1206") -> Optional[tuple]:
    """Find the latest published QCEW quarter by probing Atlanta backward from today."""
    today = date.today()
    cur_q = (today.month - 1) // 3 + 1
    for back in range(0, 6):
        y = today.year
        q = cur_q - back
        while q < 1:
            q += 4
            y -= 1
        rows = _qcew_fetch_csv(y, q, probe_area)
        if rows:
            return (y, q)
    return None


def _qcew_aggregate_sectors(rows: list, own_codes: tuple = ("5",)) -> Dict[str, dict]:
    """Aggregate QCEW rows up into our standard super-sectors.

    own_codes: tuple of QCEW ownership codes to include.
        '5' = Private (default)
        '1','2','3' = Federal/State/Local (use ("1","2","3") for government)

    Returns: {sector_label: {"employment": int, "weekly_wage_weighted": float, "annual_wage_avg": int}}
    """
    out: Dict[str, dict] = {}
    for sector_label, naics_prefixes in QCEW_SUPERSECTORS:
        out[sector_label] = {"employment": 0, "wage_total": 0.0, "n": 0}

    for row in rows:
        if row.get("own_code") not in own_codes:
            continue
        # Keep only the NAICS sector-level rows we recognize. QCEW emits manufacturing,
        # retail and transportation as hyphenated codes ("31-33", "44-45", "48-49"),
        # so a len()==2 test silently dropped them — match the explicit code set instead.
        ic = (row.get("industry_code") or "").strip()
        if ic not in QCEW_SECTOR_CODES:
            continue
        # Total size only (size breakdowns live in separate Q1 size files).
        if row.get("size_code") != "0":
            continue
        # Quarterly by-area CSVs have NO annual_avg_emplvl field — employment is in
        # month{1,2,3}_emplvl. Use the third (quarter-end) month, with fallbacks so the
        # same code still works against annual files if ever pointed at one.
        try:
            emp = 0
            for fld in ("month3_emplvl", "month2_emplvl", "month1_emplvl", "annual_avg_emplvl"):
                raw = row.get(fld)
                if raw not in (None, "", "0"):
                    emp = int(float(raw))
                    break
            wage = float(row.get("avg_wkly_wage") or 0)
        except ValueError:
            continue
        if emp <= 0 or wage <= 0:
            continue
        for sector_label, naics_prefixes in QCEW_SUPERSECTORS:
            if ic in naics_prefixes:
                out[sector_label]["employment"] += emp
                out[sector_label]["wage_total"] += wage * emp  # employment-weighted
                out[sector_label]["n"] += 1
                break

    # Finalize: annual wage = average weekly × 52
    finalized = {}
    for sector, agg in out.items():
        emp = agg["employment"]
        if emp == 0:
            continue
        avg_weekly = agg["wage_total"] / emp
        finalized[sector] = {
            "employment":      emp,
            "avg_annual_wage": int(round(avg_weekly * 52)),
        }
    return finalized


def fetch_qcew_industry_shares(cbsa: str) -> Optional[dict]:
    """Snapshot of QCEW employment shares + average annual wages for the MSA, GA, and US.

    Powers the 'Comparative Employment & Income' table.

    Returns:
        {
          "year": 2025, "quarter": 4,
          "msa": {<sector>: {"employment": N, "share_pct": X.X, "avg_annual_wage": $}},
          "ga":  {<sector>: {...}},   # state of GA total
          "us":  {<sector>: {...}},
          "totals": {"msa": N, "ga": N, "us": N}
        }
    """
    latest = _qcew_latest_quarter()
    if not latest:
        print("  [QCEW] could not determine latest quarter", file=sys.stderr)
        return None
    year, quarter = latest

    msa_area = _qcew_msa_area_code(cbsa)
    state_area = DEFAULT_STATE_FIPS + "000"
    us_area = "US000"

    msa_rows = _qcew_fetch_csv(year, quarter, msa_area)
    ga_rows  = _qcew_fetch_csv(year, quarter, state_area)
    us_rows  = _qcew_fetch_csv(year, quarter, us_area)
    if not (msa_rows and ga_rows and us_rows):
        return None

    def aggregate_and_share(rows):
        # Combine private + government for total employment denominator.
        private = _qcew_aggregate_sectors(rows, ("5",))
        gov     = _qcew_aggregate_sectors(rows, ("1", "2", "3"))
        # Tack Government on as an extra sector entry
        gov_emp = sum(s["employment"] for s in gov.values())
        gov_wage = (sum(s["avg_annual_wage"] * s["employment"] for s in gov.values()) // gov_emp) if gov_emp else 0
        combined = dict(private)
        if gov_emp > 0:
            combined["Government"] = {"employment": gov_emp, "avg_annual_wage": gov_wage}
        total = sum(s["employment"] for s in combined.values())
        # Compute share %
        for s in combined.values():
            s["share_pct"] = round(100 * s["employment"] / total, 2) if total else 0
        return combined, total

    msa_data, msa_tot = aggregate_and_share(msa_rows)
    ga_data,  ga_tot  = aggregate_and_share(ga_rows)
    us_data,  us_tot  = aggregate_and_share(us_rows)

    # Guard against a "false-live" payload: if nothing aggregated (e.g. a schema
    # change or a wrong employment field) the orchestrator would otherwise mark an
    # empty {} as "live" and the page would silently stay on demo data.
    if not msa_data or msa_tot <= 0:
        print("  [QCEW] aggregation produced no MSA sectors — treating as failed", file=sys.stderr)
        return None

    return {
        "year": year,
        "quarter": quarter,
        "as_of_label": f"{year} Q{quarter}",
        "msa": msa_data,
        "ga": ga_data,
        "us": us_data,
        "totals": {"msa": msa_tot, "ga": ga_tot, "us": us_tot},
    }


def fetch_qcew_yoy_changes(cbsa: str) -> Optional[dict]:
    """Year-over-year % change in total employment by super-sector for the MSA.

    Powers the 'Industry Employment' bar chart.

    Returns: {"year": Y, "quarter": Q, "sectors": {<label>: {"yoy_pct": float, "employment": int}}}
    """
    latest = _qcew_latest_quarter()
    if not latest:
        return None
    year_now, qtr = latest

    msa_area = _qcew_msa_area_code(cbsa)
    cur = _qcew_fetch_csv(year_now, qtr, msa_area)
    prv = _qcew_fetch_csv(year_now - 1, qtr, msa_area)
    if not (cur and prv):
        return None

    cur_agg = _qcew_aggregate_sectors(cur, ("5",))
    prv_agg = _qcew_aggregate_sectors(prv, ("5",))

    out = {}
    for sector, c in cur_agg.items():
        p = prv_agg.get(sector)
        if not p or p["employment"] == 0:
            continue
        yoy = round(100 * (c["employment"] - p["employment"]) / p["employment"], 2)
        out[sector] = {"yoy_pct": yoy, "employment": c["employment"]}

    if not out:
        print("  [QCEW] no YoY sectors computed — treating as failed", file=sys.stderr)
        return None

    return {
        "year": year_now,
        "quarter": qtr,
        "as_of_label": f"{year_now} Q{qtr}",
        "sectors": out,
    }


# ----------------------------- Economic Health Check (quarterly) -----------------------------

def _quarter_of_month(ym: str):
    """'2026-03' -> (2026, 1)."""
    return (int(ym[:4]), (int(ym[5:7]) - 1) // 3 + 1)


def fetch_health_check_quarterly(cbsa: str, n_quarters: int = 6) -> Optional[dict]:
    """Recent-quarters trajectory table for the Economic Health Check.

    QCEW MSA totals (total covered, all industries): employment, average weekly wage,
    establishment count — plus LAUS unemployment rate and labor force, averaged from
    monthly to quarterly. Columns are anchored to QCEW's available quarters (the slower-
    releasing source) so every row is fully populated rather than ragged.

    Participation rate and average weekly hours are intentionally omitted — neither has a
    clean quarterly MSA-level source (participation needs a working-age denominator that
    only ACS publishes annually; CES weekly hours are national/state only).

    Returns:
        {
          "quarters": ["2024 Q2", ...],
          "employment_000s": [...], "avg_weekly_wage": [...], "establishments": [...],
          "unemployment_rate": [...], "labor_force_000s": [...],
          "as_of_label": "2025 Q3", "source": "..."
        }
    Returns None if QCEW totals can't be built. Pure stdlib.
    """
    latest = _qcew_latest_quarter()
    if not latest:
        return None
    msa_area = _qcew_msa_area_code(cbsa)

    # --- QCEW total-covered, all-industries row for the last n_quarters+1 quarters ---
    qcew: Dict[tuple, dict] = {}
    yy, qq = latest
    for _ in range(n_quarters + 1):
        rows = _qcew_fetch_csv(yy, qq, msa_area)
        if rows:
            for r in rows:
                ic = (r.get("industry_code") or "").strip()
                if r.get("own_code") == "0" and ic == "10" and r.get("size_code") == "0":
                    try:
                        emp = int(float(r.get("month3_emplvl") or 0))
                        wage = int(float(r.get("avg_wkly_wage") or 0))
                        est = int(float(r.get("qtrly_estabs") or 0))
                    except ValueError:
                        break
                    if emp > 0:
                        qcew[(yy, qq)] = {"emp": emp, "wage": wage, "estabs": est}
                    break
        qq -= 1
        if qq < 1:
            qq = 4
            yy -= 1
    if not qcew:
        print("  [HealthCheck] no QCEW totals — treating as failed", file=sys.stderr)
        return None

    # --- LAUS monthly rate (03) + labor force (06), averaged by quarter ---
    end_year = date.today().year
    start_year = end_year - 3
    rate_sid = _laus_series_id(cbsa, "03")
    lf_sid = _laus_series_id(cbsa, "06")
    rate_q: Dict[tuple, float] = {}
    lf_q: Dict[tuple, float] = {}
    laus = _bls_request([rate_sid, lf_sid], start_year, end_year)
    if laus and laus.get("Results", {}).get("series"):
        for s in laus["Results"]["series"]:
            bucket: Dict[tuple, list] = {}
            for o in _flatten_observations(s):
                bucket.setdefault(_quarter_of_month(o["ym"]), []).append(o["value"])
            avg = {k: sum(v) / len(v) for k, v in bucket.items()}
            if s["seriesID"] == rate_sid:
                rate_q = avg
            elif s["seriesID"] == lf_sid:
                lf_q = avg

    quarters = sorted(qcew.keys())[-n_quarters:]
    labels = [f"{y} Q{q}" for (y, q) in quarters]
    return {
        "quarters": labels,
        "employment_000s":   [round(qcew[k]["emp"] / 1000, 1) for k in quarters],
        "avg_weekly_wage":   [qcew[k]["wage"] for k in quarters],
        "establishments":    [qcew[k]["estabs"] for k in quarters],
        "unemployment_rate": [round(rate_q[k], 1) if k in rate_q else None for k in quarters],
        "labor_force_000s":  [round(lf_q[k] / 1000, 1) if k in lf_q else None for k in quarters],
        "as_of_label": labels[-1],
        "source": "BLS QCEW (MSA totals) + LAUS (quarterly-averaged); EIG-assembled",
    }


# ----------------------------- CLI for quick smoke tests -----------------------------

if __name__ == "__main__":
    cbsa = sys.argv[1] if len(sys.argv) > 1 else "42340"  # Savannah default
    print(f"Fetching BLS data for CBSA {cbsa} ...", file=sys.stderr)

    emp = fetch_ces_employment_history(cbsa)
    if emp:
        print(f"  CES total nonfarm: latest {emp['latest_month']} = {emp['latest_value']:.1f}K  ({emp['latest_yoy']:+.2f}% YoY)")
        print(f"  History: {len(emp['months'])} months")

    ss = fetch_ces_supersector_history(cbsa)
    if ss:
        print(f"  CES super-sectors: {len(ss['sectors'])} sectors fetched")
        for label, d in sorted(ss["sectors"].items(), key=lambda x: -(x[1]["latest_yoy"] or -999)):
            if d["latest_yoy"] is not None:
                print(f"    {label:48s}  {d['latest_yoy']:+6.2f}% YoY")

    laus = fetch_laus_unemployment_history(cbsa)
    if laus:
        print(f"  LAUS unemployment: latest {laus['latest_month']} = {laus['latest_value']:.1f}%")
