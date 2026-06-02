"""IRS SOI county-to-county migration pulls for Metro Economic Profile reports.

IRS publishes annual county-to-county migration data based on filed tax returns
(approximately a year-and-a-half lag). Inflow file lists every flow INTO a county
(prior county -> current county). Outflow file lists every flow OUT of a county.

Source: https://www.irs.gov/statistics/soi-tax-stats-migration-data

Modern (post-2011) hosting layout: direct CSVs, NOT ZIPs. Pattern verified
2026-05 against irs.gov:
    https://www.irs.gov/pub/irs-soi/countyinflow{YY}{YZ}.csv
    https://www.irs.gov/pub/irs-soi/countyoutflow{YY}{YZ}.csv
where YY/YZ are 2-digit "from year" / "to year" (e.g. 2122 = 2021->2022).

The pre-2011 ZIP layout (e.g. 22_to_23_county_data.zip) was a guess that turned
out to be wrong. The legacy `countyinflow1011.dat` files exist for tax years
2004-2011 only; modern files use the CSV pattern above.

We aggregate the county-level flows up to MSAs using _ga_msas.COUNTY_TO_MSA.

Exposes:
  fetch_migration_flows(cbsa, year=None) -> dict

Returns:
    {
      "year": "2021→2022",
      "total_in":  18500,
      "total_out": 12800,
      "net":       5700,
      "top_in":  [{"origin_msa": "Hinesville GA", "n_returns": 2840}, ...],
      "top_out": [{"dest_msa":   "Hinesville GA", "n_returns": 2340}, ...]
    }

No API key needed — public download.
"""

from __future__ import annotations

import csv
import io
import os
import sys
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path
from typing import Optional, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ga_msas import GA_MSAS, COUNTY_TO_MSA  # noqa: E402

# Build inverse + label maps
CBSA_TO_SHORT: Dict[str, str] = {cbsa: short for cbsa, short, _, _ in GA_MSAS}

# State FIPS -> name. IRS migration files identify the far side only by FIPS (no usable
# state-name column), so we translate deterministically here rather than trusting a header.
STATE_FIPS_TO_NAME: Dict[int, str] = {
    1: "Alabama", 2: "Alaska", 4: "Arizona", 5: "Arkansas", 6: "California",
    8: "Colorado", 9: "Connecticut", 10: "Delaware", 11: "District of Columbia",
    12: "Florida", 13: "Georgia", 15: "Hawaii", 16: "Idaho", 17: "Illinois",
    18: "Indiana", 19: "Iowa", 20: "Kansas", 21: "Kentucky", 22: "Louisiana",
    23: "Maine", 24: "Maryland", 25: "Massachusetts", 26: "Michigan", 27: "Minnesota",
    28: "Mississippi", 29: "Missouri", 30: "Montana", 31: "Nebraska", 32: "Nevada",
    33: "New Hampshire", 34: "New Jersey", 35: "New Mexico", 36: "New York",
    37: "North Carolina", 38: "North Dakota", 39: "Ohio", 40: "Oklahoma", 41: "Oregon",
    42: "Pennsylvania", 44: "Rhode Island", 45: "South Carolina", 46: "South Dakota",
    47: "Tennessee", 48: "Texas", 49: "Utah", 50: "Vermont", 51: "Virginia",
    53: "Washington", 54: "West Virginia", 55: "Wisconsin", 56: "Wyoming",
    72: "Puerto Rico",
}

# Cache the CSV bodies between calls within a single orchestrator run
_CSV_CACHE: Dict[str, bytes] = {}


