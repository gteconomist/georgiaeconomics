"""School-spending pull (Quality-of-Life input) — Census F-33 district finance.

Per-pupil current spending isn't published at MSA level, so we pull the district
(LEA) individual-unit file from the Census Annual Survey of School System Finances
(F-33), keep the districts that make up the Savannah MSA, and compute an
enrollment-weighted per-pupil current expenditure:

    per_pupil = Σ(current spending) / Σ(enrollment)   across the MSA's districts

Savannah MSA (CBSA 42340) = Chatham, Effingham, Bryan counties (GA). Their public
school systems are Savannah-Chatham County, Effingham County, and Bryan County.

VALIDATION HISTORY. First Actions run (2026-05-29): URL + column mapping worked,
but every Georgia row was dropped because the F-33 STATE field is the Census
Governments state code ("11"), not "GA" — so the state filter excluded them before
name-matching. Fixed: see GA_STATE_CODES / _is_georgia. Matching now uses two
strategies — exact county-FIPS (preferred, unambiguous) and district-name keyword
disambiguated by Georgia state — and on a miss logs the name-keyword hits and the
distinct STATE values seen, so any remaining mismatch is conclusive from one log.
If it returns None, the QoL module simply computes without the school-spending
component (fail-soft).

No API key required. Pure stdlib.
"""

from __future__ import annotations

import csv
import io
import sys
import urllib.request
import urllib.error
from datetime import date
from typing import Optional, List

# District-name keywords that identify the Savannah-MSA school systems
# (Savannah-Chatham County, Effingham County, Bryan County).
DISTRICT_KEYWORDS = ["chatham", "effingham", "bryan"]

# Georgia identifiers across the encodings the F-33 file may use for STATE.
# CRITICAL: the F-33 individual-unit file's STATE column is the *Census Governments*
# state code (Georgia = "11"), NOT the USPS abbreviation and NOT the FIPS code
# ("13"). The first validation run failed precisely here — "GA" in "11" is False,
# so every Georgia row was dropped before name-matching. We now accept all three
# forms, matching EXACTLY for the numeric codes so "11"/"13" can't collide.
GA_STATE_CODES = {"GA", "GEORGIA", "11", "13"}

# County FIPS for the three Savannah-MSA counties — used as a bullet-proof exact
# match when the file exposes a county-code column (unambiguous vs. name spelling).
SAVANNAH_COUNTY_FIPS = {"13051", "13103", "13029"}  # Chatham, Effingham, Bryan


def _is_georgia(st: str) -> bool:
    s = (st or "").strip().upper()
    return s in GA_STATE_CODES or "GEORGIA" in s


# Candidate individual-unit (district-level) file URLs, comma-delimited.
# {yy}=2-digit, {yyyy}=4-digit. We deliberately omit the "_sttables" (state-
# aggregate) file — it has no district rows and only wastes a large download.
CANDIDATE_URLS = [
    "https://www2.census.gov/programs-surveys/school-finances/tables/{yyyy}/secondary-education-finance/elsec{yy}.txt",
    "https://www2.census.gov/programs-surveys/school-finances/tables/{yyyy}/secondary-education-finance/elsec{yy}t.txt",
]

# Column-header keyword sets for flexible detection (lower-cased contains-match).
ENROLL_KEYS = ["v33", "enroll", "membership", "fall membership", "pupils", "students"]
SPEND_KEYS = ["tcurelsc", "tcursalary", "total current spending", "current spending",
              "current expenditure", "totalexp", "z32"]
NAME_KEYS = ["name", "leanm", "district"]
STATE_KEYS = ["stname", "state", "stabbr"]
# County-code column (5-digit state+county FIPS), if the file carries one.
COUNTY_KEYS = ["conum", "cnty_fips", "county_fips", "fipscounty", "geo_id", "county"]


