"""Fetch GA agriculture data from USDA NASS Quick Stats API.

Target output: data/agriculture.json (replaces fixture).

USDA NASS Quick Stats API:
  - Endpoint:  https://quickstats.nass.usda.gov/api/api_GET/
  - Docs:      https://quickstats.nass.usda.gov/api
  - Free key:  Register at https://quickstats.nass.usda.gov/api (instant approval)
  - Limits:    50,000 records per query response; very generous

Env required:
  NASS_API_KEY  — from https://quickstats.nass.usda.gov/api

To enable:
  1. Register for a NASS API key (free, instant)
  2. Add NASS_API_KEY as a GitHub Secret on this repo
  3. Replace each fetch_* stub below with real implementation
  4. Add .github/workflows/update-agriculture.yml (annual cron — most NASS data is annual)

Implementation notes:
  - NASS uses commodity_desc + state_alpha = 'GA' + statisticcat_desc as filters
  - For state-level production: agg_level_desc = 'STATE'
  - For county-level production: agg_level_desc = 'COUNTY'
  - Year filter: year__GE = 2016 for the 10-year trend; year = latest for county heatmap
  - Returns JSON with records that include 'Value' (string with commas) and 'unit_desc'
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path

NASS_API_KEY = os.environ.get("NASS_API_KEY", "").strip()
NASS_BASE    = "https://quickstats.nass.usda.gov/api/api_GET/"


def nass_query(filters):
    """Hit NASS Quick Stats API with a dict of filter params, return list of records."""
    if not NASS_API_KEY:
        raise RuntimeError("NASS_API_KEY env var not set")
    q = {"key": NASS_API_KEY, "format": "JSON", **filters}
    url = NASS_BASE + "?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")).get("data", [])


def fetch_state_production_annual(commodity, statistic, unit_filter=None):
    """Return list of (year, value_float) for GA state-level annual production.

    commodity examples: 'BROILERS', 'PEANUTS', 'PECANS', 'COTTON'
    statistic examples: 'PRODUCTION', 'PRODUCTION, MEASURED IN LB'
    """
    raise NotImplementedError(
        f"Wire NASS state-level fetch for commodity={commodity}, statistic={statistic}"
    )


def fetch_county_production_latest(commodity, year):
    """Return list of {fips, label, value} for GA county-level production in `year`."""
    raise NotImplementedError(
        f"Wire NASS county-level fetch for commodity={commodity}, year={year}"
    )


def fetch_cash_receipts_breakdown():
    """Return list of {category, pct, color} for GA ag cash receipts breakdown.

    Implementation note: NASS has 'CASH RECEIPTS' under 'ECONOMICS' category.
    Filter by short_desc containing 'CASH RECEIPTS' and source_desc='SURVEY'.
    """
    raise NotImplementedError("Wire NASS cash receipts breakdown")


def main():
    if not NASS_API_KEY:
        print("WARN: NASS_API_KEY not set. Get a free key at https://quickstats.nass.usda.gov/api", file=sys.stderr)

    print("This script is a scaffold. Implementation plan:")
    print("  1. fetch_state_production_annual('BROILERS', 'PRODUCTION, MEASURED IN LB')   → trends.broilers_lbs_b")
    print("  2. fetch_state_production_annual('PEANUTS',  'PRODUCTION, MEASURED IN LB')   → trends.peanuts_lbs_b")
    print("  3. fetch_state_production_annual('PECANS',   'PRODUCTION, MEASURED IN LB')   → trends.pecans_lbs_m")
    print("  4. fetch_state_production_annual('COTTON',   'PRODUCTION, MEASURED IN 480 LB BALES') → trends.cotton_bales_k")
    print("  5. fetch_county_production_latest(<each commodity>, latest_year)             → county_production")
    print("  6. fetch_cash_receipts_breakdown()                                           → cash_receipts_breakdown")
    print("  7. Compute national rankings: pull NATIONAL totals + GA totals → ga_share_pct + national_rank")
    print()
    print("Currently doing nothing — fixture in data/agriculture.json is preserved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
