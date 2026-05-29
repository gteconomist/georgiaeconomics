"""EPA air-quality pull for the Metro Economic Profile (Quality-of-Life input).

Source: EPA AirData pre-computed "Annual AQI by CBSA" files — keyless static ZIPs:
    https://aqs.epa.gov/aqsweb/airdata/annual_aqi_by_cbsa_{YEAR}.zip
Each ZIP holds one CSV, one row per CBSA per year, so Savannah (CBSA 42340) is
available directly with NO county aggregation. Column schema (stable):
    CBSA, CBSA Code, Year, Days with AQI, Good Days, Moderate Days,
    Unhealthy for Sensitive Groups Days, Unhealthy Days, Very Unhealthy Days,
    Hazardous Days, Max AQI, 90th Percentile AQI, Median AQI, Days CO, ... PM10

Pulls the most recent year that resolves (AQI for year Y is published partway
through Y+1, so we probe newest-first and fall back). Returns None on any
failure — the orchestrator's never-blank-on-failure logic then preserves prior.

No API key required. Pure stdlib.
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
import urllib.request
import urllib.error
from datetime import date
from typing import Optional

AIRDATA_BASE = "https://aqs.epa.gov/aqsweb/airdata"


def _fetch_cbsa_aqi_year(year: int, cbsa: str, timeout: int = 45) -> Optional[dict]:
    """Fetch one year's CBSA AQI ZIP and extract this CBSA's row. None on failure."""
    url = f"{AIRDATA_BASE}/annual_aqi_by_cbsa_{year}.zip"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EIG-MSA-reports/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            blob = resp.read()
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  [EPA {year}] HTTP {e.code}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [EPA {year}] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
        return None

    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        name = zf.namelist()[0]
        text = zf.read(name).decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [EPA {year}] unzip failed: {type(e).__name__}", file=sys.stderr)
        return None

    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if (row.get("CBSA Code") or "").strip() == cbsa:
            def num(col, cast=float):
                v = (row.get(col) or "").strip()
                try:
                    return cast(v)
                except (ValueError, TypeError):
                    return None
            days = num("Days with AQI", int)
            good = num("Good Days", int)
            unhealthy = sum((num(c, int) or 0) for c in [
                "Unhealthy for Sensitive Groups Days", "Unhealthy Days",
                "Very Unhealthy Days", "Hazardous Days",
            ])
            return {
                "year": year,
                "median_aqi": num("Median AQI"),
                "max_aqi": num("Max AQI"),
                "p90_aqi": num("90th Percentile AQI"),
                "days_with_aqi": days,
                "good_days": good,
                "pct_good_days": round(100 * good / days, 1) if (days and good is not None) else None,
                "unhealthy_or_worse_days": unhealthy,
            }
    return None


def fetch_cbsa_air_quality(cbsa: str, max_lookback: int = 3) -> Optional[dict]:
    """Most recent annual AQI summary for the CBSA. Probes newest year backward."""
    this_year = date.today().year
    for y in range(this_year, this_year - max_lookback - 1, -1):
        data = _fetch_cbsa_aqi_year(y, cbsa)
        if data and data.get("median_aqi") is not None:
            data["source"] = (
                f"EPA AirData annual AQI by CBSA, {y} "
                f"({AIRDATA_BASE}/annual_aqi_by_cbsa_{y}.zip)"
            )
            return data
    return None
