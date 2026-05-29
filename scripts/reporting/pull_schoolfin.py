"""School-spending pull (Quality-of-Life input) — Census F-33 district finance.

Per-pupil current spending isn't published at MSA level, so we pull the district
(LEA) individual-unit file from the Census Annual Survey of School System Finances
(F-33), keep the districts that make up the Savannah MSA, and compute an
enrollment-weighted per-pupil current expenditure:

    per_pupil = Σ(current spending) / Σ(enrollment)   across the MSA's districts

Savannah MSA (CBSA 42340) = Chatham, Effingham, Bryan counties (GA). Their public
school systems are Savannah-Chatham County, Effingham County, and Bryan County.

IMPORTANT — needs one validation run. The exact Census F-33 individual-unit file
URL and column headers vary by year and can't be reached from the dev sandbox
(www2.census.gov is firewalled here), so this fetcher detects columns flexibly and
logs what it matched. If it returns None, the QoL module simply computes without
the school-spending component (fail-soft). Check the [SCHOOLFIN] log lines after a
run and adjust CANDIDATE_URLS / column keywords if needed.

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

# District-name keywords that identify the Savannah-MSA school systems.
DISTRICT_KEYWORDS = ["chatham", "effingham", "bryan"]
STATE_TOKENS = ["GA", "GEORGIA"]

# Candidate individual-unit file URLs (comma-delimited). {yy}=2-digit, {yyyy}=4-digit.
# Census publishes these under www2.census.gov; naming has drifted over the years,
# so we try a few patterns newest-first.
CANDIDATE_URLS = [
    "https://www2.census.gov/programs-surveys/school-finances/tables/{yyyy}/secondary-education-finance/elsec{yy}.txt",
    "https://www2.census.gov/programs-surveys/school-finances/tables/{yyyy}/secondary-education-finance/elsec{yy}t.txt",
    "https://www2.census.gov/programs-surveys/school-finances/tables/{yyyy}/secondary-education-finance/elsec{yy}_sttables.txt",
]

# Column-header keyword sets for flexible detection (lower-cased contains-match).
ENROLL_KEYS = ["v33", "enroll", "membership", "fall membership", "pupils", "students"]
SPEND_KEYS = ["tcurelsc", "tcursalary", "total current spending", "current spending",
              "current expenditure", "totalexp", "z32"]
NAME_KEYS = ["name", "leanm", "district"]
STATE_KEYS = ["stname", "state", "stabbr"]


def _download(url: str, timeout: int = 45) -> Optional[str]:
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
    if not (name_col and enroll_col and spend_col):
        print(f"  [SCHOOLFIN {year}] couldn't map columns "
              f"(name={name_col} enroll={enroll_col} spend={spend_col}); headers={header[:12]}",
              file=sys.stderr)
        return None

    tot_enroll = 0.0
    tot_spend = 0.0   # in dollars (F-33 amounts are thousands → *1000)
    matched = []
    for row in reader:
        nm = (row.get(name_col) or "").lower()
        st = (row.get(state_col) or "").upper() if state_col else ""
        if state_col and not any(t in st for t in STATE_TOKENS):
            continue
        if not any(k in nm for k in DISTRICT_KEYWORDS):
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

    if tot_enroll <= 0 or not matched:
        print(f"  [SCHOOLFIN {year}] no MSA districts matched {DISTRICT_KEYWORDS}", file=sys.stderr)
        return None

    print(f"  [SCHOOLFIN {year}] matched districts: {matched}", file=sys.stderr)
    return {
        "year": year,
        "per_pupil_current": round(tot_spend / tot_enroll),
        "total_enrollment": round(tot_enroll),
        "districts": matched,
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
        for tmpl in CANDIDATE_URLS:
            url = tmpl.format(yyyy=y, yy=f"{y % 100:02d}")
            text = _download(url)
            if not text:
                continue
            out = _parse(text, y)
            if out:
                return out
    return None