def _download(url: str, timeout: int = 40) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EIG-MSA-reports/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  [SCHOOLFIN] HTTP {e.code} {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [SCHOOLFIN] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
        return None


def _find_col(header: List[str], keys: List[str]) -> Optional[str]:
    low = {h: h.lower() for h in header}
    # exact-ish first, then contains
    for h, hl in low.items():
        if hl in keys:
            return h
    for h, hl in low.items():
        if any(k in hl for k in keys):
            return h
    return None


def _parse(text: str, year: int) -> Optional[dict]:
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    if not header:
        return None
    name_col = _find_col(header, NAME_KEYS)
    state_col = _find_col(header, STATE_KEYS)
    enroll_col = _find_col(header, ENROLL_KEYS)
    spend_col = _find_col(header, SPEND_KEYS)
    county_col = _find_col(header, COUNTY_KEYS)
    if not (name_col and enroll_col and spend_col):
        print(f"  [SCHOOLFIN {year}] couldn't map columns "
              f"(name={name_col} enroll={enroll_col} spend={spend_col}); headers={header[:12]}",
              file=sys.stderr)
        return None

    tot_enroll = 0.0
    tot_spend = 0.0   # in dollars (F-33 amounts are thousands → *1000)
    matched = []
    match_mode = None
    # Diagnostics collected on a miss so the next run's log is conclusive.
    name_hits = []          # rows whose NAME matched our keywords, ANY state
    ga_states_seen = set()  # distinct STATE values among name-keyword hits

    for row in reader:
        nm = (row.get(name_col) or "").lower()
        st = (row.get(state_col) or "") if state_col else ""
        county = (row.get(county_col) or "").strip() if county_col else ""

        # Strategy 1 (preferred, unambiguous): exact county-FIPS match.
        is_county_match = county_col and county.zfill(5) in SAVANNAH_COUNTY_FIPS

        # Strategy 2: district-name keyword, disambiguated by Georgia state.
        is_name_match = any(k in nm for k in DISTRICT_KEYWORDS)
        if is_name_match:
            name_hits.append(f"{row.get(name_col)} (state={st!r})")
            ga_states_seen.add(st.strip())
        is_name_match = is_name_match and (not state_col or _is_georgia(st))

        if not (is_county_match or is_name_match):
            continue
        try:
            enr = float(row.get(enroll_col) or 0)
            spd = float(row.get(spend_col) or 0) * 1000.0
        except (ValueError, TypeError):
            continue
        if enr <= 0:
            continue
        tot_enroll += enr
        tot_spend += spd
        matched.append(row.get(name_col))
        match_mode = "county-fips" if is_county_match else "name+state"

    if tot_enroll <= 0 or not matched:
        # Conclusive diagnostics: did ANY row's name match (so it's a state/county
        # encoding problem) or none at all (a name-spelling / wrong-column problem)?
        print(f"  [SCHOOLFIN {year}] no MSA districts matched. "
              f"name_col={name_col!r} state_col={state_col!r} county_col={county_col!r}; "
              f"name-keyword hits (any state)={name_hits[:8]}; "
              f"distinct states among hits={sorted(ga_states_seen)[:12]}", file=sys.stderr)
        return None

    print(f"  [SCHOOLFIN {year}] matched ({match_mode}): {matched}", file=sys.stderr)
    return {
        "year": year,
        "per_pupil_current": round(tot_spend / tot_enroll),
        "total_enrollment": round(tot_enroll),
        "districts": matched,
        "match_mode": match_mode,
        "source": f"Census Annual Survey of School System Finances (F-33), FY{year}",
    }


def fetch_school_finance(cbsa: str, max_lookback: int = 3) -> Optional[dict]:
    """Enrollment-weighted per-pupil current spending for the MSA's districts.

    Only Savannah (42340) is wired with a district keyword list today; other MSAs
    return None until their districts are added to DISTRICT_KEYWORDS handling.
    """
    if cbsa != "42340":
        return None  # district keyword map only covers Savannah for now
    this_year = date.today().year
    for y in range(this_year - 1, this_year - max_lookback - 2, -1):
        text = None
        for tmpl in CANDIDATE_URLS:
            url = tmpl.format(yyyy=y, yy=f"{y % 100:02d}")
            text = _download(url)
            if text:
                break  # got year y's national file — don't re-download other variants
        if not text:
            continue   # nothing published for year y; try an older year
        out = _parse(text, y)
        if out:
            return out
        # File parsed but no district matched (diagnostics already logged) — older year
    return None
