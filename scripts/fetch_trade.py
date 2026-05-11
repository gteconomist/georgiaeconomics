"""Fetch GA trade & logistics data — scaffold with TODO markers.

This script is structured to call out to:
  - Tavily search+extract → Georgia Ports Authority monthly TEU & autos releases
  - Tavily → Hartsfield-Jackson airport monthly cargo stats
  - Census API (USA Trade Online) → state exports by destination

For now, it ONLY demonstrates the structure. Each fetcher function is stubbed
with a clear contract and a NotImplementedError. Run it locally when you're
ready to swap each stub for the real implementation.

Pattern reference: see economicsguru/scripts/fetch_industry_surveys.py for the
proven Tavily-on-PR-Newswire pattern. GA Ports Authority and Hartsfield publish
to their own websites (not PR Newswire), so the search/extract URLs differ but
the parsing pattern is identical.

Env required:
  TAVILY_API_KEY  — for GPA + ATL airport scraping
  CENSUS_API_KEY  — for USA Trade Online state exports
"""
import os
import sys
import json
from pathlib import Path

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY", "").strip()

# ---------------------------------------------------------------------------
# Stubs — each replaces a section of data/trade.json when fully implemented
# ---------------------------------------------------------------------------

def fetch_savannah_teu_monthly():
    """Returns list of [YYYY-MM, teu_thousands] for the last 60+ months.

    Implementation plan:
      1. Tavily search: site:gaports.com "TEU" "monthly"
      2. Tavily extract on each result URL
      3. Regex out the "###,### TEU" headline figure and the month/year
      4. De-dupe, sort, return.

    For backfill of older months, GPA's annual reports list monthly tables.
    """
    raise NotImplementedError("Wire Tavily → GPA monthly press releases here.")


def fetch_brunswick_autos_monthly():
    """Returns list of [YYYY-MM, vehicles_thousands].

    Implementation plan:
      1. Tavily search: site:gaports.com "Brunswick" "vehicles"
      2. Same extraction pattern as Savannah.
    """
    raise NotImplementedError("Wire Tavily → GPA Brunswick auto throughput here.")


def fetch_atl_cargo_monthly():
    """Returns list of [YYYY-MM, metric_tons_thousands].

    Implementation plan:
      1. ATL airport publishes monthly stats at atl.com/business/airport-statistics/
      2. Tavily extract on the stats page (or PDF report)
      3. Regex out "Cargo (metric tons)" row.
    """
    raise NotImplementedError("Wire Tavily → ATL airport stats here.")


def fetch_atl_tw_employment_monthly():
    """Returns list of [YYYY-MM, employment_thousands] for ATL MSA T&W sector.

    Implementation plan:
      Use BLS CES API. Series ID format:
        SMU13120606060000001  (NSA, T&W, Atlanta MSA, all employees)
      Reuse the BLS fetch helper from scripts/fetch_bls_laus.py.
    """
    raise NotImplementedError("Wire BLS CES API for ATL MSA NAICS 48-49 here.")


def fetch_ga_exports_by_country():
    """Returns list of dicts: {rank, country, value_musd, yoy_pct, iso}.

    Implementation plan:
      1. Census USA Trade Online — state exports by NAICS by destination.
      2. API endpoint: api.census.gov/data/timeseries/intltrade/exports/statehs
      3. Aggregate across all NAICS to get total state exports per country.
      4. Compute YoY change vs prior year, rank top 10.
    """
    raise NotImplementedError("Wire Census USA Trade Online here.")


# ---------------------------------------------------------------------------
# Glue — when stubs are filled in, this assembles the JSON
# ---------------------------------------------------------------------------

def main():
    if not TAVILY_API_KEY:
        print("WARN: TAVILY_API_KEY not set — port scrapers will fail.", file=sys.stderr)
    if not CENSUS_API_KEY:
        print("WARN: CENSUS_API_KEY not set — exports fetch will fail.", file=sys.stderr)

    print("This script is a scaffold. Replace each fetch_* function with a real impl.")
    print("Once implemented, the assembly block below will write data/trade.json.")
    print("\nStubs to implement, in priority order:")
    print("  1. fetch_savannah_teu_monthly      (GPA via Tavily)")
    print("  2. fetch_brunswick_autos_monthly   (GPA via Tavily)")
    print("  3. fetch_ga_exports_by_country     (Census API, no scrape)")
    print("  4. fetch_atl_cargo_monthly         (ATL stats via Tavily)")
    print("  5. fetch_atl_tw_employment_monthly (BLS CES API)")
    print("\nKeeping current data/trade.json (fixture) unchanged.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
