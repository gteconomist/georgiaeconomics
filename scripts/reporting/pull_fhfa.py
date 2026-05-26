"""FHFA House Price Index pulls (via FRED) for Metro Economic Profile reports.

FHFA publishes its MSA-level HPI as a quarterly series. We pull it via FRED
(api.stlouisfed.org) which mirrors the FHFA release and provides a clean JSON API.

FRED series ID for FHFA MSA Purchase-Only Quarterly HPI:
    ATNHPIUS{CBSA}Q  (e.g. Savannah = ATNHPIUS42340Q)

Note: FRED occasionally freezes a metro series (Atlanta's ATNHPIUS12060Q has been
stale for years per scripts/fetch_msa_metrics.py). For Atlanta we fall back to a
county-level aggregate. For other GA MSAs the MSA series stays current.

Env: FRED_API_KEY (required for any meaningful volume; FRED gates unkeyed calls).
"""

from __future__ import annotations

import os
import sys
import time
import json
import urllib.request
import urllib.error
import urllib.parse
from typing import Optional, List
from datetime import date

FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Atlanta CBSA is known-stale on the MSA-level series; flag for fallback handling
ATLANTA_CBSA = "12060"


def _fred_observations(series_id: str, start_date: str = "2010-01-01") -> Optional[List[dict]]:
    """Fetch a FRED series and return its observations list. None on failure."""
    if not FRED_API_KEY:
        print(f"  [FRED] no API key in env; set FRED_API_KEY", file=sys.stderr)
        return None

    params = urllib.parse.urlencode({
        "series_id": series_id,
        "file_type": "json",
        "api_key": FRED_API_KEY,
        "observation_start": start_date,
    })
    url = f"{FRED_BASE}?{params}"

    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("observations", [])
        except urllib.error.HTTPError as e:
            print(f"  [FRED HTTP {e.code}] {series_id}", file=sys.stderr)
            if e.code in (400, 404):
                return None
        except Exception as e:
            print(f"  [FRED err] {series_id} -- {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(1 + attempt)

    return None


def _yoy_pct_quarterly(values: List[Optional[float]]) -> List[Optional[float]]:
    """Year-over-year % change for a quarterly series (lag 4)."""
    out: List[Optional[float]] = [None] * len(values)
    for i, v in enumerate(values):
        if i < 4 or v is None:
            continue
        prior = values[i - 4]
        if prior is None or prior == 0:
            continue
        out[i] = round(100 * (v - prior) / prior, 2)
    return out


def fetch_hpi_quarterly_history(cbsa: str, years_back: int = 10) -> Optional[dict]:
    """Quarterly FHFA Purchase-Only HPI for an MSA, last N years.

    Returns:
        {
          "series_id":     "ATNHPIUS42340Q",
          "quarters":      ["2016-Q1", "2016-Q2", ...],
          "values":        [184.2, 187.5, ...],     # index level
          "yoy_pct":       [None, ..., 5.4, 5.6, ...],
          "latest_quarter":"2026-Q1",
          "latest_value":  234.6,
          "latest_yoy":    3.6
        }
    """
    series_id = f"ATNHPIUS{cbsa}Q"
    start_year = date.today().year - years_back
    start_date = f"{start_year}-01-01"

    obs = _fred_observations(series_id, start_date)
    if obs is None:
        return None

    quarters: List[str] = []
    values: List[Optional[float]] = []
    for o in obs:
        date_str = o.get("date")
        val_str = o.get("value")
        if not date_str or val_str in (".", "", None):
            continue
        try:
            year, month, _ = date_str.split("-")
            quarter = (int(month) - 1) // 3 + 1
            quarters.append(f"{year}-Q{quarter}")
            values.append(float(val_str))
        except (ValueError, AttributeError):
            continue

    if not values:
        # If this is Atlanta and the MSA series is frozen, the orchestrator should
        # detect a stale latest_quarter and trigger the county-aggregate fallback
        # (see _atlanta_county_avg_hpi_yoy in fetch_msa_metrics.py).
        return None

    yoy = _yoy_pct_quarterly(values)

    return {
        "series_id":      series_id,
        "quarters":       quarters,
        "values":         values,
        "yoy_pct":        yoy,
        "latest_quarter": quarters[-1],
        "latest_value":   values[-1],
        "latest_yoy":     yoy[-1],
    }


# ----------------------------- CLI smoke test -----------------------------

if __name__ == "__main__":
    cbsa = sys.argv[1] if len(sys.argv) > 1 else "42340"
    print(f"Fetching FHFA HPI for CBSA {cbsa} ...", file=sys.stderr)
    d = fetch_hpi_quarterly_history(cbsa, years_back=10)
    if d:
        print(f"  Latest {d['latest_quarter']}: index {d['latest_value']:.1f}  ({d['latest_yoy']:+.2f}% YoY)")
        print(f"  Quarters fetched: {len(d['quarters'])}")
    else:
        print("  FAILED — likely FRED_API_KEY not set or series frozen.")
