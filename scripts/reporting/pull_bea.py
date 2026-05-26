"""BEA Regional pulls for Metro Economic Profile reports.

BEA's Regional API does NOT expose MSA-level GDP or personal income as their own
tables (only state and county). For MSA aggregates we sum the constituent
counties via _ga_msas.COUNTY_TO_MSA. This is the same approach used by the
existing scripts/fetch_msa_metrics.py.

Exposes:
  fetch_gmp_history(cbsa, years_back=7) -> dict
      Annual real Gross Metro Product (county sum, CAGDP2 LineCode=1, current $).

  fetch_personal_income_history(cbsa, years_back=7) -> dict
      Annual personal income (county sum, CAINC1 LineCode=1, current $) +
      per-capita personal income.

  fetch_industry_earnings(cbsa, year=None) -> dict
      Sector-level average annual earnings for the MSA (county-aggregated CAINC5N).
      Powers the "Comparative Employment & Income" earnings columns.

Env: BEA_API_KEY (required).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path
from typing import Optional, Dict, List

BEA_API_KEY = os.environ.get("BEA_API_KEY", "").strip()
BEA_BASE = "https://apps.bea.gov/api/data/"

# Import the county -> CBSA map from the canonical MSA file
sys.path.insert(0, str(Path(__file__).parent.parent))
from _ga_msas import GA_MSAS, COUNTY_TO_MSA  # noqa: E402

# Build inverse: cbsa -> [county_fips, ...]
CBSA_TO_COUNTIES: Dict[str, List[str]] = {}
for fips, cbsa in COUNTY_TO_MSA.items():
    CBSA_TO_COUNTIES.setdefault(cbsa, []).append(fips)

POP_BY_CBSA: Dict[str, int] = {cbsa: pop for cbsa, _, _, pop in GA_MSAS}


def _bea_get(params: Dict[str, str], retries: int = 3) -> Optional[dict]:
    """GET request to BEA API and parse JSON. Returns None on failure."""
    if not BEA_API_KEY:
        print("  [BEA] no API key in env", file=sys.stderr)
        return None
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BEA_BASE}?UserID={BEA_API_KEY}&{qs}&ResultFormat=JSON"

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            results = (data.get("BEAAPI") or {}).get("Results") or {}
            if isinstance(results, list):
                results = results[0] if results else {}
            if results.get("Error"):
                err = results["Error"]
                if isinstance(err, list):
                    err = err[0] if err else {}
                if isinstance(err, dict):
                    desc = (err.get("APIErrorDescription") or err.get("ErrorDescription") or "").strip()
                else:
                    desc = str(err).strip()
                # "Year not published yet" or similar — quiet: caller will try an earlier year.
                # Also silence empty Error objects (BEA's "Unknown error" cosmetic noise) and
                # the explicit string "unknown error".
                if (not desc or
                    "not available" in desc.lower() or
                    "no data" in desc.lower() or
                    "invalid year" in desc.lower() or
                    "unknown error" in desc.lower()):
                    return None
                print(f"  [BEA API error] {desc}", file=sys.stderr)
                return None
            return results
        except urllib.error.HTTPError as e:
            print(f"  [BEA HTTP {e.code}] attempt {attempt+1}", file=sys.stderr)
        except Exception as e:
            print(f"  [BEA err] {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(2 + attempt)
    return None


def _aggregate_county_to_msa(rows: List[dict], cbsa: str, value_field: str = "DataValue") -> Optional[float]:
    """Sum CAGDP2 / CAINC1 county rows up to one MSA. Returns float in source units or None."""
    counties = set(CBSA_TO_COUNTIES.get(cbsa, []))
    if not counties:
        return None
    total = 0.0
    matched = 0
    for row in rows:
        fips = (row.get("GeoFips") or "").strip()
        if fips not in counties:
            continue
        raw = (row.get(value_field) or "").replace(",", "")
        try:
            total += float(raw)
            matched += 1
        except ValueError:
            # BEA suppresses small-county values as "(D)"; skip rather than fail.
            continue
    return total if matched > 0 else None


# ----------------------------- GMP history -----------------------------

def fetch_gmp_history(cbsa: str, years_back: int = 7) -> Optional[dict]:
    """Annual current-$ Gross Metro Product via county-aggregated CAGDP2.

    Returns:
        {"years": [2018, ..., 2024], "gmp_billions_usd": [...],
         "yoy_pct": [None, ...], "gdp_per_capita": [...],
         "latest_year": 2024, "latest_gmp_billions_usd": 32.1, "latest_gdp_per_capita": 78294}
    """
    this_year = date.today().year
    start_year = this_year - years_back

    years: List[int] = []
    gmp_thou: List[float] = []

    for y in range(start_year, this_year):
        results = _bea_get({
            "method": "GetData",
            "DataSetName": "Regional",
            "TableName": "CAGDP2",
            "LineCode": "1",       # all-industry total
            "GeoFips": "COUNTY",
            "Year": str(y),
        })
        if not results:
            continue
        rows = results.get("Data") or []
        gmp_y = _aggregate_county_to_msa(rows, cbsa)
        if gmp_y is None:
            continue
        years.append(y)
        gmp_thou.append(gmp_y)

    if not gmp_thou:
        return None

    pop = POP_BY_CBSA.get(cbsa, 0)
    gmp_billions = [round(v * 1000 / 1e9, 2) for v in gmp_thou]
    gdp_per_capita = [int(round(v * 1000 / pop)) if pop else None for v in gmp_thou]

    yoy: List[Optional[float]] = [None]
    for i in range(1, len(gmp_thou)):
        prior = gmp_thou[i - 1]
        yoy.append(round(100 * (gmp_thou[i] - prior) / prior, 2) if prior else None)

    return {
        "source":                 "BEA CAGDP2 (county sum, current $)",
        "years":                  years,
        "gmp_billions_usd":       gmp_billions,
        "yoy_pct":                yoy,
        "gdp_per_capita":         gdp_per_capita,
        "latest_year":            years[-1],
        "latest_gmp_billions_usd": gmp_billions[-1],
        "latest_yoy":             yoy[-1],
        "latest_gdp_per_capita":  gdp_per_capita[-1],
    }


# ----------------------------- Personal income history -----------------------------

def fetch_personal_income_history(cbsa: str, years_back: int = 7) -> Optional[dict]:
    """Annual personal income (current $) and per-capita personal income via CAINC1.

    CAINC1 LineCode 1 = Personal income (thousands of $)
    CAINC1 LineCode 3 = Per capita personal income ($)
    """
    this_year = date.today().year
    start_year = this_year - years_back

    years: List[int] = []
    pi_thou: List[float] = []

    for y in range(start_year, this_year):
        results = _bea_get({
            "method": "GetData",
            "DataSetName": "Regional",
            "TableName": "CAINC1",
            "LineCode": "1",       # Personal income, thousands of dollars
            "GeoFips": "COUNTY",
            "Year": str(y),
        })
        if not results:
            continue
        rows = results.get("Data") or []
        pi_y = _aggregate_county_to_msa(rows, cbsa)
        if pi_y is None:
            continue
        years.append(y)
        pi_thou.append(pi_y)

    if not pi_thou:
        return None

    pop = POP_BY_CBSA.get(cbsa, 0)
    pi_billions = [round(v * 1000 / 1e9, 2) for v in pi_thou]
    per_capita = [int(round(v * 1000 / pop)) if pop else None for v in pi_thou]

    yoy: List[Optional[float]] = [None]
    for i in range(1, len(pi_thou)):
        prior = pi_thou[i - 1]
        yoy.append(round(100 * (pi_thou[i] - prior) / prior, 2) if prior else None)

    return {
        "source":                  "BEA CAINC1 (county sum, current $)",
        "years":                   years,
        "personal_income_billions_usd": pi_billions,
        "yoy_pct":                 yoy,
        "per_capita_income":       per_capita,
        "latest_year":             years[-1],
        "latest_personal_income_billions_usd": pi_billions[-1],
        "latest_yoy":              yoy[-1],
        "latest_per_capita_income": per_capita[-1],
    }


# ----------------------------- Industry earnings -----------------------------

# CAINC5N (Personal income by major industry) line codes used by the report.
# Source: BEA CAINC5N table key.
CAINC5N_SECTORS: Dict[str, str] = {
    "70":  "Construction",
    "100": "Manufacturing",
    "200": "Wholesale trade",
    "400": "Retail trade",
    "500": "Transportation and warehousing",
    "600": "Information",
    "700": "Finance and insurance",
    "900": "Professional, scientific, and technical services",
    "1100": "Educational services",
    "1200": "Health care and social assistance",
    "1300": "Arts, entertainment, and recreation",
    "1400": "Accommodation and food services",
    "1500": "Other services (except government)",
    "1600": "Government and government enterprises",
}


def fetch_industry_earnings(cbsa: str, year: Optional[int] = None) -> Optional[dict]:
    """Average earnings per worker by sector (county-aggregated CAINC5N).

    Returns: {"year": Y, "sectors": {<label>: total_earnings_thou_usd}, ...}

    NOTE: This is a placeholder skeleton — earnings PER WORKER requires the
    matching employment count by sector, which CAINC5N doesn't carry. Phase 1
    follow-up: combine BEA CAINC5N (earnings) with BLS QCEW (employment by
    sector) to compute average earnings.
    """
    print(f"  [BEA earnings] sectoral earnings/worker requires CAINC5N + QCEW join; deferred to Phase 1 follow-up.", file=sys.stderr)
    return None


# ----------------------------- CLI smoke test -----------------------------

if __name__ == "__main__":
    cbsa = sys.argv[1] if len(sys.argv) > 1 else "42340"
    print(f"Fetching BEA data for CBSA {cbsa} ...", file=sys.stderr)

    gmp = fetch_gmp_history(cbsa)
    if gmp:
        print(f"  GMP latest {gmp['latest_year']}: ${gmp['latest_gmp_billions_usd']}B  ({gmp['latest_yoy']:+.2f}% YoY)  GDP/cap: ${gmp['latest_gdp_per_capita']:,}")
        print(f"  Years: {gmp['years']}")

    pi = fetch_personal_income_history(cbsa)
    if pi:
        print(f"  Personal income latest {pi['latest_year']}: ${pi['latest_personal_income_billions_usd']}B  ({pi['latest_yoy']:+.2f}% YoY)  Per cap: ${pi['latest_per_capita_income']:,}")
