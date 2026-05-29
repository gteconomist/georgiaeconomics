"""Building-permit pulls (single-family + multi-family) via FRED.

Replaces the prior Census `www2.census.gov/econ/bps/Metro/ma{YY}a.txt` fetcher,
which was unreliably slow (3 MB flat files, frequent timeouts on Census-busy
days). FRED (api.stlouisfed.org) mirrors the same Census Building Permits Survey
data behind a fast, keyed JSON API.

FRED metro permit series follow a fixed naming convention:
    {GEO}BP1FH   -- New Private Housing Units Authorized: 1-Unit Structures   (= single-family)
    {GEO}BPPRIV  -- New Private Housing Units Authorized: all structure types  (= total units)
where {GEO} is a 7-char FRED area code (4 letters + 3 digits), e.g. Atlanta = ATLA013.
Multi-family is then derived as  MF = total - single-family.

The {GEO} prefix is NOT derivable from the CBSA code, so we resolve it once per
MSA via the FRED series/search API (then construct both series IDs from it).
Confirmed prefixes are cached in GEO_OVERRIDES to skip the search on later runs.

Env: FRED_API_KEY (required; FRED gates unkeyed calls).
"""

from __future__ import annotations

import os
import re
import sys
import time
import json
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict
from datetime import date

FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()
FRED_SEARCH = "https://api.stlouisfed.org/fred/series/search"
FRED_OBS = "https://api.stlouisfed.org/fred/series/observations"

# Confirmed CBSA -> FRED 7-char area prefix. Seeded with Atlanta (verified).
# The resolver fills others via search; once a run logs a resolved prefix it can
# be pasted here to make the pull deterministic and skip the search call.
GEO_OVERRIDES: Dict[str, str] = {
    "12060": "ATLA013",  # Atlanta-Sandy Springs-Alpharetta, GA
}

# A metro 1-Unit series id looks like "ATLA013BP1FH": 4 letters + 3 digits + BP1FH.
_GEO_FROM_BP1FH = re.compile(r"^([A-Z]{4}\d{3})BP1FH$")


def _fred_get(url: str, params: dict) -> Optional[dict]:
    """GET a FRED JSON endpoint. Returns parsed dict, or None on failure."""
    if not FRED_API_KEY:
        print("  [BPS/FRED] no FRED_API_KEY in env", file=sys.stderr)
        return None
    q = dict(params)
    q.update({"api_key": FRED_API_KEY, "file_type": "json"})
    full = f"{url}?{urllib.parse.urlencode(q)}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(full, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"  [BPS/FRED HTTP {e.code}] {url.rsplit('/', 1)[-1]}", file=sys.stderr)
            if e.code in (400, 404):
                return None
        except Exception as e:
            print(f"  [BPS/FRED err] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
        time.sleep(1 + attempt)
    return None


def _msa_search_term(full_name: str) -> str:
    """Reduce 'Augusta-Richmond County, GA-SC' -> 'Augusta' for FRED search."""
    return full_name.split(",")[0].split("-")[0].strip()


def _resolve_geo(cbsa: str, full_name: str) -> Optional[str]:
    """Resolve the FRED 7-char area prefix for an MSA (cached, else search)."""
    if cbsa in GEO_OVERRIDES:
        return GEO_OVERRIDES[cbsa]

    city = _msa_search_term(full_name)
    data = _fred_get(FRED_SEARCH, {
        "search_text": f"{city} 1-unit structures building permits",
        "limit": 50,
    })
    if not data:
        return None

    city_lc = city.lower()
    for s in data.get("seriess", []):
        m = _GEO_FROM_BP1FH.match(s.get("id", ""))
        if not m:
            continue
        title = s.get("title", "").lower()
        # Must be the MSA-level series for this city, not a same-named metro elsewhere.
        if city_lc in title and "(msa)" in title:
            geo = m.group(1)
            print(f"  [BPS/FRED] resolved {full_name} -> {geo} (add to GEO_OVERRIDES)",
                  file=sys.stderr)
            return geo
    print(f"  [BPS/FRED] no MSA 1-unit series found for {full_name}", file=sys.stderr)
    return None


def _annual_by_year(series_id: str, start_year: int) -> Dict[int, int]:
    """Annual values for a FRED permit series, keyed by year. Empty dict on failure.

    Requests annual aggregation (sum) so the call is correct whether the series'
    native frequency is annual or monthly.
    """
    data = _fred_get(FRED_OBS, {
        "series_id": series_id,
        "observation_start": f"{start_year}-01-01",
        "frequency": "a",
        "aggregation_method": "sum",
    })
    out: Dict[int, int] = {}
    if not data:
        return out
    for o in data.get("observations", []):
        val = o.get("value")
        if val in (None, ".", ""):
            continue
        try:
            out[int(o["date"][:4])] = int(round(float(val)))
        except (ValueError, KeyError):
            continue
    return out


def fetch_bps_permits_annual(cbsa: str, full_name: str = "", years_back: int = 6) -> Optional[dict]:
    """Annual single-family + multi-family residential permits for the MSA, via FRED.

    Returns the same dict shape as the legacy Census fetcher so the orchestrator
    and the page loader need no changes:
        single_family = 1-Unit units            (FRED {GEO}BP1FH)
        multi_family  = total units - 1-Unit     (FRED {GEO}BPPRIV - {GEO}BP1FH)

    Returns None on any failure (orchestrator's never-blank-on-failure logic then
    preserves the prior cached values).
    """
    # Look up the MSA full name if the caller didn't pass it.
    if not full_name:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from _ga_msas import GA_MSAS
            full_name = next((nm for c, _s, nm, _p in GA_MSAS if c == cbsa), "")
        except Exception:
            full_name = ""

    geo = _resolve_geo(cbsa, full_name)
    if not geo:
        return None

    sf_id = f"{geo}BP1FH"
    total_id = f"{geo}BPPRIV"
    start_year = date.today().year - years_back

    sf_by_year = _annual_by_year(sf_id, start_year)
    total_by_year = _annual_by_year(total_id, start_year)
    if not sf_by_year or not total_by_year:
        return None

    years = sorted(set(sf_by_year) & set(total_by_year))
    if not years:
        return None

    sf = [sf_by_year[y] for y in years]
    mf = [max(total_by_year[y] - sf_by_year[y], 0) for y in years]

    # permits per 1k residents, using the canonical MSA population
    pop = None
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from _ga_msas import GA_MSAS
        pop = {c: p for c, _s, _nm, p in GA_MSAS}.get(cbsa)
    except Exception:
        pop = None
    per_1k = [round((sf[i] + mf[i]) / (pop / 1000), 2) if pop else None
              for i in range(len(years))]

    return {
        "source":         f"Census Building Permits Survey via FRED ({sf_id} + {total_id})",
        "years":          years,
        "single_family":  sf,
        "multi_family":   mf,
        "permits_per_1k": per_1k,
        "latest_year":    years[-1],
        "latest_single":  sf[-1],
        "latest_multi":   mf[-1],
        "latest_per_1k":  per_1k[-1],
    }
