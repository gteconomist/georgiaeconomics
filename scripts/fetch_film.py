"""Fetch GA film industry data — scaffold with TODO markers.

This script is structured to call out to:
  - Tavily search+extract → Georgia Department of Economic Development (DECD) annual film reports
  - Tavily search → Georgia Film Office production database announcements
  - BLS QCEW API → motion picture and video industries (NAICS 5121) employment for GA

For now, ONLY demonstrates structure. Each fetcher is stubbed with a clear contract.

Pattern reference:
  - DECD typically publishes a "Georgia Film Industry by the Numbers" annual press release in summer
  - URL pattern: georgia.org/news/<year>/<slug>
  - Tavily can scrape these and extract: total spend ($B), production count, jobs estimate

Env required:
  TAVILY_API_KEY  — for DECD + Film Office scraping
  BLS_API_KEY     — for QCEW employment data (already configured for labor.yml)
"""
import os
import sys
import json
from pathlib import Path

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
BLS_API_KEY    = os.environ.get("BLS_API_KEY",    "").strip()


# ---------------------------------------------------------------------------
# Stubs — each replaces a section of data/film.json when fully implemented
# ---------------------------------------------------------------------------

def fetch_annual_production_spend():
    """Returns list of (year, spend_billions) for the last 12 years.

    Implementation plan:
      1. Tavily search: site:georgia.org "film industry" "spend" — last 12 years
      2. For each annual report URL, Tavily extract → parse "$X.X billion" headline
      3. Order by year, return.
    """
    raise NotImplementedError("Wire Tavily → DECD annual film industry reports here.")


def fetch_tax_credits_issued():
    """Returns list of (year, credits_issued_billions).

    Implementation plan:
      1. GA Department of Audits & Accounts publishes annual film tax credit summary
      2. Tavily extract → parse "Total credits issued: $X.XX billion"
      3. Cross-reference with DECD reports for double-check.
    """
    raise NotImplementedError("Wire Tavily → GA DOAA film tax credit reports here.")


def fetch_qcew_motion_picture_employment():
    """Returns list of (year, avg_employment_thousands) for GA NAICS 5121.

    Implementation plan:
      Use BLS QCEW API. Series ID format for state/industry employment:
        ENU13000510512100  (NSA, all employees, GA, NAICS 5121)
        Actually QCEW API uses a different shape — see BLS docs.
      Or use BLS CES MSA-level series for ATL motion picture sector.
    """
    raise NotImplementedError("Wire BLS QCEW for GA motion picture sector employment here.")


def fetch_notable_productions_recent():
    """Returns list of dicts: {title, year, studio, type, spend_m, location}.

    Implementation plan:
      1. GA Film Office maintains a public production database
      2. Tavily search: site:georgia.org/film-tv-music "filmed in georgia"
      3. Extract title, year, studio from announcement pages
      4. Spend figures often not public — may need to estimate from production size
    """
    raise NotImplementedError("Wire Tavily → GA Film Office production list here.")


def fetch_state_comparison():
    """Returns list of dicts: {state, spend_b, rank, color}.

    Implementation plan:
      Cross-state film production data is not in a single API — need per-state
      film office reports + FilmLA's Sound Stage Production Report (CA reference).
      Update annually based on industry trade press summaries.
    """
    raise NotImplementedError("Wire multi-source state film production aggregation here.")


def main():
    if not TAVILY_API_KEY:
        print("WARN: TAVILY_API_KEY not set — DECD/Film Office scrapers will fail.", file=sys.stderr)
    if not BLS_API_KEY:
        print("WARN: BLS_API_KEY not set — QCEW employment fetch will fail.", file=sys.stderr)

    print("This script is a scaffold. Replace each fetch_* function with a real impl.")
    print("\nStubs to implement, in priority order:")
    print("  1. fetch_qcew_motion_picture_employment  (BLS QCEW — easiest, structured API)")
    print("  2. fetch_annual_production_spend         (Tavily → DECD; clean URL pattern)")
    print("  3. fetch_tax_credits_issued              (Tavily → GA DOAA reports)")
    print("  4. fetch_notable_productions_recent      (Tavily → Film Office; messier)")
    print("  5. fetch_state_comparison                (Manual annual update from trade press)")
    print()
    print("Studios list (data/film.json → major_studios) is essentially static — update by hand.")
    print("Currently doing nothing — fixture in data/film.json is preserved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
