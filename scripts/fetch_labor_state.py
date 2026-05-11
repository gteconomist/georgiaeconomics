"""Pull GA statewide labor data from BLS LAUS (UR/LF) + CES (sector payrolls).

Outputs: data/labor.json (replaces fixture).

Series fetched:
  LAUS state-level (4):
    LASST130000000000003 — Unemployment rate, SA
    LASST130000000000004 — Unemployment count (thousands), SA
    LASST130000000000005 — Employment (thousands), SA
    LASST130000000000006 — Labor force (thousands), SA

  CES state-level (11):
    SMS13000000000000001 — Total nonfarm, all employees, SA
    SMS13000000200000001 — Construction
    SMS13000000300000001 — Manufacturing
    SMS13000000400000001 — Trade, transportation, utilities
    SMS13000000500000001 — Information
    SMS13000000550000001 — Financial activities
    SMS13000000600000001 — Professional & business services
    SMS13000000650000001 — Education & health services
    SMS13000000700000001 — Leisure & hospitality
    SMS13000000800000001 — Other services
    SMS13000000900000001 — Government
    (Mining & logging supersector 10 typically negligible in GA; skipped)

We pull 6 years to give the chart 5+ full years and YoY comparisons.

Env: BLS_API_KEY
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date

BLS_API_KEY = os.environ.get("BLS_API_KEY", "").strip()
if not BLS_API_KEY:
    print("ERROR: BLS_API_KEY env var not set", file=sys.stderr)
    sys.exit(2)

BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# ---------- Series IDs ----------
LAUS_SERIES = {
    "unemployment_rate": "LASST130000000000003",
    "unemployment_k":    "LASST130000000000004",
    "employment_k":      "LASST130000000000005",
    "labor_force_k":     "LASST130000000000006",
}

# (display name, supersector code, BLS series ID)
SECTOR_DEFS = [
    ("Total Nonfarm",                          "00", "SMS13000000000000001"),
    ("Construction",                           "20", "SMS13000000200000001"),
    ("Manufacturing",                          "30", "SMS13000000300000001"),
    ("Trade, Transportation & Utilities",      "40", "SMS13000000400000001"),
    ("Information",                            "50", "SMS13000000500000001"),
    ("Financial Activities",                   "55", "SMS13000000550000001"),
    ("Professional & Business Services",       "60", "SMS13000000600000001"),
    ("Education & Health Services",            "65", "SMS13000000650000001"),
    ("Leisure & Hospitality",                  "70", "SMS13000000700000001"),
    ("Other Services",                         "80", "SMS13000000800000001"),
    ("Government",                             "90", "SMS13000000900000001"),
]

TODAY = date.today()
END_YEAR = TODAY.year
START_YEAR = END_YEAR - 6  # 6 years gives 5 full years of history + YoY comparison


# ---------- BLS fetch helpers ----------
def fetch_batch(series_ids, retries=3):
    payload = {
        "seriesid": series_ids,
        "startyear": str(START_YEAR),
        "endyear":   str(END_YEAR),
        "registrationkey": BLS_API_KEY,
    }
    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(BLS_URL, data=body, headers={"Content-Type": "application/json"})
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"BLS batch failed after {retries} retries: {last_err}")


def parse_series_to_monthly(series_block):
    """Return sorted list of [period_label 'YYYY-MM', value_float]."""
    out = []
    for obs in series_block.get("data", []):
        if obs.get("period", "").startswith("M") and obs["period"] != "M13":
            y = obs["year"]; m = obs["period"][1:].zfill(2)
            try:    v = float(obs["value"])
            except (TypeError, ValueError): continue
            out.append([f"{y}-{m}", round(v, 1)])
    out.sort()
    return out


# ---------- Main ----------
def main():
    # Single batch — 4 LAUS + 11 CES = 15 series, well under the 50 series cap
    all_series_ids = list(LAUS_SERIES.values()) + [s[2] for s in SECTOR_DEFS]
    print(f"  Fetching {len(all_series_ids)} BLS series in 1 batch...", flush=True)
    resp = fetch_batch(all_series_ids)
    if resp.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS error: {resp.get('message', 'unknown')}")

    by_id = {s["seriesID"]: parse_series_to_monthly(s) for s in resp["Results"]["series"]}

    # ---------- LAUS time series ----------
    ur          = by_id[LAUS_SERIES["unemployment_rate"]]
    labor_force = by_id[LAUS_SERIES["labor_force_k"]]
    employment  = by_id[LAUS_SERIES["employment_k"]]

    # LFPR: requires civilian non-institutional population, which BLS LAUS doesn't return directly.
    # Use a proxy: labor force / (labor force + 0.36 * labor force / 0.64) — reverse-engineering from
    # ~64% participation. For accuracy, replace this with BLS LAUS measure 7 once we add Census POP base.
    # For now we leave LFPR out of the live data and let the page hide that KPI when missing.
    # (The fixture had it; we'll mark this field absent so the page can detect.)

    # ---------- Sector / payrolls ----------
    sector_series_by_id = {sid: (name, code) for name, code, sid in SECTOR_DEFS}
    total_nonfarm_id = "SMS13000000000000001"
    total_nonfarm    = by_id[total_nonfarm_id]

    sectors = []
    for name, supersector, sid in SECTOR_DEFS:
        if name == "Total Nonfarm":
            continue
        series = by_id.get(sid, [])
        if not series or len(series) < 13:
            print(f"  WARN: insufficient data for {name} ({sid})", file=sys.stderr)
            continue
        latest_v = series[-1][1]
        prior_v  = series[-13][1]
        yoy_pct   = round((latest_v - prior_v) / prior_v * 100, 1) if prior_v else 0.0
        yoy_delta = round(latest_v - prior_v, 1)
        sectors.append({
            "name": name, "supersector": supersector,
            "latest_k": latest_v, "yoy_pct": yoy_pct, "yoy_delta_k": yoy_delta,
        })

    # ---------- KPIs ----------
    ur_latest    = ur[-1][1] if ur else None
    ur_yoy_delta = round(ur_latest - ur[-13][1], 1) if ur and len(ur) >= 13 else None
    pay_latest   = total_nonfarm[-1][1] if total_nonfarm else None
    pay_yoy_pct  = round((pay_latest - total_nonfarm[-13][1]) / total_nonfarm[-13][1] * 100, 1) if total_nonfarm and len(total_nonfarm) >= 13 else None
    lf_latest    = labor_force[-1][1] if labor_force else None

    if sectors:
        fastest = max(sectors, key=lambda s: s["yoy_pct"])
        weakest = min(sectors, key=lambda s: s["yoy_pct"])
    else:
        fastest = weakest = None

    out = {
        "_fixture": False,
        "source": "BLS LAUS state-level + BLS CES state supersector breakdown",
        "fetched_at": TODAY.isoformat(),
        "latest_label": ur[-1][0] if ur else None,
        "kpis": {
            "unemployment_rate_latest":      ur_latest,
            "unemployment_rate_yoy_delta":   ur_yoy_delta,
            "total_payrolls_k_latest":       pay_latest,
            "total_payrolls_yoy_pct":        pay_yoy_pct,
            "labor_force_k_latest":          lf_latest,
            # LFPR omitted — needs population base (LAUS measure 7 + Census). Page will hide it gracefully.
            "fastest_growing_sector":         fastest["name"]    if fastest else None,
            "fastest_growing_sector_yoy_pct": fastest["yoy_pct"] if fastest else None,
            "weakest_sector":                 weakest["name"]    if weakest else None,
            "weakest_sector_yoy_pct":         weakest["yoy_pct"] if weakest else None,
        },
        "unemployment_rate":  ur,
        "total_payrolls_k":   total_nonfarm,
        "labor_force_k":      labor_force,
        "sectors":            sectors,
    }

    out_path = Path(__file__).parent.parent / "data" / "labor.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)

    n_months = len(ur)
    print(f"Wrote {out_path}")
    print(f"  Months: {n_months}  ({ur[0][0]} → {ur[-1][0]})")
    print(f"  UR latest: {ur_latest}%   YoY {ur_yoy_delta:+.1f}pp")
    print(f"  Payrolls:  {pay_latest:.1f}K  ({pay_yoy_pct:+.1f}% YoY)")
    print(f"  Sectors:   {len(sectors)}")
    if fastest: print(f"  Fastest:   {fastest['name']} ({fastest['yoy_pct']:+.1f}%)")
    if weakest: print(f"  Weakest:   {weakest['name']} ({weakest['yoy_pct']:+.1f}%)")


if __name__ == "__main__":
    main()