def _fetch_csv(url: str, timeout: int = 60) -> Optional[bytes]:
    """Fetch one CSV. Caches by URL. Returns None silently on 404 / network error."""
    if url in _CSV_CACHE:
        return _CSV_CACHE[url]
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; EIG-MSA-reports/1.0)",
            "Accept": "text/csv,*/*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        if not body or len(body) < 1000:
            # IRS sometimes serves an HTML "not found" page with 200; sanity-check size.
            return None
        _CSV_CACHE[url] = body
        return body
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  [IRS SOI] HTTP {e.code} for {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [IRS SOI] {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
        return None


def _build_urls(yy: int, yz: int) -> Tuple[str, str]:
    """Return the (inflow_url, outflow_url) pair for tax-year pair (yy -> yz)."""
    yyt = f"{yy % 100:02d}"
    yzt = f"{yz % 100:02d}"
    base = "https://www.irs.gov/pub/irs-soi"
    return (
        f"{base}/countyinflow{yyt}{yzt}.csv",
        f"{base}/countyoutflow{yyt}{yzt}.csv",
    )


def _discover_latest_year() -> Optional[Tuple[int, int]]:
    """Find the most recent (from_year, to_year) IRS SOI migration CSV pair that exists.

    Probes newest first. Returns the first year-pair where the INFLOW file
    downloads cleanly — the matching outflow is assumed present (IRS publishes
    them together).
    """
    today_y = date.today().year
    # IRS publishes data ~18-24 months after the tax year ends.
    # In 2026 the latest expected pair is 2023->2024 or 2022->2023.
    for yz in range(today_y - 1, today_y - 7, -1):
        yy = yz - 1
        inflow_url, _ = _build_urls(yy, yz)
        body = _fetch_csv(inflow_url)
        if body is not None:
            return (yy, yz)
    return None


def _parse_migration_csv(csv_bytes: bytes, direction: str) -> List[dict]:
    """Parse a single IRS SOI county-migration CSV.

    Modern column layout (post-2011):
        y1_statefips, y1_countyfips, y2_statefips, y2_countyfips,
        y1_state, y2_state, y1_state_name, y2_state_name,
        n1 (number of returns), n2 (number of individuals), AGI

    For INFLOWS file: y2_* is the destination, y1_* is the origin.
    For OUTFLOWS file: y1_* is the origin, y2_* is the destination.
    """
    text = csv_bytes.decode("utf-8", errors="replace")
    out: List[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            y1_state = int(row.get("y1_statefips") or 0)
            y1_county = int(row.get("y1_countyfips") or 0)
            y2_state = int(row.get("y2_statefips") or 0)
            y2_county = int(row.get("y2_countyfips") or 0)
        except (ValueError, TypeError):
            continue

        # IRS SOI files mix real county-to-county flows with AGGREGATE summary rows that
        # are NOT places: state FIPS 96/97/98 = "Total Migration US&Foreign / US / Foreign"
        # and 57/58/59 = region/same-state/different-state totals (also non-migrant rows).
        # The "other" side (origin for inflows, destination for outflows) must be a REAL
        # state (1-56) with a REAL county (!= 0), or the totals blow up and the top list
        # fills with pseudo-rows like "Other (97)".
        if direction == "in":
            if not (1 <= y2_state <= 56 and y2_county != 0):   # our target county side
                continue
            o_state, o_county = y1_state, y1_county            # origin = "other" side
            o_name = (row.get("y1_state_name") or "").strip()
        else:
            if not (1 <= y1_state <= 56 and y1_county != 0):
                continue
            o_state, o_county = y2_state, y2_county            # destination = "other" side
            o_name = (row.get("y2_state_name") or "").strip()

        is_aggregate = not (1 <= o_state <= 56 and o_county != 0)

        try:
            n_returns = int(float(row.get("n1") or 0))
        except (ValueError, TypeError):
            n_returns = 0

        out.append({
            "from_fips": f"{y1_state:02d}{y1_county:03d}",
            "to_fips":   f"{y2_state:02d}{y2_county:03d}",
            "other_state": o_state,
            "other_name": o_name,
            "is_aggregate": is_aggregate,
            "n_returns": n_returns,
            "direction": direction,
        })
    return out


def fetch_migration_flows(cbsa: str, year: Optional[int] = None) -> Optional[dict]:
    """Aggregate IRS SOI county-to-county flows up to the MSA level."""
    pair = _discover_latest_year() if year is None else None
    if pair is None:
        return None
    yy, yz = pair
    inflow_url, outflow_url = _build_urls(yy, yz)

    inflow_bytes = _fetch_csv(inflow_url)
    outflow_bytes = _fetch_csv(outflow_url)
    if not inflow_bytes or not outflow_bytes:
        print(f"  [IRS SOI] one of inflow/outflow missing for {yy}-{yz}", file=sys.stderr)
        return None

    inflows = _parse_migration_csv(inflow_bytes, "in")
    outflows = _parse_migration_csv(outflow_bytes, "out")
    if not inflows or not outflows:
        return None

    # Counties that belong to our target MSA
    target_counties = {fips for fips, c in COUNTY_TO_MSA.items() if c == cbsa}
    if not target_counties:
        return None

    def place_label(other_cbsa: Optional[str], other_state: int) -> str:
        """Label a flow's far side: tracked MSA name if the county is in one, else the
        state name (rolling up that state's non-metro / out-of-MSA counties). Never emits
        the IRS aggregate pseudo-codes (filtered upstream via is_aggregate)."""
        short = CBSA_TO_SHORT.get(other_cbsa)
        if short:
            return short
        name = STATE_FIPS_TO_NAME.get(other_state)
        return f"{name} (other)" if name else f"FIPS {other_state:02d}"

    # Inflow rows: keep where TO (y2) is in target MSA. Aggregate by source MSA / state.
    in_by_origin: Dict[str, int] = {}
    total_in = 0
    for r in inflows:
        if r["to_fips"] not in target_counties or r["is_aggregate"]:
            continue
        origin_cbsa = COUNTY_TO_MSA.get(r["from_fips"])
        if origin_cbsa == cbsa:
            continue  # within-MSA churn
        label = place_label(origin_cbsa, r["other_state"])
        in_by_origin[label] = in_by_origin.get(label, 0) + r["n_returns"]
        total_in += r["n_returns"]

    out_by_dest: Dict[str, int] = {}
    total_out = 0
    for r in outflows:
        if r["from_fips"] not in target_counties or r["is_aggregate"]:
            continue
        dest_cbsa = COUNTY_TO_MSA.get(r["to_fips"])
        if dest_cbsa == cbsa:
            continue
        label = place_label(dest_cbsa, r["other_state"])
        out_by_dest[label] = out_by_dest.get(label, 0) + r["n_returns"]
        total_out += r["n_returns"]

    top_in = sorted(
        ({"origin_msa": k, "n_returns": v} for k, v in in_by_origin.items()),
        key=lambda x: -x["n_returns"],
    )[:10]
    top_out = sorted(
        ({"dest_msa": k, "n_returns": v} for k, v in out_by_dest.items()),
        key=lambda x: -x["n_returns"],
    )[:10]

    return {
        "year_pair_label": f"{yy}→{yz}",
        "from_year":       yy,
        "to_year":         yz,
        "total_in":        total_in,
        "total_out":       total_out,
        "net":             total_in - total_out,
        "top_in":          top_in,
        "top_out":         top_out,
        "source":          f"IRS SOI county-to-county migration ({inflow_url})",
    }


def fetch_state_flows(year: Optional[int] = None) -> Optional[dict]:
    """Georgia statewide IRS SOI migration: inflows by ORIGIN state, outflows by
    DESTINATION state, and net by state. Reuses the same download + parse helpers
    as fetch_migration_flows; aggregates over the whole state (FIPS 13) and drops
    intrastate (GA->GA) flows so this is purely Georgia's exchange with OTHER states.

    Returns:
        {"year_pair_label": "2022→2023", "from_year": 2022, "to_year": 2023,
         "total_in": ..., "total_out": ..., "net": ...,
         "top_in":  [{"state": "Florida", "n_returns": ...}, ...],
         "top_out": [{"state": "Florida", "n_returns": ...}, ...],
         "net_by_state": [{"state": ..., "in": ..., "out": ..., "net": ...}, ...]}
    """
    pair = _discover_latest_year() if year is None else None
    if pair is None:
        return None
    yy, yz = pair
    inflow_url, outflow_url = _build_urls(yy, yz)
    inflow_bytes = _fetch_csv(inflow_url)
    outflow_bytes = _fetch_csv(outflow_url)
    if not inflow_bytes or not outflow_bytes:
        print(f"  [IRS SOI] one of inflow/outflow missing for {yy}-{yz}", file=sys.stderr)
        return None

    inflows = _parse_migration_csv(inflow_bytes, "in")
    outflows = _parse_migration_csv(outflow_bytes, "out")
    if not inflows or not outflows:
        return None

    in_by_state: Dict[str, int] = {}
    total_in = 0
    for r in inflows:
        # destination must be in Georgia; origin must be a real, non-GA state
        if not r["to_fips"].startswith("13") or r["is_aggregate"]:
            continue
        if r["other_state"] == 13:
            continue  # intrastate churn — excluded from state-to-state view
        name = STATE_FIPS_TO_NAME.get(r["other_state"])
        if not name:
            continue
        in_by_state[name] = in_by_state.get(name, 0) + r["n_returns"]
        total_in += r["n_returns"]

    out_by_state: Dict[str, int] = {}
    total_out = 0
    for r in outflows:
        if not r["from_fips"].startswith("13") or r["is_aggregate"]:
            continue
        if r["other_state"] == 13:
            continue
        name = STATE_FIPS_TO_NAME.get(r["other_state"])
        if not name:
            continue
        out_by_state[name] = out_by_state.get(name, 0) + r["n_returns"]
        total_out += r["n_returns"]

    if not in_by_state and not out_by_state:
        return None

    states = set(in_by_state) | set(out_by_state)
    net_by_state = sorted(
        ({"state": s, "in": in_by_state.get(s, 0), "out": out_by_state.get(s, 0),
          "net": in_by_state.get(s, 0) - out_by_state.get(s, 0)} for s in states),
        key=lambda x: x["net"], reverse=True,
    )
    top_in = sorted(({"state": k, "n_returns": v} for k, v in in_by_state.items()),
                    key=lambda x: -x["n_returns"])[:10]
    top_out = sorted(({"state": k, "n_returns": v} for k, v in out_by_state.items()),
                     key=lambda x: -x["n_returns"])[:10]

    return {
        "year_pair_label": f"{yy}→{yz}", "from_year": yy, "to_year": yz,
        "total_in": total_in, "total_out": total_out, "net": total_in - total_out,
        "top_in": top_in, "top_out": top_out, "net_by_state": net_by_state,
        "source": f"IRS SOI county-to-county migration, GA statewide ({inflow_url})",
    }


# ----------------------------- CLI smoke test -----------------------------

if __name__ == "__main__":
    cbsa = sys.argv[1] if len(sys.argv) > 1 else "42340"
    print(f"Fetching IRS SOI migration for CBSA {cbsa} ...", file=sys.stderr)
    d = fetch_migration_flows(cbsa)
    if d:
        print(f"  Year: {d['year_pair_label']}")
        print(f"  Total in:  {d['total_in']:,}")
        print(f"  Total out: {d['total_out']:,}")
        print(f"  Net:       {d['net']:+,}")
        print(f"  Top inbound MSAs:")
        for r in d['top_in'][:5]:
            print(f"    {r['origin_msa']:30s}  {r['n_returns']:,}")
        print(f"  Top outbound MSAs:")
        for r in d['top_out'][:5]:
            print(f"    {r['dest_msa']:30s}  {r['n_returns']:,}")
    else:
        print("  (no data)", file=sys.stderr)
