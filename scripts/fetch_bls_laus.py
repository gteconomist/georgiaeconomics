"""Pull BLS LAUS county-level unemployment for all 159 Georgia counties.

LAUS series ID format for county UR (unemployment rate, seasonally adjusted):
    LAUCN{ssccc}0000000003
  where ssccc = 5-digit state+county FIPS (e.g., 13121 = Fulton, GA)
  and  ...03 = unemployment rate measure

We pull 13 months so the time-slider has a full year + the prior baseline.

Output: data/counties.json — same shape as the fixture.
Env: BLS_API_KEY (registered key, free; raises rate limit to 500 queries/day).

Usage (locally):
    BLS_API_KEY=your_key python scripts/fetch_bls_laus.py
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent))
from _ga_counties import GA_COUNTIES

BLS_API_KEY = os.environ.get("BLS_API_KEY", "").strip()
if not BLS_API_KEY:
    print("ERROR: BLS_API_KEY env var not set", file=sys.stderr)
    sys.exit(2)

BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# LAUS measure codes:
#   03 = unemployment rate
#   04 = unemployment (count)
#   05 = employment
#   06 = labor force
MEASURE_UNEMP_RATE = "03"

# Date range: 13 most-recent calendar months
TODAY = date.today()
END_YEAR = TODAY.year
# LAUS typically has a ~5-week lag, so we ask for the full current year and prior year and slice in post.
START_YEAR = END_YEAR - 1

# BLS API caps at 50 series per request. 159 counties -> 4 batches.
BATCH_SIZE = 50

def laus_series_id(fips):
    return f"LAUCN{fips}0000000003"

def fetch_batch(series_ids, retries=3):
    payload = {
        "seriesid": series_ids,
        "startyear": str(START_YEAR),
        "endyear": str(END_YEAR),
        "registrationkey": BLS_API_KEY,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BLS_URL, data=body,
        headers={"Content-Type": "application/json"}
    )
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"BLS batch failed after {retries} retries: {last_err}")

def parse_series(series_block):
    """Return list of (period_label 'YYYY-MM', value_float) sorted ascending."""
    out = []
    for obs in series_block.get("data", []):
        if obs.get("period", "").startswith("M") and obs["period"] != "M13":
            y = obs["year"]
            m = obs["period"][1:].zfill(2)
            try:
                v = float(obs["value"])
            except (TypeError, ValueError):
                continue
            out.append((f"{y}-{m}", v))
    out.sort()
    return out

def main():
    name_by_fips = {fips: name for fips, name in GA_COUNTIES}
    series_ids = [laus_series_id(fips) for fips, _ in GA_COUNTIES]

    # Fetch in batches of 50
    rows_by_fips = {}
    for i in range(0, len(series_ids), BATCH_SIZE):
        batch = series_ids[i:i + BATCH_SIZE]
        print(f"  fetching batch {i//BATCH_SIZE + 1} ({len(batch)} series)...", flush=True)
        resp = fetch_batch(batch)
        if resp.get("status") != "REQUEST_SUCCEEDED":
            msg = resp.get("message", ["unknown"])
            raise RuntimeError(f"BLS error: {msg}")
        for s in resp["Results"]["series"]:
            sid = s["seriesID"]               # LAUCN13121....
            fips = sid[5:10]                  # 13121
            rows_by_fips[fips] = parse_series(s)
        time.sleep(0.5)

    # Determine the latest common month across counties (some series may lag others)
    months_sets = [{lbl for lbl, _ in rows} for rows in rows_by_fips.values() if rows]
    if not months_sets:
        raise RuntimeError("No data returned for any series")
    common = sorted(set.intersection(*months_sets))
    if not common:
        raise RuntimeError("No month is common across all GA counties")

    # Take the last 12 months (or fewer if not yet a year of data)
    months = common[-12:]
    print(f"  Months in output: {months[0]} → {months[-1]} ({len(months)} months)")

    frames = []
    for ym in months:
        pts = []
        for fips, name in GA_COUNTIES:
            v = next((val for lbl, val in rows_by_fips.get(fips, []) if lbl == ym), None)
            if v is None:
                continue
            pts.append({"fips": fips, "label": name, "value": round(v, 1)})
        frames.append({"date": ym, "points": pts})

    latest = frames[-1]
    sorted_latest = sorted(latest["points"], key=lambda p: p["value"])
    statewide_avg = round(sum(p["value"] for p in latest["points"]) / len(latest["points"]), 1)

    out = {
        "_fixture": False,
        "source": "BLS LAUS — Local Area Unemployment Statistics, county-level monthly",
        "fetched_at": TODAY.isoformat(),
        "latest_label": latest["date"],
        "kpis": {
            "statewide_avg_unemp": statewide_avg,
            "n_counties": len(latest["points"]),
            "lowest":  {"county": sorted_latest[0]["label"],  "fips": sorted_latest[0]["fips"],  "value": sorted_latest[0]["value"]},
            "highest": {"county": sorted_latest[-1]["label"], "fips": sorted_latest[-1]["fips"], "value": sorted_latest[-1]["value"]},
        },
        "metric": "unemployment_rate",
        "metric_label": "Unemployment rate",
        "unit": "%",
        "frames": frames,
    }

    out_path = Path(__file__).parent.parent / "data" / "counties.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {out_path}  ({len(latest['points'])} counties × {len(frames)} months)")

if __name__ == "__main__":
    main()
